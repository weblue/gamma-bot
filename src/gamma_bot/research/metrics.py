"""Economic statistics on completed opportunities, with explicit evidence limits.

Money is integer cents; ratios are fractions, not percentages. The equity curve
is processed in recorded order, including repeated timestamps.
"""
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime
from zoneinfo import ZoneInfo

from gamma_bot.data.models import RULE_IDS
from gamma_bot.data.provenance import to_jsonable
from gamma_bot.replay.engine import ReplayResult, TradeRecord

ET = ZoneInfo('America/New_York')


def opportunity_key(trade: TradeRecord) -> tuple[str, str, str, str]:
    """Partial FIFO lots share the signal's New York strategy session."""
    return (trade.strategy, trade.trigger_id, trade.contract,
            trade.signal_at.astimezone(ET).date().isoformat())


def _timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value


def _admissible_days(result):
    return {_timestamp(row['at']).astimezone(ET).date()
            for row in result.manifest.get('roll_history', ()) if row.get('contract') is not None}


def _union_seconds(intervals):
    total = 0.0
    end = None
    for start, stop in sorted(intervals):
        if end is None or start > end:
            total += (stop - start).total_seconds()
        elif stop > end:
            total += (stop - end).total_seconds()
        end = max(end, stop) if end is not None else stop
    return total


def compute_metrics(result: ReplayResult) -> Mapping[str, int | float | str | None]:
    """Return scalar metrics; unavailable performance and undefined ratios are null.

    Synthetic/indicative/scenario arithmetic is explicitly labeled. Unsupported
    coverage, no admitted session, missing marks, accounting disagreement, and
    any unresolved exposure suppress completed-performance statistics entirely.
    """
    coverage = result.coverage
    config = to_jsonable(result.manifest['config'])
    initial = config['initial_equity_cents']
    days = _admissible_days(result)
    omitted = to_jsonable(result.manifest.get('variant', {})).get('omitted_rules', [])
    scenario = 'scenario' in coverage.margin_provenance
    verified = coverage.real and not (coverage.synthetic or coverage.indicative or coverage.unknown or scenario)
    groups = defaultdict(list)
    for row in result.trades:
        groups[opportunity_key(row)].append(row)
    ordered = sorted(groups.values(), key=lambda rows: (max(t.exit_at for t in rows), opportunity_key(rows[0])))
    nets = [sum(row.net_pnl_cents for row in rows) for rows in ordered]
    slice_net = sum(row.net_pnl_cents for row in result.trades)
    ending = result.manifest.get('ending_equity_cents')
    status = 'complete' if result.trades else 'no_trades'
    unavailable = None
    if result.manifest.get('status') != 'complete' or coverage.missing_rules or not coverage.intersection_dates:
        unavailable = 'unsupported'
    elif result.unresolved_exposure:
        unavailable = 'unresolved'
    elif not days:
        unavailable = 'no_admissible_sessions'
    elif not result.equity_curve:
        unavailable = 'missing_equity'
    elif (ending != result.equity_curve[-1].equity_cents or ending - initial != slice_net
          or result.manifest.get('gross_realized_pnl_cents') != sum(t.gross_pnl_cents for t in result.trades)
          or result.manifest.get('fees_cents') != sum(t.fees_cents for t in result.trades)
          or any(t.gross_pnl_cents - t.fees_cents != t.net_pnl_cents for t in result.trades)):
        unavailable = 'accounting_mismatch'
    evidence = ('synthetic_mechanics' if coverage.synthetic else 'indicative' if coverage.indicative
                else 'scenario_margin' if scenario else 'historical_research' if verified else 'unverified')
    m = {'performance_status': unavailable or (evidence if evidence != 'historical_research' else status),
         'evidence_kind': evidence, 'performance_available': int(unavailable is None),
         'historical_performance_available': int(unavailable is None and verified),
         'full_strategy_coverage': int(verified and coverage.classification == 'full'
             and RULE_IDS <= coverage.supported_rules and not omitted and not coverage.missing_rules),
         'admissible_session_count': len(days),
         'excluded_session_count': len(result.manifest.get('excluded_sessions', ())),
         'initial_equity_cents': initial, 'fill_slice_count': len(result.trades),
         'closed_slice_net_pnl_cents': slice_net,
         'ending_marked_equity_cents': ending if result.equity_curve else None}
    keys = ('trade_count', 'net_pnl_cents', 'return_fraction', 'max_drawdown_cents',
            'max_drawdown_fraction', 'expectancy_cents', 'win_rate', 'profit_factor',
            'average_win_cents', 'average_loss_cents', 'win_count', 'loss_count',
            'breakeven_count', 'max_losing_streak', 'exposure_seconds',
            'observed_admissible_seconds', 'exposure_fraction', 'risk_breach_count',
            'risk_overrun_count', 'max_risk_overrun_cents')
    m.update(dict.fromkeys(keys))
    if unavailable:
        return m
    wins, losses = [n for n in nets if n > 0], [n for n in nets if n < 0]
    streak = longest = 0
    for net in nets:
        streak = streak + 1 if net < 0 else 0
        longest = max(longest, streak)
    peak = initial
    drawdown = 0
    drawdown_fraction = 0.0
    observed = defaultdict(list)
    for point in result.equity_curve:
        peak = max(peak, point.equity_cents)
        drawdown = max(drawdown, peak - point.equity_cents)
        if peak > 0:
            drawdown_fraction = max(drawdown_fraction, (peak - point.equity_cents) / peak)
        day = point.at.astimezone(ET).date()
        if day in days:
            observed[day].append(point.at)
    denominator = sum((max(times) - min(times)).total_seconds() for times in observed.values())
    exposure = _union_seconds((t.entry_at, t.exit_at) for t in result.trades)
    overruns = [max(0, -net - sum(t.planned_risk_cents for t in rows))
                for net, rows in zip(nets, ordered)]
    m.update(trade_count=len(nets), net_pnl_cents=ending - initial,
        return_fraction=(ending - initial) / initial if initial > 0 else None,
        max_drawdown_cents=drawdown, max_drawdown_fraction=drawdown_fraction,
        expectancy_cents=sum(nets) / len(nets) if nets else None,
        win_rate=len(wins) / len(nets) if nets else None,
        profit_factor=sum(wins) / -sum(losses) if losses else None,
        average_win_cents=sum(wins) / len(wins) if wins else None,
        average_loss_cents=sum(losses) / len(losses) if losses else None,
        win_count=len(wins), loss_count=len(losses), breakeven_count=nets.count(0),
        max_losing_streak=longest, exposure_seconds=exposure,
        observed_admissible_seconds=denominator,
        exposure_fraction=exposure / denominator if denominator else None,
        risk_breach_count=sum(any(t.risk_breach for t in rows) for rows in ordered),
        risk_overrun_count=sum(v > 0 for v in overruns), max_risk_overrun_cents=max(overruns, default=0))
    return m
