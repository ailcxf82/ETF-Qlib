from __future__ import annotations

import pandas as pd

from etf_ml.contracts import FoldSpec, SegmentSpec
from etf_ml.data.source import require_panel
from etf_ml.errors import ConfigurationError


def validate_fold(fold: FoldSpec, holdout_start) -> None:
    segments = (fold.train, fold.early_stop, fold.selection)
    for segment in segments:
        if pd.Timestamp(segment.start) > pd.Timestamp(segment.end):
            raise ConfigurationError("Segment start follows end")
    if not (pd.Timestamp(fold.train.end) < pd.Timestamp(fold.early_stop.start) and
            pd.Timestamp(fold.early_stop.end) < pd.Timestamp(fold.selection.start) and
            pd.Timestamp(fold.selection.end) < pd.Timestamp(holdout_start)):
        raise ConfigurationError("Development roles overlap or expose holdout")


def learning_mask(events: pd.DataFrame, labels: pd.Series, segment: SegmentSpec,
                  *, next_start=None, as_of=None) -> pd.Series:
    require_panel(events)
    if not labels.index.equals(events.index):
        raise ConfigurationError("Label/event indices differ")
    dates = events.index.get_level_values("datetime")
    mask = pd.Series((dates >= pd.Timestamp(segment.start)) &
                     (dates <= pd.Timestamp(segment.end)), index=events.index)
    mask &= labels.notna() & events.available_time.notna()
    if next_start is not None:
        # Purge actual events, including any source publication delay.
        mask &= events.available_time < pd.Timestamp(next_start)
    if as_of is not None:
        cutoff = pd.Timestamp(as_of)
        if cutoff == cutoff.normalize():
            cutoff += pd.Timedelta(hours=23, minutes=59, seconds=59)
        mask &= events.available_time <= cutoff
    return mask
