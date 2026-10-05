from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path

import pandas as pd

from etf_ml.artifacts import RunStore, environment_manifest
from etf_ml.contracts import FeatureArtifact
from etf_ml.errors import ConfigurationError, IntegrityError, QualityError
from etf_ml.research.execution import execute_baseline
from etf_ml.research.paired import _seed_model, _verify_references
from etf_ml.research.feature_sets import group_ablation_evaluation
from etf_ml.utils import (
    FileLock, atomic_json, code_hash, content_hash, ensure_within, file_hash, verify_files,
)


def run_matrix(config, snapshot_path, features, protocol_id, output, *, purpose, models, cost_multipliers=(2.,)):
    """Retain every declared model/seed/fold; each child is an owned native run."""
    rows, children = [], []
    output = Path(output)
    for seed in config.research.seeds:
        child_config = config.model_copy(deep=True)
        child_config.models = [_seed_model(model, seed) for model in models]
        identity = {"protocol_id": protocol_id, "feature_set_id": features.feature_set_id,
                    "models": [m.model_dump(mode="json") for m in child_config.models],
                    "purpose": purpose, "config_hash": child_config.config_hash,
                    "cost_multipliers": list(cost_multipliers)}
        run_id = "matrix-" + content_hash(identity)[:28]
        with RunStore(output / "experiments", run_id, identity) as child:
            if child.reused:
                report = json.loads((child.path / "baseline_report.json").read_text(encoding="utf-8"))
            else:
                report = execute_baseline(child_config, snapshot_path, child.path,
                                          run_id=run_id, feature_override=features,
                                          protocol_id=protocol_id,
                                          cost_multipliers=tuple(cost_multipliers))
                child.complete(report)
            if (report.get("status") != "completed" or report.get("protocol_id") != protocol_id or
                    report.get("snapshot_id") != features.manifest["snapshot_id"] or
                    report.get("feature_set_id") != features.feature_set_id):
                raise IntegrityError("Finalization child report identity changed")
            rows.extend(report["by_fold"])
            children.append({"run_id": run_id, "path": str(child.path),
                             "manifest_hash": file_hash(child.path / "manifest.json")})
    result = {"status": "completed", "protocol_id": protocol_id,
              "snapshot_id": features.manifest["snapshot_id"], "feature_set_id": features.feature_set_id,
              "by_fold": rows, "child_runs": children}
    _verify_references({"matrix": result}, output, config.artifact_root / "models")
    return result


def frozen_models(config, primary):
    names = [m.name for m in config.models]
    if len(set(names)) != len(names) or set(names) != {"ridge", "lightgbm", "xgboost"}:
        raise ConfigurationError("Final freeze requires one Ridge, LightGBM and XGBoost specification")
    if next(m for m in config.models if m.name == "lightgbm") != primary:
        raise ConfigurationError("Final freeze cannot change the research LightGBM specification")
    if len(config.research.seeds) < 2 or len(set(config.research.seeds)) != len(config.research.seeds):
        raise ConfigurationError("Final comparison requires at least two distinct declared seeds")
    return config.models


def remove_final_group(features, group):
    composition = features.manifest.get("composition", {})
    columns = [e["column"] for e in composition.get("factors", []) if e["group"] == group]
    if not columns:
        raise QualityError("Final group removal must remove declared candidate features")
    frame = features.frame.drop(columns=columns)
    if list(frame.columns) == composition["base_columns"]:
        return FeatureArtifact(composition["base_feature_set_id"], frame, composition["base_manifest"]), columns
    manifest = {"snapshot_id": features.manifest["snapshot_id"],
                "source_feature_set_id": features.feature_set_id, "source_manifest": features.manifest,
                "group_removed": group, "removed_columns": columns, "columns": list(frame.columns)}
    return FeatureArtifact(content_hash(manifest), frame, manifest), columns


def review_constraints(report, protocol):
    from etf_ml.research.selection import _validate_report
    try:
        rows = _validate_report(report, protocol)
    except (ValueError, KeyError, TypeError) as exc:
        raise QualityError("Final feature review evidence invalid: " + str(exc)) from exc
    violations = []
    policy = protocol.portfolio
    single = min(policy.max_weight, policy.k) if policy.k_mode == "weight_cap" else policy.max_weight
    risk_key = "max_drawdown" if policy.risk_mode == "max_drawdown" else "annualized_volatility"
    risk_limit = policy.max_drawdown_limit if policy.risk_mode == "max_drawdown" else policy.risk
    for row in rows.values():
        for scenario, metrics in [("base", row["portfolio"]), *row["cost_stress"].items()]:
            if metrics[risk_key] > risk_limit + 1e-12:
                violations.append("final_review_risk_limit")
            if metrics["max_single_weight"] > single + 1e-8:
                violations.append("final_review_single_weight_limit")
            if metrics["max_group_weight"] > policy.max_group_weight + 1e-8:
                violations.append("final_review_group_weight_limit")
            if metrics["max_unclassified_weight"] > 1e-12:
                violations.append("final_review_unclassified_holding")
            if scenario != "base" and metrics["excess_return"] < protocol.stress_min_excess_return:
                violations.append("final_review_cost_pressure_return_limit")
    return sorted(set(violations))


