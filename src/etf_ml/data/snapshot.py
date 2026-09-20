from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import pandas as pd

from etf_ml.contracts import DataSnapshot, DataSpec, UniversePolicy
from etf_ml.data.calendar import calendar_issues, read_calendar, require_calendar
from etf_ml.data.actions import validate_events
from etf_ml.data.normalize import normalize, normalized_issues
from etf_ml.data.source import QlibBinSource, encode_provider, require_panel
from etf_ml.data.universe import build_history, validate_metadata, metadata_quote_coverage
from etf_ml.errors import ConfigurationError, IntegrityError, QualityError
from etf_ml.utils import (FileLock, atomic_json, content_hash, ensure_within,
                          file_hash, source_hashes, verify_files, code_hash)


def _publish_snapshot(temp: Path, final: Path) -> None:
    # Windows scanners/readers may briefly deny a directory rename after HDF writes.
    # Retry the same fully verified staging tree; never replace an existing snapshot.
    delays = (0.05, 0.1, 0.2, 0.4, 0.8)
    for attempt in range(len(delays) + 1):
        try:
            os.replace(temp, final)
            return
        except PermissionError as error:
            if (getattr(error, "winerror", None) not in (5, 32, 33)
                    or final.exists() or attempt == len(delays)):
                raise
            time.sleep(delays[attempt])


