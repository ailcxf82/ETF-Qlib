from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pandas as pd

from etf_ml.contracts import FeatureArtifact, ResearchPolicy
from etf_ml.data.snapshot import load_snapshot
from etf_ml.data.source import require_panel
from etf_ml.errors import QualityError, ValidationFailure
from etf_ml.features.validators import validate_factor
from etf_ml.research.code_checks import validate_source
from etf_ml.runtime import DockerBackend
from etf_ml.utils import FileLock, atomic_json, content_hash, ensure_within, file_hash, code_hash


def _runtime_failure_message(result) -> str:
    """Project only fixed, prompt-safe worker contract failures to the coder.

    Container stderr can contain arbitrary generated-code text, so it is never
    copied into a repair prompt.  Known harness messages get an actionable,
    deterministic explanation; all other failures remain deliberately generic.
    """
    diagnostic = (str(result.stderr) + "\n" + str(result.stdout)).lower()
    known = (
        ("candidate must preserve the input index",
         "Candidate must return exactly the incoming panel.index in its original order; "
         "reindex any internally sorted result to panel.index."),
        ("candidate must return one numeric column",
         "Candidate must return a DataFrame with exactly one numeric column."),
        ("candidate output is not numeric",
         "Candidate output column must have a numeric dtype."),
    )
    for marker, message in known:
        if marker in diagnostic:
            return message
    return "Candidate execution failed in isolated validation: " + str(result.reason)


