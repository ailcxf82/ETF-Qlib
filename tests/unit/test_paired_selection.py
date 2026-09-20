import copy

import pytest

from etf_ml.contracts import (LabelSpec, ModelSpec, PortfolioPolicy, ResearchPolicy,
                             UniversePolicy, ValidationSpec, FoldSpec)
from etf_ml.research.protocol import ComparisonProtocol
from etf_ml.research.selection import compare


@pytest.fixture
def protocol():
    folds = [FoldSpec(name=name, train={"start": "2020-01-01", "end": "2022-12-31"},
                      early_stop={"start": "2023-01-01", "end": "2023-06-30"},
                      selection={"start": "2023-07-01", "end": "2023-12-31"})
             for name in ["A", "B", "C"]]
    return ComparisonProtocol(
        snapshot_id="snapshot", baseline_feature_set_id="base", label=LabelSpec(),
        universe=UniversePolicy(), validation=ValidationSpec(folds=folds),
        portfolio=PortfolioPolicy(k_mode="fraction", minimum_commission=0,
                                  liquidity_mode="participation", risk_mode="max_drawdown"),
        research=ResearchPolicy(seeds=[42, 43, 44]), model=ModelSpec(),
        stress_min_excess_return=0)


def report(protocol, *, candidate=False, gain=.01):
    rows = []
    for fold in protocol.validation.folds:
        for seed in protocol.research.seeds:
            metrics = {"excess_return": .01 + (gain if candidate else 0),
                       "max_drawdown": .05, "turnover": 1., "annualized_volatility": .08,
                       "max_single_weight": .2, "max_group_weight": .4,
                       "max_unclassified_weight": 0., "mean_cash_weight": .1}
            rows.append({"fold": fold.name, "seed": seed, "model": "lightgbm",
                         "model_spec": {"name": "lightgbm", "seed": seed, "constructor": {}, "fit": {}},
                         "daily_index_hash": "days",
                         "dataset": {"fold": fold.model_dump(), "holdout_start": "2026-01-01",
                                     "counts": {"train": 50, "valid": 10, "test": 10},
                                     "sample_index_hashes": {"train": "train", "valid": "valid", "test": "test"},
                                     "evaluation_index_hash": "test", "processor_fit_index_hash": "train",
                                     "processor_kind": "lightgbm",
                                     "qlib_roles": {"valid": "early_stop", "test": "selection"}},
                         "portfolio": metrics, "cost_stress": {"2.0": copy.deepcopy(metrics)}})
    return {"status": "completed", "protocol_id": protocol.protocol_id,
            "snapshot_id": "snapshot", "feature_set_id": "new" if candidate else "base",
            "baseline_feature_set_id": "base" if candidate else None, "by_fold": rows}


def test_accept_requires_full_matrix_stress_and_ablation(protocol):
    baseline, candidate = report(protocol), report(protocol, candidate=True)
    decision = compare(baseline, candidate, protocol, run_id="one", ablation=report(protocol))
    assert decision.status == "accepted" and len(decision.paired_deltas) == 9
    assert compare(baseline, candidate, protocol, run_id="two").status == "inconclusive"
    unresolved = protocol.model_copy(update={"stress_min_excess_return": None})
    baseline, candidate = report(unresolved), report(unresolved, candidate=True)
    assert compare(baseline, candidate, unresolved, run_id="three",
                   ablation=report(unresolved)).status == "inconclusive"


@pytest.mark.parametrize("problem", ["missing_row", "duplicate_row", "sample_change", "missing_metric",
                                     "infinite_metric", "protocol_change", "stress_missing", "snapshot_change",
                                     "model_change", "date_change"])
def test_incomplete_or_unmatched_comparison_is_failed(protocol, problem):
    baseline, candidate = report(protocol), report(protocol, candidate=True)
    if problem == "missing_row":
        candidate["by_fold"].pop()
    elif problem == "duplicate_row":
        candidate["by_fold"].append(copy.deepcopy(candidate["by_fold"][0]))
    elif problem == "sample_change":
        candidate["by_fold"][0]["dataset"]["sample_index_hashes"]["train"] = "different"
    elif problem == "missing_metric":
        del candidate["by_fold"][0]["portfolio"]["turnover"]
    elif problem == "infinite_metric":
        candidate["by_fold"][0]["portfolio"]["excess_return"] = float("inf")
    elif problem == "protocol_change":
        candidate["protocol_id"] = "another"
    elif problem == "stress_missing":
        candidate["by_fold"][0]["cost_stress"] = {}
    elif problem == "snapshot_change":
        candidate["snapshot_id"] = "another"
    elif problem == "model_change":
        candidate["by_fold"][0]["model_spec"]["constructor"]["num_leaves"] = 100
    elif problem == "date_change":
        candidate["by_fold"][0]["daily_index_hash"] = "different"
    assert compare(baseline, candidate, protocol, run_id="fail", ablation=report(protocol)).status == "failed"


