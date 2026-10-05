"""Read existing research artifacts and publish a separate evidence audit."""
from __future__ import annotations

from collections import defaultdict
import math
from pathlib import Path

import psutil

from etf_ml.artifacts import RUN_ID, environment_manifest
from etf_ml.data.snapshot import load_snapshot
from etf_ml.errors import ConfigurationError, IntegrityError
from etf_ml.research.campaign import CampaignLedger
from etf_ml.research.diagnostics import paired_daily_risk_attribution
from etf_ml.research.qualification import evidence, qualification_report, read_object
from etf_ml.utils import atomic_json, code_hash, content_hash, ensure_within, file_hash, source_hashes


def _numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _feedback_coverage(record, expected_pairs, allowed_root, *, evidence_status=None):
    """Compare evidence available in a committed trial with evidence on its memory card."""
    from etf_ml.research.memory_index import ResearchMemoryIndex

    card = record.get("research_card") if isinstance(record.get("research_card"), dict) else {}
    source = (record.get("feedback") or {}).get("by_candidate") or []
    source = source[0] if source and isinstance(source[0], dict) else {}
    source_obs = source.get("observations") or {}
    summary = card.get("feedback_summary") if isinstance(card.get("feedback_summary"), dict) else {}
    delivered_rows = summary.get("by_candidate") or []
    delivered = delivered_rows[0] if delivered_rows and isinstance(delivered_rows[0], dict) else {}
    pairs = lambda rows: {(row.get("fold"), row.get("seed")) for row in rows
                          if isinstance(row, dict) and isinstance(row.get("fold"), str)
                          and _numeric(row.get("seed"))}
    expected = set(expected_pairs)

    source_deltas = source.get("paired_deltas") or []
    delivered_deltas = delivered.get("paired_deltas") or []
    source_risk_rows = (source_obs.get("portfolio_metrics") or {}).get("by_fold") or []
    delivered_risk_rows = (delivered.get("risk") or {}).get("by_fold") or []
    source_cost_rows = ((source_obs.get("robustness") or {}).get("cost_stress") or [])
    delivered_cost_rows = delivered.get("cost_stress") or delivered.get("cost_stress_by_fold") or []
    source_ablation = source.get("group_ablation") or {}
    delivered_ablation = delivered.get("group_ablation") or {}
    source_exec = source_obs.get("signal_to_execution") or {}
    delivered_exec = delivered.get("signal_to_execution") or {}
    evaluation_applicable = card.get("economic_status") in {"accepted", "rejected", "inconclusive"}

    def matrix_count(rows, predicate):
        return len(pairs([row for row in rows if predicate(row)]))

    def status(source_count, delivered_count, required):
        if required == 0:
            return "not_applicable"
        if delivered_count >= required:
            return "present"
        if delivered_count:
            return "partial"
        if source_count >= required:
            return "not_transmitted"
        if source_count:
            return "upstream_partial"
        return "missing"

    reasons = delivered.get("decision_reasons") or []
    source_reasons = source.get("reasons") or []
    expected_count = len(expected) if evaluation_applicable else 0
    delivered_pairs = pairs(delivered_deltas)
    fields = {}
    fields["decision_reasons"] = {"source_count": len(source_reasons), "delivered_count": len(reasons),
        "expected_count": int(evaluation_applicable),
        "status": status(bool(source_reasons), bool(reasons), int(evaluation_applicable))}
    fields["fold_seed_matrix"] = {"source_count": len(pairs(source_deltas)), "delivered_count": len(delivered_pairs),
                                  "expected_count": expected_count,
                                  "status": status(len(pairs(source_deltas)), len(delivered_pairs), expected_count)}
    source_risk = matrix_count(source_risk_rows, lambda row: all(_numeric((row.get(kind) or {}).get("max_drawdown"))
                                                                  for kind in ("baseline", "candidate")))
    delivered_risk = matrix_count(delivered_risk_rows, lambda row: all(_numeric(row.get(key)) for key in
                                    ("baseline_value", "candidate_value", "incremental_change"))
                                    and row.get("scenario") == "base")
    fields["baseline_candidate_risk_and_delta"] = {"source_count": source_risk,
        "delivered_count": delivered_risk, "expected_count": expected_count,
        "status": status(source_risk, delivered_risk, expected_count)}
    source_cost = sum(1 for row in source_cost_rows if isinstance(row, dict) and
                      all(_numeric((row.get(kind) or {}).get(key)) for kind in ("baseline", "candidate")
                          for key in ("excess_return", "total_execution_cost")))
    delivered_cost = sum(1 for row in delivered_cost_rows if isinstance(row, dict) and (
        all(_numeric(row.get(key)) for key in ("baseline_excess_return", "candidate_excess_return",
                                                "baseline_execution_cost", "candidate_execution_cost")) or
        all(_numeric(row.get(key)) for key in
            ("median_excess_return_delta", "median_execution_cost_delta"))))
    cost_expected = len({(row.get("fold"), row.get("multiplier")) for row in source_cost_rows
                         if isinstance(row, dict)}) if source_cost_rows else expected_count
    if delivered_cost_rows and "cost_stress_by_fold" not in delivered:
        cost_expected = len(delivered_cost_rows)
    fields["cost_stress_returns_and_costs"] = {"source_count": source_cost,
        "delivered_count": delivered_cost, "expected_count": cost_expected,
        "status": status(source_cost, delivered_cost, cost_expected)}
    source_turnover = matrix_count(source_deltas, lambda row: _numeric(row.get("turnover")))
    delivered_turnover = matrix_count(delivered_deltas, lambda row: _numeric(row.get("turnover")))
    fields["turnover_delta"] = {"source_count": source_turnover, "delivered_count": delivered_turnover,
        "expected_count": expected_count, "status": status(source_turnover, delivered_turnover, expected_count)}
    ablation_expected = expected_count
    source_ablation_rows = source.get("group_paired_deltas") or []
    delivered_ablation_rows = delivered_ablation.get("paired_deltas") or []
    source_ablation_count = matrix_count(source_ablation_rows, lambda row: _numeric(row.get("excess_return")))
    delivered_ablation_count = matrix_count(delivered_ablation_rows, lambda row: _numeric(row.get("excess_return")))
    source_ablation_complete = source_ablation.get("status") in {"accepted", "rejected", "not_applicable"}
    delivered_ablation_complete = delivered_ablation.get("status") in {"accepted", "rejected", "not_applicable"}
    source_ablation_count += int(source_ablation_complete and evaluation_applicable)
    delivered_ablation_count += int(delivered_ablation_complete and evaluation_applicable)
    ablation_expected += int(evaluation_applicable)
    fields["group_ablation"] = {"source_count": source_ablation_count,
        "delivered_count": delivered_ablation_count, "expected_count": ablation_expected,
        "status": status(source_ablation_count, delivered_ablation_count, ablation_expected)}
    source_exec_rows = source_exec.get("by_fold") or []
    delivered_exec_rows = delivered_exec.get("by_fold") or []
    source_exec_count = len(pairs(source_exec_rows))
    delivered_exec_count = len(pairs(delivered_exec_rows))
    fields["signal_to_execution"] = {"source_count": source_exec_count,
        "delivered_count": delivered_exec_count, "expected_count": expected_count,
        "status": status(source_exec_count, delivered_exec_count, expected_count)}
    unknown_codes = delivered.get("unknown_evidence")
    fields["explicit_unknown_reasons"] = {"source_count": 1 if source_obs else 0,
        "delivered_count": 1 if isinstance(unknown_codes, list) else 0,
        "expected_count": int(evaluation_applicable),
        "status": "present" if isinstance(unknown_codes, list) and evaluation_applicable else
                  "not_applicable" if not evaluation_applicable else
                  "missing" if source_obs else "upstream_partial"}
    evidence_rows = card.get("evaluation_evidence") or []
    valid_hashes = sum(1 for row in evidence_rows if isinstance(row, dict) and
                       isinstance(row.get("path"), str) and isinstance(row.get("sha256"), str) and
                       len(row["sha256"]) == 64)
    delivered_hashes = card.get("evaluation_evidence_hashes") or []
    delivered_hash_count = sum(1 for row in delivered_hashes if isinstance(row, dict) and
                               isinstance(row.get("name"), str) and isinstance(row.get("sha256"), str) and
                               len(row["sha256"]) == 64)
    evidence_verified, evidence_reason = (evidence_status if evidence_status is not None else
        ResearchMemoryIndex._evaluation_evidence_status(card, allowed_root))
    evidence_expected = (valid_hashes or 1) if evaluation_applicable else 0
    fields["evaluation_evidence_hashes"] = {"source_count": valid_hashes,
        "delivered_count": delivered_hash_count, "expected_count": evidence_expected,
        "status": "not_applicable" if not evaluation_applicable else
                  "present" if evidence_verified and delivered_hash_count >= evidence_expected else
                  "not_transmitted" if evidence_verified and valid_hashes else
                  "upstream_partial" if valid_hashes else "missing", "reason": evidence_reason}
    failure = (card.get("failure_category") or card.get("reasons") or
               (record.get("feedback") or {}).get("reason"))
    fields["technical_failure_reason"] = {"source_count": int(bool(failure)),
        "delivered_count": int(bool(card.get("failure_category") or card.get("reasons"))),
        "expected_count": int(not evaluation_applicable),
        "status": "present" if not evaluation_applicable and (card.get("failure_category") or card.get("reasons")) else
                  "not_applicable" if evaluation_applicable else "missing"}
    expected_definition = content_hash({key: value for key, value in card.items() if key != "card_hash"})
    fields["card_integrity"] = {"source_count": int(bool(card)),
        "delivered_count": int(card.get("card_hash") == expected_definition), "expected_count": 1,
        "status": "present" if card.get("card_hash") == expected_definition else "missing"}
    return {"trial_id": card.get("trial_id"), "factor_id": card.get("factor_id"),
            "economic_status": card.get("economic_status"),
            "feedback_schema": summary.get("schema_version"), "expected_fold_seed_pairs": expected_count,
            "fields": fields}


