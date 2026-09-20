"""One real RDAgent proposal/code/evaluation/feedback cycle on user data."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
from etf_ml.data.snapshot import build_snapshot
from etf_ml.errors import ConfigurationError, QualityError
from etf_ml.features.baseline import materialize
from etf_ml.research.protocol import ComparisonProtocol
from etf_ml.research.session import ResearchSession
from etf_ml.research.controller import ResearchController
from etf_ml.research.execution import execute_baseline
from etf_ml.utils import atomic_json, ensure_within, file_hash


def closure_evidence(state, records, baseline):
    candidates = [candidate for record in records for candidate in record.get("result", {}).get("by_candidate", [])]
    evaluated = [candidate for candidate in candidates if candidate.get("reports") and
                 candidate.get("failure_stage") is None and candidate["status"] != "failed"]
    billing = state.get("billing", {})
    calls = billing.get("calls", {})
    paid = [call for call in calls.values() if call.get("paid")]
    # Some ledgers express paid transport as a nonzero/unknown reservation.
    completed_calls = [call for call in calls.values() if call["status"] in ("completed", "cost_unknown") and call.get("response_hash")]
    complete = (state["status"] == "completed" and len(records) == 1 and
                len(evaluated) == len(candidates) and bool(evaluated) and
                bool(baseline.get("by_fold")) and len(completed_calls) >= 3 and
                sum(bool(call.get("paid")) for call in completed_calls) >= 3)
    return {"status":"completed" if complete else "incomplete", "trial_count":len(records),
        "evaluated_candidates":len(evaluated), "candidate_count":len(candidates),
        "candidate_decisions":[{"factor_id":c.get("factor_id"), "status":c["status"],
            "reasons":c.get("evaluation", {}).get("reasons", c.get("reasons", [])),
            "reports":c.get("reports", {})} for c in candidates],
        "llm_dispatches":len(calls), "completed_llm_calls":len(completed_calls),
        "paid_call_records":len(paid), "billing":billing,
        "baseline_fold_model_runs":len(baseline.get("by_fold", [])),
        "investment_accepted":False, "formal_g0_passed":False, "holdout_evaluated":False}


def execute_first_loop(config, output, *, run_id, snapshot=None):
    if config.research.budget_mode != "unlimited" or config.research.max_trials != 1:
        raise ConfigurationError("First real loop requires frozen unlimited fees and exactly one trial")
    output = ensure_within(Path(output), config.artifact_root)
    output.mkdir(parents=True, exist_ok=True)
    progress = {"status":"running", "phase":"building_snapshot", "run_id":run_id,
                "mode":config.data.mode, "formal_g0_passed":False}
    atomic_json(output / "first_loop_progress.json", progress)
    if snapshot is None:
        snapshot = build_snapshot(config.data.source, config.data, config.universe)
    else:
        from etf_ml.data.diagnostic import require_snapshot_mode
        require_snapshot_mode(config, snapshot)
        if snapshot.manifest["spec"] != config.data.model_dump(mode="json"):
            raise ConfigurationError("Reused snapshot data specification differs from first loop")
        if snapshot.manifest["universe_policy"] != config.universe.model_dump(mode="json"):
            raise ConfigurationError("Reused snapshot universe differs from first loop")
    progress.update(phase="baseline", snapshot_path=str(snapshot.path), snapshot_id=snapshot.snapshot_id)
    atomic_json(output / "first_loop_progress.json", progress)
    baseline_root = output / "baseline"
    baseline = execute_baseline(config, snapshot.path, baseline_root, run_id=run_id + "-baseline")
    primary = [model for model in config.models if model.name == "lightgbm"]
    if len(primary) != 1:
        raise ConfigurationError("Real factor loop requires a fixed LightGBM model")
    panel = pd.read_parquet(snapshot.path / "research" / "panel.parquet")
    features = materialize({"snapshot_id":snapshot.snapshot_id}, panel)
    protocol = ComparisonProtocol(snapshot_id=snapshot.snapshot_id, baseline_feature_set_id=features.feature_set_id,
        label=config.label, universe=config.universe, validation=config.validation,
        portfolio=config.portfolio, research=config.research, model=primary[0], benchmarks=config.benchmarks)
    session = ResearchSession(config, snapshot.path, protocol, root=output / "research")
    progress.update(phase="rdagent_research", protocol_id=protocol.protocol_id)
    atomic_json(output / "first_loop_progress.json", progress)
    controller = ResearchController(session, run_id + "-research")
    state = controller.run()
    records = [json.loads((controller.root / trial["path"]).read_text(encoding="utf-8")) for trial in state["trials"]]
    report = closure_evidence(state, records, baseline)
    report.update(run_id=run_id, mode=config.data.mode, snapshot_id=snapshot.snapshot_id,
        snapshot_path=str(snapshot.path), qualification=snapshot.manifest["qualification"],
        baseline_report=str(baseline_root / "baseline_report.json"),
        checkpoint=str(controller.root / "checkpoint.json"),
        checkpoint_sha256=file_hash(controller.root / "checkpoint.json"),
        protocol_id=protocol.protocol_id, data_source=str(config.data.source.resolve()),
        replay_used=False, source_scope_instruments=len(pd.read_parquet(snapshot.path / "metadata.parquet")),
        development_folds=[fold.name for fold in config.validation.folds], seeds=config.research.seeds,
        first_loop_report=str(output / "first_loop_report.json"))
    atomic_json(output / "first_loop_report.json", report)
    progress.update(status=report["status"], phase="finished", report_path=report["first_loop_report"])
    atomic_json(output / "first_loop_progress.json", progress)
    return report
