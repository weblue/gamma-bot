from dataclasses import replace
from decimal import Decimal

import pytest

from gamma_bot.data.models import InvalidDatasetError, Wall
from gamma_bot.features.state import FeatureEngine
from .helpers import at, bar, point, snapshot, trade


def ordered(events):
    return sorted(events, key=lambda e: (e.available_at, e.event_at, e.source_id, e.sequence))


def test_engine_only_observes_arrived_inputs_and_expires_vix():
    engine = FeatureEngine()
    for minute in range(10):
        state = engine.update(point(minute, "VIX", "20"))
    assert state.vix.current == Decimal("20")
    future = point(10, "VIX", "18", available_at=at(12))
    assert engine.update(bar(9)).vix.current == Decimal("20")
    state = engine.update(future)
    assert state.vix is None  # event is already 120 seconds old when first available
    assert "vix" in state.unavailable_rules
    with pytest.raises(InvalidDatasetError, match="order"):
        engine.update(point(8, "VIX", "1"))


def test_contract_switch_resets_opening_range_cvd_and_mapping():
    engine = FeatureEngine()
    for minute in range(15):
        engine.update(bar(minute))
    engine.update(trade(15, available_at=at(15, 1)))
    state = engine.update(bar(15, contract="MESU6"))
    assert state.contract == "MESU6"
    assert state.opening_range is None
    assert state.previous_bar is None
    assert "cvd" in state.unavailable_rules
    assert state.current_gex is None


def test_cvd_resets_at_equity_open_and_ignores_late_preopen_trade():
    engine = FeatureEngine()
    engine.update(trade(-1, "sell", size=500))
    engine.update(trade(0, "buy", size=2))
    state = engine.update(trade(-1, "sell", size=900, sequence=2, available_at=at(0, 1)))
    assert engine.cvd == 2
    assert "cvd" not in state.unavailable_rules


def test_previous_gex_is_frozen_at_previous_bar_decision():
    engine = FeatureEngine()
    events = [point(m, "MESM6", "6000") for m in range(10, 17)]
    events += [point(m) for m in range(10, 17)]
    events += [snapshot(14), bar(14)]
    for event in ordered([e for e in events if e.available_at <= at(15)]):
        state = engine.update(event)
    assert state.current_gex.walls[0].level_ticks == 24000
    changed = snapshot(15, available_at=at(15, 1), walls=(Wall(wall_id="s", kind="support",
        strike=Decimal("601"), concentration=Decimal("100")),))
    engine.update(changed)
    state = engine.update(bar(15))
    assert state.previous_gex.walls[0].level_ticks == 24000
    assert state.current_gex.walls[0].level_ticks == 24040
    assert state.previous_bar.close_ticks == state.bar.close_ticks == 24000


def test_strike_level_negative_total_does_not_enable_regions():
    engine = FeatureEngine()
    events = [snapshot(15, total_gex=Decimal("-100"))]
    for m in range(11, 16):
        events.extend([point(m), point(m, "MESM6", "6000")])
    for event in ordered(events):
        state = engine.update(event)
    assert state.current_gex.snapshot.total_gex == Decimal("-100")
    assert "negative_regions" in state.unavailable_rules


def test_engine_rejects_source_model_universe_switch():
    engine = FeatureEngine()
    engine.update(snapshot(10))
    with pytest.raises(InvalidDatasetError, match="source/model/expiry"):
        engine.update(snapshot(11, model_id="another-test-model"))


def test_engine_divergence_is_unavailable_until_second_confirmation_arrives():
    engine = FeatureEngine()
    lows = [23995, 23994, 23990, 23994, 23995, 23994, 23989, 23994, 23995]
    events = [bar(m, low=low) for m, low in enumerate(lows)]
    events += [trade(0, "sell", 10), trade(3, "buy", 5)]
    events = ordered(events)
    for event in events[:-1]:
        assert engine.update(event).divergences == ()
    divergence, = engine.update(events[-1]).divergences
    assert (divergence.confirmed_at, divergence.cvd_first, divergence.cvd_second) == (at(9), -10, -5)


