import pandas as pd
import pytest

from etf_ml.data.tushare_formal import materialize_dividends, materialize_metadata
from etf_ml.errors import QualityError


def _etf(code, status, date, **extra):
    return {"ts_code": code, "list_status": status, "list_date": date,
            "index_code": "000300.SH", "etf_type": "境内", **extra}


def _fund(code, status, date, **extra):
    return {"ts_code": code, "status": status, "list_date": date,
            "fund_type": "股票型", "type": "股票型", **extra}


def test_formal_metadata_uses_listing_and_delisting_intervals():
    calendar = pd.bdate_range("2020-01-01", "2020-01-10")
    result = materialize_metadata(["510300.SH", "510500.SH"], calendar,
        pd.DataFrame([_etf("510300.SH", "L", "20200102")]),
        pd.DataFrame([_etf("510500.SH", "D", "20200102")]),
        pd.DataFrame([_fund("510300.SH", "L", "20200102")]),
        pd.DataFrame([_fund("510500.SH", "D", "20200102", delist_date="20200108")]))
    retired = result[result.instrument.eq("510500.SH")].reset_index(drop=True)
    assert retired.operating.tolist() == [True, False]
    assert retired.valid_to.iloc[0] == pd.Timestamp("2020-01-07")
    assert retired.valid_from.iloc[1] == pd.Timestamp("2020-01-08")
    assert result.attrs["metadata_mode"] == "formal_tushare_source_documented_schedule"


def test_formal_metadata_rejects_uncovered_or_undated_delisting():
    calendar = pd.bdate_range("2020-01-01", "2020-01-10")
    with pytest.raises(QualityError, match="No Tushare catalog"):
        materialize_metadata(["510300.SH"], calendar, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
    with pytest.raises(QualityError, match="lacks delist_date"):
        materialize_metadata(["510300.SH"], calendar, pd.DataFrame(), pd.DataFrame([_etf("510300.SH", "D", "20200102")]),
                             pd.DataFrame(), pd.DataFrame([_fund("510300.SH", "D", "20200102")]))


def test_qdii_or_non_stock_fund_is_not_domestic_equity():
    calendar = pd.bdate_range("2020-01-01", "2020-01-10")
    result = materialize_metadata(["513100.SH"], calendar,
        pd.DataFrame([_etf("513100.SH", "L", "20200102", etf_type="QDII", index_name="纳斯达克")]),
        pd.DataFrame(), pd.DataFrame([_fund("513100.SH", "L", "20200102")]), pd.DataFrame())
    assert result.asset_class.tolist() == ["outside_or_unclassified"]


def test_dividend_mapping_rejects_in_scope_anomaly_and_records_outside_scope():
    calendar = pd.bdate_range("2020-01-01", "2020-01-10")
    metadata = pd.DataFrame({"instrument": ["510300.SH", "511990.SH"],
                             "asset_class": ["domestic_equity", "outside_or_unclassified"]})
    raw = pd.DataFrame({"ts_code": ["511990.SH"], "div_proc": ["实施"], "ex_date": [None],
                        "record_date": ["20200103"], "pay_date": ["20200106"], "ann_date": ["20200102"], "div_cash": [0.1]})
    _, exclusions = materialize_dividends({"511990.SH": raw}, metadata, calendar)
    assert exclusions[0]["instrument"] == "511990.SH"
    bad = raw.copy(); bad.ts_code = "510300.SH"
    with pytest.raises(QualityError, match="In-scope Tushare dividend"):
        materialize_dividends({"510300.SH": bad}, metadata, calendar)
