"""Single-position MES ledger. Collateral never enters profit or cash."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from gamma_bot.data.models import (
    InvalidDatasetError, Session, Validated, _contract, _nonnegative, _positive,
)
from gamma_bot.replay.risk import TICK_VALUE_CENTS


@dataclass(frozen=True, kw_only=True)
class Fill(Validated):
    """One execution; fee_cents is the total fee for this fill's quantity."""

    order_id: str
    contract: str
    side: Literal["buy", "sell"]
    at: datetime
    quantity: int
    price_ticks: int
    fee_cents: int

    def __post_init__(self):
        super().__post_init__()
        _contract(self.contract)
        _positive(quantity=self.quantity, price_ticks=self.price_ticks)
        _nonnegative(fee_cents=self.fee_cents)


@dataclass
class _Reservation:
    order_id: str
    remaining: int
    per_contract_cents: int
    contract: str | None = None
    side: Literal["buy", "sell"] | None = None


@dataclass
class _Lot:
    quantity: int
    price_ticks: int
    margin_cents: int


class Account:
    """Cash, FIFO cost basis and marked equity in integer cents.

    reserve() checks new-entry authority; apply_fill() records owned executions
    even if a previously submitted entry fills after shutdown. The execution
    controller must cancel unfilled entries on shutdown and flatten positions.
    Unknown fills fail atomically and latch entries_blocked for reconciliation.
    No synthetic borrowing, automatic liquidation or broker state is modeled.
    """

    def __init__(self, initial_equity_cents: int, *,
                 daily_loss_fraction: Decimal = Decimal("0.03")):
        if type(initial_equity_cents) is not int or initial_equity_cents <= 0:
            raise InvalidDatasetError("initial equity must be positive integer cents")
        if (not isinstance(daily_loss_fraction, Decimal) or not daily_loss_fraction.is_finite()
                or not 0 < daily_loss_fraction <= Decimal("0.03")):
            raise InvalidDatasetError("daily_loss_fraction must be Decimal in (0, 0.03]")
        self._cash_cents = initial_equity_cents
        self._gross_realized_pnl_cents = 0
        self._fees_cents = 0
        self._mark_ticks: int | None = None
        self._lots: list[_Lot] = []
        self._direction = 0
        self._contract: str | None = None
        self._reservation: _Reservation | None = None
        self._session: Session | None = None
        self._session_start_equity_cents = initial_equity_cents
        self._daily_loss_fraction = daily_loss_fraction
        self._daily_shutdown = False
        self._entries_blocked = False

    @property
    def cash_cents(self) -> int:
        return self._cash_cents

    @property
    def gross_realized_pnl_cents(self) -> int:
        return self._gross_realized_pnl_cents

    @property
    def fees_cents(self) -> int:
        return self._fees_cents

    @property
    def realized_pnl_cents(self) -> int:
        """Realized gross less all fees charged, including open-entry fees."""
        return self._gross_realized_pnl_cents - self._fees_cents

    @property
    def unrealized_pnl_cents(self) -> int:
        if self._mark_ticks is None:
            return 0
        return sum((self._mark_ticks - lot.price_ticks) * lot.quantity
                   * self._direction * TICK_VALUE_CENTS for lot in self._lots)

    @property
    def equity_cents(self) -> int:
        return self._cash_cents + self.unrealized_pnl_cents

    @property
    def reserved_margin_cents(self) -> int:
        pending = (self._reservation.remaining * self._reservation.per_contract_cents
                   if self._reservation else 0)
        return pending + sum(lot.quantity * lot.margin_cents for lot in self._lots)

    @property
    def free_margin_cents(self) -> int:
        # Negative equity stays visible; no hypothetical capital is supplied.
        return self.equity_cents - self.reserved_margin_cents

    @property
    def position_quantity(self) -> int:
        """Signed number of whole contracts; negative means short."""
        return self._direction * sum(lot.quantity for lot in self._lots)

    @property
    def position_contract(self) -> str | None:
        return self._contract

    @property
    def session_start_equity_cents(self) -> int:
        return self._session_start_equity_cents

    @property
    def daily_shutdown(self) -> bool:
        return self._daily_shutdown

    @property
    def entries_blocked(self) -> bool:
        """Unknown fill/ownership requires reconciliation, across sessions."""
        return self._entries_blocked

    def _check_shutdown(self) -> None:
        numerator, denominator = self._daily_loss_fraction.as_integer_ratio()
        decline = self._session_start_equity_cents - self.equity_cents
        if (self.equity_cents <= 0 or
                decline * denominator >= self._session_start_equity_cents * numerator):
            self._daily_shutdown = True

    def start_session(self, session: Session) -> None:
        """Freeze marked opening equity once; repeated calls cannot reset it."""
        if not isinstance(session, Session):
            raise InvalidDatasetError("Session required")
        if self._session is None:
            # The constructor already froze the first baseline. Registration
            # after a fill/mark must not erase a loss or the shutdown latch.
            self._session = session
            self._check_shutdown()
            return
        if session.session_date < self._session.session_date:
            raise InvalidDatasetError("cannot move account to an earlier session")
        if session.session_date == self._session.session_date:
            if session != self._session:
                raise InvalidDatasetError("conflicting session calendar")
            return
        if self._lots or self._reservation is not None:
            raise InvalidDatasetError("flatten positions and cancel entries before next session")
        self._session = session
        self._session_start_equity_cents = self.equity_cents
        self._daily_shutdown = False
        self._check_shutdown()

    def mark(self, price_ticks: int) -> int:
        if type(price_ticks) is not int or price_ticks <= 0:
            raise InvalidDatasetError("mark price must be positive integer ticks")
        self._mark_ticks = price_ticks
        self._check_shutdown()
        return self.equity_cents

    def reserve(self, order_id: str, quantity: int, margin_cents: int) -> None:
        """Reserve quantity * per-contract initial margin for one entry order."""
        if not isinstance(order_id, str) or not order_id.strip():
            raise InvalidDatasetError("entry order ID required")
        if (type(quantity) is not int or quantity <= 0 or
                type(margin_cents) is not int or margin_cents <= 0):
            raise InvalidDatasetError("positive whole quantity and known margin required")
        if self._daily_shutdown or self._entries_blocked:
            raise InvalidDatasetError("account entries blocked")
        if self._lots or self._reservation is not None:
            raise InvalidDatasetError("one position or pending entry at a time")
        if quantity * margin_cents > self.free_margin_cents:
            raise InvalidDatasetError("insufficient free margin")
        self._reservation = _Reservation(order_id, quantity, margin_cents)

    def release(self, order_id: str) -> None:
        """Cancel an entry's unfilled balance; filled collateral stays held."""
        if self._reservation is None or self._reservation.order_id != order_id:
            raise InvalidDatasetError("unknown pending entry order")
        self._reservation = None

    def _unowned(self, reason: str) -> None:
        self._entries_blocked = True
        raise InvalidDatasetError(reason)

    def apply_fill(self, fill: Fill) -> None:
        if not isinstance(fill, Fill):
            self._unowned("validated Fill required")
        reservation = self._reservation
        if reservation is not None and reservation.order_id == fill.order_id:
            # A pending entry keeps its ownership terms even when an exit
            # flattens its earlier partial fill. Validate before classifying
            # an opposite-side execution as an exit or changing any ledger.
            if reservation.contract is not None and (
                    fill.contract != reservation.contract or fill.side != reservation.side):
                self._unowned("pending entry contract or side mismatch requires reconciliation")
        direction = 1 if fill.side == "buy" else -1
        if self._contract is not None and fill.contract != self._contract:
            self._unowned("unrelated contract fill requires reconciliation")
        opening = not self._lots or direction == self._direction
        if opening:
            if (reservation is None or reservation.order_id != fill.order_id
                    or fill.quantity > reservation.remaining):
                self._unowned("unreserved opening fill requires reconciliation")
            reservation.contract = fill.contract
            reservation.side = fill.side
            self._lots.append(_Lot(fill.quantity, fill.price_ticks,
                                   reservation.per_contract_cents))
            reservation.remaining -= fill.quantity
            if reservation.remaining == 0:
                self._reservation = None
            self._direction = direction
            self._contract = fill.contract
        else:
            if fill.quantity > abs(self.position_quantity):
                self._unowned("exit would reverse into an unowned position")
            remaining = fill.quantity
            while remaining:
                lot = self._lots[0]
                quantity = min(remaining, lot.quantity)
                gross = ((fill.price_ticks - lot.price_ticks) * quantity
                         * self._direction * TICK_VALUE_CENTS)
                self._gross_realized_pnl_cents += gross
                self._cash_cents += gross
                lot.quantity -= quantity
                remaining -= quantity
                if lot.quantity == 0:
                    self._lots.pop(0)
            if not self._lots:
                self._direction = 0
                self._contract = None
        self._fees_cents += fill.fee_cents
        self._cash_cents -= fill.fee_cents
        self.mark(fill.price_ticks)
