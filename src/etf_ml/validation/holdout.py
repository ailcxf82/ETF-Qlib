from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from etf_ml.artifacts import RunStore
from etf_ml.contracts import AppConfig
from etf_ml.data.calendar import read_calendar
from etf_ml.errors import ConfigurationError, IntegrityError, QualityError
from etf_ml.models.deployment import load_frozen_model
from etf_ml.registry.model_versions import ModelVersionRegistry
from etf_ml.research.execution import _raise_failure
from etf_ml.research.selection import _number, REQUIRED_METRICS, EXPOSURE_METRICS
from etf_ml.runtime.native import NativeBackend
from etf_ml.utils import atomic_json, code_hash, content_hash, ensure_within, file_hash, verify_files
from etf_ml.validation.usage import HoldoutUsageStore


def acceptance_reasons(portfolio, cost_stress, config, *, cost_multipliers, stress_min_excess):
    config.acceptance.require_resolved()
    config.portfolio.require_resolved()
    if set(cost_stress) != {str(m) for m in cost_multipliers}:
        raise QualityError("Independent acceptance cost-pressure matrix incomplete")
    reasons = []
    criteria = config.acceptance
    policy = config.portfolio
    for scenario, metrics in [("base", portfolio), *cost_stress.items()]:
        keys = (*REQUIRED_METRICS, *EXPOSURE_METRICS, "net_return", "benchmark_return", "execution_cost_over_initial_equity")
        if any(not _number(metrics.get(k)) for k in keys):
            raise QualityError("Independent acceptance metrics missing or nonfinite")
        n = metrics.get("effective_dates")
        if (not isinstance(n, int) or isinstance(n, bool) or n <= 0 or metrics.get("accounting_reconciled") is not True or
                not 0 <= metrics["max_drawdown"] <= 1 or metrics["annualized_volatility"] < 0 or
                metrics["execution_cost_over_initial_equity"] < 0 or metrics["turnover"] < 0 or
                metrics["net_return"] <= -1 or metrics["benchmark_return"] <= -1 or
                any(not 0 <= metrics[k] <= 1 + 1e-8 for k in EXPOSURE_METRICS) or
                metrics["max_group_weight"] + 1e-8 < max(metrics["max_single_weight"], metrics["max_unclassified_weight"]) or
                abs(metrics["excess_return"] - (metrics["net_return"] - metrics["benchmark_return"])) > 1e-8):
            raise QualityError("Independent acceptance ledger or metric relationships invalid")
        prefix = scenario + ":"
        if n < criteria.minimum_effective_dates: reasons.append(prefix + "insufficient_effective_dates")
        if metrics["max_drawdown"] > criteria.maximum_drawdown + 1e-12: reasons.append(prefix + "drawdown_threshold")
        if metrics["annualized_volatility"] > criteria.maximum_annualized_volatility + 1e-12: reasons.append(prefix + "volatility_threshold")
        if metrics["execution_cost_over_initial_equity"] > criteria.maximum_execution_cost_over_initial_equity + 1e-12:
            reasons.append(prefix + "execution_cost_threshold")
        risk_key = "max_drawdown" if policy.risk_mode == "max_drawdown" else "annualized_volatility"
        risk_limit = policy.max_drawdown_limit if policy.risk_mode == "max_drawdown" else policy.risk
        if metrics[risk_key] > risk_limit + 1e-12: reasons.append(prefix + "portfolio_risk_limit")
        cap = min(policy.k, policy.max_weight) if policy.k_mode == "weight_cap" else policy.max_weight
        if metrics["max_single_weight"] > cap + 1e-8: reasons.append(prefix + "single_weight_limit")
        if metrics["max_group_weight"] > policy.max_group_weight + 1e-8: reasons.append(prefix + "group_weight_limit")
        if metrics["max_unclassified_weight"] > 1e-12: reasons.append(prefix + "unclassified_holding")
        if scenario == "base":
            if metrics["net_return"] < criteria.minimum_net_return: reasons.append(prefix + "net_return_threshold")
            if metrics["excess_return"] < criteria.minimum_excess_return: reasons.append(prefix + "excess_return_threshold")
        elif stress_min_excess is None or metrics["excess_return"] < stress_min_excess:
            reasons.append(prefix + "cost_pressure_return_threshold")
    return sorted(set(reasons))


