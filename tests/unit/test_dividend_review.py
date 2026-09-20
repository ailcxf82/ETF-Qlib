import copy

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal
from pydantic import ValidationError

from etf_ml.data.dividend_review import load_review, remove_reviewed_duplicates, validate_reviewed_events, DividendReview
from etf_ml.data.tushare_supplement import dividend_events
from etf_ml.errors import QualityError, IntegrityError, ConfigurationError
from etf_ml.utils import atomic_json, file_hash


def review_fixture(tmp_path):
    good = {"ann_date": "20230103", "ex_date": "20230106", "record_date": "20230105",
            "pay_date": "20230109", "div_cash": .1}
    bad = {**good, "record_date": "20230106"}
    raw = pd.DataFrame([{**bad, "ts_code": "510300.SH", "div_proc": "实施"},
                        {**good, "ts_code": "510300.SH", "div_proc": "实施"}])
    path = tmp_path / "raw.parquet"
    raw.to_parquet(path, index=False)
    evidence = tmp_path / "notice.pdf"
    evidence.write_bytes(b"%PDF-test-primary-notice")
    obj = {"schema_version": 1,
           "evidence": [{"evidence_id": "notice", "path": "notice.pdf", "url": "https://exchange.example/notice",
                         "sha256": file_hash(evidence)}],
           "duplicates": [{"review_id": "one", "instrument": "510300.SH", "source_sha256": file_hash(path),
                           "drop_match": bad, "drop_rows": 1, "retain_match": good, "retain_rows": 1,
                           "reason": "Explicit exchange record date in test notice", "evidence_ids": ["notice"]}]}
    review_path = tmp_path / "review.json"
    atomic_json(review_path, obj)
    return raw, review_path, obj


def test_review_keeps_exchange_distribution_and_original_response(tmp_path):
    raw, path, obj = review_fixture(tmp_path)
    before = raw.copy(deep=True)
    review, hashes = load_review(path)
    corrected, applied = remove_reviewed_duplicates(raw, "510300.SH", obj["duplicates"][0]["source_sha256"], review)
    assert_frame_equal(raw, before)
    assert_frame_equal(corrected, raw.iloc[[1]].reset_index(drop=True))
    assert applied[0]["dropped_rows"] == applied[0]["retained_rows"] == 1
    events = dividend_events(corrected, pd.bdate_range("2023-01-02", "2023-01-10"), ["510300.SH"])
    events.attrs.update(reviewed_duplicates=applied, primary_evidence_hashes=hashes)
    validate_reviewed_events(events, review, hashes)
    assert len(events) == 1 and events.iloc[0].cash_per_share == .1
    events.attrs["reviewed_duplicates"] = []
    with pytest.raises(QualityError, match="not bound"):
        validate_reviewed_events(events, review, hashes)


@pytest.mark.parametrize("change", ["source_hash", "removed_count", "retained_count", "identity", "status"])
def test_review_rejects_changed_source_or_counterparts(tmp_path, change):
    raw, path, obj = review_fixture(tmp_path)
    review, _ = load_review(path)
    digest = obj["duplicates"][0]["source_sha256"]
    if change == "source_hash":
        digest = "0" * 64
    elif change == "removed_count":
        raw = pd.concat([raw, raw.iloc[[0]]], ignore_index=True)
    elif change == "retained_count":
        raw = raw.iloc[[0]]
    elif change == "identity":
        raw.loc[0, "ts_code"] = "OTHER.SH"
    elif change == "status":
        raw.loc[0, "div_proc"] = "预案"
    with pytest.raises(QualityError):
        remove_reviewed_duplicates(raw, "510300.SH", digest, review)


@pytest.mark.parametrize("change", ["tampered", "missing", "escape"])
def test_primary_evidence_files_must_match_and_stay_within_review(tmp_path, change):
    _, path, obj = review_fixture(tmp_path)
    if change == "tampered":
        (tmp_path / "notice.pdf").write_bytes(b"tampered")
    elif change == "missing":
        (tmp_path / "notice.pdf").unlink()
    else:
        obj["evidence"][0]["path"] = "../outside.pdf"
        atomic_json(path, obj)
    with pytest.raises((QualityError, IntegrityError, ConfigurationError)):
        load_review(path)


