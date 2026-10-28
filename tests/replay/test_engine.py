"""Synthetic mechanics: expectations derive from executable tick prices, not historical returns."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from gamma_bot.data.models import Dataset, MarginPoint, MesBar, Quote, RunConfig, Session, MacroRelease
from gamma_bot.strategies import OpeningRangeBaseline
from gamma_bot.strategies.baseline import OPENING_RANGE_BASELINE

UTC = timezone.utc
OPEN = datetime(2026, 6, 15, 13, 30, tzinfo=UTC)
PRIOR = OPEN - timedelta(days=3)
CONTRACT = 'MESU6'
CONFIG = RunConfig()


def session(at=OPEN, hours=6.5):
    return Session(session_date=at.date(), open_at=at, close_at=at + timedelta(hours=hours),
                   schedule_available_at=at - timedelta(days=10))


def bar(at, seq, *, contract=CONTRACT, close=24000, high=24002, low=23998, volume=10, **kw):
    return MesBar(source_id='synthetic', sequence=seq, start_at=at - timedelta(minutes=1),
                  event_at=at, available_at=at, contract=contract, open_ticks=24000,
                  high_ticks=high, low_ticks=low, close_ticks=close, volume=volume, **kw)


def prior_bars(at=PRIOR, contract=CONTRACT, volume=10):
    return tuple(bar(at + timedelta(minutes=i + 1), i, contract=contract, volume=volume) for i in range(390))


def quote(seconds, bid, ask, *, seq=1000, **kw):
    at = OPEN + timedelta(minutes=16, seconds=seconds)
    return Quote(source_id='synthetic', sequence=seq, event_at=at, available_at=at,
                 contract=CONTRACT, bid_ticks=bid, ask_ticks=ask, **kw)


def dataset(*tail, direction='long', margin=600_000, prior=True, **kw):
    bars = tuple(bar(OPEN + timedelta(minutes=i+1), 500+i) for i in range(15))
    decision = bar(OPEN + timedelta(minutes=16), 516,
                   close=24003 if direction == 'long' else 23997,
                   high=24004 if direction == 'long' else 24000,
                   low=24000 if direction == 'long' else 23996)
    return Dataset(events=(*(prior_bars() if prior else ()),
                           *(tuple(replace(b, sequence=b.sequence+2000) for b in prior_bars(contract='MESZ6', volume=5)) if prior else ()), *bars, decision, *tail),
                   sessions=(session(PRIOR), session()),
                   margin_points=(MarginPoint(contract=CONTRACT, available_at=PRIOR,
                       initial_cents=margin, maintenance_cents=margin, provenance='scenario'),
                       MarginPoint(contract='MESZ6', available_at=PRIOR, initial_cents=margin,
                                   maintenance_cents=margin, provenance='scenario')),
                   macro_coverage_dates=(PRIOR.date(), OPEN.date()),
                   macro_schedule_available_at=PRIOR - timedelta(days=10),
                   source_metadata={'synthetic': {'classification': 'synthetic', 'availability_verified': True}}, **kw)


def run(data, config=CONFIG):
    from gamma_bot.replay.engine import run_replay
    return run_replay(data, config, OPENING_RANGE_BASELINE,
                      strategies=[OpeningRangeBaseline(config)])


@pytest.mark.parametrize('direction,entry,exit,stop,target', [
    ('long', 24005, 24016, 23999, 24017), ('short', 23995, 23984, 24001, 23983)])
def test_hand_computable_roundtrip_and_marked_equity(direction, entry, exit, stop, target):
    qs = ((quote(1, 24003, 24004), quote(2, 24018, 24019, seq=1001)) if direction == 'long' else
          (quote(1, 23996, 23997), quote(2, 23981, 23982, seq=1001)))
    result = run(dataset(*qs, direction=direction))
    trade, = result.trades
    assert (trade.entry_price_ticks, trade.exit_price_ticks, trade.quantity) == (entry, exit, 1)
    assert (trade.stop_ticks, trade.target_ticks) == (stop, target)
    assert (trade.gross_pnl_cents, trade.net_pnl_cents, trade.fees_cents) == (1375, 1075, 300)
    assert trade.exit_reason == 'target'
    assert min(p.equity_cents for p in result.equity_curve) == 999600
    assert result.equity_curve[-1].equity_cents == 1001075
    assert result.manifest['ending_reserved_margin_cents'] == 0
    assert not result.unresolved_exposure


def test_gap_stop_and_no_invented_eof_close():
    result = run(dataset(quote(1, 24003, 24004), quote(2, 23990, 23991, seq=1001)))
    assert result.trades[0].exit_price_ticks == 23989
    assert result.trades[0].gross_pnl_cents == -2000
    unfinished = run(dataset(quote(1, 24003, 24004)))
    assert not unfinished.trades
    assert unfinished.unresolved_exposure['position_quantity'] == 1
    assert unfinished.manifest['ending_reserved_margin_cents'] == 600000


def test_same_clock_observation_and_delayed_old_quote_cannot_fill():
    tied = quote(0, 24003, 24004, seq=999)
    delayed = replace(quote(1, 24003, 24004), event_at=OPEN + timedelta(minutes=15))
    result = run(dataset(tied, delayed))
    assert not result.trades
    assert result.unresolved_exposure['pending_quantity'] == 1


def test_actual_entry_gap_is_resized_and_nonpositive_stop_rejected():
    result = run(dataset(quote(1, 25000, 25001)))
    assert not result.trades and not result.unresolved_exposure
    assert any(s.reason == 'zero_size_at_execution' for s in result.skips)
    result = run(dataset(quote(1, 23996, 23997)))
    assert not result.trades and not result.unresolved_exposure
    assert any(s.reason == 'invalid_stop_at_execution' for s in result.skips)


def test_partial_entries_exits_keep_exact_lots_and_release_pending_on_exit():
    data = dataset(quote(1, 24003, 24004, ask_size=1),
                   quote(2, 24004, 24005, ask_size=1, seq=1001),
                   quote(3, 24030, 24031, bid_size=1, seq=1002),
                   quote(4, 24030, 24031, bid_size=1, seq=1003), margin=100_000)
    result = run(data)
    assert [t.entry_price_ticks for t in result.trades] == [24005, 24006]
    assert [t.target_ticks for t in result.trades] == [24017, 24020]
    assert [t.exit_price_ticks for t in result.trades] == [24016, 24019]
    assert sum(t.quantity for t in result.trades) == 2
    assert result.manifest['ending_reserved_margin_cents'] == 0


def test_missing_prior_volume_excludes_startup_and_future_margin_not_used():
    result = run(dataset(quote(1, 24003, 24004), prior=False))
    assert not result.trades and not result.unresolved_exposure
    assert any(s.reason == 'roll_evidence_missing' for s in result.skips)
    data = dataset(quote(1, 24003, 24004))
    data = replace(data, margin_points=(replace(data.margin_points[0], available_at=OPEN + timedelta(hours=1)),))
    result = run(data)
    assert not result.unresolved_exposure
    assert result.manifest['status'] == 'unsupported'
    assert 'margin' in result.coverage.missing_rules


def test_deficit_marks_before_liquidation_on_next_executable_event():
    data = dataset(quote(1, 24003, 24004), quote(2, 24002, 24003, seq=1001),
                   quote(3, 23990, 23991, seq=1002), margin=999_000)
    # A newly published margin creates a deficit before the next executable quote.
    data = replace(data, margin_points=(*data.margin_points,
        MarginPoint(contract=CONTRACT, available_at=quote(2, 24002, 24003).available_at,
                    initial_cents=1_000_000, maintenance_cents=1_000_000, provenance='scenario')))
    result = run(data)
    assert result.trades[0].exit_reason == 'maintenance_deficit'
    assert result.trades[0].exit_at == quote(3, 23990, 23991).available_at
    assert result.trades[0].exit_price_ticks == 23989
    assert any(s.reason == 'maintenance_deficit' for s in result.skips)


@pytest.mark.parametrize('kind', ['early_close', 'macro', 'daily_loss'])
def test_external_shutdowns_exit_on_available_event(kind):
    data = dataset(quote(1, 24003, 24004))
    if kind == 'early_close':
        sess = replace(session(), close_at=OPEN + timedelta(minutes=22))
        late = replace(quote(2, 24003, 24004, seq=1001), event_at=OPEN+timedelta(minutes=17), available_at=OPEN+timedelta(minutes=17))
        data = replace(data, sessions=(session(PRIOR), sess), events=(*data.events, late))
    elif kind == 'macro':
        release = MacroRelease(event_id='synthetic', category='CPI', scheduled_at=OPEN+timedelta(minutes=52), schedule_available_at=PRIOR)
        late = replace(quote(2, 24003, 24004, seq=1001), event_at=OPEN+timedelta(minutes=17), available_at=OPEN+timedelta(minutes=17))
        data = replace(data, macro_releases=(release,), events=(*data.events, late))
    else:
        data = replace(data, events=(*data.events, quote(2, 23700, 23701, seq=1001), quote(3, 23690, 23691, seq=1002)))
    result = run(data)
    assert result.trades[0].exit_reason == {'early_close':'session_flatten', 'macro':'macro_flatten', 'daily_loss':'daily_loss'}[kind]


def test_bar_only_entry_next_open_and_stop_first():
    next_bar = bar(OPEN+timedelta(minutes=17), 517, high=24020, low=23990)
    later = bar(OPEN+timedelta(minutes=18), 518, high=24020, low=23990)
    result = run(dataset(next_bar, later))
    assert result.trades[0].entry_price_ticks == 24001
    # The entry bar's later OHLC can hit protection after the opening fill.
    assert result.trades[0].exit_price_ticks == 23998
    assert result.trades[0].exit_reason == 'stop'


def test_unsupported_full_request_returns_diagnostics():
    from gamma_bot.replay.engine import run_replay
    from gamma_bot.strategies import ReversalStrategy
    from gamma_bot.strategies.reversal import FULL_REVERSAL
    result = run_replay(dataset(), CONFIG, FULL_REVERSAL, strategies=[ReversalStrategy()])
    assert not result.trades
    assert result.coverage.missing_rules
    assert result.manifest['status'] == 'unsupported'


def test_roll_uses_complete_prior_session_and_immediate_next_quarter_only():
    from gamma_bot.replay.roll import RollSelector
    selector = RollSelector()
    first, today = session(PRIOR), session()
    for b in (*prior_bars(contract='MESU6', volume=10), *prior_bars(contract='MESZ6', volume=20),
              *prior_bars(contract='MESH7', volume=100)):
        selector.observe(b, first)
    active, reasons = selector.select(today, first, OPEN)
    assert active == 'MESZ6'  # startup U, advance only to Z despite H7 volume
    assert not reasons
    # Next day U dominates: never roll backward; only Z -> H7 can compete.
    next_day = session(OPEN + timedelta(days=1))
    for b in (*prior_bars(at=OPEN, contract='MESU6', volume=100),
              *prior_bars(at=OPEN, contract='MESZ6', volume=20),
              *prior_bars(at=OPEN, contract='MESH7', volume=10)):
        selector.observe(b, today)
    assert selector.select(next_day, today, next_day.open_at)[0] == 'MESZ6'


def test_roll_incomplete_delayed_and_delivery_month_evidence_excluded():
    from gamma_bot.replay.roll import RollSelector
    selector = RollSelector()
    first, today = session(PRIOR), session()
    for b in prior_bars()[:-1]:
        selector.observe(b, first)
    assert selector.select(today, first, OPEN) == (None, ('roll_evidence_missing',))
    delayed = replace(prior_bars()[-1], available_at=OPEN + timedelta(seconds=1))
    selector.observe(delayed, first)
    assert selector.select(today, first, OPEN)[0] is None
    selector = RollSelector()
    for b in prior_bars(contract='MESM6'):
        selector.observe(b, first)
    assert selector.select(today, first, OPEN) == (None, ('contract_expiry_unverified',))


def test_post_fill_risk_recheck_liquidates_later_and_records_actual_execution():
    config = replace(CONFIG, risk_fraction=Decimal('0.001175'))
    result = run(dataset(quote(1, 24003, 24004), quote(2, 24003, 24004, seq=1001)), config)
    trade, = result.trades
    assert trade.exit_reason == 'risk_breach'
    assert trade.risk_breach
    assert trade.entry_at < trade.exit_at
    assert (trade.entry_price_ticks, trade.exit_price_ticks) == (24005, 24002)


def test_runtime_sizes_with_known_margin_never_tempting_future_cheaper_margin():
    data = dataset(quote(1, 24003, 24004), quote(2, 24018, 24019, seq=1001))
    data = replace(data, margin_points=(*data.margin_points,
        replace(data.margin_points[0], initial_cents=1000, maintenance_cents=1000,
                available_at=OPEN + timedelta(hours=2))))
    result = run(data)
    assert sum(t.quantity for t in result.trades) == 1
    assert all(m.initial_cents == 600000 for m in result.manifest['margin_used'])


def test_known_exit_liquidity_is_shared_across_multiple_lots():
    data = dataset(quote(1, 24003, 24004, ask_size=1),
                   quote(2, 24004, 24005, ask_size=1, seq=1001),
                   quote(3, 23990, 23991, bid_size=1, seq=1002), margin=100_000)
    result = run(data)
    assert sum(t.quantity for t in result.trades) == 1
    assert result.unresolved_exposure['position_quantity'] == 1
    assert result.unresolved_exposure['pending_quantity'] == 0
    assert result.manifest['ending_reserved_margin_cents'] == 100000


def test_strategy_variant_mismatch_is_not_silently_relabelled():
    from gamma_bot.replay.engine import run_replay
    from gamma_bot.strategies.reversal import FULL_REVERSAL
    with pytest.raises(ValueError, match='matching'):
        run_replay(dataset(), CONFIG, FULL_REVERSAL, strategies=[OpeningRangeBaseline()])


def write_dataset(path, data):
    import json
    from gamma_bot.data.provenance import to_jsonable
    path.mkdir()
    def lines(name, rows):
        (path/name).write_text(''.join(json.dumps(to_jsonable(r), sort_keys=True)+'\n' for r in rows))
    lines('events.jsonl', [dict(type='mes_bar' if isinstance(e, MesBar) else 'quote', **to_jsonable(e)) for e in data.events])
    lines('sessions.jsonl', data.sessions)
    lines('margin_points.jsonl', data.margin_points)
    metadata = {'sources': [dict(source_id=k, **v) for k,v in to_jsonable(data.source_metadata).items()],
                'limitations': ['Synthetic mechanics only; no historical evidence.'],
                'macro_coverage_dates': to_jsonable(data.macro_coverage_dates),
                'macro_schedule_available_at': to_jsonable(data.macro_schedule_available_at)}
    (path/'metadata.json').write_text(json.dumps(metadata, sort_keys=True))


def test_cli_replay_writes_identical_mechanics_and_manifest_twice(tmp_path):
    import json
    from gamma_bot.cli import main
    data_path = tmp_path/'synthetic'
    write_dataset(data_path, dataset(quote(1,24003,24004), quote(2,24018,24019,seq=1001)))
    config = tmp_path/'config.toml'
    config.write_text('')
    for out in (tmp_path/'one', tmp_path/'two'):
        assert main(['replay', '--dataset', str(data_path), '--config', str(config),
                     '--variant','opening_range_baseline','--output',str(out)]) == 0
    for name in ('trades.json','equity_curve.json','manifest.json','coverage.json','skips.json','unresolved_exposure.json'):
        assert (tmp_path/'one'/name).read_bytes() == (tmp_path/'two'/name).read_bytes()
    assert json.loads((tmp_path/'one'/'trades.json').read_text())[0]['net_pnl_cents'] == 1075
    full = tmp_path/'unsupported'
    assert main(['replay','--dataset',str(data_path),'--config',str(config),'--variant','reversal','--output',str(full)]) == 2
    assert json.loads((full/'manifest.json').read_text())['status'] == 'unsupported'
    assert json.loads((full/'trades.json').read_text()) == []


def test_absent_immediate_next_contract_volume_excludes_whole_session():
    result = run(replace(dataset(quote(1,24003,24004)), events=tuple(
        e for e in dataset(quote(1,24003,24004)).events if getattr(e,'contract',None) != 'MESZ6')))
    assert not result.trades and not result.unresolved_exposure
    assert any(s.reason == 'roll_challenger_evidence_missing' for s in result.skips)


def test_triggered_partial_stop_keeps_liquidating_after_price_recovers():
    result = run(dataset(quote(1,24003,24004,ask_size=1),
                         quote(2,24004,24005,ask_size=1,seq=1001),
                         quote(3,23990,23991,bid_size=1,seq=1002),
                         quote(4,24004,24005,bid_size=1,seq=1003),margin=100000))
    assert [t.exit_reason for t in result.trades] == ['stop','stop']
    assert [t.exit_price_ticks for t in result.trades] == [23989,24003]
    assert not result.unresolved_exposure


def test_partial_entry_gap_cancels_remaining_reserved_risk_budget():
    result = run(dataset(quote(1,24029,24030,ask_size=1), margin=100000))
    assert result.unresolved_exposure['position_quantity'] == 1
    assert result.unresolved_exposure['pending_quantity'] == 0
    assert result.manifest['ending_reserved_margin_cents'] == 100000
    assert any(s.reason == 'pending_risk_budget_exceeded' for s in result.skips)


def test_execution_plan_risk_uses_actual_entry_and_fee_slippage_once():
    result = run(dataset(quote(1,24003,24004), quote(2,24018,24019,seq=1001)))
    assert result.trades[0].planned_risk_cents == 1175
    assert not result.trades[0].risk_breach


def test_inactive_contract_midpoint_cannot_reset_active_contract_features():
    from gamma_bot.data.models import PricePoint
    at = OPEN+timedelta(minutes=14,seconds=30)
    point = PricePoint(source_id='synthetic',sequence=3000,event_at=at,available_at=at,
                       symbol='MESZ6',value=Decimal('6000'),price_kind='mid')
    data = dataset(quote(1,24003,24004), quote(2,24018,24019,seq=1001))
    result = run(replace(data, events=(*data.events,point)))
    assert len(result.trades) == 1
    assert result.trades[0].contract == 'MESU6'


def test_midpoint_or_indicative_price_is_never_executable_liquidity():
    from gamma_bot.data.models import PricePoint
    at = OPEN+timedelta(minutes=17)
    point = PricePoint(source_id='synthetic',sequence=3000,event_at=at,available_at=at,
                       symbol='MESU6',value=Decimal('6001'),price_kind='indicative')
    data = dataset()
    result = run(replace(data, events=(*data.events,point)))
    assert not result.trades
    assert result.unresolved_exposure['position_quantity'] == 0
    assert result.unresolved_exposure['pending_quantity'] == 1


def test_multiple_midpoint_sources_fail_closed_without_source_election():
    first = quote(1,24003,24004,ask_size=1)
    other = replace(first,source_id='second_feed',sequence=1)
    later = quote(2,24004,24005,seq=1001)
    with pytest.raises(ValueError, match='source switch'):
        run(dataset(first,other,later))


@pytest.mark.parametrize('overlap', [False, True])
def test_roll_does_not_combine_or_double_count_sources(overlap):
    from gamma_bot.replay.roll import RollSelector
    selector = RollSelector()
    first, today = session(PRIOR), session()
    bars = prior_bars()
    mixed = (*bars, *(replace(b,source_id='second') for b in bars)) if overlap else (
        *bars[:195], *(replace(b,source_id='second') for b in bars[195:]))
    for b in (*mixed, *prior_bars(contract='MESZ6',volume=5)):
        selector.observe(b,first)
    active, reasons = selector.select(today,first,OPEN)
    assert active is None
    assert reasons == ('roll_volume_source_ambiguous',)


def test_roll_starts_new_contract_features_with_fresh_warmup():
    next_open = OPEN+timedelta(days=1)
    tuesday = session(next_open)
    data = dataset()
    events = (*prior_bars(), *(replace(b,sequence=b.sequence+2000) for b in prior_bars(contract='MESZ6',volume=5)),
              *(replace(b,sequence=b.sequence+4000) for b in prior_bars(at=OPEN)),
              *(replace(b,sequence=b.sequence+6000) for b in prior_bars(at=OPEN,contract='MESZ6',volume=20)),
              bar(next_open+timedelta(minutes=15),8000,contract='MESZ6'),
              bar(next_open+timedelta(minutes=16),8001,contract='MESZ6',close=24003,high=24004,low=24000))
    data = replace(data,events=events,sessions=(*data.sessions,tuesday),
                   macro_coverage_dates=(*data.macro_coverage_dates,tuesday.session_date))
    result = run(data)
    assert result.manifest['roll_history'][-1]['contract'] == 'MESZ6'
    assert not result.trades and not result.unresolved_exposure
