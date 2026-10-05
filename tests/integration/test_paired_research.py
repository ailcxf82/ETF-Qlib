import json
import time

import pandas as pd
import pytest

from etf_ml.config import load_config
from etf_ml.contracts import ModelSpec, UniversePolicy, RuntimeLimits
from etf_ml.data.snapshot import build_snapshot
from etf_ml.errors import IntegrityError, QualityError
from etf_ml.features.baseline import materialize
from etf_ml.research.context import FactorSpec, ResearchContext
from etf_ml.research.factor_engine import FactorEngine
from etf_ml.research.paired import combine_features, run_paired
from etf_ml.research.protocol import ComparisonProtocol

pytestmark = [pytest.mark.qlib, pytest.mark.docker]


def test_actual_docker_factor_fixed_qlib_pair_stress_ablation_and_verified_reuse(
        source_spec, fold, tmp_path, monkeypatch, record_property):
    # Test-only semantics/thresholds are declared before any results are observed.
    source_spec.holdout_start = "2026-01-01"
    config = load_config(overrides={
        "artifact_root": tmp_path / "artifacts",
        "portfolio": {"k_mode": "fraction", "minimum_commission": 0,
                      "liquidity_mode": "participation", "risk_mode": "max_drawdown"},
        "validation": {"folds": [fold.model_dump()]},
        "research": {"budget_mode": "free_only", "seeds": [42, 43]},
    })
    config.research.limits = RuntimeLimits(
        image="rdagent-qlib@sha256:97e456451ae9b3aa7c74456cf76afa2a6fd336b7b7cc96c5b98416a4de0bc373",
        timeout_seconds=60, memory_mb=1024)
    config.data = source_spec
    config.universe = UniversePolicy(minimum_listing_days=0, liquidity_lookback=1)
    config.models = [ModelSpec(name="lightgbm", constructor={
        "num_boost_round": 8, "early_stopping_rounds": 3, "min_data_in_leaf": 5,
        "num_threads": 1}, fit={"verbose_eval": 0})]
    snapshot = build_snapshot(source_spec.source, source_spec, config.universe)
    panel = pd.read_parquet(snapshot.path / "research" / "panel.parquet")
    baseline = materialize({"snapshot_id": snapshot.snapshot_id}, panel)
    protocol = ComparisonProtocol(
        snapshot_id=snapshot.snapshot_id, baseline_feature_set_id=baseline.feature_set_id,
        label=config.label, universe=config.universe, validation=config.validation,
        portfolio=config.portfolio, research=config.research, model=config.models[0],
        stress_min_excess_return=-1)
    context = ResearchContext(
        snapshot_id=snapshot.snapshot_id, baseline_id=baseline.feature_set_id,
        protocol_id=protocol.protocol_id, visible_start=str(panel.index.get_level_values("datetime").min()),
        visible_end=str(panel.index.get_level_values("datetime").max()),
        fields={"adj_close": {"unit": "adjusted_price", "available_at": "after_daily_ingestion"}},
        existing_features=list(baseline.frame.columns), model=protocol.model.model_dump(),
        selection_rules={"protocol_id": protocol.protocol_id},
        runtime={"maximum_lookback": 120})
    spec = FactorSpec(
        factor_id="momentum_3", hypothesis="short trend adds a distinct window",
        formula="close / lag_3_close - 1", required_fields=["adj_close"],
        lookback=4, minimum_observations=4, expected_difference="three-day window",
        source="def compute(panel):\n    close = panel['adj_close']\n    return (close / close.groupby(level='instrument').shift(3) - 1).to_frame('factor')\n",
        context_hash=context.context_hash)
    eligible = pd.read_parquet(snapshot.path / "universe.parquet").eligible.reindex(panel.index)
    factor = FactorEngine(tmp_path / "factors", config.research).materialize(
        spec, context, snapshot.path / "research", eligibility=eligible)
    combined = combine_features(baseline, factor, protocol)
    assert combined.frame.index.equals(baseline.frame.index)
    assert combined.frame.shape[1] == baseline.frame.shape[1] + 1
    # Replacing validated values in memory is also caught before training.
    changed = factor.frame.copy()
    changed.iloc[10, 0] = 999
    from etf_ml.contracts import FeatureArtifact
    with pytest.raises(QualityError, match="validated cache"):
        combine_features(baseline, FeatureArtifact(factor.feature_set_id, changed, factor.manifest), protocol)
    root = tmp_path / "paired"
    import etf_ml.research.paired as paired_module
    original_runner = paired_module.run_baseline
    def interrupt_candidate(*args, **kwargs):
        if kwargs["run_id"].startswith("candidate-"):
            raise RuntimeError("simulated interrupted candidate")
        return original_runner(*args, **kwargs)
    monkeypatch.setattr(paired_module, "run_baseline", interrupt_candidate)
    with pytest.raises(RuntimeError, match="interrupted"):
        run_paired(config, snapshot.path, factor, protocol, root, run_id="paired-fixture")
    committed_baselines = {p: p.stat().st_mtime_ns
                           for p in (root / "experiments").glob("baseline-*/manifest.json")}
    assert len(committed_baselines) == 2
    monkeypatch.setattr(paired_module, "run_baseline", original_runner)
    result = run_paired(config, snapshot.path, factor, protocol, root, run_id="paired-fixture")
    progress_root = root / "runs" / "paired-fixture"
    progress_events = [json.loads(line) for line in (progress_root / "progress.jsonl").read_text().splitlines()]
    assert json.loads((progress_root / "progress.json").read_text())["phase"] == "finished"
    assert any(event["phase"] == "model_completed" and event["kind"] == "candidate"
               and event["fold"] == fold.name for event in progress_events)
    baseline_seed_events = [event for event in progress_events
                            if event["phase"] in {"seed_completed", "seed_reused"}
                            and event["kind"] == "baseline"]
    assert {event["seed"] for event in baseline_seed_events} == {42, 43}
    assert any(event["phase"] == "kind_reused" and event["kind"] == "ablation"
               for event in progress_events)
    assert committed_baselines == {p: p.stat().st_mtime_ns for p in committed_baselines}
    assert len(list((root / "runs" / "paired-fixture").glob("attempt-*/baseline_report.json"))) == 1
    assert result["status"] in ("accepted", "rejected", "inconclusive"), result
    assert len(result["evaluation"]["paired_deltas"]) == 2
    diagnostics = result['development_diagnostics']
    assert result['evaluation']['factor_signal_gate'] == diagnostics['predictive_metrics']['factor_signal_gate']
    assert result['evaluation']['factor_signal_gate']['rule'] == protocol.factor_signal_rule
    assert len(result['evaluation']['factor_signal_by_fold']) == len(protocol.validation.folds)
    assert json.loads((root / 'runs' / 'paired-fixture' / 'development_diagnostics.json').read_text()) == diagnostics
    assert diagnostics['data_quality']['time_check'] == 'passed'
    assert diagnostics['data_quality']['index_check'] == 'passed'
    risk_rows = diagnostics['signal_to_execution']['by_fold']
    assert risk_rows and all(row['daily_risk_attribution']['status'] == 'completed' for row in risk_rows)
    first_risk = risk_rows[0]['daily_risk_attribution']['by_date']
    assert first_risk and {'equity', 'high_water_mark', 'risk_check_status',
                           'filled_order_count', 'unfilled_reasons', 'risk_order_link_status',
                           'risk_order_intents', 'risk_order_blocker_status',
                           'risk_order_blockers', 'decision_valuation_basis',
                           'execution_reference_price_basis',
                           'decision_execution_timing_status'}.issubset(first_risk[0]['candidate'])
    checked_risk_days = [day['candidate'] for day in first_risk if day['candidate']['risk_check_status'] == 'checked']
    assert checked_risk_days
    assert all(day['decision_valuation_basis'] == 'previous_session_close' and
               day['execution_reference_price_basis'] == 'current_session_open' and
               day['decision_execution_timing_status'] == 'prior_close_risk_next_open_execution'
               for day in checked_risk_days)
    assert all(day['candidate']['decision_execution_timing_status'] == 'not_checked' for day in first_risk
               if day['candidate']['risk_check_status'] != 'checked')
    assert all(day['candidate']['risk_order_link_status'] == 'completed'
               for day in first_risk)
    assert all(day['candidate']['risk_order_blocker_status'] == 'completed'
               for day in first_risk)
    exposure = risk_rows[0]['portfolio_exposure']
    assert exposure['status'] == 'completed'
    assert all(exposure[kind]['status'] == 'completed' for kind in ('baseline', 'candidate'))
    assert all(day['equity_reconciled'] for day in exposure['candidate']['by_date'])
    assert len(diagnostics['predictive_metrics']['model_by_fold']) == 2
    factor_row = diagnostics['predictive_metrics']['factor_by_fold'][0]
    assert {'ic', 'icir', 'icir_status', 'rank_icir', 'coverage'}.issubset(factor_row)
    assert factor_row['training_orientation']['direction'] in {'positive', 'reverse', 'unknown'}
    assert factor_row['oriented_validation_gate']['status'] in {'passed', 'failed', 'unknown'}
    formal_signal = result['evaluation']['factor_signal_by_fold'][0]
    assert {'ic', 'icir', 'rank_ic', 'rank_icir', 'effective_dates',
            'effective_observations', 'eligible_observations', 'coverage',
            'ic_uncertainty', 'rank_ic_uncertainty'}.issubset(formal_signal)
    assert formal_signal['effective_observations'] > 0
    assert formal_signal['ic_uncertainty']['status'] in {'completed', 'inconclusive'}
    assert formal_signal['rank_ic_uncertainty']['status'] in {'completed', 'inconclusive'}
    model_signal = diagnostics['predictive_metrics']['model_by_fold'][0]['candidate']
    assert {'ic', 'icir', 'icir_status', 'rank_icir'}.issubset(model_signal)
    assert model_signal['by_date']
    assert [r['horizon'] for r in diagnostics['factor_diagnostics']['decay']] == [1, 5, 10, 20]
    assert len(diagnostics['robustness']['cost_stress']) == len(diagnostics['robustness']['ablation']) == 2
    baseline_report = json.loads((root / "runs" / "paired-fixture" / "baseline_report.json").read_text())
    candidate_report = json.loads((root / "runs" / "paired-fixture" / "candidate_report.json").read_text())
    for b, c in zip(baseline_report["by_fold"], candidate_report["by_fold"]):
        assert b["dataset"]["sample_index_hashes"] == c["dataset"]["sample_index_hashes"]
        assert c["cost_stress"]["2.0"]["accounting_reconciled"]
        assert c["model_spec"]["seed"] in (42, 43)
        observed = next(r for r in diagnostics['predictive_metrics']['model_by_fold'] if r['seed'] == c['seed'])
        assert observed['candidate']['rank_ic'] == c['predictive']['rank_ic']
        portfolio = next(r for r in diagnostics['portfolio_metrics']['by_fold'] if r['seed'] == c['seed'])
        assert portfolio['candidate']['total_execution_cost'] == c['portfolio']['total_execution_cost']
    manifests = {p: p.stat().st_mtime_ns for p in (root / "experiments").glob("*/manifest.json")}
    assert len(manifests) == 4
    assert json.loads((root / "runs" / "paired-fixture" / "ablation_report.json").read_text())["reused_from"] == "baseline"
    assert run_paired(config, snapshot.path, factor, protocol, root,
                      run_id="paired-fixture") == result
    assert manifests == {p: p.stat().st_mtime_ns for p in manifests}

    # Measure one clean evaluation and an identical top-level cache replay under
    # the same protocol, process, snapshot, model, and Docker image.
    benchmark_root = tmp_path / "paired-cache-benchmark"
    started = time.perf_counter()
    benchmark_result = run_paired(config, snapshot.path, factor, protocol, benchmark_root,
                                  run_id="paired-cache-benchmark")
    first_evaluation_seconds = time.perf_counter() - started
    benchmark_manifests = {p: p.stat().st_mtime_ns
                           for p in (benchmark_root / "experiments").glob("*/manifest.json")}
    started = time.perf_counter()
    replay_result = run_paired(config, snapshot.path, factor, protocol, benchmark_root,
                               run_id="paired-cache-benchmark")
    cached_replay_seconds = time.perf_counter() - started
    assert replay_result == benchmark_result
    assert benchmark_manifests == {p: p.stat().st_mtime_ns for p in benchmark_manifests}
    record_property("paired_first_evaluation_seconds", round(first_evaluation_seconds, 6))
    record_property("paired_same_identity_cache_replay_seconds", round(cached_replay_seconds, 6))
    record_property("provider_dispatches", 0)

    from etf_ml.research.session import ResearchSession
    from etf_ml.research.execution import execute_research
    worker_result = execute_research(
        ResearchSession(config, snapshot.path, protocol, root=tmp_path),
        factor, run_id="paired-fixture")
    assert worker_result == result
    worker_progress = next((tmp_path / "executions").glob("*/progress.json"))
    assert json.loads(worker_progress.read_text())["component"] == "experiment_worker"
    assert json.loads(worker_progress.read_text())["status"] == "completed"
    # An intact top-level report cannot conceal corrupted referenced predictions.
    # The baseline is now portable under artifacts/reuse; tamper with a raw
    # candidate child that the completed pair still references directly.
    prediction = next((root / "experiments").glob("candidate-*/fixture/lightgbm/predictions.parquet"))
    with prediction.open("ab") as stream:
        stream.write(b"corrupt")
    with pytest.raises(IntegrityError):
        run_paired(config, snapshot.path, factor, protocol, root, run_id="paired-fixture")
