import json
import pytest

from etf_ml.artifacts import RunStore
from etf_ml.config import load_config
from etf_ml.contracts import PortfolioPolicy, ResearchPolicy
from etf_ml.errors import ConfigurationError, IntegrityError
from etf_ml.utils import atomic_json, content_hash

def test_defaults_retain_confirmed_values_and_unresolved_semantics():
    config = load_config()
    assert config.portfolio.initial_cash == 500000
    assert config.portfolio.commission_rate == 0.003
    assert config.portfolio.slippage_rate == 0.0003
    assert len(config.validation.folds) == 5
    with pytest.raises(ConfigurationError, match="Unresolved"):
        config.portfolio.require_resolved()

@pytest.mark.parametrize("override", [
    {"portfolio": {"commission_rate": -1}},
    {"portfolio": {"k": float("nan")}},
    {"unknown_section": {}},
    {"portfolio": {"k_mode": "count", "k": .05}},
])
def test_invalid_configuration_rejected(override):
    with pytest.raises(ConfigurationError):
        load_config(overrides=override)

def test_file_and_cli_override_priority(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("portfolio:\n  initial_cash: 700000\n", encoding="utf-8")
    assert load_config(path).portfolio.initial_cash == 700000
    assert load_config(path, {"portfolio": {"initial_cash": 600000}}).portfolio.initial_cash == 600000

def test_budget_policies_are_distinct():
    assert ResearchPolicy(budget_mode="unlimited").api_budget is None
    assert ResearchPolicy(budget_mode="free_only", api_budget=0).api_budget == 0
    with pytest.raises(ValueError):
        ResearchPolicy(budget_mode="capped")
    with pytest.raises(ValueError):
        ResearchPolicy(budget_mode="unlimited", api_budget=100)

def test_atomic_configuration_redacts_keys_and_environment_values(tmp_path, monkeypatch):
    secret = "private_key_should_never_appear"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    path = tmp_path / "config.json"
    atomic_json(path, {"api_key": secret, "message": f"error {secret}"})
    assert secret not in path.read_text(encoding="utf-8")
    assert json.loads(path.read_text())["api_key"] == "[REDACTED]"

def test_immutable_runs_reuse_and_detect_corruption(tmp_path):
    with RunStore(tmp_path, "run-one", {"model": "ridge"}) as run:
        (run.path / "predictions.txt").write_text("original", encoding="utf-8")
        run.complete({"quality": "passed"})
    with RunStore(tmp_path, "run-one", {"model": "ridge"}) as run:
        assert run.reused
    with pytest.raises(ConfigurationError):
        with RunStore(tmp_path, "run-one", {"model": "different"}):
            pass
    (tmp_path / "run-one" / "predictions.txt").write_text("corrupt")
    with pytest.raises(IntegrityError):
        with RunStore(tmp_path, "run-one", {"model": "ridge"}):
            pass

def test_failed_attempt_retained_on_retry(tmp_path):
    with pytest.raises(RuntimeError):
        with RunStore(tmp_path, "retry", {}) as run:
            (run.path / "partial.txt").write_text("incomplete")
            (run.path / "fold" / "model").mkdir(parents=True)
            (run.path / "fold" / "model" / "prediction.txt").write_text("partial nested output")
            raise RuntimeError("failure")
    with RunStore(tmp_path, "retry", {}) as run:
        assert len(list(run.path.glob("attempt-*/partial.txt"))) == 1
        assert not (run.path / "partial.txt").exists()
        assert not (run.path / "fold").exists()
        assert len(list(run.path.glob("attempt-*/fold/model/prediction.txt"))) == 1
        run.complete()

def test_run_id_cannot_escape_root(tmp_path):
    with pytest.raises(ConfigurationError):
        RunStore(tmp_path, "../outside", {})


@pytest.mark.parametrize("name", ["../escape", "A/B", "..", "A\\B"])
def test_fold_names_cannot_escape_artifact_directories(name):
    from etf_ml.contracts import FoldSpec
    with pytest.raises(ValueError):
        FoldSpec(name=name, train={"start": "2020-01-01", "end": "2021-12-31"},
                 early_stop={"start": "2022-01-01", "end": "2022-12-31"},
                 selection={"start": "2023-01-01", "end": "2023-12-31"})


@pytest.mark.parametrize("name,constructor", [
    ("lightgbm", {"objective": "binary"}),
    ("lightgbm", {"objective": None}),
    ("lightgbm", {"loss": "binary"}),
    ("xgboost", {"objective": "binary:logistic"}),
    ("xgboost", {"objective": None}),
])
def test_model_objective_cannot_override_return_task(name, constructor):
    from etf_ml.contracts import ModelSpec
    from etf_ml.models.registry import create_model
    with pytest.raises(ConfigurationError):
        create_model(ModelSpec(name=name, constructor=constructor))



def test_concurrent_atomic_json_publication_survives_identical_clock(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    monkeypatch.setattr('etf_ml.utils.time.time_ns',lambda:123)
    barrier=threading.Barrier(8)
    def write(i):
        barrier.wait(timeout=10)
        atomic_json(tmp_path/f'output-{i}.json',{'writer':i})
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write,range(8)))
    assert [json.loads((tmp_path/f'output-{i}.json').read_text()) for i in range(8)]==[{'writer':i} for i in range(8)]
    assert not list(tmp_path.glob('.write-*.tmp'))