def feedback_coverage_audit(config, output, *, run_id):
    """Publish a hash-checked, read-only audit of registered committed feedback cards."""
    from etf_ml.research.memory_index import (ResearchMemoryIndex,
        _evaluation_evidence_hash_projection, _memory_feedback_summary)

    if not RUN_ID.fullmatch(run_id):
        raise ConfigurationError("Invalid audit run id")
    output = ensure_within(Path(output), config.artifact_root / "research_audits")
    if output.exists():
        raise ConfigurationError("Audit output already exists; use a new audit run id")
    memory_root = config.artifact_root / "research_memory" / "v2"
    sources_path = memory_root / "sources.json"
    sources_hash = file_hash(sources_path)
    sources = ResearchMemoryIndex(memory_root)._sources()
    if not sources:
        raise ConfigurationError("No explicitly registered research-memory sources")
    trials, protected = [], {str(sources_path.resolve()): sources_hash}
    seen = set()
    for research_root, allowed_root in sources:
        research_root = ensure_within(research_root, allowed_root)
        sessions = research_root / "sessions"
        if not sessions.is_dir():
            raise IntegrityError("Registered research-memory source has no sessions directory")
        for checkpoint_path in sorted(sessions.glob("*/checkpoint.json")):
            checkpoint_hash = file_hash(checkpoint_path)
            state = read_object(checkpoint_path)
            committed = state.get("trials")
            if not isinstance(committed, list):
                raise IntegrityError("Research checkpoint has invalid committed trial list")
            session_root = checkpoint_path.parent
            protocol_path = session_root / "protocol.json"
            protocol = read_object(protocol_path)
            protocol_hash = content_hash(protocol)
            folds = [row.get("name") for row in (protocol.get("validation") or {}).get("folds", [])
                     if isinstance(row, dict) and isinstance(row.get("name"), str)]
            seeds = (protocol.get("research") or {}).get("seeds") or []
            expected_pairs = [(fold, seed) for fold in folds for seed in seeds if _numeric(seed)]
            for item in committed:
                if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("sha256"), str):
                    raise IntegrityError("Committed research trial entry is invalid")
                trial_path = ensure_within(session_root / item["path"], session_root)
                if not trial_path.is_file() or file_hash(trial_path) != item["sha256"]:
                    raise IntegrityError("Committed research trial evidence changed")
                record = read_object(trial_path)
                card = record.get("research_card") if isinstance(record.get("research_card"), dict) else {}
                trial_id = card.get("trial_id") or f"{research_root.name}:{trial_path.name}"
                if trial_id in seen:
                    raise IntegrityError("Committed trial appears in multiple registered sources")
                seen.add(trial_id)
                evidence_status = ResearchMemoryIndex._evaluation_evidence_status(card, allowed_root)
                coverage = _feedback_coverage(record, expected_pairs, allowed_root,
                                              evidence_status=evidence_status)
                persisted_fields = coverage["fields"]
                projected_summary, rebuilt = _memory_feedback_summary(record, card)
                projected_card = {**card, "feedback_summary": projected_summary,
                    "evaluation_evidence_hashes": _evaluation_evidence_hash_projection(card, allowed_root,
                        verified=evidence_status[0])}
                projected_card["card_hash"] = content_hash({key: value for key, value in projected_card.items()
                                                              if key != "card_hash"})
                projected_record = {**record, "research_card": projected_card}
                projection = _feedback_coverage(projected_record, expected_pairs, allowed_root,
                                                evidence_status=evidence_status)
                projection["fields"]["card_integrity"] = persisted_fields["card_integrity"]
                coverage["persisted_fields"] = persisted_fields
                coverage["fields"] = projection["fields"]
                coverage["feedback_summary_rebuilt_for_memory"] = rebuilt
                coverage["memory_feedback_schema"] = projected_summary.get("schema_version")
                coverage["source"] = {"research_root": str(research_root),
                    "checkpoint": str(checkpoint_path), "checkpoint_sha256": checkpoint_hash,
                    "trial": str(trial_path), "trial_sha256": item["sha256"],
                    "protocol_sha256": file_hash(protocol_path), "protocol_id": protocol_hash}
                trials.append(coverage)
                protected[str(trial_path.resolve())] = item["sha256"]
                for item in card.get("evaluation_evidence", []):
                    if isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("sha256"), str):
                        evidence_path = ensure_within(Path(item["path"]), allowed_root)
                        if evidence_path.is_file() and file_hash(evidence_path) == item["sha256"]:
                            protected[str(evidence_path.resolve())] = item["sha256"]
            if file_hash(checkpoint_path) != checkpoint_hash:
                raise IntegrityError("Research checkpoint changed during feedback audit")
            protected[str(checkpoint_path.resolve())] = checkpoint_hash
            protected[str(protocol_path.resolve())] = file_hash(protocol_path)
    if file_hash(sources_path) != sources_hash:
        raise IntegrityError("Research-memory source manifest changed during feedback audit")
    for source_path, expected_hash in protected.items():
        if file_hash(Path(source_path)) != expected_hash:
            raise IntegrityError("Registered research evidence changed during feedback audit")

    def aggregate(rows):
        result = {}
        for field in sorted({name for trial in rows for name in trial["fields"]}):
            values = [trial["fields"][field] for trial in rows if field in trial["fields"]]
            result[field] = {"trials": len(values),
                **{status_name: sum(row["status"] == status_name for row in values)
                   for status_name in ("present", "partial", "not_transmitted", "upstream_partial",
                                       "missing", "not_applicable")}}
        return result

    aggregates = aggregate(trials)
    persisted_aggregates = aggregate([{**trial, "fields": trial["persisted_fields"]} for trial in trials])
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": "feedback-coverage-audit-v1", "run_id": run_id,
        "status": "completed", "scope": "checkpoint_committed_trials_in_explicit_research_memory_sources",
        "source_manifest": evidence(sources_path), "registered_source_count": len(sources),
        "committed_trial_count": len(trials), "field_coverage": aggregates,
        "persisted_card_field_coverage": persisted_aggregates,
        "by_trial": trials, "protected_sources": protected,
        "source_code_hash": code_hash(), "environment": environment_manifest(),
        "provider_dispatches": 0, "holdout_values_read": False}
    atomic_json(output / "feedback_coverage_report.json", report)
    atomic_json(output / "audit_report.json", {"schema_version": "research-feedback-audit-v1",
        "run_id": run_id, "status": "completed", "report": evidence(output / "feedback_coverage_report.json"),
        "committed_trials": len(trials), "provider_dispatches": 0, "holdout_values_read": False})
    return report


