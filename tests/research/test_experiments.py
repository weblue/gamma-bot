"""Production-path experiment checks; synthetic inputs prove mechanics only."""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path

import pytest

from gamma_bot.cli import main
from gamma_bot.data.io import load_dataset
from gamma_bot.data.models import InvalidDatasetError, MesBar, RunConfig
from gamma_bot.replay.engine import run_replay
from gamma_bot.research.metrics import compute_metrics
from gamma_bot.strategies import OpeningRangeBaseline
from tests.replay.test_engine import (OPEN, PRIOR, bar, dataset, prior_bars, quote, session)


def complete_data(n=10):
    # One data-only roll warm-up plus n complete tradable sessions; no signals.
    opens = [PRIOR + timedelta(days=i) for i in range(n + 1)]
    rows = []
    for index, at in enumerate(opens):
        for contract, volume in [('MESU6', 10), ('MESZ6', 5)]:
            rows.extend(replace(b, sequence=b.sequence + index * 1000 + (500 if contract == 'MESZ6' else 0))
                        for b in prior_bars(at=at, contract=contract, volume=volume))
    source = dataset()
    return replace(source, events=tuple(rows), sessions=tuple(session(at) for at in opens),
                   macro_coverage_dates=tuple(at.date() for at in opens))


def test_split_is_chronological_disjoint_and_frozen(tmp_path):
    from gamma_bot.research.experiments import freeze_split
    dates = tuple(date(2026, 6, i) for i in range(1, 11))
    path = tmp_path / 'split.json'
    development, evaluation = freeze_split(tuple(reversed(dates)), path)
    assert development == dates[:7] and evaluation == dates[7:]
    assert set(development).isdisjoint(evaluation)
    before = path.read_bytes()
    assert freeze_split(dates, path) == (development, evaluation)
    with pytest.raises(InvalidDatasetError, match='frozen'):
        freeze_split((*dates, date(2026, 6, 11)), path)
    assert path.read_bytes() == before


def test_insufficient_sessions_are_pipeline_demonstration(tmp_path):
    from gamma_bot.research.experiments import freeze_split
    path = tmp_path / 'split.json'
    assert freeze_split((date(2026, 6, 1),), path) == ((), ())
    frozen = json.loads(path.read_text())
    assert frozen['status'] == 'pipeline_demonstration'
    assert frozen['development'] == frozen['evaluation'] == []


def test_complete_dates_exclude_partial_duplicate_delayed_and_data_only_sessions():
    from gamma_bot.research.experiments import complete_session_dates
    data = complete_data(10)
    assert complete_session_dates(data) == tuple(s.session_date for s in data.sessions[1:])
    last = data.events[-1]
    # A complete active U stream still counts when its inactive Z stream is partial.
    active = next(b for b in reversed(data.events) if b.contract == 'MESU6')
    without = replace(data, events=tuple(b for b in data.events if b is not active))
    assert complete_session_dates(without) == tuple(s.session_date for s in data.sessions[1:-1])
    duplicate = replace(data, events=(*data.events, active))
    assert complete_session_dates(duplicate) == tuple(s.session_date for s in data.sessions[1:-1])
    delayed = replace(data, events=tuple(replace(b, available_at=b.available_at+timedelta(days=1))
                      if b is active else b for b in data.events))
    assert complete_session_dates(delayed) == tuple(s.session_date for s in data.sessions[1:-1])
    assert last.contract == 'MESZ6'


def test_incomplete_later_session_cannot_alter_existing_split(tmp_path):
    from gamma_bot.research.experiments import complete_session_dates, freeze_split
    data = complete_data(10)
    path = tmp_path / 'split.json'
    frozen = freeze_split(complete_session_dates(data), path)
    at = data.sessions[-1].open_at + timedelta(days=1)
    extended = replace(data, sessions=(*data.sessions, session(at)),
                       events=(*data.events, bar(at+timedelta(minutes=1), 50000)))
    assert freeze_split(complete_session_dates(extended), path) == frozen


