import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from etf_ml.errors import ConfigurationError, IntegrityError
from etf_ml.research.memory_index import ResearchMemoryIndex
from etf_ml.research.research_memory import build_card
from etf_ml.research.reuse_store import ReuseStore
from etf_ml.research.search_policy import decide_admission
from etf_ml.utils import atomic_json, content_hash, file_hash, source_hashes


def committed_evaluation(tmp_path, *, outcome="rejected"):
    artifacts = tmp_path / "artifacts"
    run = artifacts / "runs" / "old-run"
    research = run / "research"
    session = research / "sessions" / "session"
    paired = research / "paired"
    evaluation = paired / "runs" / "evaluation"
    protocol = {"snapshot_id": "snapshot", "baseline_feature_set_id": "baseline",
                "source_code_hash": "full-source", "evaluation_code_hash": "engine-v1",
                "research": {"seeds": [42]}, "validation": {"folds": [{"name": "A"}]}}
    pid = content_hash(protocol)
    atomic_json(evaluation / "protocol.json", {**protocol, "protocol_id": pid})
    reports = {}
    for kind in ("baseline", "candidate"):
        child = paired / "experiments" / kind
        model = research / "executions" / kind / "artifacts" / "models" / "model"
        model.mkdir(parents=True)
        (model / "bundle.pkl").write_bytes(b"model weights")
        atomic_json(model / "manifest.json", {"model_id": kind, "bundle_sha256": file_hash(model / "bundle.pkl")})
        child.mkdir(parents=True)
        (child / "baseline_features.parquet").write_bytes(b"x" * (1024 * 1024))
        atomic_json(child / "manifest.json", {"files": source_hashes(child)})
        report = {"snapshot_id": "snapshot", "protocol_id": pid,
                  "child_runs": [{"path": str(child), "manifest_hash": file_hash(child / "manifest.json")}],
                  "by_fold": [{"fold": "A", "seed": 42, "model_id": kind, "model_path": str(model),
                               "model_manifest_hash": file_hash(model / "manifest.json"),
                               "portfolio": {"max_drawdown": .1}}]}
        path = evaluation / (kind + "_report.json")
        atomic_json(path, report)
        reports[kind] = str(path)
    atomic_json(evaluation / "evaluation.json", {"status": outcome, "protocol_id": pid, "reasons": ["risk"]})
    atomic_json(evaluation / "paired_report.json", {"status": outcome, "protocol_id": pid,
                                                   "attempted_trials": 1, "reports": reports})
    evidence = [{"path": str(path), "sha256": file_hash(path)} for path in
                [*(evaluation / (kind + "_report.json") for kind in reports),
                 evaluation / "evaluation.json", evaluation / "paired_report.json"]]
    card = build_card(hypothesis={"hypothesis": "h", "reason": "r"},
        proposal={"factor_id": "factor", "formula": "close/open", "required_fields": ["close"]},
        feedback={"status": outcome, "by_candidate": [{"status": outcome, "reasons": ["risk"]}]},
        context_hash="context", snapshot_id="snapshot", protocol_id=pid, baseline_id="baseline",
        trial_id="session:0", definition_id="definition", evaluation_id="evaluation", evaluation_evidence=evidence)
    trial = session / "trial-00000.json"
    atomic_json(trial, {"trial_index": 0, "research_card": card, "result": {"status": outcome},
                        "feedback": {"status": outcome}})
    checkpoint = session / "checkpoint.json"
    atomic_json(checkpoint, {"status": "completed", "created_at_ns": 1, "in_progress": None,
                            "trials": [{"path": trial.name, "sha256": file_hash(trial)}]})
    atomic_json(run / "status.json", {"status": "completed", "run_id": "old-run"})
    return artifacts, run, research, trial, checkpoint, card