def campaign_lineage(config):
    """Read all append-only campaign ledgers and expose cross-campaign repeats."""
    root = Path(config.artifact_root) / "research_campaigns"
    if not root.is_dir():
        return {"schema_version": "campaign-lineage-v1", "campaigns": [],
                "repeated_definitions": []}
    campaigns, definitions = [], defaultdict(list)
    for metadata_path in sorted(root.glob("*/campaign.json")):
        metadata = read_object(metadata_path)
        campaign_id = metadata_path.parent.name
        if metadata.get("campaign_id") != campaign_id:
            raise IntegrityError("Campaign directory differs from its immutable identity")
        ledger = CampaignLedger(root, campaign_id, metadata.get("max_attempts"))
        summary, events = ledger.audit_view()
        starts, proposals, commits = {}, defaultdict(list), {}
        for event in events:
            trial_key = event.get("trial_key")
            if event["event_type"] == "trial_started":
                starts[trial_key] = event
            elif event["event_type"] == "proposal_generated":
                proposals[trial_key].append(event)
            elif event["event_type"] == "trial_committed":
                commits[trial_key] = event
        trials = []
        for trial_key, started in sorted(starts.items()):
            committed = commits.get(trial_key)
            candidates = [{"definition_id": row.get("definition_id"),
                           "evaluation_id": row.get("evaluation_id"),
                           "outcome": row.get("outcome")}
                          for row in (committed.get("candidate_evaluations") or [])] if committed else []
            if not candidates and committed and committed.get("definition_id"):
                candidates = [{"definition_id": committed["definition_id"],
                               "evaluation_id": committed.get("evaluation_id"),
                               "outcome": committed.get("outcome")}]
            row = {"trial_key": trial_key, "run_id": started["run_id"],
                   "protocol_id": started["protocol_id"],
                   "mechanism_slot": started.get("mechanism_slot"),
                   "proposals": [{"definition_id": event.get("definition_id"),
                                  "research_group": event.get("research_group"),
                                  "mechanism_slot": event.get("mechanism_slot"),
                                  "proposal_index": event.get("proposal_index")}
                                 for event in sorted(proposals[trial_key],
                                                     key=lambda item: item.get("proposal_index", 0))],
                   "candidates": candidates,
                   "status": "committed" if committed else "open"}
            trials.append(row)
            references = {}
            for proposal in proposals[trial_key]:
                definition_id = proposal.get("definition_id")
                if definition_id:
                    references[definition_id] = {"campaign_id": campaign_id,
                        "trial_key": trial_key, "protocol_id": started["protocol_id"],
                        "outcome": "open" if not committed else "proposed",
                        "evaluation_id": None}
            for candidate in candidates:
                definition_id = candidate.get("definition_id")
                if definition_id:
                    references[definition_id] = {"campaign_id": campaign_id,
                        "trial_key": trial_key, "protocol_id": started["protocol_id"],
                        "outcome": candidate.get("outcome"),
                        "evaluation_id": candidate.get("evaluation_id")}
            for definition_id, reference in references.items():
                definitions[definition_id].append(reference)
        campaigns.append({"campaign_id": campaign_id, "ledger_sha256": file_hash(metadata_path),
                          "event_chain_head": summary["event_chain_head"],
                          "attempts": summary["campaign_attempted_trials"],
                          "attempt_cap": summary["max_attempts"], "trials": trials})
    repeated = [{"definition_id": definition_id, "attempts": attempts}
                for definition_id, attempts in sorted(definitions.items())
                if len({row["campaign_id"] for row in attempts}) > 1]
    return {"schema_version": "campaign-lineage-v1", "campaigns": campaigns,
            "repeated_definitions": repeated}


