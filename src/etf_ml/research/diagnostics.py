"""Development-only observations from existing paired experiments.

These diagnostics never change selection gates or read the holdout view.
Full date-level results stay in artifacts; prompts receive compact summaries.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from etf_ml.backtest.metrics import (positive_icir_gate, predictive_metrics,
                                     select_factor_orientation, summarize_icir_gates)
from etf_ml.datasets import generate_labels
from etf_ml.datasets.splits import learning_mask, validate_fold
from etf_ml.data.calendar import require_calendar
from etf_ml.data.source import require_panel
from etf_ml.errors import QualityError
from etf_ml.features.validators import redundancy
from etf_ml.utils import content_hash, file_hash

PREDICTIVE_KEYS = ('ic', 'ic_mean', 'ic_std', 'icir', 'ic_positive_day_share',
                   'rank_ic', 'rank_ic_mean', 'rank_ic_std', 'rank_icir',
                   'rank_ic_positive_day_share', 'coverage', 'effective_dates', 'total_dates')
PREDICTIVE_STATUS_KEYS = ('ic_direction', 'icir_status', 'rank_ic_direction', 'rank_icir_status')
PORTFOLIO_KEYS = ('net_return', 'excess_return', 'max_drawdown', 'annualized_volatility',
                  'turnover', 'commission', 'slippage_cost', 'total_execution_cost',
                  'effective_dates', 'max_single_weight', 'max_group_weight')


def _summary(metrics, *, include_by_date=False):
    summary = {key: metrics.get(key) for key in PREDICTIVE_KEYS + PREDICTIVE_STATUS_KEYS}
    if include_by_date:
        summary['by_date'] = metrics.get('by_date', [])
    return summary


def _metrics(metrics):
    return {key: metrics.get(key) for key in PORTFOLIO_KEYS}


def _decision_map(path: Path):
    payload = json.loads((path / "decisions.json").read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise QualityError("Backtest decisions must be a list")
    result = {}
    for row in payload:
        if not isinstance(row, dict) or not isinstance(row.get("date"), str):
            raise QualityError("Backtest decision lacks a date")
        if row["date"] in result:
            raise QualityError("Backtest has duplicate decision dates")
        result[row["date"]] = row
    return result


def _top_scores(scores, signal_date, count):
    if count <= 0:
        return ()
    try:
        values = scores.xs(pd.Timestamp(signal_date), level="datetime").iloc[:, 0]
    except (KeyError, ValueError):
        return None
    values = values.replace([np.inf, -np.inf], np.nan).dropna()
    return tuple(str(name) for name in values.sort_values(ascending=False, kind="mergesort").head(count).index)


def _execution_rows(path: Path):
    execution = pd.read_parquet(path / "execution.parquet")
    if not isinstance(execution.index, pd.DatetimeIndex):
        raise QualityError("Execution evidence requires a datetime index")
    trades = pd.read_parquet(path / "trades.parquet")
    if "datetime" not in trades or "status" not in trades:
        raise QualityError("Trade evidence is missing datetime or status")
    return execution, trades


def _trading_session_age(sessions, signal_date, decision_date):
    if not signal_date or not decision_date:
        return None
    signal = pd.Timestamp(signal_date).normalize()
    decision = pd.Timestamp(decision_date).normalize()
    positions = sessions.get_indexer([signal, decision])
    if (positions < 0).any() or positions[0] >= positions[1]:
        return None
    return int(positions[1] - positions[0])


def _risk_order_attribution(check, day_trades, date):
    """Link explicit risk sell intents to same-session sell execution rows."""
    if check is None or "risk_order_intents" not in check:
        return "unknown_legacy_trace", []
    intents = check["risk_order_intents"]
    if not isinstance(intents, list):
        raise QualityError("Risk order intents must be a list")
    if check.get("risk_triggered") is not True and intents:
        raise QualityError("Non-triggered risk check cannot contain risk sell intents")
    results = []
    for intent in intents:
        required = {"instrument", "direction", "requested_shares", "order_date", "signal_date"}
        if (not isinstance(intent, dict) or not required.issubset(intent) or
                intent["direction"] != "sell" or intent["order_date"] != date or
                not isinstance(intent["instrument"], str) or not intent["instrument"] or
                not isinstance(intent["requested_shares"], (int, float)) or
                not np.isfinite(intent["requested_shares"]) or intent["requested_shares"] <= 0):
            raise QualityError("Risk order intent has an invalid schema")
        matches = day_trades.loc[
            day_trades.instrument.eq(intent["instrument"]) &
            pd.to_numeric(day_trades.direction, errors="coerce").eq(0)
        ] if {"instrument", "direction"}.issubset(day_trades.columns) else day_trades.iloc[0:0]
        if len(matches) > 1:
            raise QualityError("Risk order intent has duplicate matching sell executions")
        if matches.empty:
            results.append({**intent, "execution_link_status": "unmatched",
                            "filled_shares": None, "execution_status": None,
                            "failure_reason": None})
            continue
        trade = matches.iloc[0]
        if not np.isclose(float(trade.requested_shares), float(intent["requested_shares"]),
                          atol=1e-8, rtol=1e-10):
            raise QualityError("Risk order intent requested shares disagree with execution ledger")
        results.append({**intent, "execution_link_status": "matched",
                        "filled_shares": float(trade.filled_shares),
                        "execution_status": str(trade.status),
                        "failure_reason": (str(trade.reason) if pd.notna(trade.reason) else None)})
    status = "completed" if all(row["execution_link_status"] == "matched" for row in results) else "partial"
    return status, results


def _risk_order_blockers(check):
    if check is None or "risk_order_blockers" not in check:
        return "unknown_legacy_trace", []
    blockers = check["risk_order_blockers"]
    if not isinstance(blockers, list):
        raise QualityError("Risk order blockers must be a list")
    for blocker in blockers:
        if (not isinstance(blocker, dict) or not isinstance(blocker.get("instrument"), str) or
                not blocker["instrument"] or not isinstance(blocker.get("reason"), str) or
                not blocker["reason"] or not isinstance(blocker.get("held_shares"), (int, float)) or
                not np.isfinite(blocker["held_shares"]) or blocker["held_shares"] <= 0):
            raise QualityError("Risk order blocker has an invalid schema")
    return "completed", blockers


def portfolio_exposure_diagnostics(path: Path, universe: pd.DataFrame, *, pit_verified: bool):
    """Measure realized holdings by their date-valid, available tracking group."""
    path = Path(path)
    if not pit_verified:
        return {"status": "bypassed", "reason": "historical_tracking_groups_not_verified",
                "by_date": []}
    if any(not (path / name).is_file() for name in ("positions.parquet", "ledger.parquet")):
        return {"status": "partial", "reason": "position_or_ledger_evidence_missing", "by_date": []}
    positions = pd.read_parquet(path / "positions.parquet")
    ledger = pd.read_parquet(path / "ledger.parquet")
    required_positions = {"datetime", "instrument", "shares", "raw_price"}
    required_ledger = {"independent_equity", "independent_cash", "independent_receivable",
                       "independent_income", "equity_residual"}
    if (not required_positions.issubset(positions.columns) or
            not required_ledger.issubset(ledger.columns) or
            not isinstance(universe.index, pd.MultiIndex) or
            not {"tracking_group"}.issubset(universe.columns)):
        raise QualityError("Portfolio exposure evidence has an invalid schema")
    positions = positions.copy()
    positions["datetime"] = pd.to_datetime(positions.datetime, errors="coerce").dt.normalize()
    values = positions[["shares", "raw_price"]].to_numpy(dtype=float)
    if (positions.datetime.isna().any() or positions.instrument.isna().any() or
            not np.isfinite(values).all() or (values[:, 0] < 0).any() or
            (values[:, 1] <= 0).any() or positions.duplicated(["datetime", "instrument"]).any()):
        raise QualityError("Portfolio positions contain invalid dates, values, or duplicate keys")
    equity = ledger.independent_equity.astype(float)
    ledger.index = pd.DatetimeIndex(ledger.index).normalize()
    if (ledger.index.has_duplicates or not ledger.index.is_monotonic_increasing or
            not np.isfinite(ledger[list(required_ledger)].to_numpy(dtype=float)).all() or (equity <= 0).any()):
        raise QualityError("Portfolio exposure ledger needs unique dates and finite positive equity")
    ledger = ledger.copy()
    ledger.index = pd.DatetimeIndex(ledger.index).normalize()
    if not positions.datetime.isin(ledger.index).all():
        raise QualityError("Portfolio positions fall outside the independent ledger calendar")
    if set(ledger.index) != set(positions.datetime.unique()):
        return {"status": "partial", "reason": "position_dates_incomplete", "by_date": []}
    holdings = positions.loc[positions.instrument.ne("CASH")].copy()
    index = pd.MultiIndex.from_arrays([holdings.datetime, holdings.instrument], names=universe.index.names)
    groups = universe.tracking_group.reindex(index)
    if groups.isna().any():
        missing = holdings.loc[groups.isna().to_numpy(), ["datetime", "instrument"]]
        return {"status": "partial", "reason": "historical_tracking_group_missing",
                "missing_count": len(missing), "missing_examples": [
                    {"date": str(row.datetime.date()), "instrument": row.instrument}
                    for row in missing.head(10).itertuples(index=False)], "by_date": []}
    holdings["tracking_group"] = groups.to_numpy()
    holdings["market_value"] = holdings.shares * holdings.raw_price
    by_group = holdings.groupby(["datetime", "tracking_group"], sort=True).agg(
        market_value=("market_value", "sum"), etf_count=("instrument", "nunique"))
    by_instrument = holdings.groupby(["datetime", "instrument"], sort=True).market_value.sum()
    by_date = []
    for day, account in ledger.iterrows():
        daily = by_group.loc[day] if day in by_group.index.get_level_values(0) else pd.DataFrame()
        daily_positions = positions.loc[positions.datetime.eq(day)]
        cash = float(daily_positions.loc[daily_positions.instrument.eq("CASH"), "shares"].sum())
        assets = daily_positions.loc[daily_positions.instrument.ne("CASH")]
        asset_value = float((assets.shares * assets.raw_price).sum())
        receivable, income = float(account.independent_receivable), float(account.independent_income)
        implied_equity = cash + asset_value + receivable + income
        if not np.isclose(cash, float(account.independent_cash), atol=1e-6, rtol=1e-9):
            raise QualityError("Position cash disagrees with the independent ledger")
        if not np.isclose(implied_equity, float(account.independent_equity), atol=1e-5, rtol=1e-9):
            raise QualityError("Position exposure does not reconcile to independent equity")
        detail = []
        if not daily.empty:
            daily = daily.reset_index()
            detail = [{"tracking_group": str(row.tracking_group),
                       "weight": float(row.market_value / account.independent_equity),
                       "market_value": float(row.market_value), "etf_count": int(row.etf_count)}
                      for row in daily.itertuples(index=False)]
        instrument_values = by_instrument.loc[day] if day in by_instrument.index.get_level_values(0) else pd.Series(dtype=float)
        by_date.append({"date": str(day.date()), "status": "completed",
            "equity": float(account.independent_equity), "cash_weight": cash / account.independent_equity,
            "receivable_weight": receivable / account.independent_equity,
            "income_weight": income / account.independent_equity,
            "max_group_weight": max((row["weight"] for row in detail), default=0.),
            "max_single_etf_weight": float(instrument_values.max() / account.independent_equity)
                if len(instrument_values) else 0.,
            "groups_with_multiple_etfs": sum(row["etf_count"] > 1 for row in detail),
            "etf_count": int(len(instrument_values)), "groups": detail,
            "equity_reconciled": True})
    return {"status": "completed", "scope": "realized_portfolio_holdings_by_pit_tracking_group",
            "by_date": by_date,
            "note": "Weights use the independent equity ledger; classifications are selected by each holding date and recorded availability."}


def _daily_risk_rows(path: Path):
    required = ("risk_checks.json", "ledger.parquet", "trades.parquet")
    if any(not (path / name).is_file() for name in required):
        return None
    checks = json.loads((path / "risk_checks.json").read_text(encoding="utf-8"))
    ledger = pd.read_parquet(path / "ledger.parquet")
    trades = pd.read_parquet(path / "trades.parquet")
    if (not isinstance(checks, list) or not isinstance(ledger.index, pd.DatetimeIndex) or
            not {"independent_equity", "independent_cash", "independent_receivable",
                 "independent_income", "cash_residual", "equity_residual", "return_residual"}.issubset(ledger.columns) or
            not {"datetime", "status", "reason", "instrument", "direction",
                 "requested_shares", "filled_shares"}.issubset(trades.columns)):
        raise QualityError("Daily risk attribution evidence has an invalid schema")
    shares = trades[["requested_shares", "filled_shares"]].to_numpy(dtype=float)
    directions = pd.to_numeric(trades.direction, errors="coerce").to_numpy(dtype=float)
    if (not np.isfinite(shares).all() or (shares[:, 0] <= 0).any() or
            (shares[:, 1] < 0).any() or (shares[:, 1] - shares[:, 0] > 1e-8).any() or
            not np.isin(directions, (0, 1)).all() or trades.instrument.isna().any()):
        raise QualityError("Daily risk attribution trades have invalid side or share quantities")
    by_date = {}
    for item in checks:
        if not isinstance(item, dict) or not isinstance(item.get("date"), str) or item["date"] in by_date:
            raise QualityError("Risk-check evidence has invalid or duplicate dates")
        by_date[item["date"]] = item
    first = next((item for item in checks if isinstance(item.get("initial_equity"), (int, float))), None)
    if first is None or first["initial_equity"] <= 0:
        return None
    equity = ledger.independent_equity.astype(float)
    if (ledger.index.has_duplicates or not ledger.index.is_monotonic_increasing or
            not np.isfinite(ledger.to_numpy(dtype=float)).all() or (equity <= 0).any()):
        raise QualityError("Daily risk ledger requires unique sorted dates and finite positive equity")
    peaks = pd.Series(np.maximum.accumulate(np.r_[float(first["initial_equity"]), equity.to_numpy()])[1:],
                      index=equity.index)
    drawdown = 1 - equity / peaks
    prior_peaks = peaks.shift(1, fill_value=float(first["initial_equity"]))
    trade_rows = trades.copy()
    trade_rows["datetime"] = pd.to_datetime(trade_rows.datetime).dt.normalize()
    if trade_rows.datetime.isna().any() or not trade_rows.datetime.isin(ledger.index).all():
        raise QualityError("Daily risk attribution trades are outside the accounting calendar")
    result = []
    for day, account in ledger.iterrows():
        date = pd.Timestamp(day).date().isoformat()
        check = by_date.get(date)
        day_trades = trade_rows.loc[trade_rows.datetime == pd.Timestamp(day)]
        intent_status, risk_orders = _risk_order_attribution(check, day_trades, date)
        blocker_status, risk_blockers = _risk_order_blockers(check)
        equity_value = float(account.independent_equity)
        result.append({"date": date, "equity": equity_value, "high_water_mark": float(peaks.loc[day]),
                       "drawdown": float(drawdown.loc[day]),
                       "risk_limit_exceeded_at_close": (bool(drawdown.loc[day] >= check["drawdown_limit"])
                           if check and check.get("risk_mode") == "max_drawdown" and
                           isinstance(check.get("drawdown_limit"), (int, float)) else None),
                       "risk_check_status": ("checked" if check and check.get("checked") else
                                             check.get("reason", "missing_risk_check") if check else "missing_risk_check"),
                       "risk_mode": check.get("risk_mode") if check else None,
                       "decision_drawdown": check.get("drawdown") if check else None,
                       "decision_valuation_basis": check.get("decision_valuation_basis") if check else None,
                       "execution_reference_price_basis": check.get("execution_reference_price_basis") if check else None,
                       "decision_execution_timing_status": (
                           check.get("decision_execution_timing_status", "unknown_legacy_trace")
                           if check else "missing_risk_check"),
                       "prior_close_high_water_mark": float(prior_peaks.loc[day]),
                       "decision_high_water_mark": check.get("peak_equity_at_decision") if check else None,
                       "decision_equity": check.get("equity_at_decision") if check else None,
                       "prior_close_peak_above_decision_peak": (
                           bool(prior_peaks.loc[day] > check["peak_equity_at_decision"] + 1e-8)
                           if check and isinstance(check.get("peak_equity_at_decision"), (int, float)) else None),
                       "valuation_timing": "ledger_close_vs_decision_execution_open",
                       "risk_triggered": check.get("risk_triggered") if check else None,
                       "signal_date": check.get("signal_date") if check else None,
                       "prior_signal_verified": (bool(pd.Timestamp(check["signal_date"]).normalize() < day)
                            if check and check.get("checked") and check.get("signal_date") else None),
                       "planned_rebalance": (check.get("planned_rebalance", check.get("reason") not in {
                           "not_scheduled_rebalance", "no_prior_session"}) if check else None),
                       "order_sizing_basis": check.get("order_sizing_basis") if check else None,
                       "generated_order_count": check.get("generated_order_count") if check else None,
                       "risk_order_link_status": intent_status,
                       "risk_order_intents": risk_orders,
                       "risk_order_blocker_status": blocker_status,
                       "risk_order_blockers": risk_blockers,
                       "risk_order_attempted": bool(risk_orders),
                       "risk_order_filled_shares": (float(sum(
                           row["filled_shares"] or 0. for row in risk_orders
                           if row["execution_link_status"] == "matched"))
                           if intent_status != "unknown_legacy_trace" else None),
                       "filled_order_count": int(day_trades.status.eq("filled").sum()),
                       "partial_order_count": int(day_trades.status.eq("partial").sum()),
                       "unfilled_order_count": int(day_trades.status.eq("unfilled").sum()),
                       "unfilled_reasons": sorted({str(value) for value in day_trades.loc[
                           day_trades.status.ne("filled"), "reason"].dropna()}),
                       "cash_weight": float(account.independent_cash / equity_value) if equity_value else None,
                       "receivable_weight": float(account.independent_receivable / equity_value) if equity_value else None,
                       "income_weight": float(account.independent_income / equity_value) if equity_value else None,
                       "accounting_residuals": {name: float(account[name]) for name in
                           ("cash_residual", "equity_residual", "return_residual")}})
    return result


def paired_daily_risk_attribution(baseline_path: Path, candidate_path: Path):
    """Pair read-only daily risk, decision and execution evidence; never changes gates."""
    base, candidate = _daily_risk_rows(Path(baseline_path)), _daily_risk_rows(Path(candidate_path))
    if base is None or candidate is None:
        return {"status": "partial", "reason": "daily_risk_evidence_unavailable", "by_date": []}
    left, right = ({row["date"]: row for row in rows} for rows in (base, candidate))
    dates = sorted(left.keys() & right.keys())
    if not dates:
        return {"status": "partial", "reason": "no_paired_daily_risk_dates", "by_date": []}
    rows = []
    for date in dates:
        b, c = left[date], right[date]
        rows.append({"date": date, "baseline": b, "candidate": c,
                     "candidate_minus_baseline_drawdown": c["drawdown"] - b["drawdown"],
                     "candidate_minus_baseline_equity": c["equity"] - b["equity"],
                     "interpretation": "paired_artifact_fact_not_causal_attribution"})
    missing_checks = [row["date"] for row in rows
                      if row["baseline"]["risk_check_status"] == "missing_risk_check" or
                      row["candidate"]["risk_check_status"] == "missing_risk_check"]
    missing_order_links = [row["date"] for row in rows
                           if "unknown_legacy_trace" in (
                               row["baseline"]["risk_order_link_status"],
                               row["candidate"]["risk_order_link_status"],
                               row["baseline"]["risk_order_blocker_status"],
                               row["candidate"]["risk_order_blocker_status"])]
    partial_order_links = [row["date"] for row in rows
                           if "partial" in (row["baseline"]["risk_order_link_status"],
                                             row["candidate"]["risk_order_link_status"])]
    complete = (len(dates) == len(left) == len(right) and not missing_checks and
                not missing_order_links and not partial_order_links)
    return {"status": "completed" if complete else "partial", "by_date": rows,
            "baseline_events": risk_breach_events(base), "candidate_events": risk_breach_events(candidate),
            "missing_baseline_dates": sorted(right.keys() - left.keys()),
            "missing_candidate_dates": sorted(left.keys() - right.keys()),
            "dates_missing_risk_checks": missing_checks,
            "dates_missing_risk_order_linkage": missing_order_links,
            "dates_partial_risk_order_linkage": partial_order_links,
            "first_close_breach_date": next((row["date"] for row in rows
                if row["candidate"]["risk_limit_exceeded_at_close"] is True), None),
            "note": "Risk and execution observations are artifact-linked; timing does not establish causality."}


def risk_breach_events(rows):
    """Describe observable close breaches without inventing executable exits."""
    events = []
    active = None
    for index, row in enumerate(rows):
        breach = row["risk_limit_exceeded_at_close"]
        if breach is not True:
            active = None
            continue
        if active is None:
            later = rows[index + 1:]
            active = {"first_observable_close": row["date"], "last_breach_close": row["date"],
                      "worst_drawdown": row["drawdown"], "unchecked_breach_days": 0,
                      "next_recorded_session": later[0]["date"] if later else None,
                      "first_later_risk_check": next((r["date"] for r in later
                          if r["risk_check_status"] == "checked"), None),
                      "first_later_risk_check_timing_status": next((r.get("decision_execution_timing_status")
                          or "unknown_legacy_trace" for r in later
                          if r["risk_check_status"] == "checked"), None),
                      "first_later_trigger": next((r["date"] for r in later
                          if r["risk_triggered"] is True), None),
                      "first_later_risk_order_attempt": next((r["date"] for r in later
                          if r.get("risk_order_attempted") is True), None),
                      "first_later_matched_risk_fill": next((r["date"] for r in later
                          if any(x.get("execution_link_status") == "matched" and
                                 (x.get("filled_shares") or 0.) > 0
                                 for x in r.get("risk_order_intents", []))), None),
                      "earliest_legal_exit": None,
                      "execution_attribution": "linked_attempts_only_earliest_legal_exit_unproven",
                      "observed_unfilled_reasons": [], "risk_order_blockers": []}
            events.append(active)
        active["last_breach_close"] = row["date"]
        active["worst_drawdown"] = max(active["worst_drawdown"], row["drawdown"])
        active["unchecked_breach_days"] += row["risk_check_status"] != "checked"
        active["observed_unfilled_reasons"] = sorted(set(active["observed_unfilled_reasons"]) |
                                                    set(row["unfilled_reasons"]))
        active["risk_order_blockers"].extend(
            {"date": row["date"], **blocker} for blocker in row.get("risk_order_blockers", [])
        )
        active.setdefault("risk_order_execution", []).extend(
            {"date": row["date"], **intent} for intent in row.get("risk_order_intents", [])
        )
    return events


def _prediction_change(baseline, candidate):
    for frame in (baseline, candidate):
        require_panel(frame, numeric=True)
        if frame.shape[1] != 1 or not np.isfinite(frame.to_numpy(dtype=float)).all():
            raise QualityError("Prediction diagnostics require one finite score column")
    common = baseline.index.intersection(candidate.index)
    delta = (candidate.iloc[:, 0].reindex(common) - baseline.iloc[:, 0].reindex(common)).abs()
    matched = baseline.index.equals(candidate.index)
    return {"status": "completed" if matched and len(common) else "partial",
            "sample_index_matches": matched, "compared_rows": len(common),
            "changed_rows": int(delta.gt(1e-12).sum()), "absolute_tolerance": 1e-12,
            "mean_absolute_delta": float(delta.mean()) if len(delta) else None,
            "max_absolute_delta": float(delta.max()) if len(delta) else None}


def _model_feature_entry(reference, candidate):
    columns = []
    for row in (reference, candidate):
        if not row.get("model_path") or not row.get("model_manifest_hash"):
            return {"status": "unknown", "reason": "model_manifest_identity_missing"}
        path = Path(row["model_path"]) / "manifest.json"
        if not path.is_file():
            return {"status": "unknown", "reason": "model_manifest_unavailable"}
        if file_hash(path) != row["model_manifest_hash"]:
            raise QualityError("Model feature diagnostic manifest hash mismatch")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        names = manifest.get("feature_names")
        if (not isinstance(names, list) or not names or not all(isinstance(x, str) for x in names)
                or len(set(names)) != len(names) or manifest.get("model_id") != row.get("model_id")):
            raise QualityError("Model feature diagnostic manifest identity invalid")
        columns.append(names)
    added = sorted(set(columns[1]) - set(columns[0]))
    return {"status": "completed", "baseline_feature_count": len(columns[0]),
            "candidate_feature_count": len(columns[1]), "added_features": added,
            "removed_features": sorted(set(columns[0]) - set(columns[1])),
            "interpretation": "manifest_declares_model_inputs_not_feature_importance"}


def signal_to_execution_diagnostics(reports, *, universe=None, pit_verified=False):
    """Read existing paired artifacts to locate—not explain—decision bottlenecks.

    No selection metric is altered.  Missing evidence is reported as unknown so
    old reports remain readable and are never silently re-run.
    """
    baseline = {(row["fold"], row["seed"]): row for row in reports.get("baseline", {}).get("by_fold", [])}
    rows = []
    for candidate in reports.get("candidate", {}).get("by_fold", []):
        key = (candidate.get("fold"), candidate.get("seed"))
        reference = baseline.get(key)
        if reference is None:
            raise QualityError("Signal diagnostic has no paired baseline")
        paths = [row.get("backtest_path") for row in (reference, candidate)]
        if not all(isinstance(value, str) for value in paths):
            rows.append({"fold": key[0], "seed": key[1], "status": "unavailable",
                         "reason": "backtest_artifacts_not_recorded"})
            continue
        base_path, candidate_path = map(Path, paths)
        daily_risk = paired_daily_risk_attribution(base_path, candidate_path)
        exposure = {kind: portfolio_exposure_diagnostics(path, universe, pit_verified=pit_verified)
                    if universe is not None else {"status": "bypassed",
                        "reason": "point_in_time_universe_not_supplied", "by_date": []}
                    for kind, path in (("baseline", base_path), ("candidate", candidate_path))}
        exposure_status = ("completed" if all(value["status"] == "completed" for value in exposure.values())
                           else "bypassed" if all(value["status"] == "bypassed" for value in exposure.values())
                           else "partial")
        required = ("predictions.parquet", "decisions.json", "execution.parquet", "trades.parquet")
        if any(not (path / name).is_file() for path in (base_path, candidate_path) for name in required):
            rows.append({"fold": key[0], "seed": key[1], "status": "unavailable",
                         "reason": "backtest_artifacts_missing"})
            continue
        base_scores, candidate_scores = (pd.read_parquet(path / "predictions.parquet")
                                         for path in (base_path, candidate_path))
        model_feature_entry = _model_feature_entry(reference, candidate)
        base_decisions, candidate_decisions = _decision_map(base_path), _decision_map(candidate_path)
        risk_decision_counts = {"baseline": sum(d.get("decision_kind") == "risk_reduction" for d in base_decisions.values()),
                                "candidate": sum(d.get("decision_kind") == "risk_reduction" for d in candidate_decisions.values())}
        # Risk-only exits have no model selection target; do not classify them as
        # unchanged ranks or missing predictions in the alpha decision funnel.
        base_decisions = {day: d for day, d in base_decisions.items() if d.get("decision_kind") != "risk_reduction"}
        candidate_decisions = {day: d for day, d in candidate_decisions.items() if d.get("decision_kind") != "risk_reduction"}
        base_execution, base_trades = _execution_rows(base_path)
        candidate_execution, candidate_trades = _execution_rows(candidate_path)
        prediction_change = _prediction_change(
            base_scores.loc[base_scores.index.get_level_values("datetime").isin(base_execution.index)],
            candidate_scores.loc[candidate_scores.index.get_level_values("datetime").isin(candidate_execution.index)])
        prediction_change["scope"] = "prediction_rows_on_backtest_sessions_not_label_filtered"
        calendar_matches = base_execution.index.equals(candidate_execution.index)
        sessions = pd.DatetimeIndex(candidate_execution.index).normalize().unique().sort_values()
        base_dates, candidate_dates = set(base_decisions), set(candidate_decisions)
        dates = sorted(base_dates.intersection(candidate_dates))
        counters = {"matched_decisions": len(dates), "rank_changed": 0, "selection_changed": 0,
                    "target_weight_changed": 0, "candidate_execution_constrained": 0,
                    "baseline_execution_constrained": 0, "missing_decisions": len(base_dates.symmetric_difference(candidate_dates)),
                    "rank_comparisons": 0, "missing_rank_predictions": 0, "missing_signal_ages": 0}
        costs = []
        ages, cost_by_age = {"baseline": [], "candidate": []}, {}
        for day in dates:
            base_decision, candidate_decision = base_decisions[day], candidate_decisions[day]
            base_age = (_trading_session_age(sessions, base_decision.get("signal_date"), day)
                        if calendar_matches else None)
            candidate_age = (_trading_session_age(sessions, candidate_decision.get("signal_date"), day)
                             if calendar_matches else None)
            if base_age is None or candidate_age is None:
                counters["missing_signal_ages"] += 1
            else:
                ages["baseline"].append(base_age)
                ages["candidate"].append(candidate_age)
            base_weights = base_decision.get("weights", {})
            candidate_weights = candidate_decision.get("weights", {})
            if not isinstance(base_weights, dict) or not isinstance(candidate_weights, dict):
                raise QualityError("Backtest decision weights must be objects")
            n = max(len(base_weights), len(candidate_weights))
            base_top = _top_scores(base_scores, base_decision.get("signal_date"), n)
            candidate_top = _top_scores(candidate_scores, candidate_decision.get("signal_date"), n)
            if base_top is None or candidate_top is None:
                counters["missing_rank_predictions"] += 1
            else:
                counters["rank_comparisons"] += 1
                if base_top != candidate_top:
                    counters["rank_changed"] += 1
            if set(base_weights) != set(candidate_weights):
                counters["selection_changed"] += 1
            if base_weights != candidate_weights or base_decision.get("cash_weight") != candidate_decision.get("cash_weight"):
                counters["target_weight_changed"] += 1
            timestamp = pd.Timestamp(day)
            for trades, label in ((base_trades, "baseline"), (candidate_trades, "candidate")):
                statuses = trades.loc[pd.to_datetime(trades.datetime) == timestamp, "status"]
                if len(statuses) and not statuses.eq("filled").all():
                    counters[label + "_execution_constrained"] += 1
            def cost(frame):
                return float(frame.loc[timestamp, "total_execution_cost"]) if timestamp in frame.index else None
            base_cost, candidate_cost = cost(base_execution), cost(candidate_execution)
            if base_cost is not None and candidate_cost is not None:
                costs.append(candidate_cost - base_cost)
                if candidate_age is not None and base_age is not None:
                    bucket = cost_by_age.setdefault(candidate_age,
                        {"decision_count": 0, "baseline_cost": 0., "candidate_cost": 0.})
                    bucket["decision_count"] += 1
                    bucket["baseline_cost"] += base_cost
                    bucket["candidate_cost"] += candidate_cost
        observations = []
        incomplete = (counters["missing_decisions"] or counters["missing_rank_predictions"] or
                      not counters["rank_comparisons"] or counters["missing_signal_ages"] or
                      not calendar_matches or prediction_change["status"] != "completed")
        if counters["rank_comparisons"] and counters["rank_changed"] == 0:
            observations.append("rank_unchanged")
        elif counters["selection_changed"] == 0:
            observations.append("rank_changed_selection_unchanged")
        if counters["candidate_execution_constrained"]:
            observations.append("candidate_execution_constrained")
        total_cost_delta = (float(candidate_execution.total_execution_cost.sum() -
                                  base_execution.total_execution_cost.sum()) if calendar_matches else None)
        if total_cost_delta is not None and total_cost_delta > 0:
            observations.append("candidate_execution_cost_higher")
        for bucket in cost_by_age.values():
            bucket["execution_cost_delta"] = bucket["candidate_cost"] - bucket["baseline_cost"]
        age_summary = {kind: {"count": len(values), "minimum": min(values) if values else None,
                              "median": float(np.median(values)) if values else None,
                              "maximum": max(values) if values else None}
                       for kind, values in ages.items()}
        rows.append({"fold": key[0], "seed": key[1], "status": "partial" if incomplete else "completed", **counters,
                     "model_feature_entry": model_feature_entry,
                     "prediction_change": prediction_change,
                     "risk_only_decisions": risk_decision_counts,
                     "execution_cost_delta": total_cost_delta,
                     "matched_decision_execution_cost_delta": float(sum(costs)) if costs else None,
                     "trading_session_calendar_matches": calendar_matches,
                     "signal_age_trading_sessions": age_summary,
                     "execution_cost_by_signal_age": [{"signal_age_trading_sessions": age, **values}
                                                       for age, values in sorted(cost_by_age.items())],
                     "daily_risk_attribution": daily_risk,
                     "portfolio_exposure": {"status": exposure_status, **exposure},
                     "observations": observations,
                     "causal_interpretation": "not_established"})
    return {"status": "completed" if rows and all(row["status"] == "completed" for row in rows) else "partial",
            "by_fold": rows,
            "note": "Signal age counts sessions between the recorded signal and decision dates. Costs are grouped by candidate signal age; no cost or return causality is inferred."}


def development_diagnostics(panel, calendar, universe, baseline, factor, protocol, reports, *, pit_verified=False):
    require_panel(panel)
    require_panel(universe)
    require_panel(baseline.frame, numeric=True)
    require_panel(factor.frame, numeric=True)
    require_calendar(calendar)
    if (panel.empty or (panel.index.get_level_values('datetime') >= pd.Timestamp(protocol.validation.holdout_start)).any()
            or (calendar >= pd.Timestamp(protocol.validation.holdout_start)).any()):
        raise QualityError('Development diagnostics cannot include holdout dates')
    if not all(frame.index.equals(panel.index) for frame in (universe, baseline.frame, factor.frame)):
        raise QualityError('Development diagnostics require the fixed research index')
    if not pd.api.types.is_bool_dtype(universe.eligible) or universe.eligible.isna().any():
        raise QualityError('Development diagnostics require complete boolean eligibility')
    if factor.frame.shape[1] != 1 or np.isinf(factor.frame.to_numpy(dtype=float)).any():
        raise QualityError('Development diagnostics require one finite-or-missing factor')
    labels, events = generate_labels(panel, calendar, protocol.label)
    horizons = [1, 5, 10, 20]
    horizon_labels = {h: generate_labels(panel, calendar, protocol.label.model_copy(update={'horizon': h}))
                      for h in horizons}
    factor_rows, quality_rows, decay, stability = [], [], [], []
    selection_mask = pd.Series(False, index=panel.index)
    dates = panel.index.get_level_values('datetime')
    for fold in protocol.validation.folds:
        validate_fold(fold, protocol.validation.holdout_start)
        mask = learning_mask(events, labels, fold.selection,
                             next_start=protocol.validation.holdout_start) & universe.eligible
        index_hash = content_hash([[str(t), i] for t, i in panel.index[mask]])
        for kind in ('baseline', 'candidate'):
            matched = [row for row in reports[kind]['by_fold'] if row['fold'] == fold.name]
            if (not matched or any(row['dataset'].get('evaluation_index_hash') != index_hash for row in matched)):
                raise QualityError('Diagnostic sample differs from the evaluated model sample')
        values = factor.frame.iloc[:, 0]
        training_mask = learning_mask(events, labels, fold.train,
                                      next_start=fold.selection.start) & universe.eligible
        training_metrics = predictive_metrics(values[training_mask], labels[training_mask])
        orientation = select_factor_orientation(training_metrics)
        orientation['training_window'] = fold.train.model_dump(mode='json')
        metrics = predictive_metrics(values[mask], labels[mask])
        oriented = (predictive_metrics(values[mask] * orientation['sign'], labels[mask])
                    if orientation['sign'] is not None else
                    {'status': 'unknown', 'reason': 'training_ic_ir_direction_unavailable'})
        factor_rows.append({'fold': fold.name, **metrics, 'training_orientation': orientation,
                            'oriented_validation': oriented,
                            'oriented_validation_gate': positive_icir_gate(oriented),
                            'evaluation_index_hash': index_hash})
        target = pd.Series((dates >= pd.Timestamp(fold.selection.start)) &
                           (dates <= pd.Timestamp(fold.selection.end)), index=panel.index) & universe.eligible
        minimum = factor.manifest.get('minimum_observations', 1)
        target &= panel.groupby(level='instrument').cumcount() + 1 >= minimum
        selection_mask |= target
        valid = values[target].notna()
        coverage = valid.groupby(level='datetime').mean()
        group_coverage = valid.groupby(universe.tracking_group[target].fillna('__unclassified__')).mean()
        quality_rows.append({'fold': fold.name, 'target_rows': int(target.sum()),
            'coverage': float(coverage.mean()) if len(coverage) else None,
            'worst_date_coverage': float(coverage.min()) if len(coverage) else None,
            'by_group': {str(k): float(v) for k, v in group_coverage.items()}})
        valid_dates = [row for row in metrics['by_date'] if row['valid']]
        midpoint = len(valid_dates) // 2
        for name, rows in [('first_half', valid_dates[:midpoint]), ('second_half', valid_dates[midpoint:])]:
            stability.append({'fold': fold.name, 'period': name, 'effective_dates': len(rows),
                'ic': float(np.mean([r['ic'] for r in rows])) if rows else None,
                'rank_ic': float(np.mean([r['rank_ic'] for r in rows])) if rows else None})
        # Compare fixed horizons on their common mature sample, rather than
        # making longer horizons look different through changed eligibility.
        masks = {h: learning_mask(e, y, fold.selection, next_start=protocol.validation.holdout_start)
                    & universe.eligible for h, (y, e) in horizon_labels.items()}
        common = pd.Series(True, index=panel.index)
        for hmask in masks.values():
            common &= hmask
        for horizon, (target_labels, _) in horizon_labels.items():
            decay.append({'fold': fold.name, 'horizon': horizon,
                'common_sample_rows': int(common.sum()),
                **predictive_metrics(values[common], target_labels[common])})
    paired = []
    model_rows = []
    pressure = []
    ablation = []
    removed = {(r['fold'], r['seed']): r for r in reports.get('ablation', {}).get('by_fold', [])}
    left = {(r['fold'], r['seed']): r for r in reports['baseline']['by_fold']}
    for row in reports['candidate']['by_fold']:
        key = (row['fold'], row['seed'])
        reference = left[key]
        model_rows.append({'fold': key[0], 'seed': key[1],
            'baseline': _summary(reference.get('predictive', {}), include_by_date=True),
            'candidate': _summary(row.get('predictive', {}), include_by_date=True)})
        paired.append({'fold': key[0], 'seed': key[1],
                       'baseline': _metrics(reference['portfolio']), 'candidate': _metrics(row['portfolio'])})
        if key in removed:
            ablation.append({'fold': key[0], 'seed': key[1],
                'excess_return_delta': row['portfolio']['excess_return'] - removed[key]['portfolio']['excess_return']})
        for multiplier in protocol.cost_multipliers:
            name = str(multiplier)
            b, c = reference['cost_stress'][name], row['cost_stress'][name]
            pressure.append({'fold': key[0], 'seed': key[1], 'multiplier': multiplier,
                             'baseline': _metrics(b), 'candidate': _metrics(c),
                             'excess_return_delta': c['excess_return'] - b['excess_return']})
    return {'status': 'completed', 'stage': 'factor_selection', 'protocol_id': protocol.protocol_id,
        'baseline_id': baseline.feature_set_id, 'candidate_id': reports['candidate']['feature_set_id'],
        'data_quality': {**{k: factor.manifest.get('quality', {}).get(k) for k in
                           ('coverage', 'worst_date_coverage', 'nonfinite', 'duplicate_keys')},
                         'time_check': factor.manifest.get('checks', {}).get('status', 'unavailable'),
                         'index_check': 'passed', 'by_fold': quality_rows},
        'predictive_metrics': {'factor_by_fold': factor_rows, 'model_by_fold': model_rows,
                              'factor_signal_gate': summarize_icir_gates(
                                  [row['oriented_validation_gate'] for row in factor_rows])},
        'portfolio_metrics': {'by_fold': paired},
        'signal_to_execution': signal_to_execution_diagnostics(
            reports, universe=universe, pit_verified=pit_verified),
        'robustness': {'cost_stress': pressure, 'ablation': ablation},
        'factor_diagnostics': {'redundancy': redundancy(factor.frame.iloc[:, 0][selection_mask],
                                                      baseline.frame[selection_mask]),
                               'stability': stability, 'decay': decay},
        'note': 'Training-only orientation; factor IC and ICIR must both be positive in a strict majority of folds (3 of 5); signal candidacy does not replace portfolio gates'}