def test_export_move_raw_and_relocate_store_still_reuses(tmp_path):
    artifacts, run, research, _, _, card = committed_evaluation(tmp_path)
    store = ReuseStore(artifacts / "reuse")
    before = source_hashes(run)
    exported = store.export_run(run, allowed_root=artifacts)
    assert source_hashes(run) == before
    package_id = exported["package_ids"][0]
    package_bytes = sum(p.stat().st_size for p in store.package_path(package_id).rglob("*") if p.is_file())
    assert package_bytes < 50_000
    memory = ResearchMemoryIndex(artifacts / "research_memory" / "v2")
    memory.register_source(research, allowed_root=artifacts)
    memory.rebuild()
    # Atomic rename simulates a raw archive. No original artifacts remain accessible.
    run.rename(tmp_path / "offline-raw")
    moved = tmp_path / "moved-reuse"
    store.root.rename(moved)
    moved_store = ReuseStore(moved)
    moved_store.index_path.unlink()
    moved_store.rebuild()
    memory = ResearchMemoryIndex(artifacts / "research_memory" / "v2", reuse_root=moved)
    memory.rebuild()
    cards = memory.query(snapshot_id="snapshot", protocol_id=card["protocol_id"], baseline_id="baseline")
    decision = decide_admission(SimpleNamespace(definition_id="definition", hard_matchable=True), cards,
        snapshot_id="snapshot", protocol_id=card["protocol_id"], baseline_id="baseline", attempted_trials=1)
    assert decision.decision == "reuse"
    assert decision.reuse_package_id == package_id
    assert moved_store.decision(package_id)["status"] == "rejected"


def test_export_refuses_uncommitted_or_changed_trial(tmp_path):
    artifacts, _, research, trial, checkpoint, _ = committed_evaluation(tmp_path)
    atomic_json(trial, {"research_card": {"status": "accepted"}})
    with pytest.raises(IntegrityError, match="checkpoint-committed"):
        ReuseStore(artifacts / "reuse").export_trial(trial, checkpoint, source=research, allowed_root=artifacts)


def test_corruption_cannot_be_hidden_by_cached_index(tmp_path):
    artifacts, run, research, _, _, card = committed_evaluation(tmp_path)
    store = ReuseStore(artifacts / "reuse")
    pid = store.export_run(run, allowed_root=artifacts)["package_ids"][0]
    memory = ResearchMemoryIndex(artifacts / "research_memory" / "v2")
    memory.register_source(research, allowed_root=artifacts)
    memory.rebuild()
    atomic_json(store.package_path(pid) / "evidence" / "evaluation.json", {"status": "accepted"})
    with pytest.raises(IntegrityError):
        memory.query(snapshot_id="snapshot", protocol_id=card["protocol_id"], baseline_id="baseline")


def test_missing_raw_evidence_only_exports_historical_feedback(tmp_path):
    artifacts, run, _, _, _, _ = committed_evaluation(tmp_path)
    (run / "research" / "paired" / "experiments" / "baseline" / "baseline_features.parquet").unlink()
    store = ReuseStore(artifacts / "reuse")
    pid = store.export_run(run, allowed_root=artifacts)["package_ids"][0]
    assert not store.load(pid)[0]["card"]["evaluation_evidence_verified"]
    with pytest.raises(IntegrityError, match="conclusion"):
        store.decision(pid)
    assert "raw_evidence_still_needed" in store.cleanup_preview(run, allowed_root=artifacts)["blockers"]


def test_cached_lookup_does_not_rebuild_or_read_original_runs(tmp_path, monkeypatch):
    artifacts, run, research, _, _, card = committed_evaluation(tmp_path)
    ReuseStore(artifacts / "reuse").export_run(run, allowed_root=artifacts)
    memory = ResearchMemoryIndex(artifacts / "research_memory" / "v2")
    memory.register_source(research, allowed_root=artifacts)
    memory.rebuild()
    monkeypatch.setattr(memory, "rebuild", lambda: pytest.fail("ordinary query rebuilt the index"))
    run.rename(tmp_path / "raw-offline")
    assert len(memory.query(snapshot_id="snapshot", protocol_id=card["protocol_id"], baseline_id="baseline")) == 1


