import numpy as np
import pandas as pd
import pytest

from etf_ml.errors import QualityError
from etf_ml.research.statistics import paired_block_uncertainty


def returns():
    dates = pd.bdate_range("2023-01-02", periods=120)
    return pd.Series(np.sin(np.arange(120) / 7) * .001, index=dates)


def test_matched_blocks_preserve_zero_difference_and_are_reproducible():
    baseline = returns()
    zero = paired_block_uncertainty(baseline, baseline, block_length=10, repetitions=200)
    assert zero["observed_net_return_increment"] == 0
    assert zero["confidence_interval"] == [0., 0.]
    candidate = baseline + .0001
    first = paired_block_uncertainty(baseline, candidate, block_length=10, repetitions=200,
                                    attempted_trials=12)
    assert first == paired_block_uncertainty(baseline, candidate, block_length=10,
                                            repetitions=200, attempted_trials=12)
    assert first["confidence_interval"][0] > 0
    assert first["attempted_trials"] == 12
    assert "selection bias" in first["note"]


def test_insufficient_temporal_information_is_explicit():
    baseline = returns().iloc[:15]
    result = paired_block_uncertainty(baseline, baseline + .001, block_length=10, repetitions=100)
    assert result["status"] == "inconclusive" and result["confidence_interval"] is None


@pytest.mark.parametrize("problem", ["dates", "nan", "duplicate", "loss", "order"])
def test_statistics_do_not_shrink_mismatched_or_invalid_inputs(problem):
    baseline, candidate = returns(), returns()
    if problem == "dates":
        candidate = candidate.iloc[1:]
    elif problem == "nan":
        candidate.iloc[0] = float("nan")
    elif problem == "duplicate":
        baseline.index = pd.DatetimeIndex([baseline.index[0], *baseline.index[:-1]])
        candidate.index = baseline.index
    elif problem == "loss":
        candidate.iloc[0] = -1
    elif problem == "order":
        baseline, candidate = baseline.iloc[::-1], candidate.iloc[::-1]
    with pytest.raises(QualityError):
        paired_block_uncertainty(baseline, candidate)
