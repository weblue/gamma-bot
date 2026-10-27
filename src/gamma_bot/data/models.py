"""Immutable shared contracts. Prices in MES records are integer quarter-point ticks."""
from dataclasses import dataclass, field, fields
from datetime import date, datetime, time
from decimal import Decimal
from math import isfinite
import re
from types import MappingProxyType, UnionType
from typing import Literal, Mapping, Union, get_args, get_origin, get_type_hints
from collections.abc import Mapping as MappingABC
from zoneinfo import ZoneInfo


class InvalidDatasetError(ValueError):
    """Corrupt data or invalid research configuration."""


class CapabilityError(ValueError):
    """An input or model capability is unavailable."""


RULE_IDS = frozenset({"mes_prices", "gex", "negative_regions", "mapping", "cvd", "vix",
                      "wall_migration", "opening_range", "macro", "margin"})
MACRO_CATEGORIES = Literal["CPI", "PPI", "EmploymentSituation", "FOMCDecision",
                           "FOMCPressConference", "ISMManufacturing", "ISMServices"]


def freeze(value):
    if isinstance(value, MappingABC):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    return value


def _check(value, annotation, name):
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (Union, UnionType):
        for option in args:
            try:
                _check(value, option, name)
                return
            except InvalidDatasetError:
                pass
        raise InvalidDatasetError(f"{name}: invalid value/type")
    if origin is Literal:
        if value not in args or type(value) not in {type(a) for a in args}:
            raise InvalidDatasetError(f"{name}: expected one of {args}")
    elif origin is tuple:
        if not isinstance(value, tuple):
            raise InvalidDatasetError(f"{name}: expected immutable tuple")
        for item in value:
            _check(item, args[0], name)
    elif origin is frozenset:
        if not isinstance(value, frozenset) or any(not isinstance(v, args[0]) for v in value):
            raise InvalidDatasetError(f"{name}: expected frozenset")
    elif origin in (MappingABC, Mapping):
        if not isinstance(value, MappingABC):
            raise InvalidDatasetError(f"{name}: expected mapping")
    elif annotation is datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise InvalidDatasetError(f"{name}: timestamp must be aware UTC")
        if value.utcoffset().total_seconds() != 0:
            raise InvalidDatasetError(f"{name}: timestamp must be UTC")
    elif annotation is Decimal:
        if not isinstance(value, Decimal) or not value.is_finite():
            raise InvalidDatasetError(f"{name}: expected finite Decimal")
    elif annotation is float:
        if type(value) not in (int, float) or not isfinite(value):
            raise InvalidDatasetError(f"{name}: expected finite number")
    elif annotation in (int, bool, str, date, time, type(None)):
        if type(value) is not annotation or (annotation is str and not value.strip()):
            raise InvalidDatasetError(f"{name}: invalid {annotation.__name__}")
    elif not isinstance(value, annotation):
        raise InvalidDatasetError(f"{name}: invalid object")


class Validated:
    def __post_init__(self):
        for name, hint in get_type_hints(type(self)).items():
            _check(getattr(self, name), hint, name)


def _nonnegative(**values):
    for name, value in values.items():
        if value < 0:
            raise InvalidDatasetError(f"{name}: must be nonnegative")


def _positive(**values):
    for name, value in values.items():
        if value <= 0:
            raise InvalidDatasetError(f"{name}: must be positive")


def _contract(contract):
    # Dated exchange notation MESM6/MESM26 or vendor notation MES2026M.
    if not re.fullmatch(r"MES(?:[HMUZ]\d{1,4}|\d{4}[HMUZ])", contract):
        raise InvalidDatasetError("contract: an explicit dated MES contract is required")


@dataclass(frozen=True, kw_only=True)
class MarketEvent(Validated):
    source_id: str
    sequence: int
    event_at: datetime
    available_at: datetime

    def __post_init__(self):
        super().__post_init__()
        _nonnegative(sequence=self.sequence)
        if self.available_at < self.event_at:
            raise InvalidDatasetError("available_at precedes event completion")


