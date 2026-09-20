from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from etf_ml.errors import ConfigurationError
from etf_ml.utils import content_hash

_FIT_LOCK = RLock()


def storage_root(artifact_root: Path) -> Path:
    """Keep the MLflow file store outside reserved artifact ancestors.

    The installed MLflow validates every ancestor of a run against its reserved
    'artifacts' directory name. Do not disable that security check.
    """
    root = Path(artifact_root).resolve()
    reserved = [p for p in (root, *root.parents) if p.name == "artifacts"]
    if reserved:
        anchor = reserved[-1].parent
        return anchor / ".etf-recorders" / content_hash(str(root))[:16]
    return root / "recorders"


@contextmanager
def scoped_recorder(run_id: str):
    import mlflow
    from qlib.workflow import R
    from qlib.workflow.recorder import Recorder

    with _FIT_LOCK:
        manager = R.exp_manager
        if manager.active_experiment is not None or mlflow.active_run() is not None:
            raise ConfigurationError("Model training requires an idle Qlib/MLflow recorder")
        def close(status):
            try:
                R.end_exp(status)
            except BaseException as closing_failure:
                try:
                    mlflow.end_run(Recorder.STATUS_FA)
                except BaseException as fallback:
                    closing_failure.add_note("MLflow cleanup failed: " + type(fallback).__name__)
                finally:
                    experiment = manager.active_experiment
                    if experiment is not None:
                        experiment.active_recorder = None
                    manager.active_experiment = None
                    manager._active_exp_uri = None
                raise

        try:
            R.start_exp(experiment_name="etf-" + run_id, recorder_name=run_id)
            yield R.get_recorder()
        except BaseException as failure:
            # R.start's cleanup begins only after startup has succeeded, and
            # catches Exception rather than cancellation. Cover both here.
            try:
                close(Recorder.STATUS_FA)
            except BaseException as cleanup:
                failure.add_note("Recorder cleanup failed: " + type(cleanup).__name__)
                experiment = manager.active_experiment
                if experiment is not None:
                    experiment.active_recorder = None
                manager.active_experiment = None
                manager._active_exp_uri = None
            raise
        else:
            close(Recorder.STATUS_FI)
