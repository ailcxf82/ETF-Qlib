import pandas as pd
import pytest
from etf_ml.backtest.accounting import Account
from etf_ml.backtest.metrics import portfolio_metrics
from etf_ml.contracts import PortfolioPolicy
from etf_ml.portfolio.allocation import construct

@pytest.fixture
def policy():
    return PortfolioPolicy(k_mode="fraction", liquidity_mode="participation",
                           risk_mode="max_drawdown", minimum_commission=0)

def test_T13_T21_buy_and_sell_raw_prices_fees_cash(policy):
    account = Account(cash=500000)
    buy = account.trade("510300.SH", 100000, 1, policy, historical_average_amount=1e9)
    assert buy["price"] == pytest.approx(1.0003)
    assert buy["amount"] == pytest.approx(100030)
    assert buy["commission"] == pytest.approx(300.09)
    assert account.cash == pytest.approx(399669.91)
    sell = account.trade("510300.SH", -100000, 1, policy, historical_average_amount=1e9)
    assert sell["price"] == pytest.approx(.9997)
    assert sell["commission"] == pytest.approx(299.91)
    assert account.cash == pytest.approx(499340)
    assert not account.shares

def test_T04_dividend_split_and_event_idempotency(policy):
    account = Account(100, shares={"A": 100}, marks={"A": 10})
    account.apply_event("dividend", "A", cash_per_share=.5)
    account.apply_event("dividend", "A", cash_per_share=.5)
    assert account.cash == 150
    account.apply_event("split", "A", share_multiplier=2)
    assert account.shares["A"] == 200
    assert account.equity() == 1100
    assert account.equity({"A": 4.75}) == 1100

def test_T12_untradable_holding_remains_in_account(policy):
    account = Account(100, shares={"A": 100}, marks={"A": 10})
    result = account.trade("A", -100, 10, policy, sellable=False,
                           historical_average_amount=1e6)
    assert result["status"] == "unfilled"
    assert account.shares["A"] == 100
    assert account.equity({"A": float("nan")}) == 1100

def test_participation_and_lot_size(policy):
    account = Account(500000)
    trade = account.trade("A", 10000, 10, policy, historical_average_amount=10000)
    assert trade["filled_shares"] == 100
    assert trade["amount"] <= 10000 * .2

def test_cash_and_minimum_commission_limit(policy):
    policy.minimum_commission = 5
    account = Account(1004)
    trade = account.trade("A", 100, 10, policy, historical_average_amount=1e6)
    assert trade["filled_shares"] == 0 and account.cash == 1004

def test_top_fraction_ties_group_cap_and_cash(policy):
    scores = pd.Series(1., index=[f"ETF{i:02}" for i in range(40)])
    constraints = {"buyable": dict.fromkeys(scores.index, True),
                   "sellable": dict.fromkeys(scores.index, True),
                   "tracking_group": dict.fromkeys(scores.index, "same")}
    policy.max_group_weight = .6
    allocation = construct(scores, {}, constraints, policy)
    assert allocation.weights == {"ETF00": .5, "ETF01": pytest.approx(.1)}
    assert allocation.cash_weight == pytest.approx(.4)

def test_locked_holding_consumes_equity_and_group_capacity(policy):
    scores = pd.Series({"A": 1., "B": 2.})
    allocation = construct(scores, {"A": .4}, {
        "sellable": {"A": False}, "buyable": {"B": True},
        "tracking_group": {"A": "g", "B": "g"}}, policy)
    assert allocation.weights["A"] == .4
    assert allocation.weights["B"] == pytest.approx(.6)

def test_risk_trigger_preserves_only_locked_assets(policy):
    allocation = construct(pd.Series({"B": 1.}), {"A": .4, "C": .2}, {
        "sellable": {"A": False, "C": True}, "risk_triggered": True}, policy)
    assert allocation.weights == {"A": .4}
    assert allocation.cash_weight == pytest.approx(.6)

def test_turnover_budget_interpolates_target(policy):
    policy.max_turnover = .2
    allocation = construct(pd.Series({"B": 1.}), {"A": 1.}, {
        "sellable": {"A": True}, "buyable": {"B": True},
        "tracking_group": {"B": "g"}}, policy)
    assert allocation.weights == {"A": pytest.approx(.8), "B": pytest.approx(.2)}

def test_metrics_include_initial_equity_and_daily_accounting():
    dates = pd.bdate_range("2023-01-02", periods=3)
    returns = pd.Series([-.1, .02, .03], index=dates)
    benchmark = pd.Series([0., .01, .02], index=dates)
    metrics = portfolio_metrics(returns, benchmark)
    assert metrics["max_drawdown"] == pytest.approx(.1)
    assert metrics["net_return"] == pytest.approx(.9 * 1.02 * 1.03 - 1)
