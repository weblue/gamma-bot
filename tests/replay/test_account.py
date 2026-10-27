from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from gamma_bot.data.models import Session
from gamma_bot.replay.account import Account, Fill


AT = datetime(2026, 6, 15, 14, tzinfo=timezone.utc)


def fill(side="buy", price=24000, qty=1, fee=150, order="entry", contract="MESM6"):
    return Fill(order_id=order, contract=contract, side=side, at=AT,
                quantity=qty, price_ticks=price, fee_cents=fee)


def day(offset=0):
    opening = datetime(2026, 6, 15, 13, 30, tzinfo=timezone.utc) + timedelta(days=offset)
    return Session(session_date=opening.date(), open_at=opening,
                   close_at=opening + timedelta(hours=6, minutes=30),
                   schedule_available_at=opening - timedelta(days=1))


def opened(side="buy", qty=1, margin=100_000, fee=150):
    account = Account(1_000_000)
    account.start_session(day())
    account.reserve("entry", qty, margin)
    account.apply_fill(fill(side=side, qty=qty, fee=fee))
    return account


def test_margin_is_collateral_and_eight_ticks_is_seven_dollars_net():
    account = Account(1_000_000)
    account.reserve("entry", 1, 100_000)
    assert account.equity_cents == 1_000_000
    assert account.free_margin_cents == 900_000
    account.apply_fill(fill())
    assert account.mark(24008) == 1_000_850
    assert account.unrealized_pnl_cents == 1_000
    assert account.realized_pnl_cents == -150
    account.apply_fill(fill("sell", 24008, order="exit"))
    assert account.gross_realized_pnl_cents == 1_000
    assert account.realized_pnl_cents == 700
    assert account.equity_cents == account.cash_cents == 1_000_700
    assert account.unrealized_pnl_cents == 0
    assert account.reserved_margin_cents == 0
    assert account.free_margin_cents == 1_000_700


def test_short_position_uses_correct_sign():
    account = opened("sell")
    assert account.mark(23992) == 1_000_850
    account.apply_fill(fill("buy", 23992, order="exit"))
    assert account.realized_pnl_cents == 700


def test_execution_fee_is_total_not_multiplied_by_contract_count():
    account = opened(qty=2, fee=150)
    account.apply_fill(fill("sell", 24008, qty=2, order="exit", fee=150))
    assert account.gross_realized_pnl_cents == 2_000
    assert account.fees_cents == 300
    assert account.equity_cents == 1_001_700


def test_partial_fills_keep_collateral_for_unfilled_and_open_contracts():
    account = Account(1_000_000)
    account.reserve("entry", 2, 100_000)
    account.apply_fill(fill(fee=0))
    account.apply_fill(fill(price=24004, fee=0))
    assert account.reserved_margin_cents == 200_000
    account.apply_fill(fill("sell", 24008, fee=0, order="exit"))
    assert account.gross_realized_pnl_cents == 1_000
    assert account.unrealized_pnl_cents == 500
    assert account.reserved_margin_cents == 100_000
    account.apply_fill(fill("sell", 24008, fee=0, order="exit"))
    assert account.equity_cents == 1_001_500
    assert account.reserved_margin_cents == 0


def test_cancellation_releases_only_unfilled_collateral():
    account = Account(1_000_000)
    account.reserve("entry", 2, 100_000)
    account.apply_fill(fill(fee=0))
    account.release("entry")
    assert account.reserved_margin_cents == 100_000
    assert account.position_quantity == 1


def test_daily_shutdown_latches_at_three_percent_even_after_recovery():
    account = opened(fee=0)
    assert account.mark(23761) == 970_125
    assert not account.daily_shutdown
    assert account.mark(23760) == 970_000
    assert account.daily_shutdown
    account.mark(24000)
    account.start_session(day())
    assert account.daily_shutdown
    # Closing fills are always allowed, including in a macro blackout/shutdown.
    account.apply_fill(fill("sell", 24000, order="exit", fee=0))
    with pytest.raises(ValueError):
        account.reserve("blocked", 1, 100_000)
    account.start_session(day(1))
    assert not account.daily_shutdown
    assert account.session_start_equity_cents == 1_000_000
    account.reserve("next", 1, 100_000)


def test_session_baseline_freezes_current_equity_not_initial_capital():
    account = opened(fee=0)
    account.apply_fill(fill("sell", 24080, order="exit", fee=0))
    account.start_session(day(1))
    assert account.session_start_equity_cents == 1_010_000
    account.reserve("next", 1, 100_000)
    account.apply_fill(fill(order="next", fee=0))
    account.mark(23758)  # $302.50 loss, below 3% of $10,100.
    assert not account.daily_shutdown
    account.mark(23757)  # $303.75 loss exceeds $303.
    assert account.daily_shutdown
    with pytest.raises(ValueError):
        account.start_session(day())


