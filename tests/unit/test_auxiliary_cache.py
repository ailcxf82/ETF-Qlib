from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from etf_ml.backtest.auxiliary import run_auxiliary
from etf_ml.config import load_config
from etf_ml.contracts import UniversePolicy


def test_auxiliary_cache_reuses_deterministic_strategy_results(
        panel, calendar, fold, tmp_path, monkeypatch):
    config = load_config(overrides={
        "portfolio": {"k_mode": "fraction", "minimum_commission": 0,
                      "liquidity_mode": "participation", "risk_mode": "max_drawdown"},
        "validation": {"folds": [fold.model_dump()]},
    })
    config.universe = UniversePolicy(minimum_listing_days=0, liquidity_lookback=1)
    eligible = pd.Series(True, index=panel.index)
    calls = []

    def evaluate(scores, policy, panel, *, output, cost_multipliers, **kwargs):
        calls.append((scores.name, str(output)))
        callback = kwargs.get("phase_callback")
        if callback:
            callback("base_started")
        days = kwargs["calendar"][(kwargs["calendar"] >= kwargs["start_time"]) &
                                    (kwargs["calendar"] <= kwargs["end_time"])]
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        pd.Series(0.0, index=days).to_frame().to_parquet(output / "daily_returns.parquet")
        for multiplier in cost_multipliers:
            if callback:
                callback("cost_stress_started", multiplier=multiplier)
            stress = output / ("cost-" + str(multiplier))
            stress.mkdir(parents=True, exist_ok=True)
            pd.Series(0.0, index=days).to_frame().to_parquet(stress / "daily_returns.parquet")
            if callback:
                callback("cost_stress_completed", multiplier=multiplier)
        if callback:
            callback("base_completed")
        return SimpleNamespace(daily_returns=pd.Series(0.0, index=days), metrics={"cached": False}), {}

    monkeypatch.setattr("etf_ml.backtest.auxiliary.evaluate_with_stress", evaluate)
    evaluation_index = panel.index[panel.index.get_level_values("datetime") >= pd.Timestamp(fold.selection.start)]
    common = {"calendar": calendar, "start_time": calendar[120], "end_time": calendar[-1]}
    arguments = dict(config=config, panel=panel, research_calendar=calendar,
                     decision_universe=SimpleNamespace(eligible=eligible), fold=fold,
                     snapshot_id="fixture", protocol_id="protocol", labels=panel["return_1d"],
                     evaluation_index=evaluation_index, cost_multipliers=(2.0,), **common)

    events = []
    progress = SimpleNamespace(emit=lambda phase, **event: events.append({"phase": phase, **event}))
    first = run_auxiliary(output=tmp_path / "first", cache_root=tmp_path / "cache",
                          progress=progress, progress_fields={"kind": "baseline", "seed": 42}, **arguments)
    monkeypatch.setattr("etf_ml.backtest.auxiliary.momentum_scores",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("cache hit built scores")))
    second = run_auxiliary(output=tmp_path / "second", cache_root=tmp_path / "cache",
                           progress=progress, progress_fields={"kind": "candidate", "seed": 43}, **arguments)

    assert len(calls) == 2
    assert first == second
    assert all((tmp_path / "cache") in Path(row["artifact_path"]).parents for row in second)
    completed = [event for event in events if event["phase"] == "auxiliary_strategy_completed"]
    assert {event["strategy"] for event in completed} == {"manual_momentum", "equal_weight_pool"}
    assert [event["cache_reused"] for event in completed] == [False, False, True, True]
    assert all(event["kind"] in {"baseline", "candidate"} for event in completed)
    assert {event["phase"] for event in events} >= {
        "auxiliary_backtest_base_started", "auxiliary_backtest_base_completed",
        "auxiliary_backtest_cost_stress_started", "auxiliary_backtest_cost_stress_completed",
    }
