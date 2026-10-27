"""Hand-computed MES economics; rejection never edits a strategy stop."""
from decimal import Decimal

import pytest

from gamma_bot.replay.risk import size_trade


def sizing(**overrides):
    values = dict(equity_cents=1_000_000, free_margin_cents=1_000_000,
                  initial_margin_cents=100_000, stop_ticks=40,
                  round_trip_fee_cents=300, slippage_ticks_per_fill=1)
    values.update(overrides)
    return size_trade(**values)


def test_costs_and_whole_contracts_limit_size():
    # 40 * $1.25 + $3 + 2 * $1.25 = $55.50; budget is $100.
    assert sizing() == 1
    # 76 ticks is $95 + $3 + $2.50 = $100.50, above the budget.
    stop = 76
    assert sizing(stop_ticks=stop) == 0
    assert stop == 76


@pytest.mark.parametrize("overrides,want", [
    ({"free_margin_cents": 99_999}, 0),
    ({"initial_margin_cents": None}, 0),
    ({"free_margin_cents": -100}, 0),
    ({"equity_cents": 0}, 0),
    ({"equity_cents": -100}, 0),
    ({"equity_cents": 2_000_000, "free_margin_cents": 299_999}, 2),
    ({"equity_cents": 2_000_000}, 3),
    ({"risk_fraction": Decimal("0.005")}, 0),
    ({"stop_ticks": 80, "round_trip_fee_cents": 0,
      "slippage_ticks_per_fill": 0}, 1),
])
def test_risk_and_collateral_are_independent_caps(overrides, want):
    assert sizing(**overrides) == want


@pytest.mark.parametrize("overrides", [
    {"stop_ticks": 0}, {"stop_ticks": 1.5}, {"stop_ticks": True},
    {"initial_margin_cents": 0}, {"initial_margin_cents": -1},
    {"round_trip_fee_cents": -1}, {"slippage_ticks_per_fill": -1},
    {"risk_fraction": Decimal("0.0101")}, {"risk_fraction": Decimal("0")},
    {"risk_fraction": Decimal("NaN")}, {"risk_fraction": 0.01},
])
def test_invalid_risk_inputs_are_rejected(overrides):
    with pytest.raises(ValueError):
        sizing(**overrides)
