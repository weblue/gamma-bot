"""Pure signal proposals and named experiment contracts.

Calendar/account/risk/fill/target gates belong to replay, including cross-strategy
position reconciliation. A proposal never consumes a source trigger until filled.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from gamma_bot.data.models import RunConfig
from gamma_bot.data.semantics import snapshot_identity
from gamma_bot.features.state import FeatureState, MappedGex

Direction = Literal["long", "short"]


@dataclass(frozen=True, kw_only=True)
class Signal:
    strategy: str
    contract: str
    direction: Direction
    decision_at: datetime
    reference_ticks: int
    stop_ticks: int
    trigger_id: str
    variant_name: str = ""
    omitted_rules: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class Variant:
    name: str
    enabled_filters: frozenset[str]
    omitted_rules: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, "enabled_filters", frozenset(self.enabled_filters))
        object.__setattr__(self, "omitted_rules", tuple(self.omitted_rules))
        if not self.name.strip() or len(set(self.omitted_rules)) != len(self.omitted_rules):
            raise ValueError("variant needs a name and distinct explicit omissions")


class Strategy(Protocol):
    def on_state(self, state: FeatureState) -> Signal | None: ...
    def on_entry_filled(self, signal: Signal) -> None: ...
    def reset_session(self, session_date: date) -> None: ...


def gex_identity(gex: MappedGex) -> tuple:
    return tuple(snapshot_identity(gex.snapshot).values())


def usable_gex(gex: MappedGex | None, as_of: datetime, config: RunConfig) -> bool:
    return (gex is not None and gex.available_at <= as_of
            and gex.snapshot.available_at <= as_of
            and 0 <= (as_of - gex.snapshot.event_at).total_seconds() <= config.gex_max_age_seconds)


class SignalStrategy:
    """Shared proposal bookkeeping; no positions, fills or risk decisions."""
    family: str
    full_variant: Variant
    optional_filters: frozenset[str] = frozenset()

    def __init__(self, config: RunConfig | None = None, *, variant: Variant | None = None):
        self.config = config or RunConfig()
        self.variant = variant or self.full_variant
        full = self.full_variant.enabled_filters
        omitted = full - self.variant.enabled_filters
        expected = set(self.full_variant.omitted_rules) | omitted
        if (not self.variant.enabled_filters <= full
                or not omitted <= self.optional_filters
                or set(self.variant.omitted_rules) != expected
                or (self.variant.name in {"reversal", "breakout", "baseline", "opening_range_baseline"}
                    and self.variant != self.full_variant)):
            raise ValueError("variant must name every supported omission and use a distinct reduced name")
        self.required_rules = self.variant.enabled_filters
        self._zone = ZoneInfo(self.config.timezone)
        self._session: date | None = None
        self._consumed: set[str] = set()
        self._proposals: dict[Signal, datetime] = {}
        self._last_filled_bar: datetime | None = None

    def reset_session(self, session_date: date) -> None:
        if session_date != self._session:
            self._session = session_date
            self._consumed.clear()
            self._proposals.clear()
            self._last_filled_bar = None

    def on_entry_filled(self, signal: Signal) -> None:
        if signal not in self._proposals:
            raise ValueError("fill must refer to this strategy's current-session proposal")
        self._consumed.add(signal.trigger_id)
        filled_bar = self._proposals[signal]
        self._last_filled_bar = max(self._last_filled_bar or filled_bar, filled_bar)

    def _ready(self, state: FeatureState, *, previous: bool = False) -> bool:
        session_date = state.as_of.astimezone(self._zone).date()
        self.reset_session(session_date)
        bar = state.bar
        if (bar is None or state.contract is None or bar.contract != state.contract
                or bar.available_at != state.as_of
                or bar.event_at - bar.start_at != timedelta(minutes=1)
                or bar.start_at.second != 0 or bar.start_at.microsecond != 0
                or bar.start_at.astimezone(self._zone).date() != session_date
                or self.required_rules.intersection(state.unavailable_rules)
                or ("opening_range" in self.required_rules and state.opening_range is None)
                or (self._last_filled_bar is not None and bar.event_at <= self._last_filled_bar)):
            return False
        if previous:
            prior = state.previous_bar
            if (prior is None or prior.contract != state.contract
                    or prior.event_at >= bar.event_at or prior.available_at > bar.available_at
                    or prior.start_at.astimezone(self._zone).date() != session_date):
                return False
        return True

    def _propose(self, state: FeatureState, direction: Direction, stop: int,
                 source_identity: tuple) -> Signal | None:
        bar = state.bar
        if stop <= 0 or (direction == "long" and stop >= bar.close_ticks) or (
                direction == "short" and stop <= bar.close_ticks):
            return None
        trigger = json.dumps((self._session.isoformat(), self.family, state.contract,
                              direction, source_identity), separators=(",", ":"))
        if trigger in self._consumed:
            return None
        signal = Signal(strategy=self.family, variant_name=self.variant.name,
            omitted_rules=self.variant.omitted_rules, contract=state.contract,
            direction=direction, decision_at=state.as_of, reference_ticks=bar.close_ticks,
            stop_ticks=stop, trigger_id=trigger)
        self._proposals[signal] = bar.event_at
        return signal
