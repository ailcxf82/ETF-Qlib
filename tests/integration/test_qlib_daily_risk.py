"""Real Qlib execution on synthetic quotes; not financial acceptance evidence."""
import numpy as np
import pandas as pd
import pytest

from etf_ml.backtest.qlib_runner import evaluate
from etf_ml.contracts import PortfolioPolicy, UniversePolicy
from etf_ml.data.universe import build_history

pytestmark = pytest.mark.qlib


@pytest.mark.parametrize("execution_case", ["gap", "blocked", "partial", "missing_quote"])
def test_daily_prior_close_risk_exit_without_rebalance_or_prediction(
        panel, calendar, metadata, source_spec, tmp_path, execution_case):
    instrument = "510300.SH"
    frame = panel.copy()
    for name in ("raw_open", "raw_close", "raw_high", "raw_low"):
        frame[name] = 10.
    frame["amount_currency"] = 1e9
    shock, exit_day, retry = calendar[12:15]
    mask = (frame.index.get_level_values("datetime") >= shock) & (
        frame.index.get_level_values("instrument") == instrument)
    frame.loc[mask, "raw_close"] = 7.
    frame.loc[(exit_day, instrument), "raw_open"] = 6.
    if execution_case == "blocked":
        frame.loc[(exit_day, instrument), "tradable"] = False
    elif execution_case == "partial":
        frame.loc[(shock, instrument), "amount_currency"] = 2000.
    elif execution_case == "missing_quote":
        frame.loc[(exit_day, instrument), "raw_open"] = np.nan
    policy = PortfolioPolicy(k_mode="count", k=1, liquidity_mode="participation",
        risk_mode="max_drawdown", initial_cash=10000, risk=.12, minimum_commission=0,
        commission_rate=0, slippage_rate=0, liquidity=1, liquidity_lookback=1)
    universe = build_history(frame, metadata, calendar,
        UniversePolicy(minimum_listing_days=0, liquidity_lookback=1))
    scores = pd.Series(np.where(frame.index.get_level_values("instrument") == instrument, 2., 1.),
                       index=frame.index, name="score")
    # Buy on the scheduled mid-month rebalance, then lose all model signals.
    scores = scores[scores.index.get_level_values("datetime") <= calendar[10]]
    result = evaluate(scores, policy, frame, universe=universe, calendar=calendar,
        benchmark=pd.read_parquet(source_spec.benchmark_path), provider=source_spec.source,
        recorder_uri=tmp_path / "recorders", start_time=calendar[8], end_time=calendar[19])
    checks = {pd.Timestamp(row["date"]): row for row in result.risk_checks}
    assert len(checks) == len(result.daily_returns)
    assert all(row["checked"] for row in checks.values())
    assert checks[shock]["risk_triggered"] is False  # cannot observe today's close yet
    assert checks[exit_day]["risk_triggered"] is True
    assert checks[exit_day]["drawdown"] == pytest.approx(.3)
    assert checks[exit_day]["risk_trigger_limit"] == .12
    assert checks[exit_day]["drawdown_limit"] == .12
    assert checks[exit_day]["acceptance_limit_exceeded"] is True
    assert checks[exit_day]["planned_rebalance"] is False
    assert checks[exit_day]["equity_at_decision"] == pytest.approx(7000.)
    assert checks[exit_day]["order_sizing_basis"] == "held_shares_and_prior_close_risk"
    assert checks[exit_day]["risk_order_intents"][0]["requested_shares"] == 1000.
    sells = result.trades[result.trades.direction == 0]
    first = sells.iloc[0]
    assert first.datetime == exit_day
    assert first.status == {"gap": "filled", "blocked": "unfilled", "partial": "partial",
                            "missing_quote": "unfilled"}[execution_case]
    if execution_case != "gap":
        assert sells.iloc[1].datetime == retry
        assert sells.iloc[1].status == "filled"
    assert sells.filled_shares.sum() == 1000.
    assert result.metrics["daily_accounting_reconciled"]
    assert result.metrics["max_drawdown"] > policy.risk  # no fabricated hard stop guarantee
    assert all(row["peak_equity_at_decision"] == 10000. for row in checks.values())
    assert not any(row.get("risk_triggered") is False for row in result.decisions
                   if pd.Timestamp(row["date"]) >= exit_day)


