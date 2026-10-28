"""All prices are synthetic integer MES ticks; USD 1.25 per tick."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from gamma_bot.data.models import MesBar, Quote, RunConfig
from gamma_bot.replay.account import Account
from gamma_bot.strategies import Signal

AT = datetime(2026, 6, 15, 13, 46, tzinfo=timezone.utc)


def signal(direction="long"):
    return Signal(strategy="baseline", contract="MESM6", direction=direction,
                  decision_at=AT, reference_ticks=24000,
                  stop_ticks=23996 if direction == "long" else 24004,
                  trigger_id="synthetic", variant_name="opening_range_baseline")


def quote(**kw):
    return Quote(**(dict(source_id="synthetic", sequence=2, event_at=AT + timedelta(seconds=1),
                        available_at=AT + timedelta(seconds=1), contract="MESM6",
                        bid_ticks=24000, ask_ticks=24001) | kw))


def bar(**kw):
    return MesBar(**(dict(source_id="synthetic", sequence=3, start_at=AT,
                         event_at=AT + timedelta(minutes=1), available_at=AT + timedelta(minutes=1),
                         contract="MESM6", open_ticks=24000, high_ticks=24010,
                         low_ticks=23990, close_ticks=24000, volume=10) | kw))


def test_quote_entry_uses_next_event_ask_bid_and_embedded_adverse_slippage():
    from gamma_bot.replay.fills import entry_fill
    account = Account(1_000_000)
    account.reserve("entry", 2, 100_000)
    for direction, expected in (("long", 24002), ("short", 23999)):
        fill = entry_fill(signal(direction), quote(ask_size=1, bid_size=1), account,
                          RunConfig(round_trip_fee_cents=301), order_id="entry", quantity=2)
        assert (fill.price_ticks, fill.quantity, fill.fee_cents) == (expected, 1, 150)
        assert fill.at > signal().decision_at
    assert entry_fill(signal(), quote(event_at=AT, available_at=AT), account,
                      RunConfig(), order_id="entry", quantity=1) is None
    assert entry_fill(signal(), quote(ask_size=0), account, RunConfig(),
                      order_id="entry", quantity=1) is None


def test_bar_next_open_cannot_use_an_open_before_the_decision():
    from gamma_bot.replay.fills import entry_fill
    account = Account(1_000_000)
    account.reserve("entry", 1, 100_000)
    assert entry_fill(signal(), bar(), account, RunConfig(), order_id="entry", quantity=1).price_ticks == 24001
    delayed = replace(signal(), decision_at=AT + timedelta(seconds=1))
    assert entry_fill(delayed, bar(), account, RunConfig(), order_id="entry", quantity=1) is None
    # An old quote arriving later is not a new executable market observation.
    assert entry_fill(signal(), quote(event_at=AT - timedelta(seconds=1)), account,
                      RunConfig(), order_id="entry", quantity=1) is None


@pytest.mark.parametrize("qty", [0, 1.5, True, -1])
def test_nonwhole_or_nonpositive_order_quantity_rejected(qty):
    from gamma_bot.replay.fills import entry_fill
    with pytest.raises(ValueError):
        entry_fill(signal(), quote(), Account(1_000_000), RunConfig(), order_id="e", quantity=qty)


def test_protective_stop_wins_ambiguous_bar_target_needs_trade_through():
    from gamma_bot.replay.fills import protective_price
    assert protective_price(bar(), direction="long", stop_ticks=23996, target_ticks=24008) == (23996, "stop")
    assert protective_price(bar(low_ticks=23997, high_ticks=24008), direction="long", stop_ticks=23996, target_ticks=24008) is None
    assert protective_price(bar(low_ticks=23997, high_ticks=24009), direction="long", stop_ticks=23996, target_ticks=24008) == (24008, "target")
    assert protective_price(bar(open_ticks=24009, low_ticks=24009, high_ticks=24012, close_ticks=24010), direction="long", stop_ticks=23996, target_ticks=24008) == (24008, "target")
    assert protective_price(bar(open_ticks=23990, high_ticks=24000, low_ticks=23988, close_ticks=23995), direction="long", stop_ticks=23996, target_ticks=24008) == (23990, "stop")


def test_exit_fees_are_odd_cent_remainder_and_gap_stop_uses_worse_bid():
    from gamma_bot.replay.fills import entry_fill, exit_fill, protective_price
    account = Account(1_000_000)
    account.reserve("entry", 1, 100_000)
    account.apply_fill(entry_fill(signal(), quote(), account, RunConfig(round_trip_fee_cents=301), order_id="entry", quantity=1))
    gap = quote(bid_ticks=23990, ask_ticks=23991, sequence=3)
    assert protective_price(gap, direction="long", stop_ticks=23996, target_ticks=24014) == (23990, "stop")
    fill = exit_fill(signal(), gap, account, RunConfig(round_trip_fee_cents=301), order_id="exit", quantity=1)
    assert (fill.price_ticks, fill.fee_cents) == (23989, 151)
    account.apply_fill(fill)
    assert account.gross_realized_pnl_cents == -1625
    assert account.equity_cents == 998074
    assert account.reserved_margin_cents == 0
