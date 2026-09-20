import importlib.util
import json
from pathlib import Path
import sys

import pandas as pd
import pytest
import yaml

from etf_ml.config import load_config
from etf_ml.data.source import QlibBinSource
from etf_ml.data.snapshot import build_snapshot
from etf_ml.contracts import UniversePolicy
from etf_ml.errors import QualityError, IntegrityError
from etf_ml.utils import atomic_json,content_hash,file_hash,source_hashes


def setup(source_spec,tmp_path,monkeypatch,*,complete=True,clock="09:25:00"):
    spec=importlib.util.spec_from_file_location("limit_materializer",Path("scripts/materialize_etf_limits.py"))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    config=load_config();config.data=source_spec
    cfg=tmp_path/"config.yaml";cfg.write_text(yaml.safe_dump(config.model_dump(mode="json")))
    reader=QlibBinSource(source_spec.source);frame=reader.read(source_spec.fields)
    start,end=reader.calendar.min().strftime("%Y%m%d"),reader.calendar.max().strftime("%Y%m%d")
    cache=tmp_path/"cache";output=tmp_path/"inputs"
    policy=tmp_path/"policy.json"
    atomic_json(policy,{"basis":"source_documented_schedule","local_time":clock,"timezone":"Asia/Shanghai","explanation":"Synthetic frozen policy"})
    for number,instrument in enumerate(reader.instruments.instrument):
        if not complete and number>0:continue
        raw=frame.xs(instrument,level="instrument").reset_index()[["datetime"]]
        raw["trade_date"]=raw.pop("datetime").dt.strftime("%Y%m%d")
        raw["ts_code"]=instrument;raw["up_limit"]=5.;raw["down_limit"]=1.
        request={"api":"etf_limit","params":{"ts_code":instrument,"start_date":start,"end_date":end,"fields":module.FIELDS}}
        root=cache/content_hash(request);root.mkdir(parents=True)
        raw.to_parquet(root/"raw.parquet",index=False)
        atomic_json(root/"manifest.json",{"request":request,"rows":len(raw),"raw_hash":file_hash(root/"raw.parquet")})
    monkeypatch.setattr(sys,"argv",["assembler","--config",str(cfg),"--start",start,"--end",end,"--policy",str(policy),"--cache-root",str(cache),"--output-root",str(output)])
    return module,cache,output


def test_limit_assembly_requires_every_target_cache(source_spec,tmp_path,monkeypatch,capsys):
    module,cache,output=setup(source_spec,tmp_path,monkeypatch,complete=False)
    assert module.main()==5
    report=json.loads(capsys.readouterr().out)
    assert report["target_instruments"]==3 and report["missing_responses"]==2
    assert not report["qualified_limit_inputs_published"] and not list(output.glob("*/manifest.json"))


def test_limit_assembly_is_immutable_and_snapshot_binds_all_evidence(source_spec,tmp_path,monkeypatch,capsys):
    module,cache,output=setup(source_spec,tmp_path,monkeypatch)
    before=source_hashes(source_spec.source)
    assert module.main()==0
    report=json.loads(capsys.readouterr().out);root=Path(report["input_root"])
    assert report["quoted_rows"]==540 and report["limit_rows"]==540
    assert not report["g0_passed"] and not report["actual_historical_receipts_proven"]
    assert module.main()==0 and json.loads(capsys.readouterr().out)["reused"]
    source_spec.limits_path=root/"limits.parquet"
    snapshot=build_snapshot(source_spec.source,source_spec,UniversePolicy(minimum_listing_days=0,liquidity_lookback=1))
    for evidence in [root/"policy.json",*root.glob("responses/*")]:
        assert str(evidence.resolve()) in snapshot.manifest["external_hashes"]
    assert source_hashes(source_spec.source)==before
    next(root.glob("responses/*.json")).write_text("tampered")
    with pytest.raises(IntegrityError):module.main()


@pytest.mark.parametrize("change",["missing_quote","outside_price","late_policy","wrong_request"])
def test_complete_limit_cache_with_bad_economics_or_timing_never_publishes(source_spec,tmp_path,monkeypatch,capsys,change):
    module,cache,output=setup(source_spec,tmp_path,monkeypatch,clock="10:00:00" if change=="late_policy" else "09:25:00")
    if change!="late_policy":
        root=next(p.parent for p in cache.glob("*/manifest.json"))
        p=root/"manifest.json";obj=json.loads(p.read_text());raw=pd.read_parquet(root/"raw.parquet")
        if change=="missing_quote":raw=raw.iloc[1:].copy()
        elif change=="outside_price":raw.down_limit=4.5
        else:obj["request"]["params"]["ts_code"]="UNRELATED"
        raw.to_parquet(root/"raw.parquet",index=False);obj.update(raw_hash=file_hash(root/"raw.parquet"),rows=len(raw));atomic_json(p,obj)
    if change=="wrong_request":
        with pytest.raises(QualityError,match="hash changed"):module.main()
    else:
        assert module.main()==5
        report=json.loads(capsys.readouterr().out)
        assert report["status"]=="blocked_limit_quality" and not report["qualified_limit_inputs_published"]
    assert not list(output.glob("*/manifest.json"))


def test_cache_tamper_is_rejected_before_limit_assembly(source_spec,tmp_path,monkeypatch):
    module,cache,_=setup(source_spec,tmp_path,monkeypatch)
    next(cache.glob("*/raw.parquet")).write_bytes(b"tampered")
    with pytest.raises(QualityError,match="hash changed"):module.main()


@pytest.mark.skipif(sys.platform!="win32",reason="Windows extended-path regression")
def test_deep_evidence_files_are_never_omitted_from_hash_manifest(tmp_path):
    from etf_ml.utils import filesystem_path,verify_files
    root=tmp_path/"deep"
    relative=Path("a"*90)/("b"*90)/("c"*64+".json")
    physical=filesystem_path(root/relative)
    physical.parent.mkdir(parents=True)
    physical.write_bytes(b"original-deep-proof")
    assert len(str(root/relative))>260
    hashes=source_hashes(root)
    assert hashes=={relative.as_posix():file_hash(root/relative)}
    verify_files(root,hashes)
    physical.write_bytes(b"tampered-deep-proof")
    with pytest.raises(IntegrityError):verify_files(root,hashes)
