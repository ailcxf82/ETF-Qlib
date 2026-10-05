import json

import pandas as pd
import pytest

from etf_ml.research.diagnostics import paired_daily_risk_attribution, risk_breach_events


def _risk_artifact(root, equity, *, checked=True):
    root.mkdir()
    (root / "risk_checks.json").write_text(json.dumps([
        {"date": "2026-01-02", "checked": checked, "reason": None if checked else "not_scheduled_rebalance",
         "risk_mode": "max_drawdown", "initial_equity": 100., "signal_date": "2026-01-01",
         "decision_valuation_basis": "current_session_open" if checked else None,
         "execution_reference_price_basis": "current_session_open" if checked else None,
         "decision_execution_timing_status": "same_session_open_ordering_unverified" if checked else "not_checked",
         "drawdown": .1, "risk_triggered": True, "generated_order_count": 1,
         "risk_order_intents": [], "risk_order_blockers": []},
    ]), encoding="utf-8")
    pd.DataFrame({"independent_equity": [equity], "independent_cash": [20.],
                  "independent_receivable": [0.], "independent_income": [0.],
                  "cash_residual": [0.], "equity_residual": [0.], "return_residual": [0.]},
                 index=pd.to_datetime(["2026-01-02"])).to_parquet(root / "ledger.parquet")
    pd.DataFrame({"datetime": [pd.Timestamp("2026-01-02")], "instrument": ["510300.SH"],
                  "direction": [0], "requested_shares": [100.], "filled_shares": [0.],
                  "status": ["unfilled"], "reason": ["limit_down"]}).to_parquet(
                      root / "trades.parquet", index=False)


def test_daily_risk_attribution_pairs_equity_risk_checks_and_execution(tmp_path):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    _risk_artifact(baseline, 90.)
    _risk_artifact(candidate, 88.)

    result = paired_daily_risk_attribution(baseline, candidate)

    assert result["status"] == "completed"
    row = result["by_date"][0]
    assert row["baseline"]["drawdown"] == pytest.approx(.1)
    assert row["candidate"]["risk_triggered"] is True
    assert row["candidate"]["decision_execution_timing_status"] == "same_session_open_ordering_unverified"
    assert row["candidate"]["decision_valuation_basis"] == "current_session_open"
    assert row["candidate"]["unfilled_reasons"] == ["limit_down"]
    assert row["candidate_minus_baseline_drawdown"] > 0
    assert row["interpretation"] == "paired_artifact_fact_not_causal_attribution"


def test_daily_risk_attribution_reports_missing_trace_as_partial(tmp_path):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    _risk_artifact(baseline, 90.)
    candidate.mkdir()

    result = paired_daily_risk_attribution(baseline, candidate)

    assert result["status"] == "partial"
    assert result["reason"] == "daily_risk_evidence_unavailable"


def test_risk_events_keep_close_observation_separate_from_same_day_execution():
    rows = []
    for day, breach, checked in [("2026-01-02", True, False), ("2026-01-05", True, True),
                                ("2026-01-06", False, True)]:
        rows.append({"date": day, "risk_limit_exceeded_at_close": breach, "drawdown": .15 if breach else .10,
                     "risk_check_status": "checked" if checked else "not_scheduled_rebalance",
                     "risk_triggered": checked and breach, "unfilled_reasons": ["limit_down"] if breach else [],
                     "risk_order_attempted": day == "2026-01-05",
                     "risk_order_intents": ([{"execution_link_status": "matched", "filled_shares": 20.}]
                                             if day == "2026-01-05" else []),
                     "risk_order_blockers": ([{"instrument": "510500.SH", "reason": "limit_down",
                                                "held_shares": 100.}] if day == "2026-01-05" else [])})
    events = risk_breach_events(rows)
    assert len(events) == 1
    assert events[0]["first_observable_close"] == "2026-01-02"
    assert events[0]["first_later_risk_check"] == "2026-01-05"
    assert events[0]["first_later_risk_check_timing_status"] == "unknown_legacy_trace"
    assert events[0]["first_later_risk_order_attempt"] == "2026-01-05"
    assert events[0]["first_later_matched_risk_fill"] == "2026-01-05"
    assert events[0]["risk_order_blockers"][0]["reason"] == "limit_down"
    assert events[0]["unchecked_breach_days"] == 1
    assert events[0]["earliest_legal_exit"] is None
    assert events[0]["observed_unfilled_reasons"] == ["limit_down"]


