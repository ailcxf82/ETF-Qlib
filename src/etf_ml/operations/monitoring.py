from __future__ import annotations

import numpy as np
import pandas as pd

from etf_ml.errors import QualityError


def feature_diagnostics(reference, current):
    if current.empty or list(current.columns) != list(reference.columns):
        raise QualityError('Monitoring feature schema or coverage is empty')
    baseline = reference.replace([np.inf, -np.inf], np.nan)
    current = current.replace([np.inf, -np.inf], np.nan)
    coverage = current.notna().mean()
    drift = {}
    for column in current:
        historical = baseline[column].dropna()
        observed = current[column].dropna()
        if historical.empty or observed.empty:
            drift[column] = {'status': 'insufficient_data', 'median_shift_over_iqr': None}
            continue
        median, iqr = float(historical.median()), float(historical.quantile(.75) - historical.quantile(.25))
        shift = float(observed.median()) - median
        drift[column] = {'status': 'constant_reference' if iqr == 0 else 'computed',
                         'median_shift_over_iqr': shift / iqr if iqr > 0 else None,
                         'median_shift': shift, 'reference_iqr': iqr}
    return {'row_count': len(current), 'column_count': len(current.columns),
            'coverage_by_column': {str(key): float(value) for key, value in coverage.items()},
            'minimum_column_coverage': float(coverage.min()),
            'complete_row_fraction': float(current.notna().all(axis=1).mean()),
            'drift': drift, 'note': 'Development-only reference; diagnostic, not a profitability test'}


def prediction_diagnostics(scores, previous=None):
    if scores.empty or scores.index.has_duplicates or not np.isfinite(scores).all():
        raise QualityError('Monitoring requires unique finite predictions')
    result = {'count': len(scores), 'mean': float(scores.mean()), 'standard_deviation': float(scores.std(ddof=0)),
              'rank_correlation': None, 'common_instruments': 0, 'stability_status': 'no_previous_signal'}
    if previous is not None:
        common = scores.index.intersection(previous.index)
        result['common_instruments'] = len(common)
        if len(common) >= 3 and scores.loc[common].nunique() > 1 and previous.loc[common].nunique() > 1:
            result['rank_correlation'] = float(scores.loc[common].corr(previous.loc[common], method='spearman'))
            result['stability_status'] = 'computed'
        else:
            result['stability_status'] = 'insufficient_cross_section'
    return result


def concentration(weights, groups):
    grouped, unknown = {}, 0.
    for instrument, weight in weights.items():
        if not np.isfinite(weight) or weight < 0:
            raise QualityError('Monitoring requires finite long position weights')
        group = groups.get(instrument)
        if pd.isna(group):
            group = '__unclassified__'
            unknown += weight
        grouped[group] = grouped.get(group, 0.) + weight
    if sum(weights.values()) > 1 + 1e-8:
        raise QualityError('Monitored weights exceed available equity')
    return {'maximum_single_weight': max(weights.values(), default=0.),
            'maximum_group_weight': max(grouped.values(), default=0.),
            'cash_weight': max(0., 1 - sum(weights.values())), 'unknown_group_weight': unknown,
            'holding_count': sum(weight > 0 for weight in weights.values())}