def _freeze_members(session, freeze_id, manifest):
    for entry in manifest["factors"]:
        spec = entry["spec"]
        current = session.registry.load(spec["factor_id"], spec["version"])
        if current["state"] == "candidate":
            proof = session.root / "freeze_proofs" / (freeze_id[:16] + "-" + entry["factor_version_id"][:16] + ".json")
            atomic_json(proof, {"status": "frozen", "freeze_id": freeze_id,
                               "factor_version_id": entry["factor_version_id"],
                               "factor_version_ids": manifest["factor_version_ids"]})
            session.registry.transition(spec["factor_id"], spec["version"], "frozen",
                                        evidence={"feature_set": proof})
        elif current["state"] != "frozen":
            raise QualityError("Final feature freeze contains an inactive candidate")


def freeze_features(session, output, *, run_id):
    """Review the final full set and every added group, then publish a freeze."""
    config, protocol, features = session.config, session.protocol, session.baseline
    from etf_ml.data.diagnostic import require_formal
    require_formal(config, session.snapshot)
    protocol.require_runtime()
    config.acceptance.require_resolved()
    frozen_models(config, protocol.model)
    if protocol.stress_min_excess_return is None:
        raise ConfigurationError("Final feature review requires a frozen development cost-pressure threshold")
    output = ensure_within(output, config.artifact_root)
    identity = {"protocol": protocol.model_dump(mode="json"),
                "feature_set_id": features.feature_set_id,
                "models": [m.model_dump(mode="json") for m in config.models],
                "acceptance": config.acceptance.model_dump(mode="json")}
    with RunStore(output / "runs", run_id, identity) as run:
        if run.reused:
            result = json.loads((run.path / "final_review.json").read_text(encoding="utf-8"))
            reports = {name: json.loads(Path(path).read_text(encoding="utf-8"))
                       for name, path in result["reports"].items()}
            _verify_references(reports, output, config.artifact_root / "models")
        else:
            full = run_matrix(config, session.snapshot.path, features, protocol.protocol_id,
                              output, purpose="final-full", models=[protocol.model], cost_multipliers=protocol.cost_multipliers)
            reports = {"full": full}
            groups = sorted({e["group"] for e in features.manifest.get("composition", {}).get("factors", [])})
            evaluations = {}
            for group in groups:
                removed, columns = remove_final_group(features, group)
                report = run_matrix(config, session.snapshot.path, removed, protocol.protocol_id,
                                    output, purpose="final-without-" + group, models=[protocol.model],
                                    cost_multipliers=protocol.cost_multipliers)
                reports["without_" + group] = report
                evaluations[group] = {**group_ablation_evaluation(full, report, protocol),
                                      "removed_columns": columns,
                                      "removed_feature_set_id": removed.feature_set_id}
            violations = review_constraints(full, protocol)
            status = "failed" if any(e["status"] == "failed" for e in evaluations.values()) else (
                "rejected" if violations or any(e["status"] != "accepted" for e in evaluations.values()) else "accepted")
            paths = {}
            for name, report in reports.items():
                path = run.path / (name + "_report.json")
                atomic_json(path, report)
                paths[name] = str(path)
            result = {"status": status, "protocol_id": protocol.protocol_id,
                      "feature_set_id": features.feature_set_id, "groups": evaluations,
                      "reasons": violations + [g + ":" + ",".join(e["reasons"]) for g, e in evaluations.items()
                                               if e["status"] != "accepted"],
                      "reports": paths, "review_manifest": str(run.path / "manifest.json")}
            atomic_json(run.path / "final_review.json", result)
            run.complete(result)
    if result["status"] != "accepted":
        return {**result, "freeze_path": None}
    stage = ensure_within(config.artifact_root / "frozen_features" / ".staging" / format(time.time_ns(), "x"),
                          config.artifact_root)
    stage.mkdir(parents=True, exist_ok=False)
    try:
        features.frame.to_parquet(stage / "features.parquet")
        trial_files = {}
        # Copy committed JSON history, preserving failures and attempted trials.
        for source in sorted((session.root / "sessions").glob("*/*.json")):
            relative = "trials/" + source.relative_to(session.root / "sessions").as_posix()
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            trial_files[relative] = file_hash(target)
        entries = features.manifest.get("composition", {}).get("factors", [])
        for entry in entries:
            spec = entry["spec"]
            relative = "sources/" + entry["column"] + ".py"
            target = stage / relative
            target.parent.mkdir(exist_ok=True)
            target.write_text(spec["source"], encoding="utf-8")
            trial_files[relative] = file_hash(target)
        files = {"features.parquet": file_hash(stage / "features.parquet"), **trial_files}
        payload = {
            "schema_version": 1, "kind": "frozen_research_features",
            "feature_set_id": features.feature_set_id, "feature_manifest": features.manifest,
            "snapshot_id": session.snapshot.snapshot_id, "snapshot_path": str(session.snapshot.path),
            "snapshot_manifest_hash": file_hash(session.snapshot.path / "snapshot_manifest.json"),
            "protocol": protocol.model_dump(mode="json"),
            "models": [m.model_dump(mode="json") for m in config.models],
            "acceptance": config.acceptance.model_dump(mode="json"),
            "factors": entries, "factor_version_ids": [e["factor_version_id"] for e in entries],
            "columns": list(features.frame.columns), "environment": environment_manifest(),
            "source_code_hash": code_hash(), "review": result,
            "review_manifest_hash": file_hash(Path(result["review_manifest"])),
            "registry_root": str(session.registry.root), "artifact_root": str(config.artifact_root.resolve()), "files": files,
            "note": "Research feature freeze only; independent final investment acceptance is separate",
        }
        freeze_id = content_hash(payload)
        destination = ensure_within(config.artifact_root / "frozen_features" / freeze_id[:32], config.artifact_root)
        manifest = {**payload, "freeze_id": freeze_id}
        atomic_json(stage / "manifest.json", manifest)
        with FileLock(config.artifact_root / "frozen_features" / ".locks" / (freeze_id + ".lock")):
            if destination.exists():
                existing = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
                if existing != manifest:
                    raise IntegrityError("Existing feature freeze changed")
                verify_files(destination, files)
            else:
                os.replace(stage, destination)
            _freeze_members(session, freeze_id, manifest)
            atomic_json(destination / "published.json", {"freeze_id": freeze_id,
                                                       "manifest_hash": file_hash(destination / "manifest.json")})
        return {**result, "freeze_id": freeze_id, "freeze_path": str(destination)}
    finally:
        # Only the explicitly enumerated files from this stage may be removed.
        if stage.exists():
            for file in sorted(stage.rglob("*"), reverse=True):
                checked = ensure_within(file, stage)
                if checked.is_file():
                    checked.unlink()
                elif checked.is_dir():
                    checked.rmdir()
            stage.rmdir()


