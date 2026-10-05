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
from etf_ml.research.progress import Progress
from etf_ml.utils import atomic_json, file_hash


def main():
    request_file = Path(sys.argv[1]).resolve()
    request = json.loads(request_file.read_text(encoding="utf-8"))
    progress = Progress(request_file.parent, request.get("run_id", request_file.stem), "experiment_worker")
    progress.emit("started", mode=request.get("mode"))
    config = AppConfig.model_validate(request["config"])
    if request["mode"] == "daily":
        from etf_ml.operations.daily import run_daily
        from etf_ml.operations.contracts import IngestionReceipt, PaperAccount
        run_daily(config, Path(request["package_path"]), Path(request["snapshot_path"]),
                  IngestionReceipt.model_validate(request["receipt"]), PaperAccount.model_validate(request["account"]),
                  request["as_of"], Path(request["output_root"]))
        progress.emit("finished", status="completed", mode="daily")
        return
    if request["mode"] == "holdout":
        from etf_ml.validation.holdout import run_holdout
        run_holdout(config, Path(request["package_path"]), Path(request["output_root"]), run_id=request["run_id"],
                    access_audit=request.get("access_audit"), access_audit_sha256=request.get("access_audit_sha256"))
        progress.emit("finished", status="completed", mode="holdout")
        return
    if request["mode"] == "baseline":
        from etf_ml.artifacts import environment_manifest
        from etf_ml.utils import code_hash
        execution_identity = {"source_code_hash": code_hash(), "environment": environment_manifest()}
        if request.get("execution_identity") is not None and request["execution_identity"] != execution_identity:
            raise IntegrityError("Baseline worker runtime differs from requested identity")
        feature = None
        if request.get("feature_override"):
            payload = request["feature_override"]
            path = Path(payload["path"])
            if file_hash(path) != payload["sha256"]:
                raise IntegrityError("Frozen baseline feature input changed")
            feature = FeatureArtifact(payload["feature_set_id"], pd.read_parquet(path), payload["manifest"])
        result = run_baseline(config, Path(request["snapshot_path"]), Path(request["output_root"]),
                     run_id=request["run_id"], feature_override=feature,
                     protocol_id=request.get("protocol_id"),
                     cost_multipliers=tuple(request.get("cost_multipliers", [2.0])),
                     progress=progress)
        if execution_identity != {"source_code_hash": code_hash(), "environment": environment_manifest()}:
            raise IntegrityError("Baseline worker runtime changed during execution")
        result["execution_identity"] = execution_identity
        atomic_json(Path(request["output_root"]) / "baseline_report.json", result)
        progress.emit("finished", status="completed", mode="baseline")
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
    progress.emit("finished", status="completed", mode="paired", evaluation_status=report["status"])


if __name__ == "__main__":
    from etf_ml.errors import ETFError
    try:
        main()
    except Exception as exc:
        request_file = Path(sys.argv[1]).resolve()
        path = request_file.parent / "worker_error.json"
        atomic_json(path, {"exit_code": exc.exit_code if isinstance(exc, ETFError) else 4,
                           "reason": getattr(exc, "reason", "execution_failed"),
                           "message": str(exc), "exception_type": type(exc).__name__})
        try:
            request = json.loads(request_file.read_text(encoding="utf-8"))
            Progress(request_file.parent, request.get("run_id", request_file.stem),
                     "experiment_worker").emit("failed", status="failed",
                                               reason=getattr(exc, "reason", "execution_failed"),
                                               exception_type=type(exc).__name__)
        except (OSError, ValueError):
            pass
        raise SystemExit(exc.exit_code if isinstance(exc, ETFError) else 4)
