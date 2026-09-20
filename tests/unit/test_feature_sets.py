import json
from types import SimpleNamespace

import pandas as pd
import pytest

from etf_ml.config import load_config
from etf_ml.contracts import FeatureArtifact
from etf_ml.errors import IntegrityError, QualityError
from etf_ml.features.baseline import materialize
from etf_ml.registry import FactorRegistry
from etf_ml.research.context import FactorSpec
from etf_ml.research.feature_sets import (
    FeatureSetStore, remove_candidate_group, group_ablation_evaluation,
)
from etf_ml.research.paired import combine_features
from etf_ml.research.protocol import ComparisonProtocol
from etf_ml.research.session import ResearchSession
from etf_ml.utils import atomic_json, file_hash


@pytest.fixture
def setup(panel, fold, tmp_path):
    base = materialize({"snapshot_id": "snapshot"}, panel)
    config = load_config(overrides={
        "portfolio": {"k_mode": "fraction", "minimum_commission": 0,
                      "liquidity_mode": "participation", "risk_mode": "max_drawdown"},
        "validation": {"folds": [fold.model_dump()]},
        "research": {"budget_mode": "free_only", "seeds": [42, 43]},
    })
    protocol = ComparisonProtocol(
        snapshot_id="snapshot", baseline_feature_set_id=base.feature_set_id,
        label=config.label, universe=config.universe, validation=config.validation,
        portfolio=config.portfolio, research=config.research, model=config.models[1],
        stress_min_excess_return=-1)
    registry = FactorRegistry(tmp_path / "registry")
    store = FeatureSetStore(tmp_path / "feature_sets", registry)

    def factor(baseline=base, current=protocol, name="trend", group="trend", accepted=True):
        # Synthetic accepted records test composition governance; no investment
        # outcome or actual factor-engine acceptance is claimed by these fixtures.
        spec = FactorSpec(factor_id=name, hypothesis="trend", formula="return_3",
                          required_fields=["adj_close"], lookback=4, minimum_observations=4,
                          expected_difference="distinct window", research_group=group,
                          source="def compute(panel): return panel", context_hash="context")
        cache = tmp_path / "factors" / name
        cache.mkdir(parents=True, exist_ok=True)
        frame = panel.adj_close.rename(name + "_v1").to_frame()
        frame.to_parquet(cache / "result.parquet")
        manifest = {"feature_set_id": spec.version_id, "snapshot_id": "snapshot",
                    "protocol_id": current.protocol_id, "path": str(cache),
                    "result_hash": file_hash(cache / "result.parquet"),
                    "source_hash": spec.source_hash, "context_hash": spec.context_hash,
                    "research_group": group, "quality": {"coverage": 1.},
                    "checks": {"status": "passed"}}
        atomic_json(cache / "feature_manifest.json", manifest)
        artifact = FeatureArtifact(spec.version_id, frame, manifest)
        combined = combine_features(baseline, artifact, current)
        registry.register(spec, lineage={"protocol_id": current.protocol_id})
        quality = cache / "quality.json"
        atomic_json(quality, {"status": "passed", "factor_version_id": spec.version_id})
        registry.transition(name, 1, "validated", evidence={"quality": quality})
        if accepted:
            proof = cache / "evaluation.json"
            atomic_json(proof, {"status": "accepted", "protocol_id": current.protocol_id,
                                "factor_version_id": spec.version_id,
                                "baseline_id": baseline.feature_set_id,
                                "candidate_id": combined.feature_set_id})
            registry.transition(name, 1, "evaluated", evidence={"evaluation": proof})
            registry.transition(name, 1, "candidate", evidence={"evaluation": proof})
        return spec, artifact

    return base, protocol, registry, store, factor


def advance(protocol, baseline):
    payload = protocol.model_dump(mode="json")
    payload["baseline_feature_set_id"] = baseline.feature_set_id
    return ComparisonProtocol.model_validate(payload)


