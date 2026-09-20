from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
import numpy as np
import pandas as pd

from etf_ml.contracts import PortfolioPolicy
from etf_ml.errors import ConfigurationError, QualityError


@dataclass(frozen=True)
class Allocation:
    weights: dict[str, float]
    cash_weight: float
    reasons: dict[str, str]
    receivable_weight: float = 0.
    income_weight: float = 0.


def construct(scores: pd.Series, positions: dict[str, float], constraints: dict,
              policy: PortfolioPolicy) -> Allocation:
    policy.require_resolved()
    if scores.index.has_duplicates or not np.isfinite(scores).all():
        raise QualityError("Allocation requires unique finite scores")
    if any(not np.isfinite(w) or w < 0 for w in positions.values()):
        raise QualityError("Invalid current position weights")
    receivable = constraints.get('receivable_weight', 0.)
    income = constraints.get('income_weight', 0.)
    if (not isinstance(income, (int, float, np.floating)) or isinstance(income, bool) or not np.isfinite(income)):
        raise QualityError('Invalid monetary income weight')
    if (not isinstance(receivable, (int, float, np.floating)) or isinstance(receivable, bool) or
            not np.isfinite(receivable) or not 0 <= receivable <= 1 or
            sum(positions.values()) + receivable + income > 1 + 1e-8 or receivable + income > 1):
        raise QualityError('Invalid or inconsistent dividend receivable weight')
    buyable = constraints.get("buyable", {})
    sellable = constraints.get("sellable", {})
    groups = constraints.get("tracking_group", {})
    weights, reasons = {}, {}
    group_weights = defaultdict(float)
    for instrument, weight in positions.items():
        if not sellable.get(instrument, False):
            weights[instrument] = weight
            group_weights[groups.get(instrument, instrument)] += weight
            reasons[instrument] = "cannot_sell"
    if receivable:
        reasons['_receivables'] = 'dividend_receivable_not_spendable'
    if income:
        reasons["_income"] = "monetary_income_not_spendable" if income > 0 else "unpaid_monetary_loss"
    remaining = max(0, 1 - receivable - income - sum(weights.values()))
    if constraints.get("risk_triggered", False):
        return Allocation(weights, remaining, {**reasons, "_risk": "risk_limit_triggered"}, receivable, income)
    candidates = sorted((i for i in scores.index if buyable.get(i, False) and i not in weights),
                        key=lambda i: (-float(scores.loc[i]), str(i)))
    if policy.k_mode == "fraction":
        number = max(1, math.ceil(len(candidates) * policy.k)) if candidates else 0
        selected = candidates[:number]
        cap = policy.max_weight
    elif policy.k_mode == "count":
        selected = candidates[:int(policy.k)]
        cap = policy.max_weight
    elif policy.k_mode == "weight_cap":
        selected = candidates
        cap = min(policy.max_weight, policy.k)
    else:
        raise ConfigurationError("K semantics unresolved")
    if selected:
        # Equal weights with caps; blocked capacity remains cash.
        ideal = remaining / len(selected) if policy.k_mode != "weight_cap" else cap
        for instrument in selected:
            group = groups.get(instrument)
            if group is None:
                raise QualityError("Tracking group missing for selected ETF")
            available_group = max(0, policy.max_group_weight - group_weights[group])
            weight = min(ideal, cap, available_group, remaining)
            if weight > 0:
                weights[instrument] = weight
                group_weights[group] += weight
                remaining -= weight
            if weight < ideal:
                reasons[instrument] = "weight_or_group_cap"
    # max_turnover is half the L1 change in asset and cash weights.
    instruments = set(weights) | set(positions)
    old_cash = 1 - receivable - income - sum(positions.values())
    cash = 1 - receivable - income - sum(weights.values())
    turnover = (sum(abs(weights.get(i, 0) - positions.get(i, 0)) for i in instruments) +
                abs(cash - old_cash)) / 2
    if turnover > policy.max_turnover:
        ratio = policy.max_turnover / turnover
        weights = {i: positions.get(i, 0) + ratio * (weights.get(i, 0) - positions.get(i, 0))
                   for i in instruments}
        weights = {i: w for i, w in weights.items() if w > 1e-14}
        reasons["_turnover"] = "turnover_budget"
    return Allocation(weights, max(0, 1 - receivable - income - sum(weights.values())), reasons, receivable, income)
