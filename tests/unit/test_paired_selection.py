import copy
import numpy as np
import pandas as pd

import pytest

from etf_ml.contracts import (LabelSpec, ModelSpec, PortfolioPolicy, ResearchPolicy,
                             UniversePolicy, ValidationSpec, FoldSpec)
from etf_ml.errors import ConfigurationError
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


def factor_signal(protocol, *, failing_fold=None):
    return [{"fold": fold.name, "training_orientation": {"direction": "positive"},
             "oriented_validation": {"ic": -.01 if fold.name == failing_fold else .01,
                                     "icir": .2, "icir_status": "available"}}
            for fold in protocol.validation.folds]


def test_accept_requires_full_matrix_stress_and_ablation(protocol):
    baseline, candidate = report(protocol), report(protocol, candidate=True)
    decision = compare(baseline, candidate, protocol, run_id="one", ablation=report(protocol),
                       factor_signal_by_fold=factor_signal(protocol))
    assert decision.status == "accepted" and len(decision.paired_deltas) == 9
    assert decision.gate_details and all(row["status"] == "passed" for row in decision.gate_details)
    assert compare(baseline, candidate, protocol, run_id="two").status == "inconclusive"
    unresolved = protocol.model_copy(update={"stress_min_excess_return": None})
    baseline, candidate = report(unresolved), report(unresolved, candidate=True)
    assert compare(baseline, candidate, unresolved, run_id="three",
                   ablation=report(unresolved)).status == "inconclusive"


def test_factor_icir_is_a_fold_level_formal_gate_without_cross_fold_averaging(protocol):
    baseline, candidate = report(protocol), report(protocol, candidate=True)
    decision = compare(baseline, candidate, protocol, run_id="ic-fold-gate",
        ablation=report(protocol), factor_signal_by_fold=factor_signal(protocol, failing_fold="B"))
    assert decision.status == "accepted"
    assert decision.factor_signal_gate["passing_folds"] == 2
    assert decision.factor_signal_gate["required_pass_folds"] == 2
    assert [(row["fold"], row["status"]) for row in decision.factor_signal_by_fold] == [
        ("A", "passed"), ("B", "failed"), ("C", "passed")]


def test_factor_icir_requires_complete_unique_fold_matrix(protocol):
    baseline, candidate = report(protocol), report(protocol, candidate=True)
    decision = compare(baseline, candidate, protocol, run_id="ic-missing-fold",
        ablation=report(protocol), factor_signal_by_fold=factor_signal(protocol)[:-1])
    assert decision.status == "failed"
    assert decision.reasons == ["factor_icir_fold_matrix_mismatch"]


def test_formal_signal_report_preserves_rank_ic_sample_sizes_and_block_intervals(protocol):
    baseline, candidate = report(protocol), report(protocol, candidate=True)
    signals = factor_signal(protocol)
    daily = [{"valid": True, "ic": float(np.sin(i / 7) * .02),
              "rank_ic": float(np.cos(i / 9) * .03),
              "cross_section": 25, "eligible_cross_section": 25}
             for i in range(120)]
    for row in signals:
        row["oriented_validation"].update({
            "rank_ic": .02, "rank_icir": .3, "effective_dates": 120,
            "total_dates": 120, "coverage": 1., "by_date": daily})
    decision = compare(baseline, candidate, protocol, run_id="signal-report",
                       ablation=report(protocol), factor_signal_by_fold=signals)
    row = decision.factor_signal_by_fold[0]
    assert row["rank_ic"] == pytest.approx(.02)
    assert row["rank_icir"] == pytest.approx(.3)
    assert row["effective_dates"] == 120
    assert row["effective_observations"] == 3000
    assert row["eligible_observations"] == 3000 and row["coverage"] == 1.
    assert row["ic_uncertainty"]["status"] == "completed"
    assert row["rank_ic_uncertainty"]["status"] == "completed"
    assert len(row["ic_uncertainty"]["mean_confidence_interval"]) == 2


def test_gate_details_identify_exact_pair_stress_and_seed_without_relaxing_gates(protocol):
    baseline, candidate = report(protocol), report(protocol, candidate=True)
    candidate["by_fold"][0]["portfolio"]["max_drawdown"] = .2
    candidate["by_fold"][0]["cost_stress"]["2.0"]["excess_return"] = -.04
    for row in candidate["by_fold"]:
        if row["seed"] == 43:
            row["portfolio"]["excess_return"] = -.01
    result = compare(baseline, candidate, protocol, run_id="details",
                     ablation=baseline, factor_signal_by_fold=factor_signal(protocol))
    failed = [row for row in result.gate_details if row["status"] == "failed"]
    risk = next(row for row in failed if row["reason"] == "absolute_risk_limit")
    assert (risk["fold"], risk["seed"], risk["cost_multiplier"]) == ("A", 42, None)
    assert risk["exceedance"] == pytest.approx(.08)
    stress = next(row for row in failed if row["reason"] == "cost_pressure_return_limit")
    assert stress["cost_multiplier"] == "2.0" and stress["exceedance"] == .04
    assert any(row["reason"] == "seed_instability" and row["seed"] == 43 for row in failed)
    assert result.status == "rejected"
    assert {row["reason"] for row in failed}.issubset(result.reasons)


@pytest.mark.parametrize("metrics,expected_status", [
    ({"ic": 0., "icir": .2, "icir_status": "available"}, "unknown"),
    ({"ic": .2, "icir": None, "icir_status": "insufficient_dates"}, "unknown"),
])
def test_factor_icir_zero_and_unavailable_never_pass(protocol, metrics, expected_status):
    baseline, candidate = report(protocol), report(protocol, candidate=True)
    signals = factor_signal(protocol, failing_fold="C")
    signals[0]["oriented_validation"] = metrics
    decision = compare(baseline, candidate, protocol, run_id="ic-strict-positive",
                       ablation=report(protocol), factor_signal_by_fold=signals)
    assert decision.status == "inconclusive"
    assert decision.factor_signal_by_fold[0]["status"] == expected_status


