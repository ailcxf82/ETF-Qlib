from __future__ import annotations

import numpy as np
import pandas as pd

from etf_ml.contracts import LabelSpec
from etf_ml.data.calendar import require_calendar
from etf_ml.data.source import require_panel
from etf_ml.errors import QualityError


def generate_labels(panel: pd.DataFrame, calendar: pd.DatetimeIndex,
                    spec: LabelSpec) -> tuple[pd.Series, pd.DataFrame]:
    require_panel(panel)
    require_calendar(calendar)
    dates = panel.index.get_level_values("datetime")
    positions = calendar.get_indexer(dates)
    if (positions < 0).any():
        raise QualityError("Label dates outside calendar")
    prices = panel[spec.price_column].unstack("instrument").reindex(calendar)
    future = prices.shift(-spec.execution_lag - spec.horizon)
    entry = prices.shift(-spec.execution_lag)
    wide_label = (future / entry - 1).replace([np.inf, -np.inf], np.nan)
    label = wide_label.stack(future_stack=True).reindex(panel.index).rename("LABEL0")
    def time_at(offset):
        values = pd.Series(pd.NaT, index=panel.index, dtype="datetime64[ns]")
        valid = positions + offset < len(calendar)
        values.loc[valid] = calendar[positions[valid] + offset].to_numpy()
        return values
    events = pd.DataFrame({"signal_time": dates,
                           "entry_time": time_at(spec.execution_lag),
                           "end_time": time_at(spec.execution_lag + spec.horizon),
                           "available_time": time_at(spec.execution_lag + spec.horizon +
                                                     spec.availability_delay_days)})
    # Open prices are conservatively treated as ready after that day's ingestion.
    events["available_time"] = events["available_time"] + pd.Timedelta(hours=16)
    return label, events