def test_admission_keeps_causal_roll_but_never_warmup_trades_or_time():
    data = dataset(quote(1, 24003, 24004), quote(2, 24018, 24019, seq=1001))
    variant = OpeningRangeBaseline().variant
    normal = run_replay(data, RunConfig(), variant, strategies=[OpeningRangeBaseline()])
    admitted = run_replay(data, RunConfig(), variant, strategies=[OpeningRangeBaseline()],
                          admitted_session_dates=(OPEN.date(),))
    assert admitted.trades == normal.trades
    assert len(admitted.trades) == 1
    assert all(p.at >= OPEN for p in admitted.equity_curve)
    assert len(admitted.manifest['roll_history']) == 1
    assert compute_metrics(admitted)['admissible_session_count'] == 1
    empty = run_replay(data, RunConfig(), variant, strategies=[OpeningRangeBaseline()], admitted_session_dates=())
    assert not empty.trades and not empty.equity_curve and not empty.manifest['roll_history']


def test_evaluate_retains_all_attempts_with_fresh_cost_sizing_and_frozen_manifest(tmp_path):
    from gamma_bot.research.experiments import evaluate, default_variants
    # Cheap collateral makes risk, rather than margin, bind the real replay quantity.
    data = dataset(quote(1, 24003, 24004), quote(2, 24040, 24041, seq=1001), margin=1000)
    results = evaluate(data, RunConfig(), default_variants(), tmp_path / 'split.json')
    frozen = json.loads((tmp_path / 'experiments.json').read_text())
    assert frozen['status'] == 'pipeline_demonstration'
    assert frozen['selection_policy'] == 'none'
    assert len(results) == 56  # eight variants x seven reference/one-at-a-time scenarios
    baseline = results['demonstration/opening_range_baseline/reference']
    cheap = results['demonstration/opening_range_baseline/fee_200']
    expensive = results['demonstration/opening_range_baseline/fee_500']
    slip = results['demonstration/opening_range_baseline/slippage_4']
    assert sum(t.quantity for t in cheap.trades) == 9
    assert sum(t.quantity for t in baseline.trades) == 8
    assert sum(t.quantity for t in expensive.trades) == 7
    assert sum(t.quantity for t in slip.trades) == 5
    assert all(t.variant_name == 'opening_range_baseline' for t in cheap.trades)
    assert results['demonstration/reversal/reference'].manifest['status'] == 'unsupported'
    assert results['demonstration/reversal_without_cvd/reference'].manifest['status'] == 'unsupported'
    assert frozen['scenarios']['mapping_900']['mapping_min_pairs'] >= 5
    before = (tmp_path / 'experiments.json').read_bytes()
    with pytest.raises(InvalidDatasetError, match='frozen'):
        evaluate(data, replace(RunConfig(), vix_ceiling=RunConfig().vix_ceiling+1), default_variants(), tmp_path/'split.json')
    assert (tmp_path/'experiments.json').read_bytes() == before


def test_chronological_runs_share_periods_accounting_and_keep_zero_trades(tmp_path):
    from gamma_bot.research.experiments import evaluate, default_variants
    data = complete_data(10)
    results = evaluate(data, RunConfig(), default_variants(), tmp_path/'split.json')
    assert len(results) == 112
    for part, count in [('development', 7), ('evaluation', 3)]:
        result = results[f'{part}/opening_range_baseline/reference']
        metrics = compute_metrics(result)
        assert metrics['admissible_session_count'] == count
        assert metrics['trade_count'] == 0
        assert result.manifest['ending_equity_cents'] == 1_000_000
        assert len({p.at.date() for p in result.equity_curve}) == count
    assert results['evaluation/reversal/reference'].manifest['status'] == 'unsupported'
    # Full and ablated variants use the same frozen input period, even when unavailable.
    for name in ['reversal', 'reversal_without_vix', 'breakout', 'breakout_without_opening_range']:
        result = results[f'evaluation/{name}/reference']
        assert set(result.coverage.intersection_dates) <= set(s.session_date for s in data.sessions[-3:])


def test_evaluate_cli_writes_all_attempts_and_preflights_inputs(tmp_path):
    args = ['evaluate', '--dataset', 'tests/fixtures/synthetic_replay', '--config', 'config/research.toml']
    out = tmp_path/'out'
    assert main([*args, '--output', str(out)]) == 0
    comparison = json.loads((out/'comparison.json').read_text())
    assert comparison['status'] == 'pipeline_demonstration'
    assert len(comparison['runs']) == 56
    assert comparison['runs']['demonstration/reversal/reference']['missing_rules']['gex']
    assert 'pipeline demonstration' in (out/'comparison.html').read_text().lower()
    equity = json.loads((out/'demonstration/opening_range_baseline/reference/equity_curve.json').read_text())
    assert {row['at'][:10] for row in equity} == {'2026-06-15'}
    assert (out/'demonstration/opening_range_baseline/reference/trades.csv').exists()
    blocked = tmp_path/'blocked'
    blocked.mkdir()
    (blocked/'comparison.json').symlink_to(Path('config/research.toml').resolve())
    before = Path('config/research.toml').read_bytes()
    assert main([*args, '--output', str(blocked)]) == 1
    assert Path('config/research.toml').read_bytes() == before
    assert not (blocked/'split.json').exists()


