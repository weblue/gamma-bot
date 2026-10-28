from dataclasses import replace
from datetime import timedelta
import json

import pytest

from gamma_bot.research.metrics import compute_metrics
from .conftest import AT, result_for, trade


def test_no_trades_does_not_invent_statistics(empty_result):
    metrics = compute_metrics(empty_result)
    assert metrics['trade_count'] == 0
    assert metrics['expectancy_cents'] is None
    assert metrics['profit_factor'] is None
    assert metrics['win_rate'] is None
    assert metrics['return_fraction'] == 0
    assert metrics['performance_status'] == 'no_trades'


def test_opportunities_not_fifo_slices_drive_pnl_statistics_and_streaks():
    # One +500 opportunity is two lots (+1000, -500), followed by -2000, -500, +1000.
    trades = (trade(1000), trade(-500), trade(-2000, trigger='two', minute=2),
              trade(-500, trigger='three', minute=4), trade(1000, trigger='four', minute=6))
    marks = ((AT, 1_000_000), (AT + timedelta(minutes=1), 1_000_500),
             (AT + timedelta(minutes=3), 997_500), (AT + timedelta(minutes=3), 998_500),
             (AT + timedelta(minutes=5), 998_000), (AT + timedelta(minutes=7), 999_000),
             (AT + timedelta(hours=1), 999_000))
    m = compute_metrics(result_for(trades, marks))
    assert (m['trade_count'], m['fill_slice_count']) == (4, 5)
    assert (m['net_pnl_cents'], m['return_fraction']) == (-1000, -0.001)
    assert (m['expectancy_cents'], m['win_rate'], m['profit_factor']) == (-250, 0.5, 0.6)
    assert (m['average_win_cents'], m['average_loss_cents']) == (750, -1250)
    assert m['max_losing_streak'] == 2
    assert m['max_drawdown_cents'] == 3000
    assert m['max_drawdown_fraction'] == pytest.approx(3000 / 1_000_500)
    assert (m['exposure_seconds'], m['observed_admissible_seconds']) == (240, 3600)
    assert m['exposure_fraction'] == pytest.approx(1/15)
    assert m['risk_overrun_count'] == 1
    assert m['max_risk_overrun_cents'] == 1000


@pytest.mark.parametrize('nets,win,loss,pf,streak', [
    ((1000, 500), 750, None, None, 0), ((-1000, -500), None, -750, 0, 2),
    ((0, 0), None, None, None, 0)])
def test_degenerate_trade_distributions_remain_finite(nets, win, loss, pf, streak):
    m = compute_metrics(result_for(tuple(trade(net, trigger=str(i), minute=i*2) for i, net in enumerate(nets))))
    assert (m['average_win_cents'], m['average_loss_cents'], m['profit_factor'], m['max_losing_streak']) == (win, loss, pf, streak)
    json.dumps(m, allow_nan=False)


def test_same_trigger_on_different_sessions_is_a_new_opportunity():
    first = trade(-100)
    second = replace(trade(200), signal_at=AT + timedelta(days=1), entry_at=AT + timedelta(days=1), exit_at=AT + timedelta(days=1, minutes=1))
    assert compute_metrics(result_for((first, second)))['trade_count'] == 2


def test_quantity_does_not_multiply_already_total_slice_risk():
    row = replace(trade(-1500, quantity=2), risk_breach=True)
    m = compute_metrics(result_for((row,)))
    assert (m['risk_overrun_count'], m['max_risk_overrun_cents'], m['risk_breach_count']) == (1, 500, 1)


@pytest.mark.parametrize('case,status', [('unsupported', 'unsupported'), ('unresolved', 'unresolved'), ('excluded', 'no_admissible_sessions')])
def test_unavailable_outcomes_do_not_claim_completed_performance(case, status):
    result = result_for()
    if case == 'unsupported':
        result = replace(result, manifest=dict(result.manifest, status='unsupported'), equity_curve=())
    elif case == 'unresolved':
        result = replace(result, unresolved_exposure={'position_quantity': 1, 'pending_quantity': 0})
    else:
        excluded = ({'at': AT, 'contract': None, 'reasons': ('roll_evidence_missing',)},)
        result = replace(result, manifest=dict(result.manifest, roll_history=excluded, excluded_sessions=excluded))
    m = compute_metrics(result)
    assert m['performance_status'] == status
    assert all(m[key] is None for key in ('return_fraction', 'net_pnl_cents', 'max_drawdown_cents', 'expectancy_cents'))


def test_warmup_is_excluded_from_exposure_denominator():
    result = result_for((trade(100),), ((AT - timedelta(days=3), 1_000_000), (AT, 1_000_000), (AT + timedelta(hours=1), 1_000_100)))
    assert compute_metrics(result)['observed_admissible_seconds'] == 3600


def test_ledger_gross_fees_and_net_must_reconcile_before_reporting_performance():
    row = replace(trade(1000), gross_pnl_cents=1400)
    result = result_for((row,))
    metrics = compute_metrics(result)
    assert metrics['performance_status'] == 'accounting_mismatch'
    assert metrics['return_fraction'] is None
