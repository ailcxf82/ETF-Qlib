"""Synthetic governance evidence; these results assert no investment advantage."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from pydantic import ValidationError

from etf_ml.contracts import AcceptancePolicy, ModelSpec, DataSpec
from etf_ml.errors import ConfigurationError, QualityError
from etf_ml.research import finalize
from etf_ml.utils import atomic_json
from test_feature_sets import setup, advance, report


@pytest.fixture
def final_session(setup, tmp_path):
    base, protocol, registry, store, factor = setup
    spec, artifact = factor(name="trend", group="trend")
    selected = store.publish(base, artifact, spec, protocol)
    active = advance(protocol, selected)
    spec, artifact = factor(selected, active, name="range", group="range")
    selected = store.publish(selected, artifact, spec, active)
    protocol = advance(protocol, selected)
    config = SimpleNamespace(
        data=DataSpec(),
        models=[ModelSpec(name="ridge"), protocol.model, ModelSpec(name="xgboost")],
        research=protocol.research, artifact_root=tmp_path / "outputs",
        acceptance=AcceptancePolicy(minimum_net_return=0, minimum_excess_return=0,
            maximum_drawdown=.12, maximum_annualized_volatility=.2,
            maximum_execution_cost_over_initial_equity=.1, minimum_effective_dates=10))
    snapshot_path = tmp_path / "snapshot"
    snapshot_path.mkdir()
    atomic_json(snapshot_path / "snapshot_manifest.json", {"snapshot_id": "snapshot"})
    root = tmp_path / "research"
    atomic_json(root / "sessions" / "trial-run" / "trial-1.json", {"status": "rejected", "factor_id": "failed_trial"})
    return SimpleNamespace(config=config, protocol=protocol, baseline=selected,
                           snapshot=SimpleNamespace(path=snapshot_path, snapshot_id="snapshot", manifest={"spec":{"mode":"formal"}}),
                           registry=registry, root=root), base


def synthetic_runner(session, monkeypatch, *, bad_group=None, mismatch_group=None, risk=.01):
    calls = []
    def run(config, snapshot_path, features, protocol_id, output, *, purpose, models, cost_multipliers):
        calls.append((purpose, list(features.frame.columns), list(cost_multipliers)))
        result = report(session.protocol, .04 if purpose == "final-full" else .02)
        result.update(feature_set_id=features.feature_set_id, child_runs=[])
        if purpose == "final-without-" + str(bad_group):
            for row in result["by_fold"]:
                row["portfolio"]["excess_return"] = .05
        if purpose == "final-without-" + str(mismatch_group):
            result["by_fold"][0]["daily_index_hash"] = "wrong"
        for row in result["by_fold"]:
            row["portfolio"]["max_drawdown"] = risk
        return result
    monkeypatch.setattr(finalize, "run_matrix", run)
    # Native references are verified by the separate actual CLI integration.
    monkeypatch.setattr(finalize, "_verify_references", lambda *args: None)
    return calls


def test_final_review_removes_each_declared_group_and_preserves_failed_trials(final_session, monkeypatch):
    session, base = final_session
    calls = synthetic_runner(session, monkeypatch)
    result = finalize.freeze_features(session, session.config.artifact_root / "reviews", run_id="all-groups")
    assert result["status"] == "accepted"
    assert [purpose for purpose, _, _ in calls] == ["final-full", "final-without-range", "final-without-trend"]
    assert [len(columns) for _, columns, _ in calls] == [22, 21, 21]
    assert all(costs == [2.] for _, _, costs in calls)
    frozen = Path(result["freeze_path"])
    manifest = json.loads((frozen / "manifest.json").read_text())
    assert set(manifest["review"]["groups"]) == {"range", "trend"}
    assert json.loads((frozen / "trials/trial-run/trial-1.json").read_text())["status"] == "rejected"
    assert (frozen / "sources/trend_v1.py").is_file()
    assert session.registry.load("trend", 1)["state"] == "frozen"
    assert session.registry.load("range", 1)["state"] == "frozen"
    again = finalize.freeze_features(session, session.config.artifact_root / "reviews", run_id="all-groups")
    assert again["freeze_id"] == result["freeze_id"] and len(calls) == 3


@pytest.mark.parametrize("bad_group,mismatch_group,status", [("trend", None, "rejected"), (None, "range", "failed")])
def test_failed_or_unconfirmed_group_never_publishes(final_session, monkeypatch, bad_group, mismatch_group, status):
    session, _ = final_session
    synthetic_runner(session, monkeypatch, bad_group=bad_group, mismatch_group=mismatch_group)
    result = finalize.freeze_features(session, session.config.artifact_root / "reviews", run_id="no-publish")
    assert result["status"] == status and result["freeze_path"] is None
    assert not (session.config.artifact_root / "frozen_features").exists()
    assert session.registry.load("trend", 1)["state"] == "candidate"
    assert session.registry.load("range", 1)["state"] == "candidate"


def test_absolute_risk_limit_prevents_freeze(final_session, monkeypatch):
    session, _ = final_session
    synthetic_runner(session, monkeypatch, risk=.13)
    result = finalize.freeze_features(session, session.config.artifact_root / "reviews", run_id="risk")
    assert result["status"] == "rejected" and "final_review_risk_limit" in result["reasons"]
    assert result["freeze_path"] is None


def test_membership_interruption_resumes_same_publication_without_retraining(final_session, monkeypatch):
    session, _ = final_session
    calls = synthetic_runner(session, monkeypatch)
    original = finalize._freeze_members
    def interrupt(session, freeze_id, manifest):
        original(session, freeze_id, {**manifest, "factors": manifest["factors"][:1]})
        raise KeyboardInterrupt()
    monkeypatch.setattr(finalize, "_freeze_members", interrupt)
    with pytest.raises(KeyboardInterrupt):
        finalize.freeze_features(session, session.config.artifact_root / "reviews", run_id="resume")
    packages = list((session.config.artifact_root / "frozen_features").glob("*/manifest.json"))
    assert len(packages) == 1 and not (packages[0].parent / "published.json").exists()
    weights_time = (packages[0].parent / "features.parquet").stat().st_mtime_ns
    monkeypatch.setattr(finalize, "_freeze_members", original)
    result = finalize.freeze_features(session, session.config.artifact_root / "reviews", run_id="resume")
    assert Path(result["freeze_path"]) == packages[0].parent and len(calls) == 3
    assert (packages[0].parent / "features.parquet").stat().st_mtime_ns == weights_time
    assert (packages[0].parent / "published.json").is_file()
    assert all(session.registry.load(name, 1)["state"] == "frozen" for name in ("trend", "range"))


def test_undeclared_acceptance_thresholds_fail_before_training(final_session, monkeypatch):
    session, _ = final_session
    calls = synthetic_runner(session, monkeypatch)
    session.config.acceptance = AcceptancePolicy()
    with pytest.raises(ConfigurationError, match="acceptance thresholds"):
        finalize.freeze_features(session, session.config.artifact_root / "reviews", run_id="undeclared")
    assert calls == []


@pytest.mark.parametrize("field,value", [("minimum_net_return", -1), ("maximum_drawdown", 1.1),
    ("maximum_annualized_volatility", -.1), ("maximum_execution_cost_over_initial_equity", -.1),
    ("minimum_effective_dates", 0)])
def test_acceptance_rejects_invalid_thresholds(field, value):
    with pytest.raises(ValidationError):
        AcceptancePolicy(**{field: value})


@pytest.fixture
def matrix(setup):
    _, protocol, _, _, _ = setup
    config = SimpleNamespace(models=[ModelSpec(name="ridge"), protocol.model, ModelSpec(name="xgboost")],
                             validation=protocol.validation, research=protocol.research)
    rows = []
    for model in config.models:
        for row in report(protocol, .01)["by_fold"]:
            row["model"] = model.name
            row["model_spec"] = finalize._seed_model(model, row["seed"]).model_dump(mode="json")
            for metrics in [row["portfolio"], *row["cost_stress"].values()]:
                metrics.update(net_return=.02, execution_cost_over_initial_equity=.01,
                               effective_dates=20, accounting_reconciled=True)
            rows.append(row)
    return config, {"status": "completed", "by_fold": rows}


def test_complete_matrix_allows_different_model_processors(matrix):
    config, result = matrix
    result["by_fold"][0]["dataset"]["processor_kind"] = "ridge"
    finalize.validate_matrix(result, config)


@pytest.mark.parametrize("corruption", ["missing", "duplicate", "fold", "holdout", "samples", "pressure", "nonfinite", "ledger", "dates"])
def test_model_matrix_rejects_invalid_comparisons(matrix, corruption):
    config, result = matrix
    if corruption == "missing":
        result["by_fold"].pop()
    elif corruption == "duplicate":
        result["by_fold"].append(copy.deepcopy(result["by_fold"][0]))
    else:
        row = result["by_fold"][0]
        if corruption == "fold": row["dataset"]["fold"]["train"]["end"] = "2022-01-01"
        if corruption == "holdout": row["dataset"]["holdout_start"] = "2025-01-01"
        if corruption == "samples": row["dataset"]["evaluation_index_hash"] = "wrong"
        if corruption == "pressure": row["cost_stress"] = {}
        if corruption == "nonfinite": row["portfolio"]["net_return"] = float("nan")
        if corruption == "ledger": row["portfolio"]["accounting_reconciled"] = False
        if corruption == "dates": row["portfolio"]["effective_dates"] = 0
    with pytest.raises(QualityError):
        finalize.validate_matrix(result, config)