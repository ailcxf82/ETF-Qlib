from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

from etf_ml.backtest.metrics import predictive_metrics
from etf_ml.backtest.results import evaluate_with_stress
from etf_ml.data.calendar import require_calendar
from etf_ml.data.source import require_panel
from etf_ml.errors import QualityError
from etf_ml.utils import atomic_json, code_hash, content_hash


def momentum_scores(panel, calendar, lookback=20):
    require_panel(panel)
    require_calendar(calendar)
    if "adj_close" not in panel:
        raise QualityError("Manual momentum requires adjusted closing prices")
    values = []
    for _, part in panel.groupby(level="instrument", sort=True):
        dates = part.index.get_level_values("datetime")
        prices = pd.Series(part.adj_close.to_numpy(), index=dates).reindex(calendar)
        score = (prices / prices.shift(lookback) - 1).reindex(dates)
        values.append(pd.Series(score.to_numpy(), index=part.index))
    return pd.concat(values).sort_index().replace([np.inf, -np.inf], np.nan).rename("score")


def equal_pool_policy(policy):
    policy.require_resolved()
    values = policy.model_dump(mode="python")
    # Validate both K fields together; switching a count above one to a
    # fraction before changing its value would create an invalid intermediate.
    if policy.k_mode == "weight_cap":
        values["max_weight"] = min(policy.max_weight, policy.k)
    values.update({"k_mode": "fraction", "k": 1.})
    return type(policy).model_validate(values)


def signal_coverage(scores, eligibility):
    if not scores.index.equals(eligibility.index):
        raise QualityError("Auxiliary scores must preserve the fixed decision universe index")
    finite = pd.Series(np.isfinite(scores), index=scores.index) & eligibility
    counts = eligibility.groupby(level="datetime").sum()
    available = finite.groupby(level="datetime").sum()
    rows = [{"date": str(day.date()), "eligible": int(counts.loc[day]),
             "finite_scores": int(available.loc[day]),
             "coverage": float(available.loc[day] / counts.loc[day]) if counts.loc[day] else None}
            for day in counts.index]
    fractions = [r["coverage"] for r in rows if r["coverage"] is not None]
    return {"by_date": rows, "mean": float(np.mean(fractions)) if fractions else None,
            "minimum": min(fractions) if fractions else None,
            "missing_policy": "insufficient_history_or_missing_prices_have_no_buy_signal"}


def run_auxiliary(config, panel, research_calendar, decision_universe, *, fold, snapshot_id, protocol_id,
                  labels, evaluation_index, output, cost_multipliers=(2.0,), **kwargs):
    rule_scores = {
        "manual_momentum": momentum_scores(panel, research_calendar, config.benchmarks.momentum_lookback),
        "equal_weight_pool": pd.Series(1., index=panel.index, name="score"),
    }
    rows = []
    for name, scores in rule_scores.items():
        policy = equal_pool_policy(config.portfolio) if name == "equal_weight_pool" else config.portfolio
        identity = {"schema_version": 1, "strategy": name, "snapshot_id": snapshot_id,
                    "protocol_id": protocol_id, "fold": fold.model_dump(mode="json"),
                    "benchmarks": config.benchmarks.model_dump(mode="json"),
                    "source_policy": config.portfolio.model_dump(mode="json"),
                    "execution_policy": policy.model_dump(mode="json"),
                    "universe_policy": config.universe.model_dump(mode="json"),
                    "validation": config.validation.model_dump(mode="json"),
                    "cost_multipliers": list(cost_multipliers), "code_hash": code_hash(),
                    "selection_rule": "all_buyable_pool_equal_weight" if name == "equal_weight_pool" else "configured_k_policy"}
        baseline_id = content_hash(identity)
        scores.attrs.update({"baseline_id": baseline_id, "snapshot_id": snapshot_id,
                             "score_type": "deterministic_rule", "learned": False})
        path = Path(output) / name
        path.mkdir(parents=True, exist_ok=True)
        scores.to_frame().to_parquet(path / "predictions.parquet")
        atomic_json(path / "rule_manifest.json", {**identity, "baseline_id": baseline_id})
        result, stress = evaluate_with_stress(scores, policy, panel, output=path,
                                              cost_multipliers=cost_multipliers, **kwargs)
        if not result.daily_returns.index.equals(kwargs["calendar"][
                (kwargs["calendar"] >= kwargs["start_time"]) & (kwargs["calendar"] <= kwargs["end_time"])]):
            raise QualityError("Auxiliary backtest changed the common evaluation dates")
        row = {"fold": fold.name, "strategy": name, "baseline_id": baseline_id,
               "learned": False, "portfolio": result.metrics, "cost_stress": stress,
               "execution_policy": policy.model_dump(mode="json"),
               "daily_index_hash": content_hash([str(t) for t in result.daily_returns.index]),
               "evaluation_index_hash": content_hash([[str(t), i] for t, i in evaluation_index]),
               "predictive": predictive_metrics(scores.loc[evaluation_index], labels.loc[evaluation_index]),
               "signal_coverage": signal_coverage(scores, decision_universe.eligible),
               "artifact_path": str(path)}
        atomic_json(path / "metrics.json", row)
        rows.append(row)
    return rows
