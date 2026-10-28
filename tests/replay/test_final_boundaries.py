"""Reviewed integration boundaries, exercised with real synthetic replay inputs."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json

import pytest

from gamma_bot.data.coverage import audit_dataset
from gamma_bot.data.models import InvalidDatasetError, NegativeRegion, RunConfig
from gamma_bot.replay.engine import run_replay
from gamma_bot.research.experiments import evaluate
from gamma_bot.research.metrics import compute_metrics
from gamma_bot.research.report import write_report, write_comparison
from gamma_bot.strategies import BreakoutStrategy, OpeningRangeBaseline, ReversalStrategy
from tests.features.helpers import snapshot, point
from tests.replay.test_engine import OPEN, PRIOR, bar, dataset, quote, run
from tests.research.test_experiments import complete_data


@pytest.mark.parametrize('partial', [True, False])
def test_execution_collateral_includes_fees_and_mark_loss(partial):
    tail = [quote(1, 24003, 24004, ask_size=1 if partial else 2)]
    if partial:
        tail.append(quote(2, 24003, 24004, ask_size=1, seq=1001))
    data = dataset(*tail, margin=500000)
    data = replace(data, margin_points=tuple(replace(m, maintenance_cents=400000) for m in data.margin_points))
    result = run(data)
    assert result.unresolved_exposure['position_quantity'] == 1
    assert result.unresolved_exposure['pending_quantity'] == 0
    assert result.manifest['ending_equity_cents'] == 999600
    assert result.manifest['ending_reserved_margin_cents'] == 500000
    assert not result.unresolved_exposure['liquidation']


def test_one_contract_fee_boundary_rejects_entry_before_any_fill():
    data = dataset(quote(1, 24003, 24004), margin=999601)
    result = run(data)
    assert not result.unresolved_exposure and not result.trades
    assert result.manifest['ending_equity_cents'] == 1000000


def test_inactive_volume_does_not_require_execution_margin():
    data = dataset(quote(1,24003,24004), quote(2,24018,24019,seq=1001))
    data = replace(data, events=(*data.events, bar(OPEN+timedelta(minutes=1),9000,contract='MESZ6')),
                   margin_points=(data.margin_points[0],))
    result = run(data)
    assert result.manifest['status'] == 'complete'
    assert result.trades[0].net_pnl_cents == 1075


def test_active_margin_absence_cannot_report_zero_return():
    data = dataset()
    data = replace(data, margin_points=(data.margin_points[1],),
                   events=(*data.events, bar(OPEN+timedelta(minutes=1),9000,contract='MESZ6')))
    result = run(data)
    assert not compute_metrics(result)['performance_available']
    assert result.manifest['status'] == 'unsupported'
    assert 'actual selected' in result.coverage.missing_rules['margin']
    assert any(s.reason == 'margin_unavailable' for s in result.skips)


def test_chronological_evaluation_accepts_active_only_margin(tmp_path):
    data = complete_data(2)
    data = replace(data, margin_points=(data.margin_points[0],))
    results = evaluate(data, RunConfig(), (OpeningRangeBaseline().variant,), tmp_path/'split.json')
    for part in ('development', 'evaluation'):
        result = results[f'{part}/opening_range_baseline/reference']
        assert result.manifest['status'] == 'complete'
        assert compute_metrics(result)['admissible_session_count'] == 1


def rolled_data(*, exclusion=False, changed=True):
    # Complete U/Z volume on Friday and Monday elects U then Z on Tuesday.
    data = complete_data(3 if exclusion else 2)
    opens = [s.open_at for s in data.sessions]
    events = []
    for event in data.events:
        if event.start_at.date() == opens[-2].date() and event.contract == 'MESZ6':
            event = replace(event, volume=20)
        if exclusion and event.start_at.date() == opens[1].date() and event.contract == 'MESU6' and event.sequence % 1000 == 0:
            continue  # next session excluded; following evidence allows recovery
        events.append(event)
    for i, at in enumerate((opens[1], opens[-1])):
        events.append(snapshot(event_at=at+timedelta(minutes=15), available_at=at+timedelta(minutes=15),
            oi_published_at=at-timedelta(hours=2), sequence=50000+i, model_id='model-B' if changed and i else 'model-A'))
    return replace(data, events=tuple(events))


@pytest.mark.parametrize('exclusion', [False, True])
def test_gex_identity_cannot_change_across_roll_or_exclusion(exclusion):
    with pytest.raises(InvalidDatasetError, match='GEX.*identity'):
        run(rolled_data(exclusion=exclusion))


def test_same_identity_survives_roll_and_remains_in_manifest():
    result = run(rolled_data(changed=False))
    assert [r['contract'] for r in result.manifest['roll_history']] == [None,'MESU6','MESZ6']
    assert result.manifest['gex_identity']['model_id'] == 'model-A'
    assert not result.trades


def test_study_rejects_identity_change_across_development_holdout_before_writes(tmp_path):
    with pytest.raises(InvalidDatasetError, match='GEX.*identity'):
        evaluate(rolled_data(), RunConfig(), (OpeningRangeBaseline().variant,), tmp_path/'split.json')
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('unsupported', [False, True])
def test_actual_zero_trade_identity_is_reported_without_optional_metadata(tmp_path, unsupported):
    data = dataset()
    data = replace(data, events=(*data.events,snapshot(model_id='actual-model-A')))
    strategy = ReversalStrategy() if unsupported else OpeningRangeBaseline()
    result = run_replay(data, RunConfig(), strategy.variant, strategies=[strategy])
    write_report(result,tmp_path)
    identity = json.loads((tmp_path/'manifest.json').read_text())['gex_identity']
    assert identity == {'status':'selected','source_id':'synthetic-gex','model_id':'actual-model-A',
                        'underlying':'SPY','expiry_days':[0,1],'provenance':'modeled'}
    assert 'actual-model-A' in (tmp_path/'report.html').read_text()


@pytest.mark.parametrize('metadata', [{'model_id':'wrong'}, {'expiry_days':(7,)}, {'underlying':'SPX'}, {'provenance':'vendor'}])
def test_contradictory_gex_metadata_fails_closed(metadata):
    data = dataset()
    data = replace(data, events=(*data.events,snapshot()),
                   source_metadata={**data.source_metadata,'synthetic-gex':metadata})
    with pytest.raises(InvalidDatasetError, match='GEX.*metadata'):
        run(data)


def test_no_snapshot_has_explicit_unselected_identity(tmp_path):
    result = run(dataset())
    write_report(result,tmp_path)
    assert json.loads((tmp_path/'manifest.json').read_text())['gex_identity'] == {'status':'unselected'}


def mapping_data():
    data = dataset(bar(OPEN+timedelta(minutes=17),517,high=24002,low=24000,close=24001),
                   bar(OPEN+timedelta(minutes=18),518,high=24002,low=23990,close=24000))
    pairs = tuple(p for i in range(10,18) for p in
                  (point(i,symbol='MESU6',value='6000'),point(i)))
    snapshots = tuple(snapshot(i,total_gex=Decimal('-100'),profile_available=True,
        negative_regions=(NegativeRegion(region_id='negative',lower=Decimal('600.05'),upper=Decimal('602')),))
        for i in (14,15,16))
    return replace(data, events=(*data.events,*pairs,*snapshots),
        source_metadata={**data.source_metadata,'synthetic-gex':{
            'classification':'synthetic','profile_boundary_convention':'lower inclusive, upper exclusive'}})


def test_explicit_midpoints_admit_and_execute_bar_breakout():
    data = mapping_data()
    assert 'mapping' in audit_dataset(data,frozenset({'mapping'})).supported_rules
    strategy = BreakoutStrategy()
    result = run_replay(data,RunConfig(),strategy.variant,strategies=[strategy])
    assert result.manifest['status'] == 'complete'
    assert len(result.trades) == 1
    assert result.trades[0].strategy == 'breakout'


@pytest.mark.parametrize('convention', ['lower exclusive, upper inclusive','unknown'])
def test_noncanonical_membership_cannot_admit_full_breakout(convention):
    data = mapping_data()
    data = replace(data,source_metadata={**data.source_metadata,'synthetic-gex':{
        'classification':'synthetic','profile_boundary_convention':convention}})
    assert 'negative_regions' in audit_dataset(data,frozenset({'negative_regions'})).missing_rules
    strategy = BreakoutStrategy()
    result = run_replay(data,RunConfig(),strategy.variant,strategies=[strategy])
    assert result.manifest['status'] == 'unsupported' and not result.trades


def test_standalone_report_uses_actual_initial_capital(tmp_path):
    result = run(dataset(),replace(RunConfig(),initial_equity_cents=1234567))
    write_report(result,tmp_path)
    assert 'USD 12,345.67 starting simulated capital' in (tmp_path/'report.html').read_text()


def test_own_reservation_does_not_block_affordable_partial_balance():
    result = run(dataset(quote(1,24003,24004,ask_size=1),
                         quote(2,24003,24004,ask_size=2,seq=1001),margin=300000))
    assert result.unresolved_exposure['position_quantity'] == 3
    assert result.unresolved_exposure['pending_quantity'] == 0
    assert result.manifest['ending_reserved_margin_cents'] == 900000
    assert result.manifest['ending_equity_cents'] == 998800


@pytest.mark.parametrize('direction', ['long','short'])
def test_exact_initial_margin_after_fee_and_spread_is_affordable(direction):
    entry = quote(1,24003,24004) if direction == 'long' else quote(1,23996,23997)
    result = run(dataset(entry,margin=999600,direction=direction))
    assert result.unresolved_exposure['position_quantity'] == (1 if direction == 'long' else -1)
    assert result.manifest['ending_equity_cents'] == result.manifest['ending_reserved_margin_cents'] == 999600
    assert not result.unresolved_exposure['liquidation']


@pytest.mark.parametrize('change', ['indicative','source_mix','contract_mix','different_minutes','late_available','stale_snapshot'])
def test_midpoint_audit_and_runtime_share_negative_pair_semantics(change):
    from gamma_bot.features.state import FeatureEngine
    points = [p for i in range(11,16) for p in (point(i,symbol='MESU6',value='6000'),point(i))]
    if change == 'indicative':
        points = [replace(p,price_kind='indicative') if p.symbol == 'SPY' else p for p in points]
    elif change == 'source_mix':
        points[-1] = replace(points[-1],source_id='second-source')
    elif change == 'contract_mix':
        points[-2] = replace(points[-2],symbol='MESZ6')
    elif change == 'different_minutes':
        points = [replace(p,event_at=p.event_at+timedelta(minutes=1),available_at=p.available_at+timedelta(minutes=1))
                  if p.symbol == 'SPY' else p for p in points]
    elif change == 'late_available':
        points[-1] = replace(points[-1],available_at=points[-1].available_at+timedelta(seconds=61))
    snap = snapshot(0 if change == 'stale_snapshot' else 15)
    data = replace(dataset(),events=(*points,snap))
    assert 'mapping' in audit_dataset(data,frozenset({'mapping'})).missing_rules
    engine = FeatureEngine()
    events = sorted(data.events,key=lambda e:(e.available_at,e.event_at,e.source_id,e.sequence))
    if change == 'source_mix':
        with pytest.raises(InvalidDatasetError,match='source switch'):
            for event in events:
                engine.update(event)
    else:
        for event in events:
            assert engine.update(event).current_gex is None


@pytest.mark.parametrize('endpoint,expected', [('lower',True),('upper',False)])
def test_current_profile_endpoint_membership_at_execution(endpoint,expected):
    from gamma_bot.data.models import GexSnapshot
    data = mapping_data()
    rows = []
    for event in data.events:
        if isinstance(event,GexSnapshot) and event.event_at == OPEN+timedelta(minutes=16):
            region = event.negative_regions[0]
            event = replace(event,event_at=event.event_at-timedelta(seconds=1),
                            available_at=event.available_at-timedelta(seconds=1),
                            negative_regions=(replace(region,**{endpoint:Decimal('600.075')}),))
        rows.append(event)
    data = replace(data,events=tuple(rows))
    strategy = BreakoutStrategy()
    result = run_replay(data,RunConfig(),strategy.variant,strategies=[strategy])
    assert bool(result.trades) is expected  # signal close 24003 equals the current endpoint


def test_runtime_profile_metadata_gate_uses_same_canonical_rule():
    from gamma_bot.data.models import GexSnapshot, PricePoint
    from gamma_bot.features.state import FeatureEngine
    data = mapping_data()
    engine = FeatureEngine(source_metadata={'synthetic-gex':{
        'profile_boundary_convention':'lower exclusive, upper inclusive'}})
    states = []
    for event in sorted((e for e in data.events if isinstance(e,(GexSnapshot,PricePoint))),
                        key=lambda e:(e.available_at,e.event_at,e.source_id,e.sequence)):
        states.append(engine.update(event))
    assert any(s.current_gex is not None for s in states)
    assert all('negative_regions' in s.unavailable_rules for s in states)


def test_study_identity_is_shared_even_before_first_snapshot(tmp_path):
    data = complete_data(2)
    at = data.sessions[-1].open_at+timedelta(minutes=15)
    snap = snapshot(event_at=at,available_at=at,oi_published_at=at-timedelta(hours=2))
    data = replace(data,events=(*data.events,snap))
    results = evaluate(data,RunConfig(),(OpeningRangeBaseline().variant,),tmp_path/'split.json')
    frozen = json.loads((tmp_path/'experiments.json').read_text())
    write_comparison(results,frozen,tmp_path)
    comparison = json.loads((tmp_path/'comparison.json').read_text())
    assert frozen['gex_identity']['model_id'] == 'test-only-import-model'
    assert comparison['gex_identity'] == frozen['gex_identity']
    for key,result in results.items():
        assert result.manifest['gex_identity']['model_id'] == 'test-only-import-model'
        assert comparison['runs'][key]['gex_identity'] == frozen['gex_identity']


@pytest.mark.parametrize('changed', ['source_id','model_id','underlying','expiry_days','provenance'])
def test_every_gex_identity_dimension_is_locked(changed):
    first = snapshot(14)
    values = dict(source_id='another',model_id='another',underlying='SPX',expiry_days=(7,),provenance='vendor')
    second = replace(snapshot(15),**{changed:values[changed]})
    with pytest.raises(InvalidDatasetError,match='GEX.*identity'):
        run(replace(dataset(),events=(*dataset().events,first,second)))


def test_importer_rejects_conflicting_optional_model_metadata(dataset_writer):
    from conftest import gex_record
    from gamma_bot.data.io import load_dataset
    path = dataset_writer([gex_record()],metadata={'sources':[{
        'source_id':'synthetic','classification':'synthetic','model_id':'wrong-model','expiry_days':[7]}]})
    with pytest.raises(InvalidDatasetError,match='GEX.*metadata'):
        load_dataset(path)


def test_exclusion_recovery_with_same_identity_keeps_fresh_warmup():
    result = run(rolled_data(exclusion=True,changed=False))
    assert [r['contract'] for r in result.manifest['roll_history']] == [None,'MESU6',None,'MESZ6']
    assert result.manifest['gex_identity']['model_id'] == 'model-A'
    assert not result.trades


def test_expiry_universe_order_does_not_change_canonical_breakout_identity():
    from gamma_bot.data.models import GexSnapshot
    data = mapping_data()
    data = replace(data,events=tuple(replace(e,expiry_days=(1,0)) if isinstance(e,GexSnapshot)
        and e.event_at == OPEN+timedelta(minutes=15) else e for e in data.events))
    strategy = BreakoutStrategy()
    result = run_replay(data,RunConfig(),strategy.variant,strategies=[strategy])
    assert len(result.trades) == 1
    assert result.manifest['gex_identity']['expiry_days'] == (0,1)


def test_import_as_of_does_not_hide_contradictory_snapshot_metadata(dataset_writer):
    from conftest import gex_record
    from gamma_bot.data.io import load_dataset
    path = dataset_writer([gex_record()],metadata={'sources':[{
        'source_id':'synthetic','classification':'synthetic','model_id':'wrong-model'}]})
    with pytest.raises(InvalidDatasetError,match='GEX.*metadata'):
        load_dataset(path,as_of=PRIOR)


@pytest.mark.parametrize('consumer', ['audit','runtime'])
def test_wall_identity_uses_same_canonical_expiry_universe(consumer):
    from gamma_bot.features.mapping import map_levels, stable_wall
    snapshots = tuple(snapshot(i,expiry_days=(1,0) if i == 3 else (0,1)) for i in (0,3,6))
    if consumer == 'audit':
        data = replace(dataset(),events=snapshots)
        assert 'wall_migration' in audit_dataset(data,frozenset({'wall_migration'})).supported_rules
    else:
        history = [map_levels(s,[(point(i,symbol='MESU6',value='6000'),point(i))
            for i in range(minute-4,minute+1)],s.available_at) for minute,s in zip((0,3,6),snapshots)]
        assert stable_wall(history,'s',snapshots[-1].available_at)
