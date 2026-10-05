from __future__ import annotations

import json
import sys
from pathlib import Path

from etf_ml.errors import (QualityError, ExecutionError, ConfigurationError, IntegrityError,
                           DataNotReady, BudgetError)
from etf_ml.runtime.native import NativeBackend
from etf_ml.utils import atomic_json, content_hash, ensure_within


def _raise_failure(workspace, result):
    path = Path(workspace) / "worker_error.json"
    if result.reason == "nonzero_exit" and path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        category = {2: ConfigurationError, 3: DataNotReady, 5: QualityError, 6: BudgetError}.get(
            payload["exit_code"], ExecutionError)
        raise category(payload["message"])
    raise ExecutionError("Trusted execution failed: " + str(result.reason))


def execute_research(session, factor, *, run_id):
    from etf_ml.research.reuse_identity import local_reuse_root
    worker_config = session.config.model_dump(mode="json")
    # The trusted worker changes cwd. Pin only the shared store here; retain
    # the existing per-worker model/output isolation for relative artifact roots.
    worker_config["reuse_root"] = str(local_reuse_root(session.config))
    request = {"mode": "paired", "config": worker_config,
               "protocol": session.protocol.model_dump(mode="json"),
               "snapshot_path": str(session.snapshot.path), "factor_manifest": factor.manifest,
               "factor_id": factor.feature_set_id, "output_root": str(session.root / "paired"),
               "run_id": run_id, "attempted_trials": getattr(session, "attempted_trials", 1),
               "accumulated_feature_set_id": session.baseline.feature_set_id if session.baseline.feature_set_id != session.initial_baseline.feature_set_id else None,
               "feature_store_root": str(session.feature_store.root)}
    workspace = ensure_within(session.root / "executions" / content_hash(request)[:24], session.root)
    workspace.mkdir(parents=True, exist_ok=True)
    atomic_json(workspace / "request.json", request)
    result = NativeBackend(session.root).run(
        [sys.executable, "-m", "etf_ml.research.experiment_worker",
         str((workspace / "request.json").resolve())],
        workspace, {}, session.config.research.limits, trusted=True)
    atomic_json(workspace / "execution_result.json", result)
    if result.status != "succeeded":
        _raise_failure(workspace, result)
    response = workspace / "response.json"
    if not response.exists():
        raise QualityError("Trusted paired worker did not publish a response")
    return json.loads(response.read_text(encoding="utf-8"))


def execute_baseline(config, snapshot_path, output, *, run_id, feature_override=None,
                     protocol_id=None, cost_multipliers=(2.0,), _allow_shared=True):
    from etf_ml.artifacts import environment_manifest
    from etf_ml.utils import code_hash
    output = ensure_within(output, config.artifact_root)
    output.mkdir(parents=True, exist_ok=True)
    if _allow_shared:
        from etf_ml.research.reuse_baseline import SharedBaseline, baseline_key
        from etf_ml.research.reuse_identity import local_reuse_root
        from etf_ml.data.snapshot import load_snapshot
        snapshot = load_snapshot(snapshot_path)
        key = baseline_key(config, snapshot.snapshot_id,
                           feature_set_id=feature_override.feature_set_id if feature_override else None,
                           cost_multipliers=cost_multipliers)
        shared = SharedBaseline(local_reuse_root(config))
        report, reference, hit = shared.get_or_run(key, output, lambda: execute_baseline(
            config, snapshot_path, output, run_id=run_id, feature_override=feature_override,
            protocol_id=protocol_id, cost_multipliers=cost_multipliers, _allow_shared=False))
        report = {**report, "shared_baseline_package_id": reference["package_id"]}
        atomic_json(output / "baseline_reference.json", reference)
        atomic_json(output / "baseline_report.json", report)
        if hit:
            atomic_json(output / "execution_result.json", {"status": "succeeded", "returncode": 0,
                                                          "reused": True, **reference})
            atomic_json(output / "pipeline_request.json", {"mode": "baseline", "config": config.model_dump(mode="json"),
                "reused": True, "baseline_reference": reference, "snapshot_path": str(snapshot_path)})
        return report
    feature_payload = None
    if feature_override is not None:
        feature_path = output / "frozen_feature_input.parquet"
        feature_override.frame.to_parquet(feature_path)
        from etf_ml.utils import file_hash
        feature_payload = {"path": str(feature_path), "sha256": file_hash(feature_path),
                           "feature_set_id": feature_override.feature_set_id,
                           "manifest": feature_override.manifest}
    execution_identity = {"source_code_hash": code_hash(), "environment": environment_manifest()}
    atomic_json(output / "pipeline_request.json", {
        "feature_override": feature_payload, "protocol_id": protocol_id,
        "cost_multipliers": list(cost_multipliers), "mode": "baseline", "config": config.model_dump(mode="json"),
        "execution_identity": execution_identity,
        "snapshot_path": str(Path(snapshot_path).resolve()), "output_root": str(output),
        "run_id": run_id})
    result = NativeBackend(config.artifact_root).run(
        [sys.executable, "-m", "etf_ml.research.experiment_worker",
         str(output / "pipeline_request.json")],
        output, {}, config.research.limits, trusted=True)
    atomic_json(output / "execution_result.json", result)
    if result.status != "succeeded":
        _raise_failure(output, result)
    report = json.loads((output / "baseline_report.json").read_text(encoding="utf-8"))
    if report.get("execution_identity") != execution_identity:
        raise IntegrityError("Baseline worker did not attest the requested runtime identity")
    return report
