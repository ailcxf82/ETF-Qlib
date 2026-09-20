import numpy as np
import pandas as pd
import pytest

from etf_ml.data.tushare_supplement import csi300_benchmark
from etf_ml.errors import QualityError


def example():
    calendar = pd.to_datetime(["2025-01-02", "2025-01-03"])
    raw = pd.DataFrame({"ts_code": ["399300.SZ"] * 2, "trade_date": ["20250103", "20250102"],
                        "open": [3001., 3000.], "close": [3002., 3001.]})
    return raw, calendar


def test_actual_index_identity_and_parquet_attrs(tmp_path):
    raw, calendar = example()
    result = csi300_benchmark(raw, calendar)
    assert result.index.tolist() == calendar.tolist()
    assert result.open.tolist() == [3000., 3001.]
    result.to_parquet(tmp_path / "benchmark.parquet")
    assert pd.read_parquet(tmp_path / "benchmark.parquet").attrs["benchmark_id"] == "CSI300"


@pytest.mark.parametrize("bad", [0, -1, np.nan, np.inf, True, "3000"])
def test_invalid_index_prices(bad):
    raw, calendar = example()
    raw["open"] = [bad, bad]
    with pytest.raises(QualityError):
        csi300_benchmark(raw, calendar)


def test_missing_dates_not_filled():
    raw, calendar = example()
    with pytest.raises(QualityError):
        csi300_benchmark(raw.iloc[:1], calendar)


def test_proxy_and_duplicate_dates_rejected():
    raw, calendar = example()
    raw.ts_code = "510300.SH"
    with pytest.raises(QualityError):
        csi300_benchmark(raw, calendar)
    raw.ts_code = "399300.SZ"
    raw.trade_date = "20250102"
    with pytest.raises(QualityError):
        csi300_benchmark(raw, calendar)


from etf_ml.data.tushare_supplement import adjustment_factors, apply_adjustment_supplement


def test_real_adjustment_overlay_without_mutating_source(tmp_path):
    raw = pd.DataFrame({"ts_code": ["510300.SH"] * 2, "trade_date": ["20250102", "20250103"],
                        "adj_factor": [1., 1.25]})
    raw.to_parquet(tmp_path / "factors.parquet")
    factors = adjustment_factors(raw)
    source = pd.DataFrame({"open": [4., 3.2], "close": [4., 3.2], "factor": [np.nan, np.nan]}, index=factors.index)
    result = apply_adjustment_supplement(source, tmp_path / "factors.parquet")
    assert source.factor.isna().all()
    assert result.factor.tolist() == [1., 1.25]
    assert (result.close * result.factor).tolist() == [4., 4.]
    source.loc[:, "factor"] = 1.
    with pytest.raises(QualityError):
        apply_adjustment_supplement(source, tmp_path / "factors.parquet")
    raw.iloc[:1].to_parquet(tmp_path / "factors.parquet")
    with pytest.raises(QualityError):
        apply_adjustment_supplement(source, tmp_path / "factors.parquet")


@pytest.mark.parametrize("bad", [0, -1, np.inf, np.nan, True])
def test_bad_adjustment_values_rejected(bad):
    raw = pd.DataFrame({"ts_code": ["510300.SH"], "trade_date": ["20250102"], "adj_factor": [bad]})
    with pytest.raises(QualityError):
        adjustment_factors(raw)


def test_duplicate_adjustment_keys_rejected():
    raw = pd.DataFrame({"ts_code": ["510300.SH"] * 2, "trade_date": ["20250102"] * 2, "adj_factor": [1., 1.]})
    with pytest.raises(QualityError):
        adjustment_factors(raw)


from etf_ml.data.tushare_supplement import dividend_events


def dividend_example():
    calendar = pd.bdate_range("2025-01-02", "2025-01-10")
    raw = pd.DataFrame({"ts_code": ["510300.SH"], "div_proc": ["\u5b9e\u65bd"],
                        "ex_date": ["20250109"], "record_date": ["20250103"], "pay_date": ["20250110"],
                        "ann_date": ["20250102"], "imp_anndate": ["20250102"], "div_cash": [0.25]})
    return raw, calendar


def test_real_dividend_maps_record_date_payment_and_yuan_per_share():
    raw, calendar = dividend_example()
    result = dividend_events(raw, calendar, ["510300.SH"])
    assert result.cash_per_share.tolist() == [0.25]
    assert result.record_date.iloc[0] == pd.Timestamp("2025-01-03")
    assert result.pay_date.iloc[0] == pd.Timestamp("2025-01-10")
    assert result.available_time.iloc[0] == pd.Timestamp("2025-01-02 23:59:59", tz="Asia/Shanghai")
    assert result.attrs["source_completeness_verified"] is False
    assert result.attrs["share_actions_included"] is False
    assert dividend_events(raw, calendar, ["510300.SH"]).event_id.tolist() == result.event_id.tolist()


def test_unimplemented_or_other_instrument_dividend_not_fabricated():
    raw, calendar = dividend_example()
    raw.div_proc = "proposal"
    assert dividend_events(raw, calendar, ["510300.SH"]).empty
    raw.div_proc = "\u5b9e\u65bd"
    assert dividend_events(raw, calendar, ["510500.SH"]).empty


@pytest.mark.parametrize("field,value", [("record_date", "20250109"), ("pay_date", "20250108"),
                                         ("ann_date", "20250109"), ("imp_anndate", "20250109"),
                                         ("div_cash", 0), ("div_cash", np.nan), ("div_cash", "0.25")])
def test_bad_dividend_mapping_refused(field, value):
    raw, calendar = dividend_example()
    raw[field] = value
    with pytest.raises(QualityError):
        dividend_events(raw, calendar, ["510300.SH"])


def test_equivalent_source_distributions_counted_once_with_explicit_report():
    raw, calendar = dividend_example()
    result = dividend_events(pd.concat([raw, raw]), calendar, ["510300.SH"])
    assert len(result) == 1
    assert result.attrs["source_rows_in_scope"] == 2
    assert result.attrs["collapsed_equivalent_rows"] == 1


def test_conflicting_source_payment_not_collapsed():
    raw, calendar = dividend_example()
    changed = raw.copy()
    changed.pay_date = "20250113"
    with pytest.raises(QualityError):
        dividend_events(pd.concat([raw, changed]), calendar, ["510300.SH"])
