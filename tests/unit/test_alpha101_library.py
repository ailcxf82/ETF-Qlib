import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from etf_ml.errors import QualityError
from etf_ml.features.alpha101 import (DEFINITIONS, alpha360_features, catalog, compute_alpha101,
                                      decay_linear, derive_vwap, rolling_rank)
from etf_ml.features.validators import check_causality


@pytest.fixture
def alpha_panel():
    rng = np.random.default_rng(807)
    index = pd.MultiIndex.from_product([pd.bdate_range("2023-01-02", periods=70), list("ABCD")],
                                      names=["datetime", "instrument"])
    c = np.exp(rng.normal(0, .03, (70, 4)).cumsum(axis=0)) * 10
    o = c * np.exp(rng.normal(0, .01, c.shape))
    high, low = np.maximum(o, c) + .2, np.minimum(o, c) - .2
    v = rng.integers(1000, 10000, c.shape).astype(float)
    return pd.DataFrame({"adj_open": o.ravel(), "adj_high": high.ravel(), "adj_low": low.ravel(),
                         "adj_close": c.ravel(), "volume_shares": v.ravel(), "adjustment_factor": 1.,
                         "raw_low": low.ravel(), "raw_high": high.ravel(),
                         "amount_currency": (c * v).ravel(), "quoted": True}, index=index)


def _reference(number, frame):
    """Independent small-array oracle: explicit windows and NumPy statistics."""
    o, h, low, c, v = [frame[x].unstack().to_numpy() for x in
                       ("adj_open", "adj_high", "adj_low", "adj_close", "volume_shares")]

    def shift(x, n):
        return np.vstack([np.full((n, x.shape[1]), np.nan), x[:-n]])

    def delta(x, n):
        return x - shift(x, n)

    def rank(x):
        x = np.round(x, decimals=12)
        out = np.full_like(x, np.nan)
        for day in range(len(x)):
            valid = x[day][np.isfinite(x[day])]
            for column, value in enumerate(x[day]):
                if np.isfinite(value):
                    out[day, column] = (np.sum(valid < value) + (np.sum(valid == value) + 1) / 2) / len(valid)
        return out

    def roll(x, n, fn, y=None):
        out = np.full_like(x, np.nan)
        for day in range(n - 1, len(x)):
            for column in range(x.shape[1]):
                a = x[day - n + 1:day + 1, column]
                b = y[day - n + 1:day + 1, column] if y is not None else None
                if np.isfinite(a).all() and (b is None or np.isfinite(b).all()):
                    out[day, column] = fn(a) if b is None else fn(a, b)
        return out

    def corr(x, y, n):
        return roll(x, n, lambda a, b: np.corrcoef(a, b)[0, 1]
                    if np.std(a) > 0 and np.std(b) > 0 else np.nan, y)

    def tr(x, n):
        return roll(x, n, lambda a: (np.sum(a < a[-1]) + (np.sum(a == a[-1]) + 1) / 2) / n)

    def std(x, n):
        return roll(x, n, lambda a: np.std(a, ddof=1))

    r = c / shift(c, 1) - 1
    if number == 3: return -corr(rank(o), rank(v), 10)
    if number == 4: return -tr(rank(low), 9)
    if number == 6: return -corr(o, v, 10)
    if number == 12: return np.sign(delta(v, 1)) * -delta(c, 1)
    if number in (13, 16): return -rank(roll(rank(c if number == 13 else h), 5, lambda a, b: np.cov(a, b, ddof=1)[0, 1], rank(v)))
    if number == 14: return -rank(delta(r, 3)) * corr(o, v, 10)
    if number == 15: return -roll(rank(corr(rank(h), rank(v), 3)), 3, np.sum)
    if number == 18: return -rank(std(abs(c - o), 5) + c - o + corr(c, o, 10))
    if number == 20: return -rank(o - shift(h, 1)) * rank(o - shift(c, 1)) * rank(o - shift(low, 1))
    if number == 23:
        average = roll(h, 20, np.mean)
        return np.where(np.isnan(average), np.nan, np.where(h > average, -delta(h, 2), 0))
    if number == 26: return -roll(corr(tr(v, 5), tr(h, 5), 5), 3, np.max)
    if number == 33: return rank(o / c - 1)
    if number == 34: return rank(1 - rank(std(r, 2) / std(r, 5)) + 1 - rank(delta(c, 1)))
    if number == 35: return tr(v, 32) * (1 - tr(c + h - low, 16)) * (1 - tr(r, 32))
    if number == 40: return -rank(std(h, 10)) * corr(h, v, 10)
    if number == 44: return -corr(h, rank(v), 5)
    if number == 45: return -rank(roll(shift(c, 5), 20, np.mean)) * corr(c, v, 2) * rank(corr(roll(c, 5, np.sum), roll(c, 20, np.sum), 2))
    if number == 55:
        minimum = roll(low, 12, np.min)
        return -corr(rank((c - minimum) / (roll(h, 12, np.max) - minimum)), rank(v), 6)
    if number == 101: return (c - o) / (h - low + .001)
    raise AssertionError(number)


