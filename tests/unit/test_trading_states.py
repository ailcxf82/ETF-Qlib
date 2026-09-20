import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from etf_ml.contracts import UniversePolicy
from etf_ml.data.normalize import normalize
from etf_ml.data.source import QlibBinSource, encode_provider
from etf_ml.data.universe import build_history, resolve
from etf_ml.errors import QualityError


def test_provider_directional_states_survive_normalization_without_writes(source_spec, calendar, tmp_path):
    raw = QlibBinSource(source_spec.source).read(source_spec.fields)
    raw["buyable"] = 1.
    raw["sellable"] = 1.
    raw["suspended"] = 0.
    key = (calendar[10], "510300.SH")
    raw.loc[key, "buyable"] = 0.
    raw.loc[(calendar[11], key[1]), "sellable"] = np.nan
    raw.loc[(calendar[12], key[1]), "suspended"] = 1.
    raw.loc[(calendar[13], key[1]), "suspended"] = np.nan
    provider = tmp_path / "directional_provider"
    encode_provider(raw, calendar, provider, {c: c for c in raw})
    decoded = QlibBinSource(provider).read({c: c for c in raw})
    before = decoded.copy(deep=True)
    actual = normalize(decoded, source_spec)
    assert_frame_equal(decoded, before)
    assert actual.loc[key, "tradable"] and not actual.loc[key, "buyable"]
    assert actual.loc[key, "sellable"]
    assert not actual.loc[(calendar[11], key[1]), "sellable"]
    assert actual.loc[(calendar[11], key[1]), "buyable"]
    for day in calendar[12:14]:
        assert not actual.loc[(day, key[1]), ["tradable", "buyable", "sellable"]].any()
    assert actual.loc[(calendar[14], key[1]), ["tradable", "buyable", "sellable"]].all()


@pytest.mark.parametrize("column", ["tradable", "buyable", "sellable", "suspended"])
@pytest.mark.parametrize("value", [-1., .5, 2., np.inf])
def test_invalid_source_directional_states_are_rejected(source_spec, column, value):
    raw = QlibBinSource(source_spec.source).read(source_spec.fields)
    raw[column] = value
    with pytest.raises(QualityError, match="Trading states"):
        normalize(raw, source_spec)


def test_explicit_source_tradability_cannot_override_missing_quotes(source_spec, calendar):
    raw = QlibBinSource(source_spec.source).read(source_spec.fields)
    raw["tradable"] = 1.
    key = (calendar[10], "510300.SH")
    raw.loc[key, ["open", "close"]] = np.nan
    actual = normalize(raw, source_spec)
    assert not actual.loc[key, "tradable"]


def test_historical_directional_states_do_not_change_membership_or_fill_gaps(panel, metadata, calendar):
    policy = UniversePolicy(minimum_listing_days=0, liquidity_lookback=1)
    original = build_history(panel, metadata, calendar, policy)
    frame = panel.copy()
    frame["buyable"] = True
    frame["sellable"] = True
    frame["suspended"] = False
    first = "510300.SH"
    frame.loc[(calendar[10], first), "buyable"] = False
    frame.loc[(calendar[11], first), "sellable"] = False
    frame.loc[(calendar[12], first), "suspended"] = True
    frame = frame.drop((calendar[13], first))
    actual = build_history(frame, metadata, calendar, policy)
    expected = pd.concat([resolve(day, frame, metadata, calendar, policy)
                                           for day in calendar]).sort_index()
    assert_frame_equal(actual, expected, atol=1e-8, rtol=1e-12)
    assert_frame_equal(actual[["eligible", "reason"]], original[["eligible", "reason"]])
    assert not actual.loc[(calendar[10], first), "buyable"]
    assert actual.loc[(calendar[10], first), "sellable"]
    assert actual.loc[(calendar[11], first), "buyable"]
    assert not actual.loc[(calendar[11], first), "sellable"]
    assert not actual.loc[(calendar[12], first), ["buyable", "sellable"]].any()
    assert not actual.loc[(calendar[13], first), ["buyable", "sellable"]].any()
    assert actual.loc[(calendar[14], first), ["buyable", "sellable"]].all()
    changed = frame.copy()
    changed.loc[changed.index.get_level_values("datetime") > calendar[15], "buyable"] = False
    future = build_history(changed, metadata, calendar, policy)
    assert_frame_equal(actual.loc[:calendar[15]], future.loc[:calendar[15]])