def audit_source(spec: DataSpec) -> dict:
    source = QlibBinSource(spec.source)
    initial_hashes = source_hashes(spec.source)
    errors = [{"code": code} for code in calendar_issues(source.calendar)]
    trusted = None
    if spec.trusted_calendar is None:
        errors.append({"code": "unverified_authoritative_calendar"})
    else:
        trusted = read_calendar(spec.trusted_calendar)
        errors.extend({"code": "trusted_" + code} for code in calendar_issues(trusted))
        errors.extend({"code": code} for code in calendar_issues(source.calendar, trusted)
                      if code not in {e["code"] for e in errors})
    frame = source.read(spec.fields)
    if spec.adjustment_path is not None:
        from etf_ml.data.tushare_supplement import apply_adjustment_supplement
        frame = apply_adjustment_supplement(frame, spec.adjustment_path)
    revisions_validation, revision_hashes = None, {}
    if spec.source_revisions_path is not None:
        from etf_ml.data.source_revisions import apply_source_revisions
        frame, revisions_validation, revision_hashes = apply_source_revisions(frame, spec.source_revisions_path, spec)
    coverage = {name: float(frame[name].notna().mean()) for name in frame}
    for name, value in coverage.items():
        if value == 0:
            errors.append({"code": "missing_required_field", "field": name})
    for name in ("volume_unit", "amount_multiplier", "price_mode", "change_unit"):
        if getattr(spec, name) is None:
            errors.append({"code": "unverified_field_semantics", "field": name})
    normalized = None
    limits_validation = None
    limit_hashes = {}
    field_issues = []
    if all(getattr(spec, n) is not None for n in (
            "volume_unit", "amount_multiplier", "price_mode", "change_unit")):
        if not frame.index.has_duplicates:
            normalized = normalize(frame, spec)
            field_issues = normalized_issues(normalized)
            errors.extend(field_issues)
        else:
            errors.append({"code": "duplicate_panel_keys"})
    if normalized is not None and spec.limits_path is not None:
        from etf_ml.data.limit_supplement import apply_limit_supplement
        limit_calendar = trusted if trusted is not None else source.calendar
        limit_calendar = limit_calendar[(limit_calendar >= source.calendar.min()) & (limit_calendar <= source.calendar.max())]
        try:
            normalized, limits_validation, limit_hashes = apply_limit_supplement(
                normalized[normalized.index.get_level_values("datetime").isin(limit_calendar)],
                spec.limits_path, limit_calendar)
            if limits_validation["status"] != "passed":
                errors.append({"code": "daily_limits_unavailable_at_open"})
        except (QualityError, OSError, ValueError) as exc:
            limits_validation = {"status": "failed", "exception_type": type(exc).__name__}
            errors.append({"code": "invalid_daily_limits"})
    metadata = None
    if not spec.point_in_time_metadata or not spec.metadata_path:
        errors.append({"code": "unverified_historical_metadata"})
    else:
        try:
            metadata = validate_metadata(pd.read_parquet(spec.metadata_path))
        except (QualityError, OSError, ValueError) as exc:
            errors.append({"code": "invalid_historical_metadata", "type": type(exc).__name__})
    try:
        declared_events = pd.read_parquet(spec.events_path) if spec.events_path else None
        event_calendar = trusted[(trusted >= source.calendar.min()) & (trusted <= source.calendar.max())] if trusted is not None else source.calendar
        validated_events, events_validation = validate_events(declared_events, event_calendar, frame.index.get_level_values('instrument').unique())
        if spec.dividend_review_path is not None:
            from etf_ml.data.dividend_review import load_review, validate_reviewed_events
            declared_review, reviewed_sources = load_review(spec.dividend_review_path)
            validate_reviewed_events(declared_events, declared_review, reviewed_sources)
        if spec.share_events_path is not None:
            from etf_ml.data.share_supplement import load_share_supplement, validate_share_binding
            declared_shares, share_sources = load_share_supplement(spec.share_events_path, event_calendar, frame.index.get_level_values('instrument').unique())
            validate_share_binding(declared_events, declared_shares, share_sources)
    except (QualityError, OSError, ValueError) as exc:
        events_validation = {'status': 'failed', 'source_completeness_verified': False}
        errors.append({'code': 'invalid_corporate_actions', 'type': type(exc).__name__})
    metadata_coverage = {"status": "not_assessed"}
    if metadata is not None and normalized is not None and not field_issues:
        scope_frame = normalized[normalized.index.get_level_values("datetime").isin(event_calendar)]
        scope_history = build_history(scope_frame, metadata, event_calendar, UniversePolicy())
        metadata_coverage = metadata_quote_coverage(scope_frame, scope_history)
        if metadata_coverage["status"] != "passed":
            errors.append({"code": "incomplete_quoted_historical_metadata",
                           "unknown_quoted_rows": metadata_coverage["unknown_quoted_rows"],
                           "unknown_quoted_instruments": metadata_coverage["unknown_quoted_instruments"]})
    income_hashes = {}
    income_validation = {"enabled": False, "rounding_proxy": False}
    if spec.income_path is not None:
        from etf_ml.data.income_supplement import load_income_supplement
        try:
            declared_income, _, income_validation, income_hashes = load_income_supplement(
                spec.income_path, event_calendar, frame.index.get_level_values("instrument").unique(),
                event_calendar[0], event_calendar[-1])
            if events_validation["status"] != "failed" and validated_events.instrument.isin(declared_income.index.get_level_values("instrument").unique()).any():
                raise QualityError("Income instruments also declare unversioned cash/share actions")
            revision_hashes.update(income_hashes)
        except (QualityError, OSError, ValueError, TypeError) as exc:
            income_validation = {"enabled": True, "status": "failed", "exception_type": type(exc).__name__}
            errors.append({"code": "invalid_income_input"})
    adjustment_validation = None
    if (normalized is not None and not field_issues and events_validation['status'] != 'failed'
            and not calendar_issues(event_calendar)):
        from etf_ml.data.action_consistency import adjustment_event_consistency
        clean = normalized[normalized.index.get_level_values('datetime').isin(event_calendar)]
        adjustment_validation = adjustment_event_consistency(clean, validated_events, event_calendar, reference_close_tick=spec.reference_close_tick)
        if adjustment_validation['status'] != 'passed':
            errors.append({'code': 'adjustment_event_consistency_' + adjustment_validation['status']})
    if not spec.benchmark_path:
        errors.append({"code": "missing_csi300_benchmark"})
    if any(file_hash(Path(p)) != h for p, h in income_hashes.items()):
        raise IntegrityError("Income evidence changed during audit")
    if any(file_hash(Path(p)) != h for p, h in limit_hashes.items()):
        raise IntegrityError("Daily limit evidence changed during audit")
    if initial_hashes != source_hashes(spec.source):
        raise IntegrityError("Raw source changed during audit")
    return {"status": "passed" if not errors else "failed", "errors": errors,
            "source": str(spec.source), "source_hashes": initial_hashes,
            "calendar_start": str(source.calendar.min().date()),
            "calendar_end": str(source.calendar.max().date()),
            "instrument_count": len(source.instruments), "row_count": len(frame),
            "field_coverage": coverage, "events_validation": events_validation,
            "income_validation": income_validation,
            "metadata_quote_coverage": metadata_coverage,
            "adjustment_validation": adjustment_validation, "limits_validation": limits_validation, "source_revisions_validation": revisions_validation, "revision_hashes": revision_hashes, "raw_source_unchanged": True}


