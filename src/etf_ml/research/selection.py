from __future__ import annotations

import math
import statistics
from dataclasses import asdict
from pathlib import Path

from etf_ml.contracts import EvaluationResult
from etf_ml.backtest.metrics import positive_icir_gate, summarize_icir_gates
from etf_ml.research.protocol import ComparisonProtocol
from etf_ml.research.statistics import moving_block_mean_uncertainty
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
            run_id: str, ablation: dict | None = None,
            factor_signal_by_fold: list[dict] | None = None,
            output: Path | None = None) -> EvaluationResult:
    """Deterministic gates; LLM commentary cannot override this decision.

    Seeds are summarized inside each fold. They are not counted as additional
    independent time periods when computing the majority-fold condition.
    Directional factor IC and ICIR must both be positive in a strict majority
    of folds (at least three of the five production development folds).
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
        result.factor_signal_gate = summarize_icir_gates(
            [{"status": "unknown"} for _ in protocol.validation.folds])
        if factor_signal_by_fold is not None:
            signal_rows = {row.get("fold"): row for row in factor_signal_by_fold
                           if isinstance(row, dict)}
            expected_folds = {fold.name for fold in protocol.validation.folds}
            if len(signal_rows) != len(factor_signal_by_fold) or set(signal_rows) != expected_folds:
                raise ValueError("factor_icir_fold_matrix_mismatch")
            for fold in protocol.validation.folds:
                row = signal_rows[fold.name]
                oriented = row.get("oriented_validation")
                if not isinstance(oriented, dict):
                    raise ValueError("factor_icir_metrics_missing")
                ic, icir = oriented.get("ic"), oriented.get("icir")
                orientation = row.get("training_orientation") or {}
                if not isinstance(orientation, dict):
                    raise ValueError("factor_training_orientation_invalid")
                direction = orientation.get("direction", "unknown")
                status = (positive_icir_gate(oriented)["status"]
                          if direction in {"positive", "reverse"} else "unknown")
                daily = [item for item in oriented.get("by_date", [])
                         if item.get("valid") is True and item.get("ic") is not None
                         and item.get("rank_ic") is not None]
                seed = protocol.bootstrap_seed + len(result.factor_signal_by_fold)
                if daily:
                    ic_uncertainty = moving_block_mean_uncertainty(
                        [item["ic"] for item in daily], block_length=protocol.time_block_length,
                        repetitions=protocol.bootstrap_repetitions, seed=seed)
                    rank_ic_uncertainty = moving_block_mean_uncertainty(
                        [item["rank_ic"] for item in daily], block_length=protocol.time_block_length,
                        repetitions=protocol.bootstrap_repetitions, seed=seed)
                    effective_observations = sum(item.get("cross_section", 0) for item in daily)
                    eligible_observations = sum(item.get("eligible_cross_section", 0) for item in daily)
                else:
                    ic_uncertainty = rank_ic_uncertainty = {
                        "status": "inconclusive", "reason": "daily_signal_series_missing",
                        "mean_confidence_interval": None,
                        "information_ratio_confidence_interval": None}
                    effective_observations = eligible_observations = 0
                result.factor_signal_by_fold.append({
                    "fold": fold.name, "ic": ic, "icir": icir,
                    "rank_ic": oriented.get("rank_ic"), "rank_icir": oriented.get("rank_icir"),
                    "effective_dates": oriented.get("effective_dates"),
                    "total_dates": oriented.get("total_dates"),
                    "effective_observations": effective_observations,
                    "eligible_observations": eligible_observations,
                    "coverage": oriented.get("coverage"),
                    "ic_uncertainty": ic_uncertainty,
                    "rank_ic_uncertainty": rank_ic_uncertainty,
                    "status": status, "training_orientation": direction})
            result.factor_signal_gate = summarize_icir_gates(result.factor_signal_by_fold)
        violations = []
        def record_gate(reason, metric, value, threshold, *, fold=None, seed=None,
                        cost_multiplier=None, minimum=False, tolerance=1e-12):
            margin = threshold - value if minimum else value - threshold
            failed = value < threshold - tolerance if minimum else value > threshold + tolerance
            result.gate_details.append({"reason": reason, "metric": metric,
                "fold": fold, "seed": seed, "cost_multiplier": cost_multiplier,
                "value": value, "threshold": threshold,
                "constraint": "minimum" if minimum else "maximum",
                "tolerance": tolerance, "exceedance": max(0., margin),
                "status": "failed" if failed else "passed"})
            if failed:
                violations.append(reason)

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
            record_gate("drawdown_deterioration", "max_drawdown_delta", delta["max_drawdown"],
                        protocol.research.max_drawdown_deterioration, fold=key[0], seed=key[1])
            record_gate("turnover_deterioration", "turnover_delta", delta["turnover"],
                        protocol.research.max_turnover_deterioration, fold=key[0], seed=key[1])
            risk_key = "max_drawdown" if protocol.portfolio.risk_mode == "max_drawdown" else "annualized_volatility"
            risk_limit = (protocol.portfolio.max_drawdown_limit
                          if protocol.portfolio.risk_mode == "max_drawdown" else protocol.portfolio.risk)
            record_gate("absolute_risk_limit", risk_key, c["portfolio"][risk_key],
                        risk_limit, fold=key[0], seed=key[1])
            single_cap = (min(protocol.portfolio.max_weight, protocol.portfolio.k)
                          if protocol.portfolio.k_mode == "weight_cap" else protocol.portfolio.max_weight)
            for multiplier, metrics in [(None, c["portfolio"]), *c["cost_stress"].items()]:
                prefix = "" if multiplier is None else "cost_pressure_"
                for reason, metric, limit, tolerance in [
                        ("single_weight_limit", "max_single_weight", single_cap, 1e-8),
                        ("group_weight_limit", "max_group_weight", protocol.portfolio.max_group_weight, 1e-8),
                        ("unclassified_holding_exposure", "max_unclassified_weight", 0., 1e-12)]:
                    record_gate(prefix + reason, metric, metrics[metric], limit, fold=key[0], seed=key[1],
                                cost_multiplier=multiplier, tolerance=tolerance)
            for multiplier, stress in c["cost_stress"].items():
                record_gate("cost_pressure_risk_limit", risk_key, stress[risk_key], risk_limit,
                            fold=key[0], seed=key[1], cost_multiplier=multiplier)
                if protocol.stress_min_excess_return is not None:
                    record_gate("cost_pressure_return_limit", "excess_return", stress["excess_return"],
                                protocol.stress_min_excess_return, fold=key[0], seed=key[1],
                                cost_multiplier=multiplier, minimum=True, tolerance=0.)
        fold_deltas = [statistics.median(d["excess_return"] for d in result.paired_deltas
                                        if d["fold"] == f.name) for f in protocol.validation.folds]
        if statistics.median(fold_deltas) <= 0 or sum(v > 0 for v in fold_deltas) <= len(fold_deltas) / 2:
            violations.append("no_majority_fold_increment")
        for seed in protocol.research.seeds:
            median = statistics.median(d["excess_return"] for d in result.paired_deltas if d["seed"] == seed)
            result.gate_details.append({"reason": "seed_instability", "metric": "median_excess_return_delta",
                "seed": seed, "fold": None, "value": median, "threshold": 0.,
                "constraint": "strictly_positive", "status": "passed" if median > 0 else "failed"})
            if median <= 0:
                violations.append("seed_instability")
        if removed is not None:
            ablation_deltas = [statistics.median(
                right[(f.name, s)]["portfolio"]["excess_return"] -
                removed[(f.name, s)]["portfolio"]["excess_return"]
                for s in protocol.research.seeds) for f in protocol.validation.folds]
            if statistics.median(ablation_deltas) <= 0 or sum(v > 0 for v in ablation_deltas) <= len(ablation_deltas) / 2:
                violations.append("ablation_not_confirmed")
        if result.factor_signal_gate["status"] == "failed":
            violations.append("factor_icir_insufficient_passing_folds")
        if violations:
            result.status, result.reasons = "rejected", sorted(set(violations))
        elif ablation is None or protocol.stress_min_excess_return is None or result.factor_signal_gate["status"] != "passed":
            result.status = "inconclusive"
            result.reasons = ([ "ablation_evidence_missing" ] if ablation is None else []) + (
                ["cost_pressure_threshold_unresolved"] if protocol.stress_min_excess_return is None else []) + (
                ["factor_icir_evidence_missing" if factor_signal_by_fold is None else "factor_icir_evidence_inconclusive"]
                if result.factor_signal_gate["status"] != "passed" else [])
        else:
            result.status, result.reasons = "accepted", ["all_frozen_portfolio_and_factor_signal_gates_passed"]
    except (ValueError, KeyError, TypeError, statistics.StatisticsError) as exc:
        result.status = "failed"
        result.reasons = [str(exc)]
    if output is not None:
        atomic_json(output, {**asdict(result), "protocol_id": protocol.protocol_id})
        result.artifacts["evaluation"] = str(output)
    return result
