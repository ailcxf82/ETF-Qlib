import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from etf_ml.contracts import LabelSpec, SegmentSpec
from etf_ml.datasets.labels import generate_labels
from etf_ml.datasets.processors import InputProcessor
from etf_ml.datasets.splits import learning_mask
from etf_ml.features.baseline import baseline_features
from etf_ml.features.validators import check_causality, check_grouping, validate_factor
from etf_ml.errors import QualityError

def test_T05_baseline_is_causal(panel, calendar):
    check_causality(baseline_features, panel, calendar[140])

def test_T05_future_shift_rejected(panel, calendar):
    def future(data):
        return data.groupby(level="instrument")["adj_close"].shift(-1).to_frame()
    with pytest.raises(QualityError, match="future"):
        check_causality(future, panel, calendar[80])

def test_T06_baseline_instrument_independence(panel):
    check_grouping(baseline_features, panel)

def test_T06_cross_instrument_rolling_rejected(panel):
    def contaminated(data):
        return data.adj_close.rolling(4).mean().to_frame()
    with pytest.raises(QualityError):
        check_grouping(contaminated, panel)

def test_label_is_open_to_open_exact_calendar_horizon(panel, calendar):
    labels, events = generate_labels(panel, calendar, LabelSpec(horizon=5))
    key = (calendar[40], "510300.SH")
    expected = panel.loc[(calendar[46], key[1]), "adj_open"] / panel.loc[(calendar[41], key[1]), "adj_open"] - 1
    assert labels.loc[key] == pytest.approx(expected)
    assert events.loc[key, "entry_time"] == calendar[41]
    assert events.loc[key, "end_time"] == calendar[46]

def test_missing_quote_does_not_compress_label_time(panel, calendar):
    panel = panel.drop((calendar[41], "510300.SH"))
    labels, _ = generate_labels(panel, calendar, LabelSpec())
    assert pd.isna(labels.loc[(calendar[40], "510300.SH")])

def test_T07_actual_event_purge(panel, calendar):
    labels, events = generate_labels(panel, calendar, LabelSpec(availability_delay_days=2))
    segment = SegmentSpec(start=str(calendar[0].date()), end=str(calendar[79].date()))
    mask = learning_mask(events, labels, segment, next_start=calendar[80])
    assert mask.loc[(calendar[71], "510300.SH")]
    assert not mask.loc[(calendar[72], "510300.SH")]

def test_T08_immature_labels_excluded_at_retrain(panel, calendar):
    labels, events = generate_labels(panel, calendar, LabelSpec())
    segment = SegmentSpec(start=str(calendar[0].date()), end=str(calendar[100].date()))
    mask = learning_mask(events, labels, segment, as_of=calendar[70])
    assert mask.loc[(calendar[64], "510300.SH")]
    assert not mask.loc[(calendar[65], "510300.SH")]

def test_T09_latest_feature_survives_missing_future_label(panel, calendar):
    labels, _ = generate_labels(panel, calendar, LabelSpec())
    features = baseline_features(panel)
    assert labels.loc[(calendar[-1], "510300.SH")] != labels.loc[(calendar[-1], "510300.SH")]
    assert np.isfinite(features.loc[(calendar[-1], "510300.SH")]).all()

@pytest.mark.parametrize("mode", ["nan", "constant", "inf", "index"])
def test_T10_invalid_factor_quality_rejected(panel, mode):
    factor = panel.adj_close.to_frame("candidate")
    if mode == "nan":
        factor.iloc[:] = np.nan
    elif mode == "constant":
        factor.iloc[:] = 1
    elif mode == "inf":
        factor.iloc[0, 0] = np.inf
    else:
        factor = factor.iloc[:-1]
    with pytest.raises(QualityError):
        validate_factor(factor, panel.index)

def test_processor_train_fit_and_feature_reordering(panel, calendar):
    features = baseline_features(panel)
    train = features[features.index.get_level_values("datetime") <= calendar[80]]
    processor = InputProcessor("ridge").fit(train)
    mean = processor.scaler.mean_.copy()
    changed = features.copy()
    changed.loc[changed.index.get_level_values("datetime") > calendar[80]] = 1e10
    processor.transform(changed)
    assert np.array_equal(mean, processor.scaler.mean_)
    assert_frame_equal(processor.transform(features),
                       processor.transform(features[features.columns[::-1]]))
    with pytest.raises(QualityError):
        processor.transform(features.iloc[:, :-1])