def require_access_review(config, package, access_audit):
    from etf_ml.research.qualification import holdout_qualification, read_object, evidence
    manifest_path = Path(package["snapshot_path"]) / "snapshot_manifest.json"
    manifest = read_object(manifest_path)
    if manifest.get("snapshot_id") != package["snapshot_id"]:
        raise IntegrityError("Holdout review snapshot differs from frozen model")
    result = holdout_qualification(config.validation.model_dump(mode="json"),
        access_audit=access_audit, snapshot_holdout_start=manifest["spec"]["holdout_start"],
        snapshot_id=manifest["snapshot_id"], snapshot_end=manifest["cutoff"],
        snapshot_manifest_sha256=file_hash(manifest_path), require_v2=True)
    if result["status"] != "eligible":
        raise ConfigurationError("Independent holdout access review required: " + ",".join(result["reason_codes"]))
    return evidence(access_audit)


def run_holdout(config, package_path, output, *, run_id, access_audit=None, access_audit_sha256=None):
    """Trusted worker: inference and ETF accounting only; never fit a model."""
    bundle, reference, package = load_frozen_model(package_path, config=config)
    if not config.validation.holdout_independent:
        raise ConfigurationError("Independent holdout use has not been confirmed")
    review = require_access_review(config, package, access_audit)
    if review["sha256"] != access_audit_sha256:
        raise IntegrityError("Holdout access review changed before worker execution")
    from etf_ml.models.frozen_features import materialize_frozen_features
    from etf_ml.models import predict
    from etf_ml.data.source import encode_provider
    from etf_ml.datasets.labels import generate_labels
    from etf_ml.backtest.metrics import predictive_metrics
    from etf_ml.backtest.results import evaluate_with_stress
    from etf_ml.models.recorders import storage_root
    output = Path(output)
    features, panel, snapshot, feature_manifest = materialize_frozen_features(
        package_path, reference, package, package["snapshot_path"], output / "inference", purpose="independent_holdout")
    calendar = read_calendar(snapshot.path / "calendar.txt")
    boundary = pd.Timestamp(config.validation.holdout_start)
    days = calendar[calendar >= boundary]
    if not len(days) or not (calendar < boundary).any():
        raise QualityError("Independent holdout needs covered dates and historical warmup")
    universe = pd.read_parquet(snapshot.path / "universe.parquet").reindex(panel.index)
    benchmark = pd.read_parquet(snapshot.path / "benchmark.parquet")
    events = pd.read_parquet(snapshot.path / "events.parquet")
    from etf_ml.data.income_supplement import snapshot_income
    income, income_rules = snapshot_income(snapshot, "holdout")
    provider = output / "provider"
    encoded = pd.DataFrame(index=panel.index)
    for field in ("open", "high", "low", "close"): encoded[field] = panel["adj_" + field]
    encoded["factor"] = panel.adjustment_factor
    encoded["volume"] = panel.volume_shares / panel.adjustment_factor
    encoded["amount"] = panel.amount_currency
    encoded["change"] = panel.return_1d
    schedule = read_calendar(snapshot.path / "execution_calendar.txt")
    encode_provider(encoded, calendar, provider, {c: c for c in encoded}, future_calendar=schedule)
    scores = predict(bundle, features)
    scores.to_frame().to_parquet(output / "predictions.parquet")
    recorder = storage_root(config.artifact_root) / "holdout" / content_hash({"version_id": package["version_id"], "run_id": run_id})[:24]
    recorder.mkdir(parents=True, exist_ok=True)
    base, stress = evaluate_with_stress(scores, config.portfolio, panel, output=output / "portfolio",
        cost_multipliers=tuple(package["protocol"]["cost_multipliers"]), universe=universe,
        calendar=calendar, benchmark=benchmark, provider=provider, recorder_uri=recorder,
        start_time=days[0], end_time=days[-1], events=events, schedule_calendar=schedule,
        income=income, income_rules=income_rules,
        annualization_days=config.validation.annualization_days, risk_free_rate=config.validation.risk_free_rate)
    if not base.daily_returns.index.equals(days):
        raise QualityError("Independent holdout daily returns changed the covered interval")
    for multiplier in package["protocol"]["cost_multipliers"]:
        pressure_dates = pd.read_parquet(output / "portfolio" / ("cost-" + str(multiplier)) / "daily_returns.parquet").index
        if not pressure_dates.equals(days): raise QualityError("Independent cost pressure changed evaluation dates")
    labels, label_events = generate_labels(panel, calendar, config.label)
    mature = (panel.index.get_level_values("datetime") >= boundary) & universe.eligible & labels.notna() & (
        label_events.available_time <= days[-1] + pd.Timedelta(hours=16))
    index = panel.index[mature]
    predictive = predictive_metrics(scores.loc[index], labels.loc[index])
    label_events.loc[index].to_parquet(output / "label_events.parquet")
    from etf_ml.research.statistics import paired_block_uncertainty
    from etf_ml.backtest.metrics import portfolio_metrics
    benchmark_returns = (benchmark.close / benchmark.close.shift(1) - 1).loc[days]
    stability = paired_block_uncertainty(benchmark_returns, base.daily_returns,
        block_length=package["protocol"]["time_block_length"],
        repetitions=package["protocol"]["bootstrap_repetitions"], seed=package["protocol"]["bootstrap_seed"])
    stability["note"] = "Independent time-block diagnostic; short periods may be inconclusive and do not establish investment effectiveness"
    by_month = [{"month": str(month), **portfolio_metrics(values, benchmark_returns.loc[values.index],
        annualization_days=config.validation.annualization_days, risk_free_rate=config.validation.risk_free_rate)}
        for month, values in base.daily_returns.groupby(base.daily_returns.index.to_period("M"))]
    reasons = acceptance_reasons(base.metrics, stress, config, cost_multipliers=package["protocol"]["cost_multipliers"],
                                 stress_min_excess=package["protocol"]["stress_min_excess_return"])
    result = {"status": "failed" if reasons else "passed", "stage": "independent_holdout", "run_id": run_id,
              "version_id": package["version_id"], "model_id": package["model_id"],
              "feature_set_id": package["feature_set_id"], "snapshot_id": snapshot.snapshot_id,
              "protocol_id": package["protocol_id"], "acceptance": config.acceptance.model_dump(mode="json"),
              "portfolio": base.metrics, "cost_stress": stress, "predictive": predictive,
              "stability": stability, "by_month": by_month,
              "reasons": reasons, "start": str(days[0].date()), "end": str(days[-1].date()),
              "daily_index_hash": content_hash([str(d) for d in days]),
              "evaluation_index_hash": content_hash([[str(t), i] for t, i in index]),
              "inference_manifest": feature_manifest, "model_fit_performed": False,
              "access_audit": review,
              "note": "Independent frozen-candidate evaluation; results must never enter agent research feedback"}
    atomic_json(output / "holdout_report.json", result)
    return result


