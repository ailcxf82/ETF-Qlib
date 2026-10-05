import json
from types import SimpleNamespace

import pytest

from etf_ml.errors import ConfigurationError, IntegrityError
from etf_ml.research.audit import (audit_research, campaign_audit, campaign_lineage,
                                   _verify_published_qualification, feedback_coverage_audit)
from etf_ml.research.campaign import CampaignLedger
from etf_ml.utils import atomic_json, content_hash, file_hash, source_hashes


def test_audit_command_is_a_separate_read_only_source_workflow(tmp_path):
    from etf_ml.cli import parser
    args = parser().parse_args(["audit-research", "--source-run", str(tmp_path), "--run-id", "audit"])
    assert args.source_run == tmp_path and args.command == "audit-research"
    assert args.holdout_access_audit is None
    audited = parser().parse_args(["audit-research", "--source-run", str(tmp_path),
        "--holdout-access-audit", str(tmp_path / "access.json"), "--run-id", "audit-verified"])
    assert audited.holdout_access_audit == tmp_path / "access.json"


def test_feedback_coverage_audit_distinguishes_upstream_from_card_delivery(tmp_path):
    from etf_ml.cli import parser

    artifacts = tmp_path / "artifacts"
    research = artifacts / "runs" / "run-one" / "research"
    session = research / "sessions" / "session-one"
    protocol = {"validation": {"folds": [{"name": "A"}, {"name": "B"}]},
                "research": {"seeds": [42]}}
    atomic_json(session / "protocol.json", protocol)
    paired = [{"fold": fold, "seed": 42, "excess_return": .01, "turnover": .02}
              for fold in ("A", "B")]
    card = {"schema_version": "research-card-v2", "trial_id": "run-one:0",
            "factor_id": "factor-one", "formula": "close / open", "definition_id": "definition",
            "evaluation_protocol_id": content_hash(protocol), "economic_status": "rejected",
            "reasons": ["risk_limit"], "feedback_summary": {"schema_version": "feedback-summary-v2",
                "by_candidate": [{"decision_reasons": ["risk_limit"], "paired_deltas": paired,
                    "signal_to_execution": {"status": "completed", "by_fold": [
                        {"fold": fold, "seed": 42} for fold in ("A", "B")]},
                    "risk": {"by_fold": [{"scenario": "base", "fold": fold, "seed": 42,
                        "baseline_value": .10, "candidate_value": .11, "incremental_change": .01}
                        for fold in ("A", "B")]}}]}}
    card["card_hash"] = content_hash(card)
    feedback = {"status": "rejected", "stage": "factor_selection",
        "protocol_id": content_hash(protocol), "risk_policy": {"mode": "max_drawdown", "limit": .12},
        "by_candidate": [{"factor_id": "factor-one", "status": "rejected", "reasons": ["risk_limit"],
        "paired_deltas": paired, "group_paired_deltas": paired,
        "group_ablation": {"status": "rejected"},
        "observations": {"portfolio_metrics": {"by_fold": [{"fold": fold, "seed": 42,
            "baseline": {"max_drawdown": .10}, "candidate": {"max_drawdown": .11}}
            for fold in ("A", "B")]},
            "robustness": {"cost_stress": [{"fold": "A", "seed": 42, "multiplier": 2.,
                "baseline": {"excess_return": .01, "total_execution_cost": 10.},
                "candidate": {"excess_return": .00, "total_execution_cost": 12.}}]},
            "signal_to_execution": {"status": "completed", "by_fold": [
                {"fold": fold, "seed": 42} for fold in ("A", "B")]}}}]}
    trial = session / "trial-00000.json"
    atomic_json(trial, {"research_card": card, "feedback": feedback,
                        "feedback_summary": card["feedback_summary"]})
    checkpoint = {"created_at_ns": 1, "trials": [{"path": trial.name, "sha256": file_hash(trial)}]}
    atomic_json(session / "checkpoint.json", checkpoint)
    sources = artifacts / "research_memory" / "v2" / "sources.json"
    atomic_json(sources, {"schema_version": "research-memory-sources-v2", "sources": [
        {"root": str(research), "allowed_root": str(artifacts)}]})
    args = parser().parse_args(["audit-feedback-coverage", "--run-id", "feedback-audit"])
    assert args.command == "audit-feedback-coverage"
    protected = source_hashes(research)

    report = feedback_coverage_audit(SimpleNamespace(artifact_root=artifacts),
        artifacts / "research_audits" / "feedback-audit", run_id="feedback-audit")

    assert report["committed_trial_count"] == 1
    row = report["by_trial"][0]
    assert row["persisted_fields"]["cost_stress_returns_and_costs"]["status"] == "not_transmitted"
    assert row["feedback_summary_rebuilt_for_memory"] is True
    assert row["fields"]["cost_stress_returns_and_costs"]["status"] == "present"
    assert row["fields"]["signal_to_execution"]["status"] == "present"
    assert row["fields"]["fold_seed_matrix"]["status"] == "present"
    assert row["fields"]["card_integrity"]["status"] == "present"
    assert source_hashes(research) == protected
    assert (artifacts / "research_audits" / "feedback-audit" / "feedback_coverage_report.json").is_file()