@pytest.fixture
def five_fold_protocol(protocol):
    payload = protocol.model_dump(mode="json")
    payload["validation"]["folds"].extend([
        {**payload["validation"]["folds"][0], "name": name} for name in ["D", "E"]])
    return ComparisonProtocol.model_validate(payload)


@pytest.mark.parametrize("passing", [0, 1, 2, 3, 4, 5])
def test_three_of_five_oriented_folds_qualify_without_averaging(five_fold_protocol, passing):
    protocol = five_fold_protocol
    signals = factor_signal(protocol)
    signals[0]["training_orientation"]["direction"] = "reverse"
    for row in signals[passing:]:
        row["oriented_validation"].update(ic=-.9, icir=-9.)
    decision = compare(report(protocol), report(protocol, candidate=True), protocol,
        run_id="three-of-five", ablation=report(protocol), factor_signal_by_fold=signals)
    assert decision.status == ("accepted" if passing >= 3 else "rejected")
    assert decision.factor_signal_gate == {
        "rule": "strict_majority", "status": "passed" if passing >= 3 else "failed",
        "fold_count": 5, "required_pass_folds": 3, "passing_folds": passing,
        "failed_folds": 5 - passing, "unknown_folds": 0}


def test_missing_signal_evidence_cannot_qualify(five_fold_protocol):
    p = five_fold_protocol
    result = compare(report(p), report(p, candidate=True), p, run_id="no-signals", ablation=report(p))
    assert result.status == "inconclusive"
    assert "factor_icir_evidence_missing" in result.reasons
    assert result.factor_signal_gate["required_pass_folds"] == 3


def test_three_signal_votes_do_not_override_risk_gate(five_fold_protocol):
    p = five_fold_protocol
    candidate = report(p, candidate=True)
    candidate["by_fold"][0]["portfolio"]["max_drawdown"] = .13
    signals = factor_signal(p)
    for row in signals[3:]:
        row["oriented_validation"].update(ic=-.1, icir=-.2)
    result = compare(report(p), candidate, p, run_id="signal-only",
                     ablation=report(p), factor_signal_by_fold=signals)
    assert result.factor_signal_gate["status"] == "passed"
    assert result.status == "rejected" and "absolute_risk_limit" in result.reasons


def test_drawdown_gate_is_independent_of_earlier_risk_trigger(five_fold_protocol):
    p = five_fold_protocol.model_copy(update={
        "portfolio": five_fold_protocol.portfolio.model_copy(update={
            "risk": .08, "max_drawdown_limit": .12})})
    candidate = report(p, candidate=True)
    candidate["by_fold"][0]["portfolio"]["max_drawdown"] = .13
    result = compare(report(p), candidate, p, run_id="independent-drawdown-gate",
        ablation=report(p), factor_signal_by_fold=factor_signal(p))
    gate = next(row for row in result.gate_details
                if row["reason"] == "absolute_risk_limit" and row["fold"] == "A")
    assert gate["threshold"] == .12
    assert gate["value"] == .13
    assert result.status == "rejected"


def test_drawdown_trigger_must_not_exceed_acceptance_limit():
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="Drawdown trigger cannot exceed"):
        PortfolioPolicy(risk_mode="max_drawdown", risk=.13, max_drawdown_limit=.12)


def test_icir_vote_rule_is_in_frozen_protocol_identity(protocol):
    from etf_ml.utils import content_hash
    payload = protocol.model_dump(mode="json")
    assert payload.pop("factor_signal_rule") == "strict_majority"
    assert content_hash(payload) != protocol.protocol_id
    assert ComparisonProtocol.model_validate_json(protocol.model_dump_json()).protocol_id == protocol.protocol_id


@pytest.mark.parametrize("change", [
    lambda p: p.model_copy(update={"snapshot_id": "different-snapshot"}),
    lambda p: p.model_copy(update={"baseline_feature_set_id": "different-baseline"}),
    lambda p: p.model_copy(update={"label": p.label.model_copy(update={"horizon": p.label.horizon + 1})}),
    lambda p: p.model_copy(update={
        "universe": p.universe.model_copy(update={"minimum_listing_days": p.universe.minimum_listing_days + 1})}),
    lambda p: p.model_copy(update={"model": p.model.model_copy(update={
        "constructor": {**p.model.constructor, "num_leaves": 17}})}),
    lambda p: p.model_copy(update={
        "research": p.research.model_copy(update={"seeds": [42, 43, 45]})}),
    lambda p: p.model_copy(update={
        "environment": {**p.environment, "identity_test": "different-runtime"}}),
    lambda p: p.model_copy(update={"source_code_hash": "0" * 64}),
])
def test_evaluation_protocol_identity_changes_with_baseline_data_model_or_runtime(protocol, change):
    assert change(protocol).protocol_id != protocol.protocol_id


@pytest.mark.parametrize("runtime_change", ["environment", "source"])
def test_frozen_protocol_rejects_replay_after_runtime_change(protocol, monkeypatch, runtime_change):
    import etf_ml.research.protocol as protocol_module

    if runtime_change == "environment":
        monkeypatch.setattr(protocol_module, "environment_manifest",
                            lambda: {**protocol.environment, "runtime_test": "changed"})
    else:
        monkeypatch.setattr(protocol_module, "code_hash", lambda: "different-source")
    with pytest.raises(ConfigurationError, match="Frozen comparison runtime changed"):
        protocol.require_runtime()


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
