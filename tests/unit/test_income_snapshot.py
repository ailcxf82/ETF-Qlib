import copy
from decimal import Decimal
import pandas as pd
import pytest
from etf_ml.contracts import UniversePolicy, PortfolioPolicy
from etf_ml.data.calendar import read_calendar
from etf_ml.data.income_supplement import load_income_supplement, snapshot_income, IncomeRule
from etf_ml.data.snapshot import build_snapshot, load_snapshot, audit_source
from etf_ml.errors import QualityError, IntegrityError, DataNotReady
from etf_ml.operations.contracts import PaperAccount
from etf_ml.operations.daily import mark_account, target_allocation
from etf_ml.utils import file_hash


def load(source_spec, calendar):
    return load_income_supplement(source_spec.income_path, read_calendar(source_spec.trusted_calendar),
        ["510300.SH", "510500.SH", "159915.SZ"], calendar[0], calendar[-1])


def test_source_bound_rules_are_shared_with_account(source_spec, calendar, income_source):
    from etf_ml.backtest.income import IncomeRule as AccountRule
    frame, rules, status, hashes = load(source_spec, calendar)
    assert AccountRule is IncomeRule
    assert status["enabled"] and not status["rounding_proxy"]
    assert frame.equals(income_source) and rules["510300.SH"].rule_id == "synthetic-income-v1"
    assert hashes[str(source_spec.income_path)] == file_hash(source_spec.income_path)
    assert len(hashes) == 3


@pytest.mark.parametrize("change", ["rule_future", "rule_units", "proxy", "source_future", "unknown_row", "unknown_rule", "missing_availability"])
def test_unqualified_income_cannot_enter_snapshot(source_spec, calendar, income_source, change):
    frame = income_source.copy(deep=True)
    frame.attrs = copy.deepcopy(income_source.attrs)
    declaration = frame.attrs["income_rules"]["510300.SH"]
    if change == "rule_future": declaration["available_time"] = "2023-01-03"
    elif change == "rule_units": declaration["basis_shares"] = 1.
    elif change == "proxy": declaration["rounding"] = "truncate_cent"
    elif change == "source_future": frame.iloc[0, frame.columns.get_loc("available_time")] = calendar[1] + pd.Timedelta(hours=10)
    elif change == "unknown_row": frame["evidence_id"] = "missing"
    elif change == "unknown_rule": declaration["evidence_id"] = "missing"
    elif change == "missing_availability": frame = frame.drop(columns="available_time")
    frame.to_parquet(source_spec.income_path)
    with pytest.raises(QualityError): build_snapshot(source_spec.source, source_spec)
    assert not list(source_spec.artifact_root.glob("*/snapshot_manifest.json"))


@pytest.mark.parametrize("change", ["hash", "traversal", "duplicate", "http"])
def test_income_source_evidence_must_match(source_spec, calendar, income_source, change):
    frame = income_source.copy(deep=True)
    frame.attrs = copy.deepcopy(income_source.attrs)
    item = frame.attrs["primary_evidence"][0]
    if change == "hash": item["sha256"] = "0" * 64
    elif change == "traversal": item["path"] = "../outside.txt"
    elif change == "duplicate": frame.attrs["primary_evidence"].append(dict(item))
    elif change == "http": item["url"] = "http://issuer.example/income"
    frame.to_parquet(source_spec.income_path)
    with pytest.raises((QualityError, IntegrityError)): load(source_spec, calendar)


def test_snapshot_income_split_and_integrity(source_spec, calendar, income_source):
    snap = build_snapshot(source_spec.source, source_spec, UniversePolicy(minimum_listing_days=0, liquidity_lookback=1))
    full, rules = snapshot_income(snap)
    research, _ = snapshot_income(snap, "research")
    holdout, _ = snapshot_income(snap, "holdout")
    boundary = pd.Timestamp(source_spec.holdout_start)
    assert research.period_end.lt(boundary).all() and holdout.period_end.ge(boundary).all()
    assert len(research) + len(holdout) == len(full)
    assert "primary_evidence" not in research.attrs and "HOLDOUT_ONLY" not in str(research.attrs)
    assert snap.manifest["external_hashes"][str(source_spec.income_path)] == file_hash(source_spec.income_path)
    quality = pd.read_json(snap.path / "data_quality.json", typ="series")
    assert quality.income_validation["enabled"]
    path = snap.path / "research/income.parquet"
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(IntegrityError): snapshot_income(snap, "research")
    with pytest.raises(IntegrityError): load_snapshot(snap.path)


def test_empty_holdout_income_view_is_disabled(source_spec, calendar, income_source):
    source_spec.holdout_start = "2026-01-01"
    snap = build_snapshot(source_spec.source, source_spec)
    assert snapshot_income(snap, "holdout") == (None, {})


def test_income_audit_rejects_future_rule(source_spec, calendar, income_source):
    income_source.attrs["income_rules"]["510300.SH"]["available_time"] = "2026-01-01"
    income_source.to_parquet(source_spec.income_path)
    result = audit_source(source_spec)
    assert result["income_validation"]["status"] == "failed"
    assert any(e["code"] == "invalid_income_input" for e in result["errors"])


