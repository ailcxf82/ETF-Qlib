import json
from types import SimpleNamespace

import pytest

from etf_ml.config import load_config
from etf_ml.errors import ConfigurationError
from etf_ml.research.first_loop import execute_first_loop, load_reusable_baseline
from etf_ml.research.session import ResearchSession


def _baseline_fixture(tmp_path):
    from etf_ml.artifacts import environment_manifest
    from etf_ml.utils import code_hash
    config = load_config(overrides={"artifact_root": tmp_path / "artifacts"})
    snapshot_path = tmp_path / "snapshot"
    snapshot_path.mkdir()
    snapshot = SimpleNamespace(
        path=snapshot_path,
        snapshot_id="snapshot-proof",
        manifest={"qualification": {"mode": "diagnostic", "investment_acceptance_eligible": False}},
    )
    root = config.artifact_root / "runs" / "successful" / "baseline"
    root.mkdir(parents=True)
    (root / "execution_result.json").write_text(json.dumps({"status": "succeeded", "returncode": 0}), encoding="utf-8")
    (root / "pipeline_request.json").write_text(json.dumps({
        "mode": "baseline", "feature_override": None, "config": config.model_dump(mode="json"),
        "execution_identity": {"source_code_hash": code_hash(), "environment": environment_manifest()},
        "snapshot_path": str(snapshot_path.resolve()),
    }), encoding="utf-8")
    (root / "baseline_report.json").write_text(json.dumps({
        "status": "completed", "snapshot_id": snapshot.snapshot_id,
        "execution_identity": {"source_code_hash": code_hash(), "environment": environment_manifest()},
        "qualification": snapshot.manifest["qualification"],
        "by_fold": [{"fold": fold.name} for fold in config.validation.folds],
    }), encoding="utf-8")
    return config, snapshot, root


def test_reusable_baseline_requires_matching_immutable_inputs(tmp_path):
    config, snapshot, root = _baseline_fixture(tmp_path)
    report, provenance = load_reusable_baseline(config, snapshot, root)
    assert report["snapshot_id"] == snapshot.snapshot_id
    assert provenance["mode"] == "reused"
    assert provenance["baseline_report_sha256"]


def test_reusable_baseline_rejects_different_config(tmp_path):
    config, snapshot, root = _baseline_fixture(tmp_path)
    request_path = root / "pipeline_request.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["config"]["portfolio"]["initial_cash"] = 1
    request_path.write_text(json.dumps(request), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="configuration differs"):
        load_reusable_baseline(config, snapshot, root)


def test_reusable_baseline_accepts_multiple_models_per_development_fold(tmp_path):
    config, snapshot, root = _baseline_fixture(tmp_path)
    report_path = root / "baseline_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["by_fold"] = [
        {"fold": fold.name, "model": model}
        for fold in config.validation.folds
        for model in ("ridge", "lightgbm")
    ]
    report_path.write_text(json.dumps(report), encoding="utf-8")

    reused, _ = load_reusable_baseline(config, snapshot, root)

    assert len(reused["by_fold"]) == 2 * len(config.validation.folds)


@pytest.mark.parametrize("identity", [None, {"source_code_hash": "old", "environment": {}},
                                     {"source_code_hash": "current", "environment": {"python": "old"}}])
def test_reusable_baseline_cannot_relabel_old_or_unproven_runtime(tmp_path, identity):
    config, snapshot, root = _baseline_fixture(tmp_path)
    path = root / "pipeline_request.json"
    request = json.loads(path.read_text(encoding="utf-8"))
    request["execution_identity"] = identity
    path.write_text(json.dumps(request), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="runtime identity"):
        load_reusable_baseline(config, snapshot, root)


def test_formal_first_loop_stops_before_creating_outputs_without_stress_threshold(tmp_path):
    config = load_config(overrides={"artifact_root": tmp_path / "artifacts",
                                    "research": {"budget_mode": "unlimited", "max_trials": 1}})
    output = config.artifact_root / "runs" / "formal"

    with pytest.raises(ConfigurationError, match="stress_min_excess_return"):
        execute_first_loop(config, output, run_id="formal")

    assert not output.exists()


def test_formal_session_cannot_bypass_unresolved_stress_threshold(tmp_path):
    config = load_config(overrides={"artifact_root": tmp_path / "artifacts"})
    protocol = SimpleNamespace(stress_min_excess_return=None)

    with pytest.raises(ConfigurationError, match="stress_min_excess_return"):
        ResearchSession(config, tmp_path / "missing_snapshot", protocol, root=tmp_path / "research")


def test_formal_first_loop_requires_campaign_cap_before_creating_outputs(tmp_path):
    config = load_config(overrides={"artifact_root": tmp_path / "artifacts",
                                    "research": {"budget_mode": "unlimited", "max_trials": 1,
                                                 "stress_min_excess_return": -0.03}})
    output = config.artifact_root / "runs" / "formal"

    with pytest.raises(ConfigurationError, match="finite campaign"):
        execute_first_loop(config, output, run_id="formal")

    with pytest.raises(ConfigurationError, match="positive finite attempt cap"):
        execute_first_loop(config, output, run_id="formal-zero", campaign_id="finite-five",
                           campaign_max_trials=0)
    assert not output.exists()


def test_formal_first_loop_stops_before_preparation_when_campaign_is_exhausted(tmp_path):
    from etf_ml.research.campaign import CampaignLedger

    config = load_config(overrides={"artifact_root": tmp_path / "artifacts",
                                    "research": {"budget_mode": "unlimited", "max_trials": 1,
                                                 "stress_min_excess_return": -0.03}})
    campaign = CampaignLedger(config.artifact_root / "research_campaigns", "formal-five", 1,
                              config.research.max_campaign_wall_seconds)
    campaign.initialize()
    assert campaign.begin_trial(run_id="prior-run", trial_index=0,
                                compatibility_group_id="group", protocol_id="protocol")
    output = config.artifact_root / "runs" / "formal"

    with pytest.raises(ConfigurationError, match="already exhausted"):
        execute_first_loop(config, output, run_id="formal", campaign_id="formal-five",
                           campaign_max_trials=1)

    assert not output.exists()


def test_first_loop_cli_accepts_explicit_campaign_cap():
    from etf_ml.cli import parser

    args = parser().parse_args(["first-loop", "--campaign-id", "formal-five",
                                "--campaign-max-trials", "5"])

    assert (args.campaign_id, args.campaign_max_trials) == ("formal-five", 5)

    readiness = parser().parse_args(["first-loop-readiness", "--campaign-id", "formal-five",
                                     "--campaign-max-trials", "5"])
    assert (readiness.campaign_id, readiness.campaign_max_trials) == ("formal-five", 5)


def test_formal_research_cli_stops_before_creating_run_without_stress_threshold(tmp_path, monkeypatch, capsys):
    from etf_ml.cli import main
    config = load_config(overrides={"artifact_root": tmp_path / "artifacts"})
    monkeypatch.setattr("etf_ml.cli.load_config", lambda *args: config)

    assert main(["research-factor", "--snapshot", str(tmp_path / "missing_snapshot"),
                 "--run-id", "missing-threshold"]) == 2
    assert "stress_min_excess_return" in capsys.readouterr().err
    assert not (config.artifact_root / "runs").exists()
