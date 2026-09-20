import numpy as np
import pandas as pd
import pytest

from etf_ml.data.action_consistency import adjustment_event_consistency
from etf_ml.errors import QualityError


def inputs():
    calendar = pd.bdate_range("2025-02-10", periods=5)
    index = pd.MultiIndex.from_product([calendar, ["ETF"]], names=["datetime", "instrument"])
    panel = pd.DataFrame({"raw_close": [10., 10., 9.2, 9.4, 9.3],
                          "adjustment_factor": [1., 1., 10/9, 10/9, 10/9]}, index=index)
    events = pd.DataFrame([{"event_id": "cash", "instrument": "ETF", "datetime": calendar[2],
                            "cash_per_share": 1., "share_multiplier": 1., "record_date": calendar[0]}])
    return panel, events, calendar


def test_cash_adjustment_uses_previous_raw_close_not_market_return():
    panel, events, calendar = inputs()
    result = adjustment_event_consistency(panel, events, calendar)
    assert result["status"] == "passed"
    assert result["checked_quote_pairs"] == 4 and result["checked_event_days"] == 1
    assert result["maximum_relative_residual"] < 1e-12
    assert result["source_completeness_verified"] is False


def test_factor_changes_without_events_blocked():
    panel, _, calendar = inputs()
    result = adjustment_event_consistency(panel, None, calendar)
    assert result["status"] == "failed"
    assert result["issues"] == [{"code": "unexplained_adjustment_change", "rows": 1}]


def test_wrong_cash_unit_and_missing_factor_change_blocked():
    panel, events, calendar = inputs()
    events.cash_per_share = .1
    assert adjustment_event_consistency(panel, events, calendar)["status"] == "failed"
    events.cash_per_share = 1.
    panel.adjustment_factor = 1.
    assert adjustment_event_consistency(panel, events, calendar)["status"] == "failed"


def test_market_price_moves_with_constant_factor_need_no_event():
    panel, _, calendar = inputs()
    panel.adjustment_factor = 1.
    assert adjustment_event_consistency(panel, None, calendar)["status"] == "passed"


def test_split_between_record_and_ex_date_converts_cash_to_current_share():
    panel, events, calendar = inputs()
    split = {"event_id": "split", "instrument": "ETF", "datetime": calendar[1],
             "cash_per_share": 0., "share_multiplier": 2., "record_date": pd.NaT}
    events = pd.concat([pd.DataFrame([split]), events], ignore_index=True)
    panel.raw_close = [10., 5., 4.5, 4.6, 4.7]
    panel.adjustment_factor = [1., 2., 2*5/4.5, 2*5/4.5, 2*5/4.5]
    assert adjustment_event_consistency(panel, events, calendar)["status"] == "passed"


def test_missing_quote_at_event_not_claimed_checked():
    panel, events, calendar = inputs()
    panel.loc[(calendar[1], "ETF"), "raw_close"] = np.nan
    result = adjustment_event_consistency(panel, events, calendar)
    assert result["status"] == "incomplete" and result["unchecked_event_days"] == 1


@pytest.mark.parametrize("bad", [0, -1, np.inf, np.nan])
def test_missing_or_invalid_quoted_factor_refused(bad):
    panel, events, calendar = inputs()
    panel.loc[(calendar[1], "ETF"), "adjustment_factor"] = bad
    with pytest.raises(QualityError):
        adjustment_event_consistency(panel, events, calendar)


@pytest.mark.parametrize("bad", [True, 0, -1, np.inf])
def test_invalid_tolerance_refused(bad):
    panel, events, calendar = inputs()
    with pytest.raises(QualityError):
        adjustment_event_consistency(panel, events, calendar, tolerance=bad)


def rounded_cash_inputs():
    calendar = pd.bdate_range("2025-01-20", periods=3)
    index = pd.MultiIndex.from_product([calendar, ["ETF"]], names=["datetime", "instrument"])
    panel = pd.DataFrame({"raw_close": [.981, .981, .973], "reference_close": [.976, .980, .981],
                          "adjustment_factor": [1., 1.001, 1.001]}, index=index)
    events = pd.DataFrame([{"event_id": "cash", "instrument": "ETF", "datetime": calendar[1],
                            "cash_per_share": .0015, "share_multiplier": 1., "record_date": calendar[0]}])
    return panel, events, calendar


