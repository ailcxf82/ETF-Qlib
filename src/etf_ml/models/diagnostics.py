"""Training evidence; descriptive diagnostics never change model selection."""
from __future__ import annotations

import json
import math

from etf_ml.errors import QualityError


def native_parameters(booster) -> dict:
    """Read resolved defaults and aliases from LightGBM's own model export."""
    exported = booster.model_to_string(num_iteration=1)
    start, end = "parameters:\n", "end of parameters"
    if start not in exported or end not in exported:
        raise QualityError("LightGBM export lacks resolved parameters")
    section = exported.split(start, 1)[1].split(end, 1)[0]
    result = {}
    for line in section.splitlines():
        if line.startswith("[") and line.endswith("]") and ": " in line:
            key, value = line[1:-1].split(": ", 1)
            try:
                result[key] = json.loads(value)
            except ValueError:
                result[key] = value
    if not {"objective", "learning_rate", "feature_fraction", "bagging_freq"} <= result.keys():
        raise QualityError("LightGBM resolved parameter snapshot is incomplete")
    return result


def training_metadata(model, history: dict, fit_parameters: dict) -> dict:
    booster = model.model
    curves, lengths, issues = {}, [], []
    for segment, metrics in history.items():
        curves[segment] = {}
        for metric, values in metrics.items():
            series = [float(v) if math.isfinite(float(v)) else None for v in values]
            curves[segment][metric] = series
            lengths.append(len(series))
            if not series or None in series:
                issues.append("empty_or_nonfinite_curve")
    evaluated = max(lengths) if lengths else None
    if not lengths or len(set(lengths)) != 1 or not {"train", "valid"} <= curves.keys():
        issues.append("incomplete_evaluation_history")
    parameters = native_parameters(booster)
    cap = int(parameters["num_iterations"])
    patience = fit_parameters.get("early_stopping_rounds")
    if patience is None:
        patience = model.early_stopping_rounds
    best, retained = int(booster.best_iteration), int(booster.current_iteration())
    if evaluated is None or issues:
        stopping = "unknown"
    elif patience > 0 and best > 0 and evaluated - best >= patience:
        stopping = "early_stopping"
    elif evaluated >= cap:
        stopping = "iteration_cap"
    else:
        stopping = "stopped_before_cap"
    flags = []
    if best == 1:
        flags.append("best_at_first_round")
    if evaluated == cap:
        flags.append("iteration_cap_reached")
    train = curves.get("train", {}).get("l2", [])
    valid = curves.get("valid", {}).get("l2", [])
    if train and valid and all(v is not None for v in (train[0], train[-1], valid[0], valid[-1])):
        if train[-1] < train[0] and valid[-1] > valid[0]:
            flags.append("train_improves_valid_worsens")
    return {
        # Retain the existing field's meaning for older consumers.
        "trained_rounds": retained, "retained_rounds": retained,
        "best_iteration": best, "iteration_base": 1,
        "evaluated_rounds": evaluated, "iteration_cap": cap,
        "early_stopping_rounds": patience, "stop_reason": stopping,
        "effective_parameters": parameters,
        "effective_parameters_source": "lightgbm_native_model_export",
        "evaluation_history": curves,
        "diagnostics": {"status": "partial" if issues else "complete",
                        "issues": sorted(set(issues)), "flags": flags,
                        "interpretation": "Descriptive only; not proof of overfitting or acceptance"},
    }
