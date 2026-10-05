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
        good = prediction.notna() & target.notna() & np.isfinite(prediction) & np.isfinite(target)
        x, y = prediction[good], target[good]
        valid = len(x) >= minimum_cross_section and x.nunique() > 1 and y.nunique() > 1
        by_date.append({"date": str(date.date()), "cross_section": len(x),
                        "eligible_cross_section": len(prediction),
                        "coverage": float(len(x) / len(prediction)) if len(prediction) else None,
                        "valid": bool(valid),
                        "ic": float(x.corr(y)) if valid else None,
                        "rank_ic": float(x.corr(y, method="spearman")) if valid else None})
    valid_rows = [row for row in by_date if row["valid"]]

    def summarize(key):
        values = np.asarray([row[key] for row in valid_rows if row[key] is not None], dtype=float)
        if not len(values):
            return {"mean": None, "std": None, "ir": None, "positive_share": None,
                    "direction": "unknown", "ir_status": "no_valid_daily_observations"}
        mean = float(values.mean())
        positive_share = float(np.mean(values > 0))
        if len(values) < 2:
            std, ir, ir_status = None, None, "fewer_than_two_valid_dates"
        else:
            std = float(values.std(ddof=1))
            if np.isclose(std, 0., rtol=0., atol=1e-12):
                std = 0.
                ir, ir_status = None, "zero_daily_standard_deviation"
            else:
                ir, ir_status = float(mean / std), "available"
        direction = ("positive" if mean > 0 and ir is not None and ir > 0 else
                     "reverse" if mean < 0 and ir is not None and ir < 0 else "unknown")
        return {"mean": mean, "std": std, "ir": ir, "positive_share": positive_share,
                "direction": direction, "ir_status": ir_status}

    ic, rank_ic = summarize("ic"), summarize("rank_ic")
    total = sum(row["eligible_cross_section"] for row in by_date)
    observed = sum(row["cross_section"] for row in by_date)
    return {"ic": ic["mean"], "ic_mean": ic["mean"], "ic_std": ic["std"], "icir": ic["ir"],
            "ic_positive_day_share": ic["positive_share"], "ic_direction": ic["direction"],
            "icir_status": ic["ir_status"], "rank_ic": rank_ic["mean"],
            "rank_ic_mean": rank_ic["mean"], "rank_ic_std": rank_ic["std"],
            "rank_icir": rank_ic["ir"], "rank_ic_positive_day_share": rank_ic["positive_share"],
            "rank_ic_direction": rank_ic["direction"], "rank_icir_status": rank_ic["ir_status"],
            "coverage": float(observed / total) if total else None,
            "effective_dates": len(valid_rows), "total_dates": len(by_date), "by_date": by_date}


def select_factor_orientation(training_metrics: dict) -> dict:
    """Choose direct/reverse orientation from training-only IC and ICIR evidence."""
    ic, icir = training_metrics.get("ic"), training_metrics.get("icir")
    finite = all(isinstance(value, (int, float, np.number)) and
                 not isinstance(value, (bool, np.bool_)) and np.isfinite(value)
                 for value in (ic, icir))
    direction = "unknown"
    if training_metrics.get("icir_status") == "available" and finite:
        if ic > 0 and icir > 0:
            direction = "positive"
        elif ic < 0 and icir < 0:
            direction = "reverse"
    sign = 1 if direction == "positive" else -1 if direction == "reverse" else None
    return {"direction": direction, "sign": sign, "ic": ic, "icir": icir,
            "effective_dates": training_metrics.get("effective_dates"),
            "coverage": training_metrics.get("coverage"),
            "status": training_metrics.get("icir_status", "missing_training_metrics")}


def positive_icir_gate(metrics: dict) -> dict:
    ic, icir = metrics.get("ic"), metrics.get("icir")
    finite = all(isinstance(value, (int, float, np.number)) and
                 not isinstance(value, (bool, np.bool_)) and np.isfinite(value)
                 for value in (ic, icir))
    if metrics.get("icir_status") != "available" or not finite or ic == 0 or icir == 0:
        return {"status": "unknown", "reason": "ic_or_icir_unavailable_or_zero"}
    if ic > 0 and icir > 0:
        return {"status": "passed", "reason": "ic_and_icir_strictly_positive"}
    return {"status": "failed", "reason": "ic_or_icir_not_positive"}


def summarize_icir_gates(gates: list[dict]) -> dict:
    """Count each development fold once; unavailable folds never reduce the denominator."""
    statuses = [gate["status"] for gate in gates]
    if not statuses or any(status not in {"passed", "failed", "unknown"} for status in statuses):
        raise ValueError("Invalid factor IC/ICIR fold gates")
    required = len(statuses) // 2 + 1
    passed, unknown = statuses.count("passed"), statuses.count("unknown")
    status = ("passed" if passed >= required else
              "unknown" if passed + unknown >= required else "failed")
    return {"rule": "strict_majority", "status": status, "fold_count": len(statuses),
            "required_pass_folds": required, "passing_folds": passed,
            "failed_folds": statuses.count("failed"), "unknown_folds": unknown}


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