def test_compatibility_trials_and_conflicting_verdicts(tmp_path):
    artifacts, run, _, _, _, card = committed_evaluation(tmp_path)
    store = ReuseStore(artifacts / "reuse")
    store.export_run(run, allowed_root=artifacts)
    cards = store.cards()
    identity = SimpleNamespace(definition_id="definition", hard_matchable=True)
    kwargs = dict(snapshot_id="snapshot", protocol_id=card["protocol_id"], baseline_id="baseline", attempted_trials=1)
    assert decide_admission(identity, cards, **kwargs).decision == "reuse"
    assert decide_admission(identity, cards, **{**kwargs, "attempted_trials": 2}).decision == "new"
    assert decide_admission(identity, cards, **{**kwargs, "snapshot_id": "different"}).decision == "new"
    assert decide_admission(identity, cards, **{**kwargs, "protocol_id": "new-full-code",
        "reuse_context_id": cards[0]["reuse_context_id"]}).decision == "reuse"
    conflict = {**cards[0], "economic_status": "accepted"}
    assert decide_admission(identity, cards + [conflict], **kwargs).decision == "blocked"


def test_compact_closure_needs_no_new_paid_calls_or_baseline_training(tmp_path):
    from etf_ml.research.first_loop import closure_evidence
    artifacts, run, _, _, _, _ = committed_evaluation(tmp_path)
    store = ReuseStore(artifacts / "reuse")
    pid = store.export_run(run, allowed_root=artifacts)["package_ids"][0]
    result = {"status": "reuse", "reuse_package_id": pid, "reused_evaluation_id": "evaluation"}
    report = closure_evidence({"status": "completed", "billing": {}}, [{"result": result}], {}, reuse_store=store)
    assert report["status"] == "completed"
    assert report["reused_candidates"] == 1
    assert report["llm_dispatches"] == report["baseline_fold_model_runs"] == 0
    assert report["investment_accepted"] is False
    # A forged reuse flag without the independent store cannot complete a run.
    assert closure_evidence({"status": "completed"}, [{"result": result}], {})["status"] == "incomplete"


def test_concurrent_exports_idempotent_and_index_rebuildable(tmp_path):
    artifacts, _, research, trial, checkpoint, _ = committed_evaluation(tmp_path)
    store = ReuseStore(artifacts / "reuse")
    with ThreadPoolExecutor(max_workers=3) as pool:
        ids = list(pool.map(lambda _: store.export_trial(trial, checkpoint, source=research, allowed_root=artifacts), range(3)))
    assert len(set(ids)) == 1
    store.index_path.unlink()
    assert len(store.rebuild()["packages"]) == 1


