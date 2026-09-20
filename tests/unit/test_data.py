import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from etf_ml.contracts import UniversePolicy
from etf_ml.data.calendar import require_calendar, rebalance_dates
from etf_ml.data.normalize import normalize, normalized_issues
from etf_ml.data.snapshot import audit_source, build_snapshot, load_snapshot
from etf_ml.data.source import QlibBinSource, encode_provider
from etf_ml.data.universe import resolve
from etf_ml.errors import ConfigurationError, IntegrityError, QualityError
from etf_ml.utils import source_hashes

@pytest.mark.parametrize("dates", [
    ["2023-01-02", "2023-01-08"],
    ["2023-01-02", "2023-01-02"],
    ["2023-01-03", "2023-01-02"],
])
def test_T01_invalid_calendar_rejected(dates):
    with pytest.raises(QualityError):
        require_calendar(pd.DatetimeIndex(dates))

def test_T02_bin_roundtrip_preserves_dates(source_spec):
    before = QlibBinSource(source_spec.source).read(source_spec.fields)
    decoded = normalize(before, source_spec)
    snapshot = build_snapshot(source_spec.source, source_spec,
                              UniversePolicy(minimum_listing_days=0, liquidity_lookback=1))
    rebuilt = QlibBinSource(snapshot.path / "research" / "provider").read(
        {"open": "open", "close": "close", "factor": "factor"})
    expected = decoded.loc[rebuilt.index, ["adj_open", "adj_close", "adjustment_factor"]]
    expected.columns = rebuilt.columns
    assert_frame_equal(expected, rebuilt, check_exact=False, atol=1e-6, rtol=1e-6)

@pytest.mark.parametrize("mutation,code", [
    ("volume", "amount_volume_unit_inconsistent"),
    ("change", "change_semantics"),
    ("factor", "missing_or_invalid_adjustment"),
])
def test_T03_wrong_semantics_block_formal_data(source_spec, mutation, code):
    frame = QlibBinSource(source_spec.source).read(source_spec.fields)
    if mutation == "volume":
        frame[mutation] *= 100
    elif mutation == "change":
        frame[mutation] += 1
    else:
        frame[mutation] = np.nan
    issues = normalized_issues(normalize(frame, source_spec))
    assert code in {issue["code"] for issue in issues}

def test_audit_source_is_read_only_and_full_range(source_spec):
    before = source_hashes(source_spec.source)
    report = audit_source(source_spec)
    assert report["status"] == "passed"
    assert report["instrument_count"] == 3
    assert report["row_count"] == 540
    assert source_hashes(source_spec.source) == before

def test_snapshot_reuse_and_hash_verification(source_spec):
    policy = UniversePolicy(minimum_listing_days=0, liquidity_lookback=1)
    first = build_snapshot(source_spec.source, source_spec, policy)
    second = build_snapshot(source_spec.source, source_spec, policy)
    assert first.snapshot_id == second.snapshot_id
    (first.path / "calendar.txt").write_text("corrupt")
    with pytest.raises(IntegrityError):
        load_snapshot(first.path)

def test_snapshot_no_original_source_writes(source_spec):
    before = source_hashes(source_spec.source)
    build_snapshot(source_spec.source, source_spec,
                   UniversePolicy(minimum_listing_days=0, liquidity_lookback=1))
    assert source_hashes(source_spec.source) == before

def test_unverified_units_and_metadata_rejected(source_spec):
    source_spec.volume_unit = None
    with pytest.raises(ConfigurationError, match="Unverified"):
        normalize(QlibBinSource(source_spec.source).read(source_spec.fields), source_spec)
    source_spec.point_in_time_metadata = False
    with pytest.raises(ConfigurationError, match="PIT"):
        build_snapshot(source_spec.source, source_spec)

def test_T23_future_metadata_not_used(panel, metadata, calendar):
    metadata["available_time"] = calendar[100]
    result = resolve(calendar[60], panel, metadata, calendar,
                     UniversePolicy(minimum_listing_days=0))
    assert not result.eligible.any()
    assert set(result.reason) == {"unknown_historical_metadata"}

def test_historical_liquidity_ignores_future(panel, metadata, calendar):
    policy = UniversePolicy(minimum_listing_days=0, minimum_average_amount=1000)
    first = resolve(calendar[60], panel, metadata, calendar, policy)
    changed = panel.copy()
    changed.loc[changed.index.get_level_values("datetime") > calendar[60], "amount_currency"] = 1e12
    assert_frame_equal(first, resolve(calendar[60], changed, metadata, calendar, policy))

def test_T24_monthly_schedule_maps_weekends_and_deduplicates():
    calendar = pd.bdate_range("2023-01-01", "2023-02-28")
    # January 15 is Sunday; execution maps to Friday, January 13.
    assert list(rebalance_dates(calendar)) == list(pd.DatetimeIndex([
        "2023-01-13", "2023-01-31", "2023-02-15", "2023-02-28"]))

def test_research_view_physically_excludes_holdout(source_spec):
    snapshot = build_snapshot(source_spec.source, source_spec,
                              UniversePolicy(minimum_listing_days=0, liquidity_lookback=1))
    research = pd.read_parquet(snapshot.path / "research" / "panel.parquet")
    holdout = pd.read_parquet(snapshot.path / "holdout" / "panel.parquet")
    assert research.index.get_level_values("datetime").max() < pd.Timestamp(source_spec.holdout_start)
    assert holdout.index.get_level_values("datetime").min() >= pd.Timestamp(source_spec.holdout_start)


def test_snapshot_publication_retries_same_verified_tree_on_windows_denial(source_spec, monkeypatch):
    import etf_ml.data.snapshot as module
    actual = module.os.replace
    attempts, delays = [], []
    def replace(source, target):
        if source.is_dir() and target.parent == source_spec.artifact_root:
            attempts.append((source, target))
            if len(attempts) == 1:
                error = PermissionError("temporary sharing denial"); error.winerror = 32
                raise error
        return actual(source, target)
    monkeypatch.setattr(module.os, "replace", replace)
    monkeypatch.setattr(module.time, "sleep", delays.append)
    snapshot = build_snapshot(source_spec.source, source_spec, UniversePolicy(minimum_listing_days=0, liquidity_lookback=1))
    assert len(attempts) == 2 and attempts[0] == attempts[1] and delays == [0.05]
    assert load_snapshot(snapshot.path).snapshot_id == snapshot.snapshot_id


def test_snapshot_publication_permanent_denial_does_not_publish_success(source_spec, monkeypatch):
    import etf_ml.data.snapshot as module
    actual = module.os.replace
    attempts = []
    def replace(source, target):
        if source.is_dir() and target.parent == source_spec.artifact_root:
            attempts.append((source, target))
            error = PermissionError("persistent access denial"); error.winerror = 5
            raise error
        return actual(source, target)
    monkeypatch.setattr(module.os, "replace", replace)
    monkeypatch.setattr(module.time, "sleep", lambda delay: None)
    with pytest.raises(PermissionError):
        build_snapshot(source_spec.source, source_spec, UniversePolicy(minimum_listing_days=0, liquidity_lookback=1))
    assert len(attempts) == 6 and len(set(attempts)) == 1
    assert not [p for p in source_spec.artifact_root.glob("*/snapshot_manifest.json") if not p.parent.name.startswith(".")]
