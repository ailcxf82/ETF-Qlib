from __future__ import annotations

import numpy as np
import pandas as pd

from etf_ml.contracts import DataSpec
from etf_ml.data.source import require_panel
from etf_ml.errors import ConfigurationError, QualityError



TRADING_FLAGS = ("tradable", "buyable", "sellable", "suspended")


def trading_permissions(frame: pd.DataFrame) -> pd.DataFrame:
    """Use only states on this bar; an explicitly unknown state blocks execution."""
    for column in TRADING_FLAGS:
        if column in frame:
            values = frame[column]
            if not (pd.api.types.is_numeric_dtype(values.dtype) or
                    pd.api.types.is_bool_dtype(values.dtype)) or not values.dropna().isin([0, 1]).all():
                raise QualityError("Trading states must be boolean or 0/1: " + column)
    tradable = frame["tradable"].astype("boolean").fillna(False)
    if "quoted" in frame:
        tradable &= frame.quoted.astype("boolean").fillna(False)
    if "volume_shares" in frame:
        tradable &= (frame.volume_shares.gt(0) & np.isfinite(frame.volume_shares)).fillna(False)
    if "suspended" in frame:
        tradable &= ~frame.suspended.astype("boolean").fillna(True)
    result = pd.DataFrame(index=frame.index)
    for column in ("buyable", "sellable"):
        allowed = (frame[column].astype("boolean").fillna(False) if column in frame
                   else pd.Series(True, index=frame.index))
        result[column] = (tradable & allowed).astype(bool)
    return result


def row_trading_permissions(row):
    """Scalar equivalent of trading_permissions for a live execution row."""
    from numbers import Real
    for column in TRADING_FLAGS:
        if column in row:
            value = row[column]
            if pd.notna(value) and (not isinstance(value, (Real, np.bool_)) or value not in (0, 1)):
                raise QualityError("Trading states must be boolean or 0/1: " + column)
    def known_true(column, default):
        value = row.get(column, default)
        return bool(pd.notna(value) and value == 1)
    tradable = known_true("tradable", False) and known_true("quoted", True)
    if "volume_shares" in row:
        volume = row.volume_shares
        tradable = tradable and bool(pd.notna(volume) and np.isfinite(volume) and volume > 0)
    if "suspended" in row:
        value = row.suspended
        tradable = tradable and bool(pd.notna(value) and value == 0)
    return {column: tradable and known_true(column, True) for column in ("buyable", "sellable")}


TRADING_BLOCK_REASONS = {
    0: None, 1: "no_quote", 2: "zero_volume", 3: "unknown_volume",
    4: "suspended", 5: "unknown_suspension_state", 6: "source_not_tradable",
    7: "unknown_tradable_state", 8: "source_buy_blocked", 9: "unknown_buy_state",
    10: "source_sell_blocked", 11: "unknown_sell_state", 12: "invalid_volume",
    13: "limit_up", 14: "limit_down", 15: "limits_unavailable_at_open",
}


def trading_block_codes(frame: pd.DataFrame) -> pd.DataFrame:
    """Retain unknown source states before normalization collapses them to False.

    Numeric codes stay outside model features; text reasons are emitted with
    universe membership. Current missing quotes and suspension take priority.
    """
    permissions = trading_permissions(frame)
    common = pd.Series(0, index=frame.index, dtype="int8")
    conditions = []
    if "quoted" in frame:
        conditions.append((~frame.quoted.astype("boolean").fillna(False), 1))
    if "suspended" in frame:
        conditions.extend([(frame.suspended.eq(1).fillna(False), 4),
                           (frame.suspended.isna(), 5)])
    if "volume_shares" in frame:
        conditions.extend([(frame.volume_shares.isna(), 3),
                           (frame.volume_shares.eq(0).fillna(False), 2),
                           ((frame.volume_shares.lt(0) | ~np.isfinite(frame.volume_shares)).fillna(False), 12)])
    conditions.extend([(frame.tradable.isna(), 7),
                       (~frame.tradable.astype("boolean").fillna(False), 6)])
    for mask, code in conditions:
        common.loc[common.eq(0) & mask] = code
    result = pd.DataFrame(index=frame.index)
    for side, blocked, unknown in (("buy", 8, 9), ("sell", 10, 11)):
        column = side + "able"
        codes = common.copy()
        if column in frame:
            codes.loc[codes.eq(0) & frame[column].isna()] = unknown
            codes.loc[codes.eq(0) & ~frame[column].astype("boolean").fillna(False)] = blocked
        saved_column = side + "_block_code"
        if saved_column in frame:
            saved = frame[saved_column]
            if (not pd.api.types.is_numeric_dtype(saved.dtype) or
                    pd.api.types.is_bool_dtype(saved.dtype) or
                    not saved.isin(TRADING_BLOCK_REASONS).all()):
                raise QualityError("Invalid preserved trading block codes")
            # False/unknown source booleans become False during normalization.
            fallback = codes.isin([6, 8, 10]) & saved.ne(0)
            codes.loc[fallback] = saved.loc[fallback].astype("int8")
        codes.loc[permissions[column]] = 0
        result[saved_column] = codes.astype("int8")
    return result


