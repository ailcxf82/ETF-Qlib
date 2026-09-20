import json
import pandas as pd
import pytest
from etf_ml.config import load_config
from etf_ml.contracts import UniversePolicy, FoldSpec
from etf_ml.data.snapshot import build_snapshot
from etf_ml.pipeline import run_baseline

pytestmark = pytest.mark.qlib


def test_source_bound_income_reaches_standard_baseline_and_pressure(source_spec, calendar, income_source, tmp_path):
    # Governed synthetic source tests the actual standard route; never real G0.
    metadata = pd.read_parquet(source_spec.metadata_path)
    metadata.loc[metadata.instrument.ne("510300.SH"), "operating"] = False
    metadata.to_parquet(source_spec.metadata_path, index=False)
    fold = FoldSpec(name="income", train={"start": str(calendar[0].date()), "end": str(calendar[79].date())},
        early_stop={"start": str(calendar[80].date()), "end": str(calendar[99].date())},
        selection={"start": str(calendar[100].date()), "end": str(calendar[129].date())})
    config = load_config(overrides={"artifact_root": tmp_path / "artifacts",
        "portfolio": {"minimum_commission": 0., "commission_rate": .0003},
        "validation": {"folds": [fold.model_dump()], "holdout_start": source_spec.holdout_start},
        "models": [{"name": "ridge"}], "research": {"budget_mode": "free_only"}})
    config.data = source_spec
    config.universe = UniversePolicy(minimum_listing_days=0, liquidity_lookback=1)
    snapshot = build_snapshot(source_spec.source, source_spec, config.universe)
    output = tmp_path / "baseline"
    result = run_baseline(config, snapshot.path, output, run_id="income-baseline")
    folder = output / "income/ridge"
    for path in [folder, folder / "cost-2.0"]:
        ledger = pd.read_parquet(path / "ledger.parquet")
        postings = pd.read_parquet(path / "income_postings.parquet")
        assert ledger.reported_income.max() > 0 and ledger.income_residual.abs().max() < 1e-6
        earned = postings[postings.kind.eq("accrual")]
        # Independent per-lot amount from actual held shares, excluding zero balances.
        assert len(earned) > 0
        assert earned.booked_income.sum() == pytest.approx((earned.held_shares * .01 / 100).sum())
        assert earned.unallocated_rounding_residual.eq(0).all()
        assert earned.datetime.min() >= calendar[100] and earned.datetime.max() <= calendar[129]
    metrics = result["by_fold"][0]["portfolio"]
    assert metrics["income_validation"]["enabled"] and metrics["accounting_reconciled"]
    assert not metrics["income_validation"]["rounding_proxy"]
