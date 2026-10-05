import json

import pytest

from etf_ml.cli import main
from etf_ml.contracts import AppConfig
from etf_ml.research.readiness import first_loop_readiness


def local_config(tmp_path):
    config = AppConfig()
    config.artifact_root = tmp_path / "artifacts"
    config.data.source = tmp_path / "provider"
    root = config.data.source
    (root / "instruments").mkdir(parents=True)
    (root / "instruments" / "all.txt").write_text("ETF.SH\t2020-01-01\t2025-12-31\n")
    (root / "calendars").mkdir()
    (root / "calendars" / "day.txt").touch()
    directory = root / "features" / "etf.sh"
    directory.mkdir(parents=True)
    for field in config.data.fields.values():
        (directory / (field + ".day.bin")).touch()
    for name in ("trusted_calendar", "metadata_path", "events_path", "benchmark_path"):
        path = tmp_path / name
        path.touch()
        setattr(config.data, name, path)
    config.data.point_in_time_metadata = True
    config.data.volume_unit = "lots"
    config.data.amount_multiplier = 1000
    config.data.price_mode = "raw"
    config.data.change_unit = "percent"
    config.portfolio.k_mode = "fraction"
    config.portfolio.minimum_commission = 0
    config.portfolio.liquidity_mode = "participation"
    config.portfolio.risk_mode = "max_drawdown"
    config.research.budget_mode = "unlimited"
    config.research.stress_min_excess_return = 0
    config.research.limits.image = "test@sha256:" + "a" * 64
    config.validation.folds = [{"name": "A", "train": {"start": "2020-01-01", "end": "2021-12-31"},
                                "early_stop": {"start": "2022-01-01", "end": "2022-12-31"},
                                "selection": {"start": "2023-01-01", "end": "2023-12-31"}}]
    return config


def test_presence_cannot_claim_g0(tmp_path):
    result = first_loop_readiness(local_config(tmp_path))
    assert result["status"] == "needs_full_validation"
    assert result["g0_passed"] is False
    assert result["external_calls"] == result["training_runs"] == 0
    assert "sidecar_contents_and_coverage" in result["not_checked"]


def test_missing_factor_not_silently_filled(tmp_path):
    config = local_config(tmp_path)
    (config.data.source / "features/etf.sh/factor.day.bin").unlink()
    result = first_loop_readiness(config)
    assert next(x for x in result["blockers"] if x["code"] == "missing_required_bins")["counts"] == {"factor": 1}


@pytest.mark.parametrize("mode", [None, "free_only", "capped"])
def test_real_transport_cost_guard(tmp_path, mode):
    config = local_config(tmp_path)
    config.research = {**config.research.model_dump(), "budget_mode": mode,
                       "api_budget": 1 if mode == "capped" else None}
    config = AppConfig.model_validate(config.model_dump())
    codes = {x["code"] for x in first_loop_readiness(config)["blockers"]}
    assert ("unresolved_research_budget" if mode is None else "unsupported_real_transport_budget") in codes


def test_pit_and_events_required(tmp_path):
    config = local_config(tmp_path)
    config.data.point_in_time_metadata = False
    config.data.events_path = None
    codes = {x["code"] for x in first_loop_readiness(config)["blockers"]}
    assert {"unverified_historical_metadata", "missing_events_path"} <= codes


def test_formal_readiness_requires_predeclared_cost_stress_threshold(tmp_path):
    config = local_config(tmp_path)
    config.research.stress_min_excess_return = None
    codes = {row["code"] for row in first_loop_readiness(config)["blockers"]}
    assert "unresolved_stress_min_excess_return" in codes


def test_readiness_validates_campaign_pair_and_reports_finite_cap(tmp_path):
    config = local_config(tmp_path)
    result = first_loop_readiness(config, campaign_id="formal-five", campaign_max_trials=5)
    assert result["campaign"] == {"campaign_id": "formal-five", "max_attempts": 5}
    assert "campaign_bounds" not in result["not_checked"]
    assert not (config.artifact_root / "research_campaigns" / "formal-five").exists()

    partial = first_loop_readiness(config, campaign_id="formal-five")
    assert "incomplete_campaign_bounds" in {row["code"] for row in partial["blockers"]}

    omitted = first_loop_readiness(config)
    assert "campaign_bounds" in omitted["not_checked"]


