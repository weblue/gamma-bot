import pytest

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
    flow.update(quote(0, source_id="synthetic-trades", sequence=9999))
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


@pytest.mark.parametrize("first_sequence,second_sequence", [(100, 1), (1, 100)])
def test_cross_source_tick_ties_are_ambiguous_regardless_of_sequences(first_sequence, second_sequence):
    flow = OrderFlow()
    flow.update(trade(0, "sell", size=3, price=24000, source_id="a-trades", sequence=first_sequence))
    flow.update(trade(0, "unknown", size=5, price=24001, source_id="z-trades", sequence=second_sequence))
    assert flow.value_at(at(0)) is None


@pytest.mark.parametrize("price,expected", [(24000, -8), (24001, 2)])
def test_same_source_tick_sequence_is_usable_at_tied_timestamps(price, expected):
    flow = OrderFlow()
    flow.update(trade(0, "sell", size=3, price=24000, source_id="one-feed", sequence=100))
    flow.update(trade(0, "unknown", size=5, price=price, source_id="one-feed", sequence=101))
    assert flow.value_at(at(0)) == expected


def test_cross_source_tick_with_later_availability_has_causal_predecessor():
    flow = OrderFlow()
    flow.update(trade(0, "sell", size=3, price=24000, source_id="a-trades", sequence=100))
    flow.update(trade(0, "unknown", size=5, price=24001, source_id="z-trades", sequence=1,
                      available_at=at(0, 1)))
    assert flow.value_at(at(0, 1)) == 2


def test_tick_rule_cannot_choose_between_ambiguous_latest_trade_feeds():
    flow = OrderFlow()
    flow.update(trade(0, "sell", size=3, price=24000, source_id="a-trades", sequence=100))
    flow.update(trade(0, "buy", size=5, price=24002, source_id="z-trades", sequence=1))
    flow.update(trade(1, "unknown", size=2, price=24001, source_id="later-trades", sequence=1))
    assert flow.value_at(at(1)) is None


def test_quote_rule_cannot_choose_between_ambiguous_latest_quote_feeds():
    flow = OrderFlow()
    flow.update(quote(0, source_id="a-quotes", sequence=100))
    flow.update(quote(0, source_id="z-quotes", sequence=1, bid_ticks=24001, ask_ticks=24003))
    flow.update(trade(1, "unknown", price=24001))
    assert flow.value_at(at(1)) is None


def test_delayed_older_quote_cannot_displace_latest_causal_market_quote():
    flow = OrderFlow()
    flow.update(quote(0, source_id="one-feed", sequence=2, event_at=at(0, 20),
                      available_at=at(0, 20), bid_ticks=24000, ask_ticks=24002))
    flow.update(quote(0, source_id="one-feed", sequence=1, available_at=at(0, 30),
                      bid_ticks=23998, ask_ticks=24000))
    flow.update(trade(0, "unknown", size=10, price=24000, source_id="one-feed",
                      sequence=3, event_at=at(0, 40), available_at=at(0, 40)))
    assert flow.value_at(at(0, 40)) == -10


def test_delayed_older_trade_cannot_displace_latest_causal_market_tick():
    flow = OrderFlow()
    flow.update(trade(0, "buy", size=5, price=24002, source_id="one-feed", sequence=2,
                      event_at=at(0, 20), available_at=at(0, 20)))
    flow.update(trade(0, "sell", size=3, price=24000, source_id="one-feed", sequence=1,
                      available_at=at(0, 30)))
    flow.update(trade(0, "unknown", size=2, price=24001, source_id="one-feed", sequence=3,
                      event_at=at(0, 40), available_at=at(0, 40)))
    assert flow.value_at(at(0, 40)) == 0


@pytest.mark.parametrize("direction", ["long", "short"])
def test_price_pivots_are_confirmed_without_cvd_after_right_bars(direction):
    detector = PivotDetector()
    lows = [23995, 23994, 23990, 23994, 23995]
    highs = [24005, 24006, 24010, 24006, 24005]
    for minute in range(4):
        detector.update(bar(minute, low=lows[minute] if direction == "long" else 23990,
                            high=highs[minute] if direction == "short" else 24010), None)
        assert detector.price_pivots == ()
    detector.update(bar(4, low=lows[4] if direction == "long" else 23990,
                        high=highs[4] if direction == "short" else 24010,
                        available_at=at(5, 7)), None)
    pivot, = detector.price_pivots
    assert (pivot.direction, pivot.at, pivot.confirmed_at, pivot.price_ticks, pivot.cvd) == (
        direction, at(3), at(5, 7), 23990 if direction == "long" else 24010, None)
