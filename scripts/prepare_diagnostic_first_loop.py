"""Assemble existing real inputs without claiming historical membership validity."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import yaml
from etf_ml.config import load_config
from etf_ml.data.source import QlibBinSource
from etf_ml.data.calendar import read_calendar
from etf_ml.data.tushare_supplement import dividend_events
from etf_ml.data.dividend_review import load_review, remove_reviewed_duplicates, validate_reviewed_events
from etf_ml.data.share_supplement import load_share_supplement, merge_share_events
from etf_ml.utils import file_hash, content_hash, atomic_json
from etf_ml.errors import QualityError


def current_metadata(raw, reader, source_path):
    if raw.ts_code.duplicated().any():
        raise QualityError("Current metadata has duplicate identities")
    raw = raw.set_index("ts_code").reindex(reader.instruments.instrument)
    if raw.metadata_asof.isna().any():
        raise QualityError("Current metadata must cover the entire original provider")
    names = raw.tracking_index_or_benchmark.fillna("") + " " + raw.full_name.fillna("")
    foreign = names.str.contains("港股|恒生|香港|标普|纳斯达克|美国|日经|德国|法国|富时新加坡|全球|海外|亚太|MSCI中国", regex=True)
    qdii = raw.etf_type.fillna("").str.contains("QDII", case=False)
    equity = raw.fund_type.eq("股票型") & ~foreign & ~qdii
    classification = pd.Series("outside_or_unclassified", index=raw.index)
    classification[equity] = "domestic_equity"
    listing = pd.to_datetime(raw.list_date, format="%Y%m%d", errors="coerce")
    missing_listing = listing.isna()
    starts = reader.instruments.set_index("instrument").start
    listing = listing.fillna(pd.to_datetime(starts.reindex(raw.index)))
    metadata = pd.DataFrame({"instrument": raw.index,
        "valid_from": reader.calendar.min(), "valid_to": reader.calendar.max(),
        "available_time": pd.to_datetime(raw.metadata_asof).to_numpy(),
        "listing_date": listing.to_numpy(), "asset_class": classification.to_numpy(),
        "tracking_group": raw.tracking_index_code.fillna("unclassified").to_numpy(),
        "operating": raw.list_status.eq("L").to_numpy()})
    metadata.attrs = {"metadata_mode": "diagnostic_current_universe",
        "source_hashes": {str(source_path.resolve()): file_hash(source_path)},
        "diagnostic_assumptions": [
            "Current status, equity classification and tracking group held constant retrospectively; survivorship bias is uncorrected.",
            "Reported metadata refresh time remains the actual available_time; historical availability is explicitly bypassed only in diagnostic mode.",
            "Domestic equity uses current stock-fund classification with explicit foreign-index/name and QDII exclusions; outside/unknown classes remain in the source panel.",
            "Reported listing date used for age; missing dates use provider observation start as an unverified proxy."],
        "missing_listing_proxy_instruments": raw.index[missing_listing].tolist()}
    return metadata


def main():
    config = load_config(Path("configs/data/tushare_first_loop.yaml"))
    spec = config.data
    reader = QlibBinSource(spec.source)
    calendar = read_calendar(spec.trusted_calendar)
    calendar = calendar[(calendar >= reader.calendar.min()) & (calendar <= reader.calendar.max())]
    output = Path("artifacts/diagnostic_first_loop/inputs").resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = Path("D:/qlib_data/etf_csv_data_meta/etf_history_metadata.csv")
    metadata = current_metadata(pd.read_csv(source, dtype=str), reader, source)
    metadata.to_parquet(output / "metadata.parquet", index=False)
    review, review_hashes = load_review(spec.dividend_review_path)
    pieces, failures, source_hashes, applied = [], [], {}, []
    for instrument in reader.instruments.instrument:
        request = {"api": "fund_div", "params": {"ts_code": instrument}}
        cache = Path("artifacts/tushare_supplements/etf") / content_hash(request)
        manifest_path = cache / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw_path = cache / "raw.parquet"
        raw_hash = file_hash(raw_path)
        if manifest["request"] != request or raw_hash != manifest["raw_hash"]:
            raise QualityError("Dividend cache integrity failed")
        source_hashes[str(raw_path.resolve())] = raw_hash
        source_hashes[str(manifest_path.resolve())] = file_hash(manifest_path)
        raw, reviews = remove_reviewed_duplicates(pd.read_parquet(raw_path), instrument, raw_hash, review)
        applied.extend(reviews)
        try:
            pieces.append(dividend_events(raw, calendar, [instrument]))
        except (QualityError, ValueError, TypeError) as exc:
            failures.append({"instrument": instrument, "raw_rows": len(raw), "reason": str(exc),
                             "source_path": str(raw_path.resolve()), "source_sha256": raw_hash,
                             "excluded_events_only": True, "instrument_retained_in_panel": True})
    events = pd.concat([piece for piece in pieces if not piece.empty], ignore_index=True)
    events["sequence"] = pd.to_numeric(events.sequence).astype("int64")
    shares, share_hashes = load_share_supplement(spec.share_events_path, calendar, reader.instruments.instrument)
    events = merge_share_events(events, shares, share_hashes, calendar, reader.instruments.instrument)
    events.attrs["reviewed_duplicates"] = applied
    events.attrs["primary_evidence_hashes"] = review_hashes
    events.attrs["source_completeness_verified"] = False
    events.attrs["unmapped_event_inputs"] = failures
    events.attrs["original_cache_hashes"] = source_hashes
    validate_reviewed_events(events, review, review_hashes)
    if any(file_hash(Path(path)) != sha for path,sha in source_hashes.items()):
        raise QualityError("Original dividend cache changed during assembly")
    events.to_parquet(output / "events.parquet", index=False)
    supplied = yaml.safe_load(Path("configs/data/tushare_first_loop.yaml").read_text(encoding="utf-8"))
    supplied["artifact_root"] = "D:/quant_project/ETF-Qlib/artifacts/diagnostic_first_loop"
    supplied["data"].update(mode="diagnostic", point_in_time_metadata=False,
        artifact_root="D:/quant_project/ETF-Qlib/artifacts/diagnostic_first_loop/data",
        metadata_path=str(output / "metadata.parquet"), events_path=str(output / "events.parquet"))
    supplied["research"]["limits"].update(timeout_seconds=14400, memory_mb=12288, cpu_count=2)
    supplied["models"] = [{"name":"lightgbm", "seed":42,
        "constructor":{"num_threads":2, "num_boost_round":200, "early_stopping_rounds":30}, "fit":{}}]
    config_path = Path("configs/data/tushare_diagnostic_first_loop.yaml")
    config_path.write_text(yaml.safe_dump(supplied, allow_unicode=True, sort_keys=False), encoding="utf-8")
    report = {"mode":"diagnostic", "formal_g0_passed":False, "external_calls":0,
        "provider_instruments":len(reader.instruments), "metadata_rows":len(metadata),
        "current_asset_classes":metadata.asset_class.value_counts().to_dict(),
        "current_operating":int(metadata.operating.sum()), "event_rows":len(events),
        "cash_events":int(events.cash_per_share.gt(0).sum()), "share_events":int(events.share_multiplier.ne(1).sum()),
        "unmapped_inputs":failures, "original_source_files":len(source_hashes),
        "config_path":str(config_path.resolve()), "assumptions":metadata.attrs["diagnostic_assumptions"]}
    atomic_json(output / "assembly_report.json", report)
    print(json.dumps(report, ensure_ascii=False))

if __name__ == "__main__":
    main()
