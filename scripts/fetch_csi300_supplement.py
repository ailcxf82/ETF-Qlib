"""Fetch only the missing CSI300 benchmark using existing Tushare access."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from etf_ml.data.calendar import read_calendar, require_calendar
from etf_ml.data.tushare_supplement import csi300_benchmark
from etf_ml.utils import FileLock, atomic_json, content_hash, file_hash


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--calendar", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output-root", type=Path, default=Path("artifacts/tushare_supplements"))
    args = parser.parse_args()
    calendar = read_calendar(args.calendar)
    calendar = calendar[(calendar >= args.start) & (calendar <= args.end)]
    require_calendar(calendar)
    request = {"source": "tushare.index_daily", "ts_code": "399300.SZ",
               "start_date": calendar.min().strftime("%Y%m%d"),
               "end_date": calendar.max().strftime("%Y%m%d"),
               "calendar_hash": file_hash(args.calendar)}
    root = args.output_root / content_hash(request)
    with FileLock(args.output_root / ".locks" / (content_hash(request) + ".lock")):
        manifest = root / "manifest.json"
        if manifest.exists():
            evidence = json.loads(manifest.read_text(encoding="utf-8"))
            if (evidence["request"] != request or
                    file_hash(root / "benchmark.parquet") != evidence["benchmark_hash"] or
                    file_hash(root / "index_daily_raw.parquet") != evidence["raw_hash"]):
                raise ValueError("Cached benchmark changed")
            print(json.dumps({"status": "succeeded", "reused": True, "rows": evidence["rows"],
                              "benchmark_path": str((root / "benchmark.parquet").resolve())}))
            return 0
        token = os.environ.get("TUSHARE_TOKEN", "").strip()
        if not token:
            print(json.dumps({"status": "blocked", "reason": "missing_tushare_token"}))
            return 5
        try:
            import tushare as ts
            raw = ts.pro_api(token, timeout=20).index_daily(
                ts_code=request["ts_code"], start_date=request["start_date"], end_date=request["end_date"])
            benchmark = csi300_benchmark(raw, calendar)
        except Exception as exc:
            # Provider error messages may contain secrets. Persist only exception type.
            print(json.dumps({"status": "failed", "exception_type": type(exc).__name__,
                              "reason": "provider_or_coverage_failure"}))
            return 5
        root.mkdir(parents=True, exist_ok=True)
        raw.to_parquet(root / "index_daily_raw.parquet", index=False)
        benchmark.to_parquet(root / "benchmark.parquet")
        atomic_json(manifest, {"request": request, "rows": len(benchmark),
                              "raw_hash": file_hash(root / "index_daily_raw.parquet"),
                              "benchmark_hash": file_hash(root / "benchmark.parquet"),
                              "g0_passed": False})
        print(json.dumps({"status": "succeeded", "reused": False, "rows": len(benchmark),
                          "benchmark_path": str((root / "benchmark.parquet").resolve())}))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
