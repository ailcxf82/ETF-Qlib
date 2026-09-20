"""Explicit, evidence-bound removal of a conflicting duplicate source dividend.

No automatic date preference or source rewrite. A retained counterpart is mandatory.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import pandas as pd
from pydantic import Field, model_validator

from etf_ml.contracts import StrictSpec
from etf_ml.errors import QualityError
from etf_ml.utils import ensure_within, file_hash


class PrimaryEvidence(StrictSpec):
    evidence_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    url: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def valid_source(self):
        if urlsplit(self.url).scheme != "https" or not urlsplit(self.url).netloc:
            raise ValueError("Primary evidence needs an HTTPS source URL")
        if Path(self.path).is_absolute():
            raise ValueError("Evidence path must be relative to the review")
        return self


class DuplicateReview(StrictSpec):
    review_id: str = Field(min_length=1)
    instrument: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    correction_kind: Literal["conflicting_dates", "conflicting_cash"] = "conflicting_dates"
    verified_cash_per_share: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    drop_match: dict[str, str | float | None]
    drop_rows: int = Field(strict=True, ge=1)
    retain_match: dict[str, str | float | None]
    retain_rows: int = Field(strict=True, ge=1)
    reason: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def numeric_cash(cls, value):
        if isinstance(value, dict):
            if isinstance(value.get("verified_cash_per_share"), (bool, str)):
                raise ValueError("Verified per-share cash must be numeric")
            for column in ("drop_match", "retain_match"):
                cash = value.get(column, {}).get("div_cash")
                if isinstance(cash, (bool, str)):
                    raise ValueError("Reviewed per-share cash must be numeric, not boolean/text")
        return value

    @model_validator(mode="after")
    def explicit_duplicate(self):
        required = {"ann_date", "ex_date", "record_date", "pay_date", "div_cash"}
        if not required.issubset(self.drop_match) or not required.issubset(self.retain_match):
            raise ValueError("Duplicate review requires explicit dividend economics and dates")
        for selectors in (self.drop_match, self.retain_match):
            cash = selectors["div_cash"]
            if not isinstance(cash, (int, float)) or not math.isfinite(cash) or cash <= 0:
                raise ValueError("Duplicate review needs finite positive per-share cash")
        if self.correction_kind == "conflicting_dates":
            if self.verified_cash_per_share is not None or any(
                self.drop_match[c] != self.retain_match[c] for c in ("ann_date", "div_cash")
            ):
                raise ValueError("A review may only remove a conflicting counterpart of one distribution")
        else:
            dates = ("ann_date", "ex_date", "record_date", "pay_date", "base_date")
            if (any(not self.drop_match.get(c) or self.drop_match.get(c) != self.retain_match.get(c)
                    for c in dates) or
                    self.drop_match["div_cash"] == self.retain_match["div_cash"] or
                    self.verified_cash_per_share != self.retain_match["div_cash"]):
                raise ValueError("Cash correction requires matching explicit dates and verified retained cash")
        if "ts_code" in self.drop_match or "ts_code" in self.retain_match:
            raise ValueError("Instrument must be bound by the review, not a row selector")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("Duplicate evidence references")
        return self


class DividendReview(StrictSpec):
    schema_version: int = Field(default=1, strict=True, ge=1, le=1)
    evidence: list[PrimaryEvidence] = Field(min_length=1)
    duplicates: list[DuplicateReview] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self):
        identifiers = [e.evidence_id for e in self.evidence]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Duplicate primary evidence IDs")
        ids = [d.review_id for d in self.duplicates]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate review IDs")
        if any(not set(d.evidence_ids).issubset(identifiers) for d in self.duplicates):
            raise ValueError("Review references unknown primary evidence")
        return self


def load_review(path: Path) -> tuple[DividendReview, dict[str, str]]:
    path = Path(path).resolve()
    initial_hash = file_hash(path)
    review = DividendReview.model_validate(json.loads(path.read_text(encoding="utf-8")))
    hashes = {str(path): initial_hash}
    for evidence in review.evidence:
        document = ensure_within(path.parent / evidence.path, path.parent)
        if not document.is_file() or file_hash(document) != evidence.sha256:
            raise QualityError("Primary dividend evidence missing or hash changed")
        hashes[str(document)] = evidence.sha256
    if file_hash(path) != initial_hash:
        raise QualityError("Dividend review changed while loading")
    return review, hashes


def _matching(raw, selectors):
    if not set(selectors).issubset(raw):
        raise QualityError("Reviewed duplicate selector fields missing from response")
    result = pd.Series(True, index=raw.index)
    for column, expected in selectors.items():
        result &= raw[column].isna() if expected is None else raw[column].eq(expected).fillna(False)
    return result


def remove_reviewed_duplicates(raw: pd.DataFrame, instrument: str, source_sha256: str,
                               review: DividendReview) -> tuple[pd.DataFrame, list[dict]]:
    rules = [d for d in review.duplicates if d.instrument == instrument]
    if not rules:
        return raw.copy(), []
    if not raw.ts_code.eq(instrument).all() or raw.index.has_duplicates:
        raise QualityError("Reviewed dividend response identity invalid")
    result = raw.copy()
    applied = []
    for rule in rules:
        if source_sha256 != rule.source_sha256:
            raise QualityError("Reviewed dividend source hash changed")
        removed = _matching(result, rule.drop_match)
        retained = _matching(result, rule.retain_match)
        if int(removed.sum()) != rule.drop_rows or int(retained.sum()) != rule.retain_rows:
            raise QualityError("Reviewed duplicate or retained counterpart count changed")
        if (removed & retained).any():
            raise QualityError("Review cannot remove its retained counterpart")
        if not result.loc[removed | retained, "div_proc"].eq("实施").all():
            raise QualityError("Review requires implemented counterpart rows")
        result = result.loc[~removed].copy()
        applied.append({"review_id": rule.review_id, "instrument": instrument,
                        "source_sha256": source_sha256, "dropped_rows": rule.drop_rows,
                        "retained_rows": rule.retain_rows, "evidence_ids": rule.evidence_ids,
                        "reason": rule.reason})
    return result.reset_index(drop=True), applied


def validate_reviewed_events(events: pd.DataFrame, review: DividendReview, hashes: dict[str, str]) -> None:
    if not isinstance(events, pd.DataFrame):
        raise QualityError("Declared dividend review requires mapped event inputs")
    expected = [{"review_id": d.review_id, "instrument": d.instrument,
                 "source_sha256": d.source_sha256, "dropped_rows": d.drop_rows,
                 "retained_rows": d.retain_rows, "evidence_ids": d.evidence_ids, "reason": d.reason}
                for d in review.duplicates]
    applied = events.attrs.get("reviewed_duplicates", [])
    if not isinstance(applied, list) or any(not isinstance(d, dict) for d in applied):
        raise QualityError("Invalid dividend review event provenance")
    if (sorted(applied, key=lambda d: d.get("review_id", "")) != sorted(expected, key=lambda d: d["review_id"]) or
            events.attrs.get("primary_evidence_hashes") != hashes):
        raise QualityError("Mapped events are not bound to the declared dividend review")
    for rule in review.duplicates:
        retained = rule.retain_match
        selected = events.instrument.eq(rule.instrument)
        for target, source in (("datetime", "ex_date"), ("record_date", "record_date"), ("pay_date", "pay_date")):
            selected &= events[target].eq(pd.to_datetime(retained[source], format="%Y%m%d"))
        selected &= events.cash_per_share.eq(retained["div_cash"])
        if int(selected.sum()) != 1:
            raise QualityError("Mapped events lost the reviewed retained distribution")
