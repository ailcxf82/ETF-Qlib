import json
from types import SimpleNamespace

import pytest

from etf_ml.errors import ConfigurationError, IntegrityError
from etf_ml.research.first_loop import closure_evidence
from etf_ml.research.qualification import holdout_qualification, qualification_report
from etf_ml.utils import atomic_json, file_hash


def snapshot_fixture(tmp_path, *, source_complete=True, normalized_errors=None):
    root = tmp_path / "snapshot"
    root.mkdir()
    qualification = {"mode": "formal", "formal_g0_passed": False, "assumptions": [],
                     "historical_membership_verified": True, "historical_availability_enforced": True}
    quality = {"status": "passed", "qualification": qualification,
               "normalized_errors": normalized_errors or [], "raw_source_unchanged": True,
               "adjustment_validation": {"status": "passed", "source_completeness_verified": source_complete},
               "events_validation": {"status": "structurally_valid", "source_completeness_verified": source_complete},
               "income_validation": {"enabled": False}, "metadata_quote_coverage": {"status": "passed"}}
    atomic_json(root / "data_quality.json", quality)
    source = root / "source-proof.json"
    atomic_json(source, {"fixture": True})
    manifest = {"snapshot_id": "snapshot", "qualification": qualification,
                "spec": {"volume_unit": "shares", "amount_multiplier": 1, "price_mode": "raw",
                         "change_unit": "ratio", "trusted_calendar": str(source),
                         "benchmark_path": str(source), "metadata_path": str(source)},
                "external_hashes": {str(source.resolve()): file_hash(source)},
                "files": {"data_quality.json": file_hash(root / "data_quality.json")}}
    atomic_json(root / "snapshot_manifest.json", manifest)
    return SimpleNamespace(path=root, manifest=manifest, snapshot_id="snapshot")


def candidate_fixture(tmp_path, status="rejected"):
    root = tmp_path / "paired"
    atomic_json(root / "evaluation.json", {"status": status, "protocol_id": "protocol",
                                           "reasons": ["absolute_risk_limit"] if status == "rejected" else []})
    atomic_json(root / "candidate_report.json", {"snapshot_id": "snapshot", "protocol_id": "protocol"})
    return [{"factor_id": "alpha", "status": status,
             "reports": {"candidate": str(root / "candidate_report.json")}}]


def test_qualification_separates_complete_data_from_rejected_factor(tmp_path):
    snapshot = snapshot_fixture(tmp_path)
    manifest_hash = file_hash(snapshot.path / "snapshot_manifest.json")
    report = qualification_report(snapshot, candidate_fixture(tmp_path), {"holdout_independent": False})
    assert report["stages"]["data_qualification"]["status"] == "passed"
    assert report["stages"]["factor_evaluation"]["status"] == "rejected"
    assert report["stages"]["holdout_qualification"]["status"] == "ineligible"
    assert report["legacy_booleans"] == {"formal_g0_passed": True, "holdout_evaluated": False,
                                         "investment_accepted": False}
    assert file_hash(snapshot.path / "snapshot_manifest.json") == manifest_hash


def test_unknown_source_completeness_does_not_hide_accepted_research(tmp_path):
    snapshot = snapshot_fixture(tmp_path, source_complete=False)
    report = qualification_report(snapshot, candidate_fixture(tmp_path, "accepted"),
                                  {"holdout_independent": True})
    assert report["stages"]["data_qualification"]["status"] == "unknown"
    assert "events_source_completeness:unknown" in report["stages"]["data_qualification"]["reason_codes"]
    assert report["stages"]["factor_evaluation"]["status"] == "accepted"
    assert report["stages"]["holdout_qualification"]["status"] == "unknown"
    assert report["stages"]["investment_readiness"]["status"] == "not_ready"


