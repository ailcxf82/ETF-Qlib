"""Money-fund income intervals, kept separate from prices and cash dividends.

Some vendors publish a holiday's summed income. That is not enough to infer
per-day income, compounding or investor-level cent rounding.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
import numpy as np
import pandas as pd
from etf_ml.errors import QualityError
from etf_ml.data.source import require_panel


def _number(value, *, positive=False):
    if isinstance(value, (bool, np.bool_)):
        raise QualityError("Money income values must be numeric, not boolean")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise QualityError("Invalid money income number") from error
    if not number.is_finite() or (positive and number <= 0):
        raise QualityError("Invalid money income number or unit")
    result = float(number)
    if not np.isfinite(result):
        raise QualityError("Money income exceeds numeric range")
    return result


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise QualityError("Money income dates require explicit calendar days")
    try:
        return pd.Timestamp(value)
    except (ValueError, TypeError) as error:
        raise QualityError("Invalid money income calendar day") from error


def money_income_intervals(raw, instrument, *, basis_shares, par_value):
    """Normalize observed LSJZ records using explicitly supplied, proven units.

    The caller must bind the instrument to the source request and prove units.
    This function neither establishes provenance nor creates payment events.
    Income may be positive, zero or negative; seven-day annualized yield is
    deliberately excluded from arithmetic.
    """
    required = {"FSRQ", "DWJZ", "SDATE", "NAVTYPE"}
    if (not isinstance(raw, pd.DataFrame) or raw.empty or raw.columns.has_duplicates
            or not required.issubset(raw)):
        raise QualityError("Missing or ambiguous monetary income source fields")
    if not isinstance(instrument, str) or not re.fullmatch(r"\d{6}\.(SH|SZ)", instrument):
        raise QualityError("Invalid monetary income instrument")
    basis = _number(basis_shares, positive=True)
    par = _number(par_value, positive=True)
    if not basis.is_integer():
        raise QualityError("Income basis must be a whole number of shares")
    rows = []
    for row in raw.to_dict("records"):
        end = _day(row["FSRQ"])
        kind = row["NAVTYPE"]
        start_value = row["SDATE"]
        missing_start = start_value is None or (isinstance(start_value, str) and start_value == "")
        if not missing_start:
            try:
                missing_start = bool(pd.isna(start_value))
            except (TypeError, ValueError):
                raise QualityError("Invalid monetary income interval start")
        if kind == "1":
            if not missing_start:
                raise QualityError("Daily monetary income has an unexpected interval")
            start = end
        elif kind == "0":
            if missing_start:
                raise QualityError("Aggregated monetary income needs an interval start")
            start = _day(start_value)
        else:
            raise QualityError("Unknown monetary income record type")
        if start > end:
            raise QualityError("Reversed monetary income interval")
        income = _number(row["DWJZ"])
        rows.append({"datetime": end, "instrument": instrument,
                     "period_start": start, "period_end": end,
                     "income_per_basis": income, "income_per_share": income / basis,
                     "basis_shares": basis, "par_value": par,
                     "source_record_type": kind})
    frame = pd.DataFrame(rows).set_index(["datetime", "instrument"]).sort_index()
    validate_money_income_intervals(frame)
    frame.attrs = {"source": "eastmoney.f10.lsjz", "payment_events_created": 0,
                   "daily_income_inferred": False, "publication_times_proven": False,
                   "units_and_identity_proven_by_caller": False}
    return frame


def validate_money_income_intervals(frame):
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise QualityError("Empty monetary income input")
    require_panel(frame)
    required = {"period_start", "period_end", "income_per_basis", "income_per_share",
                "basis_shares", "par_value", "source_record_type"}
    if not required.issubset(frame):
        raise QualityError("Incomplete normalized monetary income input")
    for column in ("period_start", "period_end"):
        if (not pd.api.types.is_datetime64_ns_dtype(frame[column].dtype)
                or isinstance(frame[column].dtype, pd.DatetimeTZDtype)):
            raise QualityError("Monetary income interval dates must be timezone-free days")
        if frame[column].isna().any() or not frame[column].eq(frame[column].dt.normalize()).all():
            raise QualityError("Invalid normalized monetary income interval day")
    dates = frame.index.get_level_values("datetime")
    if dates.tz is not None or not dates.equals(pd.DatetimeIndex(frame.period_end)):
        raise QualityError("Monetary income index differs from interval end")
    numeric = ["income_per_basis", "income_per_share", "basis_shares", "par_value"]
    for column in numeric:
        if (not pd.api.types.is_numeric_dtype(frame[column].dtype)
                or pd.api.types.is_bool_dtype(frame[column].dtype)):
            raise QualityError("Monetary income units and amounts must be numeric")
    if not np.isfinite(frame[numeric].to_numpy()).all():
        raise QualityError("Nonfinite normalized monetary income")
    if (frame.basis_shares.le(0).any() or frame.par_value.le(0).any()
            or not frame.basis_shares.eq(np.floor(frame.basis_shares)).all()):
        raise QualityError("Invalid normalized monetary income units")
    if not np.allclose(frame.income_per_share, frame.income_per_basis / frame.basis_shares,
                       atol=0, rtol=1e-12):
        raise QualityError("Monetary income share-unit conversion differs")
    if frame.period_start.gt(frame.period_end).any():
        raise QualityError("Reversed normalized monetary income interval")
    if (not frame.source_record_type.isin(["0", "1"]).all()
            or (frame.source_record_type.eq("1") & frame.period_start.ne(frame.period_end)).any()):
        raise QualityError("Monetary income record type differs from interval")
    for instrument, group in frame.groupby(level="instrument", sort=False):
        if not isinstance(instrument, str) or not re.fullmatch(r"\d{6}\.(SH|SZ)", instrument):
            raise QualityError("Invalid normalized monetary income instrument")
        if group[["basis_shares", "par_value"]].nunique().gt(1).any():
            raise QualityError("Monetary income unit changes need explicit versions")
        previous = None
        for row in group.itertuples():
            if previous is not None and row.period_start <= previous:
                raise QualityError("Overlapping monetary income source intervals")
            previous = row.period_end


def money_income_coverage(frame, start, end):
    """Count calendar-day coverage without splitting aggregate income amounts."""
    validate_money_income_intervals(frame)
    first, last = _day(start), _day(end)
    if first > last:
        raise QualityError("Reversed monetary income coverage window")
    expected = pd.date_range(first, last, freq="D")
    result = []
    for instrument, group in frame.groupby(level="instrument", sort=False):
        covered = pd.DatetimeIndex([])
        for row in group.itertuples():
            covered = covered.union(pd.date_range(row.period_start, row.period_end, freq="D"))
        missing = expected.difference(covered)
        relevant = group[group.period_end.ge(first) & group.period_start.le(last)]
        aggregates = int(relevant.period_start.ne(relevant.period_end).sum())
        result.append({"instrument": instrument, "expected_calendar_days": len(expected),
                       "covered_calendar_days": len(expected.intersection(covered)),
                       "missing_calendar_days": len(missing),
                       "missing_examples": [d.strftime("%Y-%m-%d") for d in missing[:10]],
                       "aggregate_intervals": aggregates,
                       "calendar_coverage_complete": len(missing) == 0,
                       "daily_amounts_complete": len(missing) == 0 and aggregates == 0})
    return result


def require_daily_money_income(frame):
    validate_money_income_intervals(frame)
    if frame.period_start.ne(frame.period_end).any():
        raise QualityError("Aggregate income cannot be inferred as daily income for compounding or rounding")
    return frame
