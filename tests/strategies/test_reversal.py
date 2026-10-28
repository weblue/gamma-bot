from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from gamma_bot.data.models import RunConfig
from gamma_bot.strategies.reversal import ReversalStrategy
from .helpers import at, reversal_state


def test_confirmed_reversal_has_structural_stop(reversal):
    signal = ReversalStrategy().on_state(reversal)
    assert signal is not None
    assert signal.direction == reversal.divergences[0].direction
    assert signal.reference_ticks == reversal.bar.close_ticks
    assert signal.stop_ticks == (23998 if signal.direction == "long" else 24010)
    assert signal.decision_at == at(20)
    assert signal.decision_at >= reversal.divergences[0].confirmed_at
    assert signal.strategy == "reversal"
    assert signal.omitted_rules == ()


@pytest.mark.parametrize("field", ["bar", "contract", "opening_range", "current_gex", "vix"])
def test_missing_required_object_rejects_full_reversal(reversal, field):
    assert ReversalStrategy().on_state(replace(reversal, **{field: None})) is None


@pytest.mark.parametrize("rule", ["mes_prices", "opening_range", "gex", "mapping", "vix", "cvd", "wall_migration"])
def test_missing_required_capability_rejects_full_reversal(reversal, rule):
    assert ReversalStrategy().on_state(replace(reversal, unavailable_rules={rule: "missing"})) is None


@pytest.mark.parametrize("changes", [dict(current=Decimal("22"), sma10=Decimal("23"), previous=Decimal("23")),
    dict(current=Decimal("19"), sma10=Decimal("19"), previous=Decimal("20")),
    dict(current=Decimal("18"), previous=Decimal("17")), dict(sma10=None), dict(previous=None)])
def test_strict_vix_rules(reversal, changes):
    assert ReversalStrategy().on_state(replace(reversal, vix=replace(reversal.vix, **changes))) is None


def test_vix_ceiling_comes_from_config(reversal):
    assert ReversalStrategy(RunConfig(vix_ceiling=Decimal("17"))).on_state(reversal) is None


@pytest.mark.parametrize("change", ["zero_gex", "negative_gex", "no_wall", "wrong_kind", "no_history", "migrating", "decreasing", "misaligned", "pivot_away", "not_reclaimed", "no_divergence", "unconfirmed", "expired", "wrong_cvd"])
def test_reversal_rejects_failed_setup_predicate(reversal, change):
    gex = reversal.current_gex
    d = reversal.divergences[0]
    long = d.direction == "long"
    if change in {"zero_gex", "negative_gex"}:
        reversal = replace(reversal, current_gex=replace(gex, snapshot=replace(gex.snapshot,
            total_gex=Decimal("0" if change == "zero_gex" else "-1"))))
    elif change == "no_wall":
        reversal = replace(reversal, current_gex=replace(gex, walls=()))
    elif change == "wrong_kind":
        reversal = replace(reversal, current_gex=replace(gex, walls=(replace(gex.walls[0], kind="resistance" if long else "support"),)))
    elif change == "no_history":
        reversal = replace(reversal, wall_history=())
    elif change == "migrating":
        first = reversal.wall_history[0]
        reversal = replace(reversal, wall_history=(replace(first, walls=(replace(first.walls[0], level_ticks=23900),)), *reversal.wall_history[1:]))
    elif change == "decreasing":
        first = reversal.wall_history[0]
        reversal = replace(reversal, wall_history=(replace(first, snapshot=replace(first.snapshot,
            walls=(replace(first.snapshot.walls[0], concentration=Decimal("101")),))), *reversal.wall_history[1:]))
    elif change == "misaligned":
        reversal = replace(reversal, opening_range=replace(reversal.opening_range, **{"low_ticks" if long else "high_ticks": 24100}))
    elif change == "pivot_away":
        reversal = replace(reversal, divergences=(replace(d, second_pivot_ticks=23990 if long else 24018),))
    elif change == "not_reclaimed":
        reversal = replace(reversal, bar=replace(reversal.bar, close_ticks=24000 if long else 24008))
    elif change == "no_divergence":
        reversal = replace(reversal, divergences=())
    elif change == "unconfirmed":
        reversal = replace(reversal, divergences=(replace(d, confirmed_at=at(21)),))
    elif change == "expired":
        reversal = replace(reversal, divergences=(replace(d, first_at=at(-5), second_at=at(-2)),))
    else:
        reversal = replace(reversal, divergences=(replace(d, cvd_second=d.cvd_first),))
    assert ReversalStrategy().on_state(reversal) is None


def test_wall_touch_and_alignment_tolerance_uses_ticks():
    state = reversal_state()
    state = replace(state, opening_range=replace(state.opening_range, low_ticks=24004),
        divergences=(replace(state.divergences[0], second_pivot_ticks=23996),))
    assert ReversalStrategy().on_state(state) is not None
    assert ReversalStrategy(RunConfig(level_tolerance_ticks=3)).on_state(state) is None


def test_later_input_update_does_not_reuse_old_signal_bar(reversal):
    assert ReversalStrategy().on_state(replace(reversal, as_of=at(20) + timedelta(seconds=1))) is None


@pytest.mark.parametrize("direction", ["long", "short"])
def test_cvd_ablation_uses_real_price_pivots_without_divergence(direction):
    from gamma_bot.features.state import PricePivot
    from gamma_bot.strategies.models import Variant
    state = reversal_state(direction)
    long = direction == "long"
    pivots = (PricePivot(direction=direction, at=at(15), confirmed_at=at(17),
                         price_ticks=24000 if long else 24008),
              PricePivot(direction=direction, at=at(18), confirmed_at=at(20),
                         price_ticks=23999 if long else 24009))
    state = replace(state, divergences=(), price_pivots=pivots, unavailable_rules={"cvd": "missing"})
    strategy = ReversalStrategy(variant=Variant(name="reversal_without_cvd",
        enabled_filters=frozenset({"mes_prices", "opening_range", "gex", "mapping", "vix", "wall_migration"}),
        omitted_rules=("cvd",)))
    signal = strategy.on_state(state)
    assert signal is not None
    assert signal.omitted_rules == ("cvd",)
    assert signal.stop_ticks == (23998 if long else 24010)
    assert ReversalStrategy().on_state(state) is None
    assert strategy.on_state(replace(state, price_pivots=pivots[:1])) is None
    assert strategy.on_state(replace(state, price_pivots=(pivots[0], replace(pivots[1], confirmed_at=at(21))))) is None
    # Equal pivots have no lower-low/higher-high price structure.
    assert strategy.on_state(replace(state, price_pivots=(pivots[0], replace(pivots[1], price_ticks=pivots[0].price_ticks)))) is None


def test_reversal_rejects_zero_or_negative_structural_distance():
    state = reversal_state()
    # Pivot remains within wall tolerance, but a stop at 24002 leaves no distance.
    state = replace(state, divergences=(replace(state.divergences[0], second_pivot_ticks=24003),))
    assert ReversalStrategy().on_state(state) is None


@pytest.mark.parametrize("field", ["current_gex", "vix"])
def test_future_inputs_cannot_enable_reversal(field):
    state = reversal_state()
    assert ReversalStrategy().on_state(replace(state,
        **{field: replace(getattr(state, field), available_at=at(21))})) is None


def test_stale_gex_cannot_enable_reversal_even_with_old_wall_history():
    state = reversal_state()
    stale = replace(state.current_gex, snapshot=replace(state.current_gex.snapshot,
        event_at=at(14), available_at=at(14)), available_at=at(14))
    assert ReversalStrategy().on_state(replace(state, current_gex=stale)) is None