def trading_reasons(frame: pd.DataFrame) -> pd.DataFrame:
    codes = trading_block_codes(frame)
    return pd.DataFrame({side + "_reason": codes[side + "_block_code"].map(TRADING_BLOCK_REASONS)
                         for side in ("buy", "sell")}, index=frame.index)


def normalize(frame: pd.DataFrame, spec: DataSpec) -> pd.DataFrame:
    require_panel(frame, numeric=True)
    missing = [name for name in ("volume_unit", "amount_multiplier", "price_mode", "change_unit")
               if getattr(spec, name) is None]
    if missing:
        raise ConfigurationError("Unverified data semantics: " + ", ".join(missing))
    required = ["open", "high", "low", "close", "volume", "amount", "factor",
                "change", "reference_close"]
    if not set(required).issubset(frame):
        raise QualityError("Source fields missing")
    result = pd.DataFrame(index=frame.index)
    factor = frame["factor"]
    result["adjustment_factor"] = factor
    for field in ("open", "high", "low", "close"):
        result["raw_" + field] = frame[field] / factor if spec.price_mode == "adjusted" else frame[field]
        result["adj_" + field] = frame[field] if spec.price_mode == "adjusted" else frame[field] * factor
    result["volume_shares"] = frame["volume"] * (spec.lot_size if spec.volume_unit == "lots" else 1)
    result["amount_currency"] = frame["amount"] * spec.amount_multiplier
    result["return_1d"] = frame["change"] / (100 if spec.change_unit == "percent" else 1)
    result["reference_close"] = frame["reference_close"]
    result["quoted"] = result[["raw_open", "raw_close"]].notna().all(axis=1)
    result["tradable"] = result["quoted"] & (result["volume_shares"] > 0)
    for column in TRADING_FLAGS:
        if column in frame:
            result[column] = frame[column]
    permissions = trading_permissions(result)
    block_codes = trading_block_codes(result)
    result["tradable"] = (result["quoted"] & (result["volume_shares"] > 0) &
                          result.tradable.astype("boolean").fillna(False))
    if "suspended" in result:
        result["tradable"] &= ~result.suspended.astype("boolean").fillna(True)
    for column in ("buyable", "sellable"):
        if column in frame:
            result[column] = (result.tradable & permissions[column]).astype(bool)
    result["tradable"] = result.tradable.astype(bool)
    for column in block_codes:
        result[column] = block_codes[column]
    return result


def normalized_issues(frame: pd.DataFrame) -> list[dict]:
    issues = []
    def add(code, mask):
        count = int(mask.sum())
        if count:
            issues.append({"code": code, "rows": count})

    prices = frame[["raw_open", "raw_high", "raw_low", "raw_close"]]
    any_quote = prices.notna().any(axis=1)
    complete = prices.notna().all(axis=1)
    add("partial_ohlc", any_quote & ~complete)
    add("nonpositive_or_nonfinite_price",
        any_quote & (~np.isfinite(prices).all(axis=1) | (prices <= 0).any(axis=1)))
    add("ohlc_range", complete & (
        (frame.raw_high < prices[["raw_open", "raw_close", "raw_low"]].max(axis=1)) |
        (frame.raw_low > prices[["raw_open", "raw_close", "raw_high"]].min(axis=1))))
    add("missing_or_invalid_adjustment", any_quote & (
        ~np.isfinite(frame.adjustment_factor) | (frame.adjustment_factor <= 0)))
    add("negative_or_nonfinite_volume", complete & (
        ~np.isfinite(frame.volume_shares) | (frame.volume_shares < 0)))
    add("negative_or_nonfinite_amount", complete & (
        ~np.isfinite(frame.amount_currency) | (frame.amount_currency < 0)))
    add("zero_volume_nonzero_amount", complete & (frame.volume_shares == 0) &
        (frame.amount_currency != 0))
    quoted_trades = complete & (frame.volume_shares > 0)
    avg_price = frame.amount_currency / frame.volume_shares.replace(0, np.nan)
    add("amount_volume_unit_inconsistent", quoted_trades & (
        (avg_price < frame.raw_low * 0.95) | (avg_price > frame.raw_high * 1.05)))
    reference = frame.reference_close
    expected_return = frame.raw_close / reference - 1
    add("missing_return_reference", complete & (
        ~np.isfinite(reference) | (reference <= 0) | ~np.isfinite(frame.return_1d)))
    add("change_semantics", complete & reference.gt(0) &
        ((frame.return_1d - expected_return).abs() > 5e-4))
    return issues
