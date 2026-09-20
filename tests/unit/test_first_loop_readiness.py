import json

import pytest

from etf_ml.cli import main
from etf_ml.contracts import AppConfig
from etf_ml.research.readiness import first_loop_readiness


def local_config(tmp_path):
    config = AppConfig()
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