def test_two_immutable_compositions_reuse_and_complete_lineage(setup):
    base, protocol, registry, store, factor = setup
    first_spec, first = factor()
    selected = store.publish(base, first, first_spec, protocol)
    assert selected.frame.shape[1] == 21
    again = store.publish(base, first, first_spec, protocol)
    assert again.feature_set_id == selected.feature_set_id
    pd.testing.assert_frame_equal(selected.frame, again.frame)
    active = advance(protocol, selected)
    second_spec, second = factor(selected, active, name="range", group="range")
    accumulated = store.publish(selected, second, second_spec, active)
    assert accumulated.frame.shape[1] == 22
    assert accumulated.manifest["factor_version_ids"] == [first_spec.version_id, second_spec.version_id]
    assert accumulated.manifest["composition"]["parent_feature_set_id"] == selected.feature_set_id
    assert accumulated.manifest["composition"]["base_feature_set_id"] == base.feature_set_id
    assert accumulated.frame.index.equals(base.frame.index)
    assert registry.load("trend", 1)["state"] == "candidate"


def test_only_committed_accepted_candidates_can_accumulate(setup):
    base, protocol, registry, store, factor = setup
    spec, artifact = factor(accepted=False)
    with pytest.raises(QualityError, match="accepted registered"):
        store.publish(base, artifact, spec, protocol)


def test_corrupt_composition_and_in_memory_rewrite_cannot_restore(setup):
    base, protocol, registry, store, factor = setup
    spec, artifact = factor()
    selected = store.publish(base, artifact, spec, protocol)
    changed = selected.frame.copy()
    changed.iloc[0, -1] = 1234.
    with pytest.raises(IntegrityError, match="committed data"):
        store.verify_baseline(FeatureArtifact(selected.feature_set_id, changed, selected.manifest), base.frame)
    path = store._path(selected.feature_set_id) / "features.parquet"
    with path.open("ab") as stream:
        stream.write(b"corrupt")
    with pytest.raises(IntegrityError):
        store.load(selected.feature_set_id)


def test_retired_factor_prevents_composition_reuse(setup):
    base, protocol, registry, store, factor = setup
    spec, artifact = factor()
    selected = store.publish(base, artifact, spec, protocol)
    freeze = store.root / "freeze-fixture.json"
    atomic_json(freeze, {"factor_version_id": spec.version_id, "factor_version_ids": [spec.version_id]})
    registry.transition("trend", 1, "frozen", evidence={"feature_set": freeze})
    registry.transition("trend", 1, "retired", reasons=["source_data_corrected"])
    with pytest.raises(QualityError, match="active accepted"):
        store.load(selected.feature_set_id)


def test_manifest_group_rewrite_fails_before_composition(setup):
    base, protocol, registry, store, factor = setup
    spec, artifact = factor()
    manifest = {**artifact.manifest, "research_group": "other"}
    with pytest.raises(QualityError, match="validated cache"):
        store.publish(base, FeatureArtifact(artifact.feature_set_id, artifact.frame, manifest), spec, protocol)


def test_same_group_ablation_removes_prior_and_new_factors(setup):
    base, protocol, registry, store, factor = setup
    spec, first = factor()
    selected = store.publish(base, first, spec, protocol)
    active = advance(protocol, selected)
    second_spec, second = factor(selected, active, name="trend_other")
    combined = combine_features(selected, second, active)
    removed, group, columns = remove_candidate_group(selected, combined, second, active)
    assert group == "trend" and columns == ["trend_other_v1", "trend_v1"]
    assert removed.feature_set_id == base.feature_set_id
    pd.testing.assert_frame_equal(removed.frame, base.frame)
    other_spec, other = factor(selected, active, name="range_other", group="range")
    different = combine_features(selected, other, active)
    removed, _, columns = remove_candidate_group(selected, different, other, active)
    assert removed.feature_set_id == selected.feature_set_id and columns == ["range_other_v1"]


def report(protocol, excess):
    rows = []
    for fold in protocol.validation.folds:
        for seed in protocol.research.seeds:
            metrics = {"excess_return": excess, "max_drawdown": .01, "turnover": .2,
                       "annualized_volatility": .03, "max_single_weight": .2,
                       "max_group_weight": .4, "max_unclassified_weight": 0., "mean_cash_weight": .1}
            dataset = {"fold": fold.model_dump(mode="json"), "holdout_start": "2026-01-01",
                       "counts": {}, "sample_index_hashes": {}, "evaluation_index_hash": "same",
                       "processor_fit_index_hash": "same", "processor_kind": "tree", "qlib_roles": {}}
            rows.append({"fold": fold.name, "seed": seed, "model": protocol.model.name,
                         "model_spec": {**protocol.model.model_dump(mode="json"), "seed": seed},
                         "daily_index_hash": "same", "dataset": dataset, "portfolio": metrics,
                         "cost_stress": {"2.0": metrics.copy()}})
    return {"status": "completed", "protocol_id": protocol.protocol_id,
            "snapshot_id": protocol.snapshot_id, "by_fold": rows}


