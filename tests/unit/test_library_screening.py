from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from etf_ml.contracts import AppConfig, UniversePolicy, ValidationSpec
from etf_ml.data.snapshot import build_snapshot
from etf_ml.datasets.labels import generate_labels
from etf_ml.errors import ConfigurationError, IntegrityError, QualityError
from etf_ml.features.alpha101 import catalog
from etf_ml.features.alpha101 import SOURCE
from etf_ml.research import library
from etf_ml.research.context import ResearchContext
from etf_ml.research.qualification import read_object
from etf_ml.utils import content_hash
from etf_ml.utils import atomic_json, file_hash, source_hashes, verify_files


@pytest.fixture
def screening_case(tmp_path, source_spec, fold):
    source_spec.holdout_start = "2024-01-01"
    policy = UniversePolicy(minimum_listing_days=0, liquidity_lookback=1)
    snapshot = build_snapshot(source_spec.source, source_spec, policy)
    config = AppConfig(data=source_spec, universe=policy, artifact_root=tmp_path / "artifacts",
                       validation=ValidationSpec(folds=[fold], holdout_start=source_spec.holdout_start))
    return config, snapshot


def test_screening_real_snapshot_zero_llm_and_verified_reuse(screening_case, monkeypatch):
    config, snapshot = screening_case
    output = config.artifact_root / "library_screening" / "batch"
    protected = source_hashes(snapshot.path)
    original = pd.read_parquet
    reads = []

    def research_reads(path, *args, **kwargs):
        path = Path(path)
        assert "holdout" not in path.parts
        if path.name == "universe.parquet":
            assert kwargs["filters"] == [("datetime", "<", pd.Timestamp(config.validation.holdout_start))]
        reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(library.pd, "read_parquet", research_reads)
    result = library.screen_library(config, snapshot.path, output, run_id="batch", numbers=[6, 12])
    assert result["status"] == "completed" and result["attempted_definitions"] == 2
    assert result["external_calls"] == result["formal_evaluations"] == 0
    assert result["holdout_evaluated"] is False
    assert all(row["technical_status"] == "passed" for row in result["by_factor"])
    assert all(row["formal_status"] == "not_run" for row in result["by_factor"])
    for factor in result["by_factor"]:
        assert factor['by_fold']
        assert factor['factor_signal_gate']['rule'] == 'strict_majority'
        assert factor['factor_signal_gate']['fold_count'] == len(config.validation.folds)
        assert factor['factor_signal_gate']['passing_folds'] == sum(
            item['factor_signal']['oriented_validation_gate']['status'] == 'passed'
            for item in factor['by_fold'])
        signal = factor['by_fold'][0]['factor_signal']
        assert signal['training_orientation']['direction'] in {'positive', 'reverse', 'unknown'}
        assert {'ic', 'icir', 'icir_status'}.issubset(signal['raw_validation'])
        assert signal['oriented_validation_gate']['status'] in {'passed', 'failed', 'unknown'}
    assert len(read_object(output / "horizon_diagnostics.json")["by_factor_fold_horizon"]) == 8
    correlations = read_object(output / "correlation_diagnostics.json")
    assert correlations["scope"] == "inner_validation_cross_sectional_rank_correlation"
    assert result["correlation_diagnostics"]["sha256"] == file_hash(output / "correlation_diagnostics.json")
    assert result["correlation_diagnostics"]["effect"] == "diagnostic_only_no_candidate_exclusion"
    manifest = read_object(output / "manifest.json")
    verify_files(output, manifest["files"])
    # Completion must not append progress after manifest commit.
    before = source_hashes(output)
    assert library.screen_library(config, snapshot.path, output, run_id="batch", numbers=[6, 12]) == result
    assert source_hashes(output) == before
    assert source_hashes(snapshot.path) == protected
    assert snapshot.path / "research" / "panel.parquet" in reads
    assert read_object(output / "screening_manifest.json")["screen_rule"] == library.SCREEN_RULE


