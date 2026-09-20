from __future__ import annotations

from pathlib import Path
import pandas as pd

from etf_ml.errors import DataNotReady, QualityError


def read_calendar(path: Path) -> pd.DatetimeIndex:
    if not Path(path).is_file():
        raise DataNotReady("Trading calendar is missing")
    try:
        return pd.DatetimeIndex(pd.to_datetime(Path(path).read_text(
            encoding="utf-8").splitlines(), errors="raise"), name="datetime")
    except (ValueError, TypeError) as exc:
        raise QualityError("Malformed trading calendar") from exc


def calendar_issues(calendar: pd.DatetimeIndex,
                    trusted: pd.DatetimeIndex | None = None) -> list[str]:
    issues = []
    if calendar.empty:
        issues.append("empty_calendar")
    if calendar.has_duplicates:
        issues.append("duplicate_calendar")
    if not calendar.is_monotonic_increasing:
        issues.append("unordered_calendar")
    if calendar.isna().any() or not calendar.equals(calendar.normalize()):
        issues.append("invalid_calendar_timestamp")
    if (calendar.dayofweek >= 5).any():
        issues.append("weekend_calendar")
    if trusted is not None and not calendar.isin(trusted).all():
        issues.append("calendar_not_in_authoritative_source")
    return issues


def require_calendar(calendar: pd.DatetimeIndex,
                     trusted: pd.DatetimeIndex | None = None) -> None:
    issues = calendar_issues(calendar, trusted)
    if issues:
        raise QualityError("Calendar validation failed: " + ", ".join(issues))


def rebalance_dates(calendar: pd.DatetimeIndex, mid_month_day: int = 15) -> pd.DatetimeIndex:
    require_calendar(calendar)
    dates = []
    for _, month in pd.Series(calendar, index=calendar).groupby(calendar.to_period("M")):
        before_mid = month[month.dt.day <= mid_month_day]
        if len(before_mid):
            dates.append(before_mid.iloc[-1])
        dates.append(month.iloc[-1])
    return pd.DatetimeIndex(sorted(set(dates)), name="datetime")