@dataclass(frozen=True, kw_only=True)
class MesBar(MarketEvent):
    contract: str
    start_at: datetime
    open_ticks: int
    high_ticks: int
    low_ticks: int
    close_ticks: int
    volume: int

    def __post_init__(self):
        super().__post_init__()
        _contract(self.contract)
        _positive(low_ticks=self.low_ticks)
        _nonnegative(volume=self.volume)
        if (self.start_at >= self.event_at or
                not self.low_ticks <= min(self.open_ticks, self.close_ticks) <=
                max(self.open_ticks, self.close_ticks) <= self.high_ticks):
            raise InvalidDatasetError("bar: invalid duration or OHLC ordering")


@dataclass(frozen=True, kw_only=True)
class Quote(MarketEvent):
    contract: str
    bid_ticks: int
    ask_ticks: int
    bid_size: int | None = None
    ask_size: int | None = None

    def __post_init__(self):
        super().__post_init__()
        _contract(self.contract)
        _positive(bid_ticks=self.bid_ticks, ask_ticks=self.ask_ticks)
        if self.bid_ticks > self.ask_ticks:
            raise InvalidDatasetError("quote: crossed market")
        for size in (self.bid_size, self.ask_size):
            if size is not None:
                _nonnegative(size=size)


@dataclass(frozen=True, kw_only=True)
class Trade(MarketEvent):
    contract: str
    price_ticks: int
    size: int
    aggressor: Literal["buy", "sell", "unknown"]

    def __post_init__(self):
        super().__post_init__()
        _contract(self.contract)
        _positive(price_ticks=self.price_ticks, size=self.size)


@dataclass(frozen=True, kw_only=True)
class PricePoint(MarketEvent):
    symbol: str
    value: Decimal
    price_kind: Literal["mid", "index", "indicative"]

    def __post_init__(self):
        super().__post_init__()
        _positive(value=self.value)


@dataclass(frozen=True, kw_only=True)
class Wall(Validated):
    wall_id: str
    kind: Literal["support", "resistance"]
    strike: Decimal
    concentration: Decimal

    def __post_init__(self):
        super().__post_init__()
        _positive(strike=self.strike)
        _nonnegative(concentration=self.concentration)


@dataclass(frozen=True, kw_only=True)
class NegativeRegion(Validated):
    lower: Decimal | None
    upper: Decimal | None
    region_id: str

    def __post_init__(self):
        super().__post_init__()
        for bound in (self.lower, self.upper):
            if bound is not None:
                _positive(bound=bound)
        if self.lower is not None and self.upper is not None and self.lower >= self.upper:
            raise InvalidDatasetError("region: lower must precede upper")


@dataclass(frozen=True, kw_only=True)
class GexSnapshot(MarketEvent):
    underlying: str
    spot: Decimal
    total_gex: Decimal
    walls: tuple[Wall, ...]
    negative_regions: tuple[NegativeRegion, ...]
    profile_available: bool
    model_id: str
    expiry_days: tuple[int, ...]
    oi_published_at: datetime
    provenance: Literal["vendor", "modeled", "indicative"]

    def __post_init__(self):
        super().__post_init__()
        _positive(spot=self.spot)
        if self.oi_published_at > self.available_at:
            raise InvalidDatasetError("OI was not published at snapshot availability")
        if (not self.expiry_days or any(d < 0 for d in self.expiry_days) or
                len(set(self.expiry_days)) != len(self.expiry_days)):
            raise InvalidDatasetError("expiry_days: nonnegative distinct universe required")
        if not self.profile_available and self.negative_regions:
            raise InvalidDatasetError("unavailable profile cannot contain regions")
        if len({w.wall_id for w in self.walls}) != len(self.walls):
            raise InvalidDatasetError("duplicate wall IDs")
        if len({r.region_id for r in self.negative_regions}) != len(self.negative_regions):
            raise InvalidDatasetError("duplicate region IDs")


