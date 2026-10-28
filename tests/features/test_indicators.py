from decimal import Decimal

from gamma_bot.features.indicators import OpeningRangeIndicator, VixIndicator
from .helpers import at, bar, point


def test_opening_range_excludes_0945_bar_and_requires_all_fifteen():
    indicator = OpeningRangeIndicator()
    for minute in range(14):
        assert indicator.update(bar(minute)) is None
    result = indicator.update(bar(14, low=23980, high=24020))
    assert (result.low_ticks, result.high_ticks) == (23980, 24020)
    result = indicator.update(bar(15, low=23000, high=25000))
    assert (result.low_ticks, result.high_ticks) == (23980, 24020)


def test_missing_or_nonminute_opening_bar_does_not_pass_warmup():
    indicator = OpeningRangeIndicator()
    for minute in range(1, 15):
        assert indicator.update(bar(minute)) is None
    assert indicator.update(bar(0, event_at=at(2), available_at=at(16))) is None


def test_vix_requires_ten_completed_distinct_contiguous_minutes():
    indicator = VixIndicator()
    for minute in range(9):
        assert indicator.update(point(minute, "VIX", "20")).sma10 is None
    result = indicator.update(point(9, "VIX", "18"))
    assert result.sma10 == Decimal("19.8")
    assert result.previous == Decimal("20")
    assert result.mean_reversion_eligible()
    assert indicator.update(point(11, "VIX", "17")).sma10 is None


def test_vix_equality_to_sma_or_ceiling_fails_strict_filter():
    equal = VixIndicator()
    for minute in range(10):
        result = equal.update(point(minute, "VIX", "20"))
    assert not result.mean_reversion_eligible()
    ceiling = VixIndicator()
    for minute in range(9):
        ceiling.update(point(minute, "VIX", "23"))
    assert not ceiling.update(point(9, "VIX", "22")).mean_reversion_eligible()


def test_late_vix_does_not_replace_newer_observation_or_mix_sources():
    indicator = VixIndicator()
    indicator.update(point(10, "VIX", "20"))
    assert indicator.update(point(9, "VIX", "30", available_at=at(11))).current == Decimal("20")