@pytest.mark.parametrize("change", ["cash_changed", "cash_text", "cash_boolean", "weak_selector", "unknown_evidence", "same_selector"])
def test_review_cannot_silently_delete_unrelated_or_retained_distribution(tmp_path, change):
    raw, path, obj = review_fixture(tmp_path)
    rule = obj["duplicates"][0]
    if change == "cash_changed":
        rule["retain_match"]["div_cash"] = .2
    elif change in ("cash_text", "cash_boolean"):
        rule["drop_match"]["div_cash"] = "0.1" if change == "cash_text" else True
    elif change == "weak_selector":
        rule["drop_match"] = {"div_cash": .1}
    elif change == "unknown_evidence":
        rule["evidence_ids"] = ["other"]
    else:
        rule["drop_match"] = copy.deepcopy(rule["retain_match"])
    if change == "same_selector":
        review = DividendReview.model_validate(obj)
        with pytest.raises(QualityError, match="retained counterpart"):
            remove_reviewed_duplicates(raw, "510300.SH", rule["source_sha256"], review)
    else:
        with pytest.raises(ValidationError):
            DividendReview.model_validate(obj)


def test_events_cannot_keep_provenance_but_change_entitlement_dates(tmp_path):
    raw, path, obj = review_fixture(tmp_path)
    review, hashes = load_review(path)
    corrected, applied = remove_reviewed_duplicates(raw, "510300.SH", obj["duplicates"][0]["source_sha256"], review)
    events = dividend_events(corrected, pd.bdate_range("2023-01-02", "2023-01-10"), ["510300.SH"])
    events.attrs.update(reviewed_duplicates=applied, primary_evidence_hashes=hashes)
    events.loc[0, "record_date"] = pd.Timestamp("2023-01-04")
    with pytest.raises(QualityError, match="lost"):
        validate_reviewed_events(events, review, hashes)


def cash_review_fixture(tmp_path):
    raw, path, obj = review_fixture(tmp_path)
    rule = obj["duplicates"][0]
    rule.update(correction_kind="conflicting_cash", verified_cash_per_share=.1818)
    common = {"ann_date": "20230103", "ex_date": "20230106", "record_date": "20230105",
              "pay_date": "20230109", "base_date": "20230102"}
    rule["drop_match"] = {**common, "div_cash": .1}
    rule["retain_match"] = {**common, "div_cash": .1818}
    raw = pd.DataFrame([{**rule["drop_match"], "ts_code": "510300.SH", "div_proc": "实施"},
                        {**rule["retain_match"], "ts_code": "510300.SH", "div_proc": "实施"}])
    raw.to_parquet(tmp_path / "raw.parquet", index=False)
    rule["source_sha256"] = file_hash(tmp_path / "raw.parquet")
    atomic_json(path, obj)
    return raw, path, obj


def test_explicit_cash_review_preserves_verified_source_distribution(tmp_path):
    raw, path, obj = cash_review_fixture(tmp_path)
    before = raw.copy(deep=True)
    review, hashes = load_review(path)
    corrected, applied = remove_reviewed_duplicates(raw, "510300.SH", obj["duplicates"][0]["source_sha256"], review)
    assert_frame_equal(raw, before)
    assert_frame_equal(corrected, raw.iloc[[1]].reset_index(drop=True))
    events = dividend_events(corrected, pd.bdate_range("2023-01-02", "2023-01-10"), ["510300.SH"])
    events.attrs.update(reviewed_duplicates=applied, primary_evidence_hashes=hashes)
    validate_reviewed_events(events, review, hashes)
    assert events.iloc[0].cash_per_share == .1818
    events.loc[0, "cash_per_share"] = .1
    with pytest.raises(QualityError, match="lost"):
        validate_reviewed_events(events, review, hashes)


@pytest.mark.parametrize("change", ["missing_base", "different_base", "different_ex", "wrong_verified",
                                    "missing_verified", "text_verified", "bool_verified", "infinite_cash",
                                    "nan_cash", "same_cash", "default_kind"])
def test_cash_review_cannot_delete_an_unrelated_or_unverified_distribution(tmp_path, change):
    _, _, obj = cash_review_fixture(tmp_path)
    rule = obj["duplicates"][0]
    if change == "missing_base":
        rule["drop_match"].pop("base_date")
    elif change in ("different_base", "different_ex"):
        rule["drop_match"]["base_date" if change == "different_base" else "ex_date"] = "20230104"
    elif change in ("wrong_verified", "missing_verified", "text_verified", "bool_verified"):
        rule["verified_cash_per_share"] = {"wrong_verified": .1, "missing_verified": None,
                                          "text_verified": "0.1818", "bool_verified": True}[change]
    elif change in ("infinite_cash", "nan_cash"):
        rule["drop_match"]["div_cash"] = float("inf" if change == "infinite_cash" else "nan")
    elif change == "same_cash":
        rule["drop_match"]["div_cash"] = .1818
    else:
        rule.pop("correction_kind")
    with pytest.raises(ValidationError):
        DividendReview.model_validate(obj)
