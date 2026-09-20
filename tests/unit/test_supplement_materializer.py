import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

from etf_ml.config import load_config
from etf_ml.data.source import QlibBinSource
from etf_ml.errors import IntegrityError, QualityError
from etf_ml.utils import atomic_json, content_hash, file_hash, source_hashes


def setup_materializer(source_spec, tmp_path, monkeypatch, *, complete=True):
    spec = importlib.util.spec_from_file_location("materializer", Path(__file__).resolve().parents[2] / "scripts/materialize_etf_supplements.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = load_config()
    config.data = source_spec
    config_path = tmp_path / "assembly.yaml"
    config_path.write_text(yaml.safe_dump(config.model_dump(mode="json")))
    reader = QlibBinSource(source_spec.source)
    frame = reader.read(source_spec.fields)
    start, end = reader.calendar.min().strftime("%Y%m%d"), reader.calendar.max().strftime("%Y%m%d")
    cache = tmp_path / "cache"
    if complete:
        for instrument in reader.instruments.instrument:
            factors = frame.xs(instrument, level="instrument").factor
            raw = factors.rename("adj_factor").reset_index().rename(columns={"datetime": "trade_date"})
            raw.trade_date = raw.trade_date.dt.strftime("%Y%m%d")
            raw["ts_code"] = instrument
            for api in ("fund_adj", "fund_div"):
                params = {"ts_code": instrument}
                if api == "fund_adj":
                    params.update(start_date=start, end_date=end)
                request = {"api": api, "params": params}
                root = cache / content_hash(request)
                root.mkdir(parents=True)
                response = raw if api == "fund_adj" else pd.DataFrame(columns=["ts_code", "div_proc", "ex_date", "record_date", "pay_date", "ann_date", "div_cash"])
                response.to_parquet(root / "raw.parquet", index=False)
                atomic_json(root / "manifest.json", {"request": request, "rows": len(response), "raw_hash": file_hash(root / "raw.parquet")})
    monkeypatch.setattr(sys, "argv", ["assembler", "--config", str(config_path), "--start", start, "--end", end,
                                     "--cache-root", str(cache), "--output-root", str(tmp_path / "inputs")])
    return module, cache


def test_all_target_caches_required_no_partial_input_publication(source_spec, tmp_path, monkeypatch, capsys):
    module, _ = setup_materializer(source_spec, tmp_path, monkeypatch, complete=False)
    assert module.main() == 5
    report = json.loads(capsys.readouterr().out)
    assert report["missing_responses"] == {"fund_adj": 3, "fund_div": 3}
    assert report["g0_passed"] is False
    assert not list((tmp_path / "inputs").glob("*/manifest.json"))


def test_full_assembly_immutable_reuse_does_not_claim_g0(source_spec, tmp_path, monkeypatch, capsys):
    module, _ = setup_materializer(source_spec, tmp_path, monkeypatch)
    before = source_hashes(source_spec.source)
    assert module.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "ready_for_snapshot" and report["g0_passed"] is False
    assert report["quoted_rows"] == 540 and report["cash_event_count"] == 0
    assert report["adjustment_validation"]["status"] == "passed"
    assert source_hashes(source_spec.source) == before
    assert module.main() == 0
    assert json.loads(capsys.readouterr().out)["reused"] is True
    Path(report["input_root"], "events.parquet").write_bytes(b"tampered")
    with pytest.raises(IntegrityError, match="hash"):
        module.main()


def test_cache_tampering_rejected_before_assembly(source_spec, tmp_path, monkeypatch):
    module, cache = setup_materializer(source_spec, tmp_path, monkeypatch)
    next(cache.glob("*/raw.parquet")).write_bytes(b"tampered")
    with pytest.raises(QualityError, match="hash changed"):
        module.main()


def test_unexplained_factor_change_cannot_become_ready_inputs(source_spec, tmp_path, monkeypatch, capsys):
    module, cache = setup_materializer(source_spec, tmp_path, monkeypatch)
    for path in cache.glob("*/manifest.json"):
        manifest = json.loads(path.read_text())
        if manifest["request"]["api"] == "fund_adj" and manifest["request"]["params"]["ts_code"] == "510300.SH":
            raw_path = path.parent / "raw.parquet"
            raw = pd.read_parquet(raw_path)
            raw.loc[raw.index >= 30, "adj_factor"] = 1.1
            raw.to_parquet(raw_path, index=False)
            manifest["raw_hash"] = file_hash(raw_path)
            atomic_json(path, manifest)
            (source_spec.source / "features/510300.sh/factor.day.bin").unlink()
    assert module.main() == 5
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "failed_quality"
    assert report["adjustment_validation"]["issues"] == [{"code": "unexplained_adjustment_change", "rows": 1}]
    assert report["g0_passed"] is False



def test_complete_share_assembly_and_snapshot_bind_original_factor_and_notice(
        source_spec, tmp_path, monkeypatch, capsys, calendar):
    from etf_ml.data.source import encode_provider
    from etf_ml.data.snapshot import build_snapshot
    from etf_ml.contracts import UniversePolicy

    reader = QlibBinSource(source_spec.source)
    raw = reader.read(source_spec.fields)
    instrument = "510300.SH"
    effective = calendar[25]
    mask = (raw.index.get_level_values("instrument") == instrument) & (raw.index.get_level_values("datetime") >= effective)
    for column in ["open","high","low","close","reference_close"]:
        raw.loc[mask,column] *= 2.
    raw.loc[mask,"factor"] *= .5
    raw.loc[mask,"volume"] *= .5
    clone = tmp_path / "share_raw"
    encode_provider(raw,calendar,clone,{name:out for out,name in source_spec.fields.items()})
    source_spec.source = clone
    module,cache = setup_materializer(source_spec,tmp_path,monkeypatch)
    request = {"api":"fund_adj","params":{"ts_code":instrument,"start_date":calendar[0].strftime("%Y%m%d"),"end_date":calendar[-1].strftime("%Y%m%d")}}
    factorpath = cache / content_hash(request) / "raw.parquet"
    notice = tmp_path / "share_notice.pdf"
    notice.write_bytes(b"%PDF-test-merger-notice")
    share = pd.DataFrame([{"event_id":"share-merge","instrument":instrument,"datetime":effective,
                           "cash_per_share":0.,"share_multiplier":.5,"sequence":0,"share_rounding":"ceil",
                           "record_date":calendar[24],"available_time":(calendar[20]+pd.Timedelta(hours=20)).tz_localize("Asia/Shanghai"),
                           "evidence_id":"notice"}])
    share.attrs = {"primary_evidence":[{"evidence_id":"notice","path":notice.name,"url":"https://exchange.example/notice",
                                      "sha256":file_hash(notice)}],"source_factor_hashes":{instrument:file_hash(factorpath)}}
    source_spec.share_events_path = tmp_path / "share_events.parquet"
    share.to_parquet(source_spec.share_events_path,index=False)
    configpath = tmp_path / "assembly.yaml"
    config = yaml.safe_load(configpath.read_text())
    config["data"] = source_spec.model_dump(mode="json")
    configpath.write_text(yaml.safe_dump(config))
    before = source_hashes(clone)
    assert module.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["cash_event_count"] == 0 and report["share_event_count"] == 1
    assert report["adjustment_validation"]["status"] == "passed"
    assert report["g0_passed"] is False
    source_spec.events_path = Path(report["input_root"]) / "events.parquet"
    snapshot = build_snapshot(clone,source_spec,UniversePolicy(minimum_listing_days=0,liquidity_lookback=1))
    assert snapshot.manifest["external_hashes"][str(notice.resolve())] == file_hash(notice)
    assert source_hashes(clone) == before
    saved = pd.read_parquet(source_spec.events_path)
    saved.loc[0,"share_rounding"] = "none"
    saved.to_parquet(source_spec.events_path,index=False)
    with pytest.raises(QualityError,match="changed"):
        build_snapshot(clone,source_spec,UniversePolicy(minimum_listing_days=0,liquidity_lookback=1))



def test_mapping_failure_keeps_compact_diagnostic_without_qualified_publication(
        source_spec,tmp_path,monkeypatch,capsys):
    module,cache = setup_materializer(source_spec,tmp_path,monkeypatch)
    for path in cache.glob("*/manifest.json"):
        manifest = json.loads(path.read_text())
        if manifest["request"] == {"api":"fund_div","params":{"ts_code":"510300.SH"}}:
            raw = pd.DataFrame([{"ts_code":"510300.SH","div_proc":"实施","ex_date":None,
                                 "record_date":None,"pay_date":None,"ann_date":None,"div_cash":.1}])
            raw_path = path.parent / "raw.parquet"
            raw.to_parquet(raw_path,index=False)
            manifest["raw_hash"] = file_hash(raw_path)
            atomic_json(path,manifest)
    before = source_hashes(source_spec.source)
    assert module.main() == 5
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "blocked_dividend_mapping"
    assert report["full_target_cache"] is True and report["g0_passed"] is False
    assert report["qualified_inputs_published"] is False
    assert report["mapping_failure"]["message"] == "Implemented dividend has no ex-date"
    diagnostic = Path(report["diagnostic_root"])
    assert (diagnostic / "fund_adj.parquet").is_file()
    assert (diagnostic / "assembly_report.json").is_file()
    assert not (diagnostic / "manifest.json").exists()
    assert not list((tmp_path / "inputs").glob("[0-9a-f]*/manifest.json"))
    assert source_hashes(source_spec.source) == before
