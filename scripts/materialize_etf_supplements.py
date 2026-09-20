"""Assemble complete real Tushare caches into snapshot inputs and quality evidence."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import pandas as pd

from etf_ml.config import load_config
from etf_ml.data.action_consistency import adjustment_event_consistency
from etf_ml.data.calendar import read_calendar, require_calendar
from etf_ml.data.normalize import normalize, normalized_issues
from etf_ml.data.source import QlibBinSource
from etf_ml.data.tushare_supplement import adjustment_factors, apply_adjustment_supplement, dividend_events
from etf_ml.errors import QualityError
from etf_ml.utils import FileLock, atomic_json, code_hash, content_hash, file_hash, source_hashes, verify_files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache-root", type=Path, default=Path("artifacts/tushare_supplements/etf"))
    parser.add_argument("--output-root", type=Path, default=Path("artifacts/supplement_inputs"))
    args = parser.parse_args()
    config = load_config(args.config)
    spec = config.data
    source = spec.source.resolve()
    output = args.output_root.resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise QualityError("Supplement outputs cannot overlap the raw provider")
    reader = QlibBinSource(source)
    caches, missing = [], {"fund_adj": 0, "fund_div": 0}
    for instrument in reader.instruments.instrument:
        for api in missing:
            params = {"ts_code": instrument}
            if api == "fund_adj":
                params.update(start_date=args.start, end_date=args.end)
            request = {"api": api, "params": params}
            root = args.cache_root / content_hash(request)
            path = root / "manifest.json"
            if not path.is_file():
                missing[api] += 1
                continue
            evidence = json.loads(path.read_text(encoding="utf-8"))
            if evidence["request"] != request or file_hash(root / "raw.parquet") != evidence["raw_hash"]:
                raise QualityError("Tushare cache identity or response hash changed")
            caches.append((api, root / "raw.parquet", file_hash(path), evidence["raw_hash"]))
    if any(missing.values()):
        report = {"status": "blocked_cache", "instrument_count": len(reader.instruments),
                  "missing_responses": missing, "g0_passed": False, "external_calls": 0}
        output.mkdir(parents=True, exist_ok=True)
        atomic_json(output / "pending.json", report)
        print(json.dumps(report))
        return 5
    calendar = read_calendar(spec.trusted_calendar)
    calendar = calendar[(calendar >= pd.Timestamp(args.start)) & (calendar <= pd.Timestamp(args.end)) &
                        (calendar >= reader.calendar.min()) & (calendar <= reader.calendar.max())]
    require_calendar(calendar)
    review, review_hashes = None, {}
    if spec.dividend_review_path is not None:
        from etf_ml.data.dividend_review import load_review
        review, review_hashes = load_review(spec.dividend_review_path)
        if not {d.instrument for d in review.duplicates}.issubset(set(reader.instruments.instrument)):
            raise QualityError("Dividend review contains instruments outside the target pool")
    share_events, share_hashes = None, {}
    if spec.share_events_path is not None:
        from etf_ml.data.share_supplement import load_share_supplement
        share_events, share_hashes = load_share_supplement(spec.share_events_path, calendar, reader.instruments.instrument)
        actual_factors = {json.loads((path.parent / "manifest.json").read_text(encoding="utf-8"))["request"]["params"]["ts_code"]: raw_hash
                          for api, path, _, raw_hash in caches if api == "fund_adj"}
        if any(actual_factors.get(i) != h for i, h in share_events.attrs["source_factor_hashes"].items()):
            raise QualityError("Share conversion source factor response changed")
    originals = source_hashes(source)
    identity = {"source_hashes": originals, "cache_manifests": [item[2] for item in caches],
                "data_spec": spec.model_dump(mode="json"), "calendar_dates": list(calendar.strftime("%Y-%m-%d")),
                "code_hash": code_hash(), "dividend_review_hashes": review_hashes, "share_event_hashes": share_hashes}
    key = content_hash(identity)
    final = output / key
    with FileLock(output / ".locks" / (key + ".lock")):
        if (final / "manifest.json").exists():
            manifest = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
            if manifest["identity_hash"] != key:
                raise QualityError("Assembled input identity changed")
            verify_files(final, manifest["files"])
            report = json.loads((final / "assembly_report.json").read_text(encoding="utf-8"))
            print(json.dumps({**report, "reused": True, "input_root": str(final)}))
            return 0 if report["status"] == "ready_for_snapshot" else 5
        temporary = output / ("." + key + "." + str(time.time_ns()) + ".tmp")
        temporary.mkdir(parents=True)
        factors = pd.concat([pd.read_parquet(path) for api, path, _, _ in caches if api == "fund_adj"], ignore_index=True)
        dividend_pieces, applied_reviews = [], []
        for api, path, _, raw_hash in caches:
            if api != "fund_div":
                continue
            raw = pd.read_parquet(path)
            if review is not None:
                from etf_ml.data.dividend_review import remove_reviewed_duplicates
                instrument = json.loads((path.parent / "manifest.json").read_text(encoding="utf-8"))["request"]["params"]["ts_code"]
                raw, applied = remove_reviewed_duplicates(raw, instrument, raw_hash, review)
                applied_reviews.extend(applied)
            dividend_pieces.append(raw)
        dividends = pd.concat(dividend_pieces, ignore_index=True)
        if review is not None and {a["review_id"] for a in applied_reviews} != {d.review_id for d in review.duplicates}:
            raise QualityError("Not every dividend duplicate review was applied")
        adjustment_factors(factors)
        factors.to_parquet(temporary / "fund_adj.parquet", index=False)
        decoded = reader.read(spec.fields)
        decoded = decoded[decoded.index.get_level_values("datetime").isin(calendar)]
        decoded = apply_adjustment_supplement(decoded, temporary / "fund_adj.parquet")
        normalized = normalize(decoded, spec)
        fields = normalized_issues(normalized)
        try:
            events = dividend_events(dividends, calendar, reader.instruments.instrument.tolist())
        except (QualityError, ValueError, TypeError) as error:
            if any(file_hash(path) != raw_hash or file_hash(path.parent / "manifest.json") != manifest_hash
                   for _, path, manifest_hash, raw_hash in caches):
                raise QualityError("Tushare cache changed during failed dividend mapping")
            if source_hashes(source) != originals:
                raise QualityError("Raw provider changed during failed dividend mapping")
            report = {"status": "blocked_dividend_mapping", "instrument_count": len(reader.instruments),
                      "full_target_cache": True, "quoted_rows": int(normalized.quoted.sum()),
                      "factor_rows": len(factors), "field_issues": fields,
                      "mapping_failure": {"exception_type": type(error).__name__, "message": str(error)},
                      "reviewed_duplicates": applied_reviews,
                      "raw_source_unchanged": True, "g0_passed": False,
                      "qualified_inputs_published": False,
                      "diagnostic_root": str(temporary),
                      "next_step": "resolve identified dividend source/scope anomalies before full assembly"}
            atomic_json(temporary / "assembly_report.json", report)
            atomic_json(output / "pending.json", report)
            print(json.dumps(report))
            return 5
        if share_events is not None:
            from etf_ml.data.share_supplement import merge_share_events
            events = merge_share_events(events, share_events, share_hashes, calendar, reader.instruments.instrument)
        events.attrs["reviewed_duplicates"] = applied_reviews
        events.attrs["primary_evidence_hashes"] = review_hashes
        if review is not None:
            from etf_ml.data.dividend_review import validate_reviewed_events
            validate_reviewed_events(events, review, review_hashes)
        events.to_parquet(temporary / "events.parquet", index=False)
        consistency = adjustment_event_consistency(normalized, events, calendar, reference_close_tick=spec.reference_close_tick)
        if any(file_hash(path) != raw_hash or file_hash(path.parent / "manifest.json") != manifest_hash
               for _, path, manifest_hash, raw_hash in caches):
            raise QualityError("Tushare cache changed while assembling inputs")
        if any(file_hash(Path(path)) != expected for path, expected in {**review_hashes, **share_hashes}.items()):
            raise QualityError("Primary dividend review evidence changed during assembly")
        if source_hashes(source) != originals:
            raise QualityError("Raw provider changed while assembling supplements")
        report = {"status": "ready_for_snapshot" if not fields and consistency["status"] == "passed" else "failed_quality",
                  "instrument_count": len(reader.instruments), "quoted_rows": int(normalized.quoted.sum()),
                  "factor_rows": len(factors), "cash_event_count": int(events.cash_per_share.gt(0).sum()),
                  "share_event_count": int(events.share_multiplier.ne(1).sum()), "dividend_mapping": events.attrs,
                  "field_issues": fields, "adjustment_validation": consistency,
                  "raw_source_unchanged": True, "g0_passed": False,
                  "next_step": "supply PIT membership and frozen policies before build-data"}
        atomic_json(temporary / "assembly_report.json", report)
        files = {path.name: file_hash(path) for path in temporary.iterdir() if path.is_file()}
        atomic_json(temporary / "manifest.json", {"identity_hash": key, "files": files})
        os.replace(temporary, final)
        print(json.dumps({**report, "reused": False, "input_root": str(final)}))
        return 0 if report["status"] == "ready_for_snapshot" else 5


if __name__ == "__main__":
    raise SystemExit(main())
