"""Monotone availability-clock orchestration of offline causal features."""
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import re
from types import MappingProxyType
from zoneinfo import ZoneInfo

from gamma_bot.data.models import (
    Event, GexSnapshot, InvalidDatasetError, MesBar, OptionSnapshot, PricePoint,
    Quote, RunConfig, Trade,
)
from gamma_bot.data.semantics import canonical_profile, snapshot_identity
from .gex import gex_from_options
from .indicators import OpeningRange, OpeningRangeIndicator, VixIndicator, VixState
from .mapping import MappedGex, MappedRegion, MappedWall, MidpointHistory, quote_midpoint, stable_wall
from .order_flow import Divergence, OrderFlow, PivotDetector, PricePivot


@dataclass(frozen=True, kw_only=True)
class FeatureState:
    as_of: datetime
    contract: str | None = None
    bar: MesBar | None = None
    previous_bar: MesBar | None = None
    opening_range: OpeningRange | None = None
    vix: VixState | None = None
    current_gex: MappedGex | None = None
    previous_gex: MappedGex | None = None
    wall_history: tuple[MappedGex, ...] = ()
    divergences: tuple[Divergence, ...] = ()
    price_pivots: tuple[PricePivot, ...] = ()
    unavailable_rules: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "unavailable_rules", MappingProxyType(dict(self.unavailable_rules)))
        object.__setattr__(self, "wall_history", tuple(self.wall_history))
        object.__setattr__(self, "divergences", tuple(self.divergences))
        object.__setattr__(self, "price_pivots", tuple(self.price_pivots))


