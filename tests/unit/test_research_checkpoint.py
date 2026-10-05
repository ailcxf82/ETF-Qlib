import json

from etf_ml.errors import IntegrityError
from etf_ml.research.controller import ResearchController


def test_index_refresh_failure_preserves_the_committed_trial_reference(tmp_path):
    path = tmp_path / "checkpoint.json"
    state = {"trials": [{"path": "trial-00000.json", "sha256": "committed-hash"}]}

    class BrokenIndex:
        def rebuild(self):
            raise IntegrityError("injected index failure")

    assert ResearchController._refresh_index(state, path, BrokenIndex()) is False
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["trials"] == [{"path": "trial-00000.json", "sha256": "committed-hash"}]
    assert saved["status"] == "paused_index"
    assert saved["memory_index_stale"] is True
