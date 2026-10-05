import copy
from types import SimpleNamespace

import pytest

from etf_ml.errors import ConfigurationError, IntegrityError
from etf_ml.research.campaign import CampaignLedger
from etf_ml.research.closure import prepare_closure, reconcile_calls
from etf_ml.research.qualification import read_object
from etf_ml.utils import atomic_json, content_hash, file_hash, source_hashes
from etf_ml.validation.holdout import require_access_review


@pytest.fixture
def billing(tmp_path):
    source = tmp_path / "provider-receipt.json"
    atomic_json(source, {"fixture": "provider row 17", "cost": "0.10", "tokens": [12, 3]})
    call = {"call_id": "call-1", "request_hash": "request", "currency": "USD",
            "actual_cost": None, "maximum_cost": None, "provider_input_tokens": None,
            "provider_output_tokens": None, "execution_status": "uncertain"}
    row = {**call, "actual_cost": "0.10", "provider_input_tokens": 12, "provider_output_tokens": 3,
           "billing_reference": "provider-row-17", "mapping_basis": "Human checked request fingerprint",
           "reviewer": "fixture-reviewer", "reviewed_at": "2020-01-01T00:00:00Z",
           "sources": [{"path": str(source), "sha256": file_hash(source)}]}
    receipt = {"schema_version": "campaign-billing-receipts-v1", "campaign_id": "campaign",
               "event_chain_head": "head", "calls": [row]}
    return [call], receipt


def reconcile(billing):
    calls, receipt = billing
    return reconcile_calls(calls, receipt, campaign_id="campaign", event_chain_head="head")


def test_supplement_reconciles_without_modifying_old_calls_or_execution_status(billing):
    before = copy.deepcopy(billing)
    rows = reconcile(billing)
    assert rows[0]["actual_cost"] == "0.10" and rows[0]["provider_input_tokens"] == 12
    assert rows[0]["execution_status"] == "uncertain"  # Payment evidence cannot authorize replay.
    assert billing == before
    assert reconcile(billing) == rows


@pytest.mark.parametrize("key,value", [("call_id", "other"), ("request_hash", "other"),
    ("currency", "CNY"), ("actual_cost", -1), ("actual_cost", "NaN"), ("actual_cost", True),
    ("provider_input_tokens", True), ("provider_output_tokens", -1), ("provider_input_tokens", 1.5),
    ("sources", []), ("reviewer", " "), ("reviewed_at", "invalid"),
    ("reviewed_at", "2020-01-01"), ("reviewed_at", "2999-01-01T00:00:00Z"),
    ("mapping_basis", None), ("billing_reference", None)])
def test_receipt_rejects_invalid_claims(billing, key, value):
    billing[1]["calls"][0][key] = value
    with pytest.raises((IntegrityError, ConfigurationError)):
        reconcile(billing)


def test_receipt_partial_zero_tamper_duplicate_wrong_campaign_and_overrun(billing, tmp_path):
    calls, receipt = billing
    row = receipt["calls"][0]
    row.update(actual_cost=None, provider_input_tokens=None, provider_output_tokens=None)
    assert reconcile(billing) == calls
    row.update(actual_cost="0")
    assert reconcile(billing)[0]["actual_cost"] == "0"
    assert reconcile(billing)[0]["provider_input_tokens"] is None
    receipt["calls"].append(copy.deepcopy(row))
    with pytest.raises(IntegrityError, match="Duplicate"):
        reconcile(billing)
    receipt["calls"].pop()
    receipt["event_chain_head"] = "different"
    with pytest.raises(IntegrityError, match="another campaign"):
        reconcile(billing)
    receipt["event_chain_head"] = "head"
    row["actual_cost"] = "0.10"
    calls[0]["maximum_cost"] = "0.05"
    with pytest.raises(IntegrityError, match="overrun"):
        reconcile(billing)
    calls[0]["maximum_cost"] = None
    atomic_json(tmp_path / "provider-receipt.json", {"tampered": True})
    with pytest.raises(IntegrityError, match="source changed"):
        reconcile(billing)


def test_receipt_cannot_double_count_provider_reference_or_override_measured_usage(billing):
    calls, receipt = billing
    calls[0]["actual_cost"] = "0.20"
    with pytest.raises(IntegrityError, match="conflicts"):
        reconcile(billing)
    calls[0]["actual_cost"] = None
    calls.append({**calls[0], "call_id": "call-2"})
    receipt["calls"].append({**receipt["calls"][0], "call_id": "call-2"})
    with pytest.raises(IntegrityError, match="reference reused"):
        reconcile(billing)


