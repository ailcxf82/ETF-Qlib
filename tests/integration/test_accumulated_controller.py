import json
from pathlib import Path

import pandas as pd
import pytest

from etf_ml.config import load_config
from etf_ml.contracts import FeatureArtifact, ModelSpec, UniversePolicy
from etf_ml.data.snapshot import build_snapshot
from etf_ml.features.baseline import materialize
from etf_ml.research.controller import ResearchController
from etf_ml.research.paired import combine_features
from etf_ml.research.protocol import ComparisonProtocol
from etf_ml.research.session import ResearchSession
from etf_ml.utils import atomic_json, file_hash

pytestmark = pytest.mark.qlib


def test_published_promotion_before_checkpoint_resumes_without_double_addition(
        source_spec, fold, tmp_path, monkeypatch):
    # Controlled validation/financial outcomes isolate checkpoint governance.
    # Docker and actual multi-seed training are exercised separately.
    source_spec.holdout_start = "2026-01-01"
    config = load_config(overrides={
        "artifact_root": tmp_path / "a",
        "portfolio": {"k_mode": "fraction", "minimum_commission": 0,
                      "liquidity_mode": "participation", "risk_mode": "max_drawdown"},
        "validation": {"folds": [fold.model_dump()]},
        "research": {"budget_mode": "free_only", "seeds": [42, 43], "max_trials": 2},
    })
    config.data = source_spec
    config.universe = UniversePolicy(minimum_listing_days=0, liquidity_lookback=1)
    snapshot = build_snapshot(source_spec.source, source_spec, config.universe)
    panel = pd.read_parquet(snapshot.path / "research" / "panel.parquet")
    base = materialize({"snapshot_id": snapshot.snapshot_id}, panel)
    protocol = ComparisonProtocol(
        snapshot_id=snapshot.snapshot_id, baseline_feature_set_id=base.feature_set_id,
        label=config.label, universe=config.universe, validation=config.validation,
        portfolio=config.portfolio, research=config.research, model=config.models[1],
        stress_min_excess_return=-1)
    session = ResearchSession(config, snapshot.path, protocol, root=tmp_path / "a" / "research" / "controller")
    from etf_ml.research.factor_engine import FactorEngine
    from etf_ml.adapters.rdagent.runner import ETFFactorRunner

    def controlled_materialize(self, spec, context, research_view, eligibility=None):
        spec.validate_context(context)
        cache = self.root / "validated" / spec.version_id[:32]
        cache.mkdir(parents=True, exist_ok=True)
        frame = panel.adj_close.rename(spec.factor_id + "_v1").to_frame()
        frame.to_parquet(cache / "result.parquet")
        manifest = {"feature_set_id": spec.version_id, "snapshot_id": context.snapshot_id,
                    "protocol_id": context.protocol_id, "context_hash": context.context_hash,
                    "source_hash": spec.source_hash, "research_group": spec.research_group,
                    "result_hash": file_hash(cache / "result.parquet"), "path": str(cache),
                    "quality": {"coverage": 1.}, "checks": {"status": "passed"}}
        atomic_json(cache / "feature_manifest.json", manifest)
        return FeatureArtifact(spec.version_id, frame, manifest)

    def controlled_runner(self, exp):
        rows = []
        s = self.scen.session
        for task in exp.sub_tasks:
            combined = combine_features(s.baseline, task.artifact, s.protocol)
            deltas = [{"fold": f.name, "seed": seed, "excess_return": .02,
                       "max_drawdown": 0., "turnover": 0., "annualized_volatility": 0.}
                      for f in s.protocol.validation.folds for seed in s.protocol.research.seeds]
            evaluation = {"status": "accepted", "baseline_id": s.baseline.feature_set_id,
                          "candidate_id": combined.feature_set_id,
                          "protocol_id": s.protocol.protocol_id, "factor_version_id": task.spec.version_id,
                          "paired_deltas": deltas, "reasons": ["controlled_fixture_acceptance"]}
            proof = s.root / "controlled" / (task.spec.version_id + ".json")
            atomic_json(proof, evaluation)
            current = s.registry.load(task.name, task.version)
            if current["state"] == "validated":
                s.registry.transition(task.name, task.version, "evaluated", evidence={"evaluation": proof})
                s.registry.transition(task.name, task.version, "candidate", evidence={"evaluation": proof})
            rows.append({"factor_id": task.name, "status": "accepted", "evaluation": evaluation,
                         "group_ablation": {"status": "accepted", "group": "trend",
                                            "paired_deltas": deltas}})
        exp.result = {"status": "accepted", "stage": "factor_selection",
                      "protocol_id": s.protocol.protocol_id, "by_candidate": rows}
        return exp

    from etf_ml.adapters.rdagent.scenario import ETFFactorScenario
    monkeypatch.setattr(ETFFactorScenario, "get_runtime_environment", lambda self: "controlled_governance_fixture")
    monkeypatch.setattr(FactorEngine, "materialize", controlled_materialize)
    monkeypatch.setattr(ETFFactorRunner, "develop", controlled_runner)
    common = {"hypothesis": "trend", "formula": "trend", "required_fields": ["adj_close"],
              "lookback": 4, "minimum_observations": 4, "research_group": "trend",
              "expected_difference": "a distinct window", "source": "def compute(panel): return panel"}
    replay = [{"hypothesis": "first", "factors": [{**common, "factor_id": "first_trend"}]},
              {"hypothesis": "second", "factors": [{**common, "factor_id": "second_trend"}]}]
    original_promote = session.promote_trial

    def interrupt_after_publish(exp):
        promoted = original_promote(exp)
        raise KeyboardInterrupt("published composition, checkpoint not yet committed")

    monkeypatch.setattr(session, "promote_trial", interrupt_after_publish)
    controller = ResearchController(session, "controller", replay_trials=replay)
    with pytest.raises(KeyboardInterrupt):
        controller.run()
    state = json.loads((controller.root / "checkpoint.json").read_text())
    assert state["feature_set_id"] == base.feature_set_id
    assert state["status"] == "cancelled" and not state["trials"]
    assert state["in_progress"]["phase"] == "running"
    published = {p: p.stat().st_mtime_ns for p in session.feature_store.root.glob("*/feature_manifest.json")}
    assert len(published) == 1

    restored = ResearchSession.from_file(session.root / "session.json")
    assert restored.baseline.feature_set_id == base.feature_set_id
    resumed = ResearchController(restored, "controller", replay_trials=replay).run()
    assert resumed["status"] == "completed" and len(resumed["trials"]) == 2
    assert restored.baseline.frame.shape[1] == 22
    assert resumed["billing"]["call_count"] == 6
    assert resumed["billing"]["measured_cost"] == "0"
    assert published == {p: p.stat().st_mtime_ns for p in published}
    assert resumed["feature_set_id"] == restored.baseline.feature_set_id
    entries = restored.baseline.manifest["composition"]["factors"]
    assert [e["spec"]["factor_id"] for e in entries] == ["first_trend", "second_trend"]
    assert entries[0]["spec"]["context_hash"] != entries[1]["spec"]["context_hash"]
    again = ResearchSession.from_file(session.root / "session.json")
    assert ResearchController(again, "controller", replay_trials=replay).run() == resumed
    assert again.baseline.feature_set_id == restored.baseline.feature_set_id
