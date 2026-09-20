from __future__ import annotations

import numpy as np
import pandas as pd

from etf_ml.contracts import UniversePolicy
from etf_ml.data.calendar import require_calendar
from etf_ml.data.source import require_panel
from etf_ml.data.normalize import trading_permissions, trading_reasons
from etf_ml.errors import QualityError

METADATA_COLUMNS = ["instrument", "valid_from", "valid_to", "available_time",
                    "listing_date", "asset_class", "tracking_group", "operating"]


def validate_metadata(metadata: pd.DataFrame) -> pd.DataFrame:
    if not set(METADATA_COLUMNS).issubset(metadata):
        raise QualityError("Historical metadata needs classification, validity and availability evidence")
    result = metadata.copy()
    for column in ("valid_from", "valid_to", "available_time", "listing_date"):
        result[column] = pd.to_datetime(result[column], errors="raise")
        if result[column].isna().any() or result[column].dt.tz is not None:
            raise QualityError("Metadata times must be complete and timezone-naive")
    if result[["instrument", "asset_class", "tracking_group", "operating"]].isna().any().any():
        raise QualityError("Historical classification incomplete")
    if not pd.api.types.is_bool_dtype(result.operating):
        raise QualityError("Operating state must be boolean")
    if (result.valid_from > result.valid_to).any():
        raise QualityError("Invalid metadata validity interval")
    for _, group in result.sort_values("valid_from").groupby("instrument"):
        if (group.valid_from.iloc[1:].to_numpy() <= group.valid_to.iloc[:-1].to_numpy()).any():
            raise QualityError("Overlapping historical classification intervals")
    return result


def resolve(as_of, frame: pd.DataFrame, metadata: pd.DataFrame,
            calendar: pd.DatetimeIndex, policy: UniversePolicy) -> pd.DataFrame:
    require_panel(frame)
    require_calendar(calendar)
    metadata = validate_metadata(metadata)
    as_of = pd.Timestamp(as_of)
    if as_of not in calendar:
        raise QualityError("Universe date not in trading calendar")
    known = metadata[(metadata.valid_from <= as_of) & (metadata.valid_to >= as_of) &
                     (metadata.available_time <= as_of + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1))]
    previous = frame[frame.index.get_level_values("datetime") <= as_of]
    permissions = trading_permissions(previous)
    trade_reasons = trading_reasons(previous)
    rows = []
    for instrument, history in previous.groupby(level="instrument", sort=True):
        info = known[known.instrument == instrument]
        reason = None
        tracking_group = None
        if len(info) != 1:
            reason = "unknown_historical_metadata"
        else:
            item = info.iloc[0]
            tracking_group = item.tracking_group
            age = int(((calendar >= item.listing_date) & (calendar <= as_of)).sum())
            if not item.operating:
                reason = "not_operating"
            elif item.asset_class != policy.allowed_asset_class:
                reason = "outside_asset_scope"
            elif age < policy.minimum_listing_days:
                reason = "insufficient_listing_history"
        recent = history.amount_currency.tail(policy.liquidity_lookback)
        mean_amount = float(recent.mean()) if len(recent) else 0
        if reason is None and (len(recent) < policy.liquidity_lookback or
                               recent.isna().any() or mean_amount < policy.minimum_average_amount):
            reason = "insufficient_liquidity"
        today = history[history.index.get_level_values("datetime") == as_of]
        state = permissions.loc[today.index]
        buyable = bool(len(state) and state.iloc[-1].buyable)
        sellable = bool(len(state) and state.iloc[-1].sellable)
        rows.append({"datetime": as_of, "instrument": instrument, "eligible": reason is None,
                     "buyable": reason is None and buyable, "sellable": sellable,
                     "tracking_group": tracking_group, "average_amount": mean_amount,
                     "reason": reason,
                     "buy_reason": (reason if reason is not None else
                                    trade_reasons.loc[today.index].iloc[-1].buy_reason if len(today) else "no_quote"),
                     "sell_reason": trade_reasons.loc[today.index].iloc[-1].sell_reason if len(today) else "no_quote"})
    if not rows:
        raise QualityError("Universe has no historical rows")
    return pd.DataFrame(rows).set_index(["datetime", "instrument"]).sort_index()


