"""Hand-authored synthetic mechanics only; these fixtures are not market evidence."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal


from gamma_bot.data.models import GexSnapshot, MesBar, PricePoint, Quote, Trade, Wall

BASE = datetime(2026, 6, 15, 13, 30, tzinfo=timezone.utc)


def at(minutes=0, seconds=0):
    return BASE + timedelta(minutes=minutes, seconds=seconds)


def point(minute, symbol="SPY", value="600", **changes):
    return PricePoint(**(dict(source_id="synthetic-prices", sequence=10000 + minute * 10 + (1 if symbol == "SPY" else 2 if symbol == "VIX" else 0),
        event_at=at(minute), available_at=at(minute), symbol=symbol,
        value=Decimal(value), price_kind="index" if symbol == "VIX" else "mid") | changes))


def snapshot(minute=15, **changes):
    return GexSnapshot(**(dict(source_id="synthetic-gex", sequence=10000 + minute,
        event_at=at(minute), available_at=at(minute), underlying="SPY", spot=Decimal("600"),
        total_gex=Decimal("100"), walls=(Wall(wall_id="s", kind="support",
        strike=Decimal("600"), concentration=Decimal("100")),), negative_regions=(),
        profile_available=False, model_id="test-only-import-model", expiry_days=(0, 1),
        oi_published_at=at(-100), provenance="modeled") | changes))


def bar(minute, low=23990, high=24010, close=24000, **changes):
    return MesBar(**(dict(source_id="synthetic-bars", sequence=10000 + minute,
        event_at=at(minute+1), available_at=at(minute+1), start_at=at(minute),
        contract="MESM6", open_ticks=24000, high_ticks=high, low_ticks=low,
        close_ticks=close, volume=10) | changes))


def trade(minute, aggressor="buy", size=10, price=24000, **changes):
    return Trade(**(dict(source_id="synthetic-trades", sequence=10000 + minute,
        event_at=at(minute), available_at=at(minute), contract="MESM6",
        price_ticks=price, size=size, aggressor=aggressor) | changes))


def quote(minute, **changes):
    return Quote(**(dict(source_id="synthetic-quotes", sequence=10000 + minute,
        event_at=at(minute), available_at=at(minute), contract="MESM6",
        bid_ticks=23999, ask_ticks=24001) | changes))
