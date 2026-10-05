import copy
import json

import pytest

from etf_ml.config import load_config
from etf_ml.errors import ConfigurationError, IntegrityError, QualityError
from etf_ml.validation.holdout import acceptance_reasons
from etf_ml.validation.usage import HoldoutUsageStore
from etf_ml.utils import atomic_json


@pytest.fixture
def acceptance():
    config = load_config(overrides={"portfolio": {"k_mode": "fraction", "minimum_commission": 0,
        "liquidity_mode": "participation", "risk_mode": "max_drawdown", "max_weight": .5, "max_group_weight": .7},
        "acceptance": {"minimum_net_return": .01, "minimum_excess_return": .005,
        "maximum_drawdown": .1, "maximum_annualized_volatility": .2,
        "maximum_execution_cost_over_initial_equity": .05, "minimum_effective_dates": 20}})
    metrics = {"net_return": .05, "benchmark_return": .02, "excess_return": .03, "max_drawdown": .05,
        "annualized_volatility": .1, "turnover": .3, "max_single_weight": .3, "max_group_weight": .6,
        "max_unclassified_weight": 0., "mean_cash_weight": .1, "execution_cost_over_initial_equity": .01,
        "effective_dates": 30, "accounting_reconciled": True}
    return config, metrics


def reasons(config, base, pressure):
    return acceptance_reasons(base, pressure, config, cost_multipliers=[3.], stress_min_excess=-.01)


def test_all_frozen_acceptance_limits_and_pressure_pass(acceptance):
    config, base = acceptance
    assert reasons(config, base, {"3.0": copy.deepcopy(base)}) == []


def test_holdout_acceptance_uses_drawdown_cap_not_earlier_trigger(acceptance):
    config, metrics = acceptance
    config.portfolio.risk = .08
    config.portfolio.max_drawdown_limit = .12
    config.acceptance.maximum_drawdown = .12
    metrics["max_drawdown"] = .11
    assert "base:portfolio_risk_limit" not in reasons(config, metrics, {"3.0": copy.deepcopy(metrics)})
    assert "base:drawdown_threshold" not in reasons(config, metrics, {"3.0": copy.deepcopy(metrics)})


@pytest.mark.parametrize("field,value,reason", [("max_drawdown", .11, "drawdown_threshold"),
    ("annualized_volatility", .21, "volatility_threshold"), ("execution_cost_over_initial_equity", .06, "execution_cost_threshold"),
    ("effective_dates", 19, "insufficient_effective_dates"), ("max_single_weight", .51, "single_weight_limit"),
    ("max_group_weight", .71, "group_weight_limit"), ("max_unclassified_weight", .01, "unclassified_holding")])
def test_base_and_pressure_limits_are_both_checked(acceptance, field, value, reason):
    config, base = acceptance
    pressure = copy.deepcopy(base)
    pressure[field] = value
    assert "3.0:" + reason in reasons(config, base, {"3.0": pressure})
    base[field] = value
    assert "base:" + reason in reasons(config, base, {"3.0": pressure})


def test_absolute_excess_and_cost_pressure_rejection_are_explicit(acceptance):
    config, base = acceptance
    base.update(net_return=0., excess_return=-.02)
    results = reasons(config, base, {"3.0": copy.deepcopy(base)})
    assert {"base:net_return_threshold", "base:excess_return_threshold", "3.0:cost_pressure_return_threshold"} <= set(results)


@pytest.mark.parametrize("change", [{"net_return": float("nan")}, {"accounting_reconciled": False},
    {"effective_dates": 0}, {"excess_return": .8}, {"max_group_weight": .1}, {"max_drawdown": 2.}])
def test_invalid_metrics_are_quality_failures_not_investment_rejections(acceptance, change):
    config, base = acceptance
    with pytest.raises(QualityError): reasons(config, {**base, **change}, {"3.0": base})


def test_missing_pressure_and_thresholds_fail(acceptance):
    config, base = acceptance
    with pytest.raises(QualityError): reasons(config, base, {})
    config.acceptance.minimum_net_return = None
    with pytest.raises(ConfigurationError): reasons(config, base, {"3.0": base})


@pytest.fixture
def usage(tmp_path):
    return HoldoutUsageStore(tmp_path), {"version_id": "model", "snapshot_id": "snapshot", "protocol_id": "protocol",
        "config_hash": "config", "start": "2026-01-01", "end": "2026-06-30"}