def load_frozen_features(path, *, config=None, snapshot=None):
    from etf_ml.data.snapshot import load_snapshot
    from etf_ml.registry import FactorRegistry
    from etf_ml.research.protocol import ComparisonProtocol
    path = Path(path).resolve()
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    published = json.loads((path / "published.json").read_text(encoding="utf-8"))
    payload = {k: v for k, v in manifest.items() if k != "freeze_id"}
    if (manifest.get("schema_version") != 1 or manifest["kind"] != "frozen_research_features" or content_hash(payload) != manifest["freeze_id"] or
            published != {"freeze_id": manifest["freeze_id"], "manifest_hash": file_hash(path / "manifest.json")}):
        raise IntegrityError("Feature freeze publication or identity changed")
    verify_files(path, manifest["files"])
    if manifest["environment"] != environment_manifest() or manifest["source_code_hash"] != code_hash():
        raise ConfigurationError("Feature freeze runtime changed")
    bound_snapshot = load_snapshot(manifest["snapshot_path"])
    if (bound_snapshot.snapshot_id != manifest["snapshot_id"] or
            file_hash(bound_snapshot.path / "snapshot_manifest.json") != manifest["snapshot_manifest_hash"] or
            (snapshot is not None and bound_snapshot.snapshot_id != snapshot.snapshot_id)):
        raise IntegrityError("Feature freeze snapshot changed")
    protocol = ComparisonProtocol.model_validate(manifest["protocol"])
    protocol.require_runtime()
    from etf_ml.contracts import AcceptancePolicy, ModelSpec
    AcceptancePolicy.model_validate(manifest["acceptance"]).require_resolved()
    matrix_config = type("FrozenMatrix", (), {"models": [ModelSpec.model_validate(m) for m in manifest["models"]],
                                            "research": protocol.research})()
    frozen_models(matrix_config, protocol.model)
    if (protocol.snapshot_id != manifest["snapshot_id"] or
            protocol.baseline_feature_set_id != manifest["feature_set_id"] or
            manifest["feature_manifest"]["snapshot_id"] != manifest["snapshot_id"] or
            manifest["review"].get("status") != "accepted" or
            manifest["review"].get("feature_set_id") != manifest["feature_set_id"] or
            manifest["review"].get("protocol_id") != protocol.protocol_id):
        raise IntegrityError("Feature freeze protocol or review identity mismatch")
    if config is not None:
        for name in ("label", "universe", "validation", "portfolio", "research", "benchmarks"):
            if getattr(config, name).model_dump(mode="json") != getattr(protocol, name).model_dump(mode="json"):
                raise ConfigurationError("Feature freeze changed " + name)
        if ([m.model_dump(mode="json") for m in config.models] != manifest["models"] or
                config.acceptance.model_dump(mode="json") != manifest["acceptance"]):
            raise ConfigurationError("Feature freeze model matrix or final acceptance thresholds changed")
    registry = FactorRegistry(Path(manifest["registry_root"]))
    for entry in manifest["factors"]:
        spec = entry["spec"]
        current = registry.load(spec["factor_id"], spec["version"])
        if current["state"] != "frozen" or current["definition"]["version_id"] != entry["factor_version_id"]:
            raise QualityError("Feature freeze contains an inactive factor")
    review_path = Path(manifest["review"]["review_manifest"])
    if file_hash(review_path) != manifest["review_manifest_hash"]:
        raise IntegrityError("Final feature review evidence changed")
    verify_files(review_path.parent, json.loads(review_path.read_text(encoding="utf-8"))["files"])
    reports = {name: json.loads(Path(report).read_text(encoding="utf-8"))
               for name, report in manifest["review"]["reports"].items()}
    _verify_references(reports, review_path.parent.parent.parent, Path(manifest["artifact_root"]) / "models")
    frame = pd.read_parquet(path / "features.parquet")
    if list(frame.columns) != manifest["columns"]:
        raise IntegrityError("Feature freeze column order changed")
    from etf_ml.features.baseline import materialize
    from etf_ml.research.feature_sets import FeatureSetStore
    from pandas.testing import assert_frame_equal
    panel = pd.read_parquet(bound_snapshot.path / "research" / "panel.parquet")
    artifact = FeatureArtifact(manifest["feature_set_id"], frame, manifest["feature_manifest"])
    entries = artifact.manifest.get("composition", {}).get("factors", [])
    if (manifest["factors"] != entries or manifest["factor_version_ids"] !=
            [e["factor_version_id"] for e in entries] or not frame.index.equals(panel.index)):
        raise IntegrityError("Feature freeze research index or factor lineage changed")
    if artifact.manifest.get("kind") == "accumulated_research":
        store = FeatureSetStore(Path(artifact.manifest["path"]).parent, registry)
        store.verify_baseline(artifact, panel)
    else:
        expected = materialize(artifact.manifest["spec"], panel)
        if expected.feature_set_id != artifact.feature_set_id or expected.manifest != artifact.manifest:
            raise IntegrityError("Feature freeze base definition changed")
        try:
            assert_frame_equal(expected.frame, frame, check_exact=True)
        except AssertionError as exc:
            raise IntegrityError("Feature freeze base values changed") from exc
    if review_constraints(reports["full"], protocol):
        raise QualityError("Feature freeze no longer satisfies final review constraints")
    expected_groups = sorted({e["group"] for e in entries})
    if set(manifest["review"]["groups"]) != set(expected_groups) or set(reports) != {"full", *["without_" + g for g in expected_groups]}:
        raise IntegrityError("Feature freeze group review matrix changed")
    for group in expected_groups:
        evaluated = group_ablation_evaluation(reports["full"], reports["without_" + group], protocol)
        removed, columns = remove_final_group(artifact, group)
        recorded = manifest["review"]["groups"][group]
        if recorded != {**evaluated, "removed_columns": columns, "removed_feature_set_id": removed.feature_set_id} or evaluated["status"] != "accepted":
            raise QualityError("Feature freeze group contribution changed")
    return artifact, manifest