def build_snapshot(source_path: Path, spec: DataSpec,
                   universe_policy: UniversePolicy | None = None) -> DataSnapshot:
    if Path(source_path).resolve() != spec.source.resolve():
        raise ConfigurationError("Source path differs from declared spec")
    root = spec.artifact_root.resolve()
    if root.is_relative_to(spec.source.resolve()) or spec.source.resolve().is_relative_to(root):
        raise ConfigurationError("Artifact and raw source trees must not overlap")
    if not spec.trusted_calendar or not spec.metadata_path or (spec.mode == "formal" and not spec.point_in_time_metadata):
        raise ConfigurationError("Snapshot requires authoritative calendar and PIT metadata evidence")
    if not spec.benchmark_path:
        raise ConfigurationError("Snapshot requires the CSI300 benchmark")
    original = source_hashes(spec.source)
    external = {str(p.resolve()): file_hash(p) for p in
                (spec.trusted_calendar, spec.metadata_path, spec.benchmark_path, spec.events_path, spec.adjustment_path, spec.share_events_path, spec.limits_path, spec.source_revisions_path)
                if p is not None}
    if spec.dividend_review_path is not None:
        from etf_ml.data.dividend_review import load_review
        declared_review, reviewed_sources = load_review(spec.dividend_review_path)
        external.update(reviewed_sources)
    reader = QlibBinSource(spec.source)
    trusted = read_calendar(spec.trusted_calendar)
    require_calendar(trusted)
    # Duplicate source dates cannot be repaired by guessing which quote to keep.
    source_problems = calendar_issues(reader.calendar)
    if any(code in source_problems for code in ("duplicate_calendar", "unordered_calendar")):
        raise QualityError("Ambiguous original bin calendar")
    calendar = trusted[(trusted >= reader.calendar.min()) & (trusted <= reader.calendar.max())]
    require_calendar(calendar)
    if spec.share_events_path is not None:
        from etf_ml.data.share_supplement import load_share_supplement
        declared_shares, share_sources = load_share_supplement(spec.share_events_path, calendar, reader.instruments.instrument)
        external.update(share_sources)
    decoded = reader.read(spec.fields)
    if spec.adjustment_path is not None:
        from etf_ml.data.tushare_supplement import apply_adjustment_supplement
        decoded = apply_adjustment_supplement(decoded, spec.adjustment_path)
    revisions_validation = None
    if spec.source_revisions_path is not None:
        from etf_ml.data.source_revisions import apply_source_revisions
        decoded, revisions_validation, revision_sources = apply_source_revisions(decoded, spec.source_revisions_path, spec)
        external.update(revision_sources)
    clean = decoded[decoded.index.get_level_values("datetime").isin(calendar)].copy()
    require_panel(clean)
    normalized = normalize(clean, spec)
    limits_validation = None
    if spec.limits_path is not None:
        from etf_ml.data.limit_supplement import apply_limit_supplement
        normalized, limits_validation, limit_sources = apply_limit_supplement(normalized, spec.limits_path, calendar)
        external.update(limit_sources)
        if limits_validation["status"] != "passed":
            raise QualityError("Daily limits unavailable at execution open")
    issues = normalized_issues(normalized)
    if issues:
        raise QualityError("Normalized field validation failed: " +
                           ", ".join(issue["code"] for issue in issues))
    metadata = validate_metadata(pd.read_parquet(spec.metadata_path))
    if spec.mode == "formal" and metadata.attrs.get("metadata_mode", "").startswith("diagnostic"):
        raise QualityError("Current-universe assumptions cannot be relabeled as formal PIT evidence")
    if spec.mode == "diagnostic":
        from etf_ml.data.diagnostic import validate_diagnostic_metadata
        validate_diagnostic_metadata(metadata)
    benchmark = pd.read_parquet(spec.benchmark_path)
    if not isinstance(benchmark.index, pd.DatetimeIndex) or benchmark.index.has_duplicates:
        raise QualityError("Benchmark needs unique datetime index")
    if not {"open", "close"}.issubset(benchmark) or not benchmark.reindex(calendar)[["open", "close"]].notna().all().all():
        raise QualityError("CSI300 benchmark does not cover the snapshot")
    if benchmark.attrs.get("benchmark_id") != spec.benchmark_id:
        raise QualityError("Benchmark identity must be CSI300, not an ETF proxy")
    events = pd.read_parquet(spec.events_path) if spec.events_path else pd.DataFrame(
        columns=["datetime", "instrument", "cash_per_share", "share_multiplier"])
    events, events_validation = validate_events(events, calendar, normalized.index.get_level_values('instrument').unique())
    if spec.dividend_review_path is not None:
        from etf_ml.data.dividend_review import validate_reviewed_events
        validate_reviewed_events(events, declared_review, reviewed_sources)
    if spec.share_events_path is not None:
        from etf_ml.data.share_supplement import validate_share_binding
        validate_share_binding(events, declared_shares, share_sources)
    from etf_ml.data.action_consistency import adjustment_event_consistency
    adjustment_validation = adjustment_event_consistency(normalized, events, calendar, reference_close_tick=spec.reference_close_tick)
    if adjustment_validation["status"] != "passed" and spec.mode == "formal":
        raise QualityError("Adjustment/event consistency " + adjustment_validation["status"])
    income, income_validation = None, {"enabled": False, "rounding_proxy": False}
    if spec.income_path is not None:
        from etf_ml.data.income_supplement import load_income_supplement
        income, _, income_validation, income_sources = load_income_supplement(
            spec.income_path, trusted, normalized.index.get_level_values("instrument").unique(), calendar[0], calendar[-1])
        if events.instrument.isin(income.index.get_level_values("instrument").unique()).any():
            raise QualityError("Income instruments cannot also use cash/share events without a versioned unit conversion")
        external.update(income_sources)
    policy = universe_policy or UniversePolicy()
    history = build_history(normalized, metadata, calendar, policy, diagnostic=spec.mode == "diagnostic")
    metadata_coverage = metadata_quote_coverage(normalized, history)
    if metadata_coverage["status"] != "passed":
        raise QualityError("Historical metadata does not cover the full quoted scope: " +
            str(metadata_coverage["unknown_quoted_rows"]) + " unknown rows across " +
            str(metadata_coverage["unknown_quoted_instruments"]) + " instruments")
    snapshot_id = content_hash({"source": original, "external": external,
                                "spec": spec, "universe_policy": policy, "code_hash": code_hash()})
    final = ensure_within(root / snapshot_id, root)
    root.mkdir(parents=True, exist_ok=True)
    with FileLock(root / ".locks" / (snapshot_id + ".lock")):
        if final.exists():
            return load_snapshot(final)
        # The immutable ID is already protected by its lock. Keep the unique
        # staging name short enough for nested provider files on Windows.
        temp = ensure_within(root / ("." + uuid.uuid4().hex + ".tmp"), root)
        temp.mkdir()
        repairs = [{"code": code} for code in source_problems]
        repairs.append({"code": "reencoded_from_original_calendar",
                        "excluded_dates": reader.calendar.difference(calendar).strftime("%Y-%m-%d").tolist()})
        if revisions_validation is not None:
            repairs.append({"code": "explicit_source_quote_factor_revisions", "quote_rows": revisions_validation["revised_quote_rows"]})
        normalized.to_parquet(temp / "panel.parquet")
        metadata.to_parquet(temp / "metadata.parquet", index=False)
        history.to_parquet(temp / "universe.parquet")
        benchmark.to_parquet(temp / "benchmark.parquet")
        events.to_parquet(temp / "events.parquet", index=False)
        if income is not None:
            income.to_parquet(temp / "income.parquet")
        (temp / "execution_calendar.txt").write_text(
            "\n".join(trusted.strftime("%Y-%m-%d")) + "\n", encoding="utf-8")
        (temp / "calendar.txt").write_text("\n".join(calendar.strftime("%Y-%m-%d")) + "\n", encoding="utf-8")
        holdout_start = pd.Timestamp(spec.holdout_start)
        for name, mask in (
            ("research", normalized.index.get_level_values("datetime") < holdout_start),
            ("holdout", normalized.index.get_level_values("datetime") >= holdout_start)):
            view = temp / name
            view.mkdir()
            if income is not None:
                earning_days = income.index.get_level_values("datetime")
                income_view = income[earning_days < holdout_start if name == "research" else earning_days >= holdout_start].copy()
                symbols = set(income_view.index.get_level_values("instrument"))
                # Dated source documents stay in the trusted snapshot; a research
                # view carries only rules for its own earning dates, never future receipts.
                income_view.attrs = {"income_rules": {i: r for i, r in income.attrs["income_rules"].items() if i in symbols}}
                income_view.to_parquet(view / "income.parquet")
            subset = normalized[mask]
            subset.to_parquet(view / "panel.parquet")
            subset.to_hdf(view / "daily_pv.h5", key="data",
                          format="table" if len(subset) else "fixed")
            # Export only the dates of that view; no hidden full-history provider.
            view_calendar = calendar[calendar < holdout_start] if name == "research" else calendar[calendar >= holdout_start]
            if len(subset):
                qlib_frame = pd.DataFrame(index=subset.index)
                for field in ("open", "high", "low", "close"):
                    qlib_frame[field] = subset["adj_" + field]
                qlib_frame["factor"] = subset.adjustment_factor
                qlib_frame["volume"] = subset.volume_shares / subset.adjustment_factor
                qlib_frame["amount"] = subset.amount_currency
                qlib_frame["change"] = subset.return_1d
                encode_provider(qlib_frame, view_calendar, view / "provider",
                                {name: name for name in qlib_frame},
                                future_calendar=trusted[trusted >= view_calendar[0]])
        if original != source_hashes(spec.source):
            raise IntegrityError("Raw source changed during snapshot construction")
        if any(file_hash(Path(path)) != expected for path, expected in external.items()):
            raise IntegrityError("External source changed during snapshot construction")
        qualification = {"mode": spec.mode, "formal_g0_passed": False,
                         "investment_acceptance_eligible": spec.mode == "formal",
                         "historical_membership_verified": spec.point_in_time_metadata,
                         "historical_availability_enforced": spec.mode == "formal",
                         "adjustment_consistency_passed": adjustment_validation["status"] == "passed",
                         "assumptions": metadata.attrs.get("diagnostic_assumptions", []),
                         "unmapped_event_inputs": events.attrs.get("unmapped_event_inputs", [])}
        quality = {"status": "diagnostic" if spec.mode == "diagnostic" else "passed", "qualification": qualification, "normalized_errors": [], "raw_source_unchanged": True,
                   "repairs": repairs, "events_validation": events_validation, "adjustment_validation": adjustment_validation,
                   "income_validation": income_validation,
                   "metadata_quote_coverage": metadata_coverage,
                   "limits_validation": limits_validation, "source_revisions_validation": revisions_validation,
                   "field_coverage": {n: float(normalized[n].notna().mean()) for n in normalized}}
        atomic_json(temp / "data_quality.json", quality)
        manifest = {"qualification": qualification, "snapshot_id": snapshot_id, "source": str(spec.source.resolve()),
                    "source_hashes": original, "external_hashes": external,
                    "spec": spec.model_dump(mode="json"), "universe_policy": policy.model_dump(),
                    "calendar_hash": file_hash(temp / "calendar.txt"),
                    "cutoff": str(calendar[-1].date()), "repairs": repairs,
                    "code_version": "etf-ml-0.1.0", "code_hash": code_hash(), "files": source_hashes(temp)}
        atomic_json(temp / "snapshot_manifest.json", manifest)
        _publish_snapshot(temp, final)
    return load_snapshot(final)


def load_snapshot(path: Path) -> DataSnapshot:
    path = Path(path).resolve()
    manifest = json.loads((path / "snapshot_manifest.json").read_text(encoding="utf-8"))
    verify_files(path, manifest["files"])
    if path.name != manifest["snapshot_id"]:
        raise IntegrityError("Snapshot identity differs from directory")
    return DataSnapshot(manifest["snapshot_id"], path, manifest)