def test_risk_audit_detects_decision_peak_below_previous_close_peak(tmp_path):
    base, candidate = tmp_path / "base", tmp_path / "candidate"
    for root in (base, candidate):
        _risk_artifact(root, 110.)
        ledger = pd.read_parquet(root / "ledger.parquet")
        second = ledger.copy()
        second.index = pd.to_datetime(["2026-01-05"])
        second["independent_equity"] = 90.
        pd.concat([ledger, second]).to_parquet(root / "ledger.parquet")
        checks = json.loads((root / "risk_checks.json").read_text())
        checks[0].update(drawdown_limit=.12, peak_equity_at_decision=100., equity_at_decision=100.)
        checks.append({**checks[0], "date": "2026-01-05", "signal_date": "2026-01-02",
                       "equity_at_decision": 92.})
        (root / "risk_checks.json").write_text(json.dumps(checks))
    result = paired_daily_risk_attribution(base, candidate)
    day = result["by_date"][1]["candidate"]
    assert day["prior_close_high_water_mark"] == 110.
    assert day["decision_high_water_mark"] == 100.
    assert day["prior_close_peak_above_decision_peak"] is True
    assert day["drawdown"] == pytest.approx(1 - 90 / 110)
    assert day["prior_signal_verified"] is True


def test_risk_audit_rejects_duplicate_ledger_dates(tmp_path):
    from etf_ml.errors import QualityError
    base, candidate = tmp_path / "base", tmp_path / "candidate"
    for root in (base, candidate):
        _risk_artifact(root, 90.)
    ledger = pd.read_parquet(base / "ledger.parquet")
    pd.concat([ledger, ledger]).to_parquet(base / "ledger.parquet")
    with pytest.raises(QualityError, match="unique sorted"):
        paired_daily_risk_attribution(base, candidate)


def test_daily_risk_attribution_links_explicit_risk_sell_intent_to_fill(tmp_path):
    base, candidate = tmp_path / "base", tmp_path / "candidate"
    _risk_artifact(base, 90.)
    _risk_artifact(candidate, 88.)
    root = candidate
    checks = json.loads((root / "risk_checks.json").read_text())
    checks[0]["risk_order_intents"] = [{"instrument": "510300.SH", "direction": "sell",
        "requested_shares": 100., "order_date": "2026-01-02", "signal_date": "2026-01-01"}]
    (root / "risk_checks.json").write_text(json.dumps(checks))
    trades = pd.read_parquet(root / "trades.parquet")
    trades.loc[0, "filled_shares"] = 40.
    trades.loc[0, "status"] = "partial"
    trades.loc[0, "reason"] = "liquidity_limit"
    trades.to_parquet(root / "trades.parquet", index=False)

    result = paired_daily_risk_attribution(base, candidate)

    row = result["by_date"][0]["candidate"]
    assert row["risk_order_link_status"] == "completed"
    assert row["risk_order_filled_shares"] == 40.
    assert row["risk_order_intents"][0]["execution_status"] == "partial"
    assert row["risk_order_intents"][0]["failure_reason"] == "liquidity_limit"


def test_risk_order_linkage_keeps_legacy_trace_unknown_and_rejects_duplicate_fills(tmp_path):
    from etf_ml.errors import QualityError
    base, candidate = tmp_path / "base", tmp_path / "candidate"
    _risk_artifact(base, 90.)
    _risk_artifact(candidate, 88.)
    checks = json.loads((candidate / "risk_checks.json").read_text())
    checks[0].pop("risk_order_intents")
    checks[0].pop("risk_order_blockers")
    (candidate / "risk_checks.json").write_text(json.dumps(checks))
    checks = json.loads((base / "risk_checks.json").read_text())
    checks[0].pop("risk_order_intents")
    checks[0].pop("risk_order_blockers")
    (base / "risk_checks.json").write_text(json.dumps(checks))
    checks[0]["risk_order_intents"] = [{"instrument": "510300.SH", "direction": "sell",
        "requested_shares": 100., "order_date": "2026-01-02", "signal_date": "2026-01-01"}]
    (candidate / "risk_checks.json").write_text(json.dumps(checks))
    trades = pd.read_parquet(candidate / "trades.parquet")
    pd.concat([trades, trades]).to_parquet(candidate / "trades.parquet", index=False)
    with pytest.raises(QualityError, match="duplicate matching sell executions"):
        paired_daily_risk_attribution(base, candidate)

    base_checks = json.loads((base / "risk_checks.json").read_text())
    (base / "risk_checks.json").write_text(json.dumps(base_checks))
    (candidate / "risk_checks.json").write_text(json.dumps([{
        "date": "2026-01-02", "checked": True, "risk_mode": "max_drawdown",
        "initial_equity": 100., "drawdown_limit": .12, "risk_triggered": True,
        "risk_order_intents": [], "risk_order_blockers": []}]))
    result = paired_daily_risk_attribution(base, candidate)
    assert result["status"] == "partial"
    assert result["by_date"][0]["baseline"]["risk_order_link_status"] == "unknown_legacy_trace"
    assert result["by_date"][0]["candidate"]["risk_order_link_status"] == "completed"
