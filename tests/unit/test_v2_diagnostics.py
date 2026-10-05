import json
from pathlib import Path

import pandas as pd
import pytest

from etf_ml.errors import IntegrityError, QualityError
from etf_ml.features.baseline import feature_descriptors
from etf_ml.research.diagnostics import (portfolio_exposure_diagnostics,
                                         signal_to_execution_diagnostics)
from etf_ml.research.memory_index import ResearchMemoryIndex
from etf_ml.utils import file_hash


def _backtest(root, *, scores, weights, status="filled", cost=1.0):
    root.mkdir(parents=True)
    index = pd.MultiIndex.from_product([[pd.Timestamp("2024-01-02")], ["A", "B"]],
                                       names=["datetime", "instrument"])
    pd.DataFrame({"score": scores}, index=index).to_parquet(root / "predictions.parquet")
    (root / "decisions.json").write_text(json.dumps([{"date": "2024-01-03", "signal_date": "2024-01-02",
                                                        "weights": weights, "cash_weight": 0.0}]))
    pd.DataFrame({"total_execution_cost": [0.0, cost]},
                 index=pd.DatetimeIndex(["2024-01-02", "2024-01-03"], name="datetime")).to_parquet(
        root / "execution.parquet")
    pd.DataFrame({"datetime": [pd.Timestamp("2024-01-03")], "instrument": ["A"], "status": [status]}).to_parquet(
        root / "trades.parquet")


def _reports(tmp_path, *, candidate_scores=(2.0, 1.0), candidate_weights=None,
             candidate_status="filled", candidate_cost=1.0):
    base, candidate = tmp_path / "base", tmp_path / "candidate"
    _backtest(base, scores=(2.0, 1.0), weights={"A": 1.0}, cost=1.0)
    _backtest(candidate, scores=candidate_scores, weights=candidate_weights or {"A": 1.0},
              status=candidate_status, cost=candidate_cost)
    return {"baseline": {"by_fold": [{"fold": "F", "seed": 1, "backtest_path": str(base)}]},
            "candidate": {"by_fold": [{"fold": "F", "seed": 1, "backtest_path": str(candidate)}]}}


def test_signal_to_execution_distinguishes_rank_pool_constraints_and_cost(tmp_path):
    report = signal_to_execution_diagnostics(_reports(
        tmp_path, candidate_scores=(1.0, 2.0), candidate_status="partial", candidate_cost=3.0))
    row = report["by_fold"][0]
    assert row["rank_changed"] == 1
    assert row["selection_changed"] == 0
    assert row["candidate_execution_constrained"] == 1
    assert row["execution_cost_delta"] == pytest.approx(2.0)
    assert row["signal_age_trading_sessions"]["candidate"]["median"] == 1
    assert row["execution_cost_by_signal_age"] == [{"signal_age_trading_sessions": 1,
        "decision_count": 1, "baseline_cost": 1.0, "candidate_cost": 3.0, "execution_cost_delta": 2.0}]
    assert row["observations"] == ["rank_changed_selection_unchanged", "candidate_execution_constrained",
                                   "candidate_execution_cost_higher"]
    assert row["causal_interpretation"] == "not_established"


def test_signal_to_execution_marks_old_reports_unavailable_without_rerunning():
    report = signal_to_execution_diagnostics({"baseline": {"by_fold": [{"fold": "F", "seed": 1}]},
                                              "candidate": {"by_fold": [{"fold": "F", "seed": 1}]}})
    assert report["status"] == "partial"
    assert report["by_fold"][0]["reason"] == "backtest_artifacts_not_recorded"


def test_prediction_change_is_distinct_from_rank_change(tmp_path):
    row = signal_to_execution_diagnostics(_reports(tmp_path, candidate_scores=(4., 2.)))["by_fold"][0]
    assert row["prediction_change"]["changed_rows"] == 2
    assert row["prediction_change"]["max_absolute_delta"] == 2.
    assert row["rank_changed"] == 0
    assert row["model_feature_entry"]["status"] == "unknown"
    assert row["causal_interpretation"] == "not_established"


def test_prediction_change_excludes_training_and_other_dates_outside_backtest(tmp_path):
    reports = _reports(tmp_path)
    for kind in ("baseline", "candidate"):
        path = Path(reports[kind]["by_fold"][0]["backtest_path"]) / "predictions.parquet"
        scores = pd.read_parquet(path)
        extra = scores.copy()
        extra.index = pd.MultiIndex.from_product([[pd.Timestamp("2023-01-02")], ["A", "B"]],
                                                names=scores.index.names)
        extra["score"] = 100. if kind == "candidate" else 0.
        pd.concat([extra, scores]).to_parquet(path)
    change = signal_to_execution_diagnostics(reports)["by_fold"][0]["prediction_change"]
    assert change["changed_rows"] == 0
    assert change["compared_rows"] == 2
    assert change["scope"] == "prediction_rows_on_backtest_sessions_not_label_filtered"


