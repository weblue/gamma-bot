"""Declared quote/tick trade classification and causal two-sided price pivots."""
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from gamma_bot.data.models import MesBar, Quote, RunConfig, Trade


class OrderFlow:
    """Use supplied aggressor, then fresh uncrossed bid/ask, then tick rule.

    At/above ask buys, at/below bid sells. Inside/ambiguous quotes use the last
    earlier trade's price change, carrying its classified side for an equal tick.
    Unclassified volume poisons session CVD rather than becoming valid zero.
    value_at is an event-time cutoff over already received trades, so a delayed
    bar cannot consume a trade after its own completion.
    """
    classifier = "supplied-aggressor/fresh-quote/tick-rule-v1"

    def __init__(self, config: RunConfig | None = None):
        self.config = config or RunConfig()
        self.reset()

    def reset(self) -> None:
        self._quotes: list[Quote] = []
        self._trades: list[tuple[Trade, int | None]] = []

    def update(self, event: Trade | Quote) -> None:
        if isinstance(event, Quote):
            self._quotes.append(event)
            return
        sign = {"buy": 1, "sell": -1}.get(event.aggressor)
        if sign is None:
            quotes = [q for q in self._quotes if q.contract == event.contract
                      and q.event_at <= event.event_at and q.available_at <= event.available_at
                      and (event.event_at - q.event_at).total_seconds() <= self.config.mapping_max_age_seconds]
            quote = max(quotes, key=lambda q: (q.event_at, q.available_at, q.sequence)) if quotes else None
            if quote is not None and quote.bid_ticks < quote.ask_ticks:
                if event.price_ticks >= quote.ask_ticks:
                    sign = 1
                elif event.price_ticks <= quote.bid_ticks:
                    sign = -1
            if sign is None:
                previous = [(t, s) for t, s in self._trades if t.contract == event.contract
                            and (t.event_at, t.available_at, t.sequence)
                            < (event.event_at, event.available_at, event.sequence)
                            and t.available_at <= event.available_at]
                if previous:
                    last, last_sign = max(previous, key=lambda ts: (
                        ts[0].event_at, ts[0].available_at, ts[0].sequence))
                    sign = (1 if event.price_ticks > last.price_ticks else
                            -1 if event.price_ticks < last.price_ticks else last_sign)
        self._trades.append((event, sign))

    def value_at(self, as_of: datetime) -> int | None:
        trades = [(t, s) for t, s in self._trades if t.event_at <= as_of]
        if not trades or any(s is None for _, s in trades):
            return None
        return sum(t.size * s for t, s in trades)


@dataclass(frozen=True, kw_only=True)
class Divergence:
    direction: Literal["long", "short"]
    first_at: datetime
    second_at: datetime
    confirmed_at: datetime
    second_pivot_ticks: int
    cvd_first: int
    cvd_second: int


class PivotDetector:
    def __init__(self, config: RunConfig | None = None):
        self.config = config or RunConfig()
        self._width = self.config.pivot_left_bars + self.config.pivot_right_bars + 1
        self._bars = deque(maxlen=self._width)
        self._pivots: dict[str, tuple[MesBar, int | None]] = {}

    def update(self, bar: MesBar, cvd: int | None) -> tuple[Divergence, ...]:
        self._bars.append((bar, cvd))
        if len(self._bars) < self._width:
            return ()
        bars = list(self._bars)
        if (any(b.event_at - b.start_at != timedelta(minutes=1) for b, _ in bars)
                or any(a.event_at != b.start_at or a.contract != b.contract
                       or a.source_id != b.source_id for (a, _), (b, _) in zip(bars, bars[1:]))):
            return ()
        pivot, pivot_cvd = bars[self.config.pivot_left_bars]
        others = [b for i, (b, _) in enumerate(bars) if i != self.config.pivot_left_bars]
        result = []
        for direction, field, compare in (("long", "low_ticks", min), ("short", "high_ticks", max)):
            level = getattr(pivot, field)
            extreme = compare(getattr(b, field) for b in others)
            if not (level < extreme if direction == "long" else level > extreme):
                continue
            previous = self._pivots.get(direction)
            self._pivots[direction] = (pivot, pivot_cvd)
            if previous is None or pivot_cvd is None or previous[1] is None:
                continue
            first, first_cvd = previous
            if not 0 < (pivot.event_at - first.event_at).total_seconds() <= self.config.pivot_max_separation_seconds:
                continue
            divergent = ((level <= first.low_ticks - 1 and pivot_cvd > first_cvd)
                         if direction == "long" else
                         (level >= first.high_ticks + 1 and pivot_cvd < first_cvd))
            if divergent:
                result.append(Divergence(direction=direction, first_at=first.event_at,
                    second_at=pivot.event_at, confirmed_at=bar.available_at,
                    second_pivot_ticks=level, cvd_first=first_cvd, cvd_second=pivot_cvd))
        return tuple(result)
