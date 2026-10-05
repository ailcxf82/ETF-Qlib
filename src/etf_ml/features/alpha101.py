"""Independent ETF implementation of a versioned subset of formulaic alphas.

Only mathematical definitions from Appendix A are used. This is not vendor
code or vendor performance data. Daily inputs are available after ingestion;
cross-sectional ranks use the explicit, same-date eligible instrument mask.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from etf_ml.data.source import require_panel
from etf_ml.errors import QualityError
from etf_ml.utils import canonical_json, content_hash


SOURCE = {
    "schema_version": "factor-source-v1", "library": "WorldQuant Alpha101",
    "reference": "https://arxiv.org/pdf/1601.00991v3", "section": "Appendix A",
    "document_sha256": "1f9c21afe32dcb3ee77b31548acdaea00451fbfa1c0ee10c907867bcc736fce9",
    "use_basis": "Published mathematical definitions, independently implemented for local research; Appendix B terms",
    "vendor_code_imported": False, "vendor_data_used": False, "implementation_version": "etf-alpha101-v1",
    "operator_contract": {"rank": "inputs rounded to 12 decimals; same-date eligible cross-section; average ties; percentile 1/n..1",
        "ts_rank": "rank of newest value; average ties; percentile 1/window..1",
        "stddev": "sample ddof=1", "covariance": "sample ddof=1",
        "rolling": "trading sessions, includes current day, full nonmissing window required",
        "division": "zero denominator produces missing; no forward/backward filling",
        "decay_linear": "weights oldest=1, newest=window, normalized to one"},
    "field_mapping": {"open": "adj_open", "high": "adj_high", "low": "adj_low", "close": "adj_close",
                      "volume": "volume_shares / adjustment_factor", "returns": "adj_close / delay(adj_close,1) - 1"},
    "scope": "ETF adaptation, not a claim of vendor numerical parity",
    "available_at": "after_daily_ingestion", "execution_lag_sessions": 1,
}

# Expressions are descriptive mathematical data. Execution uses the explicit
# branches below; no expression string is evaluated as Python code.
DEFINITIONS = {
    3: ("-correlation(rank(open),rank(volume),10)", 10, "volume_price"),
    4: ("-ts_rank(rank(low),9)", 9, "reversal"),
    6: ("-correlation(open,volume,10)", 10, "volume_price"),
    12: ("sign(delta(volume,1)) * -delta(close,1)", 2, "volume_price"),
    13: ("-rank(covariance(rank(close),rank(volume),5))", 5, "volume_price"),
    14: ("-rank(delta(returns,3)) * correlation(open,volume,10)", 10, "reversal"),
    15: ("-sum(rank(correlation(rank(high),rank(volume),3)),3)", 5, "volume_price"),
    16: ("-rank(covariance(rank(high),rank(volume),5))", 5, "volume_price"),
    18: ("-rank(stddev(abs(close-open),5) + close-open + correlation(close,open,10))", 10, "range"),
    20: ("-rank(open-delay(high,1))*rank(open-delay(close,1))*rank(open-delay(low,1))", 2, "reversal"),
    23: ("high > mean(high,20) ? -delta(high,2) : 0", 20, "reversal"),
    26: ("-ts_max(correlation(ts_rank(volume,5),ts_rank(high,5),5),3)", 11, "volume_price"),
    33: ("rank(open/close-1)", 1, "reversal"),
    34: ("rank(1-rank(stddev(returns,2)/stddev(returns,5))+1-rank(delta(close,1)))", 6, "volatility"),
    35: ("ts_rank(volume,32)*(1-ts_rank(close+high-low,16))*(1-ts_rank(returns,32))", 33, "volume_price"),
    40: ("-rank(stddev(high,10))*correlation(high,volume,10)", 10, "volatility"),
    44: ("-correlation(high,rank(volume),5)", 5, "volume_price"),
    45: ("-rank(mean(delay(close,5),20))*correlation(close,volume,2)*rank(correlation(sum(close,5),sum(close,20),2))", 25, "volume_price"),
    55: ("-correlation(rank((close-ts_min(low,12))/(ts_max(high,12)-ts_min(low,12))),rank(volume),6)", 17, "volume_price"),
    101: ("(close-open)/(high-low+0.001)", 1, "intraday_momentum"),
}


def catalog():
    result = []
    for number, (formula, lookback, group) in sorted(DEFINITIONS.items()):
        definition = {"factor_id": f"alpha101_{number:03d}_etf", "number": number,
                      "formula": formula, "lookback": lookback, "minimum_observations": lookback,
                      "research_group": group, "source_manifest_hash": content_hash(SOURCE),
                      "required_fields": ["adj_open", "adj_high", "adj_low", "adj_close",
                                          "volume_shares", "adjustment_factor"],
                      "direction": "positive", "direction_selection": "fixed_published_orientation",
                      "cross_sectional_universe": "explicit_historical_eligibility",
                      "missing_policy": "full_windows_no_fill", "status": "implemented_unvalidated"}
        result.append({**definition, "definition_hash": content_hash(definition)})
    return {"schema_version": "factor-catalog-v1", "source": SOURCE, "definitions": result,
            "bypassed": [{"numbers": [42, 48, 53, 54], "reason": "original_delay_zero_not_in_next_open_batch"},
                         {"numbers": [17, 43], "reason": "adv_dollar_volume_semantics_require_separate_mapping"},
                         {"reason": "other_formulas_outside_preregistered_batch_or_require_unmapped_inputs"}]}


def validate_library_reference(reference):
    """Resolve an immutable library ID to the exact locally audited definition."""
    if not isinstance(reference, dict) or set(reference) != {
            "kind", "number", "definition_hash", "source_manifest_hash", "implementation_version"}:
        raise QualityError("Trusted factor reference has an invalid schema")
    definition = next((row for row in catalog()["definitions"] if row["number"] == reference["number"]), None)
    if (reference["kind"] != "worldquant_alpha101_etf" or definition is None
            or reference["definition_hash"] != definition["definition_hash"]
            or reference["source_manifest_hash"] != content_hash(SOURCE)
            or reference["implementation_version"] != SOURCE["implementation_version"]):
        raise QualityError("Trusted factor reference differs from the audited catalogue")
    return definition


def materialize_library_reference(panel, eligible, reference):
    definition = validate_library_reference(reference)
    return compute_alpha101(panel, definition["number"], eligible=eligible)


def library_source_descriptor(reference):
    definition = validate_library_reference(reference)
    return canonical_json({"execution": "trusted_controller_library", "reference": reference,
                           "definition": definition, "source": SOURCE})


def rolling_rank(values, window):
    return values.rolling(window, min_periods=window).rank(method="average", pct=True)


def decay_linear(values, window):
    if type(window) is not int or window < 1:
        raise QualityError("Decay window must be a positive integer")
    weights = np.arange(1, window + 1, dtype=float)
    return values.rolling(window, min_periods=window).apply(lambda row: row @ weights / weights.sum(), raw=True)


def compute_alpha101(panel, number, *, eligible):
    require_panel(panel)
    if number not in DEFINITIONS:
        raise QualityError("Alpha formula is outside the registered batch")
    if (not eligible.index.equals(panel.index) or eligible.isna().any() or
            not pd.api.types.is_bool_dtype(eligible)):
        raise QualityError("Alpha rank universe requires aligned historical boolean eligibility")
    required = {"adj_open", "adj_high", "adj_low", "adj_close", "volume_shares", "adjustment_factor"}
    if not required.issubset(panel):
        raise QualityError("Alpha input fields are missing")
    if (np.isinf(panel[list(required)].to_numpy(dtype=float)).any() or
            (panel.adjustment_factor.dropna() <= 0).any()):
        raise QualityError("Alpha inputs must have finite values and positive adjustment factors")
    o, h, low, c = [panel[name].unstack("instrument") for name in ("adj_open", "adj_high", "adj_low", "adj_close")]
    v = (panel.volume_shares / panel.adjustment_factor).unstack("instrument")
    mask = eligible.unstack("instrument").reindex_like(c).fillna(False).astype(bool)
    if "quoted" in panel:
        quoted = panel.quoted.unstack("instrument").reindex_like(c).fillna(False).astype(bool)
        o, h, low, c, v = [x.where(quoted) for x in (o, h, low, c, v)]
    returns = c / c.shift(1).replace(0, np.nan) - 1

    def rank(x):
        # Cancellation in rolling covariance can otherwise rank mathematical
        # zero as different values. Quantization is part of the frozen contract.
        return x.round(12).where(mask).rank(axis=1, method="average", pct=True)

    def corr(x, y, window):
        if window == 2:
            # For exactly two nonconstant observations Pearson correlation is
            # sign(dx*dy); avoid subtracting near-equal rolling second moments.
            dx, dy = x.diff(1), y.diff(1)
            return np.sign(dx * dy).where(dx.ne(0) & dy.ne(0) & dx.notna() & dy.notna())
        result = x.rolling(window, min_periods=window).corr(y)
        return result.where((x.rolling(window).std() > 0) & (y.rolling(window).std() > 0)).clip(-1, 1)

    if number == 3:
        result = -corr(rank(o), rank(v), 10)
    elif number == 4:
        result = -rolling_rank(rank(low), 9)
    elif number == 6:
        result = -corr(o, v, 10)
    elif number == 12:
        result = np.sign(v.diff(1)) * -c.diff(1)
    elif number in (13, 16):
        price = rank(c if number == 13 else h)
        result = -rank(price.rolling(5, min_periods=5).cov(rank(v), ddof=1))
    elif number == 14:
        result = -rank(returns.diff(3)) * corr(o, v, 10)
    elif number == 15:
        result = -rank(corr(rank(h), rank(v), 3)).rolling(3, min_periods=3).sum()
    elif number == 18:
        result = -rank((c - o).abs().rolling(5, min_periods=5).std(ddof=1) + c - o + corr(c, o, 10))
    elif number == 20:
        result = -rank(o - h.shift(1)) * rank(o - c.shift(1)) * rank(o - low.shift(1))
    elif number == 23:
        average = h.rolling(20, min_periods=20).mean()
        delta = h.diff(2)
        result = (-delta).where(h > average, 0.).where(average.notna() & h.notna() & delta.notna())
    elif number == 26:
        result = -corr(rolling_rank(v, 5), rolling_rank(h, 5), 5).rolling(3, min_periods=3).max()
    elif number == 33:
        result = rank(o / c.replace(0, np.nan) - 1)
    elif number == 34:
        ratio = returns.rolling(2, min_periods=2).std(ddof=1) / returns.rolling(5, min_periods=5).std(ddof=1).replace(0, np.nan)
        result = rank(1 - rank(ratio) + 1 - rank(c.diff(1)))
    elif number == 35:
        result = rolling_rank(v, 32) * (1 - rolling_rank(c + h - low, 16)) * (1 - rolling_rank(returns, 32))
    elif number == 40:
        result = -rank(h.rolling(10, min_periods=10).std(ddof=1)) * corr(h, v, 10)
    elif number == 44:
        result = -corr(h, rank(v), 5)
    elif number == 45:
        result = (-rank(c.shift(5).rolling(20, min_periods=20).mean()) * corr(c, v, 2) *
                  rank(corr(c.rolling(5, min_periods=5).sum(), c.rolling(20, min_periods=20).sum(), 2)))
    elif number == 55:
        floor = low.rolling(12, min_periods=12).min()
        denominator = h.rolling(12, min_periods=12).max() - floor
        result = -corr(rank((c - floor) / denominator.replace(0, np.nan)), rank(v), 6)
    else:  # 101
        result = (c - o) / (h - low + .001).replace(0, np.nan)
    if np.isinf(result.to_numpy()).any():
        raise QualityError("Alpha calculation produced infinite output")
    values = result.where(mask).stack(future_stack=True).reindex(panel.index)
    return values.rename(f"alpha101_{number:03d}_etf_v1").to_frame()


def derive_vwap(panel):
    """Audit the standardized currency/share identity before deriving adjusted VWAP."""
    require_panel(panel)
    fields = {"volume_shares", "amount_currency", "adjustment_factor", "raw_low", "raw_high"}
    if not fields.issubset(panel):
        raise QualityError("VWAP requires standardized shares, currency, adjustment and raw range")
    if np.isinf(panel[list(fields)].to_numpy(dtype=float)).any():
        raise QualityError("VWAP input must be finite or missing")
    if ((panel.volume_shares < 0) | (panel.amount_currency < 0) | (panel.adjustment_factor <= 0)).any():
        raise QualityError("VWAP input units or signs are invalid")
    raw = panel.amount_currency / panel.volume_shares.replace(0, np.nan)
    valid = raw.notna()
    bad = valid & ((raw < panel.raw_low - .001) | (raw > panel.raw_high + .001) |
                   panel.raw_low.isna() | panel.raw_high.isna())
    if bad.any():
        raise QualityError("VWAP currency/share value is outside the raw price range")
    if ((panel.volume_shares == 0) & (panel.amount_currency > 0)).any():
        raise QualityError("Nonzero amount with zero shares")
    return (raw * panel.adjustment_factor).rename("adj_vwap")


def alpha360_features(panel, *, include_vwap=False):
    """Qlib-compatible column ordering, explicit ETF adaptation and no fake VWAP."""
    require_panel(panel)
    required = {"adj_close", "adj_open", "adj_high", "adj_low", "volume_shares", "adjustment_factor"}
    if not required.issubset(panel):
        raise QualityError("Alpha360 input fields missing")
    values = {name: panel[field] for name, field in (
        ("CLOSE", "adj_close"), ("OPEN", "adj_open"), ("HIGH", "adj_high"), ("LOW", "adj_low"))}
    if include_vwap:
        values["VWAP"] = derive_vwap(panel)
    volume = panel.volume_shares / panel.adjustment_factor.replace(0, np.nan)
    values["VOLUME"] = volume
    columns = {}
    for name, series in values.items():
        denominator = (volume + 1e-12) if name == "VOLUME" else panel.adj_close.replace(0, np.nan)
        grouped = series.groupby(level="instrument")
        for lag in range(59, -1, -1):
            columns[f"{name}{lag}"] = (grouped.shift(lag) if lag else series) / denominator
    return pd.DataFrame(columns, index=panel.index)
