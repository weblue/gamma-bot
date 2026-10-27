"""Planned MES loss sizing, independent from actual execution losses."""
from decimal import Decimal

from gamma_bot.data.models import InvalidDatasetError


TICK_VALUE_CENTS = 125


def size_trade(*, equity_cents: int, free_margin_cents: int,
               initial_margin_cents: int | None, stop_ticks: int,
               round_trip_fee_cents: int, slippage_ticks_per_fill: int,
               risk_fraction: Decimal = Decimal("0.01")) -> int:
    """Return whole contracts affordable under both loss and collateral caps.

    Fees and two adverse slippage fills are included in planned loss. A stop
    is never changed here and is not a promise of a maximum execution loss.
    None margin means no contemporaneous margin evidence: no entry.
    """
    for name, value in (("equity_cents", equity_cents),
                        ("free_margin_cents", free_margin_cents),
                        ("stop_ticks", stop_ticks),
                        ("round_trip_fee_cents", round_trip_fee_cents),
                        ("slippage_ticks_per_fill", slippage_ticks_per_fill)):
        if type(value) is not int:
            raise InvalidDatasetError(f"{name}: expected integer")
    if stop_ticks <= 0 or round_trip_fee_cents < 0 or slippage_ticks_per_fill < 0:
        raise InvalidDatasetError("invalid stop or execution costs")
    if (not isinstance(risk_fraction, Decimal) or not risk_fraction.is_finite()
            or not 0 < risk_fraction <= Decimal("0.01")):
        raise InvalidDatasetError("risk_fraction must be Decimal in (0, 0.01]")
    if initial_margin_cents is None:
        return 0
    if type(initial_margin_cents) is not int or initial_margin_cents <= 0:
        raise InvalidDatasetError("initial_margin_cents: positive integer required")
    loss_cents = ((stop_ticks + 2 * slippage_ticks_per_fill) * TICK_VALUE_CENTS
                  + round_trip_fee_cents)
    numerator, denominator = risk_fraction.as_integer_ratio()
    risk_capacity = equity_cents * numerator // (denominator * loss_cents)
    margin_capacity = free_margin_cents // initial_margin_cents
    return max(0, min(risk_capacity, margin_capacity))
