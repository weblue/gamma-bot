"""Causal SPY/MES midpoint mapping and complete-window wall stability."""
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import re
from statistics import median
from typing import Literal

from gamma_bot.data.semantics import snapshot_identity
from gamma_bot.data.models import GexSnapshot, InvalidDatasetError, PricePoint, Quote, RunConfig


@dataclass(frozen=True, kw_only=True)
class MappedWall:
    wall_id: str
    kind: Literal["support", "resistance"]
    level_ticks: int
    concentration: Decimal


@dataclass(frozen=True, kw_only=True)
class MappedRegion:
    region_id: str
    lower_ticks: int | None
    upper_ticks: int | None


@dataclass(frozen=True, kw_only=True)
class MappedGex:
    snapshot: GexSnapshot
    ratio: Decimal
    walls: tuple[MappedWall, ...]
    negative_regions: tuple[MappedRegion, ...]
    available_at: datetime


def quote_midpoint(quote: Quote) -> PricePoint:
    return PricePoint(source_id=quote.source_id, sequence=quote.sequence,
        event_at=quote.event_at, available_at=quote.available_at, symbol=quote.contract,
        value=Decimal(quote.bid_ticks+quote.ask_ticks)/8, price_kind='mid')


class MidpointHistory:
    """One source per symbol; latest causal observation per event-time minute."""
    def __init__(self, config: RunConfig, window_minutes: int | None = None):
        self.config, self.window_minutes = config, window_minutes
        self.points = {}
        self.sources = {}

    def update(self, point: PricePoint, as_of: datetime) -> None:
        if point.price_kind != 'mid':
            return
        source = self.sources.setdefault(point.symbol, point.source_id)
        if source != point.source_id:
            raise InvalidDatasetError('mapping midpoint source switch requires a separate feature engine')
        minute = point.event_at.replace(second=0, microsecond=0)
        points = self.points.setdefault(point.symbol, {})
        old = points.get(minute)
        if old is None or (point.event_at, point.available_at, point.sequence) > (
                old.event_at, old.available_at, old.sequence):
            points[minute] = point
        seconds = (self.window_minutes*60 if self.window_minutes is not None
                   else self.config.mapping_window_seconds)
        cutoff = as_of-timedelta(seconds=seconds)
        for symbol in self.points:
            self.points[symbol] = {m: p for m, p in self.points[symbol].items() if p.event_at >= cutoff}

    def mapped(self, snapshot: GexSnapshot, contract: str, as_of: datetime) -> MappedGex | None:
        mes, spy = self.points.get(contract, {}), self.points.get('SPY', {})
        pairs = [(mes[m], spy[m]) for m in sorted(mes.keys() & spy.keys())]
        return map_levels(snapshot, pairs, as_of, window_minutes=self.window_minutes, config=self.config)