def test_feedback_audit_marks_unmeasured_technical_failure_not_applicable(tmp_path):
    from etf_ml.research.audit import _feedback_coverage

    card = {"attempt_outcome": "failed", "failure_category": "quality_not_passed",
            "feedback_summary": {}, "card_hash": ""}
    card["card_hash"] = content_hash({key: value for key, value in card.items() if key != "card_hash"})
    coverage = _feedback_coverage({"research_card": card,
        "feedback": {"status": "failed", "reason": "quality_not_passed", "by_candidate": []}},
        [(fold, seed) for fold in "ABCDE" for seed in (42, 43, 44)], tmp_path)
    assert coverage["fields"]["fold_seed_matrix"]["status"] == "not_applicable"
    assert coverage["fields"]["group_ablation"]["status"] == "not_applicable"
    assert coverage["fields"]["decision_reasons"]["status"] == "not_applicable"
    assert coverage["fields"]["technical_failure_reason"]["status"] == "present"


def test_audit_publishes_derived_reports_without_rewriting_sources(tmp_path):
    from etf_ml.data.snapshot import load_snapshot
    config = SimpleNamespace(artifact_root=tmp_path)
    source = tmp_path / "runs" / "old"
    snapshot_path = tmp_path / "snapshots" / "snapshot"
    quality = {"status": "passed", "qualification": {"mode": "formal"}}
    atomic_json(snapshot_path / "data_quality.json", quality)
    manifest = {"snapshot_id": "snapshot", "qualification": quality["qualification"],
                "files": {"data_quality.json": file_hash(snapshot_path / "data_quality.json")}}
    atomic_json(snapshot_path / "snapshot_manifest.json", manifest)
    assert load_snapshot(snapshot_path).snapshot_id == "snapshot"
    baseline_path = tmp_path / "baseline" / "baseline_report.json"
    atomic_json(baseline_path, {"status": "completed"})
    checkpoint_path = source / "research" / "checkpoint.json"
    atomic_json(checkpoint_path, {"status": "completed"})
    protocol = {"validation": {"holdout_independent": False}, "portfolio": {"risk": .12}}
    atomic_json(checkpoint_path.parent / "protocol.json", protocol)
    atomic_json(source / "first_loop_report.json", {
        "status": "completed", "snapshot_path": str(snapshot_path), "baseline_report": str(baseline_path),
        "checkpoint": str(checkpoint_path), "checkpoint_sha256": file_hash(checkpoint_path),
        "protocol_id": content_hash(protocol), "candidate_decisions": [],
        "formal_g0_passed": False, "holdout_evaluated": False, "investment_accepted": False})
    before = source_hashes(source)
    output = tmp_path / "research_audits" / "audit"
    result = audit_research(config, source, output, run_id="audit")
    assert result["status"] == "completed" and result["external_calls"] == 0
    assert result["stage_statuses"]["data_qualification"] == "unknown"
    assert result["stage_statuses"]["factor_evaluation"] == "not_run"
    assert result["artifacts"]["qualification_link_audit"]
    assert result["stage_statuses"]["data_qualification"] != "failed"
    assert result["holdout_values_read"] is False
    assert (output / "risk_attribution_report.json").exists()
    assert source_hashes(source) == before
    with pytest.raises(ConfigurationError, match="already exists"):
        audit_research(config, source, output, run_id="audit")
    atomic_json(checkpoint_path, {"changed": True})
    with pytest.raises(IntegrityError, match="checkpoint changed"):
        audit_research(config, source, tmp_path / "research_audits" / "audit2", run_id="audit2")


