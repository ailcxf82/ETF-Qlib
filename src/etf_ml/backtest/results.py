from __future__ import annotations

import math
from pathlib import Path
import pandas as pd

from etf_ml.backtest.qlib_runner import evaluate
from etf_ml.errors import ConfigurationError
from etf_ml.utils import atomic_json


def persist_result(result, output: Path):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    result.report.to_parquet(output / "report.parquet")
    result.trades.to_parquet(output / "trades.parquet")
    result.daily_returns.to_frame().to_parquet(output / "daily_returns.parquet")
    positions = []
    for date, position in result.positions.items():
        for instrument in position.get_stock_list():
            positions.append({"datetime": date, "instrument": instrument,
                              "shares": position.get_stock_amount(instrument),
                              "raw_price": position.get_stock_price(instrument)})
        positions.append({"datetime": date, "instrument": "CASH",
                          "shares": position.get_cash(), "raw_price": 1.})
    pd.DataFrame(positions, columns=["datetime", "instrument", "shares", "raw_price"]).to_parquet(
        output / "positions.parquet", index=False)
    result.entitlements.reindex(columns=['event_id', 'instrument', 'ex_date', 'record_date',
        'entitled_shares', 'ex_date_shares', 'cash_per_record_share', 'cash_per_ex_share',
        'distribution_amount', 'basis']).to_parquet(output / 'entitlements.parquet', index=False)
    receivables = []
    for date, position in result.positions.items():
        for event_id, claim in getattr(position, 'dividend_receivables', {}).items():
            receivables.append({'datetime': date, 'event_id': event_id, **claim})
    pd.DataFrame(receivables, columns=['datetime', 'event_id', 'instrument', 'amount', 'ex_date', 'pay_date']).to_parquet(
        output / 'receivables.parquet', index=False)
    income_balances = []
    for date, position in result.positions.items():
        for instrument, amount in getattr(getattr(position, "income_book", None), "balances", {}).items():
            income_balances.append({"datetime": date, "instrument": instrument, "unpaid_income": float(amount)})
    pd.DataFrame(income_balances, columns=["datetime", "instrument", "unpaid_income"]).to_parquet(
        output / "income_balances.parquet", index=False)
    result.income_postings.reindex(columns=["datetime", "instrument", "kind", "rule_id", "held_shares",
        "gross_income", "booked_income", "unallocated_rounding_residual", "converted_shares", "conversion_cash",
        "unpaid_before", "unpaid_after", "cash_settled"]).to_parquet(output / "income_postings.parquet", index=False)
    atomic_json(output / "decisions.json", result.decisions)
    result.exposures.to_parquet(output / "exposures.parquet")
    result.execution.to_parquet(output / "execution.parquet")
    result.ledger.to_parquet(output / 'ledger.parquet')


def evaluate_with_stress(scores, policy, panel, *, output, cost_multipliers=(2.0,), **kwargs):
    if (len(set(cost_multipliers)) != len(cost_multipliers) or
            any(isinstance(m, bool) or not isinstance(m, (int, float)) or
                not math.isfinite(m) or m <= 1 for m in cost_multipliers)):
        raise ConfigurationError("Cost pressure multipliers must be distinct finite values above one")
    if any(m * max(policy.commission_rate, policy.slippage_rate) >= 1 for m in cost_multipliers):
        raise ConfigurationError("Cost pressure produces an invalid rate")
    base = evaluate(scores, policy, panel, **kwargs)
    persist_result(base, output)
    stress_metrics = {}
    for multiplier in cost_multipliers:
        stressed = policy.model_copy(deep=True)
        stressed.commission_rate *= multiplier
        stressed.minimum_commission *= multiplier
        stressed.slippage_rate *= multiplier
        result = evaluate(scores, stressed, panel, **kwargs)
        path = Path(output) / ("cost-" + str(multiplier))
        persist_result(result, path)
        atomic_json(path / "metrics.json", result.metrics)
        stress_metrics[str(multiplier)] = result.metrics
    return base, stress_metrics