@pytest.mark.parametrize("number", sorted(DEFINITIONS))
def test_each_registered_formula_matches_independent_numeric_oracle(alpha_panel, number):
    eligible = pd.Series(True, index=alpha_panel.index)
    actual = compute_alpha101(alpha_panel, number, eligible=eligible).iloc[:, 0].unstack().to_numpy()
    expected = _reference(number, alpha_panel)
    assert np.isfinite(expected).any()
    np.testing.assert_allclose(actual, expected, atol=1e-9, rtol=1e-8, equal_nan=True)


@pytest.mark.parametrize("number", sorted(DEFINITIONS))
def test_each_formula_is_causal_and_instrument_permutation_equivariant(alpha_panel, number):
    eligible = pd.Series(True, index=alpha_panel.index)
    compute = lambda frame: compute_alpha101(frame, number, eligible=eligible.reindex(frame.index))
    check_causality(compute, alpha_panel, alpha_panel.index.get_level_values("datetime").unique()[45])
    mapping = dict(zip("ABCD", "DCAB"))
    permuted = alpha_panel.rename(index=mapping, level="instrument").sort_index()
    result = compute_alpha101(permuted, number, eligible=pd.Series(True, index=permuted.index))
    restored = result.rename(index={v: k for k, v in mapping.items()}, level="instrument").sort_index()
    assert_frame_equal(compute(alpha_panel), restored, rtol=1e-8, atol=1e-9)


def test_rank_uses_historical_eligible_universe_and_keeps_missing_rows(alpha_panel):
    eligible = pd.Series(True, index=alpha_panel.index)
    eligible.loc[(slice(None), "D")] = False
    out = compute_alpha101(alpha_panel, 33, eligible=eligible)
    assert out.xs("D", level="instrument").isna().all().all()
    changed = alpha_panel.copy()
    changed.loc[(slice(None), "D"), "adj_open"] = 1e8
    assert_frame_equal(out, compute_alpha101(changed, 33, eligible=eligible))


def test_operators_have_explicit_tie_ddof_and_decay_semantics():
    values = pd.DataFrame({"x": [1., 2., 2.]})
    assert rolling_rank(values, 3).iloc[-1, 0] == pytest.approx(2.5 / 3)
    assert decay_linear(values, 3).iloc[-1, 0] == pytest.approx(11 / 6)
    assert np.isnan(rolling_rank(values, 3).iloc[1, 0])


def test_zero_range_division_missing_and_invalid_input_handling(alpha_panel):
    eligible = pd.Series(True, index=alpha_panel.index)
    with pytest.raises(QualityError, match="registered batch"):
        compute_alpha101(alpha_panel, 53, eligible=eligible)
    with pytest.raises(QualityError, match="eligibility"):
        compute_alpha101(alpha_panel, 33, eligible=eligible.astype(float))
    broken = alpha_panel.copy()
    broken.iloc[0, broken.columns.get_loc("adj_close")] = np.inf
    with pytest.raises(QualityError, match="finite"):
        compute_alpha101(broken, 33, eligible=eligible)
    constant = alpha_panel.assign(adj_low=10., adj_high=10., adj_close=10.)
    result = compute_alpha101(constant, 55, eligible=eligible)
    assert result.isna().all().all()


def test_vwap_units_and_alpha360_match_installed_qlib_configuration(alpha_panel):
    from qlib.contrib.data.loader import Alpha360DL
    full = alpha360_features(alpha_panel, include_vwap=True)
    fields, names = Alpha360DL.get_feature_config()
    assert list(full.columns) == names and len(fields) == full.shape[1] == 360
    assert alpha360_features(alpha_panel).shape[1] == 300
    first = alpha_panel.xs("A", level="instrument")
    last = full.xs("A", level="instrument").iloc[-1]
    assert last.CLOSE59 == pytest.approx(first.adj_close.iloc[-60] / first.adj_close.iloc[-1])
    assert last.VOLUME59 == pytest.approx(first.volume_shares.iloc[-60] / (first.volume_shares.iloc[-1] + 1e-12))
    assert last.VWAP0 == pytest.approx(1.)
    wrong_units = alpha_panel.assign(amount_currency=alpha_panel.amount_currency * 1000)
    with pytest.raises(QualityError, match="raw price range"):
        derive_vwap(wrong_units)
    zero = alpha_panel.copy()
    zero.loc[zero.index[0], ["volume_shares", "amount_currency"]] = 0
    assert pd.isna(derive_vwap(zero).iloc[0])


def test_catalog_is_bounded_versioned_and_records_bypasses():
    data = catalog()
    assert len(data["definitions"]) == 20
    assert len({row["definition_hash"] for row in data["definitions"]}) == 20
    assert max(row["lookback"] for row in data["definitions"]) <= 120
    assert data["source"]["document_sha256"]
    assert all(row["status"] == "implemented_unvalidated" for row in data["definitions"])
