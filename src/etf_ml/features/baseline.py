from __future__ import annotations

import inspect
import numpy as np
import pandas as pd

from etf_ml.contracts import FeatureArtifact
from etf_ml.data.source import require_panel
from etf_ml.errors import QualityError
from etf_ml.utils import content_hash


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
