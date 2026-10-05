from __future__ import annotations

import math
import re
import statistics

from rdagent.core.proposal import Experiment2Feedback, HypothesisFeedback

from etf_ml.errors import QualityError
from etf_ml.research.diagnostics import (PREDICTIVE_KEYS, PREDICTIVE_STATUS_KEYS,
                                         PORTFOLIO_KEYS)
from etf_ml.utils import canonical_json

DELTA_KEYS = {"fold", "seed", "excess_return", "max_drawdown", "turnover", "annualized_volatility"}
STATISTIC_KEYS = ('effective_dates', 'block_length', 'attempted_trials',
                  'observed_net_return_increment', 'bootstrap_fraction_positive')
IDENTIFIER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,128}$')


def _identifier(value):
    return value if isinstance(value, str) and IDENTIFIER.fullmatch(value) else None


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _numbers(row, keys):
    return {key: _number(row.get(key)) for key in keys}


def _reasons(values):
    return [value for value in values if _identifier(value)]


def _deltas(rows):
    return [{key: (_identifier(row[key]) if key == 'fold' else _number(row[key]))
             for key in DELTA_KEYS if key in row} for row in rows]


def compact_diagnostics(diagnostics):
    """Allowlist development summaries; never forward dates, paths or raw frames."""
    if not diagnostics:
        return {'status': 'unavailable'}
    quality = diagnostics.get('data_quality', {})
    predictive = diagnostics.get('predictive_metrics', {})
    robustness = diagnostics.get('robustness', {})
    factors = diagnostics.get('factor_diagnostics', {})
    quality_rows = []
    for row in quality.get('by_fold', []):
        groups = sorted(((name, _number(value)) for name, value in row.get('by_group', {}).items()
                         if _identifier(name)), key=lambda item: (item[1] is None, item[1] or 0, item[0]))
        quality_rows.append({'fold': _identifier(row.get('fold')),
            **_numbers(row, ('target_rows', 'coverage', 'worst_date_coverage')),
            'worst_groups': dict(groups[:3]), 'group_count': len(groups)})
    def predictive_summary(row):
        return {**_numbers(row, PREDICTIVE_KEYS),
                **{key: _identifier(row.get(key)) for key in PREDICTIVE_STATUS_KEYS}}

    model_rows = [{'fold': _identifier(row.get('fold')), 'seed': _number(row.get('seed')),
                   **{kind: predictive_summary(row.get(kind, {})) for kind in ('baseline', 'candidate')}}
                  for row in predictive.get('model_by_fold', [])]
    portfolio_rows = [{'fold': _identifier(row.get('fold')), 'seed': _number(row.get('seed')),
                       **{kind: _numbers(row.get(kind, {}), PORTFOLIO_KEYS) for kind in ('baseline', 'candidate')}}
                      for row in diagnostics.get('portfolio_metrics', {}).get('by_fold', [])]
    pressure = [{'fold': _identifier(row.get('fold')), **_numbers(row, ('seed', 'multiplier', 'excess_return_delta')),
                 **{kind: _numbers(row.get(kind, {}), ('excess_return', 'max_drawdown', 'total_execution_cost'))
                    for kind in ('baseline', 'candidate')}} for row in robustness.get('cost_stress', [])]
    correlations = sorted(((name, _number(value)) for name, value in factors.get('redundancy', {}).items()
                           if _identifier(name)), key=lambda item: (item[1] is None, -(item[1] or 0), item[0]))
    execution_rows = []
    for row in diagnostics.get('signal_to_execution', {}).get('by_fold', []):
        execution_rows.append({
            'fold': _identifier(row.get('fold')), 'seed': _number(row.get('seed')),
            'status': _identifier(row.get('status')), 'reason': _identifier(row.get('reason')),
            **_numbers(row, ('matched_decisions', 'rank_changed', 'selection_changed',
                             'target_weight_changed', 'candidate_execution_constrained',
                             'baseline_execution_constrained', 'missing_decisions', 'rank_comparisons',
                             'missing_rank_predictions', 'execution_cost_delta')),
            'observations': _reasons(row.get('observations', [])),
            'prediction_compared_rows': _number(row.get('prediction_change', {}).get('compared_rows')),
            'prediction_changed_rows': _number(row.get('prediction_change', {}).get('changed_rows')),
            'prediction_change_status': _identifier(row.get('prediction_change', {}).get('status')),
            'model_feature_entry_status': _identifier(row.get('model_feature_entry', {}).get('status')),
            'baseline_risk_only_decisions': _number(row.get('risk_only_decisions', {}).get('baseline')),
            'candidate_risk_only_decisions': _number(row.get('risk_only_decisions', {}).get('candidate')),
            'causal_interpretation': _identifier(row.get('causal_interpretation')),
        })
    return {'status': 'completed',
        'data_quality': {**_numbers(quality, ('coverage', 'worst_date_coverage', 'nonfinite', 'duplicate_keys')),
                         'time_check': _identifier(quality.get('time_check')),
                         'index_check': _identifier(quality.get('index_check')), 'by_fold': quality_rows},
        'predictive_metrics': {'factor_by_fold': [
            {'fold': _identifier(row.get('fold')), **predictive_summary(row),
             'training_direction': _identifier(row.get('training_orientation', {}).get('direction')),
             'oriented_validation': predictive_summary(row.get('oriented_validation', {})),
             'oriented_validation_gate': _identifier(row.get('oriented_validation_gate', {}).get('status'))}
            for row in predictive.get('factor_by_fold', [])], 'model_by_fold': model_rows,
            'factor_signal_gate': {
                **_numbers(predictive.get('factor_signal_gate', {}),
                           ('fold_count', 'required_pass_folds', 'passing_folds', 'failed_folds', 'unknown_folds')),
                **{key: _identifier(predictive.get('factor_signal_gate', {}).get(key))
                   for key in ('rule', 'status')}}},
        'portfolio_metrics': {'by_fold': portfolio_rows},
        'signal_to_execution': {'status': _identifier(diagnostics.get('signal_to_execution', {}).get('status')),
                                'by_fold': execution_rows},
        'robustness': {'cost_stress': pressure, 'ablation': [
            {'fold': _identifier(row.get('fold')), **_numbers(row, ('seed', 'excess_return_delta'))}
            for row in robustness.get('ablation', [])]},
        'factor_diagnostics': {'most_correlated_features': dict(correlations[:3]),
            'compared_features': len(correlations),
            'stability': [{'fold': _identifier(row.get('fold')), 'period': _identifier(row.get('period')),
                           **_numbers(row, PREDICTIVE_KEYS)} for row in factors.get('stability', [])],
            'decay': [{'fold': _identifier(row.get('fold')),
                       **_numbers(row, ('horizon', 'common_sample_rows', *PREDICTIVE_KEYS))}
                      for row in factors.get('decay', [])]}}


