"""Actual container inference; frozen approval metadata is a synthetic fixture."""
import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from etf_ml.contracts import FeatureArtifact, ResearchPolicy, RuntimeLimits, UniversePolicy
from etf_ml.data.snapshot import build_snapshot
from etf_ml.errors import ConfigurationError, IntegrityError, QualityError
from etf_ml.features.baseline import materialize
from etf_ml.models import deployment
from etf_ml.models.frozen_features import FrozenFactorBackend, materialize_frozen_features
from etf_ml.research.context import FactorSpec
from etf_ml.utils import atomic_json, content_hash, file_hash


@pytest.fixture
def approved_inference(source_spec, tmp_path, monkeypatch):
    universe_policy = UniversePolicy(minimum_listing_days=0, liquidity_lookback=1)
    snapshot = build_snapshot(source_spec.source, source_spec, universe_policy)
    panel = pd.read_parquet(snapshot.path / "panel.parquet")
    research = pd.read_parquet(snapshot.path / "research/panel.parquet")
    base = materialize({"snapshot_id": snapshot.snapshot_id}, research)
    spec = FactorSpec(factor_id="frozen_trend", hypothesis="trend", formula="close/lag(close)-1",
        required_fields=["adj_close"], lookback=4, minimum_observations=4,
        expected_difference="distinct trend", context_hash="synthetic-approved-context",
        source="def compute(panel):\n    close = panel['adj_close']\n    return close / close.groupby(level='instrument').shift(3) - 1\n")
    entry = {"spec": spec.model_dump(mode="json"), "factor_version_id": spec.version_id,
             "column": "frozen_trend_v1", "group": "trend"}
    frame = base.frame.copy()
    close = research.adj_close
    frame[entry["column"]] = close / close.groupby(level="instrument").shift(3) - 1
    reference = FeatureArtifact(content_hash(entry), frame, {"composition": {
        "base_manifest": base.manifest, "factors": [entry]}})
    policy = ResearchPolicy(budget_mode="free_only", limits=RuntimeLimits(timeout_seconds=180,
        memory_mb=1024, image="rdagent-qlib@sha256:97e456451ae9b3aa7c74456cf76afa2a6fd336b7b7cc96c5b98416a4de0bc373"))
    package = {"version_id": content_hash({"fixture": entry}), "columns": list(frame.columns),
               "protocol": {"universe": universe_policy.model_dump(mode="json")},
               "config": {"research": policy.model_dump(mode="json")}}
    package_path = tmp_path / "package"
    atomic_json(package_path / "features/manifest.json", {"factors": [entry]})
    # Approval/provenance validation is exercised by actual freeze/holdout tests.
    # Only that validation is stubbed here; source checks and Docker are actual.
    monkeypatch.setattr(deployment, "load_frozen_model", lambda *args, **kwargs: (None, reference, package))
    return snapshot, panel, reference, package, package_path, spec, policy


@pytest.mark.docker
def test_actual_frozen_factor_container_reproduces_past_and_materializes_holdout(approved_inference, tmp_path):
    snapshot, panel, reference, package, package_path, spec, policy = approved_inference
    features, _, _, evidence = materialize_frozen_features(package_path, reference, package,
        snapshot.path, tmp_path / "inference", purpose="independent_holdout")
    assert features.shape == (len(panel), 21)
    assert evidence["historical_feature_parity"] == "passed" and len(evidence["factors"]) == 1
    close = panel.adj_close
    expected = (close / close.groupby(level="instrument").shift(3) - 1).rename("frozen_trend_v1")
    pd.testing.assert_series_equal(features.frozen_trend_v1, expected)
    assert evidence["factors"][0]["checks"]["status"] == "passed"
    assert features.loc[reference.frame.index].equals(reference.frame)


@pytest.mark.parametrize("corruption", ["source", "command", "view"])
def test_private_backend_rejects_unapproved_source_command_or_view(approved_inference, tmp_path, corruption):
    snapshot, panel, reference, package, package_path, spec, policy = approved_inference
    root = tmp_path / "private"
    view = root / "inputs/panel"
    view.mkdir(parents=True)
    shutil.copyfile(snapshot.path / "panel.parquet", view / "panel.parquet")
    atomic_json(view / "input_manifest.json", {"version_id": package["version_id"], "snapshot_id": snapshot.snapshot_id,
        "purpose": "independent_holdout", "panel_sha256": file_hash(view / "panel.parquet")})
    workspace = root / "factor"
    workspace.mkdir()
    (workspace / "factor.py").write_text(spec.source, encoding="utf-8")
    import etf_ml.research.factor_engine as engine_module
    shutil.copyfile(Path(engine_module.__file__).with_name("factor_worker.py"), workspace / "worker.py")
    atomic_json(workspace / "factor_spec.json", spec)
    backend = FrozenFactorBackend(root, package_path, view, snapshot.path)
    argv = ["python", "worker.py", "/research/panel.parquet", "temporal"]
    if corruption == "source": (workspace / "factor.py").write_text(spec.source + "\n# changed", encoding="utf-8")
    if corruption == "command": argv = ["python", "-c", "print('unapproved')"]
    if corruption == "view": view = snapshot.path / "holdout"
    with pytest.raises((ConfigurationError, IntegrityError, QualityError)):
        backend.command(argv, workspace, view, policy.limits, container_name="no-launch")