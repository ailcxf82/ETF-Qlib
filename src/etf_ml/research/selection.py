from __future__ import annotations

import math
import statistics
from dataclasses import asdict
from pathlib import Path

from etf_ml.contracts import EvaluationResult
from etf_ml.research.protocol import ComparisonProtocol
from etf_ml.utils import atomic_json

REQUIRED_METRICS = ("excess_return", "max_drawdown", "turnover", "annualized_volatility")
EXPOSURE_METRICS = ("max_single_weight", "max_group_weight", "max_unclassified_weight", "mean_cash_weight")
SAMPLE_KEYS = ("fold", "holdout_start", "counts", "sample_index_hashes",
               "evaluation_index_hash", "processor_fit_index_hash", "processor_kind", "qlib_roles")


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_report(report: dict, protocol: ComparisonProtocol) -> dict:
    if report.get("status") != "completed" or report.get("protocol_id") != protocol.protocol_id:
        raise ValueError("report_not_completed_or_protocol_mismatch")
    if report.get("snapshot_id") != protocol.snapshot_id:
        raise ValueError("snapshot_mismatch")
    rows = {}
    expected = {(f.name, s) for f in protocol.validation.folds for s in protocol.research.seeds}
    for row in report.get("by_fold", []):
        key = (row["fold"], row["seed"])
        if key in rows or key not in expected or row["model"] != protocol.model.name:
            raise ValueError("experimental_matrix_mismatch")
        expected_fold = next(f for f in protocol.validation.folds if f.name == key[0])
        constructor = dict(protocol.model.constructor)
        for alias in ("seed", "random_state", "random_seed"):
            if alias in constructor:
                constructor[alias] = key[1]
        expected_model = {**protocol.model.model_dump(mode="json"),
                          "seed": key[1], "constructor": constructor}
        if row.get("model_spec") != expected_model or row["dataset"].get("fold") != expected_fold.model_dump(mode="json"):
            raise ValueError("model_or_fold_configuration_mismatch")
        if not row.get("daily_index_hash"):
            raise ValueError("daily_evaluation_identity_missing")
        if any(k not in row["dataset"] for k in SAMPLE_KEYS):
            raise ValueError("sample_identity_missing")
        for metrics in [row["portfolio"], *row.get("cost_stress", {}).values()]:
            if any(not _number(metrics.get(k)) for k in REQUIRED_METRICS + EXPOSURE_METRICS):
                raise ValueError("metric_missing_or_nonfinite")
            if not 0 <= metrics["max_drawdown"] <= 1 or metrics["turnover"] < 0:
                raise ValueError("invalid_portfolio_metric")
            if any(not 0 <= metrics[k] <= 1 + 1e-8 for k in EXPOSURE_METRICS):
                raise ValueError("invalid_exposure_metric")
            if (metrics["max_group_weight"] + 1e-8 < metrics["max_single_weight"] or
                    metrics["max_group_weight"] + 1e-8 < metrics["max_unclassified_weight"]):
                raise ValueError("inconsistent_exposure_metrics")
        if set(row.get("cost_stress", {})) != {str(m) for m in protocol.cost_multipliers}:
            raise ValueError("cost_pressure_matrix_mismatch")
        rows[key] = row
    if set(rows) != expected:
        raise ValueError("incomplete_experimental_matrix")
    return rows