@pytest.mark.parametrize("balance", [20., -20.])
def test_current_paper_income_changes_equity_and_budget_not_cash(panel, calendar, balance):
    day = calendar[-1]
    account = PaperAccount(trading_day=day.date(), cash=100., shares={"510300.SH": 100.},
        income_balances={"510300.SH": balance}, income_rule_ids={"510300.SH": "v1"}, income_as_of=day.date(),
        equity_history=[{"date": calendar[-2].date(), "equity": 1000.}])
    rule = IncomeRule("v1", compound_unpaid=False, convert_at_par=False)
    paper, weights, history, risk = mark_account(account, panel, calendar, day, pd.DataFrame(), income_rules={"510300.SH": rule})
    expected = 100. + 100. * panel.loc[(day, "510300.SH"), "raw_close"] + balance
    assert risk["equity"] == pytest.approx(expected) and paper.cash == 100.
    assert paper.income_book.balances["510300.SH"] == Decimal(str(balance))
    assert sum(weights.values()) + risk["available_cash_weight"] + risk["income_weight"] == pytest.approx(1.)
    policy = PortfolioPolicy(minimum_commission=0., max_turnover=2.)
    constraints = {"eligible": {"510300.SH": True}, "buyable": {"510300.SH": True}, "sellable": {"510300.SH": True}, "tracking_group": {"510300.SH": "group"}}
    targets, reasons, _ = target_allocation(pd.Series({"510300.SH": 1.}), weights, constraints, policy, {**risk, "drawdown": 0.}, 252)
    assert sum(targets.values()) <= 1. - risk["income_weight"] + 1e-9


def test_missing_paper_income_is_not_silently_zero(panel, calendar):
    day = calendar[-1]
    account = PaperAccount(trading_day=day.date(), cash=100., shares={"510300.SH": 100.},
        equity_history=[{"date": calendar[-2].date(), "equity": 1000.}])
    with pytest.raises(DataNotReady, match="income_balance"):
        mark_account(account, panel, calendar, day, pd.DataFrame(), income_rules={"510300.SH": IncomeRule("v1")})


@pytest.mark.parametrize("change", ["rule", "cents", "sold", "unknown"])
def test_paper_income_must_be_current_booked_and_source_bound(panel, calendar, change):
    day = calendar[-1]
    account = PaperAccount(trading_day=day.date(), cash=100., shares={"510300.SH": 100.},
        income_balances={"510300.SH": 20.}, income_rule_ids={"510300.SH": "v1"}, income_as_of=day.date(),
        equity_history=[{"date": calendar[-2].date(), "equity": 1000.}])
    rules = {"510300.SH": IncomeRule("v1")}
    if change == "rule": account.income_rule_ids["510300.SH"] = "v2"
    elif change == "cents": account.income_balances["510300.SH"] = .001
    elif change == "sold": account.shares = {}
    elif change == "unknown": rules = {}
    with pytest.raises(QualityError): mark_account(account, panel, calendar, day, pd.DataFrame(), income_rules=rules)


@pytest.mark.parametrize("change", ["unchanged", "amount", "publication", "rule", "rule_time"])
def test_daily_frozen_income_prefix(source_spec, calendar, income_source, tmp_path, change):
    from types import SimpleNamespace
    from etf_ml.operations.daily import verify_development_prefix
    original = build_snapshot(source_spec.source, source_spec)
    reference = SimpleNamespace(frame=pd.read_parquet(original.path / "research/panel.parquet"))
    current = income_source.copy(deep=True)
    current.attrs = copy.deepcopy(income_source.attrs)
    key = (calendar[20], "510300.SH")
    if change == "amount":
        current.loc[key, "income_per_basis"] += .01
        current.loc[key, "income_per_share"] += .0001
    elif change == "publication": current.loc[key, "available_time"] += pd.Timedelta(hours=1)
    elif change == "rule": current.attrs["income_rules"]["510300.SH"]["rule_id"] = "synthetic-income-v2"
    elif change == "rule_time": current.attrs["income_rules"]["510300.SH"]["available_time"] = "2022-12-31 00:00:00"
    current.to_parquet(source_spec.income_path)
    updated = source_spec.model_copy(update={"artifact_root": tmp_path / "incremental"})
    newer = build_snapshot(updated.source, updated)
    package = {"snapshot_path": str(original.path)}
    if change == "unchanged": verify_development_prefix(newer, package, reference)
    else:
        with pytest.raises(IntegrityError, match="income"):
            verify_development_prefix(newer, package, reference)


@pytest.mark.parametrize("invalid", ["events", "income_index"])
def test_income_audit_combined_failure_stays_diagnostic(source_spec, income_source, tmp_path, invalid):
    if invalid == "events":
        source_spec.events_path = tmp_path / "invalid_events.parquet"
        pd.DataFrame({"missing_event_fields": [1]}).to_parquet(source_spec.events_path, index=False)
    else:
        income_source.reset_index().to_parquet(source_spec.income_path, index=False)
    result = audit_source(source_spec)
    assert result["status"] == "failed"
    expected = "invalid_corporate_actions" if invalid == "events" else "invalid_income_input"
    assert any(e["code"] == expected for e in result["errors"])
