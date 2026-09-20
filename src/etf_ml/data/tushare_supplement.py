"""Minimal Tushare supplement adapters; raw ETF provider stays read-only."""
from __future__ import annotations

import numpy as np
import pandas as pd

from etf_ml.errors import QualityError


def csi300_benchmark(raw: pd.DataFrame, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    from etf_ml.data.calendar import require_calendar
    require_calendar(calendar)
    required = {"ts_code", "trade_date", "open", "close"}
    if raw.empty or not required.issubset(raw):
        raise QualityError("Missing CSI300 source fields")
    if not raw.ts_code.eq("399300.SZ").all():
        raise QualityError("Expected the CSI300 price index, not an ETF proxy")
    dates = pd.to_datetime(raw.trade_date, format="%Y%m%d", errors="raise")
    if dates.isna().any() or dates.duplicated().any():
        raise QualityError("Invalid or duplicate CSI300 dates")
    result = raw[["open", "close"]].copy()
    if any(not pd.api.types.is_numeric_dtype(dtype) or pd.api.types.is_bool_dtype(dtype) for dtype in result.dtypes):
        raise QualityError("CSI300 prices must be numeric")
    if not np.isfinite(result.to_numpy()).all() or result.le(0).any().any():
        raise QualityError("CSI300 prices must be finite and positive")
    result.index = pd.DatetimeIndex(dates, name="datetime")
    result = result.sort_index()
    if not result.reindex(calendar).notna().all().all():
        raise QualityError("CSI300 source does not cover the requested calendar")
    result = result.reindex(calendar)
    result.index.name = "datetime"
    result.attrs = {"benchmark_id": "CSI300", "source": "tushare.index_daily",
                    "ts_code": "399300.SZ", "price_convention": "price_index"}
    return result


def adjustment_factors(raw: pd.DataFrame) -> pd.Series:
    if raw.empty or not {"ts_code", "trade_date", "adj_factor"}.issubset(raw):
        raise QualityError("Missing Tushare adjustment fields")
    if raw.ts_code.isna().any() or not raw.ts_code.map(
            lambda x: isinstance(x, str) and bool(x) and all(c.isalnum() or c == "." for c in x)).all():
        raise QualityError("Invalid adjustment instrument identity")
    values = raw.adj_factor
    if (not pd.api.types.is_numeric_dtype(values) or pd.api.types.is_bool_dtype(values) or
            not np.isfinite(values).all() or values.le(0).any()):
        raise QualityError("Adjustment factors must be finite positive numbers")
    dates = pd.to_datetime(raw.trade_date, format="%Y%m%d", errors="raise")
    index = pd.MultiIndex.from_arrays([dates, raw.ts_code], names=["datetime", "instrument"])
    if dates.isna().any() or index.has_duplicates:
        raise QualityError("Invalid or duplicate adjustment keys")
    return pd.Series(values.to_numpy(), index=index, name="factor").sort_index()


def apply_adjustment_supplement(frame: pd.DataFrame, path) -> pd.DataFrame:
    factors = adjustment_factors(pd.read_parquet(path)).reindex(frame.index)
    quoted = frame[["open", "close"]].notna().all(axis=1)
    if factors[quoted].isna().any():
        raise QualityError("Adjustment supplement does not cover all actual ETF quotes")
    existing = frame.factor.notna() & factors.notna()
    if not np.isclose(frame.factor[existing], factors[existing], rtol=1e-6, atol=0).all():
        raise QualityError("Adjustment supplement conflicts with existing provider factors")
    result = frame.copy()
    result["factor"] = factors.combine_first(frame.factor)
    return result


def dividend_events(raw: pd.DataFrame, calendar: pd.DatetimeIndex, instruments) -> pd.DataFrame:
    from etf_ml.data.calendar import require_calendar
    require_calendar(calendar)
    from etf_ml.data.actions import validate_events
    from etf_ml.utils import content_hash
    required = {"ts_code", "div_proc", "ex_date", "record_date", "pay_date", "div_cash", "ann_date"}
    if not required.issubset(raw):
        raise QualityError("Missing Tushare dividend fields")
    selected = raw[raw.div_proc.eq("\u5b9e\u65bd") & raw.ts_code.isin(instruments)].copy()
    ex_dates = pd.to_datetime(selected.ex_date, format="%Y%m%d", errors="raise")
    if ex_dates.isna().any():
        raise QualityError("Implemented dividend has no ex-date")
    selected = selected[(ex_dates >= calendar.min()) & (ex_dates <= calendar.max())]
    rows = []
    for event in selected.to_dict("records"):
        cash = event["div_cash"]
        if isinstance(cash, (bool, str)) or not isinstance(cash, (int, float)) or not np.isfinite(cash) or cash <= 0:
            raise QualityError("Implemented dividend cash must be finite positive yuan per share")
        dates = {}
        for field in ("ex_date", "record_date", "pay_date", "ann_date"):
            date = pd.to_datetime(event[field], format="%Y%m%d", errors="raise")
            if pd.isna(date):
                raise QualityError("Implemented dividend requires explicit dates")
            dates[field] = date
        announcement = dates["ann_date"]
        if pd.notna(event.get("imp_anndate")):
            implementation = pd.to_datetime(event["imp_anndate"], format="%Y%m%d", errors="raise")
            announcement = max(announcement, implementation)
        # Date-only source: assume available by that date's close, never at its opening.
        known = (announcement + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)).tz_localize("Asia/Shanghai")
        identity = {"instrument": event["ts_code"], "ex_date": dates["ex_date"].isoformat(),
                    "record_date": dates["record_date"].isoformat(), "cash": float(cash)}
        rows.append({"event_id": "tushare-div-" + content_hash(identity)[:24],
                     "datetime": dates["ex_date"], "instrument": event["ts_code"],
                     "cash_per_share": float(cash), "share_multiplier": 1., "sequence": 0,
                     "record_date": dates["record_date"], "pay_date": dates["pay_date"],
                     "available_time": known})
    frame = pd.DataFrame(rows, columns=["event_id", "datetime", "instrument", "cash_per_share",
                                      "share_multiplier", "sequence", "record_date", "pay_date", "available_time"])
    source_rows = len(frame)
    # Source may repeat one per-share distribution with different ancillary fund totals.
    # Collapse only identical mapped economics; conflicting dates/cash remain errors.
    frame = frame.drop_duplicates().reset_index(drop=True)
    result, _ = validate_events(frame, calendar, instruments)
    result.attrs = {"source": "tushare.fund_div", "source_completeness_verified": False,
                    "source_rows_in_scope": source_rows, "collapsed_equivalent_rows": source_rows - len(frame),
                    "announcement_resolution": "conservative_end_of_source_date",
                    "share_actions_included": False}
    return result
