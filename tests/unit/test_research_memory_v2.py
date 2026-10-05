import json

import pytest

from etf_ml.errors import IntegrityError
from etf_ml.research.memory_index import ResearchMemoryIndex
from etf_ml.research.research_memory import build_card
from etf_ml.utils import atomic_json, content_hash, file_hash


def _committed(root, name, card):
    session = root / "sessions" / name
    session.mkdir(parents=True)
    trial = session / "trial-00000.json"
    atomic_json(trial, {"feedback": {"status": "rejected"}, "result": {"status": "rejected"}, "research_card": card})
    atomic_json(session / "checkpoint.json", {"created_at_ns": 1, "trials": [{"path": trial.name, "sha256": file_hash(trial)}]})


def test_explicit_sources_include_two_roots_and_exclude_unregistered(tmp_path):
    memory = ResearchMemoryIndex(tmp_path / "memory")
    card = {"factor_id": "a", "formula": "a", "status": "rejected", "snapshot_id": "s", "protocol_id": "p", "baseline_id": "b"}
    one, two, hidden = tmp_path / "one", tmp_path / "two", tmp_path / "hidden"
    _committed(one, "one", card)
    _committed(two, "two", {**card, "factor_id": "b"})
    _committed(hidden, "hidden", {**card, "factor_id": "hidden"})
    memory.register_source(one, allowed_root=tmp_path)
    memory.register_source(two, allowed_root=tmp_path)
    result = memory.rebuild()
    assert {row["factor_id"] for row in result["cards"]} == {"a", "b"}
    assert all("path" not in row for row in result["evidence"])


def test_tampered_or_orphan_evidence_never_enters_index(tmp_path):
    memory = ResearchMemoryIndex(tmp_path / "memory")
    root = tmp_path / "research"
    _committed(root, "one", {"factor_id": "a", "formula": "a"})
    memory.register_source(root, allowed_root=tmp_path)
    trial = root / "sessions" / "one" / "trial-00000.json"
    trial.write_text(json.dumps({"changed": True}), encoding="utf-8")
    with pytest.raises(IntegrityError, match="changed"):
        memory.rebuild()


def test_v2_card_keeps_evaluation_identity_separate_from_resulting_baseline():
    card = build_card(hypothesis=type("H", (), {"hypothesis": "h", "reason": "r"})(),
                      proposal={"factor_id": "factor", "formula": "adj_close.shift(2)", "required_fields": []},
                      feedback={"status": "accepted", "by_candidate": [{"status": "accepted"}]},
                      context_hash="context", snapshot_id="snapshot", protocol_id="protocol-before",
                      baseline_id="baseline-before", trial_id="trial", definition_id="definition",
                      evaluation_id="evaluation", resulting_baseline_id="baseline-after",
                      resulting_protocol_id="protocol-after")
    assert card["evaluation_baseline_id"] == "baseline-before"
    assert card["resulting_baseline_id"] == "baseline-after"
    assert card["card_hash"] == content_hash({key: value for key, value in card.items() if key != "card_hash"})


