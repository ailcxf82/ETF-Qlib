"""Local synthetic Qlib integration; no Docker or external LLM calls."""
import json
from pathlib import Path

import pandas as pd
import pytest

from etf_ml.config import load_config
from etf_ml.contracts import UniversePolicy
from etf_ml.data.snapshot import build_snapshot
from etf_ml.research.execution import execute_baseline
from etf_ml.research.reuse_baseline import SharedBaseline

pytestmark = pytest.mark.qlib


def test_second_run_reuses_local_baseline_when_removable_store_is_missing(source_spec, fold, tmp_path, monkeypatch):
    source_spec.holdout_start = "2026-01-01"
    config = load_config(overrides={"artifact_root": tmp_path / "artifacts",
        "portfolio": {"k_mode": "fraction", "minimum_commission": 0,
                      "liquidity_mode": "participation", "risk_mode": "max_drawdown"},
        "validation": {"folds": [fold.model_dump()]},
        "models": [{"name": "lightgbm", "constructor": {"num_boost_round": 4,
            "early_stopping_rounds": 2, "min_data_in_leaf": 5, "num_threads": 1},
            "fit": {"verbose_eval": 0}}]})
    config.data = source_spec
    config.universe = UniversePolicy(minimum_listing_days=0, liquidity_lookback=1)
    snapshot = build_snapshot(source_spec.source, source_spec, config.universe)
    output = config.artifact_root / "runs" / "first"
    first = execute_baseline(config, snapshot.path, output, run_id="first")
    reference = json.loads((output / "baseline_reference.json").read_text())
    original = output / "fixture" / "lightgbm"
    preserved = Path(reference["path"]) / "fixture" / "lightgbm"
    for filename in ("predictions.parquet", "daily_returns.parquet", "positions.parquet", "trades.parquet"):
        pd.testing.assert_frame_equal(pd.read_parquet(original / filename), pd.read_parquet(preserved / filename))
    output.rename(tmp_path / "original-offline")
    local_store = config.artifact_root / "reuse"
    config.reuse_root = tmp_path / "relocated-store"
    monkeypatch.setattr("etf_ml.runtime.native.NativeBackend.run",
                        lambda *args, **kwargs: pytest.fail("shared baseline started another worker"))
    second = execute_baseline(config, snapshot.path, config.artifact_root / "runs" / "second", run_id="second")
    assert second["shared_baseline_package_id"] == first["shared_baseline_package_id"]
    assert second["by_fold"][0]["portfolio"] == first["by_fold"][0]["portfolio"]
    assert Path(second["by_fold"][0]["model_path"]).is_relative_to(local_store)
    assert (Path(second["by_fold"][0]["model_path"]) / "bundle.pkl").is_file()
    assert SharedBaseline(local_store).rebuild_index() == 1


def test_paired_run_reuses_baseline_across_output_directories(source_spec, fold, tmp_path, monkeypatch):
    from etf_ml.contracts import FeatureArtifact
    from etf_ml.features.baseline import materialize
    from etf_ml.research import paired
    from etf_ml.research.protocol import ComparisonProtocol
    from etf_ml.utils import atomic_json, file_hash
    source_spec.holdout_start = "2026-01-01"
    config = load_config(overrides={"artifact_root": tmp_path / "artifacts",
        "portfolio": {"k_mode": "fraction", "minimum_commission": 0,
                      "liquidity_mode": "participation", "risk_mode": "max_drawdown"},
        "validation": {"folds": [fold.model_dump()]}, "research": {"seeds": [42, 43]},
        "models": [{"name": "lightgbm", "constructor": {"num_boost_round": 4,
            "early_stopping_rounds": 2, "min_data_in_leaf": 5, "num_threads": 1},
            "fit": {"verbose_eval": 0}}]})
    config.data = source_spec
    config.universe = UniversePolicy(minimum_listing_days=0, liquidity_lookback=1)
    snapshot = build_snapshot(source_spec.source, source_spec, config.universe)
    panel = pd.read_parquet(snapshot.path / "research" / "panel.parquet")
    baseline = materialize({"snapshot_id": snapshot.snapshot_id}, panel)
    protocol = ComparisonProtocol(snapshot_id=snapshot.snapshot_id,
        baseline_feature_set_id=baseline.feature_set_id, label=config.label,
        universe=config.universe, validation=config.validation, portfolio=config.portfolio,
        research=config.research, model=config.models[0], stress_min_excess_return=-1)
    # A synthetic validated-cache fixture isolates paired storage behavior from Docker.
    factor_root = tmp_path / "factor"
    factor_root.mkdir()
    frame = (panel.adj_close / panel.adj_open - 1).to_frame("fixture_factor")
    frame.to_parquet(factor_root / "result.parquet")
    manifest = {"path": str(factor_root), "snapshot_id": snapshot.snapshot_id,
        "protocol_id": protocol.protocol_id, "feature_set_id": "fixture-factor",
        "result_hash": file_hash(factor_root / "result.parquet"),
        "checks": {"status": "passed"}, "quality": {"coverage": 1.0}}
    atomic_json(factor_root / "feature_manifest.json", manifest)
    factor = FeatureArtifact("fixture-factor", frame, manifest)
    first_root = config.artifact_root / "first-paired"
    first = paired.run_paired(config, snapshot.path, factor, protocol, first_root, run_id="first")
    first_baseline = json.loads(Path(first["reports"]["baseline"]).read_text())
    first_root.rename(tmp_path / "paired-offline")
    original_runner = paired.run_baseline
    def only_candidate(*args, **kwargs):
        assert not kwargs["run_id"].startswith("baseline-"), "shared baseline was retrained"
        return original_runner(*args, **kwargs)
    monkeypatch.setattr(paired, "run_baseline", only_candidate)
    second_root = config.artifact_root / "second-paired"
    second = paired.run_paired(config, snapshot.path, factor, protocol, second_root, run_id="second")
    second_baseline = json.loads(Path(second["reports"]["baseline"]).read_text())
    assert first_baseline == second_baseline
    assert first["status"] == second["status"]
    assert first["time_block_statistics"] == second["time_block_statistics"]
    assert len(list((config.artifact_root / "reuse" / "baselines").iterdir())) == 2
