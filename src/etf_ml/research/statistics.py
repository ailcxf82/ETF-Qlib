from __future__ import annotations

import numpy as np
import pandas as pd

from etf_ml.errors import QualityError


def moving_block_mean_uncertainty(values, *, block_length=20, repetitions=1000,
                                  confidence=.95, seed=42) -> dict:
    """Development-only moving-block intervals for a daily signal statistic."""
    values = np.asarray(values, dtype=float)
    if (values.ndim != 1 or not len(values) or not np.isfinite(values).all() or
            block_length < 1 or repetitions < 100 or not 0 < confidence < 1):
        raise QualityError("Invalid daily signal series or block statistics settings")
    mean = float(values.mean())
    std = float(values.std(ddof=1)) if len(values) > 1 else None
    ir = mean / std if std is not None and std > 0 else None
    common = {"effective_dates": len(values), "block_length": block_length,
              "repetitions": repetitions, "confidence": confidence, "seed": seed,
              "mean": mean, "information_ratio": ir,
              "note": "Development diagnostic; repeated selection bias is not removed"}
    if len(values) < 3 * block_length:
        return {"status": "inconclusive", **common,
                "reason": "insufficient_time_blocks", "mean_confidence_interval": None,
                "information_ratio_confidence_interval": None}
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, len(values) - block_length + 1,
                          size=(repetitions, int(np.ceil(len(values) / block_length))))
    indices = (starts[..., None] + np.arange(block_length)).reshape(repetitions, -1)[:, :len(values)]
    samples = values[indices]
    means = samples.mean(axis=1)
    stds = samples.std(axis=1, ddof=1)
    ratios = np.divide(means, stds, out=np.full_like(means, np.nan), where=stds > 0)
    tail = (1 - confidence) / 2
    return {"status": "completed", **common,
            "mean_confidence_interval": np.quantile(means, [tail, 1 - tail]).tolist(),
            "information_ratio_confidence_interval": (
                np.quantile(ratios[np.isfinite(ratios)], [tail, 1 - tail]).tolist()
                if np.isfinite(ratios).any() else None),
            "available_block_starts": len(values) - block_length + 1}


def paired_block_uncertainty(baseline: pd.Series, candidate: pd.Series, *,
                             block_length: int = 20, repetitions: int = 1000,
                             confidence: float = .95, seed: int = 42,
                             attempted_trials: int = 1) -> dict:
    """Moving-block bootstrap using matched daily portfolio returns.

    Entire time blocks are resampled with the same indices for both strategies.
    This estimates uncertainty in candidate wealth minus baseline wealth. It is
    a development diagnostic and does not correct repeated selection bias.
    """
    if (not isinstance(baseline.index, pd.DatetimeIndex) or
            not baseline.index.equals(candidate.index) or baseline.index.has_duplicates or
            not baseline.index.is_monotonic_increasing):
        raise QualityError("Paired statistics require identical unique ordered dates")
    if (block_length < 1 or repetitions < 100 or not 0 < confidence < 1 or attempted_trials < 1):
        raise QualityError("Invalid frozen time-block statistics settings")
    values = np.column_stack([baseline.to_numpy(dtype=float), candidate.to_numpy(dtype=float)])
    if not len(values) or not np.isfinite(values).all() or (values <= -1).any():
        raise QualityError("Paired daily returns must be finite and exceed minus one")
    observed = float(np.prod(1 + values[:, 1]) - np.prod(1 + values[:, 0]))
    if not np.isfinite(observed):
        raise QualityError("Compounded paired return overflow")
    common = {"observed_net_return_increment": observed, "effective_dates": len(values),
              "block_length": block_length, "repetitions": repetitions,
              "confidence": confidence, "seed": seed, "attempted_trials": attempted_trials,
              "note": "Development time-block diagnostic; repeated selection bias is not removed"}
    # One or two blocks provide too little temporal information for this diagnostic.
    if len(values) < 3 * block_length:
        return {"status": "inconclusive", **common,
                "reason": "insufficient_time_blocks", "confidence_interval": None}
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, len(values) - block_length + 1,
                          size=(repetitions, int(np.ceil(len(values) / block_length))))
    offsets = np.arange(block_length)
    indices = (starts[..., None] + offsets).reshape(repetitions, -1)[:, :len(values)]
    samples = values[indices]
    wealth = np.prod(1 + samples, axis=1)
    if not np.isfinite(wealth).all():
        raise QualityError("Time-block compounded return overflow")
    increments = wealth[:, 1] - wealth[:, 0]
    tail = (1 - confidence) / 2
    interval = np.quantile(increments, [tail, 1 - tail]).tolist()
    return {"status": "completed", **common, "confidence_interval": interval,
            "bootstrap_fraction_positive": float(np.mean(increments > 0)),
            "available_block_starts": len(values) - block_length + 1}
