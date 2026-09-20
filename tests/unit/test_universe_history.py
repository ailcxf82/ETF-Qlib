import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from etf_ml.contracts import UniversePolicy
from etf_ml.data.universe import build_history, resolve, validate_metadata
from etf_ml.errors import QualityError


def test_history_matches_daily_resolution_with_gaps_and_state_changes(panel, metadata, calendar):
    frame = panel.copy()
    dates = frame.index.get_level_values("datetime")
    instruments = frame.index.get_level_values("instrument")
    frame = frame[~((instruments == "510300.SH") & (dates < calendar[30]))]
    frame = frame[~((frame.index.get_level_values("instrument") == "159915.SZ") &
                    frame.index.get_level_values("datetime").isin(calendar[70:90]))]
    frame.loc[(slice(calendar[45], calendar[51]), "510500.SH"), "amount_currency"] = np.nan
    frame.loc[(slice(calendar[120], None), "159915.SZ"), "tradable"] = False
    original = metadata[metadata.instrument == "510500.SH"].iloc[0].copy()
    metadata.loc[metadata.instrument == "510500.SH", "valid_to"] = calendar[99]
    changed = original.copy()
    changed["valid_from"] = calendar[100]
    changed["available_time"] = calendar[110]
    changed["tracking_group"] = "NEW_GROUP"
    changed["operating"] = False
    metadata = pd.concat([metadata, changed.to_frame().T], ignore_index=True)
    metadata["operating"] = metadata.operating.astype(bool)
    policy = UniversePolicy(minimum_listing_days=10, liquidity_lookback=5,
                            minimum_average_amount=1000)
    expected = pd.concat([resolve(day, frame, metadata, calendar, policy)
                          for day in calendar]).sort_index()
    actual = build_history(frame, metadata, calendar, policy)
    assert_frame_equal(actual, expected, atol=1e-8, rtol=1e-12)
    # A missing quote preserves membership but blocks both sides of execution.
    gap = actual.loc[(calendar[80], "159915.SZ")]
    assert gap.eligible and not gap.buyable and not gap.sellable
    late = actual.loc[(calendar[105], "510500.SH")]
    assert late.reason == "unknown_historical_metadata" and late.tracking_group is None
    retired = actual.loc[(calendar[120], "510500.SH")]
    assert retired.reason == "not_operating"


def test_history_past_membership_is_unchanged_by_future_amounts(panel, metadata, calendar):
    policy = UniversePolicy(minimum_listing_days=0, liquidity_lookback=3)
    first = build_history(panel, metadata, calendar, policy)
    changed = panel.copy()
    changed.loc[changed.index.get_level_values("datetime") > calendar[80], "amount_currency"] = 1e12
    second = build_history(changed, metadata, calendar, policy)
    assert_frame_equal(first.loc[:calendar[80]], second.loc[:calendar[80]])


@pytest.mark.parametrize("column", ["valid_from", "valid_to", "available_time", "listing_date"])
def test_incomplete_metadata_time_is_rejected(metadata, column):
    metadata.loc[0, column] = pd.NaT
    with pytest.raises(QualityError, match="complete"):
        validate_metadata(metadata)