class FactorEngine:
    def __init__(self, workspace_root: Path, policy: ResearchPolicy):
        self.root = Path(workspace_root).resolve()
        self.policy = policy
        self.backend = DockerBackend(self.root)

    def materialize(self, spec, context, research_view: Path, *, eligibility=None):
        spec.validate_context(context)
        research_view = Path(research_view).resolve()
        snapshot = load_snapshot(research_view.parent)
        if research_view != snapshot.path / "research" or snapshot.snapshot_id != context.snapshot_id:
            raise QualityError("Factor input snapshot or view mismatch")
        if spec.trusted_implementation is not None:
            return self._materialize_trusted_library(spec, context, research_view, eligibility)
        try:
            validate_source(spec.source)
        except QualityError as exc:
            raise ValidationFailure(category="causal_violation", check_id="static_source_validation",
                                    message=str(exc), recoverable=False) from exc
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
                raise ValidationFailure(category="runtime", check_id="isolated_execution",
                                        message=_runtime_failure_message(result),
                                        recoverable=True) from None
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
                raise ValidationFailure(category="causal_violation", check_id="causal_checks",
                                        message="Candidate did not pass required causal checks", recoverable=False)
            factor = pd.read_parquet(output)
            panel = pd.read_parquet(research_view / "panel.parquet")
            require_panel(factor, numeric=True)
            if not factor.index.equals(panel.index):
                raise ValidationFailure(category="index_shape", check_id="output_index",
                                        message="Candidate changed research index", recoverable=True)
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

    def _materialize_trusted_library(self, spec, context, research_view, eligibility):
        import hashlib
        import numpy as np
        from pandas.testing import assert_frame_equal
        from etf_ml.features import alpha101
        from etf_ml.features.validators import check_causality, check_grouping

        if eligibility is None:
            raise QualityError("Registered cross-sectional library factors require explicit PIT eligibility")
        panel = pd.read_parquet(research_view / "panel.parquet")
        if (not eligibility.index.equals(panel.index) or eligibility.isna().any()
                or not pd.api.types.is_bool_dtype(eligibility)):
            raise QualityError("Trusted factor eligibility differs from its research panel")
        eligible = eligibility.copy()
        reference = spec.trusted_implementation
        key = content_hash({"factor": spec, "context": context, "policy": self.policy,
                            "eligibility_hash": content_hash([[str(t), i, bool(v)] for (t, i), v in eligible.items()]),
                            "code_hash": code_hash(), "library_implementation_hash": file_hash(Path(alpha101.__file__)),
                            "research_input": file_hash(research_view / "panel.parquet")})
        cache = ensure_within(self.root / "validated" / key[:32], self.root)
        manifest_path = cache / "feature_manifest.json"
        self.root.mkdir(parents=True, exist_ok=True)
        with FileLock(self.root / ".locks" / (key + ".lock")):
            if manifest_path.is_file():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest_body = {key: value for key, value in manifest.items() if key != "manifest_hash"}
                if (manifest.get("cache_key") != key or manifest.get("feature_set_id") != spec.version_id
                        or manifest.get("trusted_implementation") != reference
                        or manifest.get("source_hash") != spec.source_hash
                        or manifest.get("context_hash") != context.context_hash
                        or manifest.get("checks", {}).get("status") != "passed"
                        or manifest.get("manifest_hash") != content_hash(manifest_body)):
                    raise QualityError("Trusted library cache identity collision")
                if (file_hash(cache / "result.parquet") != manifest.get("result_hash")
                        or file_hash(cache / "result.h5") != manifest.get("hdf_hash")):
                    raise QualityError("Trusted library cache integrity failure")
                frame = pd.read_parquet(cache / "result.parquet")
                require_panel(frame, numeric=True)
                if (not frame.index.equals(panel.index)
                        or list(frame.columns) != [f"{spec.factor_id}_v{spec.version}"]):
                    raise QualityError("Trusted library cache shape or column identity changed")
                return FeatureArtifact(spec.version_id, frame, manifest)

            compute = lambda view: alpha101.materialize_library_reference(
                view, eligible.reindex(view.index), reference)
            dates = panel.index.get_level_values("datetime").unique()
            if len(dates) < 3:
                raise QualityError("Trusted library causality checks need at least three sessions")
            cutoffs = sorted({dates[len(dates) // 2], dates[max(0, len(dates) - 2)]})
            for cutoff in cutoffs:
                check_causality(compute, panel, cutoff)
            check_grouping(compute, panel, cross_sectional=True)
            factor = compute(panel)
            require_panel(factor, numeric=True)
            if not factor.index.equals(panel.index) or factor.shape[1] != 1:
                raise ValidationFailure(category="index_shape", check_id="output_index",
                                        message="Trusted library factor changed its panel index", recoverable=False)
            warm = panel.groupby(level="instrument").cumcount() + 1 >= spec.minimum_observations
            quality = validate_factor(factor[warm & eligible], panel.index[warm & eligible],
                                      coverage_threshold=self.policy.coverage_threshold)
            factor.columns = [f"{spec.factor_id}_v{spec.version}"]
            cache.mkdir(parents=True, exist_ok=True)
            result = cache / "result.parquet"
            factor.to_parquet(result)
            factor.to_hdf(cache / "result.h5", key="data", format="table")
            checks = {"status": "passed", "checks": ["truncation_invariance", "future_perturbation_invariance",
                      "multiple_cutoff_invariance", "instrument_permutation_invariance"],
                      "cutoffs": [str(date) for date in cutoffs], "execution": "trusted_controller_library"}
            manifest = {"feature_set_id": spec.version_id, "cache_key": key,
                        "snapshot_id": context.snapshot_id, "protocol_id": context.protocol_id,
                        "source_hash": spec.source_hash, "context_hash": context.context_hash,
                        "research_group": spec.research_group, "minimum_observations": spec.minimum_observations,
                        "lookback": spec.lookback, "direction": spec.direction,
                        "applicable_scope": spec.applicable_scope, "result_hash": file_hash(result),
                        "hdf_hash": file_hash(cache / "result.h5"), "quality": quality, "checks": checks,
                        "trusted_implementation": reference,
                        "path": str(cache), "execution_workspace": str(research_view),
                        "execution_seconds": None}
            manifest["manifest_hash"] = content_hash(manifest)
            atomic_json(manifest_path, manifest)
            return FeatureArtifact(spec.version_id, factor, manifest)
