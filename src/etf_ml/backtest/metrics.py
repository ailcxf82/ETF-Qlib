from __future__ import annotations

import numpy as np
import pandas as pd

from etf_ml.errors import QualityError


def portfolio_metrics(daily_returns: pd.Series, benchmark_returns: pd.Series, *,
                      annualization_days=252, risk_free_rate=0.) -> dict:
    if daily_returns.empty or not daily_returns.index.equals(benchmark_returns.index):
        raise QualityError("Strategy and benchmark dates must be identical")
    if not np.isfinite(daily_returns).all() or not np.isfinite(benchmark_returns).all():
        raise QualityError("Daily returns must be finite")
    if (daily_returns <= -1).any() or (benchmark_returns <= -1).any():
        raise QualityError("Invalid daily return")
    wealth = (1 + daily_returns).cumprod()
    benchmark = (1 + benchmark_returns).cumprod()
    # Include initial equity in the running peak so an immediate loss is counted.
    peaks = wealth.cummax().clip(lower=1)
    drawdown = wealth / peaks - 1
    n = len(daily_returns)
    annual = float(wealth.iloc[-1] ** (annualization_days / n) - 1)
    vol = float(daily_returns.std(ddof=1) * np.sqrt(annualization_days)) if n > 1 else 0.
    excess_daily = daily_returns - risk_free_rate / annualization_days
    sharpe = float(excess_daily.mean() * annualization_days / vol) if vol > 0 else None
    return {"net_return": float(wealth.iloc[-1] - 1), "annualized_return": annual,
            "benchmark_return": float(benchmark.iloc[-1] - 1),
            "excess_return": float(wealth.iloc[-1] - benchmark.iloc[-1]),
            "max_drawdown": float(-drawdown.min()), "annualized_volatility": vol,
            "sharpe": sharpe, "effective_dates": n,
            "annualization_days": annualization_days, "risk_free_rate": risk_free_rate}


def predictive_metrics(scores: pd.Series, labels: pd.Series, *, minimum_cross_section=3) -> dict:
    if not scores.index.equals(labels.index):
        raise QualityError("Predictive evaluation indices differ")
    by_date = []
    for date, prediction in scores.groupby(level="datetime"):
        target = labels.loc[prediction.index]
        good = prediction.notna() & target.notna()
        x, y = prediction[good], target[good]
        valid = len(x) >= minimum_cross_section and x.nunique() > 1 and y.nunique() > 1
        by_date.append({"date": str(date.date()), "cross_section": len(x), "valid": bool(valid),
                        "ic": float(x.corr(y)) if valid else None,
                        "rank_ic": float(x.corr(y, method="spearman")) if valid else None})
    valid_rows = [row for row in by_date if row["valid"]]
    return {"ic": float(np.mean([r["ic"] for r in valid_rows])) if valid_rows else None,
            "rank_ic": float(np.mean([r["rank_ic"] for r in valid_rows])) if valid_rows else None,
            "effective_dates": len(valid_rows), "by_date": by_date}


def position_exposures(positions: dict, universe: pd.DataFrame):
    from etf_ml.data.source import require_panel
    require_panel(universe)
    if "tracking_group" not in universe:
        raise QualityError("Exposure accounting needs historical tracking groups")
    group_history = universe.tracking_group.unstack("instrument").sort_index().ffill()
    rows = []
    for date, position in sorted(positions.items()):
        date = pd.Timestamp(date)
        equity = float(position.calculate_value())
        cash = float(position.get_cash())
        receivable = float(position.receivable_value()) if hasattr(position, 'receivable_value') else 0.
        if not np.isfinite(receivable) or receivable < 0:
            raise QualityError("Invalid dividend receivable in exposure accounting")
        if not np.isfinite(equity) or equity <= 0 or not np.isfinite(cash) or cash < -1e-8:
            raise QualityError("Invalid equity or cash in exposure accounting")
        income = position.income_book.value() if hasattr(position, "income_book") else 0.
        if not np.isfinite(income):
            raise QualityError("Invalid signed monetary income balance")
        location = group_history.index.searchsorted(date, side="right") - 1
        known = group_history.iloc[location] if location >= 0 else pd.Series(dtype=object)
        weights, grouped, unclassified = [], {}, 0.
        for instrument in position.get_stock_list():
            quantity = float(position.get_stock_amount(instrument))
            if not np.isfinite(quantity) or quantity < 0:
                raise QualityError("Exposure accounting requires finite long positions")
            if quantity == 0:
                continue
            price = float(position.get_stock_price(instrument))
            if not np.isfinite(price) or price <= 0:
                raise QualityError("Held position requires a positive finite valuation")
            value = quantity * price
            if not np.isfinite(value) or value < 0:
                raise QualityError("Invalid held market value")
            weight = value / equity
            weights.append(weight)
            group = known.get(instrument)
            if pd.isna(group):
                unclassified += weight
                group = "__unclassified__"
            grouped[group] = grouped.get(group, 0.) + weight
        cash_weight = cash / equity
        if not np.isclose(sum(weights) + cash_weight + receivable / equity + income / equity, 1., atol=1e-8, rtol=1e-8):
            raise QualityError("Exposure values do not reconcile to account equity")
        rows.append({"datetime": date, "single_weight": max(weights, default=0.),
                     "group_weight": max(grouped.values(), default=0.),
                     "cash_weight": cash_weight, "receivable_weight": receivable / equity, "income_weight": income / equity, "unknown_group_weight": unclassified,
                     "holding_count": len(weights), "group_count": len(grouped)})
    if not rows:
        raise QualityError("No daily positions for exposure accounting")
    frame = pd.DataFrame(rows).set_index("datetime")
    summary = {"max_single_weight": float(frame.single_weight.max()),
               "max_group_weight": float(frame.group_weight.max()),
               "max_unclassified_weight": float(frame.unknown_group_weight.max()),
               "mean_cash_weight": float(frame.cash_weight.mean()),
               "max_receivable_weight": float(frame.receivable_weight.max()),
               "max_absolute_income_weight": float(frame.income_weight.abs().max()),
               "max_holding_count": int(frame.holding_count.max()),
               "group_mapping_rule": "latest_known_classification_at_or_before_valuation"}
    return summary, frame
