"""Development-only observations from existing paired experiments.

These diagnostics never change selection gates or read the holdout view.
Full date-level results stay in artifacts; prompts receive compact summaries.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from etf_ml.backtest.metrics import predictive_metrics
from etf_ml.datasets import generate_labels
from etf_ml.datasets.splits import learning_mask, validate_fold
from etf_ml.data.calendar import require_calendar
from etf_ml.data.source import require_panel
from etf_ml.errors import QualityError
from etf_ml.features.validators import redundancy
from etf_ml.utils import content_hash

PREDICTIVE_KEYS = ('ic', 'rank_ic', 'effective_dates')
PORTFOLIO_KEYS = ('net_return', 'excess_return', 'max_drawdown', 'annualized_volatility',
                  'turnover', 'commission', 'slippage_cost', 'total_execution_cost',
                  'effective_dates', 'max_single_weight', 'max_group_weight')


def _summary(metrics):
    return {key: metrics.get(key) for key in PREDICTIVE_KEYS}


def _metrics(metrics):
    return {key: metrics.get(key) for key in PORTFOLIO_KEYS}


def development_diagnostics(panel, calendar, universe, baseline, factor, protocol, reports):
    require_panel(panel)
    require_panel(universe)
    require_panel(baseline.frame, numeric=True)
    require_panel(factor.frame, numeric=True)
    require_calendar(calendar)
    if (panel.empty or (panel.index.get_level_values('datetime') >= pd.Timestamp(protocol.validation.holdout_start)).any()
            or (calendar >= pd.Timestamp(protocol.validation.holdout_start)).any()):
        raise QualityError('Development diagnostics cannot include holdout dates')
    if not all(frame.index.equals(panel.index) for frame in (universe, baseline.frame, factor.frame)):
        raise QualityError('Development diagnostics require the fixed research index')
    if not pd.api.types.is_bool_dtype(universe.eligible) or universe.eligible.isna().any():
        raise QualityError('Development diagnostics require complete boolean eligibility')
    if factor.frame.shape[1] != 1 or np.isinf(factor.frame.to_numpy(dtype=float)).any():
        raise QualityError('Development diagnostics require one finite-or-missing factor')
    labels, events = generate_labels(panel, calendar, protocol.label)
    horizons = sorted({1, protocol.label.horizon, 2 * protocol.label.horizon})
    horizon_labels = {h: generate_labels(panel, calendar, protocol.label.model_copy(update={'horizon': h}))
                      for h in horizons}
    factor_rows, quality_rows, decay, stability = [], [], [], []
    selection_mask = pd.Series(False, index=panel.index)
    dates = panel.index.get_level_values('datetime')
    for fold in protocol.validation.folds:
        validate_fold(fold, protocol.validation.holdout_start)
        mask = learning_mask(events, labels, fold.selection,
                             next_start=protocol.validation.holdout_start) & universe.eligible
        index_hash = content_hash([[str(t), i] for t, i in panel.index[mask]])
        for kind in ('baseline', 'candidate'):
            matched = [row for row in reports[kind]['by_fold'] if row['fold'] == fold.name]
            if (not matched or any(row['dataset'].get('evaluation_index_hash') != index_hash for row in matched)):
                raise QualityError('Diagnostic sample differs from the evaluated model sample')
        values = factor.frame.iloc[:, 0]
        metrics = predictive_metrics(values[mask], labels[mask])
        factor_rows.append({'fold': fold.name, **metrics, 'evaluation_index_hash': index_hash})
        target = pd.Series((dates >= pd.Timestamp(fold.selection.start)) &
                           (dates <= pd.Timestamp(fold.selection.end)), index=panel.index) & universe.eligible
        minimum = factor.manifest.get('minimum_observations', 1)
        target &= panel.groupby(level='instrument').cumcount() + 1 >= minimum
        selection_mask |= target
        valid = values[target].notna()
        coverage = valid.groupby(level='datetime').mean()
        group_coverage = valid.groupby(universe.tracking_group[target].fillna('__unclassified__')).mean()
        quality_rows.append({'fold': fold.name, 'target_rows': int(target.sum()),
            'coverage': float(coverage.mean()) if len(coverage) else None,
            'worst_date_coverage': float(coverage.min()) if len(coverage) else None,
            'by_group': {str(k): float(v) for k, v in group_coverage.items()}})
        valid_dates = [row for row in metrics['by_date'] if row['valid']]
        midpoint = len(valid_dates) // 2
        for name, rows in [('first_half', valid_dates[:midpoint]), ('second_half', valid_dates[midpoint:])]:
            stability.append({'fold': fold.name, 'period': name, 'effective_dates': len(rows),
                'ic': float(np.mean([r['ic'] for r in rows])) if rows else None,
                'rank_ic': float(np.mean([r['rank_ic'] for r in rows])) if rows else None})
        # Compare fixed horizons on their common mature sample, rather than
        # making longer horizons look different through changed eligibility.
        masks = {h: learning_mask(e, y, fold.selection, next_start=protocol.validation.holdout_start)
                    & universe.eligible for h, (y, e) in horizon_labels.items()}
        common = pd.Series(True, index=panel.index)
        for hmask in masks.values():
            common &= hmask
        for horizon, (target_labels, _) in horizon_labels.items():
            decay.append({'fold': fold.name, 'horizon': horizon,
                'common_sample_rows': int(common.sum()),
                **predictive_metrics(values[common], target_labels[common])})
    paired = []
    model_rows = []
    pressure = []
    ablation = []
    removed = {(r['fold'], r['seed']): r for r in reports.get('ablation', {}).get('by_fold', [])}
    left = {(r['fold'], r['seed']): r for r in reports['baseline']['by_fold']}
    for row in reports['candidate']['by_fold']:
        key = (row['fold'], row['seed'])
        reference = left[key]
        model_rows.append({'fold': key[0], 'seed': key[1],
            'baseline': _summary(reference.get('predictive', {})),
            'candidate': _summary(row.get('predictive', {}))})
        paired.append({'fold': key[0], 'seed': key[1],
                       'baseline': _metrics(reference['portfolio']), 'candidate': _metrics(row['portfolio'])})
        if key in removed:
            ablation.append({'fold': key[0], 'seed': key[1],
                'excess_return_delta': row['portfolio']['excess_return'] - removed[key]['portfolio']['excess_return']})
        for multiplier in protocol.cost_multipliers:
            name = str(multiplier)
            b, c = reference['cost_stress'][name], row['cost_stress'][name]
            pressure.append({'fold': key[0], 'seed': key[1], 'multiplier': multiplier,
                             'baseline': _metrics(b), 'candidate': _metrics(c),
                             'excess_return_delta': c['excess_return'] - b['excess_return']})
    return {'status': 'completed', 'stage': 'factor_selection', 'protocol_id': protocol.protocol_id,
        'baseline_id': baseline.feature_set_id, 'candidate_id': reports['candidate']['feature_set_id'],
        'data_quality': {**{k: factor.manifest.get('quality', {}).get(k) for k in
                           ('coverage', 'worst_date_coverage', 'nonfinite', 'duplicate_keys')},
                         'time_check': factor.manifest.get('checks', {}).get('status', 'unavailable'),
                         'index_check': 'passed', 'by_fold': quality_rows},
        'predictive_metrics': {'factor_by_fold': factor_rows, 'model_by_fold': model_rows},
        'portfolio_metrics': {'by_fold': paired},
        'robustness': {'cost_stress': pressure, 'ablation': ablation},
        'factor_diagnostics': {'redundancy': redundancy(factor.frame.iloc[:, 0][selection_mask],
                                                      baseline.frame[selection_mask]),
                               'stability': stability, 'decay': decay},
        'note': 'Fixed-horizon development diagnostics; IC does not override portfolio selection gates'}