@dataclass(frozen=True, kw_only=True)
class OptionRow(Validated):
    kind: Literal["call", "put"]
    strike: Decimal
    expires_at: datetime
    multiplier: int
    oi: int
    oi_published_at: datetime
    gamma: float | None
    iv: float | None

    def __post_init__(self):
        super().__post_init__()
        _positive(strike=self.strike, multiplier=self.multiplier)
        _nonnegative(oi=self.oi)
        for value in (self.gamma, self.iv):
            if value is not None:
                _nonnegative(greek=value)


@dataclass(frozen=True, kw_only=True)
class OptionSnapshot(MarketEvent):
    underlying: str
    spot: Decimal
    rows: tuple[OptionRow, ...]
    annual_rate: float | None
    dividend_yield: float | None
    rate_provenance: str
    dividend_provenance: str

    def __post_init__(self):
        super().__post_init__()
        _positive(spot=self.spot)
        for row in self.rows:
            if row.oi_published_at > self.available_at:
                raise InvalidDatasetError("OI was not published at snapshot availability")
            if row.expires_at < self.event_at:
                raise InvalidDatasetError("expired option in snapshot")


Event = MesBar | Quote | Trade | PricePoint | GexSnapshot | OptionSnapshot


@dataclass(frozen=True, kw_only=True)
class Session(Validated):
    session_date: date
    open_at: datetime
    close_at: datetime
    schedule_available_at: datetime

    def __post_init__(self):
        super().__post_init__()
        if self.close_at <= self.open_at or self.schedule_available_at > self.open_at:
            raise InvalidDatasetError("session: invalid or unavailable schedule")
        eastern = ZoneInfo("America/New_York")
        if (self.open_at.astimezone(eastern).date() != self.session_date or
                self.close_at.astimezone(eastern).date() != self.session_date):
            raise InvalidDatasetError("session: calendar date must match New York open and close")


@dataclass(frozen=True, kw_only=True)
class MacroRelease(Validated):
    event_id: str
    category: MACRO_CATEGORIES
    scheduled_at: datetime
    schedule_available_at: datetime

    def __post_init__(self):
        super().__post_init__()
        if self.schedule_available_at > self.scheduled_at:
            raise InvalidDatasetError("macro schedule unavailable before release")


@dataclass(frozen=True, kw_only=True)
class MarginPoint(Validated):
    contract: str
    available_at: datetime
    initial_cents: int
    maintenance_cents: int
    provenance: Literal["historical", "scenario"]

    def __post_init__(self):
        super().__post_init__()
        _contract(self.contract)
        _positive(initial_cents=self.initial_cents, maintenance_cents=self.maintenance_cents)
        if self.maintenance_cents > self.initial_cents:
            raise InvalidDatasetError("maintenance exceeds initial margin")


@dataclass(frozen=True, kw_only=True)
class CoverageReport:
    supported_rules: frozenset[str] = frozenset()
    missing_rules: Mapping[str, str] = field(default_factory=dict)
    intersection_dates: tuple[date, ...] = ()
    source_freshness: Mapping[str, Mapping] = field(default_factory=dict)
    source_gaps: Mapping[str, tuple[tuple[datetime, datetime], ...]] = field(default_factory=dict)
    real: bool = False
    synthetic: bool = False
    indicative: bool = False
    unknown: bool = True
    margin_provenance: frozenset[str] = frozenset()
    classification: Literal["full", "incomplete", "synthetic", "indicative"] = "incomplete"
    limitations: tuple[str, ...] = ()

    def __post_init__(self):
        for name in ("missing_rules", "source_freshness", "source_gaps"):
            object.__setattr__(self, name, freeze(getattr(self, name)))


