from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pandas as pd

from etf_ml.contracts import FeatureArtifact, ResearchPolicy
from etf_ml.data.snapshot import load_snapshot
from etf_ml.data.source import require_panel
from etf_ml.errors import QualityError
from etf_ml.features.validators import validate_factor
from etf_ml.research.code_checks import validate_source
from etf_ml.runtime import DockerBackend
from etf_ml.utils import FileLock, atomic_json, content_hash, ensure_within, file_hash, code_hash


class FactorEngine:
    def __init__(self, workspace_root: Path, policy: ResearchPolicy):
        self.root = Path(workspace_root).resolve()
        self.policy = policy
        self.backend = DockerBackend(self.root)

    def materialize(self, spec, context, research_view: Path, *, eligibility=None):
        spec.validate_context(context)
        validate_source(spec.source)
        research_view = Path(research_view).resolve()
        snapshot = load_snapshot(research_view.parent)
        if research_view != snapshot.path / "research" or snapshot.snapshot_id != context.snapshot_id:
            raise QualityError("Factor input snapshot or view mismatch")
        eligibility_hash = None
        if eligibility is not None:
            require_panel(eligibility)
            if eligibility.isna().any() or not pd.api.types.is_bool_dtype(eligibility):
                raise QualityError("Eligibility must be boolean and complete")
            eligibility_hash = content_hash([[str(t), i, bool(v)]
                                             for (t, i), v in eligibility.items()])
        # Key includes data, pool/protocol, source, missing rules, limits and image.
        key = content_hash({"factor": spec, "context": context,
                            "policy": self.policy, "eligibility_hash": eligibility_hash,
                            "code_hash": code_hash(), "research_input": file_hash(research_view / "panel.parquet")})
        workspace = ensure_within(self.root / "jobs" / key[:32], self.root)
        cache = ensure_within(self.root / "validated" / key[:32], self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        with FileLock(self.root / ".locks" / (key + ".lock")):
            # Controller-owned cache is outside every writable container mount.
            manifest_path = cache / "feature_manifest.json"
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest["cache_key"] != key or manifest["feature_set_id"] != spec.version_id:
                    raise QualityError("Factor cache identity collision")
                if (file_hash(cache / "result.parquet") != manifest["result_hash"] or
                        file_hash(cache / "result.h5") != manifest["hdf_hash"]):
                    raise QualityError("Factor cache integrity failure")
                return FeatureArtifact(manifest["feature_set_id"],
                                       pd.read_parquet(cache / "result.parquet"), manifest)
            workspace.mkdir(parents=True, exist_ok=True)
            (workspace / "factor.py").write_text(spec.source, encoding="utf-8")
            shutil.copyfile(Path(__file__).with_name("factor_worker.py"), workspace / "worker.py")
            atomic_json(workspace / "factor_spec.json", spec)
            result = self.backend.run(
                ["python", "worker.py", "/research/panel.parquet",
                 "cross_sectional" if spec.cross_sectional else "temporal"],
                workspace, research_view, self.policy.limits)
            atomic_json(workspace / "execution_result.json", result)
            if result.status != "succeeded":
                raise QualityError("Candidate execution failed: " + str(result.reason))
            output = ensure_within(workspace / "result.parquet", workspace)
            checks_path = ensure_within(workspace / "checks.json", workspace)
            hdf_path = ensure_within(workspace / "result.h5", workspace)
            if not output.is_file() or not checks_path.is_file() or not hdf_path.is_file():
                raise QualityError("Candidate did not publish required outputs")
            checks = json.loads(checks_path.read_text(encoding="utf-8"))
            required_checks = {"truncation_invariance", "future_perturbation_invariance",
                               "multiple_cutoff_invariance", "instrument_permutation_invariance"}
            if not spec.cross_sectional:
                required_checks |= {"instrument_independence", "instrument_addition_invariance"}
            if checks.get("status") != "passed" or not required_checks.issubset(checks.get("checks", [])):
                raise QualityError("Candidate did not pass required causal checks")
            factor = pd.read_parquet(output)
            panel = pd.read_parquet(research_view / "panel.parquet")
            require_panel(factor, numeric=True)
            if not factor.index.equals(panel.index):
                raise QualityError("Candidate changed research index")
            warm = panel.groupby(level="instrument").cumcount() + 1 >= spec.minimum_observations
            if eligibility is not None:
                if not eligibility.index.equals(panel.index) or eligibility.isna().any():
                    raise QualityError("Candidate eligibility differs from fixed target")
                warm &= eligibility
            quality = validate_factor(factor[warm], panel.index[warm],
                                      coverage_threshold=self.policy.coverage_threshold)
            factor.columns = [f"{spec.factor_id}_v{spec.version}"]
            # Publish canonical column names so cache and initial run return the same schema.
            cache.mkdir(parents=True, exist_ok=True)
            output = cache / "result.parquet"
            factor.to_parquet(output)
            factor.to_hdf(cache / "result.h5", key="data", format="table")
            manifest = {"feature_set_id": spec.version_id, "cache_key": key,
                        "snapshot_id": context.snapshot_id, "protocol_id": context.protocol_id,
                        "source_hash": spec.source_hash, "context_hash": context.context_hash,
                        "research_group": spec.research_group,
                        "minimum_observations": spec.minimum_observations,
                        "lookback": spec.lookback, "direction": spec.direction,
                        "applicable_scope": spec.applicable_scope,
                        "result_hash": file_hash(output), "hdf_hash": file_hash(cache / "result.h5"), "quality": quality,
                        "checks": checks,
                        "path": str(cache), "execution_workspace": str(workspace), "execution_seconds": result.duration_seconds}
            atomic_json(manifest_path, manifest)
            return FeatureArtifact(spec.version_id, factor, manifest)