def test_correlation_diagnostic_groups_redundant_signals_without_excluding_them():
    index = pd.MultiIndex.from_product(
        [[pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")], ["A", "B", "C"]],
        names=["datetime", "instrument"])
    eligible = pd.Series(True, index=index)
    factors = {"same": pd.Series([1, 2, 3, 1, 2, 3], index=index),
               "inverse": pd.Series([3, 2, 1, 3, 2, 1], index=index)}
    result = library.factor_correlation_diagnostics(factors, [{"fold": "F", "validate": eligible}])
    assert result["clusters"] == [["inverse", "same"]]
    assert result["by_fold"][0]["median_rank_correlation"] == pytest.approx(-1.)
    assert result["cluster_rule"]["effect"] == "diagnostic_only_no_candidate_exclusion"


def test_alpha101_shortlist_enforces_frozen_mechanism_slots():
    definitions = {row["definition_hash"]: row for row in catalog()["definitions"]}
    candidates = [{"definition_hash": row["definition_hash"], "score": score}
                  for score, row in enumerate(definitions.values(), start=20)]
    selected = library._diverse_shortlist(candidates, definitions)
    assert len(selected) == 5
    assert len({row["mechanism_slot"] for row in selected}) == 5
    assert {row["mechanism_slot"] for row in selected} == set(library.SCREEN_RULE["mechanism_slots"].values())
    duplicate_only = [row for row in candidates if definitions[row["definition_hash"]]["research_group"] == "volume_price"]
    assert len(library._diverse_shortlist(duplicate_only, definitions)) == 1


def test_library_horizon_comparison_uses_distinct_protocols_and_never_selects_a_winner(
        screening_case, monkeypatch):
    from etf_ml.cli import parser

    args = parser().parse_args(["compare-library-horizons", "--snapshot", "snapshot",
        "--screening-run", "screen", "--run-id", "horizon-cli"])
    assert args.command == "compare-library-horizons"
    config, snapshot = screening_case
    config.research.budget_mode = "unlimited"
    screen = config.artifact_root / "library_screening" / "screen-for-horizons"
    screen.mkdir(parents=True)
    definition = catalog()["definitions"][0]
    selection = [{"factor_id": definition["factor_id"], "definition_hash": definition["definition_hash"]}]
    atomic_json(screen / "screening_manifest.json", {"source_hash": content_hash(SOURCE),
        "snapshot_manifest_hash": file_hash(snapshot.path / "snapshot_manifest.json"),
        "definitions": [definition["definition_hash"]]})
    atomic_json(screen / "screening_report.json", {"status": "completed", "run_id": "screen",
        "snapshot_id": snapshot.snapshot_id, "attempted_definitions": 1})
    atomic_json(screen / "shortlist.json", {"schema_version": "library-shortlist-v1",
        "formal_acceptance": False, "selected": selection})
    atomic_json(screen / "status.json", {"status": "completed"})
    files = {key: value for key, value in source_hashes(screen).items()
             if key not in {"status.json", "manifest.json"}}
    atomic_json(screen / "manifest.json", {"run_id": screen.name, "files": files})

    calls = []

    def evaluate(config_for_horizon, snapshot_path, screening_path, output, *, campaign_id):
        horizon = config_for_horizon.label.horizon
        calls.append((horizon, campaign_id))
        output.mkdir(parents=True)
        report = {"schema_version": "library-formal-evaluation-v2", "status": "completed",
            "run_id": campaign_id, "snapshot_id": snapshot.snapshot_id, "label_horizon": horizon,
            "protocol_id": f"protocol-{horizon}", "attempted_formal_candidates": 1,
            "campaign_max_attempts": 5, "by_factor": [{"factor_id": definition["factor_id"],
                "formal_status": "rejected", "reasons": ["fixture_rejection"]}],
            "external_calls": 0, "holdout_evaluated": False}
        atomic_json(output / "formal_evaluation_report.json", report)
        return report

    monkeypatch.setattr(library, "evaluate_library_shortlist", evaluate)
    result = library.compare_library_horizons(config, snapshot.path, screen, run_id="horizon-fixture")

    assert [item[0] for item in calls] == [1, 5, 10, 20]
    assert [item["horizon"] for item in result["by_horizon"]] == [1, 5, 10, 20]
    assert len({item["protocol_id"] for item in result["by_horizon"]}) == 4
    assert result["horizon_selection"] == "none_development_results_are_not_independent_confirmation"
    assert result["holdout_read"] is False and result["external_calls"] == 0
    before = source_hashes(result_path := config.artifact_root / "library_horizon_comparisons" / "horizon-fixture")
    reused = library.compare_library_horizons(config, snapshot.path, screen, run_id="horizon-fixture")
    assert reused == result and len(calls) == 4
    assert source_hashes(result_path) == before
    child_report = Path(result["by_horizon"][0]["report_path"])
    atomic_json(child_report, {"tampered": True})
    with pytest.raises(IntegrityError, match="Referenced horizon evaluation report changed"):
        library.compare_library_horizons(config, snapshot.path, screen, run_id="horizon-fixture")


def test_correlation_diagnostic_marks_insufficient_overlap_unavailable():
    index = pd.MultiIndex.from_product(
        [[pd.Timestamp("2024-01-02")], ["A", "B", "C"]], names=["datetime", "instrument"])
    eligible = pd.Series(True, index=index)
    factors = {"left": pd.Series([1., 2., float("nan")], index=index),
               "right": pd.Series([1., float("nan"), 3.], index=index)}
    result = library.factor_correlation_diagnostics(factors, [{"fold": "F", "validate": eligible}])
    assert result["clusters"] == []
    assert result["by_fold"][0]["status"] == "unavailable"
    assert result["by_fold"][0]["matched_dates"] == 0


def test_correlation_diagnostic_skips_constant_cross_sections_without_warnings():
    import warnings

    index = pd.MultiIndex.from_product(
        [[pd.Timestamp("2024-01-02")], ["A", "B", "C"]], names=["datetime", "instrument"])
    eligible = pd.Series(True, index=index)
    factors = {"constant": pd.Series([1., 1., 1.], index=index),
               "variable": pd.Series([1., 2., 3.], index=index)}
    with warnings.catch_warnings(record=True) as observed:
        warnings.simplefilter("always")
        result = library.factor_correlation_diagnostics(
            factors, [{"fold": "F", "validate": eligible}])
    assert not observed
    assert result["by_fold"][0]["status"] == "unavailable"


def test_checkpoint_recovers_prefix_without_recomputing_committed_definition(screening_case, monkeypatch):
    config, snapshot = screening_case
    output = config.artifact_root / "library_screening" / "recover"
    materialize = library.materialize_library_factor
    seen = []

    def fail_second(panel, eligible, definition, **kwargs):
        seen.append(definition["number"])
        if definition["number"] == 12:
            raise RuntimeError("injected interruption")
        return materialize(panel, eligible, definition, **kwargs)

    monkeypatch.setattr(library, "materialize_library_factor", fail_second)
    with pytest.raises(RuntimeError, match="injected"):
        library.screen_library(config, snapshot.path, output, run_id="recover", numbers=[6, 12])
    committed_hash = file_hash(output / "screening_checkpoint.json")
    assert read_object(output / "status.json")["status"] == "failed"
    seen.clear()

    def remaining_only(panel, eligible, definition, **kwargs):
        seen.append(definition["number"])
        assert definition["number"] == 12
        return materialize(panel, eligible, definition, **kwargs)

    monkeypatch.setattr(library, "materialize_library_factor", remaining_only)
    result = library.screen_library(config, snapshot.path, output, run_id="recover", numbers=[6, 12])
    assert seen == [12] and result["recovery"]["recovered_definitions"] == 1
    assert result["recovery"]["sha256"] == committed_hash
    assert file_hash(Path(result["recovery"]["path"])) == committed_hash
    assert len(read_object(output / "horizon_diagnostics.json")["by_factor_fold_horizon"]) == 8
    verify_files(output, read_object(output / "manifest.json")["files"])


def test_checkpoint_tampering_blocks_resume(screening_case, monkeypatch):
    config, snapshot = screening_case
    output = config.artifact_root / "library_screening" / "bad"
    original = library.materialize_library_factor

    def fail_second(panel, eligible, definition, **kwargs):
        if definition["number"] == 12:
            raise RuntimeError("injected")
        return original(panel, eligible, definition, **kwargs)

    monkeypatch.setattr(library, "materialize_library_factor", fail_second)
    with pytest.raises(RuntimeError):
        library.screen_library(config, snapshot.path, output, run_id="bad", numbers=[6, 12])
    path = output / "screening_checkpoint.json"
    checkpoint = read_object(path)
    checkpoint["completed_definitions"][0]["screen_status"] = "accepted"
    atomic_json(path, checkpoint)
    with pytest.raises(IntegrityError, match="checkpoint identity or hash"):
        library.screen_library(config, snapshot.path, output, run_id="bad", numbers=[6, 12])


def test_materialization_cache_hashes_actual_inputs_and_validator(tmp_path, panel, monkeypatch):
    eligible = pd.Series(True, index=panel.index)
    definition = next(d for d in catalog()["definitions"] if d["number"] == 6)
    args = dict(inputs={"snapshot_id": "synthetic"}, root=tmp_path / "cache", coverage_threshold=.95)
    a, manifest, timing = library.materialize_library_factor(panel, eligible, definition, **args)
    b, _, again = library.materialize_library_factor(panel, eligible, definition, **args)
    assert not timing["cache_hit"] and again["cache_hit"]
    assert_frame_equal(a, b)
    changed = panel.copy()
    changed.loc[changed.index[-1], "adj_open"] *= 1.1
    _, changed_manifest, timing = library.materialize_library_factor(changed, eligible, definition, **args)
    assert not timing["cache_hit"] and changed_manifest["cache_key"] != manifest["cache_key"]
    original_hash = library.file_hash
    monkeypatch.setattr(library, "file_hash", lambda path: "validator-v2" if Path(path).name == "validators.py" else original_hash(path))
    _, validator_manifest, _ = library.materialize_library_factor(panel, eligible, definition, **args)
    assert validator_manifest["cache_key"] != manifest["cache_key"]
    monkeypatch.setattr(library, "file_hash", original_hash)
    cache_manifest = Path(manifest["path"]) / "feature_manifest.json"
    manifest["quality"]["coverage"] = .01
    atomic_json(cache_manifest, manifest)
    with pytest.raises(IntegrityError, match="cache identity"):
        library.materialize_library_factor(panel, eligible, definition, **args)


def test_materialization_cache_invalidates_on_environment_change(tmp_path, panel, monkeypatch):
    eligible = pd.Series(True, index=panel.index)
    definition = next(d for d in catalog()["definitions"] if d["number"] == 6)
    args = dict(inputs={"snapshot_id": "synthetic"}, root=tmp_path / "cache", coverage_threshold=.95)
    _, before, _ = library.materialize_library_factor(panel, eligible, definition, **args)
    monkeypatch.setattr(library, "environment_manifest", lambda: {"python": "different-runtime"})
    _, after, timing = library.materialize_library_factor(panel, eligible, definition, **args)
    assert not timing["cache_hit"]
    assert after["cache_key"] != before["cache_key"]


def test_training_masks_purge_labels_and_never_enter_selection(panel, calendar, fold):
    config = AppConfig(validation=ValidationSpec(folds=[fold], holdout_start="2024-01-01"))
    labels, events = generate_labels(panel, calendar, config.label)
    splits = library.training_splits(panel, calendar, labels, events, pd.Series(True, index=panel.index), config.validation)
    split = splits[0]
    assert (events.loc[split["fit"], "available_time"] < pd.Timestamp(split["fit_available_before"])).all()
    assert (events.loc[split["validate"], "available_time"] <= pd.Timestamp(fold.train.end) + pd.Timedelta(hours=23)).all()
    assert panel.index[split["validate"]].get_level_values("datetime").max() < pd.Timestamp(fold.early_stop.start)
    assert not (split["fit"] & split["validate"]).any()


@pytest.mark.parametrize("numbers", [[], [6, 6], [True], [6.0], [[6]], [999], list(range(21))])
def test_registered_bounded_batch_only(screening_case, numbers):
    config, snapshot = screening_case
    with pytest.raises(ConfigurationError, match="registered integer"):
        library.screen_library(config, snapshot.path, config.artifact_root / "library_screening" / "batch", run_id="batch", numbers=numbers)


def test_technical_rejection_is_not_economic_rejection(screening_case, monkeypatch):
    config, snapshot = screening_case
    def constant(panel, number, *, eligible):
        return pd.DataFrame({"constant": 1.}, index=panel.index)
    monkeypatch.setattr(library, "compute_alpha101", constant)
    result = library.screen_library(config, snapshot.path, config.artifact_root / "library_screening" / "constant", run_id="constant", numbers=[6])
    row = result["by_factor"][0]
    assert row["technical_status"] == "failed" and row["screen_status"] == "not_scored"
    assert row["reason_codes"] == ["Constant factor"] and result["shortlist_count"] == 0


def test_pit_exposure_is_not_inferred_from_current_classification(panel):
    universe = pd.DataFrame({"eligible": True, "tracking_group": "same"}, index=panel.index)
    assert library.exposure_diagnostics(panel, universe, pit_verified=False)["status"] == "bypassed"
    result = library.exposure_diagnostics(panel, universe, pit_verified=True)
    assert result["scope"] == "eligible_universe_not_portfolio_holdings"
    assert result["by_date"][0]["largest_group"] == 3
    universe.loc[universe.index[0], "tracking_group"] = None
    assert library.exposure_diagnostics(panel, universe, pit_verified=True)["status"] == "partial"


def test_screen_library_cli_is_separate_from_paid_research():
    from etf_ml.cli import parser
    args = parser().parse_args(["screen-library", "--snapshot", "snapshot", "--formulas", "6", "12", "--run-id", "library"])
    assert args.formulas == [6, 12] and args.command == "screen-library"


def test_trusted_library_spec_is_versioned_and_pit_bound(panel, calendar):
    definition = next(row for row in catalog()["definitions"] if row["number"] == 40)
    context = ResearchContext(snapshot_id="snapshot", baseline_id="baseline", protocol_id="protocol",
        visible_start=str(calendar[0]), visible_end=str(calendar[-1]),
        fields={name: {"allowed_usage": "proposal"} for name in definition["required_fields"]},
        existing_features=[], model={}, selection_rules={}, runtime={"maximum_lookback": 120,
            "trusted_library_source_hash": content_hash(SOURCE)})
    spec = library.registered_factor_spec(definition, context)
    assert spec.trusted_implementation["source_manifest_hash"] == content_hash(SOURCE)
    assert spec.cross_sectional and spec.formula == definition["formula"]
    tampered = spec.model_copy(update={"trusted_implementation": {
        **spec.trusted_implementation, "number": 101}})
    with pytest.raises(QualityError, match="Trusted"):
        tampered.validate_context(context)
    ordinary = context.model_copy(deep=True)
    ordinary.runtime.pop("trusted_library_source_hash")
    spec = spec.model_copy(update={"context_hash": ordinary.context_hash})
    with pytest.raises(QualityError, match="not enabled by the controller"):
        spec.validate_context(ordinary)


def test_trusted_library_factor_engine_uses_registered_pit_path(screening_case, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from etf_ml.contracts import ModelSpec, PortfolioPolicy, ResearchPolicy, ValidationSpec, UniversePolicy
    from etf_ml.features.baseline import materialize
    from etf_ml.research.context import ResearchContext
    from etf_ml.research.factor_engine import FactorEngine
    from etf_ml.research.protocol import ComparisonProtocol
    from etf_ml.registry import FactorRegistry
    from etf_ml.adapters.rdagent.experiment import ETFFactorTask, ETFWorkspace, FactorProposal

    config, snapshot = screening_case
    panel = pd.read_parquet(snapshot.path / "research" / "panel.parquet")
    baseline = materialize({"snapshot_id": snapshot.snapshot_id}, panel)
    config.universe = UniversePolicy(minimum_listing_days=0, liquidity_lookback=1)
    config.validation = ValidationSpec(folds=[config.validation.folds[0]], holdout_start=config.data.holdout_start)
    # The feature execution path requires only a valid frozen protocol identity.
    config.models = [ModelSpec(name="lightgbm")]
    protocol = ComparisonProtocol(snapshot_id=snapshot.snapshot_id,
        baseline_feature_set_id=baseline.feature_set_id, label=config.label, universe=config.universe,
        validation=config.validation, portfolio=PortfolioPolicy(k_mode="fraction", minimum_commission=0,
            liquidity_mode="participation", risk_mode="max_drawdown"),
        research=ResearchPolicy(budget_mode="unlimited", seeds=[42, 43]), model=config.models[0],
        stress_min_excess_return=-.03)
    context = ResearchContext(snapshot_id=snapshot.snapshot_id, baseline_id=baseline.feature_set_id,
        protocol_id=protocol.protocol_id, visible_start=str(panel.index.get_level_values("datetime").min()),
        visible_end=str(panel.index.get_level_values("datetime").max()),
        fields={name: {"allowed_usage": "proposal"} for name in panel.select_dtypes("number").columns},
        existing_features=list(baseline.frame.columns), model=protocol.model.model_dump(),
        selection_rules={"protocol_id": protocol.protocol_id}, runtime={"maximum_lookback": 120,
            "trusted_library_source_hash": content_hash(SOURCE)})
    definition = next(row for row in catalog()["definitions"] if row["number"] == 40)
    spec = library.registered_factor_spec(definition, context)
    session = SimpleNamespace(context=context, root=tmp_path / "session", snapshot=snapshot,
        protocol=protocol, eligibility=pd.read_parquet(snapshot.path / "universe.parquet").eligible.reindex(panel.index),
        registry=FactorRegistry(tmp_path / "registry"),
        engine=FactorEngine(tmp_path / "factors", config.research))
    monkeypatch.setattr(session.engine.backend, "run",
        lambda *args, **kwargs: pytest.fail("Trusted library execution must not enter a generated-code container"))
    proposal = FactorProposal.model_validate(spec.model_dump(mode="json"))
    task = ETFFactorTask(proposal)
    workspace = ETFWorkspace(session, task=task, experiment_id="trusted-library")
    artifact = workspace.execute()
    assert task.artifact is artifact
    assert artifact.manifest["checks"]["execution"] == "trusted_controller_library"
    assert "factor.py" not in {path.name for path in workspace.workspace_path.iterdir()}
    assert session.registry.load(spec.factor_id, spec.version)["state"] == "validated"
    cached = session.engine.materialize(spec, context, snapshot.path / "research", eligibility=session.eligibility)
    assert cached.feature_set_id == artifact.feature_set_id
    assert cached.manifest["result_hash"] == artifact.manifest["result_hash"]
    manifest_path = Path(artifact.manifest["path"]) / "feature_manifest.json"
    atomic_json(manifest_path, {**artifact.manifest, "checks": {"status": "passed"}, "manifest_hash": "tampered"})
    with pytest.raises(QualityError, match="cache identity"):
        session.engine.materialize(spec, context, snapshot.path / "research", eligibility=session.eligibility)


def test_evaluate_shortlist_cli_requires_explicit_campaign_identity():
    from etf_ml.cli import parser
    args = parser().parse_args(["evaluate-library-shortlist", "--snapshot", "snap",
        "--screening-run", "screen", "--campaign-id", "new-formal-five",
        "--campaign-max-trials", "3"])
    assert args.command == "evaluate-library-shortlist" and args.campaign_id == "new-formal-five"
    assert args.campaign_max_trials == 3


def test_frozen_shortlist_registers_and_records_formal_decision(screening_case, monkeypatch):
    from etf_ml.artifacts import RunStore
    from etf_ml.features.baseline import materialize
    from etf_ml.research.campaign import CampaignLedger

    config, snapshot = screening_case
    config.portfolio.minimum_commission = 0
    config.research.budget_mode = "unlimited"
    config.research.stress_min_excess_return = -.03
    definition = next(row for row in catalog()["definitions"] if row["number"] == 6)
    baseline = materialize({"snapshot_id": snapshot.snapshot_id}, pd.read_parquet(snapshot.path / "research" / "panel.parquet"))
    screening = config.artifact_root / "library_screening" / "synthetic-completed"
    screen_identity = {"fixture": True, "snapshot_id": snapshot.snapshot_id,
        "source_hash": content_hash(SOURCE), "snapshot_manifest_hash": file_hash(snapshot.path / "snapshot_manifest.json"),
        "definitions": [definition["definition_hash"]]}
    with RunStore(screening.parent, screening.name, screen_identity) as run:
        atomic_json(run.path / "screening_manifest.json", screen_identity)
        atomic_json(run.path / "screening_report.json", {"status": "completed", "run_id": screening.name,
            "snapshot_id": snapshot.snapshot_id, "baseline_feature_set_id": baseline.feature_set_id,
            "attempted_definitions": 20, "by_factor": [{"factor_id": definition["factor_id"],
                "definition_hash": definition["definition_hash"], "screen_status": "shortlisted",
                "technical_status": "passed", "mechanism_slot": "volume_price"}]})
        atomic_json(run.path / "shortlist.json", {"schema_version": "library-shortlist-v1",
                "formal_acceptance": False, "selected": [{"factor_id": definition["factor_id"],
                    "definition_hash": definition["definition_hash"], "mechanism_slot": "volume_price"}]})
        run.complete()

    def reject_formally(session, factor, *, run_id):
        proof = session.root / "paired" / "runs" / run_id / "evaluation.json"
        atomic_json(proof, {"status": "rejected", "factor_version_id": factor.feature_set_id,
                            "protocol_id": session.protocol.protocol_id,
                            "reasons": ["joint_formal_gate_rejected"]})
        return {"status": "rejected", "evaluation": {"reasons": ["joint_formal_gate_rejected"]}}

    monkeypatch.setattr("etf_ml.adapters.rdagent.runner.execute_research", reject_formally)
    output = config.artifact_root / "library_evaluations" / "alpha101-formal-fixture"
    first = library.evaluate_library_shortlist(config, snapshot.path, screening, output,
                                               campaign_id=output.name, campaign_max_trials=3)
    assert first["attempted_formal_candidates"] == 1
    assert first["by_factor"][0]["formal_status"] == "rejected", first["by_factor"][0]
    assert first["campaign_max_attempts"] == 3 and first["campaign_summary"]["open_attempts"] == 0
    assert first["external_calls"] == 0 and first["holdout_evaluated"] is False
    registry_path = output / "research" / "registry"
    from etf_ml.registry import FactorRegistry
    assert FactorRegistry(registry_path).load(definition["factor_id"], 1)["state"] == "rejected"
    assert CampaignLedger(config.artifact_root / "research_campaigns", output.name, 3).summary()[
        "generation_attempts"] == 1
    status = read_object(output / "status.json")
    atomic_json(output / "status.json", {**status, "status": "failed", "reason": "simulated_interruption"})
    resumed = library.evaluate_library_shortlist(config, snapshot.path, screening, output,
                                                 campaign_id=output.name, campaign_max_trials=3)
    assert resumed["by_factor"][0]["formal_status"] == "rejected"
    assert resumed["by_factor"][0]["reused_committed_attempt"] is True
    assert resumed["campaign_summary"]["generation_attempts"] == 1
