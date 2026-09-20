import pandas as pd
import pytest
from etf_ml.contracts import UniversePolicy
from etf_ml.data.universe import build_history, metadata_quote_coverage
from etf_ml.data.snapshot import build_snapshot, audit_source
from etf_ml.errors import QualityError


def test_partial_metadata_cannot_publish_full_snapshot(source_spec, panel, metadata, calendar):
    partial = metadata[metadata.instrument.ne("510300.SH")]
    partial.to_parquet(source_spec.metadata_path, index=False)
    history = build_history(panel, partial, calendar, UniversePolicy())
    report = metadata_quote_coverage(panel, history)
    assert report["unknown_quoted_rows"] == len(calendar) and report["unknown_quoted_instruments"] == 1
    assert report["quoted_rows"] == 3 * len(calendar) and report["status"] == "failed"
    audited = audit_source(source_spec)
    assert any(e["code"] == "incomplete_quoted_historical_metadata" for e in audited["errors"])
    assert audited["metadata_quote_coverage"]["unknown_quoted_rows"] == len(calendar)
    with pytest.raises(QualityError, match="full quoted scope"):
        build_snapshot(source_spec.source, source_spec)
    assert not list(source_spec.artifact_root.glob("*/snapshot_manifest.json"))


@pytest.mark.parametrize("gap", ["availability", "validity"])
def test_one_unknown_quote_cannot_publish(source_spec, metadata, calendar, gap):
    if gap == "availability": metadata.loc[metadata.instrument.eq("510300.SH"), "available_time"] = calendar[1]
    else: metadata.loc[metadata.instrument.eq("510300.SH"), "valid_from"] = calendar[1]
    metadata.to_parquet(source_spec.metadata_path, index=False)
    with pytest.raises(QualityError, match="full quoted scope"):
        build_snapshot(source_spec.source, source_spec)
    assert not list(source_spec.artifact_root.glob("*/snapshot_manifest.json"))


def test_known_outside_scope_is_not_unknown(panel, metadata, calendar):
    metadata.loc[metadata.instrument.eq("510300.SH"), "asset_class"] = "foreign_equity"
    history = build_history(panel, metadata, calendar, UniversePolicy())
    report = metadata_quote_coverage(panel, history)
    assert report["status"] == "passed" and report["unknown_quoted_rows"] == 0
    assert history.xs("510300.SH", level="instrument").reason.eq("outside_asset_scope").all()


def test_unquoted_prelisting_does_not_infer_operation(panel, metadata, calendar):
    key = (calendar[0], "510300.SH")
    panel.loc[key, "quoted"] = False
    metadata.loc[metadata.instrument.eq("510300.SH"), "valid_from"] = calendar[1]
    history = build_history(panel, metadata, calendar, UniversePolicy())
    report = metadata_quote_coverage(panel, history)
    assert report["status"] == "passed" and report["unquoted_unknown_rows"] == 1
    assert not report["unquoted_state_inferred"]


def test_missing_membership_row_is_not_known(panel, metadata, calendar):
    history = build_history(panel, metadata, calendar, UniversePolicy()).drop((calendar[10], "510300.SH"))
    report = metadata_quote_coverage(panel, history)
    assert report["status"] == "failed" and report["unknown_quoted_rows"] == 1
