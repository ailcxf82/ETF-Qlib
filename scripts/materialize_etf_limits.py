"""Publish complete quoted daily-limit inputs; never qualify a partial ETF pool."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import time

import numpy as np
import pandas as pd

from etf_ml.config import load_config
from etf_ml.data.calendar import read_calendar, require_calendar
from etf_ml.data.limit_supplement import map_source_limits, load_limit_supplement
from etf_ml.data.source import QlibBinSource
from etf_ml.errors import QualityError
from etf_ml.utils import FileLock, atomic_json, code_hash, content_hash, file_hash, source_hashes, verify_files, filesystem_path

FIELDS = "ts_code,trade_date,up_limit,down_limit,asset_type,exchange"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config",type=Path,required=True)
    parser.add_argument("--start",required=True)
    parser.add_argument("--end",required=True)
    parser.add_argument("--policy",type=Path,required=True)
    parser.add_argument("--cache-root",type=Path,default=Path("artifacts/tushare_supplements/etf_limit"))
    parser.add_argument("--output-root",type=Path,default=Path("artifacts/limit_inputs"))
    args = parser.parse_args()
    config = load_config(args.config)
    source,output,cache = config.data.source.resolve(),args.output_root.resolve(),args.cache_root.resolve()
    if (output.is_relative_to(source) or source.is_relative_to(output) or
            output.is_relative_to(cache) or cache.is_relative_to(output)):
        raise QualityError("Limit outputs cannot overlap raw source or source cache")
    reader = QlibBinSource(source)
    policy_hash = file_hash(args.policy)
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    requests,missing = [],[]
    for instrument in reader.instruments.instrument:
        request = {"api":"etf_limit","params":{"ts_code":instrument,"start_date":args.start,
                    "end_date":args.end,"fields":FIELDS}}
        root = cache/content_hash(request)
        manifest = root/"manifest.json"
        if not manifest.is_file():
            missing.append(instrument)
            continue
        evidence = json.loads(manifest.read_text(encoding="utf-8"))
        if evidence["request"] != request or file_hash(root/"raw.parquet") != evidence["raw_hash"]:
            raise QualityError("ETF limit cached request/response hash changed")
        requests.append((root,file_hash(manifest),evidence["raw_hash"]))
    if missing:
        output.mkdir(parents=True,exist_ok=True)
        report = {"status":"blocked_cache","target_instruments":len(reader.instruments),
                  "cached_instruments":len(requests),"missing_responses":len(missing),
                  "qualified_limit_inputs_published":False,"g0_passed":False,"external_calls":0}
        atomic_json(output/"pending.json",report)
        print(json.dumps(report));return 5
    calendar = read_calendar(config.data.trusted_calendar)
    calendar = calendar[(calendar>=pd.Timestamp(args.start)) & (calendar<=pd.Timestamp(args.end)) &
                        (calendar>=reader.calendar.min()) & (calendar<=reader.calendar.max())]
    require_calendar(calendar)
    original = source_hashes(source)
    identity = {"source_hashes":original,"cache_manifests":[m for _,m,_ in requests],
                "data_spec":config.data.model_dump(mode="json"),"calendar":list(calendar.strftime("%Y-%m-%d")),
                "availability_policy":policy,"policy_file_hash":policy_hash,"code_hash":code_hash()}
    key = content_hash(identity);final = output/key
    with FileLock(output/".locks"/(key+".lock")):
        if (final/"manifest.json").is_file():
            manifest = json.loads((final/"manifest.json").read_text(encoding="utf-8"))
            if manifest["identity_hash"] != key:
                raise QualityError("Limit input identity changed")
            verify_files(final,manifest["files"])
            report = json.loads((final/"assembly_report.json").read_text(encoding="utf-8"))
            print(json.dumps({**report,"reused":True,"input_root":str(final)}));return 0
        temp = output/("."+key+"."+str(time.time_ns())+".tmp")
        filesystem_path(temp/"responses").mkdir(parents=True)
        responses,proofs,manifests = [],[],[]
        for root,manifest_hash,raw_hash in requests:
            raw = pd.read_parquet(root/"raw.parquet")
            evidence_id = root.name
            name = "responses/"+evidence_id
            shutil.copyfile(filesystem_path(root/"raw.parquet"),filesystem_path(temp/(name+".parquet")))
            shutil.copyfile(filesystem_path(root/"manifest.json"),filesystem_path(temp/(name+".json")))
            responses.append(raw)
            common = {"evidence_id":evidence_id,"url":"https://api.tushare.pro"}
            proofs.append({**common,"path":name+".parquet","sha256":raw_hash})
            manifests.append({**common,"path":name+".json","sha256":manifest_hash})
        shutil.copyfile(filesystem_path(args.policy),filesystem_path(temp/"policy.json"))
        try:
            limits = map_source_limits(pd.concat(responses,ignore_index=True),policy)
            limits.attrs = {"availability_policy":policy,"source_responses":proofs,"source_manifests":manifests,
                            "availability_policy_file":"policy.json","availability_policy_sha256":policy_hash}
            limits.to_parquet(filesystem_path(temp/"limits.parquet"))
            limits,hashes = load_limit_supplement(temp/"limits.parquet",calendar,reader.instruments.instrument)
            quotes = reader.read({"raw_open":config.data.fields["open"],"raw_close":config.data.fields["close"]})
            quotes = quotes[quotes.index.get_level_values("datetime").isin(calendar)]
            required = quotes.index[quotes.notna().all(axis=1)]
            missing_quotes = int((~required.isin(limits.index)).sum())
            if missing_quotes:
                raise QualityError("Complete source caches still lack daily limits for quoted rows: "+str(missing_quotes))
            selected = limits.reindex(required)
            opening = required.get_level_values("datetime").tz_localize("Asia/Shanghai")+pd.Timedelta(hours=9,minutes=30)
            unavailable = int((~selected.available_time.le(opening)).sum())
            if unavailable:
                raise QualityError("Daily limits unavailable at execution open: "+str(unavailable))
            prices = quotes.loc[required,"raw_open"]
            outside = ((prices.gt(selected.up_limit) & ~np.isclose(prices,selected.up_limit,rtol=1e-7,atol=1e-7)) |
                       (prices.lt(selected.down_limit) & ~np.isclose(prices,selected.down_limit,rtol=1e-7,atol=1e-7)))
            if outside.any():
                raise QualityError("Raw opening prices outside source daily limits: "+str(int(outside.sum())))
        except (QualityError,ValueError,TypeError) as error:
            report = {"status":"blocked_limit_quality","target_instruments":len(reader.instruments),
                      "qualified_limit_inputs_published":False,"g0_passed":False,
                      "diagnostic_root":str(temp),"failure":{"exception_type":type(error).__name__,"message":str(error)}}
            atomic_json(temp/"assembly_report.json",report);atomic_json(output/"pending.json",report)
            print(json.dumps(report));return 5
        if (source_hashes(source)!=original or file_hash(args.policy)!=policy_hash or
                any(file_hash(root/"manifest.json")!=m or file_hash(root/"raw.parquet")!=h for root,m,h in requests) or
                any(file_hash(Path(p))!=h for p,h in hashes.items())):
            raise QualityError("Source/cache/policy inputs changed during limit assembly")
        report = {"status":"ready_for_snapshot","target_instruments":len(reader.instruments),
                  "quoted_rows":len(required),"limit_rows":len(limits),"quoted_limits_unavailable_at_open":0,
                  "raw_source_unchanged":True,"qualified_limit_inputs_published":True,"g0_passed":False,
                  "actual_historical_receipts_proven":policy["basis"]=="recorded_receipt","availability_policy":policy,
                  "external_calls":0,"next_step":"supply other G0 inputs before first-loop build-data"}
        atomic_json(temp/"assembly_report.json",report)
        atomic_json(temp/"manifest.json",{"identity_hash":key,"files":source_hashes(temp)})
        os.replace(filesystem_path(temp),filesystem_path(final))
        print(json.dumps({**report,"reused":False,"input_root":str(final)}));return 0


if __name__ == "__main__":
    raise SystemExit(main())
