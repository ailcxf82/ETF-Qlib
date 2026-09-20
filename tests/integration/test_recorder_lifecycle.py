from pathlib import Path

import mlflow
import pytest
import qlib
from qlib.workflow import R
from qlib.workflow.recorder import MLflowRecorder

from etf_ml.errors import ConfigurationError
from etf_ml.models.recorders import scoped_recorder, storage_root

pytestmark = pytest.mark.qlib


@pytest.fixture
def recorder(tmp_path):
    qlib.init(region="cn", exp_manager={
        "class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
        "kwargs": {"uri": (tmp_path / "mlruns").as_uri(), "default_exp_name": "lifecycle"}})


@pytest.mark.parametrize("after_start", [False, True])
def test_start_failure_does_not_block_next_training(recorder, monkeypatch, after_start):
    original = MLflowRecorder.start_run

    def interrupted(self):
        if after_start:
            original(self)
        raise RuntimeError("injected recorder startup failure")

    with monkeypatch.context() as patch:
        patch.setattr(MLflowRecorder, "start_run", interrupted)
        with pytest.raises(RuntimeError, match="startup failure"):
            with scoped_recorder("failed-start"):
                pytest.fail("startup failure must prevent model execution")
    assert R.exp_manager.active_experiment is None
    assert mlflow.active_run() is None
    with scoped_recorder("next-training") as current:
        assert current.id
    assert R.exp_manager.active_experiment is None


def test_cancellation_marks_run_failed_and_releases_recorder(recorder):
    with pytest.raises(KeyboardInterrupt):
        with scoped_recorder("cancelled-training") as current:
            recorder_id = current.id
            raise KeyboardInterrupt()
    assert R.exp_manager.active_experiment is None
    assert mlflow.active_run() is None
    result = mlflow.tracking.MlflowClient().get_run(recorder_id)
    assert result.info.status == "FAILED"
    with scoped_recorder("after-cancellation"):
        pass


def test_existing_experiment_is_preserved(recorder):
    with R.start(experiment_name="user-experiment"):
        current = R.get_recorder()
        with pytest.raises(ConfigurationError, match="idle"):
            with scoped_recorder("must-not-replace"):
                pass
        assert R.get_recorder().id == current.id


def test_reserved_ancestor_is_avoided_and_roots_stay_distinct(tmp_path):
    first = tmp_path / "artifacts" / "research" / "artifacts" / "session"
    second = tmp_path / "artifacts" / "another-session"
    store = storage_root(first)
    assert store.is_relative_to(tmp_path)
    assert "artifacts" not in store.parts
    assert store != storage_root(second)
    assert store == storage_root(first)


@pytest.mark.parametrize("after_end", [False, True])
def test_end_failure_is_reported_without_blocking_next_training(recorder, monkeypatch, after_end):
    original = MLflowRecorder.end_run

    def interrupted(self, status):
        if after_end:
            original(self, status)
        raise RuntimeError("injected recorder end failure")

    with monkeypatch.context() as patch:
        patch.setattr(MLflowRecorder, "end_run", interrupted)
        with pytest.raises(RuntimeError, match="end failure"):
            with scoped_recorder("failed-end"):
                pass
    assert R.exp_manager.active_experiment is None
    assert mlflow.active_run() is None
    with scoped_recorder("after-end-failure"):
        pass