def _median(rows, key):
    values = [_number(row.get(key)) for row in rows]
    values = [value for value in values if value is not None]
    return statistics.median(values) if values else None


def feedback_summary(feedback: dict) -> dict:
    """Turn allowlisted development feedback into a stable, actionable card.

    This does not re-score a candidate.  It only projects facts that are
    already present in the immutable selection result.
    """
    rows = feedback.get("by_candidate", []) if isinstance(feedback, dict) else []
    risk_policy = feedback.get("risk_policy", {}) if isinstance(feedback, dict) else {}
    risk_key = "max_drawdown" if risk_policy.get("mode") == "max_drawdown" else None
    risk_limit = _number(risk_policy.get("limit"))
    summary_rows = []
    for row in rows:
        observations = row.get("observations", {})
        deltas = row.get("paired_deltas", [])
        candidate_drawdowns = [item.get("candidate", {}) for item in
                               observations.get("portfolio_metrics", {}).get("by_fold", [])]
        baseline_drawdowns = [item.get("baseline", {}) for item in
                              observations.get("portfolio_metrics", {}).get("by_fold", [])]
        reasons = _reasons(row.get("reasons", []))
        stress_rows = []
        for item in observations.get("robustness", {}).get("cost_stress", []):
            baseline, candidate = item.get("baseline", {}), item.get("candidate", {})
            baseline_return = _number(baseline.get("excess_return"))
            candidate_return = _number(candidate.get("excess_return"))
            baseline_cost = _number(baseline.get("total_execution_cost"))
            candidate_cost = _number(candidate.get("total_execution_cost"))
            stress_rows.append({"fold": _identifier(item.get("fold")),
                "seed": _number(item.get("seed")), "multiplier": _number(item.get("multiplier")),
                "baseline_excess_return": baseline_return, "candidate_excess_return": candidate_return,
                "excess_return_delta": (candidate_return - baseline_return
                                         if candidate_return is not None and baseline_return is not None else None),
                "baseline_execution_cost": baseline_cost, "candidate_execution_cost": candidate_cost,
                "execution_cost_delta": (candidate_cost - baseline_cost
                                          if candidate_cost is not None and baseline_cost is not None else None)})
        ablation = row.get("group_ablation", {})
        unknown_evidence = []
        if not observations:
            unknown_evidence.append("development_diagnostics_unavailable")
        if not stress_rows:
            unknown_evidence.append("cost_stress_evidence_unavailable")
        elif any(item[key] is None for item in stress_rows for key in (
                "baseline_excess_return", "candidate_excess_return", "baseline_execution_cost",
                "candidate_execution_cost")):
            unknown_evidence.append("cost_stress_metrics_incomplete")
        signal = observations.get("signal_to_execution", {})
        if signal.get("status") != "completed":
            unknown_evidence.append("signal_to_execution_evidence_partial")
        if ablation.get("status") not in {"accepted", "rejected", "not_applicable"}:
            unknown_evidence.append("group_ablation_evidence_unavailable")
        quality = observations.get("data_quality", {})
        if quality.get("time_check") not in {"passed", "completed"}:
            unknown_evidence.append("factor_quality_evidence_incomplete")
        risk_by_fold = []
        risk_rows = [("base", item) for item in observations.get("portfolio_metrics", {}).get("by_fold", [])]
        risk_rows.extend(("cost_stress:" + str(item.get("multiplier")), item)
                         for item in observations.get("robustness", {}).get("cost_stress", []))
        for scenario, item in risk_rows:
            baseline_value = _number(item.get("baseline", {}).get(risk_key)) if risk_key else None
            candidate_value = _number(item.get("candidate", {}).get(risk_key)) if risk_key else None
            risk_by_fold.append({"scenario": scenario, "fold": _identifier(item.get("fold")), "seed": _number(item.get("seed")),
                                 "baseline_value": baseline_value, "candidate_value": candidate_value,
                                 "baseline_breach": baseline_value > risk_limit if baseline_value is not None and risk_limit is not None else None,
                                 "candidate_breach": candidate_value > risk_limit if candidate_value is not None and risk_limit is not None else None,
                                 "incremental_change": candidate_value - baseline_value if baseline_value is not None and candidate_value is not None else None})
        inherited = any(item["baseline_breach"] is True for item in risk_by_fold)
        nonzero = [item.get("excess_return") for item in deltas if _number(item.get("excess_return")) is not None]
        if inherited:
            action = "inherited_baseline_risk_review"
        elif nonzero and all(value == 0 for value in nonzero):
            action = "prediction_to_execution_diagnosis_needed"
        elif row.get("status") == "failed":
            action = "repair_input_or_implementation"
        else:
            action = "test_a_distinct_falsifiable_mechanism"
        summary_rows.append({
            "factor_id": _identifier(row.get("factor_id")), "status": _identifier(row.get("status")),
            "decision_reasons": reasons, "median_excess_return_delta": _median(deltas, "excess_return"),
            "delta_count": len(nonzero), "candidate_max_drawdown": _median(candidate_drawdowns, "max_drawdown"),
            "baseline_max_drawdown": _median(baseline_drawdowns, "max_drawdown"),
            "coverage": _number(observations.get("data_quality", {}).get("coverage")),
            "time_check": _identifier(observations.get("data_quality", {}).get("time_check")),
            "paired_deltas": _deltas(deltas),
            "cost_stress": stress_rows,
            "group_ablation": {"status": _identifier(ablation.get("status")),
                               "group": _identifier(ablation.get("group")),
                               "reasons": _reasons(ablation.get("reasons", [])),
                               "paired_deltas": _deltas(row.get("group_paired_deltas", []))},
            "model_by_fold": observations.get("predictive_metrics", {}).get("model_by_fold", []),
            "signal_to_execution": observations.get("signal_to_execution", {}),
            "risk": {"mode": risk_policy.get("mode"), "limit": risk_limit, "by_fold": risk_by_fold},
            "unknown_evidence": sorted(set(unknown_evidence)),
            "next_action": action, "unknown": not bool(observations) or not bool(nonzero),
        })
    return {"schema_version": "feedback-summary-v3",
            "status": _identifier(feedback.get("status")) if isinstance(feedback, dict) else None,
            "by_candidate": summary_rows}


