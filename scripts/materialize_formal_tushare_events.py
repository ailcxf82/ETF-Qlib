"""Materialize formal corporate-action input from immutable Tushare caches."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from etf_ml.config import load_config
from etf_ml.data.calendar import read_calendar
from etf_ml.data.share_supplement import load_share_supplement, merge_share_events
from etf_ml.data.tushare_formal import materialize_dividends
from etf_ml.data.dividend_review import load_review, remove_reviewed_duplicates, validate_reviewed_events
from etf_ml.data.source import QlibBinSource
from etf_ml.errors import QualityError
from etf_ml.utils import atomic_json, content_hash, file_hash


def _cached_dividend(cache_root: Path, instrument: str) -> tuple[pd.DataFrame, Path, dict]:
    request = {"api": "fund_div", "params": {"ts_code": instrument}}
    root = cache_root / content_hash(request)
    manifest_path, raw_path = root / "manifest.json", root / "raw.parquet"
    if not manifest_path.is_file() or not raw_path.is_file():
        raise QualityError(f"Missing immutable Tushare fund_div response for {instrument}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("request") != request or manifest.get("raw_hash") != file_hash(raw_path):
        raise QualityError(f"Tushare fund_div cache integrity failed for {instrument}")
    return pd.read_parquet(raw_path), raw_path, manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("configs/data/tushare_formal_first_loop.yaml"))
    parser.add_argument("--cache-root", type=Path, default=Path("artifacts/tushare_supplements/etf"))
    parser.add_argument("--output-root", type=Path, default=Path("artifacts/formal_tushare/events"))
    args = parser.parse_args()
    config = load_config(args.config)
    reader = QlibBinSource(config.data.source)
    calendar = read_calendar(config.data.trusted_calendar)
    calendar = calendar[(calendar >= reader.calendar.min()) & (calendar <= reader.calendar.max())]
    metadata = pd.read_parquet(config.data.metadata_path)
    review, review_hashes = load_review(config.data.dividend_review_path)
    responses, source_hashes, applied = {}, {}, []
    for instrument in reader.instruments.instrument:
        raw, raw_path, manifest = _cached_dividend(args.cache_root, instrument)
        raw_hash = manifest["raw_hash"]
        reviewed, entries = remove_reviewed_duplicates(raw, instrument, raw_hash, review)
        responses[instrument] = reviewed
        applied.extend(entries)
        source_hashes[str(raw_path.resolve())] = raw_hash
        source_hashes[str((raw_path.parent / "manifest.json").resolve())] = file_hash(raw_path.parent / "manifest.json")
    if {entry["review_id"] for entry in applied} != {entry.review_id for entry in review.duplicates}:
        raise QualityError("Not every dividend duplicate review was applied")
    events, exclusions = materialize_dividends(responses, metadata, calendar)
    shares, share_hashes = load_share_supplement(config.data.share_events_path, calendar, reader.instruments.instrument)
    events = merge_share_events(events, shares, share_hashes, calendar, reader.instruments.instrument)
    events.attrs.update({"source": "tushare.fund_div+primary_share_announcements",
                         "source_completeness_verified": False,
                         "unmapped_event_inputs": exclusions,
                         "reviewed_duplicates": applied,
                         "original_cache_hashes": source_hashes,
                         "primary_evidence_hashes": review_hashes})
    validate_reviewed_events(events, review, review_hashes)
    if any(file_hash(Path(path)) != expected for path, expected in source_hashes.items()):
        raise QualityError("Tushare dividend sources changed while materializing formal events")
    output = args.output_root.resolve()
    output.mkdir(parents=True, exist_ok=True)
    path = output / "events.parquet"
    events.to_parquet(path, index=False)
    report = {"status": "ready", "event_rows": len(events),
              "cash_event_count": int(events.cash_per_share.gt(0).sum()),
              "share_event_count": int(events.share_multiplier.ne(1).sum()),
              "outside_scope_unmapped_inputs": exclusions,
              "events_path": str(path), "events_sha256": file_hash(path),
              "source_response_count": len(responses), "g0_passed": False,
              "next_step": "run formal readiness then build a frozen formal snapshot"}
    atomic_json(output / "events_report.json", report)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