def test_memory_rebuild_upgrades_legacy_feedback_without_rewriting_committed_trial(tmp_path):
    root = tmp_path / "research"
    session = root / "sessions" / "session"
    session.mkdir(parents=True)
    protocol = {"validation": {"folds": [{"name": "A"}]}, "research": {"seeds": [42]}}
    atomic_json(session / "protocol.json", protocol)
    legacy = {"schema_version": "feedback-summary-v2", "by_candidate": [{"decision_reasons": ["cost"]}]}
    hypothesis = {"hypothesis": "h", "reason": "evidence"}
    feedback = {"status": "rejected", "stage": "factor_selection", "protocol_id": content_hash(protocol),
        "risk_policy": {"mode": "max_drawdown", "limit": .12},
        "by_candidate": [{"factor_id": "factor", "status": "rejected", "reasons": ["cost_stress_excess_return"],
            "paired_deltas": [{"fold": "A", "seed": 42, "excess_return": -.01, "turnover": .2}],
            "group_ablation": {"status": "not_applicable"},
            "observations": {"data_quality": {"time_check": "passed"},
                "portfolio_metrics": {"by_fold": [{"fold": "A", "seed": 42,
                    "baseline": {"max_drawdown": .1}, "candidate": {"max_drawdown": .11}}]},
                "robustness": {"cost_stress": [{"fold": "A", "seed": 42, "multiplier": 2.,
                    "baseline": {"excess_return": .01, "max_drawdown": .1, "total_execution_cost": 10.},
                    "candidate": {"excess_return": -.02, "max_drawdown": .11, "total_execution_cost": 12.}}]},
                "signal_to_execution": {"status": "partial", "by_fold": []}}}]}
    card = build_card(hypothesis=hypothesis, proposal={"factor_id": "factor", "formula": "close/open"},
        feedback={"status": "rejected", "summary": legacy, "by_candidate": feedback["by_candidate"]},
        context_hash="context", snapshot_id="snapshot", protocol_id=content_hash(protocol),
        baseline_id="baseline", trial_id="session:0", definition_id="definition",
        evaluation_id="evaluation", feedback_summary=legacy)
    trial = session / "trial-00000.json"
    atomic_json(trial, {"research_card": card, "feedback": feedback})
    checkpoint = session / "checkpoint.json"
    atomic_json(checkpoint, {"created_at_ns": 1, "trials": [{"path": trial.name, "sha256": file_hash(trial)}]})
    before = file_hash(trial)
    memory = ResearchMemoryIndex(tmp_path / "memory")
    memory.register_source(root, allowed_root=tmp_path)

    result = memory.rebuild()

    rebuilt = result["cards"][0]
    assert rebuilt["feedback_summary"]["schema_version"] == "feedback-summary-v3"
    assert rebuilt["feedback_summary"]["by_candidate"][0]["cost_stress"][0]["execution_cost_delta"] == 2.
    assert rebuilt["feedback_summary"]["by_candidate"][0]["unknown_evidence"] == [
        "signal_to_execution_evidence_partial"]
    assert rebuilt["feedback_summary_rebuilt"] is True
    assert file_hash(trial) == before


def test_card_without_paired_artifact_evidence_is_not_reusable(tmp_path):
    card = build_card(hypothesis={"hypothesis": "h", "reason": "r"},
                      proposal={"factor_id": "factor", "formula": "adj_close.shift(2)"},
                      feedback={"status": "rejected", "by_candidate": [{"status": "rejected"}]},
                      context_hash="context", snapshot_id="snapshot", protocol_id="protocol",
                      baseline_id="baseline", trial_id="trial", definition_id="definition",
                      evaluation_id="evaluation")
    root = tmp_path / "research"
    _committed(root, "one", card)
    memory = ResearchMemoryIndex(tmp_path / "memory")
    memory.register_source(root, allowed_root=tmp_path)
    indexed = memory.rebuild()["cards"]
    assert indexed[0]["evaluation_evidence_verified"] is False


def test_evidence_outside_registered_root_reports_specific_failure(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    verified, reason = ResearchMemoryIndex._evaluation_evidence_status(
        {"evaluation_evidence": [{"path": str(outside), "sha256": file_hash(outside)}]},
        tmp_path / "allowed",
    )
    assert not verified and reason == "reference_outside_allowed_root"


def test_missing_evaluation_evidence_reports_specific_failure(tmp_path):
    verified, reason = ResearchMemoryIndex._evaluation_evidence_status(
        {"evaluation_evidence": [{"path": str(tmp_path / "missing.json"), "sha256": "0" * 64}]},
        tmp_path,
    )
    assert not verified and reason == "evaluation_evidence_missing"


def test_memory_evidence_projection_sends_hashes_without_local_paths(tmp_path, monkeypatch):
    from etf_ml.research.memory_index import _evaluation_evidence_hash_projection

    monkeypatch.setattr(ResearchMemoryIndex, "_evaluation_evidence_status",
        staticmethod(lambda card, root: (True, "verified")))
    card = {"evaluation_evidence": [{"path": str(tmp_path / "evaluation.json"), "sha256": "a" * 64}]}
    projected = _evaluation_evidence_hash_projection(card, tmp_path)
    assert projected == [{"name": "evaluation.json", "sha256": "a" * 64}]
    assert all("path" not in row for row in projected)