def test_drawdown_trigger_can_precede_fixed_acceptance_limit(
        panel, calendar, metadata, source_spec, tmp_path):
    instrument = "510300.SH"
    frame = panel.copy()
    for name in ("raw_open", "raw_close", "raw_high", "raw_low"):
        frame[name] = 10.
    frame["amount_currency"] = 1e9
    shock, trigger_day = calendar[12:14]
    frame.loc[(shock, instrument), "raw_close"] = 9.1
    policy = PortfolioPolicy(k_mode="count", k=1, liquidity_mode="participation",
        risk_mode="max_drawdown", initial_cash=10000, risk=.08, max_drawdown_limit=.12,
        minimum_commission=0, commission_rate=0, slippage_rate=0, liquidity=1, liquidity_lookback=1)
    universe = build_history(frame, metadata, calendar,
        UniversePolicy(minimum_listing_days=0, liquidity_lookback=1))
    scores = pd.Series(np.where(frame.index.get_level_values("instrument") == instrument, 2., 1.),
                       index=frame.index, name="score")
    scores = scores[scores.index.get_level_values("datetime") <= calendar[10]]
    result = evaluate(scores, policy, frame, universe=universe, calendar=calendar,
        benchmark=pd.read_parquet(source_spec.benchmark_path), provider=source_spec.source,
        recorder_uri=tmp_path / "recorders", start_time=calendar[8], end_time=calendar[18])
    checks = {pd.Timestamp(row["date"]): row for row in result.risk_checks}
    assert checks[shock]["risk_triggered"] is False
    assert checks[trigger_day]["risk_triggered"] is True
    assert checks[trigger_day]["drawdown"] == pytest.approx(.09)
    assert checks[trigger_day]["risk_trigger_limit"] == .08
    assert checks[trigger_day]["drawdown_limit"] == .12
    assert checks[trigger_day]["acceptance_limit_exceeded"] is False


def test_daily_volatility_risk_uses_completed_returns_and_only_reduces_holdings(
        panel, calendar, metadata, source_spec, tmp_path):
    frame = panel.copy()
    for name in ("raw_open", "raw_close", "raw_high", "raw_low"):
        frame[name] = 10.
    frame["amount_currency"] = 1e9
    frame.loc[(calendar[34], "510300.SH"), "raw_close"] = 8.
    policy = PortfolioPolicy(k_mode="count", k=1, liquidity_mode="participation",
        risk_mode="annualized_volatility", initial_cash=10000, risk=.1, minimum_commission=0,
        commission_rate=0, slippage_rate=0, liquidity=1, liquidity_lookback=1)
    universe = build_history(frame, metadata, calendar,
        UniversePolicy(minimum_listing_days=0, liquidity_lookback=1))
    scores = pd.Series(np.where(frame.index.get_level_values("instrument") == "510300.SH", 2., 1.),
                       index=frame.index, name="score")
    result = evaluate(scores, policy, frame, universe=universe, calendar=calendar,
        benchmark=pd.read_parquet(source_spec.benchmark_path), provider=source_spec.source,
        recorder_uri=tmp_path / "recorders", start_time=calendar[8], end_time=calendar[39])
    for index, row in enumerate(result.risk_checks):
        if index < 20:
            assert row["annualized_volatility_20d"] is None
            assert row["risk_limit_exceeded"] is None
        else:
            expected = result.daily_returns.iloc[index - 20:index].std(ddof=1) * np.sqrt(252)
            assert row["annualized_volatility_20d"] == pytest.approx(expected)
    trigger = next(row for row in result.risk_checks if row["risk_triggered"])
    assert pd.Timestamp(trigger["date"]) == calendar[35]
    assert trigger["planned_rebalance"] is False
    assert trigger["risk_order_intents"]
    after_trigger = result.trades[result.trades.datetime >= calendar[35]]
    assert after_trigger.direction.eq(0).all()
    assert result.metrics["daily_accounting_reconciled"]
