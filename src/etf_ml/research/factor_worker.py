"""Portable trusted harness, copied into each isolated container workspace."""
import importlib.metadata
import json
import runpy
import sys

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

panel = pd.read_parquet(sys.argv[1])
candidate = runpy.run_path("factor.py")["compute"]
checks = []
def compute(data):
    result = candidate(data.copy())
    if isinstance(result, pd.Series):
        result = result.to_frame("factor")
    if not isinstance(result, pd.DataFrame) or result.shape[1] != 1:
        raise ValueError("Candidate must return one numeric column")
    if not result.index.equals(data.index):
        raise ValueError("Candidate must preserve the input index")
    if not pd.api.types.is_numeric_dtype(result.dtypes.iloc[0]):
        raise ValueError("Candidate output is not numeric")
    return result

full = compute(panel)
dates = panel.index.get_level_values("datetime").unique()
cutoff_positions = sorted({max(0, len(dates) // 3 - 1), max(0, len(dates) * 2 // 3 - 1),
                           max(0, len(dates) - 7), max(0, len(dates) - 2)})
for cutoff_position in cutoff_positions:
    cutoff = dates[cutoff_position]
    past_mask = panel.index.get_level_values("datetime") <= cutoff
    truncated = compute(panel[past_mask])
    assert_frame_equal(full.loc[truncated.index], truncated, check_exact=False, rtol=1e-10, atol=1e-10)
    perturbed = panel.copy()
    columns = list(perturbed.select_dtypes(include=[np.number]).columns)
    perturbed[columns] = perturbed[columns].astype(float)
    perturbed.loc[~past_mask, columns] = perturbed.loc[~past_mask, columns] * 1.73 + 23
    future_result = compute(perturbed)
    assert_frame_equal(full.loc[truncated.index], future_result.loc[truncated.index],
                       check_exact=False, rtol=1e-10, atol=1e-10)
checks.extend(["truncation_invariance", "future_perturbation_invariance",
               "multiple_cutoff_invariance"])
reordered = panel.sort_index(level=["datetime", "instrument"], ascending=[True, False])
assert_frame_equal(full, compute(reordered).reindex(full.index),
                   check_exact=False, rtol=1e-10, atol=1e-10)
checks.append("instrument_permutation_invariance")
if sys.argv[2] == "temporal":
    instrument = panel.index.get_level_values("instrument").unique()[0]
    single = panel.loc[(slice(None), [instrument]), :]
    assert_frame_equal(full.loc[single.index], compute(single), check_exact=False, rtol=1e-10, atol=1e-10)
    extra = single.copy()
    extra.index = pd.MultiIndex.from_arrays([
        extra.index.get_level_values("datetime"),
        ["__extra_instrument__"] * len(extra)], names=extra.index.names)
    enlarged = pd.concat([panel, extra]).sort_index()
    assert_frame_equal(full, compute(enlarged).loc[full.index],
                       check_exact=False, rtol=1e-10, atol=1e-10)
    checks.extend(["instrument_independence", "instrument_addition_invariance"])
full.to_parquet("result.parquet")
full.to_hdf("result.h5", key="data", format="table")
packages = {}
for name in ("numpy", "pandas", "pyarrow", "pyqlib", "rdagent"):
    try:
        packages[name] = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        packages[name] = None
with open("checks.json", "w") as stream:
    json.dump({"status": "passed", "checks": checks, "packages": packages}, stream)