def compare(baseline: dict, candidate: dict, protocol: ComparisonProtocol, *,
            run_id: str, ablation: dict | None = None, output: Path | None = None) -> EvaluationResult:
    """Deterministic gates; LLM commentary cannot override this decision.

    Seeds are summarized inside each fold. They are not counted as additional
    independent time periods when computing the majority-fold condition.
    """
    protocol.portfolio.require_resolved()
    result = EvaluationResult(status="failed", stage="factor_selection", run_id=run_id,
                              baseline_id=baseline.get("feature_set_id"),
                              candidate_id=candidate.get("feature_set_id"))
    try:
        if baseline.get("feature_set_id") != protocol.baseline_feature_set_id:
            raise ValueError("baseline_feature_identity_mismatch")
        if candidate.get("baseline_feature_set_id") != protocol.baseline_feature_set_id:
            raise ValueError("candidate_lineage_mismatch")
        if candidate.get("feature_set_id") == protocol.baseline_feature_set_id:
            raise ValueError("candidate_has_no_new_features")
        left, right = _validate_report(baseline, protocol), _validate_report(candidate, protocol)
        removed = _validate_report(ablation, protocol) if ablation is not None else None
        if removed is not None and ablation.get("feature_set_id") != protocol.baseline_feature_set_id:
            raise ValueError("ablation_feature_identity_mismatch")
        violations = []
        for key in sorted(left):
            b, c = left[key], right[key]
            if b["daily_index_hash"] != c["daily_index_hash"]:
                raise ValueError("daily_evaluation_dates_mismatch")
            if removed is not None and b["daily_index_hash"] != removed[key]["daily_index_hash"]:
                raise ValueError("ablation_evaluation_dates_mismatch")
            if any(b["dataset"][k] != c["dataset"][k] for k in SAMPLE_KEYS):
                raise ValueError("paired_sample_mismatch")
            if removed is not None and any(b["dataset"][k] != removed[key]["dataset"][k] for k in SAMPLE_KEYS):
                raise ValueError("ablation_sample_mismatch")
            delta = {"fold": key[0], "seed": key[1],
                     **{k: c["portfolio"][k] - b["portfolio"][k] for k in REQUIRED_METRICS}}
            result.paired_deltas.append(delta)
            result.by_fold.append({"fold": key[0], "seed": key[1],
                                   "baseline": b["portfolio"], "candidate": c["portfolio"],
                                   "candidate_cost_stress": c["cost_stress"]})
            if delta["max_drawdown"] > protocol.research.max_drawdown_deterioration + 1e-12:
                violations.append("drawdown_deterioration")
            if delta["turnover"] > protocol.research.max_turnover_deterioration + 1e-12:
                violations.append("turnover_deterioration")
            risk_key = "max_drawdown" if protocol.portfolio.risk_mode == "max_drawdown" else "annualized_volatility"
            if c["portfolio"][risk_key] > protocol.portfolio.risk + 1e-12:
                violations.append("absolute_risk_limit")
            single_cap = (min(protocol.portfolio.max_weight, protocol.portfolio.k)
                          if protocol.portfolio.k_mode == "weight_cap" else protocol.portfolio.max_weight)
            for prefix, metrics in [("", c["portfolio"]), *[
                    ("cost_pressure_", s) for s in c["cost_stress"].values()]]:
                if metrics["max_single_weight"] > single_cap + 1e-8:
                    violations.append(prefix + "single_weight_limit")
                if metrics["max_group_weight"] > protocol.portfolio.max_group_weight + 1e-8:
                    violations.append(prefix + "group_weight_limit")
                if metrics["max_unclassified_weight"] > 1e-12:
                    violations.append(prefix + "unclassified_holding_exposure")
            for stress in c["cost_stress"].values():
                if stress[risk_key] > protocol.portfolio.risk + 1e-12:
                    violations.append("cost_pressure_risk_limit")
                if protocol.stress_min_excess_return is not None and stress["excess_return"] < protocol.stress_min_excess_return:
                    violations.append("cost_pressure_return_limit")
        fold_deltas = [statistics.median(d["excess_return"] for d in result.paired_deltas
                                        if d["fold"] == f.name) for f in protocol.validation.folds]
        if statistics.median(fold_deltas) <= 0 or sum(v > 0 for v in fold_deltas) <= len(fold_deltas) / 2:
            violations.append("no_majority_fold_increment")
        for seed in protocol.research.seeds:
            if statistics.median(d["excess_return"] for d in result.paired_deltas if d["seed"] == seed) <= 0:
                violations.append("seed_instability")
        if removed is not None:
            ablation_deltas = [statistics.median(
                right[(f.name, s)]["portfolio"]["excess_return"] -
                removed[(f.name, s)]["portfolio"]["excess_return"]
                for s in protocol.research.seeds) for f in protocol.validation.folds]
            if statistics.median(ablation_deltas) <= 0 or sum(v > 0 for v in ablation_deltas) <= len(ablation_deltas) / 2:
                violations.append("ablation_not_confirmed")
        if violations:
            result.status, result.reasons = "rejected", sorted(set(violations))
        elif ablation is None or protocol.stress_min_excess_return is None:
            result.status = "inconclusive"
            result.reasons = ([ "ablation_evidence_missing" ] if ablation is None else []) + (
                ["cost_pressure_threshold_unresolved"] if protocol.stress_min_excess_return is None else [])
        else:
            result.status, result.reasons = "accepted", ["all_frozen_gates_passed"]
    except (ValueError, KeyError, TypeError, statistics.StatisticsError) as exc:
        result.status = "failed"
        result.reasons = [str(exc)]
    if output is not None:
        atomic_json(output, {**asdict(result), "protocol_id": protocol.protocol_id})
        result.artifacts["evaluation"] = str(output)
    return result
