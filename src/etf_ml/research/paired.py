from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd
from pandas.testing import assert_frame_equal

from etf_ml.artifacts import RunStore
from etf_ml.contracts import AppConfig, FeatureArtifact, ModelSpec
from etf_ml.data.snapshot import load_snapshot
from etf_ml.data.source import require_panel
from etf_ml.errors import ConfigurationError, QualityError
from etf_ml.features.baseline import materialize
from etf_ml.pipeline import run_baseline
from etf_ml.research.protocol import ComparisonProtocol
from etf_ml.research.selection import compare
from etf_ml.research.statistics import paired_block_uncertainty
from etf_ml.utils import atomic_json, content_hash, file_hash, ensure_within, verify_files
from etf_ml.errors import IntegrityError


def combine_features(baseline: FeatureArtifact, factor: FeatureArtifact,
                     protocol: ComparisonProtocol) -> FeatureArtifact:
    """Append a controller-validated factor without dropping any target rows."""
    require_panel(factor.frame, numeric=True)
    if baseline.feature_set_id != protocol.baseline_feature_set_id:
        raise QualityError("Baseline definition differs from the frozen protocol")
    if (factor.manifest.get("snapshot_id") != protocol.snapshot_id or
            factor.manifest.get("protocol_id") != protocol.protocol_id):
        raise QualityError("Factor snapshot or protocol mismatch")
    if not baseline.frame.index.equals(factor.frame.index) or factor.frame.shape[1] != 1:
        raise QualityError("Candidate must preserve all baseline rows and add one column")
    if set(baseline.frame.columns) & set(factor.frame.columns):
        raise QualityError("Candidate overwrites an existing feature")
    if (factor.manifest.get("checks", {}).get("status") != "passed" or
            factor.manifest.get("quality", {}).get("coverage", 0) < protocol.research.coverage_threshold):
        raise QualityError("Candidate has not passed frozen quality gates")
    cached_result = Path(factor.manifest["path"]) / "result.parquet"
    cached_manifest = cached_result.with_name("feature_manifest.json")
    if (json.loads(cached_manifest.read_text(encoding="utf-8")) != factor.manifest or
            factor.manifest.get("feature_set_id") != factor.feature_set_id):
        raise QualityError("Candidate manifest differs from its validated cache")
    if file_hash(cached_result) != factor.manifest["result_hash"]:
        raise QualityError("Validated candidate artifact changed")
    try:
        assert_frame_equal(pd.read_parquet(cached_result), factor.frame, check_exact=True)
    except AssertionError as exc:
        raise QualityError("Candidate frame differs from its validated cache") from exc
    frame = pd.concat([baseline.frame, factor.frame], axis=1)
    manifest = {
        "snapshot_id": protocol.snapshot_id, "protocol_id": protocol.protocol_id,
        "baseline_feature_set_id": baseline.feature_set_id,
        "factor_version_ids": [factor.feature_set_id],
        "factor_result_hashes": [factor.manifest["result_hash"]],
        "columns": list(frame.columns), "baseline_manifest": baseline.manifest,
    }
    return FeatureArtifact(content_hash(manifest), frame, manifest)


def _seed_model(model: ModelSpec, seed: int) -> ModelSpec:
    constructor = dict(model.constructor)
    for key in ("seed", "random_state", "random_seed"):
        if key in constructor:
            if constructor[key] != model.seed:
                raise ConfigurationError("Frozen model has conflicting seeds")
            constructor[key] = seed
    return ModelSpec(name=model.name, seed=seed, constructor=constructor, fit=dict(model.fit))


def _verify_references(reports: dict, output_root: Path, model_root: Path):
    for report in reports.values():
        for child in report["child_runs"]:
            path = ensure_within(Path(child["path"]), output_root / "experiments")
            if file_hash(path / "manifest.json") != child["manifest_hash"]:
                raise IntegrityError("Referenced experiment manifest changed")
            manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
            verify_files(path, manifest["files"])
        for row in report["by_fold"]:
            path = ensure_within(Path(row["model_path"]), model_root)
            manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
            if (file_hash(path / "manifest.json") != row["model_manifest_hash"] or
                    manifest["model_id"] != row["model_id"] or
                    file_hash(path / "bundle.pkl") != manifest["bundle_sha256"]):
                raise IntegrityError("Referenced model bundle changed")


