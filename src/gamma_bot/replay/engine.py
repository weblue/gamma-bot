"""Offline causal MES replay; no broker or network side effects.

Admission is (available_at, event_at, source_id, sequence). Source-name ties are
processing order only: entries require strictly later availability AND no older
market time. Market observations never overwrite newer event-time prices. Bars
are a labeled next-open approximation recorded at their actual availability;
entry-bar protection resolves OHLC stop first. Exact fill lots are retained.
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING
import re
from typing import Literal
from zoneinfo import ZoneInfo

from gamma_bot.data.coverage import audit_dataset
from gamma_bot.data.models import (
    CoverageReport, Dataset, InvalidDatasetError, MesBar, PricePoint, Quote, RunConfig, freeze,
)
from gamma_bot.features.state import FeatureEngine
from gamma_bot.replay.account import Account, Fill
from gamma_bot.replay.calendar import entry_gate, flatten_deadline
from gamma_bot.replay.fills import (
    LIQUIDITY_ASSUMPTIONS, entry_fill, executable_price, exit_fill, protective_price,
)
from gamma_bot.replay.risk import TICK_VALUE_CENTS, size_trade
from gamma_bot.replay.roll import RollSelector
from gamma_bot.strategies import Signal, Strategy, Variant


@dataclass(frozen=True, kw_only=True)
class TradeRecord:
    strategy: str
    trigger_id: str
    variant_name: str
    omitted_rules: tuple[str, ...]
    contract: str
    signal_at: datetime
    entry_at: datetime
    exit_at: datetime
    side: Literal['long', 'short']
    quantity: int
    entry_price_ticks: int
    exit_price_ticks: int
    stop_ticks: int
    target_ticks: int
    planned_risk_cents: int
    gross_pnl_cents: int
    net_pnl_cents: int
    fees_cents: int
    exit_reason: str
    risk_breach: bool
    exit_trigger_target_ticks: int | None = None


@dataclass(frozen=True, kw_only=True)
class EquityPoint:
    at: datetime
    equity_cents: int


@dataclass(frozen=True, kw_only=True)
class SkipRecord:
    at: datetime
    reason: str
    strategy: str | None = None
    contract: str | None = None
    trigger_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class ReplayResult:
    trades: tuple[TradeRecord, ...]
    equity_curve: tuple[EquityPoint, ...]
    skips: tuple[SkipRecord, ...]
    unresolved_exposure: Mapping
    coverage: CoverageReport
    manifest: Mapping

    def __post_init__(self):
        object.__setattr__(self, 'unresolved_exposure', freeze(self.unresolved_exposure))
        object.__setattr__(self, 'manifest', freeze(self.manifest))


@dataclass
class _Pending:
    signal: Signal
    strategy: Strategy
    order_id: str
    remaining: int
    margin_cents: int
    planned_per_contract: int


@dataclass
class _Lot:
    signal: Signal
    entry: Fill
    remaining: int
    target_ticks: int
    planned_per_contract: int
    risk_breach: bool = False


class _Replay:
    def __init__(self, dataset, config, variant, strategies, coverage, admitted_session_dates=None):
        self.admitted_session_dates = admitted_session_dates
        self.data, self.config, self.variant = dataset, config, variant
        self.strategies, self.coverage = strategies, coverage
        self.account = Account(config.initial_equity_cents, daily_loss_fraction=config.daily_loss_fraction)
        self.features = FeatureEngine(config)
        self.roll = RollSelector()
        self.session = None
        self.active = None
        self.pending = None
        self.lots = []
        self.trades, self.equity, self.skips = [], [], []
        self.forced = None
        self.last_exit_at = None
        self.last_market_at = {}
        self.roll_history = []
        self.margin_used = []
        self.count = 0

    def admitted(self, now):
        return self.admitted_session_dates is None or (
            self.session is not None and self.session.session_date in self.admitted_session_dates
            and now.astimezone(ZoneInfo('America/New_York')).date() == self.session.session_date)

    def skip(self, now, reason, signal=None):
        if not self.admitted(now):
            return
        self.skips.append(SkipRecord(at=now, reason=reason,
            strategy=signal.strategy if signal else None,
            contract=signal.contract if signal else self.active,
            trigger_id=signal.trigger_id if signal else None))

    def cancel(self, now, reason):
        if self.pending:
            self.skip(now, reason, self.pending.signal)
            self.account.release(self.pending.order_id)
            self.pending = None

    def margin(self, contract, now):
        rows = [m for m in self.data.margin_points if m.contract == contract and m.available_at <= now]
        return max(rows, key=lambda m: m.available_at) if rows else None

    def releases(self, session):
        window = timedelta(minutes=self.config.macro_window_minutes + self.config.flatten_minutes)
        return tuple(r for r in self.data.macro_releases
                     if session.open_at-window <= r.scheduled_at <= session.close_at+window)

    def gate(self, now):
        session = self.session
        verified = (session.session_date in self.data.macro_coverage_dates and
                    self.data.macro_schedule_available_at is not None and
                    self.data.macro_schedule_available_at <= now and session.schedule_available_at <= now)
        return entry_gate(now, session, self.releases(session), schedules_verified=verified, config=self.config)

    def force(self, now, reason):
        self.cancel(now, reason)
        if self.lots and self.forced is None:
            self.forced = now, reason
            self.skip(now, reason, self.lots[0].signal)
            if reason in ('maintenance_deficit', 'risk_breach'):
                for lot in self.lots:
                    lot.risk_breach = True

    def clock(self, now):
        sessions = [s for s in self.data.sessions if s.open_at <= now and s.schedule_available_at <= now]
        current = max(sessions, key=lambda s: s.open_at) if sessions else None
        if current is not None and current != self.session:
            self.cancel(now, 'session_changed')
            if self.lots:
                self.force(now, 'session_flatten')
            else:
                self.account.start_session(current)
                self.session = current
                for strategy in self.strategies:
                    strategy.reset_session(current.session_date)
                ordered = sorted(self.data.sessions, key=lambda s: s.open_at)
                index = ordered.index(current)
                previous = ordered[index-1] if index else None
                old = self.active
                self.active, reasons = self.roll.select(current, previous, now)
                if self.admitted(now):
                    self.roll_history.append({'at': now, 'contract': self.active, 'reasons': reasons})
                for reason in reasons:
                    self.skip(now, reason)
                if old != self.active:
                    self.features = FeatureEngine(self.config)
        if self.session is None or not self.admitted(now):
            return
        deadline = flatten_deadline(self.session, self.releases(self.session), now, config=self.config)
        if now >= deadline:
            regular = self.session.close_at-timedelta(minutes=self.config.flatten_minutes)
            self.force(now, 'macro_flatten' if deadline < regular else 'session_flatten')
        elif self.pending:
            allowed, reasons = self.gate(now)
            if not allowed:
                self.cancel(now, reasons[0])
        if self.account.daily_shutdown:
            self.force(now, 'daily_loss')
        if self.lots:
            margin = self.margin(self.account.position_contract, now)
            if margin and self.account.equity_cents < abs(self.account.position_quantity)*margin.maintenance_cents:
                self.force(now, 'maintenance_deficit')

    def mark(self, event):
        if self.account.position_contract == event.contract:
            price = ((event.bid_ticks if self.account.position_quantity > 0 else event.ask_ticks)
                     if isinstance(event, Quote) else event.close_ticks)
            self.account.mark(price)
            self.equity.append(EquityPoint(at=event.available_at, equity_cents=self.account.equity_cents))

    def close(self, event, *, entry_bar=False):
        if not self.lots:
            return
        # A quote's known size is total capacity across lots, not per lot.
        capacity = (event.bid_size if self.account.position_quantity > 0 else event.ask_size) if isinstance(event, Quote) else None
        remaining_capacity = abs(self.account.position_quantity) if capacity is None else capacity
        for lot in tuple(self.lots):
            if remaining_capacity <= 0:
                break
            if lot.remaining == 0:
                continue
            if lot.signal.contract != event.contract:
                continue
            if not entry_bar and (event.available_at <= lot.entry.at or event.event_at < lot.entry.at or
                                  isinstance(event, MesBar) and event.start_at < lot.entry.at):
                continue
            price_reason = protective_price(event, direction=lot.signal.direction,
                stop_ticks=lot.signal.stop_ticks, target_ticks=lot.target_ticks)
            if self.forced:
                at, reason = self.forced
                # Only the new liquidation order needs to wait. Protection that
                # was active during this bar remains valid independently.
                eligible = not (
                    reason in ('maintenance_deficit', 'risk_breach') and event.available_at <= at
                    or event.event_at < at or isinstance(event, MesBar) and event.start_at < at)
                if eligible:
                    price_reason = executable_price(event, 'sell' if lot.signal.direction == 'long' else 'buy'), reason
            if price_reason is None:
                continue
            price, reason = price_reason
            self.cancel(event.available_at, 'exit_cancelled_pending')
            if reason == 'stop' and self.forced is None:
                self.forced = event.available_at, 'stop'
            fill = exit_fill(lot.signal, event, self.account, self.config, order_id='exit-'+lot.entry.order_id,
                             quantity=min(lot.remaining, remaining_capacity), price_ticks=price)
            if fill is None:
                continue
            self.account.apply_fill(fill)
            self.record_exit(fill, reason, lot.target_ticks if reason == 'target' else None)
            remaining_capacity -= fill.quantity
            self.last_exit_at = event.available_at
        if not self.lots:
            self.forced = None

    def record_exit(self, fill, reason, trigger_target):
        """Allocate an order's execution to the same economic FIFO lots as Account.

        The originating target order need not belong to the oldest economic lot.
        Its quantity is used once by close(); remaining entries retain their own
        protection for subsequent eligible observations.
        """
        remaining = fill.quantity
        while remaining:
            lot = self.lots[0]
            qty = min(remaining, lot.remaining)
            gross = ((fill.price_ticks-lot.entry.price_ticks)*qty*TICK_VALUE_CENTS *
                     (1 if lot.signal.direction == 'long' else -1))
            fees = (self.config.round_trip_fee_cents//2)*qty+fill.fee_cents*qty//fill.quantity
            self.trades.append(TradeRecord(strategy=lot.signal.strategy, trigger_id=lot.signal.trigger_id,
                variant_name=lot.signal.variant_name, omitted_rules=lot.signal.omitted_rules,
                contract=lot.signal.contract, signal_at=lot.signal.decision_at, entry_at=lot.entry.at,
                exit_at=fill.at, side=lot.signal.direction, quantity=qty,
                entry_price_ticks=lot.entry.price_ticks, exit_price_ticks=fill.price_ticks,
                stop_ticks=lot.signal.stop_ticks, target_ticks=lot.target_ticks,
                planned_risk_cents=lot.planned_per_contract*qty, gross_pnl_cents=gross,
                net_pnl_cents=gross-fees, fees_cents=fees, exit_reason=reason,
                risk_breach=lot.risk_breach or gross*-1+fees > lot.planned_per_contract*qty,
                exit_trigger_target_ticks=trigger_target))
            lot.remaining -= qty
            remaining -= qty
            if lot.remaining == 0:
                self.lots.pop(0)

    def open(self, event):
        pending = self.pending
        if pending is None or self.forced:
            return False
        signal = pending.signal
        if (event.contract != signal.contract or event.available_at <= signal.decision_at or
            event.event_at < signal.decision_at or isinstance(event, MesBar) and event.start_at < signal.decision_at):
            return False
        allowed, reasons = self.gate(event.available_at)
        if not allowed or self.account.daily_shutdown or self.account.entries_blocked:
            self.cancel(event.available_at, reasons[0] if reasons else 'account_blocked')
            return False
        margin = self.margin(signal.contract, event.available_at)
        if margin is None or margin.initial_cents != pending.margin_cents:
            self.cancel(event.available_at, 'margin_changed')
            return False
        side = 'buy' if signal.direction == 'long' else 'sell'
        distance = (executable_price(event, side)-signal.stop_ticks)*(1 if side == 'buy' else -1)
        if distance <= 0:
            self.cancel(event.available_at, 'invalid_stop_at_execution')
            return False
        existing_risk = sum((abs(l.entry.price_ticks-l.signal.stop_ticks)+self.config.slippage_ticks)*
                            TICK_VALUE_CENTS*l.remaining + self.config.round_trip_fee_cents*l.remaining for l in self.lots)
        budget = int(self.account.equity_cents*self.config.risk_fraction)-existing_risk
        cost = (distance+2*self.config.slippage_ticks)*TICK_VALUE_CENTS+self.config.round_trip_fee_cents
        quantity = min(pending.remaining, max(0, budget//cost))
        if quantity == 0:
            self.cancel(event.available_at, 'zero_size_at_execution')
            return False
        fill = entry_fill(signal, event, self.account, self.config, order_id=pending.order_id, quantity=quantity)
        if fill is None:
            return False
        first = not self.lots
        self.account.apply_fill(fill)
        pending.remaining -= fill.quantity
        actual_distance = abs(fill.price_ticks-signal.stop_ticks)
        target_distance = int((actual_distance*self.config.target_r).to_integral_value(rounding=ROUND_CEILING))
        target = fill.price_ticks+target_distance*(1 if signal.direction == 'long' else -1)
        self.lots.append(_Lot(signal, fill, fill.quantity, target, cost))
        self.margin_used.append(margin)
        if first:
            pending.strategy.on_entry_filled(signal)
        if pending.remaining == 0:
            self.pending = None
        elif fill.quantity == quantity and quantity < pending.remaining+fill.quantity:
            self.cancel(event.available_at, 'execution_resize_cancelled_remainder')
        if isinstance(event, Quote):
            self.mark(event)
        else:
            # A bar entry is at its open: do not mark its still-future close
            # before evaluating protection that became active at that open.
            self.account.mark(event.open_ticks)
            self.equity.append(EquityPoint(at=event.available_at, equity_cents=self.account.equity_cents))
        risk = sum(((abs(l.entry.price_ticks-l.signal.stop_ticks)+self.config.slippage_ticks)*TICK_VALUE_CENTS +
                    self.config.round_trip_fee_cents)*l.remaining for l in self.lots)
        if self.pending and risk + self.pending.remaining*self.pending.planned_per_contract > int(
                self.account.equity_cents*self.config.risk_fraction):
            self.cancel(event.available_at, 'pending_risk_budget_exceeded')
        if risk > int(self.account.equity_cents*self.config.risk_fraction):
            self.force(event.available_at, 'risk_breach')
        return True

    def propose(self, state, event):
        if self.last_exit_at is not None and (event.available_at <= self.last_exit_at or event.start_at < self.last_exit_at):
            return
        if self.pending or self.lots or not self.active or self.session is None:
            return
        for strategy in self.strategies:
            signal = strategy.on_state(state)
            if signal is None:
                continue
            if signal.contract != self.active or signal.decision_at != event.available_at:
                raise InvalidDatasetError('strategy proposal does not match current contract/decision')
            allowed, reasons = self.gate(event.available_at)
            if not allowed:
                self.skip(event.available_at, reasons[0], signal)
                continue
            if self.account.daily_shutdown or self.account.entries_blocked:
                self.skip(event.available_at, 'account_blocked', signal)
                continue
            margin = self.margin(signal.contract, event.available_at)
            if margin is None:
                self.skip(event.available_at, 'margin_unavailable', signal)
                continue
            distance = (signal.reference_ticks-signal.stop_ticks)*(1 if signal.direction == 'long' else -1)
            if distance <= 0:
                self.skip(event.available_at, 'invalid_structural_stop', signal)
                continue
            quantity = size_trade(equity_cents=self.account.equity_cents, free_margin_cents=self.account.free_margin_cents,
                initial_margin_cents=margin.initial_cents, stop_ticks=distance,
                round_trip_fee_cents=self.config.round_trip_fee_cents,
                slippage_ticks_per_fill=self.config.slippage_ticks, risk_fraction=self.config.risk_fraction)
            if quantity == 0:
                self.skip(event.available_at, 'zero_size', signal)
                continue
            self.count += 1
            order = 'entry-'+str(self.count)
            self.account.reserve(order, quantity, margin.initial_cents)
            planned = (distance+2*self.config.slippage_ticks)*TICK_VALUE_CENTS+self.config.round_trip_fee_cents
            self.pending = _Pending(signal, strategy, order, quantity, margin.initial_cents, planned)
            break

    def run(self):
        events = sorted(self.data.events, key=lambda e: (e.available_at, e.event_at, e.source_id, e.sequence))
        if not events:
            return self.result('complete')
        first, last = events[0].available_at, events[-1].available_at
        timers = set()
        for session in self.data.sessions:
            timers.update((session.open_at, session.close_at, session.close_at-timedelta(minutes=self.config.flatten_minutes)))
        for release in self.data.macro_releases:
            timers.update((release.schedule_available_at,
                max(release.schedule_available_at, release.scheduled_at-timedelta(minutes=self.config.macro_window_minutes+self.config.flatten_minutes))))
        timers.update(m.available_at for m in self.data.margin_points)
        timeline = [(at, None) for at in timers if first <= at <= last]
        timeline.extend((e.available_at, e) for e in events)
        timeline.sort(key=lambda row: (row[0], 0 if row[1] is None else 1,
            row[1].event_at if row[1] else row[0], row[1].source_id if row[1] else '', row[1].sequence if row[1] else 0))
        if self.admitted_session_dates is None:
            self.equity.append(EquityPoint(at=first, equity_cents=self.account.equity_cents))
        last_admitted = max(self.admitted_session_dates) if self.admitted_session_dates else None
        for now, event in timeline:
            if last_admitted is not None and now.astimezone(ZoneInfo('America/New_York')).date() > last_admitted:
                break
            self.clock(now)
            if self.admitted_session_dates is not None and self.admitted(now) and not self.equity:
                self.equity.append(EquityPoint(at=now, equity_cents=self.account.equity_cents))
            if event is None:
                continue
            if isinstance(event, MesBar):
                for session in self.data.sessions:
                    if session.open_at <= event.start_at < session.close_at:
                        self.roll.observe(event, session)
                        break
            if (not self.admitted(now) or self.admitted_session_dates is not None
                    and event.event_at.astimezone(ZoneInfo('America/New_York')).date() not in self.admitted_session_dates):
                continue
            contract = getattr(event, 'contract', None)
            if isinstance(event, PricePoint) and re.fullmatch(r'MES(?:[HMUZ]\d{1,4}|\d{4}[HMUZ])', event.symbol):
                contract = event.symbol
            managed = contract == self.account.position_contract and bool(self.lots)
            if contract is not None and contract != self.active and not managed:
                continue
            market = isinstance(event, (Quote, MesBar))
            fresh = market and event.event_at >= self.last_market_at.get(contract, event.event_at)
            if fresh:
                self.last_market_at[contract] = event.event_at
                if isinstance(event, MesBar):
                    # Existing protection was active inside the completed bar,
                    # before a closing mark could create a shutdown request.
                    self.close(event)
                self.mark(event)
                self.clock(now)  # marked equity precedes shutdown/maintenance checks
                if isinstance(event, Quote):
                    self.close(event)
            state = self.features.update(event) if contract is None or contract == self.active else None
            if isinstance(event, MesBar) and state is not None:
                self.propose(state, event)
            if fresh:
                opened = self.open(event)
                if opened and isinstance(event, MesBar):
                    self.close(event, entry_bar=True)
                self.mark(event)
            self.equity.append(EquityPoint(at=now, equity_cents=self.account.equity_cents))
        return self.result('complete')

    def result(self, status):
        unresolved = {}
        if self.lots or self.pending:
            unresolved = {'position_quantity': self.account.position_quantity,
                'contract': self.account.position_contract or self.pending.signal.contract,
                'pending_quantity': self.pending.remaining if self.pending else 0,
                'lots': tuple({'entry': l.entry, 'remaining': l.remaining, 'stop_ticks': l.signal.stop_ticks,
                               'target_ticks': l.target_ticks, 'risk_breach': l.risk_breach} for l in self.lots),
                'liquidation': self.forced}
        manifest = {'schema_version': 1, 'status': status, 'variant': self.variant,
            'experiment_scope': 'reduced' if self.variant.omitted_rules else 'full',
            'config': self.config, 'input_hashes': self.data.input_hashes,
            'sources': self.data.source_metadata, 'coverage': self.coverage,
            'limitations': (*self.data.limitations, *LIQUIDITY_ASSUMPTIONS,
                'Delivery-month contracts excluded without verified expiry metadata; valid pre-expiry sessions may be lost.',
                'Roll volume requires every regular-session one-minute bar for current and next quarter; missing comparison excludes session.'),
            'roll_history': tuple(self.roll_history), 'margin_used': tuple(self.margin_used),
            'excluded_sessions': tuple(row for row in self.roll_history if row['contract'] is None),
            'ending_reserved_margin_cents': self.account.reserved_margin_cents,
            'ending_equity_cents': self.account.equity_cents,
            'gross_realized_pnl_cents': self.account.gross_realized_pnl_cents,
            'fees_cents': self.account.fees_cents}
        return ReplayResult(trades=tuple(self.trades), equity_curve=tuple(self.equity), skips=tuple(self.skips),
            unresolved_exposure=unresolved, coverage=self.coverage, manifest=manifest)


def run_replay(dataset: Dataset, config: RunConfig, variant: Variant, *,
               strategies: Sequence[Strategy],
               admitted_session_dates: Sequence[date] | None = None) -> ReplayResult:
    """Run one declared experiment; unsupported requests return diagnostics only.

    Each strategy must belong to the supplied experiment. Reduced experiments
    require their explicitly declared inputs plus common macro/margin evidence.
    A supported audit establishes potential; every decision still gates runtime.
    admitted_session_dates optionally keeps other dates as roll-volume evidence
    only: no decisions, trades, equity/time or reported session counts. Coverage
    is audited on admitted dates, and future dates cannot alter their account.
    """
    if not strategies or any(s.variant != variant for s in strategies):
        raise InvalidDatasetError('provide strategy instances matching the requested variant')
    required = frozenset().union(*(s.required_rules for s in strategies), {'macro', 'margin'})
    # Keep preceding bars for causal roll selection, but do not let warm-up
    # feature availability establish the evaluated period's input capabilities.
    admitted = None if admitted_session_dates is None else frozenset(admitted_session_dates)
    audit_data = dataset
    if admitted is not None:
        if not admitted <= {s.session_date for s in dataset.sessions}:
            raise InvalidDatasetError('admitted dates must have session schedules')
        et = ZoneInfo('America/New_York')
        audit_data = replace(dataset,
            events=tuple(e for e in dataset.events if e.available_at.astimezone(et).date() in admitted
                         and e.event_at.astimezone(et).date() in admitted),
            sessions=tuple(s for s in dataset.sessions if s.session_date in admitted))
    coverage = audit_dataset(audit_data, required, config=config)
    replay = _Replay(dataset, config, variant, strategies, coverage, admitted)
    if coverage.missing_rules or not coverage.intersection_dates:
        return replay.result('unsupported')
    return replay.run()
