"""Preparation resolves numbers without converting absent evidence into a pass."""
import copy
from pathlib import Path

import pytest

from etf_ml.config import load_config
from etf_ml.errors import ConfigurationError, QualityError
from etf_ml.research.qualification import read_object
from etf_ml.validation.holdout import acceptance_reasons, evaluate_holdout


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/data/tushare_formal_w08_preparation.yaml"


@pytest.fixture
def preparation():
    config = load_config(CONFIG)
    metrics = {"net_return": .03, "benchmark_return": .02, "excess_return": .01,
        "max_drawdown": .12, "annualized_volatility": .15, "turnover": .3,
        "max_single_weight": .3, "max_group_weight": .6, "max_unclassified_weight": 0.,
        "mean_cash_weight": .1, "execution_cost_over_initial_equity": .02,
        "effective_dates": 252, "accounting_reconciled": True}
    return config, metrics


def judge(config, base, stress):
    return acceptance_reasons(base, {"2.0": stress}, config,
        cost_multipliers=[2.], stress_min_excess=config.research.stress_min_excess_return)


def test_only_acceptance_values_change_and_historical_config_stays_unresolved(preparation):
    config, _ = preparation
    old = load_config(ROOT / "configs/data/tushare_formal_first_loop.yaml")
    config.acceptance.require_resolved()
    assert config.acceptance.model_dump() == {
        "minimum_net_return": .03, "minimum_excess_return": .01,
        "maximum_drawdown": .12, "maximum_annualized_volatility": .15,
        "maximum_execution_cost_over_initial_equity": .02, "minimum_effective_dates": 252}
    assert all(value is None for value in old.acceptance.model_dump().values())
    assert config.model_dump(exclude={"acceptance"}) == old.model_dump(exclude={"acceptance"})
    assert config.config_hash != old.config_hash
    assert config.validation.holdout_independent is False


def test_preparation_is_not_an_access_attestation_or_execution_authority(preparation, tmp_path, monkeypatch):
    config, _ = preparation
    plan = read_object(ROOT / "configs/research/w08-independent-confirmation-plan.json")
    assert plan["status"] == "prepared_validation_deferred"
    assert plan["historical_window"]["access_history"] == "unknown"
    assert plan["missing_evidence_policy"]["treat_skipped_as_passed"] is False
    assert plan["future_confirmation"]["status"] == "not_registered"
    assert plan["future_confirmation"]["start"] is None
    config.artifact_root = tmp_path
    monkeypatch.setattr("etf_ml.validation.holdout.load_frozen_model", lambda *a, **k: (None, None, {}))
    with pytest.raises(ConfigurationError, match="not been confirmed"):
        evaluate_holdout(config, tmp_path / "model", run_id="must-not-run")
    assert not list(tmp_path.rglob("*.json"))


def test_frozen_acceptance_boundaries_and_pressure_are_distinct(preparation):
    config, base = preparation
    stress = {**base, "net_return": -.01, "excess_return": -.03}
    assert judge(config, base, stress) == []
    stress.update(net_return=-.010001, excess_return=-.030001)
    assert judge(config, base, stress) == ["2.0:cost_pressure_return_threshold"]


@pytest.mark.parametrize("field,value,reason", [
    ("max_drawdown", .12001, "drawdown_threshold"),
    ("annualized_volatility", .15001, "volatility_threshold"),
    ("execution_cost_over_initial_equity", .02001, "execution_cost_threshold"),
    ("effective_dates", 251, "insufficient_effective_dates")])
def test_each_guard_applies_to_base_and_pressure(preparation, field, value, reason):
    config, base = preparation
    changed = {**base, field: value}
    assert "base:" + reason in judge(config, changed, base)
    assert "2.0:" + reason in judge(config, base, changed)


@pytest.mark.parametrize("net,benchmark,expected", [
    (.029, .0, "base:net_return_threshold"),
    (.03, .021, "base:excess_return_threshold")])
def test_cumulative_net_and_excess_hurdles_cannot_substitute_for_each_other(preparation, net, benchmark, expected):
    config, base = preparation
    changed = {**base, "net_return": net, "benchmark_return": benchmark, "excess_return": net - benchmark}
    assert expected in judge(config, changed, base)


@pytest.mark.parametrize("value", [None, float("nan")])
def test_missing_real_metrics_are_quality_failures_never_filled_with_defaults(preparation, value):
    config, base = preparation
    changed = copy.deepcopy(base)
    changed["annualized_volatility"] = value
    with pytest.raises(QualityError, match="missing or nonfinite"):
        judge(config, changed, base)
