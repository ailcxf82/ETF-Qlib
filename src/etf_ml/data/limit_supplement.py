"""Daily raw-price limits with exact source binding and explicit timing policy."""
from __future__ import annotations

from datetime import time
import json
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from etf_ml.data.calendar import require_calendar
from etf_ml.data.dividend_review import PrimaryEvidence
from etf_ml.data.normalize import trading_permissions, trading_block_codes
from etf_ml.data.source import require_panel
from etf_ml.errors import QualityError
from etf_ml.utils import ensure_within, file_hash, filesystem_path


LIMIT_COLUMNS = ["up_limit", "down_limit", "available_time"]


def map_source_limits(raw: pd.DataFrame, policy: dict) -> pd.DataFrame:
    required = {"ts_code", "trade_date", "up_limit", "down_limit"}
    if not required.issubset(raw) or raw.empty:
        raise QualityError("Daily limits need explicit source dates, identity and prices")
    if (not isinstance(policy, dict) or policy.get("basis") not in
            ("source_documented_schedule", "recorded_receipt")):
        raise QualityError("Daily limits need an explicit availability basis")
    dates = pd.to_datetime(raw.trade_date, format="%Y%m%d", errors="raise")
    mapped = pd.DataFrame({"datetime": dates, "instrument": raw.ts_code,
                           "up_limit": raw.up_limit, "down_limit": raw.down_limit})
    if policy["basis"] == "source_documented_schedule":
        if set(policy) != {"basis", "local_time", "timezone", "explanation"}:
            raise QualityError("Scheduled availability needs a frozen, explained local clock")
        if policy["timezone"] != "Asia/Shanghai" or not policy["explanation"]:
            raise QualityError("Scheduled availability needs Shanghai timezone and explanation")
        try:
            clock = time.fromisoformat(policy["local_time"])
        except (ValueError, TypeError) as exc:
            raise QualityError("Invalid daily limit availability clock") from exc
        if clock.tzinfo is not None:
            raise QualityError("Availability clock must be local")
        delay = pd.Timedelta(hours=clock.hour, minutes=clock.minute, seconds=clock.second,
                             microseconds=clock.microsecond)
        mapped["available_time"] = (dates + delay).dt.tz_localize("Asia/Shanghai")
    else:
        if set(policy) != {"basis"} or "available_time" not in raw:
            raise QualityError("Recorded receipts must be present in the bound source rows")
        timestamps = pd.to_datetime(raw.available_time, errors="raise")
        if timestamps.dt.tz is None or timestamps.isna().any():
            raise QualityError("Daily limit receipt times need complete explicit timezone")
        mapped["available_time"] = timestamps.dt.tz_convert("Asia/Shanghai")
    mapped = mapped.set_index(["datetime", "instrument"]).sort_index()
    require_panel(mapped)
    if mapped.index.get_level_values("instrument").isna().any():
        raise QualityError("Daily limit instrument missing")
    for column in ("up_limit", "down_limit"):
        values = mapped[column]
        if (not pd.api.types.is_numeric_dtype(values.dtype) or pd.api.types.is_bool_dtype(values.dtype)
                or (~np.isfinite(values) | values.le(0)).any()):
            raise QualityError("Daily price limits must be finite positive numeric values")
    if mapped.down_limit.ge(mapped.up_limit).any():
        raise QualityError("Daily lower price limit must precede upper limit")
    return mapped


