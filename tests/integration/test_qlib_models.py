import numpy as np
import pandas as pd
import pytest

from etf_ml.contracts import LabelSpec, ModelSpec
from etf_ml.datasets.builder import build
from etf_ml.datasets.labels import generate_labels
from etf_ml.features.baseline import materialize
from etf_ml.models import fit, predict, save_bundle, load_bundle
from etf_ml.errors import ConfigurationError

pytestmark = pytest.mark.qlib

@pytest.fixture(autouse=True)
def isolated_recorder(tmp_path, monkeypatch):
    import qlib
    from qlib.constant import REG_CN
    monkeypatch.setenv("MLFLOW_TRACKING_URI", str(tmp_path / "mlruns"))
    qlib.init(region=REG_CN, exp_manager={
        "class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
        "kwargs": {"uri": (tmp_path / "mlruns").as_uri(), "default_exp_name": "tests"}})

@pytest.mark.parametrize("name", ["ridge", "lightgbm", "xgboost"])
def test_G1_qlib_training_latest_inference_persistence(panel, calendar, fold, tmp_path, name):
    feature_set = materialize({"snapshot_id": "fixture"}, panel)
    labels, events = generate_labels(panel, calendar, LabelSpec())
    prepared = build(feature_set, labels, events, fold, model_kind=name)
    constructor = {"num_boost_round": 10, "early_stopping_rounds": 3,
                   "min_data_in_leaf": 5} if name == "lightgbm" else {}
    fit_params = {"verbose_eval": 0} if name == "lightgbm" else {}
    if name == "xgboost":
        constructor = {"max_depth": 3, "eta": .1, "nthread": 1}
        fit_params = {"num_boost_round": 10, "early_stopping_rounds": 3, "verbose_eval": False}
    bundle = fit(prepared, ModelSpec(name=name, constructor=constructor, fit=fit_params),
                 feature_set_id=feature_set.feature_set_id, snapshot_id="fixture")
    latest = feature_set.frame.loc[(slice(calendar[-1], calendar[-1]), slice(None)), :]
    first = predict(bundle, latest)
    assert len(first) == 3 and np.isfinite(first).all()
    path = save_bundle(bundle, tmp_path / "models")
    restored = load_bundle(path, trusted_root=tmp_path / "models")
    second = predict(restored, latest[latest.columns[::-1]])
    np.testing.assert_allclose(first, second, atol=1e-8, rtol=1e-8)
    assert first.index.equals(latest.index)
    if name == "xgboost":
        assert bundle.manifest["environment"]["packages"]["xgboost"] == "3.2.0"
        assert bundle.manifest["training"]["prediction_iteration_range"][1] == (
            bundle.manifest["training"]["best_iteration"] + 1)
        assert bundle.manifest["training"]["trained_rounds"] >= (
            bundle.manifest["training"]["prediction_iteration_range"][1])
    train = prepared.dataset.prepare("train", data_key="learn")
    valid = prepared.dataset.prepare("valid", data_key="learn")
    assert train.index.max()[0] < pd.Timestamp(fold.early_stop.start)
    assert valid.index.max()[0] < pd.Timestamp(fold.selection.start)

def test_development_handler_rejects_holdout_rows(panel, calendar, fold):
    features = materialize({}, panel)
    labels, events = generate_labels(panel, calendar, LabelSpec())
    with pytest.raises(ConfigurationError, match="holdout"):
        build(features, labels, events, fold, holdout_start=str(calendar[170].date()))


