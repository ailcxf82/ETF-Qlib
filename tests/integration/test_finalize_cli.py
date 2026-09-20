import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from etf_ml.cli import main
from etf_ml.config import load_config
from etf_ml.contracts import ModelSpec, UniversePolicy
from etf_ml.data.snapshot import build_snapshot
from etf_ml.features.baseline import materialize
from etf_ml.research.finalize import load_frozen_features
from etf_ml.research.protocol import ComparisonProtocol
from etf_ml.research.session import ResearchSession

pytestmark = pytest.mark.qlib


def test_actual_cli_feature_freeze_three_model_multiseed_comparison_and_integrity(
        source_spec, fold, tmp_path, capsys):
    source_spec.holdout_start = "2026-01-01"
    # Synthetic acceptance limits are declared before results. Holdout is not run.
    config = load_config(overrides={
        "artifact_root": tmp_path / "a",
        "portfolio": {"k_mode": "fraction", "minimum_commission": 0,
                      "liquidity_mode": "participation", "risk_mode": "max_drawdown"},
        "validation": {"folds": [fold.model_dump()]},
        "research": {"budget_mode": "free_only", "seeds": [42, 43]},
        "acceptance": {"minimum_net_return": -.99, "minimum_excess_return": -1.,
                       "maximum_drawdown": .12, "maximum_annualized_volatility": 1.,
                       "maximum_execution_cost_over_initial_equity": 1., "minimum_effective_dates": 5},
    })
    config.data = source_spec
    config.universe = UniversePolicy(minimum_listing_days=0, liquidity_lookback=1)
    config.models = [
        ModelSpec(name="ridge"),
        ModelSpec(name="lightgbm", constructor={"num_boost_round": 8, "early_stopping_rounds": 3,
                  "min_data_in_leaf": 5, "num_threads": 1}, fit={"verbose_eval": 0}),
        ModelSpec(name="xgboost", constructor={"max_depth": 3, "eta": .1, "nthread": 1},
                  fit={"num_boost_round": 8, "early_stopping_rounds": 3, "verbose_eval": False}),
    ]
    snapshot = build_snapshot(source_spec.source, source_spec, config.universe)
    panel = pd.read_parquet(snapshot.path / "research" / "panel.parquet")
    base = materialize({"snapshot_id": snapshot.snapshot_id}, panel)
    protocol = ComparisonProtocol(
        snapshot_id=snapshot.snapshot_id, baseline_feature_set_id=base.feature_set_id,
        label=config.label, universe=config.universe, validation=config.validation,
        portfolio=config.portfolio, research=config.research, model=config.models[1],
        stress_min_excess_return=-1., cost_multipliers=[3.])
    session = ResearchSession(config, snapshot.path, protocol, root=tmp_path / "a" / "research" / "study")
    session.save(session.root / "session.json")
    config_path = tmp_path / "freeze.yaml"
    config_path.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8")
    freeze_args = ["freeze-features", "--config", str(config_path), "--session", str(session.root / "session.json"),
                   "--run-id", "freeze-fixture"]
    assert main(freeze_args) == 0
    result = json.loads(capsys.readouterr().out)
    frozen_path = Path(result["frozen_feature_path"])
    features, manifest = load_frozen_features(frozen_path, config=config, snapshot=snapshot)
    assert features.feature_set_id == base.feature_set_id
    assert manifest["review"]["groups"] == {}
    assert manifest["acceptance"] == config.acceptance.model_dump(mode="json")
    pd.testing.assert_frame_equal(features.frame, base.frame)
    assert not (features.frame.index.get_level_values("datetime") >= pd.Timestamp("2026-01-01")).any()
    compare_args = ["compare-models", "--config", str(config_path), "--snapshot", str(snapshot.path),
                    "--frozen-features", str(frozen_path), "--run-id", "compare-fixture"]
    assert main(compare_args) == 0
    comparison = json.loads(capsys.readouterr().out)
    assert comparison["fold_model_runs"] == 6
    assert set(comparison["ranking"]) == {"ridge", "lightgbm", "xgboost"}
    assert comparison["selection_status"] == "awaiting_explicit_model_freeze"
    report = json.loads((tmp_path / "a" / "runs" / "compare-fixture" / "comparison.json").read_text())
    assert {r["seed"] for r in report["report"]["by_fold"]} == {42, 43}
    assert len({r["daily_index_hash"] for r in report["report"]["by_fold"]}) == 1
    for row in report["report"]["by_fold"]:
        assert set(row["cost_stress"]) == {"3.0"}
        assert row["cost_stress"]["3.0"]["accounting_reconciled"]
        model = json.loads((Path(row["model_path"]) / "manifest.json").read_text())
        assert model["feature_names"] == list(base.frame.columns)
    times = {p: p.stat().st_mtime_ns for p in (tmp_path / "a" / "comparisons" / "experiments").glob("*/manifest.json")}
    assert len(times) == 2
    assert main(freeze_args) == 0
    assert json.loads(capsys.readouterr().out)["reused"]
    assert main(compare_args) == 0
    assert json.loads(capsys.readouterr().out)["reused"]
    assert times == {p: p.stat().st_mtime_ns for p in times}
    # Explicit model selection binds the full weights/processor/features package.
    from etf_ml.models.deployment import load_frozen_model
    from etf_ml.models import predict
    from etf_ml.registry.model_versions import ModelVersionRegistry
    freeze_model_args = ["freeze-model", "--config", str(config_path),
        "--frozen-features", str(frozen_path), "--comparison", comparison["comparison_path"],
        "--model", "ridge", "--fold", "fixture", "--seed", "42",
        "--selection-reason", "Synthetic explicit baseline selection before holdout", "--run-id", "model-freeze"]
    assert main(freeze_model_args) == 0
    model_output = capsys.readouterr()
    assert model_output.out, repr(model_output)
    model_result = json.loads(model_output.out)
    model_package = Path(model_result["frozen_model_path"])
    bundle, frozen_frame, frozen_model = load_frozen_model(model_package, config=config)
    assert frozen_model["registry_state"] == "frozen"
    assert frozen_model["investment_status"] == "not_evaluated"
    assert frozen_model["selection"]["model"] == "ridge"
    original_predictions = pd.read_parquet(tmp_path / "a" / "comparisons" / "experiments" /
        report["report"]["child_runs"][0]["run_id"] / "fixture" / "ridge" / "predictions.parquet").score
    pd.testing.assert_series_equal(predict(bundle, frozen_frame.frame), original_predictions)
    assert main(freeze_model_args) == 0
    assert json.loads(capsys.readouterr().out)["reused"]
    # Loading a completed command also verifies the copied weights and marker.
    for damaged_file in (model_package / "published.json",
                         model_package / frozen_model["model_relative_path"] / "bundle.pkl"):
        original_bytes = damaged_file.read_bytes()
        damaged_file.write_bytes(original_bytes + b"corrupt")
        assert main(freeze_model_args) == 5
        capsys.readouterr()
        damaged_file.write_bytes(original_bytes)
    assert main(freeze_model_args) == 0
    assert json.loads(capsys.readouterr().out)["reused"]
    # Altering explicit selection with the same run ID is refused.
    other_selection = [a if a != "ridge" else "lightgbm" for a in freeze_model_args]
    assert main(other_selection) == 2
    capsys.readouterr()
    ModelVersionRegistry(tmp_path / "a" / "model_versions").transition(
        model_result["version_id"], "retired", reasons=["synthetic governance retirement"])
    assert main(freeze_model_args) == 5
    capsys.readouterr()
    # A changed final threshold cannot silently reuse the old feature freeze.
    changed = config.model_dump(mode="json")
    changed["acceptance"]["minimum_excess_return"] = -.5
    changed_path = tmp_path / "changed.yaml"
    changed_path.write_text(yaml.safe_dump(changed), encoding="utf-8")
    changed_args = [a if a != str(config_path) else str(changed_path) for a in compare_args]
    changed_args[-1] = "different-threshold"
    assert main(changed_args) == 2
    capsys.readouterr()
    # Successful command reuse verifies external experiment ledgers.
    trade_file = next((tmp_path / "a" / "comparisons" / "experiments").glob("*/fixture/ridge/trades.parquet"))
    with trade_file.open("ab") as stream:
        stream.write(b"corrupt")
    assert main(compare_args) == 5
    capsys.readouterr()
    # Successful freeze command reuse also verifies its own feature data.
    with (frozen_path / "features.parquet").open("ab") as stream:
        stream.write(b"corrupt")
    assert main(freeze_args) == 5
