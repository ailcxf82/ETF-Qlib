from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from etf_ml.artifacts import environment_manifest
from etf_ml.contracts import ModelSpec
from etf_ml.models.registry import create_model
from etf_ml.utils import content_hash, code_hash


@dataclass
class ModelBundle:
    model: Any
    processor: Any
    manifest: dict


def fit(prepared, model_spec: ModelSpec, *, feature_set_id: str,
        snapshot_id: str, horizon=5, universe_policy=None, run_id=None) -> ModelBundle:
    from etf_ml.models.recorders import scoped_recorder
    from etf_ml.artifacts import RUN_ID
    from etf_ml.errors import ConfigurationError
    model = create_model(model_spec)
    run_id = run_id or content_hash({"dataset": prepared.manifest,
                                     "model": model_spec, "snapshot": snapshot_id})[:24]
    if not RUN_ID.fullmatch(run_id):
        raise ConfigurationError("Invalid model run_id")
    # A scoped recorder prevents model logging from leaking to another experiment.
    with scoped_recorder(run_id) as recorder:
        from copy import deepcopy
        model.fit(prepared.dataset, **deepcopy(model_spec.fit))
        recorder_id = recorder.id
    learner = getattr(model, "model", None)
    training = {}
    if model_spec.name == "lightgbm":
        training = {"trained_rounds": learner.current_iteration(),
                    "best_iteration": learner.best_iteration, "iteration_base": 1}
    elif model_spec.name == "xgboost":
        best = learner.attr("best_iteration")
        training = {"trained_rounds": learner.num_boosted_rounds(),
                    "best_iteration": int(best) if best is not None else None,
                    "iteration_base": 0,
                    "prediction_iteration_range": list(model.prediction_iteration_range_)}
    manifest = {
        "schema_version": 1, "training": training, "model_spec": model_spec.model_dump(),
        "feature_set_id": feature_set_id, "snapshot_id": snapshot_id,
        "feature_names": list(prepared.features.columns),
        "horizon": horizon, "score_type": "ranking_score",
        "training_cutoff": prepared.manifest["fold"]["train"]["end"],
        "label_maturity_cutoff": prepared.manifest["fold"]["early_stop"]["start"],
        "dataset_manifest": prepared.manifest, "universe_policy": universe_policy,
        "environment": environment_manifest(), "code_version": "0.1.0", "code_hash": code_hash(),
        "recorder_id": recorder_id, "run_id": run_id,
    }
    manifest["model_id"] = content_hash(manifest)
    return ModelBundle(model, prepared.processor, manifest)
