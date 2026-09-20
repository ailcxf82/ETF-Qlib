import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_series_equal

from etf_ml.backtest.auxiliary import momentum_scores, equal_pool_policy, signal_coverage
from etf_ml.contracts import BenchmarkPolicy, PortfolioPolicy


def test_momentum_uses_trading_calendar_and_preserves_warmup_rows(panel, calendar):
    scores = momentum_scores(panel, calendar, 20)
    assert scores.index.equals(panel.index)
    assert scores.loc[:calendar[19]].isna().all()
    assert scores.loc[calendar[20]:].notna().all()
    instrument = "510300.SH"
    expected = panel.loc[(calendar[40], instrument), "adj_close"] / panel.loc[
        (calendar[20], instrument), "adj_close"] - 1
    assert scores.loc[(calendar[40], instrument)] == pytest.approx(expected)
    missing = panel.drop(index=(calendar[20], instrument))
    changed = momentum_scores(missing, calendar, 20)
    assert pd.isna(changed.loc[(calendar[40], instrument)])
    assert changed.index.equals(missing.index)


def test_momentum_is_causal_under_future_perturbation_and_truncation(panel, calendar):
    first = momentum_scores(panel, calendar)
    changed = panel.copy()
    changed.loc[changed.index.get_level_values("datetime") > calendar[70], "adj_close"] *= 100
    assert_series_equal(first.loc[:calendar[70]], momentum_scores(changed, calendar).loc[:calendar[70]])
    truncated = panel.loc[:calendar[70]]
    assert_series_equal(first.loc[:calendar[70]], momentum_scores(truncated, calendar[:71]))


@pytest.mark.parametrize("mode,k,cap", [("fraction", .05, .7), ("count", 2, .7), ("weight_cap", .05, .05)])
def test_equal_pool_preserves_cost_risk_liquidity_and_weight_cap(mode, k, cap):
    policy = PortfolioPolicy(k_mode=mode, k=k, minimum_commission=5,
                             liquidity_mode="participation", risk_mode="max_drawdown",
                             max_weight=.7, max_group_weight=.8, max_turnover=.4)
    original = policy.model_dump()
    equal = equal_pool_policy(policy)
    assert policy.model_dump() == original
    assert equal.k == 1 and equal.k_mode == "fraction" and equal.max_weight == cap
    for key, value in original.items():
        if key not in ("k", "k_mode", "max_weight"):
            assert equal.model_dump()[key] == value


def test_coverage_reports_missing_scores_without_shrinking_target(panel, calendar):
    scores = momentum_scores(panel, calendar)
    eligible = pd.Series(True, index=panel.index)
    report = signal_coverage(scores, eligible)
    assert len(report["by_date"]) == len(calendar)
    assert report["minimum"] == 0
    assert report["by_date"][0]["eligible"] == 3
    assert report["by_date"][0]["finite_scores"] == 0
    assert report["by_date"][20]["coverage"] == 1
    assert report["mean"] == pytest.approx((len(calendar) - 20) / len(calendar))


@pytest.mark.parametrize("lookback", [0, -1, 121])
def test_invalid_benchmark_window_is_rejected(lookback):
    with pytest.raises(ValueError):
        BenchmarkPolicy(momentum_lookback=lookback)


@pytest.mark.parametrize("multipliers", [(1.,), (float("nan"),), (2., 2.), (1000.,)])
def test_invalid_cost_stress_is_rejected_before_backtest(tmp_path, monkeypatch, multipliers):
    from etf_ml.backtest import results
    from etf_ml.errors import ConfigurationError
    monkeypatch.setattr(results, "evaluate", lambda *a, **k: pytest.fail("invalid costs reached execution"))
    with pytest.raises(ConfigurationError):
        results.evaluate_with_stress(None, PortfolioPolicy(), None,
                                     output=tmp_path, cost_multipliers=multipliers)