def test_query_cli_works_after_raw_removal_without_snapshot(tmp_path, monkeypatch, capsys):
    from etf_ml.cli import main
    artifacts, run, _, _, _, card = committed_evaluation(tmp_path)
    store = ReuseStore(artifacts / "reuse")
    store.export_run(run, allowed_root=artifacts)
    run.rename(tmp_path / "archived-run")
    monkeypatch.setattr("etf_ml.cli.load_snapshot", lambda *_: pytest.fail("lookup accessed snapshot"))
    assert main(["query-reuse", "--reuse-root", str(store.root), "--definition-id", "definition",
                 "--snapshot-id", "snapshot", "--baseline-id", "baseline", "--protocol-id", card["protocol_id"]]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["decision"]["decision"] == "reuse"
    assert output["external_calls"] == output["training_runs"] == 0


def test_cleanup_preview_preserves_running_and_pending(tmp_path):
    artifacts, run, _, _, checkpoint, _ = committed_evaluation(tmp_path)
    store = ReuseStore(artifacts / "reuse")
    store.export_run(run, allowed_root=artifacts)
    assert store.cleanup_preview(run, allowed_root=artifacts)["status"] == "review_required"
    atomic_json(run / "status.json", {"status": "running"})
    with pytest.raises(ConfigurationError, match="terminal"):
        store.export_run(run, allowed_root=artifacts)
    assert "run_not_terminal" in store.cleanup_preview(run, allowed_root=artifacts)["blockers"]


def test_shared_baseline_retains_numeric_artifacts_and_survives_raw_removal(tmp_path):
    from etf_ml.research.reuse_baseline import SharedBaseline
    from etf_ml.research.paired import _verify_references
    artifacts, run, _, _, _, _ = committed_evaluation(tmp_path)
    child = run / "research" / "paired" / "experiments" / "baseline"
    report = json.loads((run / "research" / "paired" / "runs" / "evaluation" / "baseline_report.json").read_text())
    numeric = child / "A" / "lightgbm"
    numeric.mkdir(parents=True)
    (numeric / "daily_returns.parquet").write_bytes(b"required numeric series")
    report["by_fold"][0]["backtest_path"] = str(numeric)
    shared = SharedBaseline(artifacts / "reuse")
    cached, reference, hit = shared.get_or_run("a" * 64, child, lambda: report)
    assert not hit
    assert not (Path(reference["path"]) / "baseline_features.parquet").exists()
    run.rename(tmp_path / "offline")
    (shared.store.root / "baseline_index" / ("a" * 64 + ".json")).unlink()
    assert shared.rebuild_index() == 1
    cached, reference, hit = shared.get_or_run("a" * 64, child, lambda: pytest.fail("duplicate baseline trained"))
    assert hit
    assert Path(cached["by_fold"][0]["backtest_path"]).joinpath("daily_returns.parquet").is_file()
    _verify_references({"baseline": {"child_runs": [reference], "by_fold": cached["by_fold"]}},
                       run / "research" / "paired", artifacts / "models", reuse_root=shared.store.root)


def test_legacy_export_retains_attempt_count_and_is_idempotent(tmp_path):
    artifacts, run, _, trial, checkpoint, _ = committed_evaluation(tmp_path)
    paired = run / "research" / "paired" / "runs" / "evaluation" / "paired_report.json"
    value = json.loads(paired.read_text())
    value.pop("attempted_trials")
    value["time_block_statistics"] = [{"attempted_trials": 3}, {"attempted_trials": 3}]
    atomic_json(paired, value)
    record = json.loads(trial.read_text())
    for item in record["research_card"]["evaluation_evidence"]:
        item["sha256"] = file_hash(Path(item["path"]))
    atomic_json(trial, record)
    state = json.loads(checkpoint.read_text())
    state["trials"][0]["sha256"] = file_hash(trial)
    atomic_json(checkpoint, state)
    store = ReuseStore(artifacts / "reuse")
    first = store.export_run(run, allowed_root=artifacts)
    assert first == store.export_run(run, allowed_root=artifacts)
    assert store.cards()[0]["evaluation_attempted_trials"] == 3


def test_legacy_protocol_hash_is_not_changed_by_optional_field(fold):
    from etf_ml.config import load_config
    from etf_ml.research.protocol import ComparisonProtocol
    config = load_config(overrides={"validation": {"folds": [fold.model_dump()]}})
    protocol = ComparisonProtocol(snapshot_id="s", baseline_feature_set_id="b",
        label=config.label, universe=config.universe, validation=config.validation,
        portfolio=config.portfolio, research=config.research,
        model=next(model for model in config.models if model.name == "lightgbm"))
    old = protocol.model_dump(mode="json")
    assert "evaluation_code_hash" not in old
    assert ComparisonProtocol.model_validate(old).protocol_id == content_hash(old)


def test_controller_reuse_after_raw_move_preserves_proposal_and_billing(tmp_path, monkeypatch):
    from etf_ml.adapters.rdagent.proposal import ETFHypothesisGen, ETFHypothesis2Experiment
    from etf_ml.adapters.rdagent.scenario import ETFFactorScenario
    from etf_ml.adapters.rdagent.coder import ETFFactorCoder
    from etf_ml.errors import DuplicateProposal
    from etf_ml.research.controller import ResearchController
    artifacts, run, _, _, _, card = committed_evaluation(tmp_path)
    store = ReuseStore(artifacts / "reuse")
    package_id = store.export_run(run, allowed_root=artifacts)["package_ids"][0]
    run.rename(tmp_path / "raw-offline")
    class Protocol(SimpleNamespace):
        def model_dump(self, **kwargs):
            return {"protocol_id": self.protocol_id}
    protocol = Protocol(protocol_id=card["protocol_id"])
    base = SimpleNamespace(feature_set_id="baseline")
    context = SimpleNamespace(context_hash="context", fields={"close": {}})
    billing = {"call_count": 2, "measured_cost": "0.01", "calls": {}}
    session = SimpleNamespace(initial_protocol=protocol, protocol=protocol,
        initial_baseline=base, baseline=base, snapshot=SimpleNamespace(snapshot_id="snapshot"),
        context=context, config=SimpleNamespace(artifact_root=artifacts, reuse_root=None,
            research=SimpleNamespace(max_trials=1)), root=artifacts / "research" / "new-run",
        memory_root=artifacts / "research_memory" / "v2", select_baseline=lambda _: None,
        build_context=lambda _: context, save=lambda _: None, bind_repair_state=lambda *args: None,
        llm=SimpleNamespace(ledger=SimpleNamespace(summary=lambda: billing)))
    def duplicate(*args):
        decision = decide_admission(SimpleNamespace(definition_id="definition", hard_matchable=True),
            store.cards(), snapshot_id="snapshot", baseline_id="baseline",
            protocol_id=protocol.protocol_id, attempted_trials=1)
        assert decision.decision == "reuse"
        error = DuplicateProposal("reuse")
        error.decision = decision.to_dict()
        error.proposal = {"factor_id": "factor", "formula": "close/open"}
        raise error
    monkeypatch.setattr(ETFFactorScenario, "get_runtime_environment", lambda _: {})
    monkeypatch.setattr(ETFHypothesisGen, "gen", lambda *args: SimpleNamespace(hypothesis="h", reason="r"))
    monkeypatch.setattr(ETFHypothesis2Experiment, "convert", duplicate)
    monkeypatch.setattr(ETFFactorCoder, "develop", lambda *args: pytest.fail("reused factor entered coding"))
    controller = ResearchController(session, "new-run")
    state = controller.run()
    assert state["status"] == "completed"
    assert state["billing"] == billing
    record = json.loads((controller.root / state["trials"][0]["path"]).read_text())
    assert record["result"]["reuse_package_id"] == package_id
    assert record["result"]["by_candidate"][0]["status"] == "rejected"
    assert record["research_card"]["factor_id"] == "factor"
    assert record["promotion_materialized"] is False
    assert "reuse_export_pending" not in state


def test_cleanup_preview_finds_cross_run_pointer(tmp_path):
    artifacts, run, _, _, _, _ = committed_evaluation(tmp_path)
    store = ReuseStore(artifacts / "reuse")
    store.export_run(run, allowed_root=artifacts)
    pointer = run.parent / "another-run" / "baseline_reference.json"
    atomic_json(pointer, {"root": str(run / "baseline")})
    result = store.cleanup_preview(run, allowed_root=artifacts)
    assert "external_run_references" in result["blockers"]
    assert result["external_references"] == [str(pointer)]


def test_trusted_worker_receives_absolute_shared_store(tmp_path, monkeypatch):
    from etf_ml.config import load_config
    from etf_ml.research.execution import execute_research
    monkeypatch.chdir(tmp_path)
    config = load_config(overrides={"artifact_root": "artifacts"})
    protocol = SimpleNamespace(model_dump=lambda **kwargs: {})
    base = SimpleNamespace(feature_set_id="base")
    session = SimpleNamespace(config=config, protocol=protocol,
        snapshot=SimpleNamespace(path=tmp_path / "snapshot"), root=tmp_path / "research",
        baseline=base, initial_baseline=base, feature_store=SimpleNamespace(root=tmp_path / "features"))
    def worker(self, argv, workspace, *args, **kwargs):
        request = json.loads(Path(argv[-1]).read_text())
        assert request["config"]["reuse_root"] == str(tmp_path / "artifacts" / "reuse")
        atomic_json(workspace / "response.json", {"status": "rejected"})
        from etf_ml.contracts import ExecutionResult
        return ExecutionResult("succeeded", 0, "", "", 0, None)
    monkeypatch.setattr("etf_ml.runtime.native.NativeBackend.run", worker)
    result = execute_research(session, SimpleNamespace(manifest={}, feature_set_id="factor"), run_id="test")
    assert result["status"] == "rejected"
