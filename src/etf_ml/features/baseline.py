from __future__ import annotations

import inspect
import numpy as np
import pandas as pd

from etf_ml.contracts import FeatureArtifact
from etf_ml.data.source import require_panel
from etf_ml.errors import QualityError
from etf_ml.utils import content_hash


def feature_descriptors(columns: list[str], *, composition: dict | None = None) -> list[dict]:
    """Audited compact baseline catalogue used in v2 proposal prompts."""
    descriptors = []
    accumulated = {str(entry.get("column")): entry for entry in (composition or {}).get("factors", [])
                   if isinstance(entry, dict)}
    for name in sorted(columns):
        # Only exact built-in IDs are parsed.  An admitted factor commonly has
        # a `<factor_id>_vN` column and must be described from its verified
        # composition spec rather than guessed from a name prefix.
        if name in accumulated:
            spec = accumulated[name].get("spec", {})
            formula, group = spec.get("formula"), spec.get("research_group")
            if not isinstance(formula, str) or not formula or not isinstance(group, str) or not group:
                raise QualityError("Accumulated feature descriptor lacks verified specification")
            descriptors.append({"feature_id": name, "formula": formula, "group": group,
                                "definition_hash": content_hash({"column": name, "spec": spec})})
            continue
        if name in {"momentum_1", "momentum_5", "momentum_10", "momentum_20", "momentum_60", "momentum_120"}:
            window = int(name.rsplit("_", 1)[1])
            formula, group = f"adj_close / adj_close.shift({window}) - 1", "trend"
        elif name in {"reversal_5", "reversal_20"}:
            window = int(name.rsplit("_", 1)[1])
            formula, group = f"-(adj_close / adj_close.shift({window}) - 1)", "reversal"
        elif name in {"volatility_10", "volatility_20", "volatility_60"}:
            window = int(name.rsplit("_", 1)[1])
            formula, group = f"rolling_std(adj_close.pct_change(), {window})", "volatility"
        elif name in {"intraday_return", "daily_range", "average_range_20"}:
            formula, group = name, "range"
        elif name in {"price_location_20", "price_location_60"}:
            window = int(name.rsplit("_", 1)[1])
            formula, group = f"(adj_close - rolling_min(adj_low, {window})) / range({window})", "trend"
        elif name in {"volume_ratio_5_20", "amount_ratio_5_20", "log_average_amount_20", "illiquidity_20"}:
            formula, group = name, "liquidity"
        else:
            # Preserve every column in the catalogue without inventing its
            # formula.  Unknown columns are visibly unavailable to proposal
            # generation rather than silently disappearing.
            descriptors.append({"feature_id": name, "formula": None, "group": "unverified",
                                "definition_hash": content_hash({"name": name, "state": "unverified"})})
            continue
        descriptors.append({"feature_id": name, "formula": formula, "group": group,
                            "definition_hash": content_hash({"name": name, "formula": formula})})
    return descriptors


def baseline_features(panel: pd.DataFrame) -> pd.DataFrame:
    require_panel(panel)
    required = {"adj_open", "adj_high", "adj_low", "adj_close",
                "volume_shares", "amount_currency"}
    if not required.issubset(panel):
        raise QualityError("Baseline feature fields missing")
    pieces = []
    for instrument, part in panel.groupby(level="instrument", sort=True):
        price = part.adj_close
        returns = price.pct_change(fill_method=None)
        columns = {}
        for window in (1, 5, 10, 20, 60, 120):
            columns[f"momentum_{window}"] = price / price.shift(window) - 1
        for window in (5, 20):
            columns[f"reversal_{window}"] = -(price / price.shift(window) - 1)
        for window in (10, 20, 60):
            columns[f"volatility_{window}"] = returns.rolling(window, min_periods=window).std()
        columns["intraday_return"] = part.adj_close / part.adj_open - 1
        columns["daily_range"] = part.adj_high / part.adj_low - 1
        columns["average_range_20"] = columns["daily_range"].rolling(20, min_periods=20).mean()
        for window in (20, 60):
            low = part.adj_low.rolling(window, min_periods=window).min()
            high = part.adj_high.rolling(window, min_periods=window).max()
            columns[f"price_location_{window}"] = (price - low) / (high - low).replace(0, np.nan)
        columns["volume_ratio_5_20"] = (
            part.volume_shares.rolling(5, min_periods=5).mean() /
            part.volume_shares.rolling(20, min_periods=20).mean().replace(0, np.nan))
        columns["amount_ratio_5_20"] = (
            part.amount_currency.rolling(5, min_periods=5).mean() /
            part.amount_currency.rolling(20, min_periods=20).mean().replace(0, np.nan))
        columns["log_average_amount_20"] = np.log1p(
            part.amount_currency.rolling(20, min_periods=20).mean())
        columns["illiquidity_20"] = (
            returns.abs() / part.amount_currency.replace(0, np.nan)).rolling(20, min_periods=20).mean()
        pieces.append(pd.DataFrame(columns, index=part.index))
    result = pd.concat(pieces).sort_index().replace([np.inf, -np.inf], np.nan)
    require_panel(result, numeric=True)
    return result


def materialize(feature_set: dict, panel: pd.DataFrame, cutoff=None) -> FeatureArtifact:
    if cutoff is not None:
        panel = panel[panel.index.get_level_values("datetime") <= pd.Timestamp(cutoff)]
    result = baseline_features(panel)
    selected = feature_set.get("columns", list(result.columns))
    if not set(selected).issubset(result):
        raise QualityError("Unknown baseline feature")
    result = result.loc[:, selected]
    manifest = {"columns": selected, "snapshot_id": feature_set.get("snapshot_id"),
                "formula_hash": content_hash(inspect.getsource(baseline_features)),
                "cutoff": str(panel.index.get_level_values("datetime").max()),
                "spec": feature_set, "maximum_lookback": 120}
    return FeatureArtifact(content_hash(manifest), result, manifest)
