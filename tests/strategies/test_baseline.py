from dataclasses import replace
from datetime import timedelta

import pytest

from gamma_bot.strategies.baseline import OpeningRangeBaseline
from gamma_bot.strategies.breakout import BreakoutStrategy
from gamma_bot.strategies.models import Variant
from gamma_bot.strategies.reversal import ReversalStrategy
from .helpers import at, breakout_state, reversal_state


def test_price_only_baseline_uses_same_crossing_and_structural_stop(breakout):
    state = replace(breakout, current_gex=None, previous_gex=None,
        unavailable_rules={r: "missing" for r in ("gex", "mapping", "vix", "cvd", "wall_migration", "negative_regions")})
    signal = OpeningRangeBaseline().on_state(state)
    assert signal is not None
    assert signal.strategy == "baseline"
    assert signal.variant_name == "opening_range_baseline"
    assert signal.stop_ticks == (23995 if signal.direction == "long" else 24013)
    assert signal.reference_ticks == state.bar.close_ticks
    assert set(signal.omitted_rules) == {"gex", "mapping", "negative_regions", "vix", "cvd", "wall_migration"}


@pytest.mark.parametrize("change", ["opening_range", "previous_bar", "bar", "contract", "at_boundary", "already_outside", "missing_prices"])
def test_baseline_still_requires_actual_range_crossing(breakout, change):
    if change in {"opening_range", "previous_bar", "bar", "contract"}:
        breakout = replace(breakout, **{change: None})
    elif change == "at_boundary":
        breakout = replace(breakout, bar=replace(breakout.bar,
            close_ticks=24008 if breakout.bar.close_ticks == 24009 else 24000))
    elif change == "already_outside":
        breakout = replace(breakout, previous_bar=replace(breakout.previous_bar,
            close_ticks=breakout.bar.close_ticks))
    else:
        breakout = replace(breakout, unavailable_rules={"mes_prices": "missing"})
    assert OpeningRangeBaseline().on_state(breakout) is None


@pytest.mark.parametrize("factory,state_factory", [(ReversalStrategy, reversal_state),
    (BreakoutStrategy, breakout_state), (OpeningRangeBaseline, breakout_state)])
def test_only_actual_fills_consume_trigger_and_session_reset_restores_it(factory, state_factory):
    strategy = factory()
    state = state_factory()
    signal = strategy.on_state(state)
    assert signal is not None
    assert strategy.on_state(state) == signal  # Rejection/cancellation leaves setup eligible.
    strategy.on_entry_filled(signal)
    assert strategy.on_state(state) is None
    # A different bar still cannot reuse the filled source wall/region trigger.
    next_state = replace(state, as_of=at(21), bar=replace(state.bar, event_at=at(21),
        available_at=at(21), start_at=at(20)))
    assert strategy.on_state(next_state) is None
    next_day = timedelta(days=1)
    strategy.reset_session((at(20) + next_day).date())
    # Return to this date is a fresh session, so it can propose again.
    strategy.reset_session(at(20).date())
    assert strategy.on_state(state) is not None


def test_fill_blocks_other_direction_until_a_later_completed_bar():
    strategy = ReversalStrategy()
    signal = strategy.on_state(reversal_state())
    strategy.on_entry_filled(signal)
    short = reversal_state("short")
    assert strategy.on_state(short) is None
    later = replace(short, as_of=at(21), bar=replace(short.bar, event_at=at(21),
        available_at=at(21), start_at=at(20)))
    assert strategy.on_state(later).direction == "short"


def test_trigger_uses_source_model_session_and_wall_identity():
    state = reversal_state()
    original = ReversalStrategy().on_state(state).trigger_id
    gex = state.current_gex
    for key, value in (("source_id", "other-source"), ("model_id", "other-model")):
        modified = replace(state, current_gex=replace(gex, snapshot=replace(gex.snapshot, **{key: value})))
        # Keep wall-history identity consistent with the selected source/model.
        modified = replace(modified, wall_history=tuple(replace(h, snapshot=replace(h.snapshot, **{key: value})) for h in state.wall_history))
        assert ReversalStrategy().on_state(modified).trigger_id != original
    assert BreakoutStrategy().on_state(breakout_state()).trigger_id != original


def test_reduced_reversal_omits_only_explicit_vix_and_exposes_it_on_signal():
    state = replace(reversal_state(), vix=None, unavailable_rules={"vix": "missing"})
    strategy = ReversalStrategy(variant=Variant(name="reversal_without_vix",
        enabled_filters=frozenset({"mes_prices", "opening_range", "gex", "mapping", "cvd", "wall_migration"}),
        omitted_rules=("vix",)))
    signal = strategy.on_state(state)
    assert signal is not None
    assert signal.strategy == "reversal"
    assert signal.variant_name == "reversal_without_vix"
    assert signal.omitted_rules == ("vix",)
    assert strategy.on_state(replace(state, divergences=())) is None


@pytest.mark.parametrize("variant", [
    Variant(name="reversal", enabled_filters=frozenset({"mes_prices", "opening_range", "gex", "mapping", "cvd", "wall_migration"}), omitted_rules=("vix",)),
    Variant(name="hidden_omission", enabled_filters=frozenset({"mes_prices", "opening_range", "gex", "mapping", "cvd", "wall_migration"}), omitted_rules=()),
    Variant(name="price_less", enabled_filters=frozenset({"opening_range", "gex", "mapping", "cvd", "wall_migration", "vix"}), omitted_rules=("mes_prices",)),
])
def test_variant_cannot_hide_omissions_or_drop_core_prices(variant):
    with pytest.raises(ValueError):
        ReversalStrategy(variant=variant)


def test_breakout_cannot_omit_region_semantics_and_keep_breakout_name():
    with pytest.raises(ValueError):
        BreakoutStrategy(variant=Variant(name="breakout", enabled_filters=frozenset({"mes_prices", "opening_range", "gex", "mapping"}), omitted_rules=("negative_regions",)))


@pytest.mark.parametrize("name", ["breakout", "baseline", "opening_range_baseline"])
def test_reduced_variant_cannot_borrow_another_reserved_full_name(name):
    with pytest.raises(ValueError):
        ReversalStrategy(variant=Variant(name=name,
            enabled_filters=frozenset({"mes_prices", "opening_range", "gex", "mapping", "cvd", "wall_migration"}),
            omitted_rules=("vix",)))


@pytest.mark.parametrize("factory,state_factory", [(ReversalStrategy, reversal_state),
    (BreakoutStrategy, breakout_state), (OpeningRangeBaseline, breakout_state)])
def test_session_account_and_macro_gates_are_external(factory, state_factory):
    state = state_factory()
    assert factory().on_state(replace(state,
        unavailable_rules={"macro": "external calendar gate", "margin": "external account gate"})) is not None


@pytest.mark.parametrize("factory,state_factory", [(ReversalStrategy, reversal_state),
    (BreakoutStrategy, breakout_state), (OpeningRangeBaseline, breakout_state)])
def test_same_session_reset_does_not_allow_a_filled_trigger_again(factory, state_factory):
    strategy = factory()
    state = state_factory()
    strategy.on_entry_filled(strategy.on_state(state))
    strategy.reset_session(at(20).date())
    assert strategy.on_state(state) is None


def test_unknown_fill_cannot_consume_a_different_experiments_trigger():
    strategy = ReversalStrategy()
    other = BreakoutStrategy().on_state(breakout_state())
    with pytest.raises(ValueError):
        strategy.on_entry_filled(other)
    assert strategy.on_state(reversal_state()) is not None
