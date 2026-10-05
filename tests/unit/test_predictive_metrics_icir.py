import numpy as np
import pandas as pd
import pytest

from etf_ml.backtest.metrics import (positive_icir_gate, predictive_metrics,
                                     select_factor_orientation, summarize_icir_gates)


def _panel(target_rows):
    dates = pd.date_range('2025-01-01', periods=len(target_rows), freq='D')
    instruments = ['A', 'B', 'C', 'D']
    index = pd.MultiIndex.from_product([dates, instruments], names=['datetime', 'instrument'])
    scores = pd.Series(np.tile([0., 1., 2., 3.], len(dates)), index=index)
    labels = pd.Series(np.asarray(target_rows, dtype=float).reshape(-1), index=index)
    return scores, labels


def test_predictive_metrics_estimates_daily_sample_icir_and_reverse_direction():
    scores, labels = _panel([[3., 2., 1., 0.], [2., 3., 0., 1.]])
    result = predictive_metrics(scores, labels)
    daily = np.asarray([row['ic'] for row in result['by_date']])
    assert result['ic'] == pytest.approx(daily.mean())
    assert result['ic_std'] == pytest.approx(daily.std(ddof=1))
    assert result['icir'] == pytest.approx(daily.mean() / daily.std(ddof=1))
    assert result['ic_direction'] == 'reverse'
    assert result['rank_ic_direction'] == 'reverse'
    assert result['coverage'] == 1.


def test_negative_training_signal_is_inverted_before_validation_gate():
    scores, labels = _panel([[3., 2., 1., 0.], [2., 3., 0., 1.]])
    training = predictive_metrics(scores, labels)
    orientation = select_factor_orientation(training)
    raw_validation = predictive_metrics(scores, labels)
    oriented_validation = predictive_metrics(scores * orientation['sign'], labels)

    assert orientation['direction'] == 'reverse' and orientation['sign'] == -1
    assert positive_icir_gate(raw_validation)['status'] == 'failed'
    assert positive_icir_gate(oriented_validation)['status'] == 'passed'


@pytest.mark.parametrize(('ic', 'icir', 'status', 'expected', 'sign'), [
    (-.2, -.4, 'available', 'reverse', -1),
    (.2, .4, 'available', 'positive', 1),
    (-.2, .4, 'available', 'unknown', None),
    (-.2, -.4, 'zero_daily_standard_deviation', 'unknown', None),
    (0., -.4, 'available', 'unknown', None),
    (None, -.4, 'available', 'unknown', None),
    (-.2, np.inf, 'available', 'unknown', None),
])
def test_training_orientation_requires_matching_finite_nonzero_ic_and_icir(
        ic, icir, status, expected, sign):
    selected = select_factor_orientation({'ic': ic, 'icir': icir, 'icir_status': status})
    assert selected['direction'] == expected
    assert selected['sign'] == sign


@pytest.mark.parametrize(('ic', 'icir', 'status', 'expected'), [
    (.2, .4, 'available', 'passed'),
    (-.2, .4, 'available', 'failed'),
    (.2, -.4, 'available', 'failed'),
    (0., .4, 'available', 'unknown'),
    (.2, None, 'fewer_than_two_valid_dates', 'unknown'),
    (True, .4, 'available', 'unknown'),
])
def test_validation_gate_is_strict_and_preserves_unknown(ic, icir, status, expected):
    assert positive_icir_gate({'ic': ic, 'icir': icir, 'icir_status': status})['status'] == expected


@pytest.mark.parametrize('statuses,expected', [
    (['passed'] * 3 + ['unknown'] * 2, 'passed'),
    (['passed'] * 2 + ['unknown'] * 3, 'unknown'),
    (['passed'] * 2 + ['failed'] * 3, 'failed'),
    (['passed'] + ['failed'] * 3 + ['unknown'], 'failed'),
])
def test_unknown_folds_do_not_reduce_five_fold_vote_denominator(statuses, expected):
    result = summarize_icir_gates([{'status': status} for status in statuses])
    assert result['fold_count'] == 5 and result['required_pass_folds'] == 3
    assert result['passing_folds'] == statuses.count('passed')
    assert result['status'] == expected
