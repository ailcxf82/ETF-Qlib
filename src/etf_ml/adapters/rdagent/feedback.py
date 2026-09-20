from __future__ import annotations

import math
import re

from rdagent.core.proposal import Experiment2Feedback, HypothesisFeedback

from etf_ml.errors import QualityError
from etf_ml.research.diagnostics import PREDICTIVE_KEYS, PORTFOLIO_KEYS
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
    model_rows = [{'fold': _identifier(row.get('fold')), 'seed': _number(row.get('seed')),
                   **{kind: _numbers(row.get(kind, {}), PREDICTIVE_KEYS) for kind in ('baseline', 'candidate')}}
                  for row in predictive.get('model_by_fold', [])]
    portfolio_rows = [{'fold': _identifier(row.get('fold')), 'seed': _number(row.get('seed')),
                       **{kind: _numbers(row.get(kind, {}), PORTFOLIO_KEYS) for kind in ('baseline', 'candidate')}}
                      for row in diagnostics.get('portfolio_metrics', {}).get('by_fold', [])]
    pressure = [{'fold': _identifier(row.get('fold')), **_numbers(row, ('seed', 'multiplier', 'excess_return_delta')),
                 **{kind: _numbers(row.get(kind, {}), ('excess_return', 'max_drawdown', 'total_execution_cost'))
                    for kind in ('baseline', 'candidate')}} for row in robustness.get('cost_stress', [])]
    correlations = sorted(((name, _number(value)) for name, value in factors.get('redundancy', {}).items()
                           if _identifier(name)), key=lambda item: (item[1] is None, -(item[1] or 0), item[0]))
    return {'status': 'completed',
        'data_quality': {**_numbers(quality, ('coverage', 'worst_date_coverage', 'nonfinite', 'duplicate_keys')),
                         'time_check': _identifier(quality.get('time_check')),
                         'index_check': _identifier(quality.get('index_check')), 'by_fold': quality_rows},
        'predictive_metrics': {'factor_by_fold': [
            {'fold': _identifier(row.get('fold')), **_numbers(row, PREDICTIVE_KEYS)}
            for row in predictive.get('factor_by_fold', [])], 'model_by_fold': model_rows},
        'portfolio_metrics': {'by_fold': portfolio_rows},
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
                               "protocol_id": result["protocol_id"], "by_candidate": public}
        return feedback