def test_known_data_failure_wins_over_unknown(tmp_path):
    snapshot = snapshot_fixture(tmp_path, source_complete=False, normalized_errors=["negative_volume"])
    report = qualification_report(snapshot, [], {})
    assert report["stages"]["data_qualification"]["status"] == "failed"
    assert report["stages"]["factor_evaluation"]["status"] == "not_run"


def test_qualification_rejects_tampering_and_invalid_roots(tmp_path):
    snapshot = snapshot_fixture(tmp_path)
    atomic_json(snapshot.path / "data_quality.json", [])
    with pytest.raises(IntegrityError, match="hash mismatch"):
        qualification_report(snapshot, [], {})
    snapshot.manifest["files"]["data_quality.json"] = file_hash(snapshot.path / "data_quality.json")
    with pytest.raises(IntegrityError, match="JSON object"):
        qualification_report(snapshot, [], {})


def test_candidate_report_cannot_upgrade_rejection(tmp_path):
    snapshot = snapshot_fixture(tmp_path)
    candidates = candidate_fixture(tmp_path)
    candidates[0]["status"] = "accepted"
    with pytest.raises(IntegrityError, match="identity or status"):
        qualification_report(snapshot, candidates, {})


def test_holdout_requires_reviewed_hash_bound_history_not_just_boolean(tmp_path):
    source = tmp_path / "access-history.json"
    atomic_json(source, {"review": "fixture covering complete project history"})
    audit = tmp_path / "audit.json"
    record = {"schema_version": "holdout-access-audit-v1", "start": "2026-01-01",
              "history_complete": True, "prior_selection_use": False, "prior_result_access": False,
              "reviewer": "fixture-reviewer", "reviewed_at": "2026-01-01T00:00:00Z",
              "sources": [{"path": str(source), "sha256": file_hash(source)}]}
    validation = {"holdout_independent": True, "holdout_start": "2026-01-01"}
    atomic_json(audit, record)
    assert holdout_qualification(validation, access_audit=audit,
        snapshot_holdout_start="2026-01-01")["status"] == "eligible"
    assert holdout_qualification(validation, access_audit=audit)["reason_codes"] == [
        "snapshot_holdout_boundary_missing"]
    assert holdout_qualification(validation, access_audit=audit,
        snapshot_holdout_start="2026-02-01")["reason_codes"] == [
            "validation_snapshot_holdout_boundary_mismatch"]
    atomic_json(audit, {**record, "prior_selection_use": True})
    assert holdout_qualification(validation, access_audit=audit,
        snapshot_holdout_start="2026-01-01")["status"] == "ineligible"
    atomic_json(audit, {**record, "history_complete": False})
    assert holdout_qualification(validation, access_audit=audit,
        snapshot_holdout_start="2026-01-01")["status"] == "unknown"
    atomic_json(audit, record)
    atomic_json(source, {"changed": True})
    with pytest.raises(IntegrityError, match="source changed"):
        holdout_qualification(validation, access_audit=audit, snapshot_holdout_start="2026-01-01")


def test_deterministic_closure_needs_no_calls_but_live_mode_still_does():
    state = {"status": "completed", "billing": {"calls": {}}}
    records = [{"result": {"by_candidate": [{"status": "rejected", "reports": {"candidate": "report"}}]}}]
    baseline = {"by_fold": [{"fold": "A"}]}
    assert closure_evidence(state, records, baseline)["status"] == "incomplete"
    offline = closure_evidence(state, records, baseline, execution_mode="deterministic_library")
    assert offline["status"] == "completed" and offline["llm_dispatches"] == 0
    state["billing"]["calls"] = {str(i): {"paid": True, "status": "cost_unknown", "response_hash": "hash"}
                                  for i in range(3)}
    assert closure_evidence(state, records, baseline)["status"] == "completed"
    assert closure_evidence(state, records, baseline, execution_mode="deterministic_library")["status"] == "incomplete"
    with pytest.raises(ConfigurationError, match="execution mode"):
        closure_evidence(state, records, baseline, execution_mode="made_up")
