import json
import pytest

from etf_ml.artifacts import RunStore
from etf_ml.config import load_config
from etf_ml.contracts import ModelSpec, UniversePolicy
from etf_ml.data.snapshot import build_snapshot
from etf_ml.pipeline import run_baseline

pytestmark = pytest.mark.qlib

def test_baseline_pipeline_persists_models_predictions_trades_positions(
        source_spec, calendar, fold, tmp_path):
    # Both roles must precede holdout. A late holdout leaves the selection intact.
    source_spec.holdout_start = "2026-01-01"
    config = load_config(overrides={
        "artifact_root": tmp_path / "artifacts",
        "portfolio": {"k_mode": "fraction", "minimum_commission": 0,
                      "liquidity_mode": "participation", "risk_mode": "max_drawdown"},
        "validation": {"folds": [fold.model_dump()], "annualization_days": 240,
                       "risk_free_rate": 0.02},
        "models": [
            {"name": "ridge"},
            {"name": "lightgbm", "constructor": {"num_boost_round": 10,
                "early_stopping_rounds": 3, "min_data_in_leaf": 5, "num_threads": 1},
             "fit": {"verbose_eval": 0}},
            {"name": "xgboost", "constructor": {"max_depth": 3, "eta": .1, "nthread": 1},
             "fit": {"num_boost_round": 10, "early_stopping_rounds": 3, "verbose_eval": False}},
        ],
    })
    config.data = source_spec
    config.universe = UniversePolicy(minimum_listing_days=0, liquidity_lookback=1)
    snapshot = build_snapshot(source_spec.source, source_spec, config.universe)
    with RunStore(tmp_path / "runs", "baseline-fixture", config.model_dump(mode="json")) as run:
        result = run_baseline(config, snapshot.path, run.path, run_id="baseline-fixture")
        run.complete(result)
    folder = run.path / "fixture" / "ridge"
    for name in ("predictions.parquet", "trades.parquet", "positions.parquet",
                 "daily_returns.parquet", "report.parquet", "metrics.json", "execution.parquet"):
        assert (folder / name).is_file()
    assert result["by_fold"][0]["portfolio"]["accounting_reconciled"]
    assert result["research_passed"] is None
    assert result["cost_multipliers"] == [2.0]
    assert {r["strategy"] for r in result["auxiliary_by_fold"]} == {
        "manual_momentum", "equal_weight_pool"}
    for row in result["auxiliary_by_fold"]:
        assert row["learned"] is False
        assert row["portfolio"]["accounting_reconciled"]
        assert row["portfolio"]["commission"] > 0
        assert row["daily_index_hash"] == result["by_fold"][0]["daily_index_hash"]
        assert row["evaluation_index_hash"] == result["by_fold"][0]["dataset"]["evaluation_index_hash"]
        assert row["portfolio"]["annualization_days"] == 240
        assert row["portfolio"]["risk_free_rate"] == .02
        assert row["cost_stress"]["2.0"]["accounting_reconciled"]
        aux_folder = run.path / "fixture" / "auxiliary" / row["strategy"]
        for filename in ("predictions.parquet", "positions.parquet", "trades.parquet",
                         "daily_returns.parquet", "decisions.json", "rule_manifest.json", "exposures.parquet", "execution.parquet"):
            assert (aux_folder / filename).is_file()
        assert (aux_folder / "cost-2.0" / "positions.parquet").is_file()
        assert (aux_folder / "cost-2.0" / "decisions.json").is_file()
        assert (aux_folder / "cost-2.0" / "exposures.parquet").is_file()
        assert (aux_folder / "cost-2.0" / "execution.parquet").is_file()
        assert row["portfolio"]["slippage_cost"] > 0
        assert row["cost_stress"]["2.0"]["slippage_cost"] > 0
        assert 0 <= row["portfolio"]["max_single_weight"] <= 1
        assert row["portfolio"]["max_unclassified_weight"] == 0
    assert all("2.0" in row["cost_stress"] for row in result["by_fold"])
    assert all(row["portfolio"]["max_unclassified_weight"] == 0 for row in result["by_fold"])
    assert all((run.path / "fixture" / row["model"] / "exposures.parquet").is_file()
               for row in result["by_fold"])
    assert {r["model"] for r in result["by_fold"]} == {"ridge", "lightgbm", "xgboost"}
    import pandas as pd
    equal_decisions = json.loads((run.path / "fixture" / "auxiliary" / "equal_weight_pool" / "decisions.json").read_text())
    assert all(len(d["weights"]) == 3 for d in equal_decisions)
    assert all(all(w == pytest.approx(1 / 3) for w in d["weights"].values()) for d in equal_decisions)
    hashes = {r["dataset"]["evaluation_index_hash"] for r in result["by_fold"]}
    assert len(hashes) == 1
    assert len({r["daily_index_hash"] for r in result["by_fold"]}) == 1
    assert all(r["portfolio"]["accounting_reconciled"] for r in result["by_fold"])
    for name in ("lightgbm", "xgboost"):
        model_folder = run.path / "fixture" / name
        assert (model_folder / "trades.parquet").is_file()
        assert (model_folder / "positions.parquet").is_file()
        assert (model_folder / "predictions.parquet").is_file()
    for row in result["by_fold"]:
        for cost_folder in (run.path / "fixture" / row["model"],
                            run.path / "fixture" / row["model"] / "cost-2.0"):
            execution = pd.read_parquet(cost_folder / "execution.parquet")
            trades = pd.read_parquet(cost_folder / "trades.parquet")
            ledger = pd.read_parquet(cost_folder / 'ledger.parquet')
            assert len(ledger) == row['portfolio']['effective_dates']
            assert ledger.maximum_share_residual.max() < 1e-6
            assert ledger.equity_residual.abs().max() < 1e-6
            assert row['portfolio']['daily_accounting_reconciled']
            assert len(execution) == row["portfolio"]["effective_dates"]
            assert execution.commission.sum() == pytest.approx(trades.commission.sum())
            assert execution.slippage_cost.sum() == pytest.approx(trades.slippage_cost.sum())
    metrics = result["by_fold"][0]["portfolio"]
    assert metrics["annualization_days"] == 240
    assert metrics["risk_free_rate"] == 0.02
    from pathlib import Path
    assert "artifacts" not in Path(result["by_fold"][0]["recorder_uri"]).parts
