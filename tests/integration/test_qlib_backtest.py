import numpy as np
import pandas as pd
import pytest

from etf_ml.backtest.qlib_runner import evaluate
from etf_ml.contracts import PortfolioPolicy, UniversePolicy
from etf_ml.data.universe import build_history

pytestmark = pytest.mark.qlib

def test_G1_actual_qlib_monthly_execution_reconciles_with_ledger(
        panel, calendar, metadata, source_spec, tmp_path):
    policy = PortfolioPolicy(k_mode="fraction", liquidity_mode="participation",
                             risk_mode="max_drawdown", minimum_commission=0,
                             k=0.34, liquidity_lookback=5)
    universe = build_history(panel, metadata, calendar,
                             UniversePolicy(minimum_listing_days=0, liquidity_lookback=1))
    scores = panel.adj_close.rename("score")
    benchmark = pd.read_parquet(source_spec.benchmark_path)
    result = evaluate(scores, policy, panel, universe=universe, calendar=calendar,
                      benchmark=benchmark, provider=source_spec.source,
                      recorder_uri=tmp_path / "recorders",
                      start_time=calendar[10], end_time=calendar[60],
                      annualization_days=240, risk_free_rate=0.02)
    assert result.metrics["accounting_reconciled"]
    assert result.metrics["annualization_days"] == 240
    assert result.metrics["risk_free_rate"] == 0.02
    expected_vol = result.daily_returns.std(ddof=1) * np.sqrt(240)
    assert result.metrics["annualized_volatility"] == pytest.approx(expected_vol)
    if expected_vol > 0:
        expected_sharpe = (result.daily_returns.mean() * 240 - 0.02) / expected_vol
        assert result.metrics["sharpe"] == pytest.approx(expected_sharpe)
    assert result.metrics["trade_count"] > 0
    assert np.isfinite(result.daily_returns).all()
    for decision in result.decisions:
        assert pd.Timestamp(decision["signal_date"]) < pd.Timestamp(decision["date"])
    assert result.trades.price.notna().all()
    assert result.trades.commission.sum() > 0
    assert result.execution.index.equals(result.daily_returns.index)
    assert result.metrics["partial_order_count"] > 0
    assert result.metrics["incomplete_order_rate"] > result.metrics["unfilled_rate"]
    expected_slippage = result.trades.filled_shares * (
        result.trades.price - result.trades.reference_price).abs()
    assert result.metrics["slippage_cost"] == pytest.approx(expected_slippage.sum())
    assert result.metrics["total_execution_cost"] == pytest.approx(
        result.trades.commission.sum() + expected_slippage.sum())
    assert result.execution.executed_notional.sum() == pytest.approx(result.trades.amount.sum())
    assert result.metrics["reference_notional_fill_rate"] < 1

def test_actual_qlib_all_cash_retains_zero_execution_calendar(
        panel, calendar, metadata, source_spec, tmp_path):
    policy = PortfolioPolicy(k_mode="fraction", liquidity_mode="participation",
                             risk_mode="max_drawdown", minimum_commission=0)
    universe = build_history(panel, metadata, calendar,
                             UniversePolicy(minimum_listing_days=0, liquidity_lookback=1))
    universe["eligible"] = False
    benchmark = pd.read_parquet(source_spec.benchmark_path)
    result = evaluate(panel.adj_close.rename("score"), policy, panel, universe=universe,
                      calendar=calendar, benchmark=benchmark, provider=source_spec.source,
                      recorder_uri=tmp_path / "cash-recorders",
                      start_time=calendar[10], end_time=calendar[40])
    assert result.metrics["accounting_reconciled"]
    assert result.trades.empty
    assert result.metrics["trade_count"] == 0
    assert result.metrics["total_execution_cost"] == 0
    assert result.metrics["reference_notional_fill_rate"] is None
    assert result.execution.index.equals(result.daily_returns.index)
    assert result.execution.to_numpy().sum() == 0
    assert (result.exposures.cash_weight == 1).all()
    assert (result.daily_returns == 0).all()
    from etf_ml.backtest.results import persist_result
    output = tmp_path / "all-cash-result"
    persist_result(result, output)
    assert pd.read_parquet(output / "trades.parquet").empty
    assert pd.read_parquet(output / "execution.parquet").index.equals(result.daily_returns.index)