def test_closure_uses_real_ledger_and_preserves_failed_slot_and_unknowns(tmp_path):
    config = SimpleNamespace(artifact_root=tmp_path, research=SimpleNamespace(budget_mode="unlimited"))
    root = tmp_path / "runs" / "old"
    session = root / "research" / "sessions" / "old-research"
    snapshot = tmp_path / "snapshots" / "snapshot"
    atomic_json(snapshot / "snapshot_manifest.json", {"snapshot_id": "snapshot", "cutoff": "2026-09-04",
        "spec": {"holdout_start": "2026-01-01"}})
    protocol = {"snapshot_id": "snapshot", "validation": {"holdout_start": "2026-01-01", "holdout_independent": False}}
    atomic_json(session / "protocol.json", protocol)
    call = {"status": "cost_unknown", "request_hash": "request", "actual_cost": None,
            "usage": {"attempts": [{"provider_input_tokens": None, "provider_output_tokens": None}]}}
    atomic_json(root / "research/llm/billing.json", {"calls": {"call-1": call}, "currency": "USD"})
    atomic_json(session / "checkpoint.json", {"status": "paused_budget", "billing": {"calls": {"call-1": call}}})
    atomic_json(root / "status.json", {"status": "failed"})
    atomic_json(root / "first_loop_report.json", {"status": "failed", "checkpoint": str(session / "checkpoint.json"),
        "checkpoint_sha256": file_hash(session / "checkpoint.json"), "protocol_id": content_hash(protocol),
        "snapshot_path": str(snapshot), "candidate_decisions": []})
    ledger = CampaignLedger(tmp_path / "research_campaigns", "campaign", 1)
    ledger.initialize()
    assert ledger.begin_trial(run_id="old-research", trial_index=0, compatibility_group_id="group", protocol_id=content_hash(protocol))
    ledger.record_usage(run_id="old-research", trial_index=0, usage_summary={"cost": {"calls": {"call-1": call}}})
    protected = {str(path): source_hashes(path) for path in (root, snapshot, ledger.root)}
    out = tmp_path / "research_audits/closure"
    report = prepare_closure(config, root, out, campaign_id="campaign")
    assert report["external_calls"] == 0 and not report["holdout_values_read"]
    inventory = read_object(out / "billing_inventory.json")
    assert inventory["unknown_cost_calls"] == inventory["provider_usage_unknown_calls"] == 1
    assert inventory["status"] == "partial" and inventory["attempt_slots_released"] == 0
    assert read_object(out / "holdout_access_audit.template.json")["history_complete"] is None
    assert read_object(out / "campaign_audit.json")["uncommitted_attempts"][0]["slot_retained"]
    assert protected == {str(path): source_hashes(path) for path in (root, snapshot, ledger.root)}
    reviewed_out = tmp_path / "research_audits/blank-receipt"
    prepare_closure(config, root, reviewed_out, campaign_id="campaign",
                    billing_receipts=out / "billing_receipts.template.json")
    assert read_object(reviewed_out / "billing_inventory.json")["unknown_cost_calls"] == 1
    assert protected == {str(path): source_hashes(path) for path in (root, snapshot, ledger.root)}
    assert not ledger.begin_trial(run_id="extra", trial_index=0, compatibility_group_id="group", protocol_id="p")
    with pytest.raises(ConfigurationError, match="already exists"):
        prepare_closure(config, root, out, campaign_id="campaign")


@pytest.fixture
def access(tmp_path):
    from etf_ml.config import load_config
    config = load_config(overrides={"validation": {"holdout_start": "2026-01-01", "holdout_independent": True}})
    snapshot = tmp_path / "snapshot"
    atomic_json(snapshot / "snapshot_manifest.json", {"snapshot_id": "snapshot", "cutoff": "2026-09-04",
        "spec": {"holdout_start": "2026-01-01"}})
    source = tmp_path / "history.json"
    atomic_json(source, {"fixture": "reviewed complete history"})
    record = {"schema_version": "holdout-access-audit-v2", "start": "2026-01-01", "end": "2026-09-04",
        "snapshot_id": "snapshot", "snapshot_manifest_sha256": file_hash(snapshot / "snapshot_manifest.json"),
        "history_complete": True, "prior_selection_use": False, "prior_result_access": False,
        "reviewer": "fixture", "reviewed_at": "2020-01-01T00:00:00Z",
        "sources": [{"path": str(source), "sha256": file_hash(source)}]}
    audit = tmp_path / "access.json"
    atomic_json(audit, record)
    return config, {"snapshot_path": str(snapshot), "snapshot_id": "snapshot"}, audit, record


def test_holdout_execution_requires_complete_review_before_claim_or_values(access):
    config, package, audit, record = access
    with pytest.raises(ConfigurationError, match="not_audited"):
        require_access_review(config, package, None)
    assert require_access_review(config, package, audit)["sha256"] == file_hash(audit)
    config.validation.holdout_independent = False
    with pytest.raises(ConfigurationError, match="not_declared"):
        require_access_review(config, package, audit)


@pytest.mark.parametrize("key,value", [("history_complete", None), ("prior_selection_use", True),
    ("prior_result_access", True), ("snapshot_id", "other"), ("end", "2026-09-03"),
    ("snapshot_manifest_sha256", "other"), ("reviewer", None), ("sources", []),
    ("schema_version", "holdout-access-audit-v1")])
def test_holdout_execution_rejects_unknown_exposed_or_mismatched_review(access, key, value):
    config, package, audit, record = access
    atomic_json(audit, {**record, key: value})
    with pytest.raises((ConfigurationError, IntegrityError)):
        require_access_review(config, package, audit)


def test_holdout_worker_rejects_missing_or_changed_audit_before_materialization(access, tmp_path, monkeypatch):
    from etf_ml.validation import holdout
    config, package, audit, record = access
    monkeypatch.setattr(holdout, "load_frozen_model", lambda *args, **kwargs: (None, None, package))
    output = tmp_path / "output"
    with pytest.raises(ConfigurationError, match="not_audited"):
        holdout.run_holdout(config, tmp_path / "package", output, run_id="test")
    with pytest.raises(IntegrityError, match="changed before worker"):
        holdout.run_holdout(config, tmp_path / "package", output, run_id="test",
                            access_audit=audit, access_audit_sha256="changed")
    assert not output.exists()