def test_risk_only_decisions_do_not_pollute_rank_funnel_but_all_costs_count(tmp_path):
    reports = _reports(tmp_path)
    candidate = Path(reports["candidate"]["by_fold"][0]["backtest_path"])
    path = candidate / "decisions.json"
    decisions = json.loads(path.read_text())
    decisions.append({"date": "2024-01-02", "signal_date": "2024-01-01",
                      "decision_kind": "risk_reduction", "risk_triggered": True})
    path.write_text(json.dumps(decisions))
    execution = pd.read_parquet(candidate / "execution.parquet")
    execution.iloc[0, 0] = 5.
    execution.to_parquet(candidate / "execution.parquet")
    row = signal_to_execution_diagnostics(reports)["by_fold"][0]
    assert row["risk_only_decisions"] == {"baseline": 0, "candidate": 1}
    assert row["rank_comparisons"] == 1 and row["missing_decisions"] == 0
    assert row["execution_cost_delta"] == 5.
    assert row["matched_decision_execution_cost_delta"] == 0.


def test_model_feature_manifest_entry_is_hash_bound_without_unpickling(tmp_path):
    reports = _reports(tmp_path)
    for kind, columns in (("baseline", ["momentum"]), ("candidate", ["momentum", "new_factor"])):
        root = tmp_path / (kind + "-model")
        root.mkdir()
        path = root / "manifest.json"
        path.write_text(json.dumps({"model_id": kind, "feature_names": columns}))
        reports[kind]["by_fold"][0].update(model_path=str(root), model_id=kind, model_manifest_hash=file_hash(path))
    row = signal_to_execution_diagnostics(reports)["by_fold"][0]
    assert row["model_feature_entry"]["added_features"] == ["new_factor"]
    path.write_text("{}")
    with pytest.raises(QualityError, match="hash mismatch"):
        signal_to_execution_diagnostics(reports)


def test_prediction_diagnostic_rejects_nonfinite_evidence(tmp_path):
    reports = _reports(tmp_path, candidate_scores=(float("nan"), 1.))
    with pytest.raises(QualityError, match="finite"):
        signal_to_execution_diagnostics(reports)


def test_signal_to_execution_marks_missing_prediction_or_decision_evidence_partial(tmp_path):
    reports = _reports(tmp_path)
    candidate_predictions = pd.read_parquet(Path(reports["candidate"]["by_fold"][0]["backtest_path"]) / "predictions.parquet")
    candidate_predictions.index = pd.MultiIndex.from_arrays(
        [[pd.Timestamp("2024-01-04")] * len(candidate_predictions),
         candidate_predictions.index.get_level_values("instrument")], names=candidate_predictions.index.names)
    candidate_predictions.to_parquet(Path(reports["candidate"]["by_fold"][0]["backtest_path"]) / "predictions.parquet")
    row = signal_to_execution_diagnostics(reports)["by_fold"][0]
    assert row["status"] == "partial"
    assert row["rank_comparisons"] == 0
    assert row["missing_rank_predictions"] == 1
    assert "rank_unchanged" not in row["observations"]


def test_signal_to_execution_marks_unmatched_decisions_partial(tmp_path):
    reports = _reports(tmp_path)
    candidate = Path(reports["candidate"]["by_fold"][0]["backtest_path"])
    (candidate / "decisions.json").write_text(json.dumps([]), encoding="utf-8")
    row = signal_to_execution_diagnostics(reports)["by_fold"][0]
    assert row["status"] == "partial"
    assert row["matched_decisions"] == 0
    assert row["missing_decisions"] == 1
    assert "rank_unchanged" not in row["observations"]


def test_signal_age_requires_matching_trading_calendar_evidence(tmp_path):
    reports = _reports(tmp_path)
    for kind in ("baseline", "candidate"):
        path = Path(reports[kind]["by_fold"][0]["backtest_path"])
        (path / "decisions.json").write_text(json.dumps([{"date": "2024-01-03",
            "signal_date": "2024-01-01", "weights": {"A": 1.0}, "cash_weight": 0.0}]))
    result = signal_to_execution_diagnostics(reports)
    row = result["by_fold"][0]
    assert result["status"] == row["status"] == "partial"
    assert row["missing_signal_ages"] == 1
    assert row["signal_age_trading_sessions"]["candidate"]["count"] == 0


def _trial(path, card):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"research_card": card}), encoding="utf-8")
    checkpoint = path.parent / "checkpoint.json"
    state = {"created_at_ns": 1, "trials": [{"path": path.name, "sha256": file_hash(path)}]}
    checkpoint.write_text(json.dumps(state), encoding="utf-8")


def _card(**changes):
    row = {"schema_version": "research-card-v1", "factor_id": "trend_3", "formula": "close / lag(3) - 1",
           "mechanism": "short trend", "required_fields": ["adj_close"], "lookback": 4,
           "research_group": "trend", "hypothesis": "short trend", "reason": "testable",
           "status": "rejected", "reasons": ["no_increment"], "context_hash": "c",
           "snapshot_id": "snapshot", "protocol_id": "protocol", "baseline_id": "baseline",
           "comparable": True, "card_hash": "card"}
    row.update(changes)
    return row


