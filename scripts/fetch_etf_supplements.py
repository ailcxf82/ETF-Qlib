"""Resumable real ETF factor/dividend collection using existing Tushare access."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from etf_ml.utils import FileLock, atomic_json, content_hash, file_hash


def parallel_collect(args, instruments, token):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import tushare as ts
    jobs, available = [], set()
    for instrument in instruments:
        for api in ("fund_adj", "fund_div"):
            params = {"ts_code": instrument}
            if api == "fund_adj":
                params.update(start_date=args.start, end_date=args.end)
            request = {"api": api, "params": params}
            root = args.output_root / content_hash(request)
            manifest = root / "manifest.json"
            if manifest.is_file():
                evidence = json.loads(manifest.read_text(encoding="utf-8"))
                if evidence["request"] != request or file_hash(root / "raw.parquet") != evidence["raw_hash"]:
                    raise ValueError("Cached source response changed")
                available.add((instrument, api))
            else:
                jobs.append((instrument, api, params, request, root))
    reused = len(available)
    selected = jobs[:args.max_calls]
    stop, gate, local = threading.Event(), threading.Lock(), threading.local()
    state = {"calls": 0, "next_dispatch": 0., "published": 0}

    def collect(job):
        instrument, api, params, request, root = job
        if stop.is_set():
            return None
        with FileLock(args.output_root / ".locks" / (content_hash(request) + ".lock")):
            # A concurrent process may have published this exact request.
            manifest = root / "manifest.json"
            if manifest.is_file():
                evidence = json.loads(manifest.read_text(encoding="utf-8"))
                if evidence["request"] != request or file_hash(root / "raw.parquet") != evidence["raw_hash"]:
                    raise ValueError("Cached source response changed")
                return instrument, api, None
            with gate:
                if stop.is_set():
                    return None
                delay = state["next_dispatch"] - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                if stop.is_set():
                    return None
                state["next_dispatch"] = time.monotonic() + args.interval_seconds
                state["calls"] += 1
            try:
                if not hasattr(local, "pro"):
                    local.pro = ts.pro_api(token, timeout=20)
                raw = getattr(local.pro, api)(**params)
                if api == "fund_adj" and len(raw) >= 2000:
                    raise ValueError("Possible source pagination truncation")
                if not raw.empty and ("ts_code" not in raw or not raw.ts_code.eq(instrument).all()):
                    raise ValueError("Unexpected source instrument identity")
                root.mkdir(parents=True, exist_ok=True)
                raw.to_parquet(root / "raw.parquet", index=False)
                atomic_json(manifest, {"request": request, "rows": len(raw),
                                      "raw_hash": file_hash(root / "raw.parquet"),
                                      "source_completeness_verified": False})
                with gate:
                    state["published"] += 1
                    if state["published"] % 50 == 0:
                        print(json.dumps({"status": "collecting", "calls": state["calls"],
                                          "published": state["published"], "reused": reused}), flush=True)
                return instrument, api, None
            except Exception as exc:
                stop.set()
                return instrument, api, type(exc).__name__

    failures = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for result in pool.map(collect, selected):
            if result is None:
                continue
            instrument, api, error = result
            if error:
                failures.append({"instrument": instrument, "api": api, "exception_type": error})
            else:
                available.add((instrument, api))
    completed = sum(all((instrument, api) in available for api in ("fund_adj", "fund_div"))
                    for instrument in instruments)
    report = {"status": "failed" if failures else "collected" if completed == len(instruments) else "partial",
              "calls": state["calls"], "reused": reused, "completed_instruments": completed,
              "source_completeness_verified": False, "failures": failures}
    print(json.dumps(report), flush=True)
    return 5 if failures else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--instruments", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--max-calls", type=int, default=10)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--interval-seconds", type=float, default=0.6)
    parser.add_argument("--output-root", type=Path, default=Path("artifacts/tushare_supplements/etf"))
    args = parser.parse_args()
    if args.max_calls <= 0 or args.interval_seconds < 0 or not 1 <= args.workers <= 4:
        raise ValueError("Invalid collection limits")
    instruments = [line.split("\t")[0] for line in args.instruments.read_text(encoding="utf-8-sig").splitlines()]
    if not instruments or len(set(instruments)) != len(instruments) or any(
            not code or not all(c.isalnum() or c == "." for c in code) for code in instruments):
        raise ValueError("Invalid instrument identities")
    import tushare as ts
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        print(json.dumps({"status": "blocked", "reason": "missing_tushare_token"}))
        return 5
    if args.workers > 1:
        return parallel_collect(args, instruments, token)
    pro = ts.pro_api(token, timeout=20)
    calls, reused, completed = 0, 0, 0
    for instrument in instruments:
        for api in ("fund_adj", "fund_div"):
            params = {"ts_code": instrument}
            if api == "fund_adj":
                params.update(start_date=args.start, end_date=args.end)
            request = {"api": api, "params": params}
            key = content_hash(request)
            root = args.output_root / key
            manifest = root / "manifest.json"
            with FileLock(args.output_root / ".locks" / (key + ".lock")):
                if manifest.exists():
                    evidence = json.loads(manifest.read_text(encoding="utf-8"))
                    if evidence["request"] != request or file_hash(root / "raw.parquet") != evidence["raw_hash"]:
                        raise ValueError("Cached source response changed")
                    reused += 1
                    continue
                if calls >= args.max_calls:
                    print(json.dumps({"status": "partial", "calls": calls, "reused": reused,
                                      "completed_instruments": completed, "source_completeness_verified": False}))
                    return 0
                try:
                    calls += 1
                    raw = getattr(pro, api)(**params)
                    if api == "fund_adj" and len(raw) >= 2000:
                        raise ValueError("Possible source pagination truncation")
                    if not raw.empty and ("ts_code" not in raw or not raw.ts_code.eq(instrument).all()):
                        raise ValueError("Unexpected source instrument identity")
                    root.mkdir(parents=True, exist_ok=True)
                    raw.to_parquet(root / "raw.parquet", index=False)
                    atomic_json(manifest, {"request": request, "rows": len(raw),
                                          "raw_hash": file_hash(root / "raw.parquet"),
                                          "source_completeness_verified": False})
                except Exception as exc:
                    print(json.dumps({"status": "failed", "api": api, "instrument": instrument,
                                      "exception_type": type(exc).__name__, "calls": calls}))
                    return 5
            time.sleep(args.interval_seconds)
        completed += 1
    print(json.dumps({"status": "collected", "calls": calls, "reused": reused,
                      "completed_instruments": completed, "source_completeness_verified": False,
                      "next_step": "validate factor quote coverage, implemented events and adjustment consistency"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