def test_same_session_call_cannot_rebase_a_partial_decline():
    account = opened(fee=0)
    account.mark(23840)  # $200 decline.
    account.start_session(day())
    assert account.session_start_equity_cents == 1_000_000
    account.mark(23760)
    assert account.daily_shutdown


def test_gap_loss_can_exceed_planned_risk_and_capital():
    account = opened(fee=0)
    account.apply_fill(fill("sell", 15000, order="gap_exit", fee=0))
    assert account.equity_cents == -125_000
    assert account.free_margin_cents == -125_000
    assert account.daily_shutdown
    with pytest.raises(ValueError):
        account.reserve("borrow", 1, 1)


@pytest.mark.parametrize("action", ["unreserved", "unrelated", "oversized_exit"])
def test_unreconciled_fills_block_new_entries_without_inventing_balances(action):
    account = opened(fee=0) if action != "unreserved" else Account(1_000_000)
    before = account.equity_cents
    incoming = {"unreserved": fill(), "unrelated": fill(contract="MESU6"),
                "oversized_exit": fill("sell", qty=2, order="exit")}[action]
    with pytest.raises(ValueError):
        account.apply_fill(incoming)
    assert account.entries_blocked
    assert account.equity_cents == before
    if account.position_quantity:
        account.apply_fill(fill("sell", order="exit", fee=0))
    account.start_session(day(1))
    with pytest.raises(ValueError):
        account.reserve("later", 1, 100_000)


def test_account_cannot_overreserve_or_pyramid():
    account = Account(1_000_000)
    with pytest.raises(ValueError):
        account.reserve("too_large", 2, 500_001)
    account.reserve("entry", 1, 100_000)
    with pytest.raises(ValueError):
        account.reserve("second", 1, 100_000)
    account.apply_fill(fill())
    with pytest.raises(ValueError):
        account.reserve("pyramid", 1, 100_000)


def test_lower_configured_daily_limit_is_honored():
    account = Account(1_000_000, daily_loss_fraction=Decimal("0.01"))
    account.reserve("entry", 1, 100_000)
    account.apply_fill(fill(fee=0))
    account.mark(23920)
    assert account.daily_shutdown


def test_first_session_registration_cannot_reset_an_existing_daily_loss():
    account = Account(1_000_000)
    account.reserve("entry", 1, 100_000)
    account.apply_fill(fill(fee=0))
    account.mark(23760)
    account.start_session(day())
    assert account.session_start_equity_cents == 1_000_000
    assert account.daily_shutdown


@pytest.mark.parametrize("held_position", [True, False])
def test_new_session_rejects_uncleared_positions_or_orders(held_position):
    account = Account(1_000_000)
    account.start_session(day())
    account.reserve("entry", 1, 100_000)
    if held_position:
        account.apply_fill(fill(fee=0))
        account.mark(23760)
    with pytest.raises(ValueError):
        account.start_session(day(1))
    assert account.session_start_equity_cents == 1_000_000
    assert account.daily_shutdown is held_position
    if held_position:
        account.apply_fill(fill("sell", 23760, order="exit", fee=0))
    else:
        account.release("entry")
    account.start_session(day(1))
    assert account.session_start_equity_cents == (970_000 if held_position else 1_000_000)
    assert not account.daily_shutdown


def test_fees_alone_trigger_daily_shutdown():
    account = opened(fee=30_000)
    assert account.equity_cents == 970_000
    assert account.daily_shutdown


def test_reserved_late_fill_is_recorded_after_shutdown():
    account = Account(1_000_000)
    account.reserve("entry", 2, 100_000)
    account.apply_fill(fill(fee=30_000))
    assert account.daily_shutdown
    account.apply_fill(fill(fee=0))
    assert account.position_quantity == 2
    assert account.cash_cents == 970_000
    assert account.reserved_margin_cents == 200_000


@pytest.mark.parametrize("margin", [None, 0, -1, True, 1.5])
def test_missing_or_invalid_margin_cannot_reserve_capital(margin):
    account = Account(1_000_000)
    with pytest.raises(ValueError):
        account.reserve("entry", 1, margin)
    assert account.reserved_margin_cents == 0
    assert account.equity_cents == 1_000_000


@pytest.mark.parametrize("fraction", [Decimal("0"), Decimal("0.031"),
                                    Decimal("NaN"), 0.03])
def test_daily_limit_cannot_exceed_approved_cap(fraction):
    with pytest.raises(ValueError):
        Account(1_000_000, daily_loss_fraction=fraction)


@pytest.mark.parametrize("overrides", [
    {"quantity": 0}, {"quantity": 1.5}, {"quantity": True},
    {"fee_cents": -1}, {"side": "unknown"}, {"contract": "ESM6"},
    {"price_ticks": 0}, {"at": datetime(2026, 6, 15)},
])
def test_invalid_fills_are_rejected(overrides):
    values = dict(order_id="entry", contract="MESM6", side="buy", at=AT,
                  quantity=1, price_ticks=24000, fee_cents=150)
    values.update(overrides)
    with pytest.raises(ValueError):
        Fill(**values)
