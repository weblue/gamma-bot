"""Hand-authored synthetic strategy states, never market evidence."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from gamma_bot.data.models import GexSnapshot, MesBar, NegativeRegion, Wall
from gamma_bot.features.state import (
    Divergence, FeatureState, MappedGex, MappedRegion, MappedWall, OpeningRange, VixState,
)

BASE = datetime(2026, 6, 15, 13, 30, tzinfo=timezone.utc)


def at(minute):
    return BASE + timedelta(minutes=minute)


def bar(minute=20, *, close=24002, low=23996, high=24010):
    return MesBar(source_id="synthetic-bars", sequence=minute, event_at=at(minute),
        available_at=at(minute), start_at=at(minute-1), contract="MESM6",
        open_ticks=close, high_ticks=high, low_ticks=low, close_ticks=close, volume=10)


def profile(minute=20, *, direction="long", negative=False, level=None, source="synthetic-gex", model="synthetic-model"):
    level = level if level is not None else (24000 if direction == "long" else 24008)
    kind = "support" if direction == "long" else "resistance"
    regions = ((MappedRegion(region_id="r", lower_ticks=level, upper_ticks=24040),)
               if direction == "long" else
               (MappedRegion(region_id="r", lower_ticks=23960, upper_ticks=level),)) if negative else ()
    snapshot = GexSnapshot(source_id=source, sequence=minute, event_at=at(minute),
        available_at=at(minute), underlying="SPY", spot=Decimal("600"),
        total_gex=Decimal("-100" if negative else "100"),
        walls=(Wall(wall_id="w", kind=kind, strike=Decimal(level) / 40,
                    concentration=Decimal("100")),),
        negative_regions=tuple(NegativeRegion(region_id=r.region_id,
            lower=Decimal(r.lower_ticks) / 40 if r.lower_ticks else None,
            upper=Decimal(r.upper_ticks) / 40 if r.upper_ticks else None) for r in regions),
        profile_available=negative, model_id=model, expiry_days=(0, 1),
        oi_published_at=at(-100), provenance="modeled")
    return MappedGex(snapshot=snapshot, ratio=Decimal("10"),
        walls=(MappedWall(wall_id="w", kind=kind, level_ticks=level,
                         concentration=Decimal("100")),),
        negative_regions=regions, available_at=at(minute))


def reversal_state(direction="long"):
    long = direction == "long"
    history = tuple(profile(m, direction=direction) for m in (14, 17, 20))
    return FeatureState(as_of=at(20), contract="MESM6",
        bar=bar(close=24002 if long else 24006),
        previous_bar=bar(19), opening_range=OpeningRange(high_ticks=24008, low_ticks=24000),
        vix=VixState(current=Decimal("18"), previous=Decimal("18"),
                     sma10=Decimal("19"), available_at=at(20)),
        current_gex=history[-1], wall_history=history,
        divergences=(Divergence(direction=direction, first_at=at(15), second_at=at(18),
            confirmed_at=at(20), second_pivot_ticks=23999 if long else 24009,
            cvd_first=10, cvd_second=20 if long else 0),))


def breakout_state(direction="long"):
    long = direction == "long"
    return FeatureState(as_of=at(20), contract="MESM6",
        bar=bar(close=24009 if long else 23999, low=23996, high=24012),
        previous_bar=bar(19, close=24008 if long else 24000),
        opening_range=OpeningRange(high_ticks=24008, low_ticks=24000),
        previous_gex=profile(19, direction=direction, negative=True, level=24008 if long else 24000),
        current_gex=profile(20, direction=direction, negative=True, level=24008 if long else 24000),
        unavailable_rules={"vix": "missing", "cvd": "missing", "wall_migration": "missing"})
