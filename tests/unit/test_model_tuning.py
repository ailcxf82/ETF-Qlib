import pytest

from etf_ml.config import load_config
from etf_ml.errors import ConfigurationError, IntegrityError
from etf_ml.research.model_tuning import TuningPlan, run_screen, summarize


@pytest.mark.parametrize("variants", [
    {"baseline": {}, "bad": {"objective": "binary"}},
    {"baseline": {"num_leaves": 7}, "small": {}},
    {"baseline": {}, "../escape": {"num_leaves": 7}},
    {"baseline": {}, "bad": {"feature_fraction": 0}},
    {"baseline": {}, "bad": {"num_leaves": 1}},
    {"baseline": {}, "bad": {"lambda_l2": float("nan")}},
    {"baseline": {}, "bad": {"min_data_in_leaf": True}},
])
def test_tuning_rejects_task_changes_and_invalid_parameters(variants):
    with pytest.raises(ValueError):
        TuningPlan(variants=variants)


def test_fit_budget_is_checked_before_any_data_read_or_output(tmp_path):
    plan = TuningPlan(variants={"baseline": {}, "small": {"num_leaves": 7}}, max_fits=1)
    with pytest.raises(ConfigurationError, match="budget"):
        run_screen(load_config(), tmp_path / "missing-snapshot", plan, tmp_path / "new-output")
    assert not (tmp_path / "new-output").exists()


def test_summary_does_not_promote_unknown_rank_or_mismatched_samples():
    plan = TuningPlan(variants={"baseline": {}, "small": {"num_leaves": 7}})
    rows = [{"fold": "A", "seed": 42, "variant": name, "validation_index_hash": "fixed",
             "validation": {"mse": 1., "rank_ic": None}, "training": {"best_iteration": 1}}
            for name in plan.variants]
    summary = summarize(rows, plan, ["A", "B"])
    assert summary[1]["expected_fits"] == 2
    assert summary[1]["valid_rank_comparisons"] == 0
    assert summary[1]["median_rank_ic_delta"] is None
    rows[1]["validation_index_hash"] = "different"
    with pytest.raises(IntegrityError, match="validation sample"):
        summarize(rows, plan, ["A"])