@pytest.mark.parametrize("violation,reason", [
    ("drawdown", "drawdown_deterioration"), ("turnover", "turnover_deterioration"),
    ("stress", "cost_pressure_return_limit"), ("seed", "seed_instability"),
    ("ablation", "ablation_not_confirmed"), ("fold", "no_majority_fold_increment")])
def test_hard_gates_cannot_be_overridden(protocol, violation, reason):
    baseline, candidate, ablation = report(protocol), report(protocol, candidate=True), report(protocol)
    if violation == "drawdown":
        candidate["by_fold"][0]["portfolio"]["max_drawdown"] = .13
    elif violation == "turnover":
        candidate["by_fold"][0]["portfolio"]["turnover"] = 1.1
    elif violation == "stress":
        candidate["by_fold"][0]["cost_stress"]["2.0"]["excess_return"] = -.01
    elif violation == "seed":
        for row in candidate["by_fold"]:
            if row["seed"] == 44:
                row["portfolio"]["excess_return"] = -.01
    elif violation == "ablation":
        ablation = report(protocol, gain=.02, candidate=True)
        ablation["feature_set_id"] = "base"
    elif violation == "fold":
        for row in candidate["by_fold"]:
            if row["fold"] in ("A", "B"):
                row["portfolio"]["excess_return"] = .005
    decision = compare(baseline, candidate, protocol, run_id="reject", ablation=ablation)
    assert decision.status == "rejected" and reason in decision.reasons


@pytest.mark.parametrize("alias", ["seed", "random_state", "random_seed"])
def test_hidden_constructor_seed_cannot_override_declared_experiment(alias):
    from etf_ml.models.registry import create_model
    from etf_ml.errors import ConfigurationError
    with pytest.raises(ConfigurationError, match="seed conflicts"):
        create_model(ModelSpec(seed=42, constructor={alias: 43}))


def test_frozen_protocol_rejects_seed_or_fold_duplicates(protocol):
    from pydantic import ValidationError
    payload = protocol.model_dump(mode="json")
    payload["research"]["seeds"] = [42, 42]
    with pytest.raises(ValidationError, match="distinct"):
        ComparisonProtocol.model_validate(payload)
    payload = protocol.model_dump(mode="json")
    payload["validation"]["folds"].append(payload["validation"]["folds"][0])
    with pytest.raises(ValidationError, match="unique"):
        ComparisonProtocol.model_validate(payload)


def test_auxiliary_window_is_bound_to_frozen_protocol_identity(protocol):
    from etf_ml.contracts import BenchmarkPolicy
    first = protocol.protocol_id
    changed = protocol.model_copy(deep=True)
    changed.benchmarks = BenchmarkPolicy(momentum_lookback=60)
    assert changed.protocol_id != first
    restored = ComparisonProtocol.model_validate_json(changed.model_dump_json())
    assert restored.benchmarks.momentum_lookback == 60
    assert restored.protocol_id == changed.protocol_id


@pytest.mark.parametrize("metric,value,reason", [
    ("max_single_weight", .35, "single_weight_limit"),
    ("max_group_weight", .7, "group_weight_limit"),
    ("max_unclassified_weight", .1, "unclassified_holding_exposure"),
])
@pytest.mark.parametrize("pressure", [False, True])
def test_actual_exposure_limits_gate_candidates_and_cost_pressure(protocol, metric, value, reason, pressure):
    protocol.portfolio.max_weight = .3
    protocol.portfolio.max_group_weight = .6
    baseline, candidate, ablation = report(protocol), report(protocol, candidate=True), report(protocol)
    target = candidate["by_fold"][0]["cost_stress"]["2.0"] if pressure else candidate["by_fold"][0]["portfolio"]
    target[metric] = value
    result = compare(baseline, candidate, protocol, run_id="exposure-gate", ablation=ablation)
    assert result.status == "rejected"
    assert ("cost_pressure_" if pressure else "") + reason in result.reasons


def test_missing_or_impossible_exposure_metrics_cannot_be_accepted(protocol):
    baseline, candidate, ablation = report(protocol), report(protocol, candidate=True), report(protocol)
    del candidate["by_fold"][0]["portfolio"]["max_single_weight"]
    assert compare(baseline, candidate, protocol, run_id="missing-exposure", ablation=ablation).status == "failed"
    candidate = report(protocol, candidate=True)
    candidate["by_fold"][0]["portfolio"]["max_group_weight"] = .1
    decision = compare(baseline, candidate, protocol, run_id="impossible-exposure", ablation=ablation)
    assert decision.status == "failed" and "inconsistent_exposure_metrics" in decision.reasons