class FeatureEngine:
    """Consume events in (available_at, event_at, source_id, sequence) order.

    Feed only events already admitted by the replay clock. Future events cannot be
    inserted ahead of their availability, and backward clocks/conflicting repeats
    fail. A run locks GEX source/model/underlying/expiry/provenance on first import;
    a different model needs a different engine. Contract/date switches reset all
    histories, and CVD/pivots reset again at equity open. Macro/margin are separate
    replay gates, not features marked available here. Replay supplies source
    metadata to gate normalized profile membership; standalone feature callers
    omitting it assert that their snapshots already use canonical semantics.
    """
    def __init__(self, config: RunConfig | None = None, *, mapping_window_minutes: int | None = None,
                 source_metadata: Mapping | None = None):
        self.config = config or RunConfig()
        self.source_metadata = source_metadata
        if mapping_window_minutes is not None and (
                type(mapping_window_minutes) is not int or mapping_window_minutes <= 0):
            raise InvalidDatasetError("mapping_window_minutes must be a positive integer")
        self.mapping_window_minutes = mapping_window_minutes
        self._zone = ZoneInfo(self.config.timezone)
        self._date: date | None = None
        self._contract: str | None = None
        self._gex_identity = None
        self._last_key = None
        self._last_event = None
        self._last_state: FeatureState | None = None
        self._reset_features()

    def _reset_features(self) -> None:
        self._opening = OpeningRangeIndicator(self.config)
        self._vix = VixIndicator(self.config)
        self._vix_state = None
        self._flow = OrderFlow(self.config)
        self._pivots = PivotDetector(self.config)
        self._equity_started = False
        self._bar = self._previous_bar = None
        self._opening_range = None
        self._bar_gex = self._previous_gex = None
        self._gex_snapshot: GexSnapshot | None = None
        self._raw_gex: list[GexSnapshot] = []
        self._history: dict[tuple[str, int], MappedGex] = {}
        self._midpoints = MidpointHistory(self.config, self.mapping_window_minutes)
        self._divergences: tuple[Divergence, ...] = ()

    @property
    def cvd(self) -> int | None:
        return self._flow.value_at(self._last_key[0]) if self._last_key else None

    def _point(self, point: PricePoint, as_of: datetime) -> None:
        if point.price_kind != "mid":
            return
        if point.symbol != "SPY" and point.symbol != self._contract:
            return
        self._midpoints.update(point, as_of)

    def _mapped(self, as_of: datetime) -> MappedGex | None:
        if self._gex_snapshot is None or self._contract is None:
            return None
        return self._midpoints.mapped(self._gex_snapshot, self._contract, as_of)

    def update(self, event: Event) -> FeatureState:
        key = (event.available_at, event.event_at, event.source_id, event.sequence)
        if self._last_key is not None and key <= self._last_key:
            if key == self._last_key and event == self._last_event:
                return self._last_state
            raise InvalidDatasetError("feature events must follow deterministic availability order")
        # An unsupported reconstruction must fail without converting missing inputs
        # into a valid zero-GEX import.
        if isinstance(event, OptionSnapshot):
            gex_from_options(event)
        if isinstance(event, GexSnapshot):
            identity = snapshot_identity(event)
            if self._gex_identity is not None and identity != self._gex_identity:
                raise InvalidDatasetError("GEX source/model/expiry switch requires a separate feature engine")
            self._gex_identity = identity
        local = event.available_at.astimezone(self._zone)
        event_contract = getattr(event, "contract", None)
        if isinstance(event, PricePoint) and re.fullmatch(r"MES(?:[HMUZ]\d{1,4}|\d{4}[HMUZ])", event.symbol):
            event_contract = event.symbol
        if local.date() != self._date:
            self._reset_features()
            self._date = local.date()
        if event_contract is not None and event_contract != self._contract:
            # Initial contract discovery preserves same-session underlying inputs;
            # an actual switch starts all warm-up again.
            if self._contract is not None:
                self._reset_features()
            self._contract = event_contract
        if not self._equity_started and local.time() >= self.config.opening_range_start:
            self._flow.reset()
            self._pivots = PivotDetector(self.config)
            self._divergences = ()
            self._equity_started = True
        if isinstance(event, PricePoint):
            if event.symbol == "VIX" and event.price_kind == "index":
                self._vix_state = self._vix.update(event)
            else:
                self._point(event, event.available_at)
        elif isinstance(event, Quote):
            self._flow.update(event)
            self._point(quote_midpoint(event), event.available_at)
        elif isinstance(event, Trade):
            trade_local = event.event_at.astimezone(self._zone)
            if trade_local.date() == self._date and trade_local.time() >= self.config.opening_range_start:
                self._flow.update(event)
        elif isinstance(event, GexSnapshot):
            self._raw_gex.append(event)
            if self._gex_snapshot is None or event.event_at >= self._gex_snapshot.event_at:
                self._gex_snapshot = event
        current = self._mapped(event.available_at)
        if current is not None:
            self._history[(current.snapshot.source_id, current.snapshot.sequence)] = current
        if isinstance(event, MesBar):
            if self._bar is not None and event.event_at <= self._bar.event_at:
                raise InvalidDatasetError("completed MES bars must follow event-time order")
            self._previous_bar, self._bar = self._bar, event
            self._previous_gex, self._bar_gex = self._bar_gex, current
            same_session = event.start_at.astimezone(self._zone).date() == self._date
            if same_session:
                self._opening_range = self._opening.update(event)
            if (same_session and self._equity_started
                    and event.start_at.astimezone(self._zone).time() >= self.config.opening_range_start):
                self._divergences += self._pivots.update(event, self._flow.value_at(event.event_at))
        cutoff = event.available_at - timedelta(seconds=self.config.wall_max_span_seconds + self.config.gex_max_age_seconds)
        self._raw_gex = [g for g in self._raw_gex if g.event_at >= cutoff]
        self._history = {k: g for k, g in self._history.items() if g.snapshot.event_at >= cutoff}
        self._divergences = tuple(d for d in self._divergences if (
            event.available_at - d.second_at).total_seconds() <= self.config.pivot_max_separation_seconds)
        vix = self._vix_state
        if self._vix.latest is None or (
                event.available_at - self._vix.latest.event_at).total_seconds() > self.config.vix_max_age_seconds:
            vix = None
        history = tuple(sorted(self._history.values(), key=lambda g: (
            g.snapshot.event_at, g.snapshot.available_at, g.snapshot.sequence)))
        unavailable = {}
        if (self._bar is None or self._bar.event_at - self._bar.start_at != timedelta(minutes=1)
                or self._bar.start_at.second != 0 or self._bar.start_at.microsecond != 0
                or self._bar.start_at.astimezone(self._zone).date() != self._date):
            unavailable["mes_prices"] = "current-session completed one-minute MES bar unavailable"
        if self._opening_range is None:
            unavailable["opening_range"] = "complete 09:30–09:45 minute history unavailable"
        if vix is None or vix.sma10 is None:
            unavailable["vix"] = "ten contiguous completed VIX minutes or fresh latest observation unavailable"
        if self._gex_snapshot is None or (
                event.available_at - self._gex_snapshot.event_at).total_seconds() > self.config.gex_max_age_seconds:
            unavailable["gex"] = "fresh imported GEX snapshot unavailable"
        if current is None:
            unavailable["mapping"] = "five causal paired midpoint minutes and fresh latest pair unavailable"
        if (current is None or not current.snapshot.profile_available or
                self.source_metadata is not None and not canonical_profile(
                    self.source_metadata.get(current.snapshot.source_id, {}))):
            unavailable["negative_regions"] = "documented price-dependent negative-gamma profile unavailable"
        if self._flow.value_at(event.available_at) is None:
            unavailable["cvd"] = "classified session trade history unavailable or contains unknown volume"
        walls_stable = current is not None and any(stable_wall(
            history, w.wall_id, event.available_at, config=self.config) for w in current.walls)
        # A snapshot that could not be mapped is not silently skipped to obtain
        # stability across a contradictory or missing intervening observation.
        if current is not None:
            starts = [g.event_at for g in self._raw_gex if
                self.config.wall_min_span_seconds <=
                (current.snapshot.event_at - g.event_at).total_seconds() <=
                self.config.wall_max_span_seconds]
            earliest = max(starts) if starts else current.snapshot.event_at
            missing_history = any(earliest <= g.event_at <= current.snapshot.event_at
                and (g.source_id, g.sequence) not in self._history for g in self._raw_gex)
            walls_stable = walls_stable and not missing_history
        if not walls_stable:
            unavailable["wall_migration"] = "complete stable wall window unavailable"
        state = FeatureState(as_of=event.available_at, contract=self._contract,
            bar=self._bar, previous_bar=self._previous_bar, opening_range=self._opening_range,
            vix=vix, current_gex=current, previous_gex=self._previous_gex,
            wall_history=history, divergences=self._divergences,
            price_pivots=tuple(p for p in self._pivots.price_pivots if
                (event.available_at - p.at).total_seconds() <= 2 * self.config.pivot_max_separation_seconds),
            unavailable_rules=unavailable)
        self._last_key, self._last_event, self._last_state = key, event, state
        return state