def _live_run_processes(run_root):
    """Only return identities, never command lines that could contain credentials."""
    matches, inaccessible = [], 0
    for process in psutil.process_iter(["pid", "cmdline", "create_time"]):
        try:
            command = " ".join(process.info["cmdline"] or []).lower()
            if str(run_root).lower() in command or run_root.name.lower() in command:
                matches.append({"pid": process.pid, "create_time": process.info["create_time"]})
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            inaccessible += 1
    return {"matching_processes": matches, "inaccessible_processes": inaccessible,
            "scope": "run_path_or_id_in_command_line_not_exhaustive_process_ownership"}


def campaign_audit(config, campaign_id):
    root = ensure_within(config.artifact_root / "research_campaigns" / campaign_id,
                         config.artifact_root / "research_campaigns")
    metadata = read_object(root / "campaign.json")
    ledger = CampaignLedger(root.parent, campaign_id, metadata["max_attempts"])
    summary, events = ledger.audit_view()
    committed = {row["trial_key"] for row in events if row["event_type"] == "trial_committed"}
    rows = []
    for event in events:
        if event["event_type"] != "trial_started" or event["trial_key"] in committed:
            continue
        run_id = event["run_id"]
        run_root = ensure_within(config.artifact_root / "runs" / run_id.removesuffix("-research"),
                                 config.artifact_root / "runs")
        status_path = run_root / "status.json"
        report_path = run_root / "first_loop_report.json"
        row = {"trial_key": event["trial_key"], "disposition": "uncertain", "slot_retained": True,
               "evidence": [], "process_observation": _live_run_processes(run_root)}
        if status_path.is_file() and report_path.is_file():
            status, report = read_object(status_path), read_object(report_path)
            row["evidence"] += [evidence(status_path), evidence(report_path)]
            checkpoint_path = ensure_within(Path(report["checkpoint"]), run_root)
            if file_hash(checkpoint_path) != report.get("checkpoint_sha256"):
                raise IntegrityError("Audited checkpoint differs from first-loop receipt")
            checkpoint = read_object(checkpoint_path)
            row["evidence"].append(evidence(checkpoint_path))
            row.update(run_status=status.get("status"), checkpoint_status=checkpoint.get("status"),
                       phase=(checkpoint.get("in_progress") or {}).get("phase"))
            calls = (checkpoint.get("billing") or {}).get("calls", {})
            row["uncertain_calls"] = sum(call.get("status") not in {"completed", "cost_unknown", "cancelled"}
                                         for call in calls.values())
            if row["process_observation"]["matching_processes"]:
                row["disposition"] = "live_process_observed"
            elif status.get("status") in {"failed", "cancelled", "incomplete"}:
                row["disposition"] = "terminal_run_uncommitted_trial"
        rows.append(row)
    return {"schema_version": "campaign-audit-v1", "campaign_id": campaign_id,
            "summary": summary, "uncommitted_attempts": rows,
            "cross_campaign_lineage": campaign_lineage(config),
            "evidence": [evidence(root / "campaign.json")], "event_chain_head": summary["event_chain_head"],
            "policy": {"budget_mode": config.research.budget_mode,
                       "unknown_cost": "record_unknown", "automatic_restart": False,
                       "release_attempt_slots": False}}