class ETFFeedback(Experiment2Feedback):
    def generate_feedback(self, exp, trace):
        result = exp.result
        if (not isinstance(result, dict) or result.get("stage") != "factor_selection" or
                result.get("protocol_id") != self.scen.session.protocol.protocol_id):
            raise QualityError("ETF feedback requires matching development evaluation")
        public = []
        for row in result["by_candidate"]:
            evaluation = row.get("evaluation", {})
            diagnostics = row.get('development_diagnostics', {})
            if diagnostics and (diagnostics.get('stage') != 'factor_selection' or
                    diagnostics.get('status') != 'completed' or diagnostics.get('protocol_id') != result['protocol_id'] or
                    diagnostics.get('baseline_id') != evaluation.get('baseline_id') or
                    diagnostics.get('candidate_id') != evaluation.get('candidate_id')):
                raise QualityError('Feedback diagnostics differ from the development evaluation')
            reasons = _reasons(evaluation.get('reasons', row.get('reasons', [])))
            group = row.get('group_ablation', {})
            statistics = []
            for item in row.get('time_block_statistics', []):
                interval = item.get('confidence_interval')
                if not isinstance(interval, (list, tuple)) or len(interval) != 2:
                    interval = None
                else:
                    interval = [_number(value) for value in interval]
                statistics.append({'fold': _identifier(item.get('fold')), 'seed': _number(item.get('seed')),
                    'status': _identifier(item.get('status')), 'reason': _identifier(item.get('reason')),
                    **_numbers(item, STATISTIC_KEYS), 'confidence_interval': interval})
            public.append({
                'factor_id': row['factor_id'], 'factor_version_id': _identifier(row.get('factor_version_id')),
                'status': row['status'], 'reasons': reasons,
                'baseline_id': _identifier(evaluation.get('baseline_id')),
                'candidate_id': _identifier(evaluation.get('candidate_id')),
                'run_id': _identifier(evaluation.get('run_id')),
                'direction': row.get('direction') if row.get('direction') in ('positive', 'negative', 'unknown') else None,
                'applicable_scope': _identifier(row.get('applicable_scope')),
                'paired_deltas': _deltas(evaluation.get('paired_deltas', [])),
                'group_ablation': {'status': _identifier(group.get('status')), 'group': _identifier(group.get('group')),
                                   'reasons': _reasons(group.get('reasons', []))},
                'group_paired_deltas': _deltas(group.get('paired_deltas', [])),
                'observations': compact_diagnostics(diagnostics), 'time_block_statistics': statistics,
                'decision': {'status': row['status'], 'reasons': reasons,
                             'kind': 'technical_failure' if row['status'] == 'failed' else 'development_selection',
                             'failure_stage': _identifier(row.get('failure_stage'))}})
        feedback = HypothesisFeedback(
            observations=canonical_json(public), hypothesis_evaluation=result["status"],
            new_hypothesis="Use compact development observations under the fixed learning/execution rules and versioned baseline",
            reason=canonical_json([r["reasons"] for r in public]),
            decision=any(r["status"] == "accepted" for r in public),
            acceptable=result["status"] in ("accepted", "rejected", "inconclusive"))
        feedback.structured = {"status": result["status"], "stage": "factor_selection",
                               "protocol_id": result["protocol_id"], "by_candidate": public,
                               "risk_policy": {"mode": self.scen.session.protocol.portfolio.risk_mode,
                                               "trigger_limit": self.scen.session.protocol.portfolio.risk,
                                               "limit": (self.scen.session.protocol.portfolio.max_drawdown_limit
                                                   if self.scen.session.protocol.portfolio.risk_mode == "max_drawdown"
                                                   else self.scen.session.protocol.portfolio.risk)}}
        feedback.structured["summary"] = feedback_summary(feedback.structured)
        return feedback