def load_limit_supplement(path, calendar, instruments):
    path = Path(path).resolve()
    before = file_hash(path)
    canonical = pd.read_parquet(filesystem_path(path))
    require_panel(canonical)
    require_calendar(calendar)
    if list(canonical.columns) != LIMIT_COLUMNS:
        raise QualityError("Daily limit supplement schema changed")
    responses = canonical.attrs.get("source_responses")
    if not isinstance(responses, list) or not responses:
        raise QualityError("Daily limit source response evidence missing")
    hashes = {str(path): before}
    policy_file = canonical.attrs.get("availability_policy_file")
    if policy_file is not None:
        if not isinstance(policy_file, str) or Path(policy_file).is_absolute():
            raise QualityError("Frozen limit policy path must be relative")
        policy_path = ensure_within(path.parent / policy_file, path.parent)
        policy_sha = canonical.attrs.get("availability_policy_sha256")
        if not filesystem_path(policy_path).is_file() or file_hash(policy_path) != policy_sha:
            raise QualityError("Frozen limit policy file missing or changed")
        if json.loads(filesystem_path(policy_path).read_text(encoding="utf-8")) != canonical.attrs.get("availability_policy"):
            raise QualityError("Frozen policy file differs from the mapped availability policy")
        hashes[str(policy_path)] = policy_sha
    metadata = canonical.attrs.get("source_manifests")
    manifest_proofs = {}
    if metadata is not None:
        if not isinstance(metadata, list) or len(metadata) != len(responses):
            raise QualityError("Daily limit request manifests incomplete")
        for entry in metadata:
            proof = PrimaryEvidence.model_validate(entry)
            if proof.evidence_id in manifest_proofs:
                raise QualityError("Duplicate daily limit request manifest")
            document = ensure_within(path.parent / proof.path, path.parent)
            if not filesystem_path(document).is_file() or file_hash(document) != proof.sha256:
                raise QualityError("Daily limit request manifest missing or changed")
            manifest_proofs[proof.evidence_id] = json.loads(filesystem_path(document).read_text(encoding="utf-8"))
            hashes[str(document)] = proof.sha256
    pieces, identifiers = [], set()
    for entry in responses:
        proof = PrimaryEvidence.model_validate(entry)
        if proof.evidence_id in identifiers:
            raise QualityError("Duplicate daily limit source evidence")
        identifiers.add(proof.evidence_id)
        source = ensure_within(path.parent / proof.path, path.parent)
        if not filesystem_path(source).is_file() or file_hash(source) != proof.sha256:
            raise QualityError("Daily limit source response missing or changed")
        hashes[str(source)] = proof.sha256
        response = pd.read_parquet(filesystem_path(source))
        if metadata is not None:
            declaration = manifest_proofs.get(proof.evidence_id, {})
            if not isinstance(declaration, dict) or not isinstance(declaration.get("request"), dict):
                raise QualityError("Invalid daily limit request manifest")
            request = declaration["request"]
            params = request.get("params", {})
            if not isinstance(params, dict) or (len(response) and not {"ts_code", "trade_date"}.issubset(response)):
                raise QualityError("Invalid daily limit request/response schema")
            if (request.get("api") != "etf_limit" or declaration.get("raw_hash") != proof.sha256
                    or declaration.get("rows") != len(response)
                    or not {"ts_code", "start_date", "end_date"}.issubset(params)
                    or (len(response) and (not response.ts_code.eq(params["ts_code"]).all()
                                          or not response.trade_date.between(params["start_date"], params["end_date"]).all()))):
                raise QualityError("Daily limit response differs from declared request identity/range")
        pieces.append(response)
    expected = map_source_limits(pd.concat(pieces, ignore_index=True), canonical.attrs.get("availability_policy"))
    try:
        assert_frame_equal(canonical, expected, check_freq=False)
    except AssertionError as exc:
        raise QualityError("Daily limit values/timing differ from the bound source mapping") from exc
    if (not canonical.index.get_level_values("datetime").isin(calendar).all() or
            not canonical.index.get_level_values("instrument").isin(instruments).all()):
        raise QualityError("Daily limit source outside declared calendar or instrument scope")
    if any(file_hash(Path(p)) != h for p, h in hashes.items()):
        raise QualityError("Daily limit supplement changed while loading")
    return canonical, hashes


def apply_limit_supplement(panel, path, calendar):
    require_panel(panel)
    limits, hashes = load_limit_supplement(path, calendar, panel.index.get_level_values("instrument").unique())
    missing = panel.index[panel.quoted & ~panel.index.isin(limits.index)]
    if len(missing):
        raise QualityError("Declared daily limits do not cover every quoted instrument/date")
    result = panel.copy()
    aligned = limits.reindex(panel.index)
    opening = pd.DatetimeIndex(panel.index.get_level_values("datetime")).tz_localize("Asia/Shanghai") + pd.Timedelta(hours=9, minutes=30)
    known = (aligned.available_time.notna() & aligned.available_time.le(opening)).to_numpy()
    permissions = trading_permissions(panel)
    prior_codes = trading_block_codes(panel)
    for column in ("up_limit", "down_limit"):
        result[column] = aligned[column]
    result["limits_known_at_open"] = known
    for side, edge, code in (("buy", "up_limit", 13), ("sell", "down_limit", 14)):
        boundary = ((result.raw_open.ge(result[edge]) if side == "buy" else result.raw_open.le(result[edge])) |
                    np.isclose(result.raw_open, result[edge], rtol=1e-7, atol=1e-7)).fillna(False)
        permitted = permissions[side + "able"]
        result[side + "able"] = permitted & known & ~boundary
        reason_codes = prior_codes[side + "_block_code"].copy()
        reason_codes.loc[permitted & ~known] = 15
        reason_codes.loc[permitted & known & boundary] = code
        result[side + "_block_code"] = reason_codes
    unavailable = int((panel.quoted & ~known).sum())
    report = {"status": "incomplete" if unavailable else "passed", "quoted_rows": int(panel.quoted.sum()),
              "quoted_limits_unavailable_at_open": int((panel.quoted & ~known).sum()),
              "source_rows": len(limits), "availability_policy": limits.attrs["availability_policy"],
              "actual_historical_receipts_proven": limits.attrs["availability_policy"]["basis"] == "recorded_receipt",
              "source_completeness_verified": False}
    return result, report, hashes