def test_audit_research_uses_supplemental_holdout_attestation_without_rewriting_it(tmp_path):
    from etf_ml.data.snapshot import load_snapshot
    artifacts = tmp_path / "artifacts"
    config = SimpleNamespace(artifact_root=artifacts)
    source = artifacts / "runs" / "old"
    snapshot_path = artifacts / "snapshots" / "snapshot"
    quality = {"status": "passed", "qualification": {"mode": "formal"}}
    atomic_json(snapshot_path / "data_quality.json", quality)
    manifest = {"snapshot_id": "snapshot", "qualification": quality["qualification"],
        "spec": {"holdout_start": "2026-01-01"},
        "files": {"data_quality.json": file_hash(snapshot_path / "data_quality.json")}}
    atomic_json(snapshot_path / "snapshot_manifest.json", manifest)
    load_snapshot(snapshot_path)
    baseline_path = artifacts / "baseline" / "baseline_report.json"
    atomic_json(baseline_path, {"status": "completed"})
    checkpoint_path = source / "research" / "checkpoint.json"
    protocol = {"validation": {"holdout_independent": True, "holdout_start": "2026-01-01"},
        "portfolio": {"risk": .12}}
    atomic_json(checkpoint_path, {"status": "completed"})
    atomic_json(checkpoint_path.parent / "protocol.json", protocol)
    atomic_json(source / "first_loop_report.json", {
        "status": "completed", "snapshot_path": str(snapshot_path), "baseline_report": str(baseline_path),
        "checkpoint": str(checkpoint_path), "checkpoint_sha256": file_hash(checkpoint_path),
        "protocol_id": content_hash(protocol), "candidate_decisions": [],
        "formal_g0_passed": False, "holdout_evaluated": False, "investment_accepted": False})
    history_source = artifacts / "attestations" / "access-history.txt"
    history_source.parent.mkdir(parents=True, exist_ok=True)
    history_source.write_text("reviewed project and operator access history", encoding="utf-8")
    access_audit = artifacts / "attestations" / "holdout-access.json"
    atomic_json(access_audit, {"schema_version": "holdout-access-audit-v1", "start": "2026-01-01",
        "history_complete": True, "prior_selection_use": False, "prior_result_access": False,
        "reviewer": "independent-reviewer", "reviewed_at": "2026-09-26T00:00:00Z",
        "sources": [{"path": str(history_source), "sha256": file_hash(history_source)}]})
    before = {str(path): file_hash(path) for path in (access_audit, history_source)}

    result = audit_research(config, source, artifacts / "research_audits" / "audited",
        run_id="audited", holdout_access_audit=access_audit)

    assert result["stage_statuses"]["holdout_qualification"] == "eligible"
    assert result["holdout_values_read"] is False
    assert {str(path): file_hash(path) for path in (access_audit, history_source)} == before
    output = artifacts / "research_audits" / "audited"
    published = json.loads((output / "holdout_qualification.json").read_text())
    link = json.loads((output / "qualification_link_audit.json").read_text())
    assert published["status"] == "eligible"
    assert link["status"] == "legacy_recomputed"


def test_qualification_consumer_verifies_new_links_and_recomputes_legacy_without_booleans(tmp_path):
    derived = {"snapshot_id": "snapshot", "stages": {
        "data_qualification": {"status": "unknown"},
        "factor_evaluation": {"status": "not_run"}}}
    legacy = {"formal_g0_passed": False, "holdout_evaluated": False,
              "investment_accepted": False}
    assert _verify_published_qualification(legacy, tmp_path, derived)["status"] == "legacy_recomputed"
    assert _verify_published_qualification(legacy, tmp_path, derived)["legacy_booleans_used"] is False

    path = tmp_path / "qualification_report.json"
    published = {"schema_version": "research-qualification-v1", "snapshot_id": "snapshot",
                 "stages": {name: dict(stage) for name, stage in derived["stages"].items()}}
    atomic_json(path, published)
    linked = {"qualification_report": str(path), "qualification_report_sha256": file_hash(path)}
    result = _verify_published_qualification(linked, tmp_path, derived)
    assert result["status"] == "verified" and result["legacy_booleans_used"] is False
    published["stages"]["data_qualification"]["status"] = "passed"
    atomic_json(path, published)
    with pytest.raises(IntegrityError, match="hash changed"):
        _verify_published_qualification(linked, tmp_path, derived)
    linked["qualification_report_sha256"] = file_hash(path)
    with pytest.raises(IntegrityError, match="statuses differ"):
        _verify_published_qualification(linked, tmp_path, derived)