def audit_research(config, source_run, output, *, run_id, holdout_access_audit=None):
    if not RUN_ID.fullmatch(run_id):
        raise ConfigurationError("Invalid audit run id")
    source_run = ensure_within(Path(source_run), config.artifact_root)
    output = ensure_within(Path(output), config.artifact_root / "research_audits")
    if output.exists():
        raise ConfigurationError("Audit output already exists; use a new audit run id")
    report_path = source_run / "first_loop_report.json"
    report = read_object(report_path)
    if report.get("status") not in {"completed", "incomplete", "failed"}:
        raise ConfigurationError("Audit requires a terminal research report")
    snapshot_path = ensure_within(Path(report["snapshot_path"]), config.artifact_root)
    snapshot = load_snapshot(snapshot_path)
    checkpoint_path = ensure_within(Path(report["checkpoint"]), source_run)
    if file_hash(checkpoint_path) != report.get("checkpoint_sha256"):
        raise IntegrityError("Research checkpoint changed since report publication")
    protocol_path = checkpoint_path.parent / "protocol.json"
    protocol = read_object(protocol_path)
    if content_hash(protocol) != report.get("protocol_id"):
        raise IntegrityError("Research protocol differs from first-loop identity")
    baseline_path = ensure_within(Path(report["baseline_report"]), config.artifact_root)
    protected = {str(root): source_hashes(root) for root in (snapshot_path, baseline_path.parent, source_run)}
    source_stages = qualification_report(snapshot, report.get("candidate_decisions", []), protocol["validation"])
    qualification_link = _verify_published_qualification(report, source_run, source_stages)
    stages = source_stages
    access_audit_hashes = {}
    if holdout_access_audit is not None:
        access_path = Path(holdout_access_audit).resolve()
        audit_payload = read_object(access_path)
        access_paths = [access_path, *(Path(item["path"]).resolve() for item in audit_payload.get("sources", []))]
        access_audit_hashes = {str(path): file_hash(path) for path in access_paths}
        stages = qualification_report(snapshot, report.get("candidate_decisions", []),
            protocol["validation"], access_audit=access_path)
        if qualification_link.get("status") == "verified":
            qualification_link["status"] = "verified_with_supplemental_holdout_audit"
        qualification_link["supplemental_holdout_evidence"] = [evidence(access_path)]
    risk_rows = []
    for decision in report.get("candidate_decisions", []):
        references = decision.get("reports") or {}
        if not references.get("candidate") or not references.get("baseline"):
            continue
        candidate_path = ensure_within(Path(references["candidate"]), source_run)
        base_path = ensure_within(Path(references["baseline"]), source_run)
        candidate, baseline = read_object(candidate_path), read_object(base_path)
        left = {(row["fold"], row["seed"]): row for row in baseline["by_fold"]}
        right = {(row["fold"], row["seed"]): row for row in candidate["by_fold"]}
        if (len(left) != len(baseline["by_fold"]) or len(right) != len(candidate["by_fold"]) or
                left.keys() != right.keys()):
            raise IntegrityError("Risk audit paired fold/seed matrix differs")
        for key, row in sorted(right.items()):
            base = left[key]
            risk = paired_daily_risk_attribution(
                ensure_within(Path(base["backtest_path"]), source_run),
                ensure_within(Path(row["backtest_path"]), source_run))
            risk_rows.append({"factor_id": decision["factor_id"], "fold": key[0], "model_seed": key[1],
                              "baseline_max_drawdown": base["portfolio"]["max_drawdown"],
                              "candidate_max_drawdown": row["portfolio"]["max_drawdown"],
                              "baseline_source": evidence(base_path), "candidate_source": evidence(candidate_path),
                              **risk})
    risk_report = {"schema_version": "research-risk-audit-v1", "protocol_id": report["protocol_id"],
                   "status": "completed" if risk_rows and all(r["status"] == "completed" for r in risk_rows) else "partial",
                   "risk_limit": protocol["portfolio"].get("max_drawdown_limit", protocol["portfolio"]["risk"]),
                   "risk_trigger_limit": protocol["portfolio"]["risk"],
                   "max_drawdown_acceptance_limit": protocol["portfolio"].get("max_drawdown_limit", .12),
                   "by_fold_seed": risk_rows,
                   "strategy_changed": False}
    campaign_id = (report.get("campaign_summary") or {}).get("campaign_id")
    campaign = campaign_audit(config, campaign_id) if campaign_id else {"status": "not_applicable"}
    lineage = campaign_lineage(config)
    after = {root: source_hashes(Path(root)) for root in protected}
    if protected != after:
        raise IntegrityError("Protected research artifacts changed during audit")
    if access_audit_hashes != {path: file_hash(Path(path)) for path in access_audit_hashes}:
        raise IntegrityError("Holdout access evidence changed during audit")
    output.mkdir(parents=True, exist_ok=False)
    for name, data in (("qualification_report", stages), ("risk_attribution_report", risk_report),
                       ("qualification_link_audit", qualification_link),
                       ("campaign_audit", campaign), ("campaign_lineage", lineage),
                       ("holdout_qualification", stages["stages"]["holdout_qualification"])):
        atomic_json(output / (name + ".json"), data)
    atomic_json(output / "protected_files.json", {"before": protected, "unchanged": True})
    result = {"schema_version": "research-audit-v1", "run_id": run_id, "source_run": str(source_run),
              "status": "completed", "source_code_hash": code_hash(), "environment": environment_manifest(),
              "source_report": evidence(report_path), "protocol": evidence(protocol_path),
              "protected_files": evidence(output / "protected_files.json"), "artifacts": {
                  path.stem: evidence(path) for path in sorted(output.glob("*.json"))},
              "stage_statuses": {key: value["status"] for key, value in stages["stages"].items()},
              "risk_pairs": len(risk_rows), "external_calls": 0, "training_runs": 0,
              "holdout_values_read": False}
    atomic_json(output / "audit_report.json", result)
    return result


