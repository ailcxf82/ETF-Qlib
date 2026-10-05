"""Read-only closure worksheets and supplemental billing evidence for old runs."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from etf_ml.errors import ConfigurationError, IntegrityError
from etf_ml.research.budget import money
from etf_ml.research.campaign import CampaignLedger
from etf_ml.research.qualification import evidence, read_object, validate_review_sources
from etf_ml.utils import atomic_json, code_hash, content_hash, ensure_within, file_hash


def reconcile_calls(calls, receipts, *, campaign_id, event_chain_head):
    """Overlay verified receipts; never mutate reservations, execution status or slots."""
    if (receipts.get("schema_version") != "campaign-billing-receipts-v1" or
            receipts.get("campaign_id") != campaign_id or
            receipts.get("event_chain_head") != event_chain_head):
        raise IntegrityError("Billing receipts belong to another campaign observation")
    rows = receipts.get("calls")
    if not isinstance(rows, list):
        raise IntegrityError("Billing receipt calls must be a list")
    result = {row["call_id"]: dict(row) for row in calls}
    seen, references = set(), set()
    for row in rows:
        if not isinstance(row, dict) or row.get("call_id") not in result:
            raise IntegrityError("Unknown billing call identity")
        call_id = row["call_id"]
        if call_id in seen:
            raise IntegrityError("Duplicate billing call")
        seen.add(call_id)
        target = result[call_id]
        if (row.get("request_hash") != target["request_hash"] or
                row.get("currency") != target["currency"]):
            raise IntegrityError("Billing request identity or currency differs")
        fields = ("actual_cost", "provider_input_tokens", "provider_output_tokens")
        supplied = {key: row.get(key) for key in fields if row.get(key) is not None}
        if not supplied:  # A generated blank worksheet is evidence of nothing.
            continue
        if not validate_review_sources(row):
            raise IntegrityError("Billing receipt needs reviewed, hash-bound source evidence")
        reference = row.get("billing_reference")
        if not isinstance(reference, str) or not reference.strip() or not row.get("mapping_basis"):
            raise IntegrityError("Billing receipt needs a unique provider reference and mapping basis")
        if reference in references:
            raise IntegrityError("Provider billing reference reused across calls")
        references.add(reference)
        for key, value in supplied.items():
            if key == "actual_cost":
                value = str(money(value))
                if target.get("maximum_cost") is not None and money(value) > money(target["maximum_cost"]):
                    raise IntegrityError("Receipt exceeds reserved cost bound; investigate overrun")
            elif type(value) is not int or value < 0:
                raise IntegrityError("Provider token counts must be nonnegative integers")
            existing = target.get(key)
            if existing is not None and (money(existing) != money(value) if key == "actual_cost" else existing != value):
                raise IntegrityError("Receipt conflicts with existing measured usage")
            target[key] = value
        target["receipt_evidence"] = row
    return list(result.values())


def accounting_disposition(policy_path, *, campaign_id, event_chain_head):
    if policy_path is None:
        return {"status": "pending", "historical_backfill_required": True}
    policy = read_object(policy_path)
    expected = {"schema_version": "historical-accounting-policy-v1", "campaign_id": campaign_id,
                "event_chain_head": event_chain_head, "decision": "defer_historical_reconciliation",
                "unknown_cost_action": "retain_unknown"}
    if (any(policy.get(key) != value for key, value in expected.items()) or
            policy.get("release_attempt_slots") is not False or policy.get("relax_research_gates") is not False or
            not policy.get("approved_by") or not policy.get("approved_on") or not policy.get("approval_text")):
        raise IntegrityError("Historical accounting policy scope or safeguards differ")
    return {"status": "deferred_by_user", "historical_backfill_required": False,
            "accounting_complete": False, "research_gates_unchanged": True,
            "attempt_slots_released": 0, "evidence": evidence(policy_path)}


def prepare_closure(config, source_run, output, *, campaign_id, billing_receipts=None, accounting_policy=None):
    """Metadata only: no provider dispatch, training, holdout claim or data reads."""
    from etf_ml.research.audit import campaign_audit

    source_run = ensure_within(Path(source_run), config.artifact_root / "runs")
    output = ensure_within(Path(output), config.artifact_root / "research_audits")
    if output.exists():
        raise ConfigurationError("Closure output already exists; use a new run id")
    protected = {}

    def capture(path):
        item = evidence(path)
        protected[item["path"]] = item["sha256"]
        return item

    report_path = source_run / "first_loop_report.json"
    report = read_object(report_path)
    capture(report_path)
    if report.get("status") not in {"completed", "failed", "incomplete"}:
        raise ConfigurationError("Closure preparation requires a terminal source report")
    checkpoint = ensure_within(Path(report["checkpoint"]), source_run)
    if capture(checkpoint)["sha256"] != report["checkpoint_sha256"]:
        raise IntegrityError("Source checkpoint changed")
    protocol_path = checkpoint.parent / "protocol.json"
    protocol = read_object(protocol_path)
    capture(protocol_path)
    if content_hash(protocol) != report["protocol_id"]:
        raise IntegrityError("Source protocol changed")
    snapshot_path = ensure_within(Path(report["snapshot_path"]), config.artifact_root)
    snapshot_manifest = snapshot_path / "snapshot_manifest.json"
    snapshot = read_object(snapshot_manifest)
    snapshot_ref = capture(snapshot_manifest)
    if snapshot.get("snapshot_id") != protocol.get("snapshot_id"):
        raise IntegrityError("Source snapshot differs from protocol")

    audit = campaign_audit(config, campaign_id)
    campaign_root = config.artifact_root / "research_campaigns" / campaign_id
    capture(campaign_root / "campaign.json")
    ledger = CampaignLedger(campaign_root.parent, campaign_id, audit["summary"]["max_attempts"])
    summary, events = ledger.audit_view()
    if summary["event_chain_head"] != audit["event_chain_head"]:
        raise IntegrityError("Campaign changed during closure preparation")
    disposition = accounting_disposition(accounting_policy, campaign_id=campaign_id,
                                        event_chain_head=summary["event_chain_head"])
    if accounting_policy:
        capture(accounting_policy)
    for event in events:
        capture(campaign_root / "events" / (event["event_id"] + ".json"))
    for row in audit["uncommitted_attempts"]:
        for item in row["evidence"]:
            if capture(item["path"])["sha256"] != item["sha256"]:
                raise IntegrityError("Trial evidence changed during closure preparation")

    calls, event_calls, missing_ledgers = {}, {}, []
    run_ids = {event["run_id"] for event in events if event["event_type"] == "trial_started"}
    if source_run.name + "-research" not in run_ids:
        raise IntegrityError("Source run is not a member of this campaign")
    for event in events:
        for call in event.get("calls", []):
            event_calls[call["call_id"]] = call
    for run_id in sorted(run_ids):
        run_root = ensure_within(config.artifact_root / "runs" / run_id.removesuffix("-research"),
                                 config.artifact_root / "runs")
        billing_path = run_root / "research" / "llm" / "billing.json"
        if not billing_path.is_file():
            missing_ledgers.append(run_id)
            continue  # Deterministic or pre-dispatch failure; absence is not zero usage.
        billing = read_object(billing_path)
        billing_ref = capture(billing_path)
        for call_id, call in billing["calls"].items():
            if call_id in calls:
                raise IntegrityError("Call identity occurs in multiple run ledgers")
            usage = call.get("usage") or {}
            attempts = usage.get("attempts") or []
            # Legacy APIBackend used one physical attempt. Multi-attempt totals require separate evidence.
            tokens = attempts[0] if len(attempts) == 1 else {}
            calls[call_id] = {"call_id": call_id, "run_id": run_id,
                "request_hash": call["request_hash"], "response_hash": call.get("response_hash"),
                "execution_status": call["status"], "currency": billing["currency"],
                "actual_cost": call["actual_cost"], "maximum_cost": call.get("maximum_cost"),
                "provider_input_tokens": tokens.get("provider_input_tokens"),
                "provider_output_tokens": tokens.get("provider_output_tokens"),
                "provider": usage.get("provider"), "stage": usage.get("stage"),
                "attempt_count": len(attempts), "source": billing_ref}
    for call_id, call in event_calls.items():
        if call_id not in calls or call.get("actual_cost") != calls[call_id]["actual_cost"]:
            raise IntegrityError("Campaign usage differs from run billing; reconcile source evidence first")
    template = {"schema_version": "campaign-billing-receipts-v1", "campaign_id": campaign_id,
        "event_chain_head": summary["event_chain_head"], "calls": [
            {"call_id": row["call_id"], "request_hash": row["request_hash"], "currency": row["currency"],
             "actual_cost": None, "provider_input_tokens": None, "provider_output_tokens": None,
             "billing_reference": None, "mapping_basis": None, "reviewer": None,
             "reviewed_at": None, "sources": []} for row in calls.values()]}
    values = list(calls.values())
    if billing_receipts:
        capture(billing_receipts)
        receipt = read_object(billing_receipts)
        values = reconcile_calls(values, receipt, campaign_id=campaign_id,
                                 event_chain_head=summary["event_chain_head"])
        for row in receipt["calls"]:
            for item in row.get("sources", []):
                if capture(item["path"])["sha256"] != item["sha256"]:
                    raise IntegrityError("Receipt source changed")
    unknown_cost = sum(row["actual_cost"] is None for row in values)
    unknown_tokens = sum(any(row[key] is None for key in ("provider_input_tokens", "provider_output_tokens"))
                         for row in values)
    subtotals = {}
    for row in values:
        currency = row["currency"]
        subtotals.setdefault(currency, Decimal(0))
        if row["actual_cost"] is not None:
            subtotals[currency] += money(row["actual_cost"])
    inventory = {"schema_version": "campaign-billing-inventory-v1", "calls": values,
        "unknown_cost_calls": unknown_cost, "provider_usage_unknown_calls": unknown_tokens,
        "known_subtotals_by_currency": {key: str(value) for key, value in subtotals.items()},
        "status": "partial" if unknown_cost or unknown_tokens or summary["open_attempts"] or missing_ledgers else "completed",
        "missing_billing_ledgers": missing_ledgers,
        "accounting_disposition": disposition,
        "historical_summary": summary, "historical_ledgers_modified": False,
        "attempt_slots_released": 0, "dispatches": 0}
    holdout_template = {"schema_version": "holdout-access-audit-v2",
        "snapshot_id": snapshot["snapshot_id"], "snapshot_manifest_sha256": snapshot_ref["sha256"],
        "start": snapshot["spec"]["holdout_start"], "end": snapshot["cutoff"],
        "history_complete": None, "prior_selection_use": None, "prior_result_access": None,
        "reviewer": None, "reviewed_at": None, "sources": [],
        "note": "Draft only. Absence of a local usage log is not proof of independence."}
    usage_root = config.artifact_root / "final_acceptance" / "usage"
    local_usage = [capture(path) for path in sorted(usage_root.rglob("*.json"))] if usage_root.exists() else []
    readiness = {"schema_version": "independent-confirmation-preparation-v1",
        "status": "not_ready", "source_protocol_id": report["protocol_id"],
        "holdout_independent_declared": protocol["validation"].get("holdout_independent") is True,
        "local_usage_records": local_usage, "local_search_scope": str(usage_root),
        "history_review_status": "unknown", "absence_proves_independence": False,
        "factor_decisions": [{"factor_id": row.get("factor_id"), "status": row.get("status")}
                             for row in report.get("candidate_decisions", [])],
        "holdout_values_read": False, "holdout_claimed": False,
        "next_steps": ["Review complete human and program access history with source hashes",
                       "Use a new protocol and matched baseline; never flip the old protocol",
                       "Require accepted research before candidate freeze and one-time confirmation",
                       "If history is unknown or exposed, pre-register future OOS after candidate freeze"]}
    for path, expected in protected.items():
        if file_hash(Path(path)) != expected:
            raise IntegrityError("Closure source changed during preparation")
    if ledger.audit_view()[0]["event_chain_head"] != summary["event_chain_head"]:
        raise IntegrityError("Campaign changed during closure preparation")
    output.mkdir(parents=True, exist_ok=False)
    for name, payload in {"campaign_audit.json": audit, "billing_inventory.json": inventory,
                          "billing_receipts.template.json": template,
                          "holdout_access_audit.template.json": holdout_template,
                          "independent_confirmation_preparation.json": readiness}.items():
        atomic_json(output / name, payload)
    manifest = {"schema_version": "research-closure-preparation-v1", "status": "partial",
        "generated_at": datetime.now(timezone.utc).isoformat(), "source_hash": code_hash(),
        "campaign_id": campaign_id, "protected_sources": protected,
        "files": {path.name: file_hash(path) for path in output.iterdir()},
        "external_calls": 0, "holdout_values_read": False, "artifact_path": str(output)}
    atomic_json(output / "manifest.json", manifest)
    return manifest
