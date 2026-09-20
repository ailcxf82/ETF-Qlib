from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from etf_ml.artifacts import RunStore, environment_manifest
from etf_ml.errors import ConfigurationError, IntegrityError, QualityError
from etf_ml.models.persistence import load_bundle
from etf_ml.registry.model_versions import ModelVersionRegistry
from etf_ml.research.finalize import load_frozen_features, validate_matrix
from etf_ml.research.paired import _verify_references
from etf_ml.utils import FileLock, atomic_json, code_hash, content_hash, ensure_within, file_hash, verify_files


def load_comparison(path, config, frozen):
    path = ensure_within(Path(path), config.artifact_root / "comparisons" / "runs")
    if path.name != "comparison.json":
        raise ConfigurationError("Model freeze requires the owned comparison.json")
    run_path = path.parent
    status = json.loads((run_path / "status.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_path / "manifest.json").read_text(encoding="utf-8"))
    expected = {"freeze_id": frozen["freeze_id"], "config": config.model_dump(mode="json")}
    comparison_id = content_hash(expected)
    if (status["status"] != "completed" or status["config_hash"] != comparison_id or
            manifest["config_hash"] != comparison_id or "comparison.json" not in manifest["files"]):
        raise IntegrityError("Model comparison ownership changed or incomplete")
    verify_files(run_path, manifest["files"])
    result = json.loads(path.read_text(encoding="utf-8"))
    report = result["report"]
    if (result.get("status") != "completed" or result["comparison_id"] != comparison_id or
            result["freeze_id"] != frozen["freeze_id"] or report["protocol_id"] != comparison_id or
            report["snapshot_id"] != frozen["snapshot_id"] or report["feature_set_id"] != frozen["feature_set_id"]):
        raise IntegrityError("Model comparison does not belong to the frozen features")
    validate_matrix(report, config, cost_multipliers=frozen["protocol"]["cost_multipliers"])
    _verify_references({"comparison": report}, config.artifact_root / "comparisons", config.artifact_root / "models")
    return result


def select_model(result, *, model, fold, seed, reason):
    if not isinstance(reason, str) or not reason.strip():
        raise ConfigurationError("Model freeze requires an explicit selection reason")
    rows = [r for r in result["report"]["by_fold"] if (r["model"], r["fold"], r["seed"]) == (model, fold, seed)]
    if len(rows) != 1:
        raise ConfigurationError("Explicit model/fold/seed is absent or ambiguous in the comparison")
    return rows[0]


def validate_selected_bundle(bundle, row, frozen):
    manifest = bundle.manifest
    if (manifest["model_id"] != row["model_id"] or manifest["model_spec"] != row["model_spec"] or
            manifest["dataset_manifest"] != row["dataset"] or
            manifest["feature_set_id"] != frozen["feature_set_id"] or
            manifest["snapshot_id"] != frozen["snapshot_id"] or
            manifest["feature_names"] != frozen["columns"] or bundle.processor.columns != frozen["columns"] or
            manifest["horizon"] != frozen["protocol"]["label"]["horizon"] or
            manifest["universe_policy"] != frozen["protocol"]["universe"]):
        raise IntegrityError("Selected model weights or preprocessing do not match the frozen comparison")
    import pandas as pd
    boundary = pd.Timestamp(frozen["protocol"]["validation"]["holdout_start"])
    for role in ("train", "early_stop", "selection"):
        if pd.Timestamp(manifest["dataset_manifest"]["fold"][role]["end"]) >= boundary:
            raise QualityError("Frozen model development role includes holdout")
    if bundle.processor.fitted_index_hash != row["dataset"]["processor_fit_index_hash"]:
        raise IntegrityError("Selected model preprocessing fitted different samples")


def _remove_stage(stage):
    if stage.exists():
        for item in sorted(stage.rglob("*"), reverse=True):
            target = ensure_within(item, stage)
            if target.is_file(): target.unlink()
            elif target.is_dir(): target.rmdir()
        stage.rmdir()


def freeze_model(config, frozen_path, comparison_path, *, model, fold, seed, reason, run_id):
    features, frozen = load_frozen_features(frozen_path, config=config)
    result = load_comparison(comparison_path, config, frozen)
    row = select_model(result, model=model, fold=fold, seed=seed, reason=reason)
    model_path = ensure_within(Path(row["model_path"]), config.artifact_root / "models")
    bundle = load_bundle(model_path, trusted_root=config.artifact_root / "models")
    validate_selected_bundle(bundle, row, frozen)
    identity = {"freeze_id": frozen["freeze_id"], "comparison_id": result["comparison_id"],
                "comparison_hash": file_hash(Path(comparison_path)), "selection": {
                    "model": model, "fold": fold, "seed": seed, "reason": reason.strip()},
                "model_manifest_hash": row["model_manifest_hash"]}
    with RunStore(config.artifact_root / "model_freezes" / "runs", run_id, identity) as run:
        if run.reused:
            saved = json.loads((run.path / "frozen_model.json").read_text(encoding="utf-8"))
            load_frozen_model(saved["frozen_model_path"], config=config)
            return saved
        root = ensure_within(config.artifact_root / "frozen_models", config.artifact_root)
        stage = root / ".staging" / format(time.time_ns(), "x")
        stage.mkdir(parents=True, exist_ok=False)
        try:
            copied_model = stage / "model" / row["model_id"]
            copied_model.mkdir(parents=True)
            for name in ("manifest.json", "bundle.pkl"):
                shutil.copyfile(model_path / name, copied_model / name)
            # Preserve the complete frozen feature package, not only its column list.
            copied_features = stage / "features"
            copied_features.mkdir()
            for name in [*frozen["files"], "manifest.json", "published.json"]:
                source = ensure_within(Path(frozen_path) / name, frozen_path)
                target = ensure_within(copied_features / name, copied_features)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            comparison_manifest = Path(comparison_path).parent / "manifest.json"
            payload = {
                "schema_version": 1, "kind": "frozen_model_version",
                "model_id": row["model_id"], "model_relative_path": "model/" + row["model_id"],
                "selected_row": row, "selection": identity["selection"],
                "feature_set_id": features.feature_set_id, "feature_freeze_id": frozen["freeze_id"],
                "feature_relative_path": "features", "columns": list(features.frame.columns),
                "snapshot_id": frozen["snapshot_id"], "snapshot_path": frozen["snapshot_path"],
                "protocol": frozen["protocol"], "protocol_id": content_hash(frozen["protocol"]),
                "acceptance": frozen["acceptance"], "comparison_path": str(Path(comparison_path).resolve()),
                "comparison_hash": identity["comparison_hash"],
                "comparison_manifest_hash": file_hash(comparison_manifest),
                "config": config.model_dump(mode="json"), "environment": environment_manifest(),
                "code_hash": code_hash(), "registry_root": str((config.artifact_root / "model_versions").resolve()),
                "investment_status": "not_evaluated", "files": {},
                "note": "Explicit development model freeze; no holdout evaluation or investment promotion",
            }
            from etf_ml.utils import source_hashes
            payload["files"] = source_hashes(stage)
            version_id = content_hash(payload)
            manifest = {**payload, "version_id": version_id}
            atomic_json(stage / "manifest.json", manifest)
            destination = root / version_id
            with FileLock(root / ".locks" / (version_id + ".lock")):
                if destination.exists():
                    if json.loads((destination / "manifest.json").read_text(encoding="utf-8")) != manifest:
                        raise IntegrityError("Existing model freeze changed")
                    verify_files(destination, manifest["files"])
                else:
                    os.replace(stage, destination)
                registry = ModelVersionRegistry(payload["registry_root"])
                current = registry.register(destination)
                if current["state"] not in ("frozen", "accepted"):
                    raise QualityError("Inactive model version cannot be republished")
                atomic_json(destination / "published.json", {"version_id": version_id,
                                                            "manifest_sha256": file_hash(destination / "manifest.json")})
            saved = {"status": "completed", "version_id": version_id, "model_id": row["model_id"],
                     "frozen_model_path": str(destination), "investment_status": "not_evaluated",
                     "selection": identity["selection"]}
            atomic_json(run.path / "frozen_model.json", saved)
            run.complete(saved)
            return saved
        finally:
            _remove_stage(stage)


def load_frozen_model(path, *, config=None, allow_rejected=False):
    from etf_ml.contracts import AppConfig
    path = Path(path).resolve()
    manifest_path = path / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        publication = json.loads((path / "published.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or not isinstance(publication, dict):
            raise ValueError("Package records must be JSON objects")
    except (OSError, ValueError) as exc:
        raise IntegrityError("Frozen model manifest or publication is missing or invalid") from exc
    identity = content_hash({k: v for k, v in manifest.items() if k != "version_id"})
    if (manifest.get("schema_version") != 1 or manifest.get("kind") != "frozen_model_version" or
            identity != manifest["version_id"] or path.name != identity or
            publication != {"version_id": identity, "manifest_sha256": file_hash(manifest_path)}):
        raise IntegrityError("Frozen model publication identity changed")
    verify_files(path, manifest["files"])
    if manifest["environment"] != environment_manifest() or manifest["code_hash"] != code_hash():
        raise ConfigurationError("Frozen model runtime changed")
    frozen_config = AppConfig.model_validate(manifest["config"])
    if config is not None and config.model_dump(mode="json") != frozen_config.model_dump(mode="json"):
        raise ConfigurationError("Frozen model configuration changed")
    current = ModelVersionRegistry(manifest["registry_root"]).load(identity)
    allowed = {"frozen", "accepted", "rejected"} if allow_rejected else {"frozen", "accepted"}
    if current["state"] not in allowed or Path(current["definition"]["package_path"]).resolve() != path:
        raise QualityError("Frozen model version is inactive")
    feature_path = ensure_within(path / manifest["feature_relative_path"], path)
    features, frozen = load_frozen_features(feature_path, config=frozen_config)
    if (manifest["feature_freeze_id"] != frozen["freeze_id"] or manifest["feature_set_id"] != features.feature_set_id or
            manifest["columns"] != list(features.frame.columns) or manifest["protocol"] != frozen["protocol"] or
            manifest["protocol_id"] != content_hash(frozen["protocol"]) or manifest["acceptance"] != frozen["acceptance"]):
        raise IntegrityError("Frozen model feature or protocol binding changed")
    comparison_path = ensure_within(Path(manifest["comparison_path"]), frozen_config.artifact_root / "comparisons" / "runs")
    if (file_hash(comparison_path) != manifest["comparison_hash"] or
            file_hash(comparison_path.parent / "manifest.json") != manifest["comparison_manifest_hash"]):
        raise IntegrityError("Frozen model comparison evidence changed")
    result = load_comparison(comparison_path, frozen_config, frozen)
    row = select_model(result, **manifest["selection"])
    if row != manifest["selected_row"] or row["model_id"] != manifest["model_id"]:
        raise IntegrityError("Frozen model selection changed")
    model_path = ensure_within(path / manifest["model_relative_path"], path / "model")
    bundle = load_bundle(model_path, trusted_root=path / "model")
    validate_selected_bundle(bundle, row, frozen)
    manifest = {**manifest, "registry_state": current["state"],
                "investment_status": {"frozen": "not_evaluated", "accepted": "passed", "rejected": "failed"}[current["state"]]}
    return bundle, features, manifest