import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import threading

import pandas as pd
import pytest


def collector():
    spec = importlib.util.spec_from_file_location("limit_collector",Path("scripts/fetch_etf_limits.py"))
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def arguments(tmp_path,max_calls=2,workers=2):
    return SimpleNamespace(output_root=tmp_path/"cache",start="20230102",end="20230106",
                           max_calls=max_calls,workers=workers,interval_seconds=0.)


def test_limit_collector_call_cap_and_restart_reuse(tmp_path):
    module = collector();calls=[];gate=threading.Lock()
    class Pro:
        def etf_limit(self,**params):
            with gate:calls.append(params["ts_code"])
            return pd.DataFrame([{"ts_code":params["ts_code"],"trade_date":"20230103","up_limit":5.,"down_limit":1.}])
    args=arguments(tmp_path)
    first=module.collect(args,["A","B","C"],Pro)
    assert first["status"]=="partial" and first["calls"]==2 and first["completed_instruments"]==2
    second=module.collect(args,["A","B","C"],Pro)
    assert second["status"]=="collected" and second["calls"]==1 and second["reused"]==2
    assert sorted(calls)==["A","B","C"]
    assert not second["source_completeness_verified"]


def test_limit_cache_tamper_blocks_even_when_no_new_calls(tmp_path):
    module=collector()
    class Pro:
        def etf_limit(self,**params):
            return pd.DataFrame([{"ts_code":params["ts_code"],"trade_date":"20230103","up_limit":5.,"down_limit":1.}])
    args=arguments(tmp_path)
    module.collect(args,["A"],Pro)
    next(args.output_root.glob("*/raw.parquet")).write_bytes(b"tampered")
    with pytest.raises(ValueError,match="cache identity"):
        module.collect(args,["A"],Pro)


def test_limit_source_failure_stops_new_dispatch_without_success_manifest(tmp_path):
    module=collector();calls=[]
    class Pro:
        def etf_limit(self,**params):
            calls.append(params["ts_code"])
            raise TimeoutError("Test only")
    report=module.collect(arguments(tmp_path,max_calls=3,workers=1),["A","B","C"],Pro)
    assert report["status"]=="failed" and report["calls"]==1 and calls==["A"]
    assert not list((tmp_path/"cache").glob("*/manifest.json"))