def _verify_published_qualification(report, source_run, derived):
    """Verify new qualification links; recompute legacy reports without trusting booleans."""
    path_value, expected_hash = (report.get("qualification_report"),
                                 report.get("qualification_report_sha256"))
    if path_value is None and expected_hash is None:
        return {"schema_version": "qualification-link-audit-v1", "status": "legacy_recomputed",
                "legacy_booleans_used": False,
                "reason_codes": ["legacy_report_has_no_qualification_link"], "evidence": []}
    if not path_value or not expected_hash:
        raise IntegrityError("Published qualification link is incomplete")
    path = ensure_within(Path(path_value), source_run)
    if file_hash(path) != expected_hash:
        raise IntegrityError("Published qualification report hash changed")
    published = read_object(path)
    if (published.get("schema_version") != "research-qualification-v1" or
            published.get("snapshot_id") != derived.get("snapshot_id")):
        raise IntegrityError("Published qualification schema or snapshot identity differs")
    names = set(derived["stages"])
    actual = {name: item.get("status") for name, item in derived["stages"].items()}
    saved_stages = published.get("stages") or {}
    saved = {name: (saved_stages.get(name) or {}).get("status") for name in names}
    if saved != actual:
        raise IntegrityError("Published qualification stage statuses differ from source evidence")
    return {"schema_version": "qualification-link-audit-v1", "status": "verified",
            "legacy_booleans_used": False, "reason_codes": [], "evidence": [evidence(path)],
            "stage_statuses": actual}