def _verify_result(result, package, config):
    for key in ("version_id", "model_id", "feature_set_id", "snapshot_id", "protocol_id", "acceptance"):
        if result.get(key) != package[key]: raise IntegrityError("Independent result identity changed: " + key)
    if result.get("stage") != "independent_holdout" or result.get("model_fit_performed") is not False:
        raise IntegrityError("Independent result performed an unexpected research stage")
    reasons = acceptance_reasons(result["portfolio"], result["cost_stress"], config,
        cost_multipliers=package["protocol"]["cost_multipliers"], stress_min_excess=package["protocol"]["stress_min_excess_return"])
    if result["reasons"] != reasons or result["status"] != ("failed" if reasons else "passed"):
        raise IntegrityError("Independent result bypassed the frozen acceptance thresholds")
    from etf_ml.data.calendar import read_calendar
    days = read_calendar(Path(package["snapshot_path"]) / "calendar.txt")
    days = days[days >= pd.Timestamp(config.validation.holdout_start)]
    if (not len(days) or result.get("start") != str(days[0].date()) or result.get("end") != str(days[-1].date()) or
            result.get("daily_index_hash") != content_hash([str(d) for d in days]) or
            any(m["effective_dates"] != len(days) for m in [result["portfolio"], *result["cost_stress"].values()]) or
            result.get("inference_manifest", {}).get("historical_feature_parity") != "passed"):
        raise IntegrityError("Independent result changed the frozen evaluation dates or feature parity")


