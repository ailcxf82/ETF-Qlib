"""One real RDAgent proposal/code/evaluation/feedback cycle on user data."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
from etf_ml.data.snapshot import build_snapshot
from etf_ml.errors import ConfigurationError
from etf_ml.features.baseline import materialize
from etf_ml.research.protocol import ComparisonProtocol
from etf_ml.research.session import ResearchSession
from etf_ml.research.controller import ResearchController
from etf_ml.research.execution import execute_baseline
from etf_ml.research.progress import Progress
from etf_ml.utils import atomic_json, ensure_within, file_hash


def load_reusable_baseline(config, snapshot, baseline_root):
    """Load a completed baseline only when its immutable execution inputs match."""
    root = ensure_within(Path(baseline_root), config.artifact_root)
    if (root / "baseline_reference.json").is_file():
        from etf_ml.research.reuse_baseline import SharedBaseline, baseline_key
        from etf_ml.research.reuse_identity import local_reuse_root
        reference = json.loads((root / "baseline_reference.json").read_text(encoding="utf-8"))
        if reference.get("package_id"):
            shared = SharedBaseline(local_reuse_root(config))
            report, checked = shared.load(reference["package_id"])
            if checked["cache_key"] != baseline_key(config, snapshot.snapshot_id):
                raise ConfigurationError("Reusable baseline configuration differs from first loop")
            return report, {"mode": "shared", **checked}
    paths = {name: root / name for name in (
        "baseline_report.json", "execution_result.json", "pipeline_request.json")}
    if not all(path.is_file() for path in paths.values()):
        raise ConfigurationError("Reusable baseline is missing required audit artifacts")
    execution = json.loads(paths["execution_result.json"].read_text(encoding="utf-8"))
    request = json.loads(paths["pipeline_request.json"].read_text(encoding="utf-8"))
    report = json.loads(paths["baseline_report.json"].read_text(encoding="utf-8"))
    if execution.get("status") != "succeeded" or execution.get("returncode") != 0:
        raise ConfigurationError("Reusable baseline did not finish successfully")
    if request.get("mode") != "baseline" or request.get("feature_override") is not None:
        raise ConfigurationError("Reusable baseline is not the unmodified baseline experiment")
    if request.get("config") != config.model_dump(mode="json"):
        raise ConfigurationError("Reusable baseline configuration differs from first loop")
    from etf_ml.artifacts import environment_manifest
    from etf_ml.utils import code_hash
    if request.get("execution_identity") != {"source_code_hash": code_hash(), "environment": environment_manifest()}:
        raise ConfigurationError("Reusable baseline runtime identity is missing or differs; build a new baseline")
    if report.get("execution_identity") != request["execution_identity"]:
        raise ConfigurationError("Reusable baseline worker runtime identity differs from request")
    if Path(request.get("snapshot_path", "")).resolve() != snapshot.path.resolve():
        raise ConfigurationError("Reusable baseline snapshot path differs from first loop")
    if report.get("status") != "completed" or report.get("snapshot_id") != snapshot.snapshot_id:
        raise ConfigurationError("Reusable baseline report does not match the completed snapshot")
    if report.get("qualification") != snapshot.manifest["qualification"]:
        raise ConfigurationError("Reusable baseline qualification differs from first loop")
    expected_folds = {fold.name for fold in config.validation.folds}
    completed_folds = {row.get("fold") for row in report.get("by_fold", [])}
    if completed_folds != expected_folds:
        raise ConfigurationError("Reusable baseline does not cover every configured development fold")
    provenance = {"mode": "reused", "root": str(root),
                  **{f"{name.removesuffix('.json')}_sha256": file_hash(path)
                     for name, path in paths.items()}}
    return report, provenance


def closure_evidence(state, records, baseline, *, execution_mode="rdagent_live", reuse_store=None):
    if execution_mode not in {"rdagent_live", "deterministic_library"}:
        raise ConfigurationError("Unknown research execution mode")
    candidates = [candidate for record in records for candidate in record.get("result", {}).get("by_candidate", [])]
    evaluated = [candidate for candidate in candidates if candidate.get("reports") and
                 candidate.get("failure_stage") is None and candidate["status"] != "failed"]
    billing = state.get("billing", {})
    calls = billing.get("calls", {})
    paid = [call for call in calls.values() if call.get("paid")]
    # Some ledgers express paid transport as a nonzero/unknown reservation.
    completed_calls = [call for call in calls.values() if call["status"] in ("completed", "cost_unknown") and call.get("response_hash")]
    dispatch_complete = ((len(completed_calls) >= 3 and
                          sum(bool(call.get("paid")) for call in completed_calls) >= 3)
                         if execution_mode == "rdagent_live" else not calls)
    complete = (state["status"] == "completed" and len(records) == 1 and
                len(evaluated) == len(candidates) and bool(evaluated) and
                bool(baseline.get("by_fold")) and dispatch_complete)
    reused = []
    if reuse_store is not None and state["status"] == "completed" and len(records) == 1:
        result = records[0].get("result", {})
        if result.get("status") == "reuse" and result.get("reuse_package_id"):
            candidate = reuse_store.decision(result["reuse_package_id"])
            if candidate["evaluation_id"] != result.get("reused_evaluation_id"):
                raise ConfigurationError("Reused evaluation differs from compact receipt")
            reused = [candidate]
            candidates = evaluated = reused
            complete = True
    return {"status":"completed" if complete else "incomplete", "trial_count":len(records),
        "execution_mode": execution_mode,
        "reused_candidates": len(reused),
        "reuse_package_ids": [row["reuse_package_id"] for row in reused],
        "evaluated_candidates":len(evaluated), "candidate_count":len(candidates),
        "candidate_decisions":[{"factor_id":c.get("factor_id"), "status":c["status"],
            "reasons":c.get("evaluation", {}).get("reasons", c.get("reasons", [])),
            "reports":c.get("reports", {})} for c in candidates],
        "llm_dispatches":len(calls), "completed_llm_calls":len(completed_calls),
        "paid_call_records":len(paid), "billing":billing,
        "baseline_fold_model_runs":len(baseline.get("by_fold", [])),
        "campaign_summary":state.get("campaign_summary"),
        "investment_accepted":False, "formal_g0_passed":False, "holdout_evaluated":False}


def execute_first_loop(config, output, *, run_id, snapshot=None, baseline_root=None,
                       campaign_id=None, campaign_max_trials=None, mechanism_plan=None):
    if config.research.budget_mode != "unlimited" or config.research.max_trials != 1:
        raise ConfigurationError("First real loop requires frozen unlimited fees and exactly one trial")
    if config.data.mode == "formal" and config.research.stress_min_excess_return is None:
        raise ConfigurationError("Formal first loop requires research.stress_min_excess_return")
    if (campaign_id is None) != (campaign_max_trials is None):
        raise ConfigurationError("Campaign id and finite campaign max trials must be supplied together")
    if config.data.mode == "formal" and campaign_id is None:
        raise ConfigurationError("Formal first loop requires a finite campaign id and max-trials cap")
    if campaign_id is not None:
        from etf_ml.research.campaign import CampaignLedger
        from etf_ml.research.campaign import FIVE_FACTOR_MECHANISM_PLAN
        plans = {"five_factor_v1": FIVE_FACTOR_MECHANISM_PLAN}
        if mechanism_plan not in (None, *plans):
            raise ConfigurationError("Unknown campaign mechanism plan")
        campaign = CampaignLedger(config.artifact_root / "research_campaigns", campaign_id,
                                  campaign_max_trials, config.research.max_campaign_wall_seconds,
                                  mechanism_plan=plans.get(mechanism_plan))
        campaign.initialize()
        if campaign.summary()["campaign_attempted_trials"] >= campaign_max_trials:
            raise ConfigurationError("Campaign attempt limit is already exhausted")
    output = ensure_within(Path(output), config.artifact_root)
    output.mkdir(parents=True, exist_ok=True)
    events = Progress(output, run_id, "first_loop")
    progress = {"status":"running", "phase":"building_snapshot", "run_id":run_id,
                "mode":config.data.mode, "formal_g0_passed":False}
    atomic_json(output / "first_loop_progress.json", progress)
    events.emit("building_snapshot", mode=config.data.mode)
    if snapshot is None:
        snapshot = build_snapshot(config.data.source, config.data, config.universe)
    else:
        from etf_ml.data.diagnostic import require_snapshot_mode
        require_snapshot_mode(config, snapshot)
        if snapshot.manifest["spec"] != config.data.model_dump(mode="json"):
            raise ConfigurationError("Reused snapshot data specification differs from first loop")
        if snapshot.manifest["universe_policy"] != config.universe.model_dump(mode="json"):
            raise ConfigurationError("Reused snapshot universe differs from first loop")
    progress.update(snapshot_path=str(snapshot.path), snapshot_id=snapshot.snapshot_id)
    events.emit("snapshot_ready", snapshot_id=snapshot.snapshot_id,
                snapshot_path=str(snapshot.path))
    baseline, baseline_provenance = {}, {"mode": "deferred_until_admission"}
    used_baseline_root = output / "baseline"
    if baseline_root is None:
        events.emit("baseline_deferred", reason="check_compact_reuse_before_training")
    else:
        progress.update(phase="validating_baseline_reuse")
        atomic_json(output / "first_loop_progress.json", progress)
        events.emit("validating_baseline_reuse", snapshot_id=snapshot.snapshot_id)
        used_baseline_root = ensure_within(Path(baseline_root), config.artifact_root)
        baseline, baseline_provenance = load_reusable_baseline(config, snapshot, used_baseline_root)
        atomic_json(output / "baseline_reference.json", baseline_provenance)
        progress.update(phase="baseline_reused", baseline_reference=str(used_baseline_root))
        atomic_json(output / "first_loop_progress.json", progress)
        events.emit("baseline_reused", baseline_reference=str(used_baseline_root))
    primary = [model for model in config.models if model.name == "lightgbm"]
    if len(primary) != 1:
        raise ConfigurationError("Real factor loop requires a fixed LightGBM model")
    events.emit("loading_research_panel", snapshot_id=snapshot.snapshot_id)
    panel = pd.read_parquet(snapshot.path / "research" / "panel.parquet")
    events.emit("materializing_baseline_features", panel_rows=len(panel))
    features = materialize({"snapshot_id":snapshot.snapshot_id}, panel)
    events.emit("baseline_features_ready", feature_set_id=features.feature_set_id,
                feature_count=len(features.frame.columns))
    from etf_ml.research.reuse_identity import evaluation_code_hash, local_reuse_root
    from etf_ml.research.reuse_store import ReuseStore
    store = ReuseStore(local_reuse_root(config))
    protocol = ComparisonProtocol(snapshot_id=snapshot.snapshot_id, baseline_feature_set_id=features.feature_set_id,
        evaluation_code_hash=evaluation_code_hash(),
        label=config.label, universe=config.universe, validation=config.validation,
        portfolio=config.portfolio, research=config.research, model=primary[0], benchmarks=config.benchmarks,
        stress_min_excess_return=config.research.stress_min_excess_return)
    events.emit("creating_research_session", protocol_id=protocol.protocol_id)
    session = ResearchSession(config, snapshot.path, protocol, root=output / "research",
                              memory_root=config.artifact_root / "research_memory" / "v2")
    events.emit("research_session_ready", protocol_id=protocol.protocol_id)
    progress.update(phase="rdagent_research", protocol_id=protocol.protocol_id)
    atomic_json(output / "first_loop_progress.json", progress)
    events.emit("rdagent_research", protocol_id=protocol.protocol_id)
    controller = ResearchController(session, run_id + "-research", campaign_id=campaign_id,
                                    campaign_max_trials=campaign_max_trials,
                                    mechanism_plan=mechanism_plan)
    state = controller.run()
    records = [json.loads((controller.root / trial["path"]).read_text(encoding="utf-8")) for trial in state["trials"]]
    reused = (state["status"] == "completed" and len(records) == 1 and
              records[0].get("result", {}).get("reuse_package_id"))
    if reused:
        baseline_provenance = {"mode": "not_required_for_compact_reuse"}
    elif not baseline and state["status"] == "completed":
        events.emit("baseline", snapshot_id=snapshot.snapshot_id)
        baseline = execute_baseline(config, snapshot.path, used_baseline_root, run_id=run_id + "-baseline")
        baseline_provenance = {"mode": "computed_or_shared", "root": str(used_baseline_root),
                               "baseline_report_sha256": file_hash(used_baseline_root / "baseline_report.json")}
    report = closure_evidence(state, records, baseline, reuse_store=store)
    report.update(run_id=run_id, mode=config.data.mode, snapshot_id=snapshot.snapshot_id,
        snapshot_path=str(snapshot.path), qualification=snapshot.manifest["qualification"],
        baseline_report=str(used_baseline_root / "baseline_report.json") if baseline else None,
        baseline_provenance=baseline_provenance,
        checkpoint=str(controller.root / "checkpoint.json"),
        checkpoint_sha256=file_hash(controller.root / "checkpoint.json"),
        protocol_id=protocol.protocol_id, data_source=str(config.data.source.resolve()),
        replay_used=False, source_scope_instruments=len(pd.read_parquet(snapshot.path / "metadata.parquet")),
        development_folds=[fold.name for fold in config.validation.folds], seeds=config.research.seeds,
        first_loop_report=str(output / "first_loop_report.json"))
    from etf_ml.research.qualification import qualification_report
    qualification = qualification_report(snapshot, report.get("candidate_decisions", []),
                                         config.validation.model_dump(mode="json"))
    atomic_json(output / "qualification_report.json", qualification)
    report.update(qualification_report=str(output / "qualification_report.json"),
                  qualification_report_sha256=file_hash(output / "qualification_report.json"),
                  stage_statuses=qualification["stages"], **qualification["legacy_booleans"])
    atomic_json(output / "first_loop_report.json", report)
    progress.update(status=report["status"], phase="finished", report_path=report["first_loop_report"])
    atomic_json(output / "first_loop_progress.json", progress)
    events.emit("finished", status=report["status"], report_path=report["first_loop_report"])
    return report
