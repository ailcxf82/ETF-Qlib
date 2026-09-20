from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd
from pandas.testing import assert_frame_equal

from etf_ml.data.snapshot import load_snapshot
from etf_ml.errors import ConfigurationError, IntegrityError, QualityError
from etf_ml.features.baseline import materialize
from etf_ml.features.validators import validate_factor
from etf_ml.research.context import FactorSpec
from etf_ml.runtime.docker import DockerBackend
from etf_ml.utils import atomic_json, content_hash, ensure_within, file_hash


class FrozenFactorBackend(DockerBackend):
    """Private inference backend accepts only registered frozen factor source."""
    def __init__(self, root, package_path, view, snapshot_path):
        super().__init__(root)
        from etf_ml.models.deployment import load_frozen_model
        _, _, self.package = load_frozen_model(package_path, allow_rejected=True)
        self.package_path = Path(package_path).resolve()
        self.snapshot = load_snapshot(snapshot_path)
        self.view = ensure_within(Path(view), self.workspace_root / "inputs")
        self.feature_manifest = json.loads((self.package_path / "features" / "manifest.json").read_text(encoding="utf-8"))

    def command(self, argv, workspace, research_view, limits, *, container_name):
        view = Path(research_view).resolve()
        marker = json.loads((self.view / "input_manifest.json").read_text(encoding="utf-8"))
        if (view != self.view or marker.get("purpose") not in {"independent_holdout", "daily_inference"} or
                marker.get("version_id") != self.package["version_id"] or
                marker.get("snapshot_id") != self.snapshot.snapshot_id or
                marker.get("panel_sha256") != file_hash(self.view / "panel.parquet") or
                marker["panel_sha256"] != file_hash(self.snapshot.path / "panel.parquet")):
            raise IntegrityError("Frozen inference input is not the verified snapshot panel")
        workspace = ensure_within(Path(workspace), self.workspace_root)
        spec = FactorSpec.model_validate_json((workspace / "factor_spec.json").read_text(encoding="utf-8"))
        entries = self.feature_manifest["factors"]
        if (not any(e["factor_version_id"] == spec.version_id and e["spec"] == spec.model_dump(mode="json") for e in entries) or
                (workspace / "factor.py").read_text(encoding="utf-8") != spec.source):
            raise QualityError("Inference may execute only the exact registered frozen factor")
        harness = Path(__file__).parents[1] / "research" / "factor_worker.py"
        if file_hash(workspace / "worker.py") != file_hash(harness):
            raise IntegrityError("Frozen inference harness changed")
        if limits.model_dump(mode="json") != self.package["config"]["research"]["limits"]:
            raise ConfigurationError("Frozen inference resource/image policy changed")
        expected_argv = ["python", "worker.py", "/research/panel.parquet",
                         "cross_sectional" if spec.cross_sectional else "temporal"]
        if argv != expected_argv:
            raise ConfigurationError("Frozen inference requires the approved harness command")
        original_view = self.snapshot.path / "research"
        command = super().command(argv, workspace, original_view, limits, container_name=container_name)
        old_mount = f"type=bind,source={original_view},target=/research,readonly"
        command[command.index(old_mount)] = f"type=bind,source={self.view},target=/research,readonly"
        return command


def materialize_frozen_features(package_path, reference, package, snapshot_path, output, *, purpose):
    snapshot = load_snapshot(snapshot_path)
    output = Path(output)
    panel = pd.read_parquet(snapshot.path / "panel.parquet")
    policy = package["config"]["research"]
    if snapshot.manifest["universe_policy"] != package["protocol"]["universe"]:
        raise ConfigurationError("Inference snapshot universe policy changed")
    from etf_ml.contracts import ResearchPolicy
    policy = ResearchPolicy.model_validate(policy)
    definition = reference.manifest.get("composition", {}).get("base_manifest", reference.manifest)
    features = materialize(definition["spec"], panel).frame
    entries = reference.manifest.get("composition", {}).get("factors", [])
    marker = {"purpose": purpose, "version_id": package["version_id"], "snapshot_id": snapshot.snapshot_id,
              "panel_sha256": file_hash(snapshot.path / "panel.parquet")}
    view = output / "inputs" / "panel"
    view.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(snapshot.path / "panel.parquet", view / "panel.parquet")
    atomic_json(view / "input_manifest.json", marker)
    factor_evidence = []
    backend = FrozenFactorBackend(output, package_path, view, snapshot_path) if entries else None
    from etf_ml.research.code_checks import validate_source
    for entry in entries:
        spec = FactorSpec.model_validate(entry["spec"])
        validate_source(spec.source)
        if not set(spec.required_fields).issubset(panel.columns):
            raise QualityError("Frozen factor input fields missing")
        workspace = output / "factors" / spec.version_id
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "factor.py").write_text(spec.source, encoding="utf-8")
        shutil.copyfile(Path(__file__).parents[1] / "research" / "factor_worker.py", workspace / "worker.py")
        atomic_json(workspace / "factor_spec.json", spec)
        execution = backend.run(["python", "worker.py", "/research/panel.parquet",
                                  "cross_sectional" if spec.cross_sectional else "temporal"],
                                 workspace, view, policy.limits)
        atomic_json(workspace / "execution_result.json", execution)
        if execution.status != "succeeded":
            raise QualityError("Frozen factor execution failed: " + str(execution.reason))
        factor = pd.read_parquet(ensure_within(workspace / "result.parquet", workspace))
        checks = json.loads((workspace / "checks.json").read_text(encoding="utf-8"))
        required = {"truncation_invariance", "future_perturbation_invariance", "multiple_cutoff_invariance",
                    "instrument_permutation_invariance"}
        if not spec.cross_sectional:
            required |= {"instrument_independence", "instrument_addition_invariance"}
        if checks.get("status") != "passed" or not required.issubset(checks.get("checks", [])):
            raise QualityError("Frozen factor causal checks failed")
        warm = panel.groupby(level="instrument").cumcount() + 1 >= spec.minimum_observations
        eligibility = pd.read_parquet(snapshot.path / "universe.parquet").eligible.reindex(panel.index)
        quality = validate_factor(factor[warm & eligibility], panel.index[warm & eligibility],
                                  coverage_threshold=policy.coverage_threshold)
        if not factor.index.equals(panel.index) or factor.shape[1] != 1:
            raise QualityError("Frozen factor changed inference target rows")
        features[entry["column"]] = factor.iloc[:, 0]
        factor_evidence.append({"factor_version_id": spec.version_id, "checks": checks, "quality": quality,
                                "result_sha256": file_hash(workspace / "result.parquet")})
    if list(features.columns) != package["columns"] or not reference.frame.index.isin(features.index).all():
        raise IntegrityError("Frozen inference columns or historical rows changed")
    try:
        assert_frame_equal(features.loc[reference.frame.index], reference.frame, check_exact=False, rtol=1e-10, atol=1e-10)
    except AssertionError as exc:
        raise IntegrityError("Frozen inference no longer reproduces development features") from exc
    features.to_parquet(output / "features.parquet")
    evidence = {**marker, "feature_set_id": reference.feature_set_id, "columns": list(features.columns),
                "features_sha256": file_hash(output / "features.parquet"), "factors": factor_evidence,
                "historical_feature_parity": "passed"}
    atomic_json(output / "inference_manifest.json", evidence)
    return features, panel, snapshot, evidence