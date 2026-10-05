"""Real snapshot/worker integration on synthetic prices; never real holdout use."""
import json
from pathlib import Path

import pandas as pd
import pytest

from etf_ml.config import load_config
from etf_ml.contracts import UniversePolicy, ModelSpec
from etf_ml.data.snapshot import build_snapshot
from etf_ml.research.readiness import first_loop_readiness
from etf_ml.utils import file_hash, atomic_json, source_hashes


@pytest.fixture
def prepared(source_spec, fold, tmp_path):
    source_spec.holdout_start = "2026-01-01"
    config = load_config(overrides={"artifact_root": tmp_path / "artifacts",
        "research": {"budget_mode": "unlimited", "stress_min_excess_return": -.03,
            "limits": {"image": "rdagent-qlib@sha256:97e456451ae9b3aa7c74456cf76afa2a6fd336b7b7cc96c5b98416a4de0bc373",
                       "timeout_seconds": 180, "memory_mb": 4096}},
        "portfolio": {"minimum_commission": 0},
        "validation": {"folds": [fold.model_dump()]}})
    config.data = source_spec
    config.universe = UniversePolicy(minimum_listing_days=0, liquidity_lookback=1)
    config.models = [ModelSpec(name="ridge")]
    snapshot = build_snapshot(source_spec.source, source_spec, config.universe)
    return config, snapshot


def test_frozen_snapshot_route_with_unavailable_raw_source_never_decodes_holdout(prepared, monkeypatch):
    config, snapshot = prepared
    before = source_hashes(snapshot.path)
    raw = config.data.source
    parked = raw.with_name("temporarily-offline")
    raw.rename(parked)  # Only the disposable pytest fixture; never real user data.
    original = pd.read_parquet
    read_paths = []
    def guarded(path, *args, **kwargs):
        read_paths.append(str(path))
        assert "holdout" not in Path(path).parts
        return original(path, *args, **kwargs)
    monkeypatch.setattr(pd, "read_parquet", guarded)
    try:
        result = first_loop_readiness(config, snapshot_path=snapshot.path,
                                      campaign_id="new-five", campaign_max_trials=5)
        assert result["blockers"] == []
        assert result["data_route"]["status"] == "verified_frozen_research"
        assert result["data_route"]["data_qualification"] == "unknown"
        assert result["deferred_stages"][0]["status"] == "not_run"
        assert result["investment_ready"] is False
        assert read_paths == [str(snapshot.path / "research/panel.parquet")]
        assert not (config.artifact_root / "research_campaigns/new-five").exists()
        assert source_hashes(snapshot.path) == before
        missing = first_loop_readiness(config)
        assert "missing_source_instruments" in {r["code"] for r in missing["blockers"]}
    finally:
        parked.rename(raw)


def test_corrupt_mismatched_and_holdout_contaminated_snapshots_do_not_fall_back(prepared):
    config, snapshot = prepared
    config.universe.minimum_listing_days = 1
    result = first_loop_readiness(config, snapshot_path=snapshot.path)
    assert "invalid_frozen_snapshot" in {r["code"] for r in result["blockers"]}
    config.universe.minimum_listing_days = 0
    panel_path = snapshot.path / "research/panel.parquet"
    panel = pd.read_parquet(panel_path)
    panel.index = pd.MultiIndex.from_arrays([
        pd.DatetimeIndex(["2026-01-02"] * len(panel)),
        [f"instrument-{i}" for i in range(len(panel))]], names=["datetime", "instrument"])
    panel.to_parquet(panel_path)
    result = first_loop_readiness(config, snapshot_path=snapshot.path)
    assert "invalid_frozen_snapshot" in {r["code"] for r in result["blockers"]}
    manifest_path = snapshot.path / "snapshot_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["research/panel.parquet"] = file_hash(panel_path)
    atomic_json(manifest_path, manifest)
    result = first_loop_readiness(config, snapshot_path=snapshot.path)
    assert any("holdout dates" in r.get("reason", "") for r in result["blockers"])


@pytest.mark.qlib
def test_actual_baseline_worker_and_preparation_reuse_identity(prepared):
    from etf_ml.research.execution import execute_baseline
    from etf_ml.research.first_loop import load_reusable_baseline
    config, snapshot = prepared
    output = config.artifact_root / "preparation-baseline"
    raw = config.data.source
    parked = raw.with_name("offline-during-worker")
    raw.rename(parked)  # Stronger than presence-only preflight: run the actual worker offline.
    try:
        report = execute_baseline(config, snapshot.path, output, run_id="preparation-baseline")
        reused, provenance = load_reusable_baseline(config, snapshot, output)
    finally:
        parked.rename(raw)
    assert reused == report and provenance["mode"] == "reused"
    result = first_loop_readiness(config, snapshot_path=snapshot.path, baseline_root=output)
    assert result["blockers"] == [] and result["baseline"]["status"] == "reusable"
    assert report["execution_identity"]["source_code_hash"]
    assert result["holdout_values_decoded"] is False