def run_paired(config: AppConfig, snapshot_path: Path, factor: FeatureArtifact,
               protocol: ComparisonProtocol, output_root: Path, *, run_id: str,
               perform_ablation: bool = True, attempted_trials: int = 1,
               baseline_override: FeatureArtifact | None = None) -> dict:
    """Run actual fixed-model baseline/candidate/ablation Qlib experiments.

    Baseline caches are common across candidates and verify every committed file.
    Candidate caches also bind the validated factor data hash. All seed and fold
    results are retained; there is no best-seed selection or holdout access.
    """
    protocol.require_runtime()
    snapshot = load_snapshot(snapshot_path)
    if snapshot.snapshot_id != protocol.snapshot_id:
        raise ConfigurationError("Comparison snapshot changed")
    frozen = {
        "label": protocol.label, "universe": protocol.universe,
        "validation": protocol.validation, "portfolio": protocol.portfolio,
        "research": protocol.research, "benchmarks": protocol.benchmarks,
    }
    for key, value in frozen.items():
        if getattr(config, key).model_dump(mode="json") != value.model_dump(mode="json"):
            raise ConfigurationError("Application configuration differs from frozen " + key)
    panel = pd.read_parquet(snapshot.path / "research" / "panel.parquet")
    baseline = baseline_override or materialize({"snapshot_id": snapshot.snapshot_id}, panel)
    if baseline_override is not None:
        from etf_ml.research.feature_sets import FeatureSetStore
        from etf_ml.registry import FactorRegistry
        store_path = Path(baseline.manifest["path"]).parent
        FeatureSetStore(store_path, FactorRegistry(store_path.parent / "registry")).verify_baseline(baseline, panel)
    candidate = combine_features(baseline, factor, protocol)
    from etf_ml.research.feature_sets import remove_candidate_group, group_ablation_evaluation
    group_removed, group_name, removed_columns = remove_candidate_group(baseline, candidate, factor, protocol)
    output_root = Path(output_root).resolve()
    identity = {"protocol": protocol.model_dump(mode="json"),
                "candidate_feature_set_id": candidate.feature_set_id,
                "perform_ablation": perform_ablation, "attempted_trials": attempted_trials,
                "group_ablation_feature_set_id": group_removed.feature_set_id,
                "group_name": group_name, "removed_columns": removed_columns}
    with RunStore(output_root / "runs", run_id, identity) as overall:
        if overall.reused:
            result = json.loads((overall.path / "paired_report.json").read_text(encoding="utf-8"))
            reports = {kind: json.loads((overall.path / (kind + "_report.json")).read_text(encoding="utf-8"))
                       for kind in result["reports"]}
            _verify_references(reports, output_root, config.artifact_root / "models")
            return result
        atomic_json(overall.path / "protocol.json", {
            **protocol.model_dump(mode="json"), "protocol_id": protocol.protocol_id})
        atomic_json(overall.path / "candidate_manifest.json", candidate.manifest)
        reports = {}
        kinds = [("baseline", baseline), ("candidate", candidate)]
        if perform_ablation:
            # Independent re-fit after removing the sole new factor.
            kinds.append(("ablation", baseline))
            if group_removed.feature_set_id != baseline.feature_set_id:
                kinds.append(("group_ablation", group_removed))
        for kind, features in kinds:
            rows, child_runs = [], []
            for seed in protocol.research.seeds:
                child_config = config.model_copy(deep=True)
                child_config.models = [_seed_model(protocol.model, seed)]
                experiment_identity = {
                    "protocol_id": protocol.protocol_id, "seed": seed,
                    "feature_set_id": features.feature_set_id, "kind": kind,
                    "candidate_id": candidate.feature_set_id if kind == "ablation" else None,
                }
                experiment_id = kind + "-" + content_hash(experiment_identity)[:28]
                with RunStore(output_root / "experiments", experiment_id, experiment_identity) as child:
                    if child.reused:
                        report = json.loads((child.path / "baseline_report.json").read_text(encoding="utf-8"))
                    else:
                        report = run_baseline(child_config, snapshot.path, child.path,
                                              run_id=experiment_id, feature_override=features,
                                              protocol_id=protocol.protocol_id,
                                              cost_multipliers=tuple(protocol.cost_multipliers))
                        child.complete(report)
                    rows.extend(report["by_fold"])
                    child_runs.append({"run_id": experiment_id, "path": str(child.path),
                                       "manifest_hash": file_hash(child.path / "manifest.json")})
            reports[kind] = {
                "status": "completed", "protocol_id": protocol.protocol_id,
                "snapshot_id": snapshot.snapshot_id, "feature_set_id": features.feature_set_id,
                "baseline_feature_set_id": features.manifest.get("baseline_feature_set_id"),
                "by_fold": rows, "child_runs": child_runs,
            }
            atomic_json(overall.path / (kind + "_report.json"), reports[kind])
        _verify_references(reports, output_root, config.artifact_root / "models")
        evaluation = compare(reports["baseline"], reports["candidate"], protocol,
                             run_id=run_id, ablation=reports.get("ablation"))
        group_evaluation = {"status": "inconclusive", "reasons": ["group_ablation_evidence_missing"], "paired_deltas": []}
        if perform_ablation:
            group_evaluation = group_ablation_evaluation(
                reports["candidate"], reports.get("group_ablation", reports["ablation"]), protocol)
            if evaluation.status == "accepted" and group_evaluation["status"] != "accepted":
                evaluation.status = "failed" if group_evaluation["status"] == "failed" else "rejected"
                evaluation.reasons = group_evaluation["reasons"]
        group_evaluation.update({"group": group_name, "removed_columns": removed_columns,
                                 "removed_feature_set_id": group_removed.feature_set_id})
        atomic_json(overall.path / "group_ablation.json", group_evaluation)
        uncertainty = []
        # Child order is the frozen seed order, each child includes every fold.
        for seed_index, seed in enumerate(protocol.research.seeds):
            bpath = Path(reports["baseline"]["child_runs"][seed_index]["path"])
            cpath = Path(reports["candidate"]["child_runs"][seed_index]["path"])
            for fold in protocol.validation.folds:
                relative = Path(fold.name) / protocol.model.name / "daily_returns.parquet"
                breturns = pd.read_parquet(bpath / relative).iloc[:, 0]
                creturns = pd.read_parquet(cpath / relative).iloc[:, 0]
                uncertainty.append({"fold": fold.name, "seed": seed,
                    **paired_block_uncertainty(breturns, creturns,
                        block_length=protocol.time_block_length,
                        repetitions=protocol.bootstrap_repetitions,
                        seed=protocol.bootstrap_seed, attempted_trials=attempted_trials)})
        atomic_json(overall.path / "time_block_statistics.json", uncertainty)
        from etf_ml.data.calendar import read_calendar
        from etf_ml.research.diagnostics import development_diagnostics
        calendar = read_calendar(snapshot.path / "calendar.txt")
        calendar = calendar[calendar < pd.Timestamp(protocol.validation.holdout_start)]
        universe = pd.read_parquet(snapshot.path / "universe.parquet").reindex(panel.index)
        diagnostics = development_diagnostics(panel, calendar, universe, baseline, factor, protocol, reports)
        atomic_json(overall.path / "development_diagnostics.json", diagnostics)
        result = {
            "status": evaluation.status, "protocol_id": protocol.protocol_id,
            "factor_version_id": factor.feature_set_id,
            "evaluation": asdict(evaluation), "time_block_statistics": uncertainty,
            "development_diagnostics": diagnostics,
            "group_ablation": group_evaluation,
            "reports": {kind: str(overall.path / (kind + "_report.json")) for kind in reports},
            "note": "Development selection only; independent final acceptance is separate",
        }
        atomic_json(overall.path / "evaluation.json", {
            **asdict(evaluation), "protocol_id": protocol.protocol_id,
            "factor_version_id": factor.feature_set_id})
        atomic_json(overall.path / "paired_report.json", result)
        overall.complete(result)
        return result