def test_terminal_campaign_audit_keeps_attempt_and_usage_uncertainty(tmp_path, monkeypatch):
    config = SimpleNamespace(artifact_root=tmp_path, research=SimpleNamespace(budget_mode="unlimited"))
    ledger = CampaignLedger(tmp_path / "research_campaigns", "five", 5)
    ledger.initialize()
    ledger.begin_trial(run_id="old-research", trial_index=0, compatibility_group_id="group", protocol_id="protocol")
    root = tmp_path / "runs" / "old"
    checkpoint = root / "research" / "checkpoint.json"
    atomic_json(checkpoint, {"status": "paused_budget", "in_progress": {"phase": "proposing"},
                             "billing": {"calls": {"pending": {"status": "reserved"}}}})
    atomic_json(root / "status.json", {"status": "failed"})
    atomic_json(root / "first_loop_report.json", {"checkpoint": str(checkpoint),
                                                 "checkpoint_sha256": file_hash(checkpoint)})
    monkeypatch.setattr("etf_ml.research.audit._live_run_processes", lambda root: {"matching_processes": []})
    before = source_hashes(ledger.root)
    result = campaign_audit(config, "five")
    row = result["uncommitted_attempts"][0]
    assert row["disposition"] == "terminal_run_uncommitted_trial"
    assert row["uncertain_calls"] == 1 and row["slot_retained"] is True
    assert result["policy"]["budget_mode"] == "unlimited"
    assert source_hashes(ledger.root) == before
    monkeypatch.setattr("etf_ml.research.audit._live_run_processes",
                        lambda root: {"matching_processes": [{"pid": 123, "create_time": 1.}]})
    assert campaign_audit(config, "five")["uncommitted_attempts"][0]["disposition"] == "live_process_observed"


def test_campaign_lineage_exposes_repeated_definitions_without_mutating_ledgers(tmp_path):
    config = SimpleNamespace(artifact_root=tmp_path)
    roots = []
    for campaign_id, protocol_id, evaluation_id in (
            ("batch-one", "protocol-1", "eval-1"), ("batch-two", "protocol-2", "eval-2")):
        ledger = CampaignLedger(tmp_path / "research_campaigns", campaign_id, 5)
        ledger.initialize()
        ledger.begin_trial(run_id="candidate", trial_index=0,
                           compatibility_group_id=protocol_id, protocol_id=protocol_id)
        ledger.record_proposal(run_id="candidate", trial_index=0, proposal_index=0,
                               definition_id="same-definition", research_group="trend",
                               compatibility_group_id=protocol_id, protocol_id=protocol_id)
        ledger.commit_trial(run_id="candidate", trial_index=0,
                            compatibility_group_id=protocol_id, protocol_id=protocol_id,
                            record={"result": {"status": "rejected"},
                                    "campaign_candidates": [{"definition_id": "same-definition",
                                        "evaluation_id": evaluation_id, "outcome": "rejected"}]})
        roots.append(ledger.root)
    open_ledger = CampaignLedger(tmp_path / "research_campaigns", "batch-open", 5)
    open_ledger.initialize()
    open_ledger.begin_trial(run_id="interrupted", trial_index=0,
                            compatibility_group_id="protocol-3", protocol_id="protocol-3")
    open_ledger.record_proposal(run_id="interrupted", trial_index=0, proposal_index=0,
                                definition_id="same-definition", research_group="trend",
                                compatibility_group_id="protocol-3", protocol_id="protocol-3")
    roots.append(open_ledger.root)
    before = {str(root): source_hashes(root) for root in roots}

    result = campaign_lineage(config)

    assert len(result["campaigns"]) == 3
    assert len(result["repeated_definitions"]) == 1
    repeated = result["repeated_definitions"][0]
    assert repeated["definition_id"] == "same-definition"
    assert {row["campaign_id"] for row in repeated["attempts"]} == {
        "batch-one", "batch-two", "batch-open"}
    assert any(row["campaign_id"] == "batch-open" and row["outcome"] == "open"
               for row in repeated["attempts"])
    open_trial = next(row for campaign in result["campaigns"] if campaign["campaign_id"] == "batch-open"
                      for row in campaign["trials"])
    assert open_trial["status"] == "open"
    assert before == {str(root): source_hashes(root) for root in roots}
