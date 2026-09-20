"""Explicit, source-bound raw quote and mechanical factor revisions; no raw writes."""
from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from pathlib import Path

import numpy as np
import pandas as pd

from etf_ml.data.calendar import read_calendar
from etf_ml.data.source import require_panel
from etf_ml.data.tushare_supplement import adjustment_factors
from etf_ml.errors import QualityError
from etf_ml.utils import file_hash


def _apply_source_revisions(frame, path, spec):
    require_panel(frame)
    path = Path(path)
    review = json.loads(path.read_text(encoding="utf-8"))
    if review.get("version") != 1 or review.get("policy") != "dual_vendor_raw_quote_factor_boundary_revision":
        raise QualityError("Unsupported source revision policy")
    if Path(review["source_root"]).resolve() != spec.source.resolve():
        raise QualityError("Source revision provider identity differs")
    semantics = {name: getattr(spec, name) for name in
                 ("price_mode", "volume_unit", "amount_multiplier", "change_unit")}
    if (semantics != review["semantics"] or spec.price_mode != "raw" or spec.lot_size != 100
            or spec.volume_unit not in ("lots", "shares") or spec.change_unit not in ("percent", "decimal")
            or spec.amount_multiplier is None):
        raise QualityError("Source revision units differ from declared source")
    hashes = {str(path.resolve()): file_hash(path)}
    def bind(file, expected):
        file = Path(file)
        if not file.is_file() or file_hash(file) != expected:
            raise QualityError("Source revision evidence changed")
        hashes[str(file.resolve())] = expected
        return file
    for file, expected in review["evidence_hashes"].items():
        bind(file, expected)
    for relative, expected in review["raw_file_hashes"].items():
        file = (spec.source / relative).resolve()
        if not file.is_relative_to(spec.source.resolve()):
            raise QualityError("Source revision raw path escapes provider")
        bind(file, expected)
    revisions = pd.read_parquet(bind(review["quote_revisions_path"], review["quote_revisions_sha256"]))
    require_panel(revisions, numeric=True)
    fields = ("close", "change", "volume", "amount")
    columns = {prefix + field for prefix in ("before_", "after_") for field in fields}
    if set(revisions.columns) != columns or revisions.empty or not np.isfinite(revisions.to_numpy()).all():
        raise QualityError("Invalid source revision quote rows")
    rules = review["factor_rules"]
    expected_keys = {(pd.Timestamp(rule["previous_date"]), rule["instrument"]) for rule in rules}
    if len(expected_keys) != len(rules) or set(revisions.index) != expected_keys:
        raise QualityError("Unbound or duplicate source revision rows")
    required_raw = {"calendars/day.txt", "instruments/all.txt"}
    for rule in rules:
        for field in ("open", "high", "low", "close", "change", "volume", "amount", "reference_close"):
            required_raw.add("features/" + rule["instrument"].lower() + "/" + spec.fields[field] + ".day.bin")
    if not required_raw.issubset(review["raw_file_hashes"]):
        raise QualityError("Revision original raw field hashes incomplete")
    calendar = read_calendar(spec.trusted_calendar)
    result = frame.copy()
    applied = []
    for rule in rules:
        instrument = rule["instrument"]
        if not isinstance(instrument, str) or not instrument.endswith((".SH", ".SZ")) or not instrument[:6].isdigit():
            raise QualityError("Invalid revision instrument")
        previous, day = pd.Timestamp(rule["previous_date"]), pd.Timestamp(rule["boundary_date"])
        positions = calendar.get_indexer([previous, day])
        if positions[0] < 0 or positions[1] != positions[0] + 1:
            raise QualityError("Revision factor boundary is not an adjacent trading day")
        key, nextkey = (previous, instrument), (day, instrument)
        if key not in frame.index or nextkey not in frame.index:
            raise QualityError("Revision dates absent from original panel")
        source_rows = []
        code = instrument[:6]
        symbol = ("sh" if instrument.endswith(".SH") else "sz") + code
        for vendor in ("eastmoney", "tencent"):
            source = rule["quote_sources"][vendor]
            receipt = json.loads(bind(source["receipt_path"], source["receipt_sha256"]).read_text(encoding="utf-8"))
            response = bind(source["response_path"], receipt["response_sha256"])
            if receipt["instrument"] != instrument or receipt["status"] != "independent_raw_quote_response_captured":
                raise QualityError("Revision source instrument identity differs")
            if vendor == "eastmoney":
                data = json.loads(response.read_text(encoding="utf-8"))
                market = "1" if instrument.endswith(".SH") else "0"
                if (receipt["url"] != "https://push2his.eastmoney.com/api/qt/stock/kline/get"
                        or receipt["params"]["fqt"] != "0" or receipt["params"]["klt"] != "101"
                        or receipt["params"]["secid"] != market + "." + code
                        or data["rc"] != 0 or data["data"]["code"] != code):
                    raise QualityError("Revision requires identified unadjusted daily quotes")
                rows = [line.split(",") for line in data["data"]["klines"]]
            else:
                text = response.read_text(encoding="utf-8")
                data = json.loads(text[text.index("={") + 1:])
                if (receipt["url"] != "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
                        or receipt["raw_adjustment_key"] != "day" or data["code"] != 0
                        or not receipt["params"]["param"].startswith(symbol + ",day,")
                        or not receipt["params"]["param"].endswith(",")):
                    raise QualityError("Revision requires identified Tencent raw quotes")
                rows = data["data"][symbol]["day"]
            selected = [row for row in rows if row[0] in (str(previous.date()), str(day.date()))]
            if len(selected) != 2 or len({row[0] for row in selected}) != 2:
                raise QualityError("Independent source does not cover revision boundary")
            source_rows.append({row[0]: row for row in selected})
        em, tx = source_rows
        for date in (str(previous.date()), str(day.date())):
            prices = [Decimal(x) for x in em[date][1:5]]
            quantity, amount = Decimal(em[date][5]), Decimal(em[date][6])
            if (not all(value.is_finite() and value > 0 for value in prices)
                    or not prices[3] <= min(prices[:2]) <= max(prices[:2]) <= prices[2]
                    or not quantity.is_finite() or quantity < 0 or not amount.is_finite() or amount < 0):
                raise QualityError("Invalid independent revision prices, quantity or amount")
            if [Decimal(x) for x in em[date][1:6]] != [Decimal(x) for x in tx[date][1:6]]:
                raise QualityError("Independent revision OHLCV sources disagree")
            if abs(Decimal(em[date][6]) - Decimal(tx[date][8]) * 10000) > Decimal(50):
                raise QualityError("Independent revision amounts disagree at display precision")
            quote = frame.loc[(pd.Timestamp(date), instrument)]
            for field, position in (("open", 1), ("high", 3), ("low", 4)):
                if not np.isclose(quote[field], float(em[date][position]), rtol=np.finfo(np.float32).eps, atol=0):
                    raise QualityError("Revision original OHLC context differs")
        old = revisions.loc[key]
        if not all(frame.loc[key, field] == old["before_" + field] for field in fields):
            raise QualityError("Revision before values differ from original panel")
        old_reference = float(frame.loc[key, "reference_close"])
        new_close = Decimal(em[str(previous.date())][2])
        reference = new_close - Decimal(em[str(previous.date())][9])
        if not np.isclose(old_reference, float(reference), rtol=np.finfo(np.float32).eps, atol=0):
            raise QualityError("Revision previous reference price differs")
        if (not np.isclose(frame.loc[nextkey, "reference_close"], float(new_close), rtol=np.finfo(np.float32).eps, atol=0)
                or not np.isclose(frame.loc[nextkey, "close"], float(em[str(day.date())][2]), rtol=np.finfo(np.float32).eps, atol=0)
                or np.isclose(frame.loc[key, "close"], float(new_close), rtol=np.finfo(np.float32).eps, atol=0)):
            raise QualityError("Revision is not a confirmed previous-close/reference discrepancy")
        percent = ((new_close / reference - 1) * 100).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        volume = Decimal(em[str(previous.date())][5])
        replacement = {"close": float(new_close), "change": float(percent if spec.change_unit == "percent" else percent / 100),
                       "volume": float(volume if spec.volume_unit == "lots" else volume * 100),
                       "amount": float(Decimal(em[str(previous.date())][6]) / Decimal(str(spec.amount_multiplier)))}
        if not all(old["after_" + field] == replacement[field] for field in fields):
            raise QualityError("Revision after values are not independently source-derived")
        bank = bind(rule["factor_source_path"], rule["factor_source_sha256"])
        original = adjustment_factors(pd.read_parquet(bank))
        if set(original.index.get_level_values("instrument")) != {instrument}:
            raise QualityError("Revision factor source instrument differs")
        subset = frame[frame.index.get_level_values("instrument") == instrument]
        expected = original.reindex(subset.index)
        if not np.array_equal(subset.factor.to_numpy(), expected.to_numpy(), equal_nan=True):
            raise QualityError("Revision factors differ from original source bank")
        ratio = float(original.loc[nextkey] / original.loc[key])
        if not np.isfinite(ratio) or ratio <= 0 or ratio == 1 or ratio != rule["source_boundary_ratio"]:
            raise QualityError("Revision factor boundary differs")
        dividends = pd.read_parquet(bind(rule["dividend_source_path"], rule["dividend_source_sha256"]))
        if not dividends.empty and ("ts_code" not in dividends or not dividends.ts_code.eq(instrument).all()):
            raise QualityError("Revision dividend source instrument differs")
        if (dividends.div_proc.eq("实施") & dividends.ex_date.astype(str).eq(day.strftime("%Y%m%d"))).any():
            raise QualityError("Revision boundary has a declared cash action")
        for field, value in replacement.items():
            result.loc[key, field] = value
        mask = ((result.index.get_level_values("instrument") == instrument)
                & (result.index.get_level_values("datetime") >= day))
        result.loc[mask, "factor"] /= ratio
        applied.append({"instrument": instrument, "quote_date": str(previous.date()), "factor_boundary_date": str(day.date()),
                        "before": {field: float(old["before_" + field]) for field in fields}, "after": replacement,
                        "removed_source_factor_boundary_ratio": ratio})
    for file, expected in hashes.items():
        if file_hash(Path(file)) != expected:
            raise QualityError("Revision evidence changed during application")
    return result, {"status": "passed", "revised_quote_rows": len(applied), "factor_boundaries_rebased": len(applied),
                    "raw_source_modified": False, "economic_events_created": 0, "revisions": applied}, hashes


def apply_source_revisions(frame, path, spec):
    try:
        return _apply_source_revisions(frame, path, spec)
    except QualityError:
        raise
    except (KeyError, ValueError, TypeError, IndexError, AttributeError, OSError, InvalidOperation) as error:
        raise QualityError("Malformed source revision input or evidence") from error
