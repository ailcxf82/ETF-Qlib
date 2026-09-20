from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from etf_ml.artifacts import RunStore, environment_manifest
from etf_ml.config import load_config
from etf_ml.data.snapshot import audit_source, build_snapshot, load_snapshot
from etf_ml.errors import ETFError, ConfigurationError, IntegrityError
from etf_ml.utils import atomic_json, redact, code_hash, file_hash, source_hashes, ensure_within, verify_files


def parser():
    result = argparse.ArgumentParser(description="ETF research framework")
    commands = result.add_subparsers(dest="command", required=True)
    for command in ("first-loop", "first-loop-readiness", "audit-data", "build-data", "baseline", "research-factor", "freeze-features", "compare-models", "freeze-model", "evaluate-holdout", "daily-signal", "activate-model", "restore-model"):
        child = commands.add_parser(command)
        child.add_argument("--config", type=Path)
        child.add_argument("--run-id")
        child.add_argument("--source", type=Path)
        if command == "first-loop":
            child.add_argument("--snapshot", type=Path)
        if command in ("baseline", "research-factor", "compare-models", "daily-signal"):
            child.add_argument("--snapshot", type=Path, required=True)
        if command == "freeze-features":
            child.add_argument("--session", type=Path, required=True)
        if command in ("compare-models", "freeze-model"):
            child.add_argument("--frozen-features", type=Path, required=True)
        if command == "evaluate-holdout":
            child.add_argument("--frozen-model", type=Path, required=True)
        if command == "daily-signal":
            child.add_argument("--frozen-model", type=Path)
            child.add_argument("--ingestion", type=Path, required=True)
            child.add_argument("--account", type=Path, required=True)
            child.add_argument("--as-of", required=True)
        if command in ("activate-model", "restore-model"):
            child.add_argument("--selection-reason", required=True)
            if command == "activate-model":
                child.add_argument("--frozen-model", type=Path, required=True)
        if command == "freeze-model":
            child.add_argument("--comparison", type=Path, required=True)
            child.add_argument("--model", choices=("ridge", "lightgbm", "xgboost"), required=True)
            child.add_argument("--fold", required=True)
            child.add_argument("--seed", type=int, required=True)
            child.add_argument("--selection-reason", required=True)
        if command == "research-factor":
            child.add_argument("--max-trials", type=int)
            child.add_argument("--replay", type=Path)
            child.add_argument("--stress-min-excess", type=float)
    return result


def _model_references(rows):
    return [{"path": r["model_path"], "manifest_hash": r["model_manifest_hash"]}
            for r in rows]


def _verify_models(references, root):
    for item in references:
        path = ensure_within(Path(item["path"]), root / "models")
        if file_hash(path / "manifest.json") != item["manifest_hash"]:
            raise IntegrityError("Referenced model manifest changed")
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        if file_hash(path / "bundle.pkl") != manifest["bundle_sha256"]:
            raise IntegrityError("Referenced model weights changed")


def _print(run_id, metrics, *, reused=False):
    public = {k: v for k, v in metrics.items() if k not in ("model_references", "research_files", "external_runs")}
    print(json.dumps({"run_id": run_id, "reused": reused, **public}, ensure_ascii=False))


