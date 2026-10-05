"""Evidence-bound stage statuses; legacy first-loop booleans are not evidence."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from etf_ml.errors import IntegrityError
from etf_ml.utils import content_hash, file_hash, verify_files


def read_object(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise IntegrityError(f"Evidence must be a JSON object: {path}")
    return value


def evidence(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": file_hash(path)}


def validate_review_sources(record):
    """An explicit human review plus intact sources, never an inferred attestation."""
    if (not isinstance(record.get("reviewer"), str) or not record["reviewer"].strip() or
            not isinstance(record.get("sources"), list) or not record["sources"]):
        return False
    try:
        reviewed = datetime.fromisoformat(record.get("reviewed_at", "").replace("Z", "+00:00"))
        if reviewed.tzinfo is None or reviewed > datetime.now(timezone.utc):
            return False
    except (AttributeError, TypeError, ValueError):
        return False
    for item in record["sources"]:
        try:
            valid = (isinstance(item, dict) and isinstance(item.get("path"), str) and
                     isinstance(item.get("sha256"), str) and
                     Path(item["path"]).is_absolute() and file_hash(Path(item["path"])) == item["sha256"])
        except OSError:
            valid = False
        if not valid:
            raise IntegrityError("Reviewed evidence source changed or missing")
    return True


def _stage(status, reasons, sources=(), **details):
    return {"status": status, "reason_codes": sorted(set(reasons)),
            "evidence": list(sources), **details}


def data_qualification(snapshot):
    """Separate verified structure from independently verified source completeness.

    Snapshot construction already checks units, calendar, benchmark and PIT.
    Its hash-bound quality report is authoritative for those checks, but its
    legacy formal_g0_passed constant is deliberately not a qualification input.
    """
    manifest = snapshot.manifest
    quality_path = snapshot.path / "data_quality.json"
    quality = read_object(quality_path)
    expected = manifest.get("files", {}).get("data_quality.json")
    if expected != file_hash(quality_path):
        raise IntegrityError("Snapshot data-quality evidence is missing or changed")
    qualification = manifest.get("qualification", {})
    if quality.get("qualification") != qualification:
        raise IntegrityError("Snapshot and data-quality qualifications differ")
    checks = {}

    def check(name, passed, *, failed=False):
        checks[name] = "failed" if failed else "passed" if passed else "unknown"

    check("formal_mode", qualification.get("mode") == "formal",
          failed=qualification.get("mode") == "diagnostic")
    check("structural_validation", quality.get("status") == "passed",
          failed=quality.get("status") == "failed")
    errors = quality.get("normalized_errors")
    check("normalized_fields", errors == [], failed=isinstance(errors, list) and bool(errors))
    check("raw_source_unchanged", quality.get("raw_source_unchanged") is True,
          failed=quality.get("raw_source_unchanged") is False)
    for key in ("historical_membership_verified", "historical_availability_enforced"):
        check(key, qualification.get(key) is True)
    for key in ("adjustment_validation", "metadata_quote_coverage"):
        record = quality.get(key) or {}
        check(key, record.get("status") == "passed", failed=record.get("status") == "failed")
    events = quality.get("events_validation") or {}
    check("events_structure", events.get("status") in {"passed", "structurally_valid"},
          failed=events.get("status") == "failed")
    check("events_source_completeness", events.get("source_completeness_verified") is True)
    adjustment = quality.get("adjustment_validation") or {}
    check("adjustment_source_completeness", adjustment.get("source_completeness_verified") is True)
    income = quality.get("income_validation") or {}
    if income.get("enabled") is not False:
        check("income_source_completeness", income.get("source_completeness_verified") is True)
    check("assumptions_resolved", qualification.get("assumptions") == [],
          failed=bool(qualification.get("assumptions")))
    spec = manifest.get("spec") or {}
    check("declared_units", all(spec.get(key) is not None for key in
          ("volume_unit", "amount_multiplier", "price_mode", "change_unit")))
    external = manifest.get("external_hashes") or {}
    for key in ("trusted_calendar", "benchmark_path", "metadata_path"):
        path = spec.get(key)
        check(key + "_source_binding", bool(path and str(Path(path).resolve()) in external))
    status = ("failed" if "failed" in checks.values() else
              "unknown" if "unknown" in checks.values() else "passed")
    return _stage(status, [f"{key}:{value}" for key, value in checks.items() if value != "passed"],
                  [evidence(snapshot.path / "snapshot_manifest.json"), evidence(quality_path)],
                  checks=checks, legacy_formal_g0_ignored=True)


def factor_qualification(decisions, *, snapshot_id):
    rows, sources = [], []
    for candidate in decisions:
        reports = candidate.get("reports") or {}
        report_path = reports.get("candidate")
        if not report_path:
            rows.append({"factor_id": candidate.get("factor_id"), "status": "failed",
                         "reason_codes": ["candidate_evaluation_evidence_missing"]})
            continue
        path = Path(report_path).parent / "evaluation.json"
        if not path.is_file():
            rows.append({"factor_id": candidate.get("factor_id"), "status": "inconclusive",
                         "reason_codes": ["evaluation_artifact_missing"]})
            continue
        evaluation = read_object(path)
        report = read_object(report_path)
        if (report.get("snapshot_id") != snapshot_id or
                evaluation.get("protocol_id") != report.get("protocol_id") or
                evaluation.get("status") != candidate.get("status")):
            raise IntegrityError("Candidate evaluation identity or status differs from its report")
        status = evaluation.get("status")
        if status not in {"accepted", "rejected", "inconclusive", "failed"}:
            raise IntegrityError("Invalid factor evaluation status")
        sources.extend([evidence(path), evidence(report_path)])
        rows.append({"factor_id": candidate.get("factor_id"), "status": status,
                     "reason_codes": evaluation.get("reasons", [])})
    statuses = {row["status"] for row in rows}
    status = next((value for value in ("failed", "inconclusive", "accepted", "rejected")
                   if value in statuses), "not_run")
    return _stage(status, [reason for row in rows for reason in row["reason_codes"]],
                  sources, by_candidate=rows)


def holdout_qualification(validation, *, access_audit=None, snapshot_holdout_start=None,
                          snapshot_id=None, snapshot_end=None, snapshot_manifest_sha256=None,
                          require_v2=False):
    """Eligibility needs explicit attestation of history, not an empty local log."""
    if not validation.get("holdout_independent"):
        return _stage("ineligible", ["holdout_independence_not_declared"])
    if snapshot_holdout_start is None:
        return _stage("unknown", ["snapshot_holdout_boundary_missing"])
    if validation.get("holdout_start") != snapshot_holdout_start:
        return _stage("ineligible", ["validation_snapshot_holdout_boundary_mismatch"])
    if access_audit is None:
        return _stage("unknown", ["holdout_access_history_not_audited"])
    audit = read_object(access_audit)
    source = [evidence(access_audit)]
    version = audit.get("schema_version")
    if (version not in {"holdout-access-audit-v1", "holdout-access-audit-v2"} or
            audit.get("start") != validation.get("holdout_start")):
        raise IntegrityError("Holdout access audit schema or boundary differs")
    if require_v2 and version != "holdout-access-audit-v2":
        return _stage("unknown", ["holdout_snapshot_bound_audit_required"], source)
    if version == "holdout-access-audit-v2":
        expected = {"snapshot_id": snapshot_id, "end": snapshot_end,
                    "snapshot_manifest_sha256": snapshot_manifest_sha256}
        if any(value is None for value in expected.values()):
            return _stage("unknown", ["holdout_snapshot_binding_missing"], source)
        if any(audit.get(key) != value for key, value in expected.items()):
            raise IntegrityError("Holdout access audit snapshot or end boundary differs")
    if audit.get("prior_selection_use") is True or audit.get("prior_result_access") is True:
        return _stage("ineligible", ["holdout_previously_exposed"], source)
    required = {"history_complete": True, "prior_selection_use": False,
                "prior_result_access": False}
    if (any(audit.get(key) is not value for key, value in required.items()) or
            not validate_review_sources(audit)):
        return _stage("unknown", ["holdout_access_audit_incomplete"], source)
    return _stage("eligible", ["explicit_history_audit_verified"], source)


def qualification_report(snapshot, decisions, validation, *, access_audit=None):
    # This also verifies cached/provider files without loading holdout values.
    verify_files(snapshot.path, snapshot.manifest["files"])
    stages = {
        "data_qualification": data_qualification(snapshot),
        "factor_evaluation": factor_qualification(decisions, snapshot_id=snapshot.snapshot_id),
        "holdout_qualification": holdout_qualification(validation, access_audit=access_audit,
            snapshot_holdout_start=(snapshot.manifest.get("spec") or {}).get("holdout_start"),
            snapshot_id=snapshot.snapshot_id, snapshot_end=snapshot.manifest.get("cutoff"),
            snapshot_manifest_sha256=file_hash(snapshot.path / "snapshot_manifest.json")),
        "holdout_evaluation": _stage("not_run", ["research_report_does_not_evaluate_holdout"]),
        "prospective_evaluation": _stage("not_started", ["prospective_evidence_not_supplied"]),
        "investment_readiness": _stage("not_ready", ["independent_and_operational_evidence_required"]),
    }
    observed_at = datetime.now(timezone.utc).isoformat()
    for value in stages.values():
        value.update(schema_version="qualification-stage-v1", evaluated_at=observed_at)
    return {"schema_version": "research-qualification-v1", "snapshot_id": snapshot.snapshot_id,
            "stages": stages, "legacy_booleans": {
                "formal_g0_passed": stages["data_qualification"]["status"] == "passed",
                "holdout_evaluated": False, "investment_accepted": False},
            "evidence_identity": content_hash({key: value["evidence"] for key, value in stages.items()})}
