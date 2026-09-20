import copy

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from etf_ml.data.limit_supplement import map_source_limits, load_limit_supplement, apply_limit_supplement
from etf_ml.data.normalize import trading_reasons
from etf_ml.data.snapshot import build_snapshot, audit_source
from etf_ml.contracts import UniversePolicy
from etf_ml.errors import QualityError
from etf_ml.utils import file_hash


def limit_fixture(tmp_path, panel, *, clock="09:25:00", receipt=False):
    raw = panel.reset_index()[["datetime", "instrument"]].rename(columns={"instrument":"ts_code"})
    raw["trade_date"] = raw.pop("datetime").dt.strftime("%Y%m%d")
    raw["up_limit"], raw["down_limit"] = 5., 1.
    if receipt:
        raw["available_time"] = pd.to_datetime(raw.trade_date, format="%Y%m%d").dt.tz_localize("Asia/Shanghai") + pd.Timedelta(hours=9)
        policy = {"basis":"recorded_receipt"}
    else:
        policy = {"basis":"source_documented_schedule", "local_time":clock,
                  "timezone":"Asia/Shanghai", "explanation":"Synthetic test policy; not an actual historical receipt"}
    source = tmp_path / "limit_source.parquet"
    raw.to_parquet(source, index=False)
    mapped = map_source_limits(raw, policy)
    mapped.attrs = {"availability_policy":policy,
                    "source_responses":[{"evidence_id":"limits", "path":source.name,
                       "url":"https://api.tushare.pro", "sha256":file_hash(source)}]}
    path = tmp_path / "limits.parquet"
    mapped.to_parquet(path)
    return raw, mapped, path


@pytest.mark.parametrize("receipt", [False, True])
def test_bound_limit_source_reloads_with_explicit_timing(tmp_path, panel, calendar, receipt):
    _, canonical, path = limit_fixture(tmp_path, panel, receipt=receipt)
    loaded, hashes = load_limit_supplement(path, calendar, panel.index.get_level_values("instrument").unique())
    assert_frame_equal(loaded, canonical)
    result, report, actual = apply_limit_supplement(panel, path, calendar)
    assert actual == hashes and len(hashes) == 2
    assert report["status"] == "passed" and report["actual_historical_receipts_proven"] == receipt
    assert result.buyable.all() and result.sellable.all()


@pytest.mark.parametrize("edge,side,other,reason", [(5.,"buyable","sellable","limit_up"),
                                                   (1.,"sellable","buyable","limit_down")])
def test_opening_limit_blocks_only_the_restricted_direction(tmp_path, panel, calendar, edge, side, other, reason):
    _, _, path = limit_fixture(tmp_path,panel)
    key = (calendar[10], "510300.SH")
    panel.loc[key,"raw_open"] = edge
    before = panel.copy(deep=True)
    result,_,_ = apply_limit_supplement(panel,path,calendar)
    assert not result.loc[key,side] and result.loc[key,other]
    assert trading_reasons(result).loc[key,"buy_reason" if side == "buyable" else "sell_reason"] == reason
    assert_frame_equal(panel,before)
    late = panel.copy()
    late.loc[late.index.get_level_values("datetime") > calendar[10],"raw_open"] = 99.
    future,_,_ = apply_limit_supplement(late,path,calendar)
    assert_frame_equal(future.loc[:calendar[10]],result.loc[:calendar[10]])


def test_late_receipts_block_execution_and_qualified_snapshot(tmp_path,panel,calendar,source_spec):
    _,_,path = limit_fixture(tmp_path,panel,clock="10:00:00")
    result,report,_ = apply_limit_supplement(panel,path,calendar)
    assert not result[["buyable","sellable"]].any().any()
    assert report["status"] == "incomplete"
    assert trading_reasons(result).buy_reason.eq("limits_unavailable_at_open").all()
    source_spec.limits_path = path
    with pytest.raises(QualityError,match="unavailable"):
        build_snapshot(source_spec.source,source_spec,UniversePolicy(minimum_listing_days=0,liquidity_lookback=1))
    assert not list(source_spec.artifact_root.glob("*/snapshot_manifest.json"))
    assert "daily_limits_unavailable_at_open" in {e["code"] for e in audit_source(source_spec)["errors"]}


@pytest.mark.parametrize("change", ["canonical_price","canonical_time","source_price","missing_source",
                                    "missing_quotes","duplicate_source","weak_policy","wrong_scope"])
def test_daily_limits_cannot_be_rewritten_or_partially_substituted(tmp_path,panel,calendar,change):
    raw,mapped,path = limit_fixture(tmp_path,panel)
    if change == "canonical_price":
        mapped.iloc[0,mapped.columns.get_loc("up_limit")] = 10.
    elif change == "canonical_time":
        mapped.iloc[0,mapped.columns.get_loc("available_time")] += pd.Timedelta(hours=1)
    elif change == "source_price":
        raw.up_limit = 10.;raw.to_parquet(tmp_path/"limit_source.parquet",index=False)
    elif change == "missing_source":
        (tmp_path/"limit_source.parquet").unlink()
    elif change == "missing_quotes":
        raw = raw.iloc[1:].copy();raw.to_parquet(tmp_path/"limit_source.parquet",index=False)
        mapped = map_source_limits(raw,mapped.attrs["availability_policy"])
        mapped.attrs = {"availability_policy": {"basis":"source_documented_schedule","local_time":"09:25:00","timezone":"Asia/Shanghai","explanation":"Synthetic"},
                        "source_responses":[{"evidence_id":"limits","path":"limit_source.parquet","url":"https://api.tushare.pro","sha256":file_hash(tmp_path/"limit_source.parquet")}]}
    elif change == "duplicate_source":
        mapped.attrs["source_responses"].append(copy.deepcopy(mapped.attrs["source_responses"][0]))
    elif change == "weak_policy":
        mapped.attrs["availability_policy"].pop("explanation")
    else:
        calendar = calendar[:-1]
    mapped.to_parquet(path)
    with pytest.raises((QualityError,ValueError)):
        apply_limit_supplement(panel,path,calendar)


@pytest.mark.parametrize("bad", [0., -1., np.inf, np.nan, True, "5.0"])
def test_source_limit_prices_need_explicit_valid_numeric_values(tmp_path,panel,bad):
    raw,mapped,_ = limit_fixture(tmp_path,panel)
    raw.up_limit = bad
    with pytest.raises(QualityError):
        map_source_limits(raw,mapped.attrs["availability_policy"])


def test_snapshot_binds_both_canonical_limits_and_original_response(tmp_path,panel,source_spec):
    _,_,path = limit_fixture(tmp_path,panel)
    source_spec.limits_path = path
    snapshot = build_snapshot(source_spec.source,source_spec,UniversePolicy(minimum_listing_days=0,liquidity_lookback=1))
    assert str(path.resolve()) in snapshot.manifest["external_hashes"]
    assert str((tmp_path/"limit_source.parquet").resolve()) in snapshot.manifest["external_hashes"]
    persisted = pd.read_parquet(snapshot.path/"panel.parquet")
    assert persisted.limits_known_at_open.all()
    assert pd.read_parquet(snapshot.path/"universe.parquet").buy_reason.isna().all()
