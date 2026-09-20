import json

import pandas as pd
import pytest

from etf_ml.config import load_config
from etf_ml.contracts import ModelSpec, UniversePolicy, RuntimeLimits
from etf_ml.data.snapshot import build_snapshot
from etf_ml.research.context import FactorSpec
from etf_ml.research.execution import execute_research
from etf_ml.research.paired import combine_features
from etf_ml.research.protocol import ComparisonProtocol
from etf_ml.research.session import ResearchSession
from etf_ml.features.baseline import materialize
from etf_ml.utils import atomic_json

pytestmark = [pytest.mark.qlib, pytest.mark.docker]


def test_accumulated_session_worker_trains_all_features_and_removes_entire_group(
        source_spec, fold, tmp_path):
    source_spec.holdout_start = "2026-01-01"
    config = load_config(overrides={
        "artifact_root": tmp_path / "a",
        "portfolio": {"k_mode": "fraction", "minimum_commission": 0,
                      "liquidity_mode": "participation", "risk_mode": "max_drawdown"},
        "validation": {"folds": [fold.model_dump()]},
        "research": {"budget_mode": "free_only", "seeds": [42, 43]},
    })
    config.data = source_spec
    config.universe = UniversePolicy(minimum_listing_days=0, liquidity_lookback=1)
    config.models = [ModelSpec(name="lightgbm", constructor={
        "num_boost_round": 8, "early_stopping_rounds": 3, "min_data_in_leaf": 5,
        "num_threads": 1}, fit={"verbose_eval": 0})]
    config.research.limits = RuntimeLimits(
        image="rdagent-qlib@sha256:97e456451ae9b3aa7c74456cf76afa2a6fd336b7b7cc96c5b98416a4de0bc373",
        timeout_seconds=180, memory_mb=1024)
    snapshot = build_snapshot(source_spec.source, source_spec, config.universe)
    panel = pd.read_parquet(snapshot.path / "research" / "panel.parquet")
    base = materialize({"snapshot_id": snapshot.snapshot_id}, panel)
    protocol = ComparisonProtocol(
        snapshot_id=snapshot.snapshot_id, baseline_feature_set_id=base.feature_set_id,
        label=config.label, universe=config.universe, validation=config.validation,
        portfolio=config.portfolio, research=config.research, model=config.models[0],
        stress_min_excess_return=-1)
    session = ResearchSession(config, snapshot.path, protocol, root=tmp_path / "a" / "research" / "accumulated")

    def factor(name, lag):
        spec = FactorSpec(factor_id=name, hypothesis="trend", formula="close/lag(close)-1",
                          required_fields=["adj_close"], lookback=lag+1, minimum_observations=lag+1,
                          research_group="trend", expected_difference="a distinct trend window",
                          source="def compute(panel):\n    close = panel['adj_close']\n    return (close / close.groupby(level='instrument').shift(" + str(lag) + ") - 1).to_frame('factor')\n",
                          context_hash=session.context.context_hash)
        artifact = session.engine.materialize(spec, session.context, snapshot.path / "research",
                                               eligibility=session.eligibility)
        return spec, artifact

    first_spec, first = factor("momentum_three", 3)
    combined = combine_features(base, first, protocol)
    session.registry.register(first_spec, lineage={"protocol_id": protocol.protocol_id})
    quality = session.root / "synthetic_quality.json"
    atomic_json(quality, {"status": "passed", "factor_version_id": first_spec.version_id})
    session.registry.transition(first_spec.factor_id, 1, "validated", evidence={"quality": quality})
    # Explicitly curated synthetic acceptance exercises subsequent composition
    # execution. This is not evidence of first-factor financial acceptance.
    proof = session.root / "synthetic_acceptance.json"
    atomic_json(proof, {"status": "accepted", "factor_version_id": first_spec.version_id,
                       "protocol_id": protocol.protocol_id, "baseline_id": base.feature_set_id,
                       "candidate_id": combined.feature_set_id})
    session.registry.transition(first_spec.factor_id, 1, "evaluated", evidence={"evaluation": proof})
    session.registry.transition(first_spec.factor_id, 1, "candidate", evidence={"evaluation": proof})
    selected = session.feature_store.publish(base, first, first_spec, protocol)
    session.select_baseline(selected.feature_set_id)
    session.context = session.build_context()
    assert "momentum_three_v1" in session.context.existing_features
    assert session.context.selection_rules["candidate_groups"] == {"momentum_three_v1": "trend"}
    session.save(session.root / "session.json")
    restored = ResearchSession.from_file(session.root / "session.json")
    assert restored.protocol.protocol_id == session.protocol.protocol_id
    pd.testing.assert_frame_equal(restored.baseline.frame, session.baseline.frame)

    second_spec, second = factor("momentum_four", 4)
    result = execute_research(session, second, run_id="accumulated-pair")
    assert result["status"] in ("accepted", "rejected", "inconclusive"), result
    assert result["group_ablation"]["group"] == "trend"
    assert result["group_ablation"]["removed_columns"] == ["momentum_four_v1", "momentum_three_v1"]
    assert result["group_ablation"]["removed_feature_set_id"] == base.feature_set_id
    assert "group_ablation" in result["reports"]
    reports = {name: json.loads(__import__("pathlib").Path(path).read_text())
               for name, path in result["reports"].items()}
    expected = {"baseline": 21, "candidate": 22, "ablation": 21, "group_ablation": 20}
    for kind, report in reports.items():
        assert len(report["by_fold"]) == 2
        for row in report["by_fold"]:
            model_manifest = json.loads((__import__("pathlib").Path(row["model_path"]) / "manifest.json").read_text())
            assert len(model_manifest["feature_names"]) == expected[kind]
            assert row["dataset"]["sample_index_hashes"] == reports["baseline"]["by_fold"][0]["dataset"]["sample_index_hashes"]
            assert row["cost_stress"]["2.0"]["accounting_reconciled"]
    manifests = {p: p.stat().st_mtime_ns
                 for p in (session.root / "paired" / "experiments").glob("*/manifest.json")}
    assert len(manifests) == 8
    assert execute_research(restored, second, run_id="accumulated-pair") == result
    assert manifests == {p: p.stat().st_mtime_ns for p in manifests}