def test_project_wide_overlap_blocks_changed_candidate_snapshot_and_protocol(usage):
    store, identity = usage
    claim = store.claim(identity, run_id="first")
    assert store.claim(identity, run_id="new-retry-name") == claim
    for field in ("version_id", "snapshot_id", "protocol_id", "config_hash"):
        with pytest.raises(ConfigurationError, match="already consumed"):
            store.claim({**identity, field: "changed"}, run_id="another")
    with pytest.raises(ConfigurationError): store.claim({**identity, "start": "2026-06-30", "end": "2026-12-31"}, run_id="overlap")
    assert store.claim({**identity, "start": "2026-07-01", "end": "2026-12-31"}, run_id="new-forward")["usage_id"] != claim["usage_id"]


def test_technical_retries_preserve_history_and_completed_claim_cannot_restart(usage):
    store, identity = usage
    claim = store.claim(identity, run_id="retry")
    key = claim["usage_id"]
    for status in ("started", "technical_failed", "started", "completed"):
        store.record(key, status, details={"identity": identity, "reason": status})
    history = store.history(key)
    assert [h["status"] for h in history] == ["started", "technical_failed", "started", "completed"]
    assert history[1]["previous"] == history[0]["event_id"]
    assert store.record(key, "completed", details={"identity": identity, "reason": "completed"}) == history[-1]
    with pytest.raises(ConfigurationError): store.record(key, "started", details={})


def test_usage_claim_and_audit_history_corruption_is_rejected(usage):
    store, identity = usage
    claim = store.claim(identity, run_id="audit")
    store.record(claim["usage_id"], "started", details={})
    event = store.history(claim["usage_id"])[0]
    event_path = store.root / "history" / claim["usage_id"] / (event["event_id"] + ".json")
    atomic_json(event_path, {"status": "completed"})
    with pytest.raises(IntegrityError): store.history(claim["usage_id"])
    claim_path = store.root / "claims" / (claim["usage_id"] + ".json")
    payload = json.loads(claim_path.read_text())
    payload["identity"]["version_id"] = "changed"
    atomic_json(claim_path, payload)
    with pytest.raises(IntegrityError): store.claim(identity, run_id="audit")

def test_holdout_parser_requires_frozen_model_and_access_review():
    from etf_ml.cli import parser
    with pytest.raises(SystemExit):
        parser().parse_args(["evaluate-holdout", "--frozen-model", "frozen-package"])
    parsed = parser().parse_args(["evaluate-holdout", "--frozen-model", "frozen-package",
                                 "--holdout-access-audit", "review.json"])
    assert str(parsed.frozen_model) == "frozen-package"
    assert str(parsed.holdout_access_audit) == "review.json"
    assert not hasattr(parsed, "frozen_features")

def test_unconfirmed_independence_fails_before_usage_or_execution(acceptance, tmp_path, monkeypatch):
    from etf_ml.validation import holdout
    config, _ = acceptance
    config.artifact_root = tmp_path
    monkeypatch.setattr(holdout, "load_frozen_model", lambda *args, **kwargs: (None, None, {}))
    with pytest.raises(ConfigurationError, match="not been confirmed"):
        holdout.evaluate_holdout(config, tmp_path / "package", run_id="not-independent")
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("change", [{"version_id": "wrong"}, {"model_fit_performed": True}, {"status": "failed"}])
def test_independent_result_cannot_bypass_identity_inference_or_thresholds(acceptance, change):
    from etf_ml.validation.holdout import _verify_result
    config, base = acceptance
    package = {"version_id": "version", "model_id": "model", "feature_set_id": "features",
               "snapshot_id": "snapshot", "protocol_id": "protocol", "acceptance": config.acceptance.model_dump(mode="json"),
               "protocol": {"cost_multipliers": [3.], "stress_min_excess_return": -.01}}
    result = {**{k: package[k] for k in ("version_id", "model_id", "feature_set_id", "snapshot_id", "protocol_id", "acceptance")},
              "stage": "independent_holdout", "model_fit_performed": False, "status": "passed", "reasons": [],
              "portfolio": base, "cost_stress": {"3.0": copy.deepcopy(base)}, **change}
    with pytest.raises(IntegrityError): _verify_result(result, package, config)