def test_declared_source_reference_precision_preserves_exact_cash_and_reports_rounding():
    panel, events, calendar = rounded_cash_inputs()
    original = events.copy(deep=True)
    assert adjustment_event_consistency(panel, events, calendar)["status"] == "failed"
    result = adjustment_event_consistency(panel, events, calendar, reference_close_tick=.001)
    assert result["status"] == "passed" and result["rounding_supported_event_days"] == 1
    assert result["maximum_relative_residual"] > result["relative_tolerance"]
    assert result["maximum_effective_relative_residual"] < result["relative_tolerance"]
    pd.testing.assert_frame_equal(events, original)


@pytest.mark.parametrize("change", ["wrong_cash", "wrong_factor", "off_grid", "no_event"])
def test_reference_precision_does_not_infer_missing_actions_or_accept_inconsistent_sources(change):
    panel, events, calendar = rounded_cash_inputs()
    if change == "wrong_cash":
        events.cash_per_share = .01
    elif change == "wrong_factor":
        panel.loc[(calendar[1], "ETF"), "adjustment_factor"] = 1.01
    elif change == "off_grid":
        panel.loc[(calendar[1], "ETF"), "reference_close"] = .9796
    else:
        events = None
        panel.adjustment_factor = [1., 2., 2.]
        panel.reference_close = [.976, .4905, .981]
    assert adjustment_event_consistency(panel, events, calendar, reference_close_tick=.001)["status"] == "failed"


@pytest.mark.parametrize("tick", [True, 0., -.001, .01, float("nan")])
def test_invalid_reference_precision_cannot_relax_gate(tick):
    panel, events, calendar = rounded_cash_inputs()
    with pytest.raises(QualityError, match="tick"):
        adjustment_event_consistency(panel, events, calendar, reference_close_tick=tick)


def test_declared_precision_without_source_reference_fails():
    panel, events, calendar = rounded_cash_inputs()
    with pytest.raises(QualityError, match="requires source reference"):
        adjustment_event_consistency(panel.drop(columns="reference_close"), events, calendar, reference_close_tick=.001)


def test_audit_and_snapshot_share_precision_policy_without_changing_cash(source_spec, calendar, tmp_path):
    from etf_ml.data.source import QlibBinSource, encode_provider
    from etf_ml.data.snapshot import audit_source, build_snapshot
    from etf_ml.contracts import UniversePolicy
    from etf_ml.utils import source_hashes

    raw = QlibBinSource(source_spec.source).read(source_spec.fields)
    instrument, day = "510300.SH", calendar[30]
    rows = raw.index.get_level_values("instrument") == instrument
    for col in ("open", "high", "low", "close", "reference_close"):
        raw.loc[rows, col] = .981
    raw.loc[(day, instrument), "reference_close"] = .980
    raw.loc[rows, "change"] = raw.loc[rows, "close"] / raw.loc[rows, "reference_close"] - 1
    raw.loc[rows, "amount"] = raw.loc[rows, "close"] * raw.loc[rows, "volume"]
    after = rows & (raw.index.get_level_values("datetime") >= day)
    raw.loc[after, "factor"] = 1.001
    clone = tmp_path / "rounded_reference_provider"
    encode_provider(raw, calendar, clone, {physical:logical for logical,physical in source_spec.fields.items()})
    source_spec.source = clone
    events = pd.DataFrame([{"event_id":"precise-cash", "instrument":instrument, "datetime":day,
                            "cash_per_share":.0015, "share_multiplier":1., "record_date":calendar[29],
                            "pay_date":calendar[32], "available_time":calendar[28].tz_localize("Asia/Shanghai")}])
    source_spec.events_path = tmp_path / "precise_events.parquet"
    events.to_parquet(source_spec.events_path, index=False)
    before = source_hashes(clone)
    assert audit_source(source_spec)["adjustment_validation"]["status"] == "failed"
    with pytest.raises(QualityError, match="consistency"):
        build_snapshot(clone, source_spec)
    source_spec.reference_close_tick = .001
    audit = audit_source(source_spec)
    assert audit["status"] == "passed"
    snapshot = build_snapshot(clone, source_spec, UniversePolicy(minimum_listing_days=0, liquidity_lookback=1))
    import json
    quality = json.loads((snapshot.path / "data_quality.json").read_text(encoding="utf-8"))
    assert quality["adjustment_validation"]["rounding_supported_event_days"] == 1
    assert snapshot.manifest["spec"]["reference_close_tick"] == .001
    assert pd.read_parquet(source_spec.events_path).cash_per_share.iloc[0] == .0015
    assert source_hashes(clone) == before