def test_engine_retains_quote_units_and_expires_gex_by_information_time():
    from .helpers import quote
    engine = FeatureEngine()
    events = [snapshot(15)]
    for m in range(11, 16):
        events += [point(m), quote(m, bid_ticks=23999, ask_ticks=24001)]
    for event in ordered(events):
        state = engine.update(event)
    assert state.current_gex.ratio == Decimal("10")
    assert state.current_gex.walls[0].level_ticks == 24000
    state = engine.update(bar(20))
    assert state.current_gex is None
    assert "gex" in state.unavailable_rules


def test_unmapped_snapshot_outside_selected_wall_window_does_not_block_stability():
    engine = FeatureEngine()
    events = [snapshot(0)]  # No mapping warm-up existed at this early observation.
    events += [snapshot(m) for m in range(10, 16)]
    for m in range(6, 16):
        events += [point(m), point(m, "MESM6", "6000")]
    for event in ordered(events):
        state = engine.update(event)
    assert "wall_migration" not in state.unavailable_rules


def test_new_session_discards_old_opening_range_and_late_previous_session_bars():
    from datetime import timedelta
    engine = FeatureEngine()
    for minute in range(15):
        engine.update(bar(minute))
    day_later = at(15) + timedelta(days=1)
    state = engine.update(point(15, "VIX", "20", event_at=day_later, available_at=day_later))
    assert state.opening_range is None
    for minute in range(15):
        state = engine.update(bar(minute, sequence=20000+minute,
                                 available_at=day_later + timedelta(seconds=minute+1)))
    assert state.opening_range is None


def test_nonminute_or_prior_session_bars_cannot_supply_decision_prices():
    from datetime import timedelta
    engine = FeatureEngine()
    state = engine.update(bar(0, event_at=at(2), available_at=at(2)))
    assert "mes_prices" in state.unavailable_rules
    engine = FeatureEngine()
    state = engine.update(bar(0, available_at=at(1) + timedelta(days=1)))
    assert "mes_prices" in state.unavailable_rules


def test_engine_mapping_window_override_is_propagated():
    engine = FeatureEngine(mapping_window_minutes=15)
    events = [snapshot(15)]
    for minute in [1, 2, 3, 4, 15]:
        events += [point(minute), point(minute, "MESM6", "6000")]
    for event in ordered(events):
        state = engine.update(event)
    assert state.current_gex.ratio == Decimal("10")


@pytest.mark.parametrize("ordering,expected", [
    ("cross_source_tie", None), ("same_source_sequence", 10),
    ("different_availability", 10), ("different_event_time", 10),
])
def test_import_audit_and_runtime_share_quote_predecessor_policy(dataset_writer, ordering, expected):
    from gamma_bot.data.io import load_dataset
    from gamma_bot.data.provenance import to_jsonable
    from .helpers import quote

    q = quote(0, source_id="a-quotes", sequence=1)
    t = trade(0, "unknown", price=24001, source_id="z-trades", sequence=2)
    if ordering == "same_source_sequence":
        t = replace(t, source_id=q.source_id)
    elif ordering == "different_availability":
        t = replace(t, available_at=at(0, 1))
    elif ordering == "different_event_time":
        q = replace(q, available_at=at(0, 1))
        t = replace(t, event_at=at(0, 1), available_at=at(0, 1))
    records = [dict(type="quote", **to_jsonable(q)), dict(type="trade", **to_jsonable(t))]
    dataset = load_dataset(dataset_writer(records))
    engine = FeatureEngine()
    for event in dataset.events:
        state = engine.update(event)
    if expected is None:
        assert "cvd" in dataset.coverage.missing_rules
        assert "cvd" in state.unavailable_rules
    else:
        assert "cvd" in dataset.coverage.supported_rules
        assert "cvd" not in state.unavailable_rules
    assert engine.cvd == expected