def evaluate_holdout(config, package_path, *, run_id, access_audit=None):
    from etf_ml.data.diagnostic import require_formal
    require_formal(config)
    _, _, package = load_frozen_model(package_path, config=config, allow_rejected=True)
    config.acceptance.require_resolved()
    if not config.validation.holdout_independent:
        raise ConfigurationError("Independent holdout use has not been confirmed before freeze")
    review = require_access_review(config, package, access_audit)
    from etf_ml.data.snapshot import load_snapshot
    snapshot = load_snapshot(package["snapshot_path"])
    if snapshot.manifest["spec"]["holdout_start"] != config.validation.holdout_start:
        raise ConfigurationError("Frozen data and validation holdout boundaries differ")
    usage = HoldoutUsageStore(config.artifact_root / "final_acceptance" / "usage")
    identity = {"version_id": package["version_id"], "snapshot_id": snapshot.snapshot_id,
                "protocol_id": package["protocol_id"], "config_hash": config.config_hash,
                "start": config.validation.holdout_start, "end": snapshot.manifest["cutoff"]}
    claim = usage.claim(identity, run_id=run_id)
    usage.history(claim["usage_id"])
    canonical_id = claim["run_id"]
    run_identity = {"usage_id": claim["usage_id"], "identity": identity,
                    "access_audit": review,
                    "package_manifest_hash": file_hash(Path(package_path) / "manifest.json")}
    with RunStore(config.artifact_root / "final_acceptance" / "runs", canonical_id, run_identity,
                  preserve_completed_on_error=True) as run:
        if run.reused:
            result = json.loads((run.path / "holdout_report.json").read_text(encoding="utf-8"))
            _verify_result(result, package, config)
            if result.get("access_audit") != review:
                raise IntegrityError("Cached holdout access review differs")
        else:
            if package["registry_state"] != "frozen":
                raise QualityError("A decided model cannot start another holdout experiment")
            usage.record(claim["usage_id"], "started", details={"run_id": canonical_id, "identity": identity, "code_hash": code_hash()})
            try:
                request = {"mode": "holdout", "config": config.model_dump(mode="json"),
                           "access_audit": review["path"], "access_audit_sha256": review["sha256"],
                           "package_path": str(Path(package_path).resolve()), "output_root": str(run.path), "run_id": canonical_id}
                atomic_json(run.path / "pipeline_request.json", request)
                execution = NativeBackend(config.artifact_root).run(
                    [sys.executable, "-m", "etf_ml.research.experiment_worker", str(run.path / "pipeline_request.json")],
                    run.path, {}, config.research.limits, trusted=True)
                atomic_json(run.path / "execution_result.json", execution)
                if execution.status != "succeeded": _raise_failure(run.path, execution)
                result = json.loads((run.path / "holdout_report.json").read_text(encoding="utf-8"))
                _verify_result(result, package, config)
                if result.get("access_audit") != review:
                    raise IntegrityError("Worker holdout access review differs")
                run.complete(result)
            except BaseException as exc:
                usage.record(claim["usage_id"], "technical_failed", details={"identity": identity,
                    "reason": getattr(exc, "reason", "execution_failed"), "exception_type": type(exc).__name__, "code_hash": code_hash()})
                raise
        try:
            registry = ModelVersionRegistry(package["registry_root"])
            current = registry.load(package["version_id"])
            state = "accepted" if result["status"] == "passed" else "rejected"
            if current["state"] == "frozen":
                registry.transition(package["version_id"], state, evidence=run.path / "holdout_report.json", reasons=result["reasons"])
            elif current["state"] != state:
                raise IntegrityError("Independent acceptance and registry decision disagree")
            usage.record(claim["usage_id"], "completed", details={"identity": identity,
                "status": result["status"], "report_manifest_hash": file_hash(run.path / "manifest.json")})
        except BaseException as exc:
            history = usage.history(claim["usage_id"])
            if not history or history[-1]["status"] != "completed":
                usage.record(claim["usage_id"], "technical_failed", details={"identity": identity,
                    "phase": "decision_commit", "exception_type": type(exc).__name__, "code_hash": code_hash()})
            raise
        return {**result, "artifact_path": str(run.path), "reused": run.reused,
                "usage_id": claim["usage_id"], "manifest_hash": file_hash(run.path / "manifest.json")}
