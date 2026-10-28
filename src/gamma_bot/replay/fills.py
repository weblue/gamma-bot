"""Conservative MES executions, with price slippage already in each Fill.

Unknown quote size uses a labeled requested-quantity liquidity approximation;
known sizes cap each event. Bars use next eligible open for market orders. A
resting target requires strict trade-through, pays only the target even on a
favorable gap, then incurs adverse slippage. OHLC ambiguity resolves stop first.
These are research approximations, never proof of queue priority/executability.
"""
from gamma_bot.data.models import InvalidDatasetError, MesBar, Quote, RunConfig
from gamma_bot.replay.account import Account, Fill
from gamma_bot.strategies import Signal


LIQUIDITY_ASSUMPTIONS = (
    "Unknown quote size: requested-quantity liquidity approximation; no queue evidence.",
    "Bar-only execution: next eligible open; OHLC stops before targets; target needs strict trade-through.",
    "Target gaps: fill at target without favorable gap improvement, then adverse slippage.",
)


def executable_price(event: Quote | MesBar, side: str) -> int:
    if isinstance(event, Quote):
        return event.ask_ticks if side == "buy" else event.bid_ticks
    return event.open_ticks


def _fill(signal, event, account, config, *, order_id, quantity, opening, price_ticks):
    if type(quantity) is not int or quantity <= 0:
        raise InvalidDatasetError("fill quantity must be positive whole contracts")
    if event.contract != signal.contract:
        return None
    side = "buy" if (signal.direction == "long") == opening else "sell"
    if isinstance(event, Quote):
        size = event.ask_size if side == "buy" else event.bid_size
        if size is not None:
            quantity = min(quantity, size)
    if not opening:
        quantity = min(quantity, abs(account.position_quantity))
    if quantity == 0:
        return None
    price = executable_price(event, side) if price_ticks is None else price_ticks
    price += config.slippage_ticks * (1 if side == "buy" else -1)
    per_contract_fee = (config.round_trip_fee_cents // 2 if opening else
                        config.round_trip_fee_cents - config.round_trip_fee_cents // 2)
    return Fill(order_id=order_id, contract=signal.contract, side=side, at=event.available_at,
                quantity=quantity, price_ticks=price, fee_cents=per_contract_fee * quantity)


def entry_fill(signal: Signal, event: Quote | MesBar, account: Account, config: RunConfig,
               *, order_id: str, quantity: int) -> Fill | None:
    # No order can trade on the signal observation or a delayed pre-decision quote.
    if type(quantity) is not int or quantity <= 0:
        raise InvalidDatasetError("fill quantity must be positive whole contracts")
    if (event.available_at <= signal.decision_at or event.event_at < signal.decision_at
            or isinstance(event, MesBar) and event.start_at < signal.decision_at):
        return None
    return _fill(signal, event, account, config, order_id=order_id, quantity=quantity,
                 opening=True, price_ticks=None)


def exit_fill(signal: Signal, event: Quote | MesBar, account: Account, config: RunConfig,
              *, order_id: str, quantity: int, price_ticks: int | None = None) -> Fill | None:
    return _fill(signal, event, account, config, order_id=order_id, quantity=quantity,
                 opening=False, price_ticks=price_ticks)


def protective_price(event: Quote | MesBar, *, direction: str, stop_ticks: int,
                     target_ticks: int) -> tuple[int, str] | None:
    """Return pre-slippage executable level/reason; touch alone cannot fill target."""
    long = direction == "long"
    if isinstance(event, Quote):
        price = event.bid_ticks if long else event.ask_ticks
        if (long and price <= stop_ticks) or (not long and price >= stop_ticks):
            return price, "stop"
        if (long and price > target_ticks) or (not long and price < target_ticks):
            return target_ticks, "target"
        return None
    if long:
        if event.low_ticks <= stop_ticks:
            return min(event.open_ticks, stop_ticks), "stop"
        if event.high_ticks > target_ticks:
            return target_ticks, "target"
    else:
        if event.high_ticks >= stop_ticks:
            return max(event.open_ticks, stop_ticks), "stop"
        if event.low_ticks < target_ticks:
            return target_ticks, "target"
    return None
