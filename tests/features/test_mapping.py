from dataclasses import replace
from decimal import Decimal

import pytest

from gamma_bot.data.models import NegativeRegion, Wall
from gamma_bot.features.mapping import map_levels, stable_wall
from .helpers import at, point, snapshot


def test_mapping_uses_causal_median_and_preserves_source_snapshot(pairs):
    mapped = map_levels(snapshot(), pairs, at(15))
    assert mapped.ratio == Decimal("10")
    assert mapped.walls[0].level_ticks == 24000
    assert mapped.snapshot.model_id == "test-only-import-model"
    assert mapped.snapshot.walls[0].strike == Decimal("600")


def test_five_successive_minutes_are_valid_but_stale_latest_is_not(pairs):
    assert map_levels(snapshot(), pairs, at(15)) is not None
    assert map_levels(snapshot(), pairs, at(16, 1)) is None


@pytest.mark.parametrize("bad", ["future", "late", "different_minute", "indicative", "insufficient"])
def test_invalid_pair_cannot_be_replaced_by_fixed_ten_times_ratio(pairs, bad):
    mes, spy = pairs[-1]
    if bad == "future":
        pairs[-1] = (mes, replace(spy, available_at=at(16)))
    elif bad == "late":
        pairs[-1] = (mes, replace(spy, available_at=at(16, 1)))
    elif bad == "different_minute":
        pairs[-1] = (mes, point(14))
    elif bad == "indicative":
        pairs[-1] = (mes, replace(spy, price_kind="indicative"))
    else:
        pairs.pop()
    assert map_levels(snapshot(), pairs, at(15)) is None


def test_duplicate_minutes_and_mixed_contracts_do_not_supply_five_pairs(pairs):
    assert map_levels(snapshot(), [pairs[0]] * 5, at(15)) is None
    pairs[-1] = (replace(pairs[-1][0], symbol="MESU6"), pairs[-1][1])
    assert map_levels(snapshot(), pairs, at(15)) is None


def test_mapping_rounds_half_tick_and_keeps_unbounded_profile(pairs):
    source = snapshot(walls=(Wall(wall_id="s", kind="support", strike=Decimal("600.0125"),
                                 concentration=Decimal("100")),),
                      profile_available=True,
                      negative_regions=(NegativeRegion(region_id="negative", lower=Decimal("601"), upper=None),))
    mapped = map_levels(source, pairs, at(15))
    assert mapped.walls[0].level_ticks == 24001
    assert (mapped.negative_regions[0].lower_ticks, mapped.negative_regions[0].upper_ticks) == (24040, None)


def history(minutes):
    return [map_levels(snapshot(m), [(point(i, "MESM6", "6000"), point(i))
                    for i in range(m-4, m+1)], at(m)) for m in minutes]


def test_three_snapshots_spanning_five_minutes_establish_stability():
    assert stable_wall(history([10, 13, 15]), "s", at(15))
    assert not stable_wall(history([0, 15, 30]), "s", at(30))


def test_minute_cadence_uses_six_observations_to_span_five_minutes():
    assert stable_wall(history(list(range(10, 16))), "s", at(15))


@pytest.mark.parametrize("change", ["strike", "concentration", "migration", "model"])
def test_intervening_contradiction_cannot_be_cherry_picked(change):
    observations = history(list(range(10, 16)))
    middle = observations[2]
    if change in ("strike", "concentration"):
        wall = replace(middle.snapshot.walls[0], **({"strike": Decimal("599")} if change == "strike"
                        else {"concentration": Decimal("99")}))
        observations[2] = replace(middle, snapshot=replace(middle.snapshot, walls=(wall,)))
    elif change == "migration":
        observations[2] = replace(middle, walls=(replace(middle.walls[0], level_ticks=24005),))
    else:
        observations[2] = replace(middle, snapshot=replace(middle.snapshot, model_id="other-model"))
    assert not stable_wall(observations, "s", at(15))


def test_mapping_minimum_five_cannot_be_lowered_by_configuration(pairs):
    from gamma_bot.data.models import RunConfig
    assert map_levels(snapshot(), pairs[-3:], at(15), config=RunConfig(mapping_min_pairs=3)) is None


def test_mapping_window_sensitivity_keeps_five_pairs_and_newest_fresh():
    from gamma_bot.data.models import RunConfig
    old_pairs = [(point(m, "MESM6", "6000"), point(m)) for m in [1, 2, 3, 4, 15]]
    assert map_levels(snapshot(), old_pairs, at(15)) is None
    assert map_levels(snapshot(), old_pairs, at(15), window_minutes=15).ratio == Decimal("10")
    assert map_levels(snapshot(), old_pairs, at(15), config=RunConfig(mapping_window_seconds=900)) is not None


def test_all_observations_at_selected_start_time_are_validated():
    observations = history([10, 10, 13, 15])
    first = observations[0]
    observations[0] = replace(first, snapshot=replace(first.snapshot, sequence=0,
        walls=(replace(first.snapshot.walls[0], concentration=Decimal("101")),)))
    # Both 09:40 observations belong to the window; the decay from 101 to 100
    # cannot be hidden by selecting the second occurrence as the start index.
    assert not stable_wall(observations, "s", at(15))
