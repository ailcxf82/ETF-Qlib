"""Fetch immutable Tushare catalog responses and materialize formal ETF metadata."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from etf_ml.data.calendar import read_calendar
from etf_ml.data.tushare_formal import materialize_metadata
from etf_ml.errors import QualityError
from etf_ml.utils import FileLock, atomic_json, content_hash, file_hash


_DOCS = {
    "etf_basic": "https://tushare.pro/document/2?doc_id=385",
    "fund_basic": "https://tushare.pro/document/1?doc_id=19",
}


def _instruments(path: Path) -> list[str]:
    result = [line.split("\t")[0] for line in path.read_text(encoding="utf-8-sig").splitlines() if line]
    if not result or len(result) != len(set(result)):
        raise QualityError("Qlib ETF instrument identities are empty or duplicated")
    return result


def _read_cached(root: Path, request: dict) -> tuple[pd.DataFrame, dict] | None:
    cache = root / content_hash(request)
    manifest_path, raw_path = cache / "manifest.json", cache / "raw.parquet"
    if not manifest_path.is_file() or not raw_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("request") != request or manifest.get("raw_hash") != file_hash(raw_path):
        raise QualityError("Tushare formal metadata cache identity or response hash changed")
    return pd.read_parquet(raw_path), manifest


def _fetch(root: Path, pro, request: dict) -> tuple[pd.DataFrame, dict, bool]:
    cached = _read_cached(root, request)
    if cached is not None:
        frame, manifest = cached
        return frame, manifest, True
    cache = root / content_hash(request)
    with FileLock(root / ".locks" / (content_hash(request) + ".lock")):
        cached = _read_cached(root, request)
        if cached is not None:
            frame, manifest = cached
            return frame, manifest, True
        frame = getattr(pro, request["api"])(**request["params"])
        if not isinstance(frame, pd.DataFrame):
            raise QualityError("Tushare catalog response is not a table")
        cache.mkdir(parents=True, exist_ok=True)
        raw_path = cache / "raw.parquet"
        frame.to_parquet(raw_path, index=False)
        manifest = {"request": request, "rows": len(frame), "raw_hash": file_hash(raw_path),
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "source_url": _DOCS[request["api"]]}
        atomic_json(cache / "manifest.json", manifest)
    return frame, manifest, False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instruments", type=Path, default=Path("D:/qlib_data/etf_qlib_data/instruments/all.txt"))
    parser.add_argument("--calendar", type=Path, default=Path("artifacts/data_evidence/b2e86cde671f974ee89f4f7e9f982d86e50d9c430fae4ad1c767a8c9b325ea4c/trusted_calendar.txt"))
    parser.add_argument("--output-root", type=Path, default=Path("artifacts/formal_tushare/metadata"))
    args = parser.parse_args()
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        print(json.dumps({"status": "blocked", "reason": "missing_tushare_token"}))
        return 5
    import tushare as ts

    root = args.output_root.resolve()
    raw_root = root / "raw"
    pro = ts.pro_api(token, timeout=20)
    requests = {
        "etf_listed": {"api": "etf_basic", "params": {"list_status": "L"}},
        "etf_delisted": {"api": "etf_basic", "params": {"list_status": "D"}},
        "fund_listed": {"api": "fund_basic", "params": {"market": "E", "status": "L"}},
        "fund_delisted": {"api": "fund_basic", "params": {"market": "E", "status": "D"}},
    }
    frames, evidence, reused = {}, [], 0
    for name, request in requests.items():
        frame, manifest, hit = _fetch(raw_root, pro, request)
        frames[name] = frame
        reused += int(hit)
        raw_path = raw_root / content_hash(request) / "raw.parquet"
        evidence.append({"name": name, "request": request, "path": str(raw_path.resolve()),
                         "raw_sha256": manifest["raw_hash"], "manifest": str((raw_path.parent / "manifest.json").resolve()),
                         "retrieved_at": manifest["retrieved_at"], "source_url": manifest["source_url"]})
    metadata = materialize_metadata(_instruments(args.instruments), read_calendar(args.calendar),
                                    frames["etf_listed"], frames["etf_delisted"],
                                    frames["fund_listed"], frames["fund_delisted"])
    metadata.attrs = {**metadata.attrs, "source_responses": evidence}
    root.mkdir(parents=True, exist_ok=True)
    metadata_path = root / "metadata.parquet"
    metadata.to_parquet(metadata_path, index=False)
    report = {"status": "ready", "instrument_count": len(_instruments(args.instruments)), "metadata_rows": len(metadata),
              "reused_requests": reused, "new_requests": len(requests) - reused,
              "metadata_path": str(metadata_path), "metadata_sha256": file_hash(metadata_path),
              "source_responses": evidence, "g0_passed": False,
              "next_step": "materialize complete corporate-action events and run formal first-loop readiness"}
    atomic_json(root / "metadata_report.json", report)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