def compare_models(config, snapshot, frozen_path, output, *, run_id):
    features, frozen = load_frozen_features(frozen_path, config=config, snapshot=snapshot)
    output = ensure_within(output, config.artifact_root)
    identity = {"freeze_id": frozen["freeze_id"], "config": config.model_dump(mode="json")}
    comparison_id = content_hash(identity)
    with RunStore(output / "runs", run_id, identity) as run:
        if run.reused:
            result = json.loads((run.path / "comparison.json").read_text(encoding="utf-8"))
            _verify_references({"comparison": result["report"]}, output, config.artifact_root / "models")
            validate_matrix(result["report"], config, cost_multipliers=frozen["protocol"]["cost_multipliers"])
            return result
        report = run_matrix(config, snapshot.path, features, comparison_id, output,
                            purpose="compare-frozen-models", models=config.models,
                            cost_multipliers=frozen["protocol"]["cost_multipliers"])
        validate_matrix(report, config, cost_multipliers=frozen["protocol"]["cost_multipliers"])
        summaries = {}
        for model in config.models:
            model_rows = [r for r in report["by_fold"] if r["model"] == model.name]
            summaries[model.name] = {
                "median_net_return": statistics.median(r["portfolio"]["net_return"] for r in model_rows),
                "median_excess_return": statistics.median(r["portfolio"]["excess_return"] for r in model_rows),
                "worst_drawdown": max(r["portfolio"]["max_drawdown"] for r in model_rows),
                "run_count": len(model_rows),
            }
        ranking = sorted(summaries, key=lambda name: (-summaries[name]["median_excess_return"], name))
        result = {"status": "completed", "comparison_id": comparison_id, "freeze_id": frozen["freeze_id"],
                  "report": report, "summary": summaries, "ranking": ranking,
                  "selection_status": "awaiting_explicit_model_freeze",
                  "note": "Development comparison; no model is automatically promoted or evaluated on holdout"}
        atomic_json(run.path / "comparison.json", result)
        run.complete(result)
        return result


