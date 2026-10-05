import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

from etf_ml.cli import main
from etf_ml.config import load_config
from etf_ml.contracts import ModelSpec, RuntimeLimits, UniversePolicy
from etf_ml.data.snapshot import build_snapshot

pytestmark = [pytest.mark.qlib, pytest.mark.docker]
IMAGE = "rdagent-qlib@sha256:97e456451ae9b3aa7c74456cf76afa2a6fd336b7b7cc96c5b98416a4de0bc373"


def test_real_rdagent_adapters_cli_multitrial_and_safe_feedback(source_spec, fold, tmp_path, capsys, monkeypatch):
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
    config.models = [ModelSpec(name="lightgbm", constructor={
        "num_boost_round": 8, "early_stopping_rounds": 3,
        "min_data_in_leaf": 5, "num_threads": 1}, fit={"verbose_eval": 0})]
    config.research.limits = RuntimeLimits(image=IMAGE, timeout_seconds=180, memory_mb=1024)
    snapshot = build_snapshot(source_spec.source, source_spec, config.universe)
    config_path = tmp_path / "research.yaml"
    config_path.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8")
    common = {"hypothesis": "short trend", "formula": "close / lag3(close) - 1",
              "required_fields": ["adj_close"], "lookback": 4, "minimum_observations": 4,
              "expected_difference": "three-day adjusted-close return differs from momentum_5",
              "mechanism": "short-horizon price continuation may differ from the five-day baseline",
              "direction": "unknown", "direction_reason": "the model may learn either monotonic response",
              "failure_conditions": ["coverage below the frozen threshold"],
              "compared_features": ["momentum_5"], "research_group": "trend"}
    replay_path = tmp_path / "replay.json"
    replay_path.write_text(json.dumps([
        {"hypothesis": "reject future access", "factors": [{
            **common, "factor_id": "future_bad",
            "source": "def compute(panel):\n    return panel['adj_close'].groupby(level='instrument').shift(-1).to_frame('factor')\n"}]},
        {"hypothesis": "causal short trend", "factors": [{
            **common, "factor_id": "momentum_three",
            "source": "def compute(panel):\n    close = panel['adj_close']\n    return (close / close.groupby(level='instrument').shift(3) - 1).to_frame('factor')\n"}]},
    ]), encoding="utf-8")
    from etf_ml.features.baseline import materialize
    from etf_ml.research.protocol import ComparisonProtocol
    from etf_ml.research.reuse_identity import evaluation_code_hash
    from etf_ml.research.session import ResearchSession
    from etf_ml.adapters.rdagent.experiment import ETFExperiment
    panel = pd.read_parquet(snapshot.path / "research" / "panel.parquet")
    base = materialize({"snapshot_id": snapshot.snapshot_id}, panel)
    protocol = ComparisonProtocol(snapshot_id=snapshot.snapshot_id, baseline_feature_set_id=base.feature_set_id,
        evaluation_code_hash=evaluation_code_hash(),
        label=config.label, universe=config.universe, validation=config.validation,
        portfolio=config.portfolio, research=config.research, model=config.models[0],
        stress_min_excess_return=-1)
    session = ResearchSession(config, snapshot.path, protocol,
        root=tmp_path / "a" / "research" / "rd-replay")
    reference = ETFExperiment(session, []).experiment_workspace.execute()
    assert len(reference["by_fold"]) == 2
    assert all("2.0" in r["cost_stress"] for r in reference["by_fold"])
    baseline_times = {p: p.stat().st_mtime_ns for p in (session.root / "paired" / "experiments").glob("*/manifest.json")}
    args = ["research-factor", "--config", str(config_path), "--snapshot", str(snapshot.path),
            "--run-id", "rd-replay", "--replay", str(replay_path), "--max-trials", "2",
            "--stress-min-excess", "-1", "--campaign-id", "rd-replay-campaign",
            "--campaign-max-trials", "2"]
    from etf_ml.adapters.rdagent.runner import ETFFactorRunner
    original_runner = ETFFactorRunner.develop
    def interrupt_after_code(self, exp):
        if any(t.name == "momentum_three" for t in exp.sub_tasks):
            raise KeyboardInterrupt("interrupt after validated code")
        return original_runner(self, exp)
    monkeypatch.setattr(ETFFactorRunner, "develop", interrupt_after_code)
    assert main(args) == 130
    capsys.readouterr()
    pending_path = tmp_path / "a" / "research" / "rd-replay" / "sessions" / "rd-replay" / "checkpoint.json"
    pending = json.loads(pending_path.read_text())
    assert pending["status"] == "cancelled" and pending["in_progress"]["phase"] == "running"
    assert len(pending["trials"]) == 1
    monkeypatch.setattr(ETFFactorRunner, "develop", original_runner)
    assert main(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["trial_count"] == 2
    root = tmp_path / "a" / "research" / "rd-replay"
    worker_progress = [json.loads(path.read_text()) for path in (root / "executions").glob("*/progress.json")]
    assert any(event["component"] == "experiment_worker" and event["status"] == "completed"
               and event["mode"] == "paired" for event in worker_progress)
    state = json.loads((root / "sessions" / "rd-replay" / "checkpoint.json").read_text())
    assert state["status"] == "completed"
    assert state["campaign_summary"]["generation_attempts"] == 2
    assert state["campaign_summary"]["completed_unique_evaluations"] == 1
    assert state["campaign_summary"]["max_attempts"] == 2
    assert baseline_times == {p: p.stat().st_mtime_ns for p in baseline_times}
    assert state["billing"]["call_count"] == 6
    assert state["billing"]["measured_cost"] == "0"
    assert state["billing"]["unknown_cost_calls"] == 0
    first = json.loads((root / "sessions" / "rd-replay" / "trial-00000.json").read_text())
    second = json.loads((root / "sessions" / "rd-replay" / "trial-00001.json").read_text())
    assert first["result"]["status"] == "failed"
    legal = second["result"]["by_candidate"][0]
    assert legal["status"] in ("accepted", "rejected", "inconclusive"), legal
    assert len(legal["evaluation"]["paired_deltas"]) == 2
    public = second['feedback']['by_candidate'][0]
    assert public['observations']['status'] == 'completed'
    assert public['observations']['data_quality']['time_check'] == 'passed'
    assert public['direction'] == 'unknown' and public['applicable_scope'] == 'domestic_equity'
    from etf_ml.adapters.rdagent.feedback import compact_diagnostics
    compact_metrics = compact_diagnostics(legal['development_diagnostics'])['predictive_metrics']
    assert public['observations']['predictive_metrics']['model_by_fold'] == compact_metrics['model_by_fold']
    assert all('by_date' not in row['candidate'] for row in public['observations']['predictive_metrics']['model_by_fold'])
    assert len(public['observations']['robustness']['cost_stress']) == 2
    assert first['feedback']['by_candidate'][0]['decision']['failure_stage'] == 'factor_validation'
    compact = json.dumps(public)
    assert all(value not in compact for value in ('by_date', 'model_path', 'holdout_start', '2026-01-01'))
    assert all(row["attempted_trials"] == 2 for row in legal["time_block_statistics"])
    assert {row["model_seed"] for row in legal["time_block_statistics"]} == {42, 43}
    assert {row["bootstrap_seed"] for row in legal["time_block_statistics"]} == {42}
    assert len(list((tmp_path / "a" / "runs" / "rd-replay").glob("attempt-*"))) == 1
    # Static causal rejection is retained in the trial and LLM artifacts but
    # must not create a reusable factor-registry version.
    assert not (root / "registry" / "future_bad" / "v1" / "head.json").exists()
    assert (root / "registry" / "momentum_three" / "v1" / "head.json").exists()
    request = next(p for p in (root / "llm" / "calls").glob("*/request.json")
                   if json.loads(p.read_text())["prompt"]["stage"] == "hypothesis"
                   and "future_bad" in json.loads(p.read_text())["prompt"]["user_prompt"])
    prompt = json.loads(request.read_text())["prompt"]["user_prompt"]
    assert "2026-01-01" not in prompt and "holdout" not in prompt
    combined = next((root / "workspaces").glob("*/combined_factors_df.parquet"))
    assert set(pd.read_parquet(combined).columns.get_level_values(0)) == {"feature"}
    # Required legacy HDF files are real, schema-compatible research-only views.
    pd.testing.assert_frame_equal(pd.read_hdf(snapshot.path / "research" / "daily_pv.h5", key="data"),
                                  pd.read_parquet(snapshot.path / "research" / "panel.parquet"))
    cache = next((root / "factors" / "validated").glob("*/result.parquet"))
    pd.testing.assert_frame_equal(pd.read_hdf(cache.with_name("result.h5"), key="data"),
                                  pd.read_parquet(cache))
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["reused"]
    # Use the real settings singleton in a clean process: all extension paths are
    # injected before import and resolve to concrete ETF implementations.
    code = (
        "import importlib\n"
        "from etf_ml.adapters.rdagent.bootstrap import configure, EXTENSIONS\n"
        f"configure({str(root / 'session.json')!r})\n"
        "from rdagent.app.qlib_rd_loop.conf import FACTOR_PROP_SETTING\n"
        "keys={'SCEN':'scen','HYPOTHESIS_GEN':'hypothesis_gen',"
        "'HYPOTHESIS2EXPERIMENT':'hypothesis2experiment','CODER':'coder',"
        "'RUNNER':'runner','SUMMARIZER':'summarizer'}\n"
        "for name, attr in keys.items():\n"
        " path=getattr(FACTOR_PROP_SETTING,attr); assert path==EXTENSIONS['QLIB_FACTOR_'+name]\n"
        " module,cls=path.rsplit('.',1); assert getattr(importlib.import_module(module),cls)\n"
        "scenario=importlib.import_module('etf_ml.adapters.rdagent.scenario').ETFFactorScenario()\n"
        "assert scenario.session.snapshot.snapshot_id\n"
        "try: configure('unused')\n"
        "except Exception: print('late_configuration_rejected')\n"
    )
    check = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert check.returncode == 0, check.stderr
    assert "late_configuration_rejected" in check.stdout
    # CLI completed reuse still verifies models outside the command run folder.
    metrics = json.loads((tmp_path / "a" / "runs" / "rd-replay" / "metrics.json").read_text())
    model_path = next(Path(ref["path"]) / "bundle.pkl" for ref in metrics["model_references"]
                      if Path(ref["path"]).is_relative_to(tmp_path / "a" / "models"))
    model = model_path
    with model.open("ab") as stream:
        stream.write(b"corrupt")
    assert main(args) == 5
