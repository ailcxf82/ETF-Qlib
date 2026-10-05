import pandas as pd
import pytest

from etf_ml.contracts import ResearchPolicy, RuntimeLimits, UniversePolicy
from etf_ml.data.snapshot import build_snapshot
from etf_ml.errors import QualityError
from etf_ml.research.context import FactorSpec, ResearchContext
from etf_ml.research.factor_engine import FactorEngine

IMAGE = "rdagent-qlib@sha256:97e456451ae9b3aa7c74456cf76afa2a6fd336b7b7cc96c5b98416a4de0bc373"
pytestmark = pytest.mark.docker

@pytest.fixture
def candidate_setup(source_spec, tmp_path):
    snapshot = build_snapshot(source_spec.source, source_spec,
                              UniversePolicy(minimum_listing_days=0, liquidity_lookback=1))
    panel = pd.read_parquet(snapshot.path / "research/panel.parquet")
    context = ResearchContext(snapshot_id=snapshot.snapshot_id, baseline_id="fixed",
                              protocol_id="fixture-protocol",
                              visible_start=str(panel.index.get_level_values("datetime").min()),
                              visible_end=str(panel.index.get_level_values("datetime").max()),
                              fields={c: {"unit": "verified_fixture"} for c in panel.select_dtypes("number")},
                              existing_features=["momentum_20"], model={"name": "lightgbm"},
                              selection_rules={"coverage": .95}, runtime={"maximum_lookback": 120})
    policy = ResearchPolicy(budget_mode="free_only", limits=RuntimeLimits(
        image=IMAGE, timeout_seconds=30, memory_mb=1024))
    spec = FactorSpec(factor_id="momentum_three", hypothesis="fixture momentum",
                      formula="close / lag3(close) - 1", required_fields=["adj_close"],
                      lookback=4, minimum_observations=4, expected_difference="shorter horizon",
                      context_hash=context.context_hash, source=(
                          "def compute(panel):\n"
                          "    value = panel['adj_close']\n"
                          "    lag = value.groupby(level='instrument').shift(3)\n"
                          "    return (value / lag - 1).to_frame('factor')\n"))
    return snapshot, context, policy, spec

def test_G2_real_isolated_factor_execution_and_cache(candidate_setup, tmp_path):
    snapshot, context, policy, spec = candidate_setup
    engine = FactorEngine(tmp_path / "workspaces", policy)
    artifact = engine.materialize(spec, context, snapshot.path / "research")
    assert artifact.manifest["quality"]["coverage"] == 1
    assert "future_perturbation_invariance" in artifact.manifest["checks"]["checks"]
    cached = engine.materialize(spec, context, snapshot.path / "research")
    pd.testing.assert_frame_equal(artifact.frame, cached.frame)


def test_G2_accepts_causal_rolling_skew_under_bounded_future_perturbation(candidate_setup, tmp_path):
    """Regression for pandas rolling-skew instability under extreme test data."""
    snapshot, context, policy, spec = candidate_setup
    spec.factor_id = "skewness_twenty"
    spec.lookback = 20
    spec.minimum_observations = 20
    spec.source = (
        "def compute(panel):\n"
        "    value = panel['return_1d']\n"
        "    result = value.groupby(level='instrument').transform(\n"
        "        lambda item: item.rolling(20, min_periods=20).skew()\n"
        "    )\n"
        "    return result.to_frame('factor')\n")
    artifact = FactorEngine(tmp_path / "workspaces", policy).materialize(
        spec, context, snapshot.path / "research"
    )
    assert "future_perturbation_invariance" in artifact.manifest["checks"]["checks"]


def test_G2_rejects_future_rolling_that_static_checks_cannot_prove(candidate_setup, tmp_path):
    snapshot, context, policy, spec = candidate_setup
    spec.source = (
        "def compute(panel):\n"
        "    value = panel['adj_close']\n"
        "    return value.iloc[::-1].rolling(5, min_periods=5).mean().iloc[::-1].to_frame('factor')\n")
    with pytest.raises(QualityError):
        FactorEngine(tmp_path / "workspaces", policy).materialize(spec, context, snapshot.path / "research")

@pytest.mark.parametrize("source", [
    "def compute(panel):\n    return panel['adj_close'].shift(-1).to_frame('factor')\n",
    "def compute(panel):\n    return (panel['adj_close'] * float('nan')).to_frame('factor')\n",
    "def compute(panel):\n    return panel['adj_close'].iloc[:-1].to_frame('factor')\n",
])
def test_G2_reject_future_nan_and_changed_index(candidate_setup, tmp_path, source):
    snapshot, context, policy, spec = candidate_setup
    spec.source = source
    with pytest.raises(QualityError):
        FactorEngine(tmp_path / "workspaces", policy).materialize(spec, context, snapshot.path / "research")