def test_lgbm_diagnostics_capture_discarded_rounds_and_preserve_predictions(panel, calendar, fold):
    from etf_ml.contracts import FeatureArtifact
    from etf_ml.models.recorders import scoped_recorder
    from etf_ml.models.registry import create_model

    frame = panel[["adj_close"]].rename(columns={"adj_close": "signal"})
    features = FeatureArtifact("lgb-early-stop", frame, {"snapshot_id": "fixture"})
    labels, events = generate_labels(panel, calendar, LabelSpec())
    labels = panel.adj_close.rename(labels.name) / 100
    labels.loc[labels.index.get_level_values("datetime") >= pd.Timestamp(fold.early_stop.start)] *= -1
    prepared = build(features, labels, events, fold)
    spec = ModelSpec(constructor={"num_boost_round": 30, "early_stopping_rounds": 3,
        "min_data_in_leaf": 5, "learning_rate": .5, "num_threads": 1},
        fit={"verbose_eval": 0, "evals_result": {"old": {"l2": [99.]}}})
    bundle = fit(prepared, spec, feature_set_id=features.feature_set_id, snapshot_id="fixture")
    evidence = bundle.manifest["training"]
    assert evidence["evaluated_rounds"] > evidence["retained_rounds"]
    assert evidence["evaluated_rounds"] == evidence["best_iteration"] + 3
    assert evidence["stop_reason"] == "early_stopping"
    assert evidence["diagnostics"]["status"] == "complete"
    assert set(evidence["evaluation_history"]) == {"train", "valid"}
    assert spec.fit["evals_result"] == {"old": {"l2": [99.]}}
    assert evidence["effective_parameters"]["feature_fraction"] == 1
    assert evidence["effective_parameters"]["bagging_freq"] == 0
    # Observe the same model without the new evidence capture: predictions must agree.
    reference = create_model(spec)
    with scoped_recorder("plain-lgbm-reference"):
        reference.fit(prepared.dataset, verbose_eval=0)
    np.testing.assert_allclose(bundle.model.predict(prepared.dataset), reference.predict(prepared.dataset),
                               atol=1e-12, rtol=1e-12)


def test_lgbm_native_snapshot_resolves_aliases_and_fit_budget(panel, calendar, fold):
    features = materialize({"snapshot_id": "fixture"}, panel)
    labels, events = generate_labels(panel, calendar, LabelSpec())
    prepared = build(features, labels, events, fold)
    spec = ModelSpec(constructor={"num_boost_round": 20, "early_stopping_rounds": 10,
        "min_data_in_leaf": 5, "num_threads": 1, "colsample_bytree": .8,
        "subsample": .75, "subsample_freq": 1},
        fit={"num_boost_round": 4, "early_stopping_rounds": 8, "verbose_eval": 0})
    bundle = fit(prepared, spec, feature_set_id=features.feature_set_id, snapshot_id="fixture")
    evidence = bundle.manifest["training"]
    assert evidence["effective_parameters"]["feature_fraction"] == .8
    assert evidence["effective_parameters"]["bagging_fraction"] == .75
    assert evidence["effective_parameters"]["bagging_freq"] == 1
    assert evidence["iteration_cap"] == evidence["evaluated_rounds"] == 4
    assert evidence["early_stopping_rounds"] == 8
    assert evidence["stop_reason"] == "iteration_cap"


def test_xgboost_early_stop_prediction_excludes_later_trees(panel, calendar, fold):
    import xgboost as xgb
    from etf_ml.contracts import FeatureArtifact

    frame = panel[["adj_close"]].rename(columns={"adj_close": "signal"})
    features = FeatureArtifact("controlled-early-stop", frame, {"snapshot_id": "fixture"})
    labels, events = generate_labels(panel, calendar, LabelSpec())
    labels = panel.adj_close.rename(labels.name) / 100
    labels.loc[labels.index.get_level_values("datetime") >= pd.Timestamp(fold.early_stop.start)] *= -1
    prepared = build(features, labels, events, fold, model_kind="xgboost")
    spec = ModelSpec(name="xgboost", constructor={"max_depth": 2, "eta": .5, "nthread": 1},
                     fit={"num_boost_round": 30, "early_stopping_rounds": 3, "verbose_eval": False,
                          "evals_result": {}})
    bundle = fit(prepared, spec, feature_set_id=features.feature_set_id, snapshot_id="fixture",
                 run_id="controlled-xgb")
    assert spec.fit["evals_result"] == {}
    booster = bundle.model.model
    end = bundle.model.prediction_iteration_range_[1]
    assert end < booster.num_boosted_rounds()
    latest = frame.loc[(slice(calendar[-1], calendar[-1]), slice(None)), :]
    scores = predict(bundle, latest)
    matrix = xgb.DMatrix(latest.to_numpy())
    expected = booster.predict(matrix, iteration_range=(0, end))
    all_trees = booster.predict(matrix)
    np.testing.assert_allclose(scores, expected, atol=1e-8, rtol=1e-8)
    assert not np.allclose(scores, all_trees, atol=1e-8, rtol=1e-8)
    # Independent fits do not share Qlib's mutable evals_result default.
    second = fit(prepared, spec, feature_set_id=features.feature_set_id, snapshot_id="fixture",
                 run_id="controlled-xgb-second")
    assert spec.fit["evals_result"] == {}
    assert second.model.evals_result_ is not bundle.model.evals_result_
    np.testing.assert_allclose(predict(second, latest), scores, atol=1e-8, rtol=1e-8)
