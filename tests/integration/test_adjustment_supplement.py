import pandas as pd

from etf_ml.contracts import UniversePolicy
from etf_ml.data.snapshot import build_snapshot, load_snapshot
from etf_ml.data.source import QlibBinSource
from etf_ml.utils import source_hashes


def test_real_factor_sidecar_is_bound_to_snapshot_and_views(source_spec, tmp_path):
    reader = QlibBinSource(source_spec.source)
    panel = reader.read(source_spec.fields)
    raw = panel.factor.rename("adj_factor").reset_index()
    raw = raw.rename(columns={"instrument": "ts_code", "datetime": "trade_date"})
    raw.trade_date = raw.trade_date.dt.strftime("%Y%m%d")
    path = tmp_path / "fund_adj.parquet"
    raw.to_parquet(path, index=False)
    source_spec.adjustment_path = path
    for instrument in reader.instruments.instrument:
        (source_spec.source / "features" / instrument.lower() / "factor.day.bin").unlink()
    before = source_hashes(source_spec.source)
    policy = UniversePolicy(minimum_listing_days=0, liquidity_lookback=1)
    snapshot = build_snapshot(source_spec.source, source_spec, policy)
    assert source_hashes(source_spec.source) == before
    assert load_snapshot(snapshot.path).snapshot_id == snapshot.snapshot_id
    normalized = pd.read_parquet(snapshot.path / "panel.parquet")
    assert normalized.adjustment_factor.eq(1).all()
    research = pd.read_parquet(snapshot.path / "research/panel.parquet")
    assert research.index.get_level_values("datetime").max() < pd.Timestamp(source_spec.holdout_start)
    raw.adj_factor = 2.
    raw.to_parquet(path, index=False)
    changed = build_snapshot(source_spec.source, source_spec, policy)
    assert changed.snapshot_id != snapshot.snapshot_id
    assert pd.read_parquet(changed.path / "panel.parquet").adjustment_factor.eq(2).all()
    assert source_hashes(source_spec.source) == before


def test_missing_real_dividend_blocks_audit_and_snapshot(source_spec, tmp_path):
    import pytest
    from etf_ml.errors import QualityError
    from etf_ml.data.snapshot import audit_source
    panel = QlibBinSource(source_spec.source).read(source_spec.fields)
    raw = panel.factor.rename("adj_factor").reset_index().rename(
        columns={"instrument": "ts_code", "datetime": "trade_date"})
    raw.loc[(raw.ts_code == "510300.SH") & (raw.trade_date >= raw.trade_date.min() + pd.Timedelta(days=30)), "adj_factor"] = 1.1
    raw.trade_date = raw.trade_date.dt.strftime("%Y%m%d")
    path = tmp_path / "unexplained-factors.parquet"
    raw.to_parquet(path, index=False)
    source_spec.adjustment_path = path
    for instrument in panel.index.get_level_values("instrument").unique():
        (source_spec.source / "features" / instrument.lower() / "factor.day.bin").unlink()
    before = source_hashes(source_spec.source)
    report = audit_source(source_spec)
    assert report["adjustment_validation"]["status"] == "failed"
    assert {x["code"] for x in report["errors"]} >= {"adjustment_event_consistency_failed"}
    with pytest.raises(QualityError, match="consistency failed"):
        build_snapshot(source_spec.source, source_spec, UniversePolicy(minimum_listing_days=0, liquidity_lookback=1))
    assert not list(source_spec.artifact_root.glob("*/snapshot_manifest.json"))
    assert source_hashes(source_spec.source) == before
