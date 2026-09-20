"""Synthetic state/provenance fixtures; not investment acceptance evidence."""
import json
from types import SimpleNamespace

import pytest

from etf_ml.errors import ConfigurationError, IntegrityError, QualityError
from etf_ml.models.deployment import select_model, validate_selected_bundle
from etf_ml.registry.model_versions import ModelVersionRegistry
from etf_ml.utils import atomic_json, content_hash


@pytest.fixture
def registered(tmp_path):
    payload = {"kind": "frozen_model_version", "schema_version": 1,
               "protocol_id": "fixed-protocol", "acceptance": {"minimum_net_return": .01}}
    version = content_hash(payload)
    package = tmp_path / "packages" / version
    atomic_json(package / "manifest.json", {**payload, "version_id": version})
    registry = ModelVersionRegistry(tmp_path / "registry")
    registry.register(package)
    return registry, version, package


def test_immutable_registration_and_frozen_state(registered):
    registry, version, package = registered
    current = registry.load(version)
    assert current["state"] == "frozen" and len(current["events"]) == 1
    assert registry.register(package) == current
    atomic_json(package / "manifest.json", {"kind": "changed", "version_id": version})
    with pytest.raises(IntegrityError): registry.load(version)


@pytest.mark.parametrize("state,status", [("accepted", "passed"), ("rejected", "failed")])
def test_independent_decision_proof_is_copied_and_hash_linked(registered, tmp_path, state, status):
    registry, version, _ = registered
    proof = tmp_path / "acceptance.json"
    atomic_json(proof, {"version_id": version, "stage": "independent_holdout", "status": status,
                       "protocol_id": "fixed-protocol", "acceptance": {"minimum_net_return": .01}})
    result = registry.transition(version, state, evidence=proof, reasons=["synthetic decision"])
    proof.write_text("changed external file", encoding="utf-8")
    assert registry.load(version) == result
    assert [e["state"] for e in result["events"]] == ["frozen", state]
    assert result["events"][1]["previous"] == result["events"][0]["event_id"]
    with pytest.raises(ConfigurationError): registry.transition(version, "accepted", evidence=proof)
    assert registry.transition(version, "retired", reasons=["superseded"])["state"] == "retired"


@pytest.mark.parametrize("change", [{"version_id": "other"}, {"stage": "development"},
    {"status": "failed"}, {"protocol_id": "different"}, {"acceptance": {"minimum_net_return": .02}}])
def test_wrong_acceptance_identity_does_not_change_state(registered, tmp_path, change):
    registry, version, _ = registered
    proof = tmp_path / "wrong.json"
    payload = {"version_id": version, "stage": "independent_holdout", "status": "passed",
               "protocol_id": "fixed-protocol", "acceptance": {"minimum_net_return": .01}}
    atomic_json(proof, {**payload, **change})
    with pytest.raises(QualityError): registry.transition(version, "accepted", evidence=proof)
    assert registry.load(version)["state"] == "frozen"


def test_missing_evidence_and_retirement_reason_fail(registered):
    registry, version, _ = registered
    with pytest.raises(QualityError): registry.transition(version, "accepted")
    with pytest.raises(QualityError): registry.transition(version, "retired")
    assert registry.load(version)["state"] == "frozen"


def test_corrupt_history_is_refused(registered):
    registry, version, _ = registered
    event = registry.load(version)["events"][0]
    path = registry._path(version) / "events" / (event["event_id"] + ".json")
    raw = json.loads(path.read_text())
    raw["state"] = "accepted"
    atomic_json(path, raw)
    with pytest.raises(IntegrityError, match="history changed"): registry.load(version)


@pytest.mark.parametrize("identity", ["../escape", "x" * 64, "aa", ""])
def test_invalid_registry_identity_is_rejected(tmp_path, identity):
    with pytest.raises(ConfigurationError): ModelVersionRegistry(tmp_path).load(identity)


def test_explicit_selection_has_no_ranking_fallback():
    result = {"ranking": ["lightgbm", "ridge"], "report": {"by_fold": [
        {"model": "ridge", "fold": "A", "seed": 42}, {"model": "lightgbm", "fold": "A", "seed": 43}]}}
    assert select_model(result, model="ridge", fold="A", seed=42, reason="explicit") == result["report"]["by_fold"][0]
    with pytest.raises(ConfigurationError): select_model(result, model="ridge", fold="A", seed=43, reason="explicit")
    with pytest.raises(ConfigurationError): select_model(result, model="ridge", fold="A", seed=42, reason=" ")


def test_preprocessor_and_holdout_lineage_mismatch_are_rejected():
    row = {"model_id": "model", "model_spec": {"name": "ridge"},
           "dataset": {"fold": {role: {"end": "2025-12-01"} for role in ("train", "early_stop", "selection")},
                       "processor_fit_index_hash": "fit"}}
    frozen = {"feature_set_id": "features", "snapshot_id": "snapshot", "columns": ["f"],
              "protocol": {"label": {"horizon": 5}, "universe": {},
                           "validation": {"holdout_start": "2026-01-01"}}}
    manifest = {"model_id": "model", "model_spec": row["model_spec"], "dataset_manifest": row["dataset"],
                "feature_set_id": "features", "snapshot_id": "snapshot", "feature_names": ["f"],
                "horizon": 5, "universe_policy": {}}
    bundle = SimpleNamespace(manifest=manifest, processor=SimpleNamespace(columns=["f"], fitted_index_hash="fit"))
    validate_selected_bundle(bundle, row, frozen)
    bundle.processor.fitted_index_hash = "other"
    with pytest.raises(IntegrityError): validate_selected_bundle(bundle, row, frozen)
    bundle.processor.fitted_index_hash = "fit"
    manifest["dataset_manifest"]["fold"]["early_stop"]["end"] = "2026-01-01"
    with pytest.raises(QualityError): validate_selected_bundle(bundle, row, frozen)

def test_unpublished_registration_recovers_same_definition(registered, tmp_path, monkeypatch):
    _, version, package = registered
    registry = ModelVersionRegistry(tmp_path / "interrupted-registry")
    original = ModelVersionRegistry._publish
    def interrupt(path, event):
        atomic_json(path / "events" / (content_hash(event) + ".json"), event)
        raise KeyboardInterrupt()
    monkeypatch.setattr(ModelVersionRegistry, "_publish", staticmethod(interrupt))
    with pytest.raises(KeyboardInterrupt): registry.register(package)
    assert not (registry._path(version) / "head.json").exists()
    monkeypatch.setattr(ModelVersionRegistry, "_publish", staticmethod(original))
    current = registry.register(package)
    assert current["state"] == "frozen" and len(current["events"]) == 1
    assert current["definition"]["version_id"] == version