def signaled_complete_data(n=3):
    data = complete_data(n)
    rows = list(data.events)
    for s in data.sessions[1:]:
        when = s.open_at + timedelta(minutes=16)
        for i, b in enumerate(rows):
            if isinstance(b, MesBar) and b.contract == 'MESU6' and b.event_at == when:
                rows[i] = replace(b, low_ticks=24000, high_ticks=24004, close_ticks=24003)
        delta = s.open_at - OPEN
        rows.extend(replace(q, event_at=q.event_at+delta, available_at=q.available_at+delta,
                            sequence=60000+len(rows)+i)
                    for i, q in enumerate((quote(1, 24003, 24004), quote(2, 24018, 24019, seq=1001))))
    return replace(data, events=tuple(rows))


def test_signaled_warmup_cannot_change_evaluation_equity_or_first_session():
    data = signaled_complete_data()
    variant = OpeningRangeBaseline().variant
    admitted = data.sessions[-1].session_date
    result = run_replay(data, RunConfig(), variant, strategies=[OpeningRangeBaseline()],
                        admitted_session_dates=(admitted,))
    assert len(result.trades) == 1
    assert result.trades[0].signal_at.date() == admitted
    assert result.manifest['ending_equity_cents'] == 1_001_075
    assert result.equity_curve[0].equity_cents == 1_000_000
    assert {p.at.date() for p in result.equity_curve} == {admitted}
    assert compute_metrics(result)['observed_admissible_seconds'] == 23400


def test_all_configs_are_frozen_before_real_replay_and_strategy_state_is_fresh(tmp_path, monkeypatch):
    import gamma_bot.research.experiments as experiments
    actual = experiments.run_replay
    instances = []
    def observed(*args, **kwargs):
        frozen = json.loads((tmp_path/'experiments.json').read_text())
        assert (tmp_path/'split.json').exists()
        assert len(frozen['scenarios']) == 7
        assert kwargs['strategies'][0] not in instances
        instances.append(kwargs['strategies'][0])
        return actual(*args, **kwargs)
    monkeypatch.setattr(experiments, 'run_replay', observed)
    results = experiments.evaluate(signaled_complete_data(2), RunConfig(),
        (OpeningRangeBaseline().variant,), tmp_path/'split.json')
    assert len(results) == 14
    assert len(results['evaluation/opening_range_baseline/reference'].trades) == 1
    assert results['evaluation/opening_range_baseline/reference'].manifest['ending_equity_cents'] == 1_001_075


def test_direct_datasets_without_byte_hashes_cannot_change_frozen_inputs(tmp_path):
    from gamma_bot.research.experiments import evaluate
    data = dataset(quote(1, 24003, 24004), quote(2, 24018, 24019, seq=1001))
    variants = (OpeningRangeBaseline().variant,)
    evaluate(data, RunConfig(), variants, tmp_path/'split.json')
    changed = replace(data, events=(*data.events, quote(3, 24020, 24021, seq=1002)))
    with pytest.raises(InvalidDatasetError, match='frozen'):
        evaluate(changed, RunConfig(), variants, tmp_path/'split.json')


def test_comparison_keeps_requested_periods_and_source_security(tmp_path):
    from gamma_bot.research.experiments import default_variants, evaluate, experiment_plan
    from gamma_bot.research.report import write_comparison
    data = load_dataset(Path('tests/fixtures/synthetic_replay'))
    results = dict(evaluate(data, RunConfig(), default_variants(), tmp_path/'split.json'))
    first = next(iter(results))
    row = results[first]
    results[first] = replace(row, manifest=dict(row.manifest, sources={'<script>':
        {'model_id': '<img src=x>', 'api_key': 'PRIVATE', 'raw_events': ['LICENSED'],
         'documentation_url': 'https://example.com/?token=PRIVATE'}}))
    write_comparison(results, experiment_plan(data, RunConfig(), default_variants()), tmp_path)
    comparison = json.loads((tmp_path/'comparison.json').read_text())
    full = comparison['runs']['demonstration/reversal/reference']
    assert full['requested_session_count'] == 1
    assert full['session_dates'] == ['2026-06-15']
    text = (tmp_path/'comparison.html').read_text()
    assert '<script>' not in text and '<img src=x>' not in text
    assert '&lt;script&gt;' in text
    assert 'PRIVATE' not in text and 'LICENSED' not in text


