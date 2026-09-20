from __future__ import annotations

import numpy as np
import pandas as pd

from etf_ml.data.calendar import require_calendar
from etf_ml.errors import QualityError

TRADE_COLUMNS = [
    "datetime", "instrument", "direction", "requested_shares", "filled_shares",
    "amount", "commission", "price", "reference_price", "slippage_cost",
    "status", "reason", "historical_average_amount",
]
DAILY_COLUMNS = [
    "order_count", "filled_order_count", "partial_order_count", "unfilled_order_count",
    "unpriced_order_count", "executed_notional", "buy_notional", "sell_notional",
    "commission", "slippage_cost", "total_execution_cost",
    "requested_reference_notional", "filled_reference_notional",
]


def execution_metrics(trades: pd.DataFrame, calendar: pd.DatetimeIndex, initial_cash: float):
    """Currency costs and order outcomes; fills valued at the raw reference price.

    Unpriced requests are counted explicitly and excluded from the notional fill
    ratio. Slippage is already in execution prices and must not be debited again.
    """
    require_calendar(calendar)
    if not np.isfinite(initial_cash) or initial_cash <= 0:
        raise QualityError("Execution accounting needs positive initial cash")
    missing = set(TRADE_COLUMNS) - set(trades.columns)
    if missing:
        raise QualityError("Incomplete execution ledger: " + ", ".join(sorted(missing)))
    daily = pd.DataFrame(0., index=calendar, columns=DAILY_COLUMNS)
    daily.index.name = "datetime"
    if len(trades):
        work = trades.copy()
        try:
            work["datetime"] = pd.to_datetime(work.datetime).dt.normalize()
        except (ValueError, TypeError) as exc:
            raise QualityError("Invalid execution dates") from exc
        if work.datetime.isna().any() or not work.datetime.isin(calendar).all():
            raise QualityError("Execution ledger contains dates outside the evaluation calendar")
        numeric = ["requested_shares", "filled_shares", "amount", "commission", "slippage_cost"]
        try:
            work[numeric] = work[numeric].astype(float)
            reference = pd.to_numeric(work.reference_price, errors="raise")
            price = pd.to_numeric(work.price, errors="raise")
        except (ValueError, TypeError) as exc:
            raise QualityError("Non-numeric execution ledger") from exc
        if not np.isfinite(work[numeric]).all().all():
            raise QualityError("Execution quantities and costs must be finite")
        if ((work.requested_shares <= 0).any() or (work[numeric[1:]] < 0).any().any() or
                (work.filled_shares > work.requested_shares + 1e-8).any()):
            raise QualityError("Invalid execution quantity or cost")
        if not work.direction.isin([0, 1]).all():
            raise QualityError("Invalid execution direction")
        has_fill = work.filled_shares > 0
        known_reference = reference.notna() & np.isfinite(reference) & (reference > 0)
        if (reference.notna() & ~known_reference).any():
            raise QualityError("Invalid raw reference price")
        if (has_fill & (~known_reference | price.isna() | ~np.isfinite(price) | (price <= 0))).any():
            raise QualityError("Filled order needs finite positive reference and execution prices")
        if not np.allclose(work.loc[has_fill, "amount"],
                           work.loc[has_fill, "filled_shares"] * price[has_fill],
                           atol=1e-6, rtol=1e-9):
            raise QualityError("Executed amount does not reconcile to shares and price")
        favorable = ((work.direction == 1) & (price < reference - 1e-10)) | (
            (work.direction == 0) & (price > reference + 1e-10))
        if (has_fill & favorable).any():
            raise QualityError("Slippage must be adverse to the raw reference price")
        expected_slippage = work.loc[has_fill, "filled_shares"] * (
            price[has_fill] - reference[has_fill]).abs()
        if not np.allclose(work.loc[has_fill, "slippage_cost"], expected_slippage,
                           atol=1e-6, rtol=1e-9):
            raise QualityError("Slippage cost does not reconcile to execution prices")
        if (work.loc[~has_fill, ["amount", "commission", "slippage_cost"]] != 0).any().any():
            raise QualityError("Unfilled orders cannot incur execution costs")
        full = np.isclose(work.filled_shares, work.requested_shares, atol=1e-8, rtol=0)
        status = np.where(~has_fill, "unfilled", np.where(full, "filled", "partial"))
        if not (work.status.to_numpy() == status).all():
            raise QualityError("Execution status disagrees with filled quantity")
        work["order_count"] = 1
        work["filled_order_count"] = status == "filled"
        work["partial_order_count"] = status == "partial"
        work["unfilled_order_count"] = status == "unfilled"
        work["unpriced_order_count"] = ~known_reference
        work["executed_notional"] = work.amount
        work["buy_notional"] = work.amount.where(work.direction == 1, 0.)
        work["sell_notional"] = work.amount.where(work.direction == 0, 0.)
        work["total_execution_cost"] = work.commission + work.slippage_cost
        work["requested_reference_notional"] = (
            work.requested_shares * reference).where(known_reference, 0.)
        work["filled_reference_notional"] = (
            work.filled_shares * reference).where(known_reference, 0.)
        aggregated = work.groupby("datetime")[DAILY_COLUMNS].sum()
        daily.loc[aggregated.index, DAILY_COLUMNS] = aggregated
    totals = daily.sum()
    order_count = int(totals.order_count)
    requested = float(totals.requested_reference_notional)
    notional = float(totals.executed_notional)
    cost = float(totals.total_execution_cost)
    summary = {
        "trade_count": order_count,
        "filled_order_count": int(totals.filled_order_count),
        "partial_order_count": int(totals.partial_order_count),
        "unfilled_order_count": int(totals.unfilled_order_count),
        "unpriced_order_count": int(totals.unpriced_order_count),
        "unfilled_rate": float(totals.unfilled_order_count / order_count) if order_count else 0.,
        "incomplete_order_rate": float(
            (totals.partial_order_count + totals.unfilled_order_count) / order_count) if order_count else 0.,
        "reference_notional_fill_rate": float(
            totals.filled_reference_notional / requested) if requested else None,
        "reference_notional_fill_definition": "filled_over_requested_raw_notional_for_priced_orders_only",
        "executed_notional": notional,
        "buy_notional": float(totals.buy_notional),
        "sell_notional": float(totals.sell_notional),
        "commission": float(totals.commission),
        "slippage_cost": float(totals.slippage_cost),
        "total_execution_cost": cost,
        "execution_cost_over_initial_equity": cost / initial_cash,
        "execution_cost_over_traded_notional": cost / notional if notional else None,
        "slippage_accounting": "embedded_in_execution_price_no_additional_cash_debit",
    }
    for column in [name for name in DAILY_COLUMNS if name.endswith("count")]:
        daily[column] = daily[column].astype("int64")
    return summary, daily
