"""Dated money-fund rules and source-bound daily income snapshot inputs."""
from __future__ import annotations
from dataclasses import dataclass, fields
from pathlib import Path
from urllib.parse import urlsplit
import math
import numpy as np
import pandas as pd
from etf_ml.data.money_income import require_daily_money_income, money_income_coverage
from etf_ml.errors import QualityError, IntegrityError, ConfigurationError
from etf_ml.utils import ensure_within, file_hash


@dataclass(frozen=True)
class IncomeRule:
    rule_id: str
    basis_shares: float=100.
    par_value: float=100.
    compound_unpaid: bool=True
    convert_at_par: bool=True
    rounding: str="exact"

    def __post_init__(self):
        if not isinstance(self.rule_id,str) or not self.rule_id.strip():
            raise QualityError("Income rule needs an explicit version identity")
        for value in (self.basis_shares,self.par_value):
            if isinstance(value,(bool,np.bool_)) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
                raise QualityError("Invalid monetary income rule units")
        if not float(self.basis_shares).is_integer():
            raise QualityError("Income rule basis must be whole shares")
        if (not isinstance(self.compound_unpaid,bool) or not isinstance(self.convert_at_par,bool)
                or self.rounding not in ("exact","truncate_cent")):
            raise QualityError("Unknown monetary income rule")


def validate_income_inputs(income,rules,instruments,calendar,start,end,*,allow_rounding_proxy=False):
    if income is None:
        if rules:
            raise QualityError("Income rules require an income input")
        return None,{}, {"enabled":False,"rounding_proxy":False}
    frame=income.copy(deep=True)
    require_daily_money_income(frame)
    symbols=set(frame.index.get_level_values("instrument"))
    if not isinstance(rules,dict) or symbols!=set(rules) or not symbols.issubset(set(instruments)):
        raise QualityError("Income symbols, rules and price panel differ")
    if not isinstance(allow_rounding_proxy,bool):
        raise QualityError("Income approximation authorization must be boolean")
    for symbol,rule in rules.items():
        if not isinstance(rule,IncomeRule):
            raise QualityError("Income rules require validated IncomeRule objects")
        group=frame.xs(symbol,level="instrument")
        if not group.basis_shares.eq(rule.basis_shares).all() or not group.par_value.eq(rule.par_value).all():
            raise QualityError("Income source units differ from the declared rule")
        if rule.rounding=="truncate_cent" and not allow_rounding_proxy:
            raise QualityError("Cent truncation without final redistribution evidence is a technical proxy")
    if "available_time" not in frame:
        raise QualityError("Income requires explicit publication availability")
    times=frame.available_time
    if (not pd.api.types.is_datetime64_ns_dtype(times.dtype) or isinstance(times.dtype,pd.DatetimeTZDtype)
            or times.isna().any()):
        raise QualityError("Income availability must be complete Shanghai-local timestamps")
    if times.lt(frame.period_end).any():
        raise QualityError("Income cannot be available before its earning day")
    start,end=pd.Timestamp(start).normalize(),pd.Timestamp(end).normalize()
    for item in money_income_coverage(frame,start.strftime("%Y-%m-%d"),end.strftime("%Y-%m-%d")):
        if not item["daily_amounts_complete"]:
            raise QualityError("Income input does not cover every evaluation calendar day")
    active=frame[frame.period_end.between(start,end)]
    # Closing income can be used in subsequent sizing only if it was actually
    # public before that next execution opening. Never use future disclosures.
    for row in active.itertuples():
        future=calendar[calendar>row.period_end]
        if len(future) and row.available_time>future[0]+pd.Timedelta(hours=9,minutes=30):
            raise QualityError("Income was unavailable before the next execution opening")
    return active,dict(rules),{"enabled":True,"rounding_proxy":any(r.rounding=="truncate_cent" for r in rules.values()),
                              "calendar_day_coverage":True,"daily_income_inferred":False}


def income_rules(frame):
    declarations = frame.attrs.get("income_rules")
    symbols = set(frame.index.get_level_values("instrument"))
    if not isinstance(declarations, dict) or set(declarations) != symbols:
        raise QualityError("Income input needs one explicit rule for each instrument")
    names = {f.name for f in fields(IncomeRule)}
    rules = {}
    for symbol, item in declarations.items():
        if not isinstance(item, dict) or set(item) != names | {"evidence_id", "available_time"}:
            raise QualityError("Income rule needs explicit units, settlement, rounding, evidence and availability")
        if not isinstance(item["evidence_id"], str) or not item["evidence_id"]:
            raise QualityError("Income rule evidence identity is empty")
        try:
            available = pd.Timestamp(item["available_time"])
        except (TypeError, ValueError) as exc:
            raise QualityError("Invalid income rule availability") from exc
        first = frame.xs(symbol, level="instrument").period_start.min()
        if pd.isna(available) or available.tzinfo is not None or available > first:
            raise QualityError("Income rule was unavailable before its earning period")
        rules[symbol] = IncomeRule(**{name: item[name] for name in names})
    return rules


def load_income_supplement(path, calendar, instruments, start, end):
    path = Path(path).resolve()
    initial = file_hash(path)
    frame = pd.read_parquet(path)
    require_daily_money_income(frame)
    rules = income_rules(frame)
    frame, rules, status = validate_income_inputs(frame, rules, instruments, calendar, start, end)
    evidence = frame.attrs.get("primary_evidence")
    if not isinstance(evidence, list) or not evidence:
        raise QualityError("Income source and rule evidence missing")
    hashes, ids = {str(path): initial}, set()
    for item in evidence:
        if not isinstance(item, dict) or set(item) != {"evidence_id", "path", "url", "sha256"}:
            raise QualityError("Invalid income source evidence")
        if any(not isinstance(item[k], str) or not item[k] for k in item):
            raise QualityError("Income evidence fields must be nonempty strings")
        identifier = item["evidence_id"]
        url = urlsplit(item["url"])
        if not isinstance(identifier, str) or not identifier or identifier in ids or url.scheme != "https" or not url.netloc:
            raise QualityError("Income evidence identity or URL invalid")
        if Path(item["path"]).is_absolute():
            raise QualityError("Income evidence must use a relative path")
        try:
            document = ensure_within(path.parent / item["path"], path.parent)
        except ConfigurationError as exc:
            raise QualityError("Income evidence path leaves its source directory") from exc
        if not document.is_file() or file_hash(document) != item["sha256"]:
            raise QualityError("Income evidence missing or hash changed")
        ids.add(identifier)
        hashes[str(document)] = item["sha256"]
    if "evidence_id" not in frame or not frame.evidence_id.isin(ids).all():
        raise QualityError("Income rows reference unknown source evidence")
    if any(item["evidence_id"] not in ids for item in frame.attrs["income_rules"].values()):
        raise QualityError("Income rules reference unknown source evidence")
    if file_hash(path) != initial:
        raise IntegrityError("Income input changed while loading")
    return frame, rules, status, hashes


def snapshot_income(snapshot, view=None):
    if snapshot.manifest["spec"].get("income_path") is None:
        return None, {}
    relative = (Path(view) / "income.parquet" if view else Path("income.parquet"))
    key = relative.as_posix()
    expected = snapshot.manifest["files"].get(key)
    path = ensure_within(snapshot.path / relative, snapshot.path)
    if expected is None or not path.is_file() or file_hash(path) != expected:
        raise IntegrityError("Snapshot lost or changed its declared income input")
    frame = pd.read_parquet(path)
    if frame.empty:
        return None, {}
    require_daily_money_income(frame)
    return frame, income_rules(frame)
