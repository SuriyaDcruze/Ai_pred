"""Tests for the risk manager — the account-risk cap must be a hard guarantee."""

import pytest

from app.data.schemas import Side
from app.risk.manager import RiskManager


@pytest.fixture
def rm():
    return RiskManager(account_equity=10_000, max_account_risk=0.01, min_rr=2.0)


def test_buy_plan_geometry(rm):
    plan = rm.build_plan(Side.BUY, entry=100.0, atr=2.0)
    assert plan is not None
    assert plan.stop_loss < plan.entry_low
    assert plan.take_profit_1 > plan.entry_high
    assert plan.take_profit_2 > plan.take_profit_1
    assert plan.risk_reward >= 2.0


def test_sell_plan_geometry(rm):
    plan = rm.build_plan(Side.SELL, entry=100.0, atr=2.0)
    assert plan.stop_loss > plan.entry_high
    assert plan.take_profit_1 < plan.entry_low


def test_account_risk_cap_is_hard(rm):
    entry, atr = 100.0, 2.0
    plan = rm.build_plan(Side.BUY, entry=entry, atr=atr)
    risk_per_unit = abs(entry - plan.stop_loss)
    total_risk = plan.position_size * risk_per_unit
    # Never risk more than 1% of 10_000 = 100
    assert total_risk <= 100.0 + 1e-6


def test_wait_side_returns_none(rm):
    assert rm.build_plan(Side.WAIT, entry=100.0, atr=2.0) is None


def test_zero_atr_returns_none(rm):
    assert rm.build_plan(Side.BUY, entry=100.0, atr=0.0) is None


def test_min_rr_check(rm):
    plan = rm.build_plan(Side.BUY, entry=100.0, atr=2.0, tp1_r=1.0)
    assert plan.risk_reward < 2.0
    assert rm.meets_min_rr(plan) is False