@pytest.mark.parametrize("column,value,buy,sell", [
    ("buyable", 0., "source_buy_blocked", None),
    ("buyable", np.nan, "unknown_buy_state", None),
    ("sellable", 0., None, "source_sell_blocked"),
    ("sellable", np.nan, None, "unknown_sell_state"),
    ("tradable", 0., "source_not_tradable", "source_not_tradable"),
    ("tradable", np.nan, "unknown_tradable_state", "unknown_tradable_state"),
    ("suspended", 1., "suspended", "suspended"),
    ("suspended", np.nan, "unknown_suspension_state", "unknown_suspension_state"),
    ("volume", 0., "zero_volume", "zero_volume"),
    ("volume", np.nan, "unknown_volume", "unknown_volume"),
    ("volume", -1., "invalid_volume", "invalid_volume"),
    ("volume", np.inf, "invalid_volume", "invalid_volume"),
])
def test_normalization_preserves_the_actual_unknown_or_blocked_reason(source_spec, calendar, column, value, buy, sell):
    from etf_ml.data.normalize import trading_reasons
    raw = QlibBinSource(source_spec.source).read(source_spec.fields)
    raw["buyable"] = raw["sellable"] = raw["tradable"] = 1.
    raw["suspended"] = 0.
    key = (calendar[10], "510300.SH")
    raw.loc[key, column] = value
    normalized = normalize(raw, source_spec)
    reasons = trading_reasons(normalized)
    assert reasons.loc[key, "buy_reason"] == buy
    assert reasons.loc[key, "sell_reason"] == sell
    assert all(pd.api.types.is_numeric_dtype(normalized[c]) for c in normalized)
    # A persisted numeric reason must retain the source distinction on reload.
    path = source_spec.artifact_root.parent / "reasons.parquet"
    normalized.to_parquet(path)
    assert_frame_equal(trading_reasons(pd.read_parquet(path)), reasons)


def test_trade_reason_priority_never_inherits_missing_day_or_future_state(panel, metadata, calendar):
    from etf_ml.data.normalize import trading_reasons, trading_permissions
    policy = UniversePolicy(minimum_listing_days=0, liquidity_lookback=1)
    key = (calendar[10], "510300.SH")
    frame = panel.copy()
    frame["suspended"] = False
    frame["buyable"] = True
    frame.loc[key, "suspended"] = True
    frame.loc[key, "buyable"] = False
    assert trading_reasons(frame).loc[key, "buy_reason"] == "suspended"
    frame.loc[key, "quoted"] = False
    assert not trading_permissions(frame).loc[key].any()
    assert trading_reasons(frame).loc[key, "buy_reason"] == "no_quote"
    frame = frame.drop((calendar[11], key[1]))
    history = build_history(frame, metadata, calendar, policy)
    assert history.loc[(calendar[11], key[1]), "buy_reason"] == "no_quote"
    assert history.loc[(calendar[11], key[1]), "sell_reason"] == "no_quote"
    assert history.loc[(calendar[12], key[1]), ["buy_reason", "sell_reason"]].isna().all()
    future = frame.copy()
    future.loc[future.index.get_level_values("datetime") > calendar[12], "suspended"] = True
    assert_frame_equal(build_history(future, metadata, calendar, policy).loc[:calendar[12]], history.loc[:calendar[12]])


def test_ineligible_holding_can_exit_without_a_false_buy_reason(panel, metadata, calendar):
    policy = UniversePolicy(minimum_listing_days=0, liquidity_lookback=1)
    metadata.loc[metadata.instrument.eq("510300.SH"), "operating"] = False
    history = build_history(panel, metadata, calendar, policy)
    row = history.loc[(calendar[10], "510300.SH")]
    assert not row.buyable and row.buy_reason == "not_operating"
    assert row.sellable and pd.isna(row.sell_reason)
