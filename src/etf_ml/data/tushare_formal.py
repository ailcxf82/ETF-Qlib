"""Formal PIT metadata built from immutable Tushare ETF catalog responses.

The catalog APIs are current-state endpoints.  We therefore keep their raw
responses separately and use only their dated listing/delisting fields to
materialize a conservative, source-scheduled membership history.  Missing or
ambiguous records are rejected instead of being filled from the local Qlib
instrument list.
"""
from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from etf_ml.data.tushare_supplement import dividend_events
from etf_ml.errors import QualityError


_FOREIGN_MARKERS = ("QDII", "港", "香港", "恒生", "美国", "纳斯达克", "标普", "日经", "德国", "法国", "海外", "全球", "亚太")


def _date(value, field: str) -> pd.Timestamp:
    try:
        result = pd.to_datetime(value, format="%Y%m%d", errors="raise")
    except (TypeError, ValueError) as error:
        raise QualityError(f"Tushare {field} is missing or invalid") from error
    if pd.isna(result) or result.tzinfo is not None:
        raise QualityError(f"Tushare {field} is missing or invalid")
    return result.normalize()


def _end_of_source_date(day: pd.Timestamp) -> pd.Timestamp:
    """Date-only source fields become usable only after that source day closes."""
    return day + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)


def _require_unique(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    if frame.empty and "ts_code" not in frame:
        return pd.DataFrame(index=pd.Index([], name="ts_code"))
    if "ts_code" not in frame:
        raise QualityError(f"Tushare {name} response lacks ts_code")
    result = frame.copy()
    result = result[result.ts_code.notna()].copy()
    if result.ts_code.duplicated().any():
        raise QualityError(f"Tushare {name} response has duplicate ETF identities")
    return result.set_index("ts_code", drop=False)


def _domestic_equity(row: pd.Series) -> bool:
    values = " ".join(str(row.get(name, "") or "") for name in
                      ("etf_type", "fund_type", "type", "invest_type", "cname", "csname", "extname", "index_name"))
    if any(marker.lower() in values.lower() for marker in _FOREIGN_MARKERS):
        return False
    return str(row.get("fund_type", "")) == "股票型" or str(row.get("type", "")) == "股票型"


def _merged_row(code: str, etf: pd.DataFrame, fund: pd.DataFrame) -> pd.Series:
    pieces = [table.loc[code] for table in (etf, fund) if code in table.index]
    if not pieces:
        raise QualityError(f"No Tushare catalog record for {code}")
    merged: dict[str, object] = {}
    for piece in pieces:
        for key, value in piece.items():
            if key not in merged or pd.isna(merged[key]) or merged[key] in ("",):
                merged[key] = value
    return pd.Series(merged)


def _previous_calendar_day(calendar: pd.DatetimeIndex, day: pd.Timestamp) -> pd.Timestamp:
    earlier = calendar[calendar < day]
    if not len(earlier):
        raise QualityError("Delisting precedes the available authoritative calendar")
    return earlier[-1]


def materialize_metadata(
    instruments: Iterable[str],
    calendar: pd.DatetimeIndex,
    etf_listed: pd.DataFrame,
    etf_delisted: pd.DataFrame,
    fund_listed: pd.DataFrame,
    fund_delisted: pd.DataFrame,
) -> pd.DataFrame:
    """Create non-overlapping, date-available ETF membership intervals.

    The first interval is available after the listing date's close.  A
    delisting is a distinct non-operating interval available after its own
    source date, so a later retrieved catalog never exposes a future delist in
    the earlier operating interval.
    """
    if not isinstance(calendar, pd.DatetimeIndex) or not len(calendar) or calendar.has_duplicates:
        raise QualityError("Formal metadata needs a unique authoritative calendar")
    calendar = pd.DatetimeIndex(calendar).sort_values().normalize()
    listed_etf, delisted_etf = _require_unique(etf_listed, "etf_basic listed"), _require_unique(etf_delisted, "etf_basic delisted")
    listed_fund, delisted_fund = _require_unique(fund_listed, "fund_basic listed"), _require_unique(fund_delisted, "fund_basic delisted")
    listed = pd.concat([listed_etf, listed_fund[~listed_fund.index.isin(listed_etf.index)]])
    delisted = pd.concat([delisted_etf, delisted_fund[~delisted_fund.index.isin(delisted_etf.index)]])
    rows: list[dict[str, object]] = []
    for code in sorted(set(instruments)):
        if code not in listed.index and code not in delisted.index:
            raise QualityError(f"No Tushare catalog record for {code}")
        active = code in listed.index
        row = _merged_row(code, listed if active else delisted, listed_fund if active else delisted_fund)
        listing = _date(row.get("list_date"), "list_date")
        if listing > calendar[-1]:
            continue
        asset_class = "domestic_equity" if _domestic_equity(row) else "outside_or_unclassified"
        group = str(row.get("index_code") or code).strip() or code
        end = calendar[-1]
        if not active:
            delist_source = row.get("delist_date")
            if pd.isna(delist_source) or str(delist_source).strip() == "":
                raise QualityError(f"Tushare delisted ETF {code} lacks delist_date")
            delist = _date(delist_source, "delist_date")
            end = _previous_calendar_day(calendar, delist)
            if end >= listing:
                rows.append({"instrument": code, "valid_from": listing, "valid_to": end,
                             "available_time": _end_of_source_date(listing), "listing_date": listing,
                             "asset_class": asset_class, "tracking_group": group, "operating": True})
            if delist <= calendar[-1]:
                rows.append({"instrument": code, "valid_from": delist, "valid_to": calendar[-1],
                             "available_time": _end_of_source_date(delist), "listing_date": listing,
                             "asset_class": asset_class, "tracking_group": group, "operating": False})
            continue
        rows.append({"instrument": code, "valid_from": listing, "valid_to": end,
                     "available_time": _end_of_source_date(listing), "listing_date": listing,
                     "asset_class": asset_class, "tracking_group": group, "operating": True})
    metadata = pd.DataFrame(rows, columns=["instrument", "valid_from", "valid_to", "available_time",
                                            "listing_date", "asset_class", "tracking_group", "operating"])
    if metadata.empty:
        raise QualityError("Tushare catalog produced no historical metadata")
    metadata.attrs = {
        "metadata_mode": "formal_tushare_source_documented_schedule",
        "availability_policy": "date_only_catalog_fields_available_after_source_date_close",
        "source": "tushare.etf_basic+tushare.fund_basic",
        "limitations": [
            "Catalog availability is a documented date-only schedule, not a recorded historical receipt timestamp.",
            "The materializer rejects missing catalog identities and delisting dates; it never backfills from local Qlib metadata.",
        ],
    }
    return metadata.sort_values(["instrument", "valid_from"]).reset_index(drop=True)


def materialize_dividends(
    responses: dict[str, pd.DataFrame], metadata: pd.DataFrame, calendar: pd.DatetimeIndex,
) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    """Map Tushare dividends without hiding in-scope source anomalies.

    Date-less implemented distributions are known to occur in money-market
    funds.  Those may be retained as explicit exclusions only when metadata
    already proves that the instrument is outside the domestic-equity strategy
    scope.  The same anomaly for an in-scope ETF remains a hard failure.
    """
    required = {"instrument", "asset_class"}
    if not required.issubset(metadata):
        raise QualityError("Formal dividend mapping needs historical metadata")
    domestic = set(metadata.loc[metadata.asset_class.eq("domestic_equity"), "instrument"])
    pieces, exclusions = [], []
    for instrument, raw in sorted(responses.items()):
        try:
            mapped = dividend_events(raw, calendar, [instrument])
        except QualityError as error:
            if instrument in domestic:
                raise QualityError(f"In-scope Tushare dividend mapping failed for {instrument}: {error}") from error
            exclusions.append({"instrument": instrument, "raw_rows": len(raw), "reason": str(error),
                               "excluded_because": "outside_domestic_equity_scope"})
            continue
        if not mapped.empty:
            pieces.append(mapped)
    events = (pd.concat(pieces, ignore_index=True) if pieces else
              pd.DataFrame(columns=["event_id", "datetime", "instrument", "cash_per_share", "share_multiplier", "sequence"]))
    events.attrs = {"source": "tushare.fund_div", "source_completeness_verified": False,
                    "unmapped_event_inputs": exclusions,
                    "event_scope": "all mapped Tushare fund_div responses; exclusions are explicit"}
    return events, exclusions