@dataclass(frozen=True, kw_only=True)
class Dataset:
    events: tuple[Event, ...] = ()
    sessions: tuple[Session, ...] = ()
    macro_releases: tuple[MacroRelease, ...] = ()
    margin_points: tuple[MarginPoint, ...] = ()
    source_metadata: Mapping[str, Mapping] = field(default_factory=dict)
    coverage: CoverageReport = field(default_factory=CoverageReport)
    limitations: tuple[str, ...] = ()
    input_hashes: Mapping[str, str] = field(default_factory=dict)
    macro_coverage_dates: tuple[date, ...] = ()
    macro_schedule_available_at: datetime | None = None

    def __post_init__(self):
        for name in ("events", "sessions", "macro_releases", "margin_points", "limitations", "macro_coverage_dates"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(self, "source_metadata", freeze(self.source_metadata))
        object.__setattr__(self, "input_hashes", freeze(self.input_hashes))


@dataclass(frozen=True, kw_only=True)
class RunConfig(Validated):
    initial_equity_cents: int = 1_000_000
    risk_fraction: Decimal = Decimal("0.01")
    daily_loss_fraction: Decimal = Decimal("0.03")
    required_rules: frozenset[str] = RULE_IDS
    timezone: str = "America/New_York"
    opening_range_start: time = time(9, 30)
    opening_range_end: time = time(9, 45)
    entry_start: time = time(9, 45)
    entry_end: time = time(10, 30)
    flatten_minutes: int = 5
    max_positions: int = 1
    vix_periods: int = 10
    vix_ceiling: Decimal = Decimal("22")
    level_tolerance_ticks: int = 4
    wall_snapshot_count: int = 3
    wall_min_span_seconds: int = 300
    wall_max_span_seconds: int = 900
    wall_max_gap_seconds: int = 300
    pivot_left_bars: int = 2
    pivot_right_bars: int = 2
    pivot_max_separation_seconds: int = 900
    target_r: Decimal = Decimal("2")
    slippage_ticks: int = 1
    round_trip_fee_cents: int = 300
    cost_sensitivity_cents: tuple[int, ...] = (200, 300, 500)
    slippage_sensitivity_ticks: tuple[int, ...] = (1, 2, 4)
    gex_max_age_seconds: int = 300
    vix_max_age_seconds: int = 90
    mapping_max_age_seconds: int = 60
    mapping_window_seconds: int = 300
    mapping_min_pairs: int = 5
    macro_window_minutes: int = 30
    development_fraction: Decimal = Decimal("0.70")

    def __post_init__(self):
        super().__post_init__()
        if not 0 < self.risk_fraction <= Decimal("0.01"):
            raise InvalidDatasetError("risk_fraction must be in (0, 0.01]")
        if not 0 < self.daily_loss_fraction <= Decimal("0.03"):
            raise InvalidDatasetError("daily_loss_fraction must be in (0, 0.03]")
        if self.required_rules != RULE_IDS:
            raise InvalidDatasetError("required filters cannot be removed implicitly; use an explicit strategy variant")
        if self.max_positions != 1 or self.timezone != "America/New_York":
            raise InvalidDatasetError("one position and New York session rules required")
        for f in fields(self):
            value = getattr(self, f.name)
            if type(value) is int:
                if f.name in ("slippage_ticks", "round_trip_fee_cents"):
                    _nonnegative(**{f.name: value})
                else:
                    _positive(**{f.name: value})
        for value in self.cost_sensitivity_cents + self.slippage_sensitivity_ticks:
            _nonnegative(sensitivity=value)
        if not self.opening_range_start < self.opening_range_end <= self.entry_start < self.entry_end:
            raise InvalidDatasetError("invalid opening range/entry window")
        if not 0 < self.development_fraction < 1:
            raise InvalidDatasetError("development_fraction must be in (0, 1)")
        _positive(target_r=self.target_r, vix_ceiling=self.vix_ceiling)
        if self.wall_min_span_seconds > self.wall_max_span_seconds:
            raise InvalidDatasetError("invalid wall observation window")