def main(argv=None):
    args = parser().parse_args(argv)
    run_id = args.run_id or (args.command + "-" + uuid.uuid4().hex[:16])
    try:
        override = {"data": {"source": args.source}} if args.source else {}
        if getattr(args, "max_trials", None) is not None:
            override["research"] = {"max_trials": args.max_trials}
        config = load_config(args.config, override)
        if args.command == "first-loop-readiness":
            from etf_ml.research.readiness import first_loop_readiness
            report = first_loop_readiness(config)
            _print(run_id, report)
            return 5 if report["status"] == "blocked" else 0
        if args.command in ("activate-model", "restore-model"):
            from etf_ml.operations.releases import change_release
            report = change_release(config, run_id=run_id, reason=args.selection_reason,
                                    package_path=getattr(args, "frozen_model", None), restore=args.command == "restore-model")
            _print(run_id, report, reused=report["reused"])
            return 0
        if args.command == "daily-signal":
            from etf_ml.operations.daily import daily_signal
            package_path = args.frozen_model
            if package_path is None:
                from etf_ml.operations.releases import load_active_model
                _, _, _, release = load_active_model(config)
                package_path = Path(release["package_path"])
            report = daily_signal(config, package_path=package_path, snapshot_path=args.snapshot,
                                  receipt_path=args.ingestion, account_path=args.account, as_of=args.as_of, run_id=run_id)
            _print(run_id, report, reused=report["reused"])
            return report["exit_code"]
        snapshot = load_snapshot(args.snapshot) if getattr(args, "snapshot", None) else None
        session_payload = json.loads(args.session.read_text(encoding="utf-8")) if getattr(args, "session", None) else None
        if session_payload:
            snapshot = load_snapshot(session_payload["snapshot_path"])
        identity = {
            "command": args.command, "config": config.model_dump(mode="json"),
            "code_hash": code_hash(), "environment": environment_manifest(),
            "snapshot_manifest_hash": file_hash(snapshot.path / "snapshot_manifest.json") if snapshot else None,
            "source_hashes": source_hashes(config.data.source) if args.command in ("audit-data", "build-data") else None,
            "replay_hash": file_hash(args.replay) if getattr(args, "replay", None) else None,
            "stress_min_excess": getattr(args, "stress_min_excess", None),
            "session_hash": file_hash(args.session) if getattr(args, "session", None) else None,
            "frozen_feature_manifest_hash": file_hash(args.frozen_features / "manifest.json") if getattr(args, "frozen_features", None) else None,
            "comparison_hash": file_hash(args.comparison) if getattr(args, "comparison", None) else None,
            "frozen_model_manifest_hash": file_hash(args.frozen_model / "manifest.json") if getattr(args, "frozen_model", None) else None,
            "model_selection": {k: getattr(args, k, None) for k in ("model", "fold", "seed", "selection_reason")},
        }
        with RunStore(config.artifact_root / "runs", run_id, identity) as run:
            if run.reused:
                metrics = json.loads((run.path / "metrics.json").read_text(encoding="utf-8"))
                _verify_models(metrics.get("model_references", []), config.artifact_root)
                if metrics.get("research_checkpoint"):
                    verify_files(Path(metrics["research_root"]), metrics["research_files"])
                    if file_hash(Path(metrics["research_checkpoint"])) != metrics["research_checkpoint_hash"]:
                        raise IntegrityError("Research checkpoint changed after completion")
                for item in metrics.get("external_runs", []):
                    external = ensure_within(Path(item["path"]), config.artifact_root)
                    if file_hash(external / "manifest.json") != item["manifest_hash"]:
                        raise IntegrityError("Referenced finalization experiment changed")
                    verify_files(external, json.loads((external / "manifest.json").read_text(encoding="utf-8"))["files"])
                if metrics.get("frozen_feature_path"):
                    from etf_ml.research.finalize import load_frozen_features
                    load_frozen_features(metrics["frozen_feature_path"], config=config, snapshot=snapshot)
                if metrics.get("frozen_model_path"):
                    from etf_ml.models.deployment import load_frozen_model
                    _, _, frozen_package = load_frozen_model(metrics["frozen_model_path"], config=config,
                                                              allow_rejected=args.command == "evaluate-holdout")
                    if metrics.get("holdout_path"):
                        from etf_ml.validation.holdout import _verify_result
                        holdout_path = ensure_within(Path(metrics["holdout_path"]), config.artifact_root / "final_acceptance" / "runs")
                        if file_hash(holdout_path / "manifest.json") != metrics["holdout_manifest_hash"]:
                            raise IntegrityError("Independent holdout manifest changed")
                        verify_files(holdout_path, json.loads((holdout_path / "manifest.json").read_text(encoding="utf-8"))["files"])
                        holdout_report = json.loads((holdout_path / "holdout_report.json").read_text(encoding="utf-8"))
                        _verify_result(holdout_report, frozen_package, config)
                        from etf_ml.validation.usage import HoldoutUsageStore
                        history = HoldoutUsageStore(config.artifact_root / "final_acceptance" / "usage").history(metrics["usage_id"])
                        if (not history or history[-1]["status"] != "completed" or
                                history[-1]["details"]["report_manifest_hash"] != metrics["holdout_manifest_hash"]):
                            raise IntegrityError("Independent holdout usage audit changed")
                        if holdout_report["status"] != metrics["investment_status"]:
                            raise IntegrityError("Independent holdout decision changed")
                _print(run_id, metrics, reused=True)
                return metrics.get("exit_code", 0 if metrics.get("quality_status", "passed") == "passed" else 5)
            if args.command == "audit-data":
                report = audit_source(config.data)
                atomic_json(run.path / "data_quality.json", report)
                metrics = {"quality_status": report["status"], "errors": report["errors"],
                           "instrument_count": report["instrument_count"], "row_count": report["row_count"],
                           "artifact_path": str(run.path)}
                run.complete(metrics)
                _print(run_id, metrics)
                return 0 if report["status"] == "passed" else 5
            if args.command == "first-loop":
                from etf_ml.research.first_loop import execute_first_loop
                report = execute_first_loop(config, run.path, run_id=run_id, snapshot=snapshot)
                metrics = {**report, "quality_status":"diagnostic" if config.data.mode == "diagnostic" else "passed",
                           "artifact_path":str(run.path), "exit_code":0 if report["status"] == "completed" else 4}
                if report["status"] == "completed":
                    run.complete(metrics)
                _print(run_id, metrics)
                return metrics["exit_code"]
            if args.command == "baseline":
                from etf_ml.research.execution import execute_baseline
                config.portfolio.require_resolved()
                report = execute_baseline(config, snapshot.path, run.path, run_id=run_id)
                metrics = {"quality_status": "passed", "snapshot_id": report["snapshot_id"],
                           "fold_model_runs": len(report["by_fold"]),
                           "auxiliary_runs": len(report["auxiliary_by_fold"]), "artifact_path": str(run.path),
                           "model_references": _model_references(report["by_fold"])}
                run.complete(metrics)
                _print(run_id, metrics)
                return 0
            if args.command == "freeze-features":
                from etf_ml.research.session import ResearchSession
                from etf_ml.research.protocol import ComparisonProtocol
                from etf_ml.research.finalize import freeze_features
                session = ResearchSession(
                    config, snapshot.path, ComparisonProtocol.model_validate(session_payload["protocol"]),
                    root=Path(session_payload["root"]),
                    selected_feature_set_id=session_payload.get("selected_feature_set_id"))
                report = freeze_features(session, config.artifact_root / "final_reviews", run_id=run_id)
                reports = [json.loads(Path(path).read_text(encoding="utf-8")) for path in report["reports"].values()]
                code = 0 if report["status"] == "accepted" else 5
                metrics = {"quality_status": "passed" if code == 0 else "failed",
                           "freeze_status": report["status"], "frozen_feature_path": report["freeze_path"],
                           "feature_set_id": report["feature_set_id"], "artifact_path": report["freeze_path"] or str(run.path),
                           "model_references": _model_references([r for rep in reports for r in rep["by_fold"]]),
                           "external_runs": [child for rep in reports for child in rep["child_runs"]],
                           "exit_code": code}
                run.complete(metrics)
                _print(run_id, metrics)
                return code
            if args.command == "evaluate-holdout":
                from etf_ml.validation.holdout import evaluate_holdout
                report = evaluate_holdout(config, args.frozen_model, run_id=run_id)
                code = 0 if report["status"] == "passed" else 5
                metrics = {"quality_status": "passed", "investment_status": report["status"],
                           "frozen_model_path": str(args.frozen_model.resolve()), "holdout_path": report["artifact_path"],
                           "holdout_manifest_hash": report["manifest_hash"], "portfolio": report["portfolio"],
                           "reasons": report["reasons"], "usage_id": report["usage_id"],
                           "holdout_reused": report["reused"], "artifact_path": report["artifact_path"], "exit_code": code}
                run.complete(metrics)
                _print(run_id, metrics)
                return code
            if args.command == "freeze-model":
                from etf_ml.models.deployment import freeze_model
                report = freeze_model(config, args.frozen_features, args.comparison,
                                      model=args.model, fold=args.fold, seed=args.seed,
                                      reason=args.selection_reason, run_id=run_id)
                metrics = {**report, "quality_status": "passed", "artifact_path": report["frozen_model_path"]}
                run.complete(metrics)
                _print(run_id, metrics)
                return 0
            if args.command == "compare-models":
                from etf_ml.research.finalize import compare_models
                report = compare_models(config, snapshot, args.frozen_features,
                                        config.artifact_root / "comparisons", run_id=run_id)
                metrics = {"quality_status": "passed", "comparison_id": report["comparison_id"],
                           "ranking": report["ranking"], "selection_status": report["selection_status"],
                           "comparison_path": str((config.artifact_root / "comparisons" / "runs" / run_id / "comparison.json").resolve()),
                           "frozen_feature_path": str(args.frozen_features.resolve()), "artifact_path": str(run.path),
                           "fold_model_runs": len(report["report"]["by_fold"]),
                           "model_references": _model_references(report["report"]["by_fold"]),
                           "external_runs": report["report"]["child_runs"]}
                atomic_json(run.path / "comparison.json", report)
                run.complete(metrics)
                _print(run_id, metrics)
                return 0
            if args.command == "research-factor":
                from etf_ml.features.baseline import materialize
                from etf_ml.research.protocol import ComparisonProtocol
                from etf_ml.research.session import ResearchSession
                from etf_ml.research.controller import ResearchController
                import pandas as pd
                models = [m for m in config.models if m.name == "lightgbm"]
                if len(models) != 1:
                    raise ConfigurationError("Research requires exactly one fixed LightGBM model")
                panel = pd.read_parquet(snapshot.path / "research" / "panel.parquet")
                baseline = materialize({"snapshot_id": snapshot.snapshot_id}, panel)
                protocol = ComparisonProtocol(
                    snapshot_id=snapshot.snapshot_id, baseline_feature_set_id=baseline.feature_set_id,
                    label=config.label, universe=config.universe, validation=config.validation,
                    portfolio=config.portfolio, research=config.research, model=models[0],
                    benchmarks=config.benchmarks,
                    stress_min_excess_return=args.stress_min_excess)
                root = ensure_within(config.artifact_root / "research" / run_id, config.artifact_root)
                session = ResearchSession(config, snapshot.path, protocol, root=root)
                replay = json.loads(args.replay.read_text(encoding="utf-8")) if args.replay else None
                if replay is not None and not isinstance(replay, list):
                    raise ConfigurationError("Replay must be a list of research trials")
                controller = ResearchController(session, run_id, replay_trials=replay)
                state = controller.run()
                atomic_json(run.path / "protocol.json", protocol)
                atomic_json(run.path / "research_state.json", state)
                references = []
                for trial in state["trials"]:
                    record = json.loads((controller.root / trial["path"]).read_text(encoding="utf-8"))
                    for candidate in record["result"]["by_candidate"]:
                        for path in candidate.get("reports", {}).values():
                            report = json.loads(Path(path).read_text(encoding="utf-8"))
                            references.extend(_model_references(report["by_fold"]))
                exit_code = 6 if state["status"] == "paused_budget" else 130 if state["status"] == "cancelled" else 0
                metrics = {"quality_status": "passed" if state["status"] == "completed" else "incomplete",
                           "research_status": state["status"], "trial_count": len(state["trials"]),
                           "candidate_found": any(t["status"] == "accepted" for t in state["trials"]),
                           "artifact_path": str(controller.root),
                           "research_root": str(session.root),
                           "research_files": source_hashes(session.root) if state["status"] == "completed" else {},
                           "research_checkpoint": str(controller.root / "checkpoint.json"),
                           "research_checkpoint_hash": file_hash(controller.root / "checkpoint.json"),
                           "model_references": references, "exit_code": exit_code}
                if state["status"] == "completed":
                    run.complete(metrics)
                _print(run_id, metrics)
                return exit_code
            snapshot = build_snapshot(config.data.source, config.data, config.universe)
            metrics = {"snapshot_id": snapshot.snapshot_id, "quality_status": "diagnostic" if config.data.mode == "diagnostic" else "passed",
                       "qualification":snapshot.manifest.get("qualification", {}),
                       "artifact_path": str(snapshot.path)}
            run.complete(metrics)
            _print(run_id, metrics)
            return 0
    except ETFError as exc:
        print(json.dumps(redact({"status": "failed", "run_id": run_id,
                                "reason": exc.reason, "message": str(exc)}),
                         ensure_ascii=False), file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print(json.dumps({"status": "cancelled", "run_id": run_id}), file=sys.stderr)
        return 130
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "failed", "run_id": run_id,
                          "reason": "execution_failed", "exception_type": type(exc).__name__}),
              file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
