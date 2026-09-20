from __future__ import annotations

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from etf_ml.data.source import require_panel
from etf_ml.errors import QualityError


def validate_factor(factor: pd.DataFrame, expected_index: pd.MultiIndex, *,
                    coverage_threshold: float = 0.95) -> dict:
    require_panel(factor, numeric=True)
    if factor.shape[1] != 1:
        raise QualityError("A candidate factor must have one numeric column")
    if not factor.index.equals(expected_index):
        raise QualityError("Candidate changed fixed evaluation index")
    if np.isinf(factor.to_numpy(dtype=float)).any():
        raise QualityError("Infinite factor values")
    valid = factor.iloc[:, 0].notna()
    by_date = valid.groupby(level="datetime").mean()
    coverage = float(by_date.mean()) if len(by_date) else 0
    worst = float(by_date.min()) if len(by_date) else 0
    if coverage < coverage_threshold:
        raise QualityError("Factor coverage below frozen threshold")
    if factor.iloc[:, 0].dropna().nunique() <= 1:
        raise QualityError("Constant factor")
    return {"coverage": coverage, "worst_date_coverage": worst,
            "by_date": {str(k.date()): float(v) for k, v in by_date.items()},
            "nonfinite": 0, "duplicate_keys": 0}


def check_causality(compute, panel: pd.DataFrame, cutoff, *, tolerance=1e-10) -> None:
    require_panel(panel, numeric=True)
    cutoff = pd.Timestamp(cutoff)
    mask = panel.index.get_level_values("datetime") <= cutoff
    full = compute(panel).loc[panel.index[mask]]
    truncated = compute(panel[mask]).loc[panel.index[mask]]
    perturbed = panel.copy()
    numeric_columns = list(panel.select_dtypes(include=[np.number]).columns)
    perturbed[numeric_columns] = perturbed[numeric_columns].astype(float)
    perturbed.loc[~mask, numeric_columns] = perturbed.loc[~mask, numeric_columns] * 1.73 + 23
    changed = compute(perturbed).loc[panel.index[mask]]
    try:
        assert_frame_equal(full, truncated, check_exact=False, rtol=tolerance, atol=tolerance)
        assert_frame_equal(full, changed, check_exact=False, rtol=tolerance, atol=tolerance)
    except AssertionError as exc:
        raise QualityError("Factor depends on future data") from exc


def check_grouping(compute, panel: pd.DataFrame, *, cross_sectional=False,
                   tolerance=1e-10) -> None:
    original = compute(panel)
    shuffled = compute(panel.sample(frac=1, random_state=123).sort_index())
    try:
        assert_frame_equal(original, shuffled, check_exact=False, rtol=tolerance, atol=tolerance)
        if not cross_sectional:
            first = panel.index.get_level_values("instrument").unique()[0]
            single = panel.loc[(slice(None), [first]), :]
            assert_frame_equal(original.loc[single.index], compute(single),
                               check_exact=False, rtol=tolerance, atol=tolerance)
    except AssertionError as exc:
        raise QualityError("Factor grouping or ordering contamination") from exc


def redundancy(factor: pd.Series, existing: pd.DataFrame) -> dict[str, float | None]:
    result = {}
    for column in existing:
        values = []
        for date, candidate in factor.groupby(level="datetime"):
            sample = pd.concat([candidate, existing[column].reindex(candidate.index)], axis=1).dropna()
            if len(sample) >= 3 and all(sample[c].nunique() > 1 for c in sample):
                values.append(abs(float(sample.iloc[:, 0].corr(sample.iloc[:, 1], method="spearman"))))
        result[column] = float(np.median(values)) if values else None
    return result
