"""Completed-minute opening range and VIX observations; no bar-price proxies."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from gamma_bot.data.models import InvalidDatasetError, MesBar, PricePoint, RunConfig


@dataclass(frozen=True, kw_only=True)
class OpeningRange:
    high_ticks: int
    low_ticks: int


@dataclass(frozen=True, kw_only=True)
class VixState:
    current: Decimal
    previous: Decimal | None
    sma10: Decimal | None
    available_at: datetime

    def mean_reversion_eligible(self, ceiling: Decimal = Decimal("22")) -> bool:
        return (self.sma10 is not None and self.previous is not None
                and self.current < self.sma10 and self.current < ceiling
                and self.current <= self.previous)


class OpeningRangeIndicator:
    def __init__(self, config: RunConfig | None = None):
        self.config = config or RunConfig()
        self._bars: dict[datetime, MesBar] = {}
        self._date = None

    def update(self, bar: MesBar) -> OpeningRange | None:
        local = bar.start_at.astimezone(ZoneInfo(self.config.timezone))
        if local.date() != self._date:
            self._bars.clear()
            self._date = local.date()
        start = datetime.combine(local.date(), self.config.opening_range_start,
                                 tzinfo=local.tzinfo)
        end = datetime.combine(local.date(), self.config.opening_range_end,
                               tzinfo=local.tzinfo)
        if (start <= local < end and local.second == local.microsecond == 0
                and bar.event_at - bar.start_at == timedelta(minutes=1)):
            self._bars[bar.start_at] = bar
        expected = int((end - start).total_seconds() // 60)
        if len(self._bars) != expected:
            return None
        return OpeningRange(high_ticks=max(b.high_ticks for b in self._bars.values()),
                            low_ticks=min(b.low_ticks for b in self._bars.values()))


class VixIndicator:
    def __init__(self, config: RunConfig | None = None):
        self.config = config or RunConfig()
        self._points: dict[datetime, PricePoint] = {}
        self._source: str | None = None

    @property
    def latest(self) -> PricePoint | None:
        return self._points[max(self._points)] if self._points else None

    def update(self, point: PricePoint) -> VixState:
        if point.symbol != "VIX" or point.price_kind != "index":
            raise InvalidDatasetError("VIX requires completed one-minute index observations")
        if self._source is not None and point.source_id != self._source:
            raise InvalidDatasetError("VIX source switch requires a separate feature engine")
        self._source = point.source_id
        if point.event_at.second != 0 or point.event_at.microsecond != 0:
            raise InvalidDatasetError("VIX observation must complete on a minute boundary")
        self._points[point.event_at] = point
        points = sorted(self._points.values(), key=lambda p: p.event_at)[-self.config.vix_periods:]
        self._points = {p.event_at: p for p in points}
        complete = (len(points) == self.config.vix_periods and all(
            right.event_at - left.event_at == timedelta(minutes=1)
            for left, right in zip(points, points[1:])))
        return VixState(current=points[-1].value,
                        previous=points[-2].value if len(points) > 1 else None,
                        sma10=(sum((p.value for p in points), Decimal(0)) / len(points)
                               if complete else None),
                        available_at=max(p.available_at for p in points))
