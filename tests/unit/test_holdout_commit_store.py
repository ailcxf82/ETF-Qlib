import json

import pytest

from etf_ml.artifacts import RunStore
from etf_ml.utils import atomic_json, file_hash


@pytest.mark.parametrize("preserve", [False, True])
def test_completed_execution_can_preserve_data_when_later_decision_commit_fails(tmp_path, preserve):
    root = tmp_path / "runs"
    with pytest.raises(RuntimeError, match="decision publication"):
        with RunStore(root, "evaluation", {"candidate": "fixed"}, preserve_completed_on_error=preserve) as run:
            atomic_json(run.path / "data.json", {"fixed_output": 42})
            run.complete({"execution_status": "completed"})
            original_hash = file_hash(run.path / "manifest.json")
            raise RuntimeError("decision publication failed")
    status = json.loads((root / "evaluation/status.json").read_text())
    assert status["status"] == ("completed" if preserve else "failed")
    with RunStore(root, "evaluation", {"candidate": "fixed"}, preserve_completed_on_error=preserve) as resumed:
        assert resumed.reused is preserve
        if preserve:
            assert file_hash(resumed.path / "manifest.json") == original_hash
        else:
            assert list(resumed.path.glob("attempt-*"))