def validate_matrix(report, config, *, cost_multipliers=(2.,)):
    from etf_ml.research.selection import _number, REQUIRED_METRICS, EXPOSURE_METRICS
    if report.get("status") != "completed":
        raise QualityError("Frozen model comparison did not complete")
    expected = {(f.name, m.name, s) for f in config.validation.folds for m in config.models
                for s in config.research.seeds}
    seen, references = set(), {}
    for row in report["by_fold"]:
        key = row["fold"], row["model"], row["seed"]
        if key in seen or key not in expected:
            raise QualityError("Frozen model comparison matrix mismatch")
        seen.add(key)
        model = next(m for m in config.models if m.name == row["model"])
        if row["model_spec"] != _seed_model(model, row["seed"]).model_dump(mode="json"):
            raise QualityError("Frozen comparison model parameters changed")
        # Processor type may differ; time roles and target rows must not.
        identity = {k: row["dataset"][k] for k in (
            "fold", "holdout_start", "counts", "sample_index_hashes", "evaluation_index_hash",
            "processor_fit_index_hash", "qlib_roles")}
        identity["daily_index_hash"] = row["daily_index_hash"]
        expected_fold = next(f for f in config.validation.folds if f.name == row["fold"])
        if (row["dataset"]["fold"] != expected_fold.model_dump(mode="json") or
                row["dataset"]["holdout_start"] != config.validation.holdout_start or
                not row["daily_index_hash"]):
            raise QualityError("Frozen comparison changed a declared time role")
        if set(row.get("cost_stress", {})) != {str(m) for m in cost_multipliers}:
            raise QualityError("Frozen comparison cost pressure matrix changed")
        for metrics in [row["portfolio"], *row["cost_stress"].values()]:
            if any(not _number(metrics.get(k)) for k in
                   (*REQUIRED_METRICS, *EXPOSURE_METRICS, "net_return", "execution_cost_over_initial_equity")):
                raise QualityError("Frozen comparison metric missing or nonfinite")
            dates = metrics.get("effective_dates")
            if (metrics.get("accounting_reconciled") is not True or not isinstance(dates, int) or
                    isinstance(dates, bool) or dates <= 0 or metrics["execution_cost_over_initial_equity"] < 0 or
                    not 0 <= metrics["max_drawdown"] <= 1 or metrics["annualized_volatility"] < 0 or
                    metrics["turnover"] < 0 or any(not 0 <= metrics[k] <= 1 + 1e-8 for k in EXPOSURE_METRICS)):
                raise QualityError("Frozen comparison ledger or metric invalid")
        fold = row["fold"]
        if fold in references and references[fold] != identity:
            raise QualityError("Frozen models evaluated different sample or daily indices")
        references[fold] = identity
    if seen != expected:
        raise QualityError("Frozen model comparison matrix incomplete")
