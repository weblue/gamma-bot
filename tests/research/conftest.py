from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from gamma_bot.data.models import CoverageReport, RULE_IDS, RunConfig
from gamma_bot.replay.engine import EquityPoint, ReplayResult, TradeRecord
from gamma_bot.strategies.reversal import FULL_REVERSAL

AT = datetime(2026, 6, 15, 14, tzinfo=timezone.utc)


def trade(net, *, trigger='one', minute=0, entry_minute=None, quantity=1, **kw):
    return TradeRecord(strategy='reversal', trigger_id=trigger, variant_name='reversal',
        omitted_rules=(), contract='MESU6', signal_at=AT + timedelta(minutes=minute),
        entry_at=AT + timedelta(minutes=minute if entry_minute is None else entry_minute),
        exit_at=AT + timedelta(minutes=minute + 1), side='long', quantity=quantity,
        entry_price_ticks=24000, exit_price_ticks=24000, stop_ticks=23990,
        target_ticks=24020, planned_risk_cents=1000, gross_pnl_cents=net + 300,
        net_pnl_cents=net, fees_cents=300, exit_reason='stop' if net < 0 else 'target',
        risk_breach=False, **kw)


def result_for(trades=(), marks=None, **changes):
    if marks is None:
        marks = ((AT, 1_000_000), (AT + timedelta(hours=1), 1_000_000 + sum(t.net_pnl_cents for t in trades)))
    coverage = CoverageReport(supported_rules=RULE_IDS, intersection_dates=(date(2026, 6, 15),),
        real=True, unknown=False, margin_provenance=frozenset({'historical'}), classification='full')
    result = ReplayResult(trades=tuple(trades), equity_curve=tuple(EquityPoint(at=a, equity_cents=v) for a, v in marks),
        skips=(), unresolved_exposure={}, coverage=coverage,
        manifest={'schema_version': 1, 'status': 'complete', 'variant': FULL_REVERSAL,
            'experiment_scope': 'full', 'config': RunConfig(), 'input_hashes': {'events.jsonl': 'a'*64},
            'sources': {'provider': {'classification': 'historical', 'availability_verified': True,
                'model_id': 'dealer-model', 'expiry_days': [0, 1]}}, 'coverage': coverage,
            'limitations': ('Delivery-month contracts excluded without verified expiry metadata.',),
            'roll_history': ({'at': AT, 'contract': 'MESU6', 'reasons': ()},), 'excluded_sessions': (),
            'ending_equity_cents': marks[-1][1] if marks else 1_000_000,
            'gross_realized_pnl_cents': sum(t.gross_pnl_cents for t in trades),
            'fees_cents': sum(t.fees_cents for t in trades), 'ending_reserved_margin_cents': 0})
    return replace(result, **changes)


@pytest.fixture
def empty_result():
    return result_for()
