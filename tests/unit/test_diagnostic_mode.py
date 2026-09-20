from types import SimpleNamespace
import pandas as pd
import pytest
from etf_ml.contracts import AppConfig, UniversePolicy
from etf_ml.data.snapshot import build_snapshot
from etf_ml.data.universe import build_history
from etf_ml.data.diagnostic import require_formal, require_snapshot_mode
from etf_ml.errors import ConfigurationError, QualityError


def mark_metadata(spec):
    metadata = pd.read_parquet(spec.metadata_path)
    metadata.available_time = pd.Timestamp("2026-09-05")
    metadata.attrs = {"metadata_mode":"diagnostic_current_universe",
        "diagnostic_assumptions":["Current classification held constant retrospectively"],
        "source_hashes":{"original":"synthetic-test-proof"}}
    metadata.to_parquet(spec.metadata_path, index=False)
    return metadata


def test_diagnostic_preserves_refresh_time_and_source_scope(source_spec):
    spec = source_spec.model_copy(update={"mode":"diagnostic", "point_in_time_metadata":False})
    metadata = mark_metadata(spec)
    snapshot = build_snapshot(spec.source, spec)
    assert snapshot.manifest["qualification"]["mode"] == "diagnostic"
    assert not snapshot.manifest["qualification"]["investment_acceptance_eligible"]
    stored = pd.read_parquet(snapshot.path / "metadata.parquet")
    assert stored.available_time.eq(pd.Timestamp("2026-09-05")).all()
    panel = pd.read_parquet(snapshot.path / "panel.parquet")
    assert panel.index.get_level_values("instrument").nunique() == len(metadata)
    assert pd.read_parquet(snapshot.path / "universe.parquet").eligible.any()
    calendar = pd.DatetimeIndex(panel.index.get_level_values("datetime").unique())
    assert not build_history(panel, metadata, calendar, UniversePolicy()).eligible.any()


def test_formal_cannot_relabel_assumed_metadata(source_spec):
    mark_metadata(source_spec)
    with pytest.raises(QualityError, match="relabeled"):
        build_snapshot(source_spec.source, source_spec)


def test_diagnostic_requires_explicit_assumptions(source_spec):
    spec = source_spec.model_copy(update={"mode":"diagnostic", "point_in_time_metadata":False})
    with pytest.raises(QualityError, match="explicitly declare"):
        build_snapshot(spec.source, spec)


def test_diagnostic_retains_failed_action_consistency(source_spec, monkeypatch):
    spec = source_spec.model_copy(update={"mode":"diagnostic", "point_in_time_metadata":False})
    mark_metadata(spec)
    monkeypatch.setattr("etf_ml.data.action_consistency.adjustment_event_consistency",
        lambda *a,**kw: {"status":"incomplete", "unmapped":1})
    snapshot = build_snapshot(spec.source, spec)
    assert not snapshot.manifest["qualification"]["adjustment_consistency_passed"]
    import json
    quality = json.loads((snapshot.path / "data_quality.json").read_text())
    assert quality["adjustment_validation"]["unmapped"] == 1
    assert quality["status"] == "diagnostic"


@pytest.mark.parametrize("mode", ["formal", "diagnostic"])
def test_snapshot_modes_cannot_be_crossed(mode):
    config = AppConfig()
    config.data.mode = mode
    opposite = "formal" if mode == "diagnostic" else "diagnostic"
    with pytest.raises(ConfigurationError, match="qualification"):
        require_snapshot_mode(config, SimpleNamespace(manifest={"spec":{"mode":opposite}}))


def test_formal_freeze_rejects_diagnostic_before_training():
    from etf_ml.research.finalize import freeze_features
    config = AppConfig()
    config.data.mode = "diagnostic"
    session = SimpleNamespace(config=config, protocol=None, baseline=None,
        snapshot=SimpleNamespace(manifest={"spec":{"mode":"diagnostic"}}))
    with pytest.raises(ConfigurationError, match="formal acceptance"):
        freeze_features(session, None, run_id="test")
    with pytest.raises(ConfigurationError):
        require_formal(config)


def test_loop_closure_requires_real_responses_and_evaluated_candidate():
    from etf_ml.research.first_loop import closure_evidence
    state = {"status":"completed", "billing":{"calls":{
        str(i):{"status":"cost_unknown", "response_hash":"proof", "paid":True} for i in range(3)}}}
    record = {"result":{"by_candidate":[{"factor_id":"test", "status":"rejected", "reports":{"baseline":"proof"}}]}}
    baseline = {"by_fold":[{}]}
    result = closure_evidence(state, [record], baseline)
    assert result["status"] == "completed"
    assert result["completed_llm_calls"] == 3
    assert not result["investment_accepted"]
    record["result"]["by_candidate"][0]["failure_stage"] = "model_evaluation"
    assert closure_evidence(state, [record], baseline)["status"] == "incomplete"


def test_free_replay_cannot_claim_real_loop():
    from etf_ml.research.first_loop import closure_evidence
    state = {"status":"completed", "billing":{"calls":{
        str(i):{"status":"completed", "response_hash":"proof", "paid":False} for i in range(3)}}}
    record = {"result":{"by_candidate":[{"status":"rejected", "reports":{"baseline":"proof"}}]}}
    assert closure_evidence(state, [record], {"by_fold":[{}]})["status"] == "incomplete"