def build_history(frame, metadata, calendar, policy, *, diagnostic=False):
    """Materialize PIT membership without rescanning all prior rows per day.

    Keep rows after an instrument's first observation, including gaps and
    retirement periods. Those rows are needed to account for existing holdings.
    Liquidity windows count historical observations, matching resolve().
    """
    require_panel(frame)
    require_calendar(calendar)
    metadata = validate_metadata(metadata)
    if frame.empty or frame.index.get_level_values("datetime").min() > calendar[0]:
        raise QualityError("Universe has no historical rows")
    by_instrument = {i: g.sort_values("valid_from").reset_index(drop=True)
                     for i, g in metadata.groupby("instrument", sort=False)}
    pieces = []
    permissions = trading_permissions(frame)
    trade_reasons = trading_reasons(frame)
    window = policy.liquidity_lookback
    for instrument, history in frame.groupby(level="instrument", sort=True):
        history = history.droplevel("instrument").sort_index()
        dates = calendar[calendar >= history.index[0]]
        if not len(dates):
            continue
        amounts = history.amount_currency
        mean = amounts.rolling(window, min_periods=1).mean().reindex(dates, method="ffill")
        valid_count = amounts.rolling(window, min_periods=1).count().reindex(dates, method="ffill")
        observations = pd.Series(np.minimum(np.arange(1, len(history) + 1), window),
                                 index=history.index).reindex(dates, method="ffill")
        reason = np.full(len(dates), "unknown_historical_metadata", dtype=object)
        groups = np.full(len(dates), None, dtype=object)
        info = by_instrument.get(instrument)
        if info is not None and len(info):
            locations = np.searchsorted(info.valid_from.to_numpy(), dates.to_numpy(),
                                        side="right") - 1
            selected = info.iloc[np.maximum(locations, 0)].reset_index(drop=True)
            known = ((locations >= 0) &
                     (selected.valid_to.to_numpy() >= dates.to_numpy()) &
                     (np.ones(len(dates), dtype=bool) if diagnostic else
                      selected.available_time.to_numpy() <=
                      (dates + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)).to_numpy()))
            groups[known] = selected.tracking_group.to_numpy()[known]
            reason[known] = None
            age = np.maximum(0, np.searchsorted(calendar, dates, side="right") -
                             np.searchsorted(calendar, selected.listing_date, side="left"))
            for failed, label in (
                (~selected.operating.to_numpy(), "not_operating"),
                (selected.asset_class.to_numpy() != policy.allowed_asset_class, "outside_asset_scope"),
                (age < policy.minimum_listing_days, "insufficient_listing_history"),
            ):
                reason[known & pd.isna(reason) & failed] = label
        liquid = ((observations.to_numpy() >= window) &
                  (valid_count.to_numpy() >= window) &
                  (mean.to_numpy() >= policy.minimum_average_amount))
        reason[pd.isna(reason) & ~liquid] = "insufficient_liquidity"
        eligible = pd.isna(reason)
        state = permissions.xs(instrument, level="instrument").reindex(dates).fillna(False)
        reasons = trade_reasons.xs(instrument, level="instrument").reindex(dates)
        missing_bar = ~dates.isin(history.index)
        reasons.loc[missing_bar, ["buy_reason", "sell_reason"]] = "no_quote"
        pieces.append(pd.DataFrame({
            "datetime": dates, "instrument": instrument, "eligible": eligible,
            "buyable": eligible & state.buyable.to_numpy(dtype=bool),
            "sellable": state.sellable.to_numpy(dtype=bool),
            "tracking_group": groups, "average_amount": mean.to_numpy(),
            "reason": reason,
            "buy_reason": np.where(eligible, reasons.buy_reason.to_numpy(), reason),
            "sell_reason": reasons.sell_reason.to_numpy(),
        }).set_index(["datetime", "instrument"]))
    if not pieces:
        raise QualityError("Universe has no historical rows")
    return pd.concat(pieces).sort_index()


def metadata_quote_coverage(frame, history):
    """Report missing PIT classification across the entire quoted source scope.

    Unknown membership remains a diagnostic exclusion in resolve/build_history;
    it cannot silently narrow a formally admitted snapshot's quoted universe.
    Unquoted gaps do not assert that an ETF was operating or listed.
    """
    require_panel(frame)
    require_panel(history)
    if "quoted" not in frame or not {"reason", "eligible"}.issubset(history):
        raise QualityError("Metadata coverage requires normalized quote and membership states")
    quoted = frame.index[frame.quoted]
    state = history.reindex(quoted)
    unknown = state.eligible.isna() | state.reason.eq("unknown_historical_metadata")
    missing = quoted[unknown.to_numpy()]
    return {"status": "failed" if len(missing) else "passed", "quoted_rows": len(quoted),
        "unknown_quoted_rows": len(missing),
        "unknown_quoted_instruments": len(set(missing.get_level_values("instrument"))),
        "examples": [{"datetime": str(t.date()), "instrument": i} for t, i in missing[:10]],
        "unquoted_unknown_rows": int((history.reason.eq("unknown_historical_metadata") & ~history.index.isin(quoted)).sum()),
        "unquoted_state_inferred": False}