def test_group_gate_requires_matching_complete_multiseed_increment(setup):
    _, protocol, _, _, _ = setup
    full, removed = report(protocol, .04), report(protocol, .02)
    assert group_ablation_evaluation(full, removed, protocol)["status"] == "accepted"
    assert group_ablation_evaluation(removed, full, protocol)["status"] == "rejected"
    removed["by_fold"][0]["daily_index_hash"] = "different"
    failed = group_ablation_evaluation(full, removed, protocol)
    assert failed["status"] == "failed" and failed["reasons"] == ["group_ablation_sample_mismatch"]


def test_trial_promotes_one_deterministic_winner_with_no_joint_union(setup, panel):
    base, protocol, registry, store, factor = setup
    spec_a, factor_a = factor(name="trend_a")
    spec_b, factor_b = factor(name="trend_b")
    session = ResearchSession.__new__(ResearchSession)
    session.initial_baseline = session.baseline = base
    session.initial_protocol = session.protocol = protocol
    session.panel, session.feature_store = panel, store
    rows = [{"factor_id": name, "status": "accepted", "group_ablation": {"status": "accepted"},
             "evaluation": {"paired_deltas": [
                 {"fold": f.name, "seed": s, "excess_return": .02}
                 for f in protocol.validation.folds for s in protocol.research.seeds]}}
            for name in ("trend_b", "trend_a")]
    exp = SimpleNamespace(result={"by_candidate": rows}, sub_tasks=[
        SimpleNamespace(name="trend_b", artifact=factor_b, spec=spec_b),
        SimpleNamespace(name="trend_a", artifact=factor_a, spec=spec_a)])
    promotion = session.promote_trial(exp)
    assert promotion["factor_id"] == "trend_a"
    assert session.baseline.frame.shape[1] == 21
    assert "trend_b_v1" not in session.baseline.frame
    assert session.protocol.protocol_id != protocol.protocol_id
    assert session.protocol.model == protocol.model
    assert session.protocol.portfolio == protocol.portfolio


def test_restoring_composition_rejects_changed_learning_or_execution_rules(setup, panel):
    from etf_ml.errors import ConfigurationError
    base, protocol, registry, store, factor = setup
    spec, artifact = factor()
    selected = store.publish(base, artifact, spec, protocol)
    session = ResearchSession.__new__(ResearchSession)
    session.initial_baseline = session.baseline = base
    changed = protocol.model_dump(mode="json")
    changed["portfolio"]["commission_rate"] = .004
    session.initial_protocol = session.protocol = ComparisonProtocol.model_validate(changed)
    session.panel, session.feature_store = panel, store
    with pytest.raises(ConfigurationError, match="frozen learning or execution"):
        session.select_baseline(selected.feature_set_id)


def test_parent_cannot_be_rewritten_while_appending_new_factor(setup):
    base, protocol, registry, store, factor = setup
    spec, artifact = factor()
    selected = store.publish(base, artifact, spec, protocol)
    active = advance(protocol, selected)
    new_spec, new_artifact = factor(selected, active, name="new_trend")
    changed = selected.frame.copy()
    changed.iloc[5, -1] = 123.
    with pytest.raises(IntegrityError, match="parent differs"):
        store.publish(FeatureArtifact(selected.feature_set_id, changed, selected.manifest),
                      new_artifact, new_spec, active)


def test_initial_baseline_values_are_recomputed_from_snapshot_before_training(setup, panel):
    base, protocol, registry, store, factor = setup
    changed = base.frame.copy()
    changed.iloc[10, 0] = 123.
    false_base = FeatureArtifact(base.feature_set_id, changed, base.manifest)
    spec, artifact = factor(false_base)
    selected = store.publish(false_base, artifact, spec, protocol)
    with pytest.raises(IntegrityError, match="initial feature values"):
        store.verify_baseline(selected, panel)
