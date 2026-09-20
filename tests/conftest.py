from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from etf_ml.contracts import DataSpec, FoldSpec
from etf_ml.data.source import encode_provider

@pytest.fixture
def calendar():
    return pd.bdate_range("2023-01-02", periods=180, name="datetime")


@pytest.fixture
def panel(calendar):
    pieces = []
    for j, instrument in enumerate(["510300.SH", "510500.SH", "159915.SZ"]):
        t = np.arange(len(calendar))
        raw = 2 + j + 0.004 * t + 0.06 * np.sin(t / (5 + j))
        volume = 100000 + (np.sin(t / 4) * 20000).astype(int)
        data = pd.DataFrame({
            "raw_open": raw, "raw_high": raw * 1.02, "raw_low": raw * 0.98,
            "raw_close": raw * 1.001, "adj_open": raw, "adj_high": raw * 1.02,
            "adj_low": raw * 0.98, "adj_close": raw * 1.001,
            "volume_shares": volume, "amount_currency": raw * 1.001 * volume,
            "adjustment_factor": 1.0, "quoted": True, "tradable": True,
        }, index=calendar)
        data["reference_close"] = data.raw_close.shift().fillna(data.raw_close.iloc[0])
        data["return_1d"] = data.raw_close / data.reference_close - 1
        data["instrument"] = instrument
        pieces.append(data.reset_index().set_index(["datetime", "instrument"]))
    return pd.concat(pieces).sort_index()


@pytest.fixture
def metadata(calendar):
    return pd.DataFrame({
        "instrument": ["510300.SH", "510500.SH", "159915.SZ"],
        "valid_from": calendar[0], "valid_to": calendar[-1],
        "available_time": calendar[0] - pd.Timedelta(days=1),
        "listing_date": calendar[0], "asset_class": "domestic_equity",
        "tracking_group": ["CSI300", "CSI500", "CHINEXT"], "operating": True,
    })


@pytest.fixture
def fold(calendar):
    return FoldSpec(name="fixture",
                    train={"start": str(calendar[0].date()), "end": str(calendar[79].date())},
                    early_stop={"start": str(calendar[80].date()), "end": str(calendar[119].date())},
                    selection={"start": str(calendar[120].date()), "end": str(calendar[-1].date())})


@pytest.fixture
def source_spec(tmp_path, panel, metadata, calendar):
    source = tmp_path / "raw"
    mapped = pd.DataFrame(index=panel.index)
    for field in ("open", "high", "low", "close"):
        mapped[field] = panel["raw_" + field]
    for field, name in (("volume", "volume_shares"), ("amount", "amount_currency"),
                        ("factor", "adjustment_factor"), ("change", "return_1d"),
                        ("pre_close", "reference_close")):
        mapped[field] = panel[name]
    future_calendar = pd.bdate_range(calendar[0], calendar[-1] + pd.offsets.MonthEnd(2), name="datetime")
    encode_provider(mapped, calendar, source, {c: c for c in mapped}, future_calendar=future_calendar)
    trusted = tmp_path / "trusted_calendar.txt"
    trusted.write_text("\n".join(future_calendar.strftime("%Y-%m-%d")) + "\n", encoding="utf-8")
    meta = tmp_path / "historical_metadata.parquet"
    metadata.to_parquet(meta, index=False)
    benchmark = pd.DataFrame({"open": np.arange(len(calendar)) + 4000,
                              "close": np.arange(len(calendar)) + 4001}, index=calendar)
    benchmark.attrs["benchmark_id"] = "CSI300"
    benchmark_path = tmp_path / "csi300.parquet"
    benchmark.to_parquet(benchmark_path)
    return DataSpec(source=source, artifact_root=tmp_path / "snapshots",
                    trusted_calendar=trusted, volume_unit="shares", amount_multiplier=1,
                    price_mode="raw", change_unit="decimal", point_in_time_metadata=True,
                    metadata_path=meta, benchmark_path=benchmark_path,
                    holdout_start=str(calendar[130].date()))


@pytest.fixture
def income_source(source_spec, calendar, tmp_path):
    """Synthetic dated income and source proof; never real G0 evidence."""
    from etf_ml.data.money_income import money_income_intervals
    from etf_ml.utils import file_hash
    days = pd.date_range(calendar[0], calendar[-1])
    raw = pd.DataFrame({"FSRQ": days.strftime("%Y-%m-%d"), "DWJZ": .01,
                        "SDATE": None, "NAVTYPE": "1"})
    frame = money_income_intervals(raw, "510300.SH", basis_shares=100, par_value=100)
    frame["available_time"] = frame.period_end + pd.Timedelta(days=1, hours=8)
    frame["evidence_id"] = "dated-source"
    proof = tmp_path / "income_source.txt"
    proof.write_text("Synthetic daily series, publication timestamps and versioned cent settlement", encoding="utf8")
    future = tmp_path / "holdout_source.txt"
    future.write_text("HOLDOUT_ONLY_DOCUMENT", encoding="utf8")
    frame.attrs = {
        "income_rules": {"510300.SH": {"rule_id": "synthetic-income-v1", "basis_shares": 100.,
            "par_value": 100., "compound_unpaid": False, "convert_at_par": False, "rounding": "exact",
            "evidence_id": "dated-source", "available_time": "2023-01-01 00:00:00"}},
        "primary_evidence": [
            {"evidence_id": "dated-source", "path": proof.name, "url": "https://issuer.example/income", "sha256": file_hash(proof)},
            {"evidence_id": "future-source", "path": future.name, "url": "https://issuer.example/holdout", "sha256": file_hash(future)}]}
    source_spec.income_path = tmp_path / "income.parquet"
    frame.to_parquet(source_spec.income_path)
    return frame