def test_unsafe_instrument_never_reads_outside_provider(tmp_path):
    config = local_config(tmp_path)
    (config.data.source / "instruments/all.txt").write_text("../secret\t2020-01-01\t2025-01-01\n")
    assert "invalid_source_instruments" in {x["code"] for x in first_loop_readiness(config)["blockers"]}


def test_cli_reports_all_blockers_without_run_or_credentials(tmp_path, capsys, monkeypatch):
    config = local_config(tmp_path)
    config.portfolio.k_mode = None
    config.data.benchmark_path = None
    monkeypatch.setattr("etf_ml.cli.load_config", lambda *args: config)
    monkeypatch.setenv("OPENAI_API_KEY", "private-secret-value")
    assert main(["first-loop-readiness"]) == 5
    output = capsys.readouterr().out
    result = json.loads(output)
    assert "private-secret-value" not in output
    assert "missing_benchmark_path" in output and "unresolved_portfolio" in output
    assert not (config.artifact_root / "runs" / result["run_id"]).exists()


@pytest.mark.parametrize("name", ["limits_path", "dividend_review_path", "share_events_path"])
def test_cli_declared_missing_supplement_blocks_without_training(tmp_path, capsys, monkeypatch, name):
    config = local_config(tmp_path)
    setattr(config.data, name, tmp_path / "not_collected" / name)
    monkeypatch.setattr("etf_ml.cli.load_config", lambda *args: config)
    assert main(["first-loop-readiness"]) == 5
    result = json.loads(capsys.readouterr().out)
    assert {x["code"] for x in result["blockers"]} == {"missing_" + name}
    assert result["external_calls"] == result["training_runs"] == 0
    assert not (config.artifact_root / "runs" / result["run_id"]).exists()


def test_exhausted_expired_and_corrupt_campaign_are_not_ready(tmp_path, monkeypatch):
    from etf_ml.research.campaign import CampaignLedger
    from etf_ml.utils import source_hashes
    config = local_config(tmp_path)
    ledger = CampaignLedger(config.artifact_root / "research_campaigns", "used", 1,
                            config.research.max_campaign_wall_seconds)
    ledger.initialize()
    monkeypatch.setattr("etf_ml.research.campaign.time.time_ns", lambda: 0)
    assert ledger.begin_trial(run_id="old", trial_index=0, compatibility_group_id="g", protocol_id="p")
    monkeypatch.setattr("etf_ml.research.campaign.time.time_ns", lambda: 30_000 * 10**9)
    before = source_hashes(ledger.root)
    result = first_loop_readiness(config, campaign_id="used", campaign_max_trials=1)
    assert {"campaign_exhausted", "campaign_expired"} <= {r["code"] for r in result["blockers"]}
    assert result["campaign_observation"]["campaign_attempted_trials"] == 1
    assert source_hashes(ledger.root) == before
    event = next(ledger.events.glob("*.json"))
    event.write_text("{}", encoding="utf-8")
    result = first_loop_readiness(config, campaign_id="used", campaign_max_trials=1)
    assert "invalid_campaign_ledger" in {r["code"] for r in result["blockers"]}


def test_existing_campaign_duration_cannot_be_silently_replaced(tmp_path):
    from etf_ml.research.campaign import CampaignLedger
    config = local_config(tmp_path)
    ledger = CampaignLedger(config.artifact_root / "research_campaigns", "old", 5)
    ledger.initialize()
    result = first_loop_readiness(config, campaign_id="old", campaign_max_trials=5)
    assert "campaign_duration_mismatch" in {r["code"] for r in result["blockers"]}


def test_runtime_failure_does_not_dispatch_and_w08_remains_deferred(tmp_path, monkeypatch):
    from etf_ml.errors import DataNotReady
    config = local_config(tmp_path)
    def unavailable(*args):
        raise DataNotReady("offline")
    monkeypatch.setattr("etf_ml.runtime.docker.DockerBackend.preflight", unavailable)
    result = first_loop_readiness(config, check_runtime=True)
    assert result["runtime"]["status"] == "blocked"
    assert result["external_calls"] == result["training_runs"] == 0
    assert result["deferred_stages"][0]["blocks_research_preparation"] is False
    assert result["deferred_stages"][0]["status"] == "not_run"
    assert result["investment_ready"] is False
