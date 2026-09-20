import json

import pytest

from etf_ml.errors import ConfigurationError, IntegrityError, QualityError
from etf_ml.registry import FactorRegistry
from etf_ml.research.context import FactorSpec
from etf_ml.utils import atomic_json


@pytest.fixture
def factor():
    return FactorSpec(factor_id="momentum", hypothesis="trend", formula="return_5d",
                      required_fields=["adj_close"], lookback=6, minimum_observations=6,
                      expected_difference="shorter window", source="def compute(panel):\n    return panel",
                      context_hash="context")


def proof(tmp_path, factor, kind, **extra):
    target = tmp_path / (kind + ".json")
    atomic_json(target, {"factor_version_id": factor.version_id, **extra})
    return target


def test_lifecycle_copies_evidence_and_preserves_all_versions(tmp_path, factor):
    registry = FactorRegistry(tmp_path / "registry")
    initial = registry.register(factor, lineage={"snapshot_id": "snapshot", "protocol_id": "protocol"})
    assert initial["state"] == "proposed"
    assert registry.register(factor, lineage=initial["definition"]["lineage"]) == initial
    quality = proof(tmp_path, factor, "quality", status="passed")
    registry.transition("momentum", 1, "validated", evidence={"quality": quality})
    evaluation = proof(tmp_path, factor, "evaluation", status="accepted")
    registry.transition("momentum", 1, "evaluated", evidence={"evaluation": evaluation})
    registry.transition("momentum", 1, "candidate", evidence={"evaluation": evaluation})
    feature_set = proof(tmp_path, factor, "feature_set", factor_version_ids=[factor.version_id])
    frozen = registry.transition("momentum", 1, "frozen", evidence={"feature_set": feature_set})
    # Workspace evidence can change; the committed copy and decision stay immutable.
    quality.write_text("changed workspace", encoding="utf-8")
    assert registry.load("momentum", 1) == frozen
    retired = registry.transition("momentum", 1, "retired", reasons=["source_data_corrected"])
    assert len(retired["events"]) == 6
    next_spec = factor.model_copy(update={"version": 2, "formula": "return_10d"})
    assert registry.register(next_spec, lineage={})["state"] == "proposed"
    assert registry.load("momentum", 1)["state"] == "retired"


def test_definition_changes_need_new_version(tmp_path, factor):
    registry = FactorRegistry(tmp_path / "registry")
    registry.register(factor, lineage={})
    with pytest.raises(ConfigurationError, match="new version"):
        registry.register(factor.model_copy(update={"source": "different code"}), lineage={})
    with pytest.raises(ConfigurationError, match="lineage"):
        registry.register(factor, lineage={"new": True})


def test_illegal_transitions_and_foreign_proofs_fail(tmp_path, factor):
    registry = FactorRegistry(tmp_path / "registry")
    registry.register(factor, lineage={})
    with pytest.raises(ConfigurationError):
        registry.transition("momentum", 1, "frozen")
    with pytest.raises(QualityError, match="Missing"):
        registry.transition("momentum", 1, "validated")
    quality = proof(tmp_path, factor, "quality", status="failed")
    with pytest.raises(QualityError, match="not passed"):
        registry.transition("momentum", 1, "validated", evidence={"quality": quality})
    other = factor.model_copy(update={"version": 2})
    foreign = proof(tmp_path, other, "quality", status="passed")
    with pytest.raises(QualityError, match="another"):
        registry.transition("momentum", 1, "validated", evidence={"quality": foreign})
    with pytest.raises(QualityError, match="reason"):
        registry.transition("momentum", 1, "rejected")
    registry.transition("momentum", 1, "rejected", reasons=["failed_coverage"])
    with pytest.raises(ConfigurationError):
        registry.transition("momentum", 1, "validated")


@pytest.mark.parametrize("kind", ["source", "event", "evidence"])
def test_corrupt_source_history_or_proof_is_detected(tmp_path, factor, kind):
    registry = FactorRegistry(tmp_path / "registry")
    registry.register(factor, lineage={})
    quality = proof(tmp_path, factor, "quality", status="passed")
    current = registry.transition("momentum", 1, "validated", evidence={"quality": quality})
    root = tmp_path / "registry" / "momentum" / "v1"
    target = {"source": root / "source.py",
              "event": root / "events" / (current["events"][-1]["event_id"] + ".json"),
              "evidence": root / current["events"][-1]["evidence"]["quality"]["path"]}[kind]
    target.write_text('{"status":"forged"}', encoding="utf-8")
    with pytest.raises(IntegrityError):
        registry.load("momentum", 1)


def test_rejected_evaluation_cannot_be_promoted(tmp_path, factor):
    registry = FactorRegistry(tmp_path / "registry")
    registry.register(factor, lineage={})
    quality = proof(tmp_path, factor, "quality", status="passed")
    registry.transition("momentum", 1, "validated", evidence={"quality": quality})
    evaluation = proof(tmp_path, factor, "evaluation", status="rejected")
    registry.transition("momentum", 1, "evaluated", evidence={"evaluation": evaluation})
    with pytest.raises(QualityError, match="accepted"):
        registry.transition("momentum", 1, "candidate", evidence={"evaluation": evaluation})


def test_candidate_cannot_switch_to_an_uncommitted_evaluation(tmp_path, factor):
    registry = FactorRegistry(tmp_path / "registry")
    registry.register(factor, lineage={})
    quality = proof(tmp_path, factor, "quality", status="passed")
    registry.transition("momentum", 1, "validated", evidence={"quality": quality})
    evaluation = proof(tmp_path, factor, "evaluation", status="inconclusive", protocol_id="old")
    registry.transition("momentum", 1, "evaluated", evidence={"evaluation": evaluation})
    changed = proof(tmp_path, factor, "evaluation", status="accepted", protocol_id="new")
    with pytest.raises(QualityError, match="committed evaluation"):
        registry.transition("momentum", 1, "candidate", evidence={"evaluation": changed})