def map_levels(snapshot: GexSnapshot,
               paired_prices: Sequence[tuple[PricePoint, PricePoint]],
               as_of: datetime, *, window_minutes: int | None = None,
               config: RunConfig | None = None) -> MappedGex | None:
    """Pairs are (dated MES midpoint in points, SPY midpoint).

    Five distinct causal minutes minimum; historical pairs are validated at their
    own availability, and both prices in the newest pair must still be fresh now.
    Window defaults to RunConfig.mapping_window_seconds (300); an explicit
    window_minutes overrides only that field for the 5/10/15-minute experiments.
    Unsupported underlyings and indicative mapping prices remain unavailable.
    """
    cfg = config or RunConfig()
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise InvalidDatasetError("mapping as_of must be timezone aware")
    if window_minutes is not None and (type(window_minutes) is not int or window_minutes <= 0):
        raise InvalidDatasetError("mapping window_minutes must be a positive integer")
    window = cfg.mapping_window_seconds if window_minutes is None else window_minutes * 60
    if (snapshot.underlying != "SPY" or snapshot.available_at > as_of
            or (as_of - snapshot.event_at).total_seconds() > cfg.gex_max_age_seconds):
        return None
    valid = []
    for mes, spy in paired_prices:
        pair_at = max(mes.event_at, spy.event_at)
        pair_available = max(mes.available_at, spy.available_at)
        if (mes.price_kind != "mid" or spy.price_kind != "mid" or spy.symbol != "SPY"
                or not re.fullmatch(r"MES(?:[HMUZ]\d{1,4}|\d{4}[HMUZ])", mes.symbol)
                or pair_available > as_of or pair_at < as_of - timedelta(seconds=window)
                or mes.event_at.replace(second=0, microsecond=0)
                    != spy.event_at.replace(second=0, microsecond=0)
                or max((pair_available - p.event_at).total_seconds() for p in (mes, spy))
                    > cfg.mapping_max_age_seconds):
            continue
        valid.append((mes, spy))
    # A run must never pool sources or delivery contracts to pass its warm-up.
    identities = {(m.symbol, m.source_id, s.source_id) for m, s in valid}
    if len(identities) != 1:
        return None
    by_minute = {}
    for mes, spy in sorted(valid, key=lambda pair: (
            max(p.event_at for p in pair), max(p.available_at for p in pair),
            pair[0].sequence, pair[1].sequence)):
        by_minute[mes.event_at.replace(second=0, microsecond=0)] = (mes, spy)
    pairs = list(by_minute.values())
    if len(pairs) < max(5, cfg.mapping_min_pairs):
        return None
    if max((as_of - p.event_at).total_seconds() for p in pairs[-1]) > cfg.mapping_max_age_seconds:
        return None
    ratio = median(m.value / s.value for m, s in pairs)

    def ticks(value: Decimal | None) -> int | None:
        return None if value is None else int((value * ratio * 4).to_integral_value(rounding=ROUND_HALF_UP))

    walls = tuple(MappedWall(wall_id=w.wall_id, kind=w.kind, level_ticks=ticks(w.strike),
                             concentration=w.concentration) for w in snapshot.walls)
    regions = tuple(MappedRegion(region_id=r.region_id, lower_ticks=ticks(r.lower),
                                 upper_ticks=ticks(r.upper)) for r in snapshot.negative_regions)
    # Rounding cannot turn a sub-tick source interval into a usable empty region.
    if any(r.lower_ticks is not None and r.upper_ticks is not None
           and r.lower_ticks >= r.upper_ticks for r in regions):
        return None
    return MappedGex(snapshot=snapshot, ratio=ratio, walls=walls, negative_regions=regions,
                     available_at=max(snapshot.available_at,
                                      *(p.available_at for pair in pairs for p in pair)))


def stable_wall(history: Sequence[MappedGex], wall_id: str, as_of: datetime, *,
                config: RunConfig | None = None) -> bool:
    """Validate every snapshot from the closest start at least five minutes back.

    Snapshot count is a minimum, with no skipped intervening observations. Source
    strikes and concentrations come from the preserved original snapshots.
    """
    cfg = config or RunConfig()
    causal = sorted((g for g in history if g.available_at <= as_of),
                    key=lambda g: (g.snapshot.event_at, g.snapshot.available_at,
                                   g.snapshot.source_id, g.snapshot.sequence))
    if not causal:
        return False
    newest = causal[-1]
    end = newest.snapshot.event_at
    if (as_of - end).total_seconds() > cfg.gex_max_age_seconds:
        return False
    starts = [i for i, g in enumerate(causal)
              if cfg.wall_min_span_seconds <= (end - g.snapshot.event_at).total_seconds()
              <= cfg.wall_max_span_seconds]
    if not starts:
        return False
    start_at = causal[max(starts)].snapshot.event_at
    selected = [g for g in causal if g.snapshot.event_at >= start_at]
    if (len({g.snapshot.event_at for g in selected}) < cfg.wall_snapshot_count
            or any((b.snapshot.event_at - a.snapshot.event_at).total_seconds() > cfg.wall_max_gap_seconds
                   for a, b in zip(selected, selected[1:]))):
        return False
    identities, strikes, concentrations, levels = set(), set(), [], []
    for mapped in selected:
        snap = mapped.snapshot
        identities.add(tuple(snapshot_identity(snap).values()))
        source_wall = next((w for w in snap.walls if w.wall_id == wall_id), None)
        wall = next((w for w in mapped.walls if w.wall_id == wall_id), None)
        if source_wall is None or wall is None:
            return False
        strikes.add((source_wall.strike, source_wall.kind))
        concentrations.append(source_wall.concentration)
        levels.append(wall.level_ticks)
    return (len(identities) == len(strikes) == 1
            and all(b >= a for a, b in zip(concentrations, concentrations[1:]))
            and max(levels) - min(levels) <= cfg.level_tolerance_ticks)
