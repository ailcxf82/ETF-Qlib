import json

import pandas as pd
import pytest

from etf_ml.config import load_config
from etf_ml.errors import ConfigurationError, IntegrityError
from etf_ml.research.model_tuning import TuningPlan, research_inputs, run_screen
from etf_ml.utils import atomic_json, file_hash

pytestmark = pytest.mark.qlib


@pytest.fixture
def tuning_snapshot(tmp_path, panel, calendar, fold):
    config = load_config()
    config.validation.folds = [fold]
    root = tmp_path / "synthetic-snapshot"
    (root / "research").mkdir(parents=True)
    panel.to_parquet(root / "research" / "panel.parquet")
    pd.DataFrame({"eligible": True}, index=panel.index).to_parquet(root / "universe.parquet")
    (root / "calendar.txt").write_text("\n".join(calendar.strftime("%Y-%m-%d")), encoding="utf8")
    files = {name: file_hash(root / name) for name in
             ("research/panel.parquet", "calendar.txt", "universe.parquet")}
    # Deliberately nonexistent: a research-only screen must not even hash this file.
    files["holdout/panel.parquet"] = "unreadable-holdout"
    atomic_json(root / "snapshot_manifest.json", {"snapshot_id": root.name,
        "spec": {"holdout_start": config.validation.holdout_start},
        "universe_policy": config.universe.model_dump(), "files": files,
        "qualification": {"formal_g0_passed": False}})
    return config, root


def test_parameter_screen_persists_evidence_without_selection_or_holdout(tuning_snapshot, tmp_path):
    config, snapshot = tuning_snapshot
    plan = TuningPlan(variants={"baseline": {}, "small_leaf": {"min_data_in_leaf": 5}},
                      num_boost_round=5, early_stopping_rounds=2, num_threads=1, max_fits=2)
    output = tmp_path / "screen"
    report = run_screen(config, snapshot, plan, output)
    assert report["status"] == "completed" and len(report["rows"]) == 2
    assert not any(report[k] for k in ("holdout_evaluated", "selection_evaluated", "portfolio_evaluated", "investment_accepted"))
    assert report["qualification"]["formal_g0_passed"] is False
    for row in report["rows"]:
        manifest = json.loads((output / "models" / row["model_id"] / "manifest.json").read_text())
        assert manifest["training"]["effective_parameters_source"] == "lightgbm_native_model_export"
        assert manifest["training"]["evaluation_history"]["valid"]["l2"]
        predictions = pd.read_parquet(output / f"fixture-42-{row['variant']}-validation.parquet")
        dates = predictions.index.get_level_values("datetime")
        assert dates.min() >= pd.Timestamp(config.validation.folds[0].early_stop.start)
        assert dates.max() < pd.Timestamp(config.validation.folds[0].selection.start)
    assert (output / "report.md").is_file()
    with pytest.raises(ConfigurationError, match="already exists"):
        run_screen(config, snapshot, plan, output)


def test_screen_rejects_changed_input_and_moved_holdout_boundary(tuning_snapshot):
    config, root = tuning_snapshot
    config.validation.holdout_start = "2027-01-01"
    with pytest.raises(ConfigurationError, match="holdout boundary"):
        research_inputs(root, config)
    config.validation.holdout_start = "2026-01-01"
    with (root / "calendar.txt").open("a") as stream:
        stream.write("\n2023-12-31")
    with pytest.raises(IntegrityError, match="hash mismatch"):
        research_inputs(root, config)
