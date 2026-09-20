"""Trusted model/backtest worker; accepts only versioned project requests."""
from __future__ import annotations

import json
import sys
import os
import warnings

# Trusted workers retain structured progress/results. Repeated third-party
# empty-quote warnings and terminal progress bars can exhaust bounded logs
# on a full ETF universe without conveying additional diagnostic evidence.
os.environ.setdefault("TQDM_DISABLE", "1")
warnings.filterwarnings("ignore", message="Mean of empty slice", category=RuntimeWarning,
                        module=r"qlib\.utils\.index_data")
from pathlib import Path

import pandas as pd

from etf_ml.contracts import AppConfig, FeatureArtifact
from etf_ml.errors import IntegrityError
from etf_ml.pipeline import run_baseline
from etf_ml.research.paired import run_paired
from etf_ml.research.protocol import ComparisonProtocol
from etf_ml.utils import atomic_json, file_hash


def main():
    request_file = Path(sys.argv[1]).resolve()
    request = json.loads(request_file.read_text(encoding="utf-8"))
    config = AppConfig.model_validate(request["config"])
    if request["mode"] == "daily":
        from etf_ml.operations.daily import run_daily
        from etf_ml.operations.contracts import IngestionReceipt, PaperAccount
        run_daily(config, Path(request["package_path"]), Path(request["snapshot_path"]),
                  IngestionReceipt.model_validate(request["receipt"]), PaperAccount.model_validate(request["account"]),
                  request["as_of"], Path(request["output_root"]))
        return
    if request["mode"] == "holdout":
        from etf_ml.validation.holdout import run_holdout
        run_holdout(config, Path(request["package_path"]), Path(request["output_root"]), run_id=request["run_id"])
        return
    if request["mode"] == "baseline":
        feature = None
        if request.get("feature_override"):
            payload = request["feature_override"]
            path = Path(payload["path"])
            if file_hash(path) != payload["sha256"]:
                raise IntegrityError("Frozen baseline feature input changed")
            feature = FeatureArtifact(payload["feature_set_id"], pd.read_parquet(path), payload["manifest"])
        run_baseline(config, Path(request["snapshot_path"]), Path(request["output_root"]),
                     run_id=request["run_id"], feature_override=feature,
                     protocol_id=request.get("protocol_id"),
                     cost_multipliers=tuple(request.get("cost_multipliers", [2.0])))
        return
    if request["mode"] != "paired":
        raise IntegrityError("Unknown trusted research operation")
    manifest = request["factor_manifest"]
    cache = Path(manifest["path"])
    if file_hash(cache / "result.parquet") != manifest["result_hash"]:
        raise IntegrityError("Worker input factor cache changed")
    factor = FeatureArtifact(request["factor_id"], pd.read_parquet(cache / "result.parquet"), manifest)
    baseline = None
    if request.get("accumulated_feature_set_id"):
        from etf_ml.registry import FactorRegistry
        from etf_ml.research.feature_sets import FeatureSetStore
        store_root = Path(request["feature_store_root"])
        baseline = FeatureSetStore(store_root, FactorRegistry(store_root.parent / "registry")).load(
            request["accumulated_feature_set_id"])
    report = run_paired(config, Path(request["snapshot_path"]), factor,
                        ComparisonProtocol.model_validate(request["protocol"]),
                        Path(request["output_root"]), run_id=request["run_id"],
                        attempted_trials=request.get("attempted_trials", 1), baseline_override=baseline)
    atomic_json(request_file.parent / "response.json", report)


if __name__ == "__main__":
    from etf_ml.errors import ETFError
    try:
        main()
    except Exception as exc:
        path = Path(sys.argv[1]).resolve().parent / "worker_error.json"
        atomic_json(path, {"exit_code": exc.exit_code if isinstance(exc, ETFError) else 4,
                           "reason": getattr(exc, "reason", "execution_failed"),
                           "message": str(exc), "exception_type": type(exc).__name__})
        raise SystemExit(exc.exit_code if isinstance(exc, ETFError) else 4)
