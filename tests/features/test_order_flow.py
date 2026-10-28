from gamma_bot.features.order_flow import OrderFlow, PivotDetector
from .helpers import at, bar, quote, trade


def test_unknown_trade_poisoned_cvd_is_not_valid_zero():
    flow = OrderFlow()
    flow.update(trade(0, "unknown"))
    flow.update(trade(1, "buy"))
    assert flow.value_at(at(1)) is None
    flow.reset()
    flow.update(trade(2, "sell", 3))
    assert flow.value_at(at(2)) == -3


def test_declared_quote_then_tick_classifier_and_causal_cvd_cutoff():
    flow = OrderFlow()
    flow.update(quote(0))
    flow.update(trade(0, "unknown", size=3, price=24001))
    flow.update(trade(1, "unknown", size=2, price=24002))
    flow.update(trade(2, "sell", size=4))
    assert flow.value_at(at(1)) == 5
    assert flow.value_at(at(2)) == 1


def test_future_or_stale_quote_cannot_classify_unknown_trade():
    flow = OrderFlow()
    flow.update(quote(2))
    flow.update(trade(0, "unknown", price=24001))
    assert flow.value_at(at(2)) is None


def test_divergence_waits_for_two_completed_confirmation_bars():
    pivots = PivotDetector()
    lows = [23995, 23994, 23990, 23994, 23995, 23994, 23989, 23994, 23995]
    cvds = [0, 0, -10, 0, 0, 0, -5, 0, 0]
    for minute, (low, cvd) in enumerate(zip(lows[:-1], cvds[:-1])):
        assert pivots.update(bar(minute, low=low), cvd) == ()
    result = pivots.update(bar(8, low=lows[-1]), cvds[-1])
    assert len(result) == 1
    assert (result[0].direction, result[0].second_pivot_ticks) == ("long", 23989)
    assert result[0].confirmed_at == at(9)
    assert (result[0].cvd_first, result[0].cvd_second) == (-10, -5)


def test_unknown_cvd_or_missing_confirmation_minute_cannot_confirm_divergence():
    pivots = PivotDetector()
    for minute, low in enumerate([23995, 23994, 23990, 23994, 23995, 23994, 23989, 23994, 23995]):
        assert pivots.update(bar(minute, low=low), None) == ()


def test_missing_right_confirmation_bar_cannot_manufacture_a_pivot():
    pivots = PivotDetector()
    for minute, low, cvd in [(0, 23995, 0), (1, 23994, 0), (2, 23990, -10),
            (3, 23994, 0), (4, 23995, 0), (5, 23994, 0), (6, 23989, -5),
            (7, 23994, 0), (9, 23995, 0)]:
        assert pivots.update(bar(minute, low=low), cvd) == ()


def test_bearish_divergence_compares_successive_confirmed_highs():
    pivots = PivotDetector()
    highs = [24005, 24006, 24010, 24006, 24005, 24006, 24011, 24006, 24005]
    cvds = [0, 0, 10, 0, 0, 0, 5, 0, 0]
    for minute in range(8):
        assert pivots.update(bar(minute, high=highs[minute]), cvds[minute]) == ()
    divergence, = pivots.update(bar(8, high=highs[8]), cvds[8])
    assert (divergence.direction, divergence.second_pivot_ticks, divergence.confirmed_at) == ("short", 24011, at(9))
