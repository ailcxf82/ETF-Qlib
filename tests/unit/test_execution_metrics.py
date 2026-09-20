import numpy as np
import pandas as pd
import pytest

from etf_ml.backtest.execution import TRADE_COLUMNS, execution_metrics
from etf_ml.errors import QualityError


def ledger():
    date = pd.Timestamp("2023-01-03")
    rows = [
        dict(datetime=date, instrument="A", direction=1, requested_shares=1000,
             filled_shares=1000, amount=10003., commission=30.009, price=10.003,
             reference_price=10., slippage_cost=3., status="filled", reason=None,
             historical_average_amount=1e6),
        dict(datetime=date, instrument="B", direction=0, requested_shares=1000,
             filled_shares=500, amount=9997., commission=29.991, price=19.994,
             reference_price=20., slippage_cost=3., status="partial", reason="liquidity",
             historical_average_amount=1e5),
        dict(datetime=date, instrument="C", direction=1, requested_shares=100,
             filled_shares=0, amount=0., commission=0., price=None,
             reference_price=5., slippage_cost=0., status="unfilled", reason="not_tradable",
             historical_average_amount=1e5),
        dict(datetime=date, instrument="D", direction=0, requested_shares=50,
             filled_shares=0, amount=0., commission=0., price=None,
             reference_price=None, slippage_cost=0., status="unfilled", reason="no_quote",
             historical_average_amount=1e5),
    ]
    return pd.DataFrame(rows, columns=TRADE_COLUMNS)


def test_costs_partial_fills_missing_quotes_and_daily_alignment():
    calendar = pd.bdate_range("2023-01-02", periods=3, name="datetime")
    summary, daily = execution_metrics(ledger(), calendar, 500000.)
    assert daily.index.equals(calendar)
    assert daily.loc[calendar[0]].sum() == 0
    assert daily.loc[calendar[2]].sum() == 0
    assert summary["trade_count"] == 4
    assert summary["filled_order_count"] == 1
    assert summary["partial_order_count"] == 1
    assert summary["unfilled_order_count"] == 2
    assert summary["unpriced_order_count"] == 1
    assert summary["unfilled_rate"] == .5
    assert summary["incomplete_order_rate"] == .75
    assert summary["reference_notional_fill_rate"] == pytest.approx(20000 / 30500)
    assert summary["commission"] == pytest.approx(60.)
    assert summary["slippage_cost"] == pytest.approx(6.)
    assert summary["executed_notional"] == pytest.approx(20000.)
    assert summary["buy_notional"] == pytest.approx(10003.)
    assert summary["sell_notional"] == pytest.approx(9997.)
    assert summary["total_execution_cost"] == pytest.approx(66.)
    assert summary["execution_cost_over_initial_equity"] == pytest.approx(66 / 500000)
    assert summary["execution_cost_over_traded_notional"] == pytest.approx(66 / 20000)
    # The attribution is descriptive: execution prices already include slippage.
    assert daily.total_execution_cost.sum() == pytest.approx(66.)


def test_no_orders_retains_all_evaluation_dates_without_invented_fill_ratio():
    calendar = pd.bdate_range("2023-01-02", periods=3, name="datetime")
    summary, daily = execution_metrics(pd.DataFrame(columns=TRADE_COLUMNS), calendar, 500000)
    assert summary["trade_count"] == summary["total_execution_cost"] == 0
    assert summary["reference_notional_fill_rate"] is None
    assert summary["execution_cost_over_traded_notional"] is None
    assert daily.to_numpy().sum() == 0
    assert daily.order_count.dtype == np.dtype("int64")


@pytest.mark.parametrize("column,value", [
    ("amount", 9999.), ("slippage_cost", 4.), ("commission", -1.),
    ("filled_shares", 1001.), ("requested_shares", 0.),
    ("price", None), ("reference_price", np.inf),
    ("status", "partial"), ("direction", 2),
    ("datetime", pd.Timestamp("2023-02-01")),
])
def test_invalid_ledger_fails_instead_of_reporting_success(column, value):
    trades = ledger()
    trades.loc[0, column] = value
    with pytest.raises(QualityError):
        execution_metrics(trades, pd.bdate_range("2023-01-02", periods=3), 500000)


def test_unfilled_order_cannot_be_charged_and_incomplete_schema_fails():
    trades = ledger()
    trades.loc[2, "commission"] = 1.
    with pytest.raises(QualityError, match="Unfilled"):
        execution_metrics(trades, pd.bdate_range("2023-01-02", periods=3), 500000)
    with pytest.raises(QualityError, match="Incomplete"):
        execution_metrics(pd.DataFrame(), pd.bdate_range("2023-01-02", periods=3), 500000)

def test_favorable_execution_price_cannot_be_reported_as_adverse_slippage():
    trades = ledger()
    trades.loc[0, "price"] = 9.997
    trades.loc[0, "amount"] = 9997.
    # Absolute price difference alone would otherwise accept a favorable fill.
    with pytest.raises(QualityError, match="adverse"):
        execution_metrics(trades, pd.bdate_range("2023-01-02", periods=3), 500000)