def test_cross_session_index_is_restartable_marks_noncomparable_and_rejects_sensitive_cards(tmp_path):
    _trial(tmp_path / "sessions" / "one" / "trial-00000.json", _card())
    _trial(tmp_path / "sessions" / "two" / "trial-00000.json", _card(factor_id="other", baseline_id="other"))
    index = ResearchMemoryIndex(tmp_path)
    first = index.rebuild()
    assert first["ledger"]["trial_count"] == 2
    recovered = ResearchMemoryIndex(tmp_path).query(snapshot_id="snapshot", protocol_id="protocol",
                                                    baseline_id="baseline", fields=["adj_close"])
    assert recovered[0]["factor_id"] == "trend_3" and recovered[0]["comparable"] is True
    assert any(row["factor_id"] == "other" and row["comparable"] is False for row in recovered)
    _trial(tmp_path / "sessions" / "bad" / "trial-00000.json", _card(hypothesis="holdout result"))
    with pytest.raises(QualityError, match="disallowed"):
        index.rebuild()


def test_memory_index_ignores_orphans_and_rejects_tampered_committed_trials(tmp_path):
    committed = tmp_path / "sessions" / "one" / "trial-00000.json"
    _trial(committed, _card())
    orphan = committed.parent / "trial-99999.json"
    orphan.write_text(json.dumps({"research_card": _card(factor_id="orphan")}), encoding="utf-8")
    assert ResearchMemoryIndex(tmp_path).rebuild()["ledger"]["trial_count"] == 1
    committed.write_text(json.dumps({"research_card": _card(formula="tampered")}), encoding="utf-8")
    with pytest.raises(IntegrityError, match="evidence changed"):
        ResearchMemoryIndex(tmp_path).rebuild()


def test_memory_index_scans_project_level_runs(tmp_path):
    _trial(tmp_path / "run-one" / "sessions" / "one" / "trial-00000.json", _card(factor_id="one"))
    _trial(tmp_path / "run-two" / "sessions" / "two" / "trial-00000.json", _card(factor_id="two"))
    index = ResearchMemoryIndex(tmp_path)
    assert {card["factor_id"] for card in index.rebuild()["cards"]} == {"one", "two"}


def test_portfolio_exposure_uses_date_valid_pit_groups_and_reconciles_equity(tmp_path):
    dates = pd.to_datetime(["2026-01-02", "2026-01-05"])
    positions = pd.DataFrame([
        (dates[0], "CASH", 500., 1.), (dates[0], "ETF_A", 100., 3.),
        (dates[0], "ETF_B", 100., 2.), (dates[1], "CASH", 400., 1.),
        (dates[1], "ETF_A", 100., 3.5), (dates[1], "ETF_B", 100., 2.5),
    ], columns=["datetime", "instrument", "shares", "raw_price"])
    positions.to_parquet(tmp_path / "positions.parquet", index=False)
    pd.DataFrame({"independent_equity": [1000., 1000.], "independent_cash": [500., 400.],
        "independent_receivable": [0., 0.], "independent_income": [0., 0.],
        "equity_residual": [0., 0.]}, index=dates).to_parquet(tmp_path / "ledger.parquet")
    index = pd.MultiIndex.from_product([dates, ["ETF_A", "ETF_B"]], names=["datetime", "instrument"])
    universe = pd.DataFrame({"tracking_group": ["broad", "broad", "large", "tech"]}, index=index)

    result = portfolio_exposure_diagnostics(tmp_path, universe, pit_verified=True)

    assert result["status"] == "completed"
    assert result["by_date"][0]["groups"][0]["tracking_group"] == "broad"
    assert result["by_date"][0]["max_group_weight"] == pytest.approx(.5)
    assert result["by_date"][0]["groups_with_multiple_etfs"] == 1
    assert result["by_date"][1]["groups_with_multiple_etfs"] == 0
    assert result["by_date"][1]["max_single_etf_weight"] == pytest.approx(.35)

    missing = universe.copy()
    missing.loc[(dates[1], "ETF_B"), "tracking_group"] = None
    assert portfolio_exposure_diagnostics(tmp_path, missing, pit_verified=True)["status"] == "partial"
    assert portfolio_exposure_diagnostics(tmp_path, universe, pit_verified=False)["status"] == "bypassed"
    ledger = pd.read_parquet(tmp_path / "ledger.parquet")
    ledger.loc[dates[0], "independent_equity"] = 999.
    ledger.to_parquet(tmp_path / "ledger.parquet")
    with pytest.raises(QualityError, match="does not reconcile"):
        portfolio_exposure_diagnostics(tmp_path, universe, pit_verified=True)


def test_accumulated_feature_descriptor_uses_verified_spec_not_name_parsing():
    composition = {"factors": [{"column": "momentum_three_v1", "spec": {
        "formula": "adj_close / adj_close.shift(3) - 1", "research_group": "trend"}}]}
    rows = {row["feature_id"]: row for row in feature_descriptors(
        ["momentum_5", "momentum_three_v1", "unknown_feature"], composition=composition)}
    assert rows["momentum_three_v1"]["formula"] == "adj_close / adj_close.shift(3) - 1"
    assert rows["unknown_feature"]["formula"] is None
