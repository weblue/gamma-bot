from dataclasses import replace
from decimal import Decimal

import pytest

from gamma_bot.strategies.breakout import BreakoutStrategy
from .helpers import at, breakout_state, profile


def test_breakout_requires_price_crossing_and_uses_signal_bar_stop(breakout):
    signal = BreakoutStrategy().on_state(breakout)
    assert signal is not None
    assert signal.direction == ("long" if breakout.bar.close_ticks == 24009 else "short")
    assert signal.stop_ticks == (23995 if signal.direction == "long" else 24013)
    assert signal.reference_ticks == breakout.bar.close_ticks
    assert signal.decision_at == at(20)
    assert signal.strategy == "breakout"


@pytest.mark.parametrize("rule", ["mes_prices", "opening_range", "gex", "mapping", "negative_regions"])
def test_missing_breakout_capability_is_not_passed(breakout, rule):
    assert BreakoutStrategy().on_state(replace(breakout, unavailable_rules={rule: "missing"})) is None


@pytest.mark.parametrize("field", ["bar", "contract", "previous_bar", "opening_range", "previous_gex", "current_gex"])
def test_missing_breakout_object_is_not_passed(breakout, field):
    assert BreakoutStrategy().on_state(replace(breakout, **{field: None})) is None


def test_mean_reversion_filters_do_not_reject_breakout(breakout):
    from gamma_bot.features.state import VixState
    state = replace(breakout, vix=VixState(current=Decimal("30"), previous=Decimal("29"),
        sma10=Decimal("28"), available_at=at(20)))
    assert BreakoutStrategy().on_state(state) is not None


def test_profile_migration_without_price_move_does_not_trigger(breakout):
    state = replace(breakout, previous_bar=replace(breakout.previous_bar,
        close_ticks=breakout.bar.close_ticks))
    assert BreakoutStrategy().on_state(state) is None


@pytest.mark.parametrize("change", ["at_boundary", "already_inside", "past_region", "current_not_negative", "current_positive_total", "no_semantics", "previous_no_semantics", "misaligned", "profile_switch", "future_prior"])
def test_breakout_rejects_invalid_crossing_or_profile(breakout, change):
    long = breakout.bar.close_ticks == 24009
    boundary = 24008 if long else 24000
    if change == "at_boundary":
        breakout = replace(breakout, bar=replace(breakout.bar, close_ticks=boundary))
    elif change == "already_inside":
        breakout = replace(breakout, previous_bar=replace(breakout.previous_bar,
            close_ticks=24009 if long else 23999))
    elif change == "past_region":
        old = breakout.previous_gex
        region = replace(old.negative_regions[0], **{"upper_ticks" if long else "lower_ticks": breakout.bar.close_ticks if long else breakout.bar.close_ticks + 1})
        breakout = replace(breakout, previous_gex=replace(old, negative_regions=(region,)))
    elif change == "current_not_negative":
        breakout = replace(breakout, current_gex=replace(breakout.current_gex, negative_regions=()))
    elif change == "current_positive_total":
        current = breakout.current_gex
        breakout = replace(breakout, current_gex=replace(current,
            snapshot=replace(current.snapshot, total_gex=Decimal("1"))))
    elif change in {"no_semantics", "previous_no_semantics"}:
        key = "current_gex" if change == "no_semantics" else "previous_gex"
        current = getattr(breakout, key)
        breakout = replace(breakout, **{key: replace(current,
            snapshot=replace(current.snapshot, profile_available=False, negative_regions=()))})
    elif change == "misaligned":
        breakout = replace(breakout, opening_range=replace(breakout.opening_range,
            **{"high_ticks" if long else "low_ticks": boundary+5}))
    elif change == "profile_switch":
        current = breakout.current_gex
        breakout = replace(breakout, current_gex=replace(current,
            snapshot=replace(current.snapshot, model_id="other-model")))
    else:
        breakout = replace(breakout, previous_gex=replace(breakout.previous_gex, available_at=at(20)))
    assert BreakoutStrategy().on_state(breakout) is None


def test_frozen_boundary_can_trigger_even_when_current_boundary_moves():
    state = breakout_state()
    state = replace(state, current_gex=profile(20, negative=True, level=24007))
    assert BreakoutStrategy().on_state(state) is not None