def test_invalid_manifest_split_collision_is_rejected_before_writing(tmp_path):
    from gamma_bot.research.experiments import evaluate
    with pytest.raises(InvalidDatasetError, match='differ'):
        evaluate(complete_data(2), RunConfig(), (OpeningRangeBaseline().variant,), tmp_path/'experiments.json')
    assert not list(tmp_path.iterdir())


def test_mapping_sensitivity_admits_five_sparse_pairs_without_weakening_default_audit():
    from gamma_bot.data.coverage import audit_dataset
    from gamma_bot.data.models import PricePoint
    from gamma_bot.strategies import BreakoutStrategy
    from tests.strategies.helpers import profile
    data = dataset()
    points = []
    for i, minute in enumerate((10, 12, 14, 16, 18)):
        at = OPEN + timedelta(minutes=minute)
        points.extend((replace(quote(1, 24000, 24002), event_at=at, available_at=at, sequence=70000+i),
            PricePoint(source_id='synthetic', sequence=80000+i, event_at=at, available_at=at,
                       symbol='SPY', value=Decimal('600'), price_kind='mid')))
    data = replace(data, events=(*data.events, *points, profile(18, negative=True, source='synthetic').snapshot),
        source_metadata={'synthetic': {'classification': 'synthetic', 'availability_verified': True,
            'profile_boundary_convention': 'lower inclusive, upper exclusive'}})
    required = frozenset({'mes_prices', 'opening_range', 'gex', 'mapping', 'negative_regions', 'macro', 'margin'})
    assert 'mapping' in audit_dataset(data, required).missing_rules
    cfg = replace(RunConfig(), mapping_window_seconds=600)
    strategy = BreakoutStrategy(cfg)
    result = run_replay(data, cfg, strategy.variant, strategies=[strategy], admitted_session_dates=(OPEN.date(),))
    assert result.manifest['status'] == 'complete'
    assert 'mapping' not in result.coverage.missing_rules
    assert 'mapping' in audit_dataset(data, required).missing_rules
    # Six-pair minimum must not be downgraded to the five-pair floor.
    cfg = replace(cfg, mapping_min_pairs=6)
    strategy = BreakoutStrategy(cfg)
    result = run_replay(data, cfg, strategy.variant, strategies=[strategy])
    assert result.manifest['status'] == 'unsupported'
    assert 'mapping' in result.coverage.missing_rules


def test_delayed_warmup_bar_cannot_become_an_evaluated_strategy_decision():
    class ObservedBaseline(OpeningRangeBaseline):
        def __init__(self):
            super().__init__()
            self.decisions = []
        def on_state(self, state):
            self.decisions.append(state.bar.event_at.date())
            return super().on_state(state)
    data = dataset(quote(1, 24003, 24004), quote(2, 24018, 24019, seq=1001))
    old = replace(prior_bars()[15], available_at=OPEN+timedelta(minutes=17), sequence=90000)
    data = replace(data, events=(*data.events, old))
    strategy = ObservedBaseline()
    result = run_replay(data, RunConfig(), strategy.variant, strategies=[strategy], admitted_session_dates=(OPEN.date(),))
    assert len(result.trades) == 1
    assert set(strategy.decisions) == {OPEN.date()}


@pytest.mark.parametrize('changes', [{'initial_equity_cents': 2_000_000}, {'development_fraction': Decimal('0.5')}])
def test_study_rejects_misleading_capital_or_split_settings(changes, tmp_path):
    from gamma_bot.research.experiments import evaluate
    with pytest.raises(InvalidDatasetError):
        evaluate(dataset(), replace(RunConfig(), **changes), (OpeningRangeBaseline().variant,), tmp_path/'split.json')
    assert not list(tmp_path.iterdir())
