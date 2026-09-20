"""Resumable ETF limit collection with one shared dispatch gate."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time

from etf_ml.data.source import QlibBinSource
from etf_ml.utils import FileLock, atomic_json, content_hash, file_hash

FIELDS = "ts_code,trade_date,up_limit,down_limit,asset_type,exchange"


def collect(args, instruments, pro_factory):
    jobs, available = [], set()
    for instrument in instruments:
        request = {"api":"etf_limit", "params":{"ts_code":instrument,
                    "start_date":args.start,"end_date":args.end,"fields":FIELDS}}
        root = args.output_root / content_hash(request)
        manifest = root / "manifest.json"
        if manifest.is_file():
            evidence = json.loads(manifest.read_text(encoding="utf-8"))
            if evidence["request"] != request or file_hash(root/"raw.parquet") != evidence["raw_hash"]:
                raise ValueError("ETF limit cache identity or response changed")
            available.add(instrument)
        else:
            jobs.append((instrument,request,root))
    reused = len(available)
    gate, stop, local = threading.Lock(), threading.Event(), threading.local()
    state = {"calls":0,"next_dispatch":0.,"published":0}

    def fetch(job):
        instrument,request,root = job
        if stop.is_set():
            return None
        with FileLock(args.output_root/".locks"/(content_hash(request)+".lock")):
            path = root/"manifest.json"
            if path.exists():
                evidence = json.loads(path.read_text(encoding="utf-8"))
                if evidence["request"] != request or file_hash(root/"raw.parquet") != evidence["raw_hash"]:
                    raise ValueError("Concurrent ETF limit cache changed")
                return instrument,None
            with gate:
                if stop.is_set():
                    return None
                delay = state["next_dispatch"]-time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                if stop.is_set():
                    return None
                state["next_dispatch"] = time.monotonic()+args.interval_seconds
                state["calls"] += 1
            try:
                if not hasattr(local,"pro"):
                    local.pro = pro_factory()
                raw = local.pro.etf_limit(**request["params"])
                if len(raw) >= 3000:
                    raise ValueError("Possible ETF limit pagination truncation")
                if not raw.empty and (not {"ts_code","trade_date","up_limit","down_limit"}.issubset(raw)
                        or not raw.ts_code.eq(instrument).all() or raw.trade_date.duplicated().any()
                        or not raw.trade_date.between(args.start,args.end).all()):
                    raise ValueError("ETF limit source identity/date mismatch")
                root.mkdir(parents=True,exist_ok=True)
                raw.to_parquet(root/"raw.parquet",index=False)
                atomic_json(path,{"request":request,"rows":len(raw),"raw_hash":file_hash(root/"raw.parquet"),
                                  "observed_at":datetime.now(timezone.utc).isoformat(),
                                  "historical_publication_times_not_provided":True,
                                  "source_completeness_verified":False})
                with gate:
                    state["published"] += 1
                    if state["published"] % 50 == 0:
                        print(json.dumps({"status":"collecting","calls":state["calls"],
                                          "published":state["published"],"reused":reused}),flush=True)
                return instrument,None
            except Exception as exc:
                stop.set()
                return instrument,type(exc).__name__

    failures = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for result in pool.map(fetch,jobs[:args.max_calls]):
            if result is None:
                continue
            instrument,error = result
            if error:
                failures.append({"instrument":instrument,"exception_type":error})
            else:
                available.add(instrument)
    return {"status":"failed" if failures else "collected" if len(available)==len(instruments) else "partial",
            "calls":state["calls"],"reused":reused,"completed_instruments":len(available),
            "target_instruments":len(instruments),"failures":failures,"source_completeness_verified":False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source",type=Path,default=Path("D:/qlib_data/etf_qlib_data"))
    parser.add_argument("--start",required=True)
    parser.add_argument("--end",required=True)
    parser.add_argument("--output-root",type=Path,default=Path("artifacts/tushare_supplements/etf_limit"))
    parser.add_argument("--max-calls",type=int,default=1)
    parser.add_argument("--workers",type=int,choices=range(1,5),default=1)
    parser.add_argument("--interval-seconds",type=float,default=.8)
    args = parser.parse_args()
    for value in (args.start,args.end):
        if len(value)!=8 or datetime.strptime(value,"%Y%m%d").strftime("%Y%m%d")!=value:
            parser.error("Dates must be YYYYMMDD")
    if args.start > args.end or args.max_calls < 1 or args.interval_seconds < 0:
        parser.error("Invalid date/call/dispatch limits")
    source,output = args.source.resolve(),args.output_root.resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):
        parser.error("Output cannot overlap raw source")
    token = os.environ.get("TUSHARE_TOKEN","").strip()
    if not token:
        print(json.dumps({"status":"blocked","reason":"missing_tushare_token"}))
        return 5
    import tushare as ts
    instruments = QlibBinSource(source).instruments.instrument.tolist()
    report = collect(args,instruments,lambda:ts.pro_api(token,timeout=20))
    print(json.dumps(report),flush=True)
    return 1 if report["status"]=="failed" else 0 if report["status"]=="collected" else 5


if __name__ == "__main__":
    raise SystemExit(main())
