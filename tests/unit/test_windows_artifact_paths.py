import json

import pytest

from etf_ml.errors import IntegrityError
from etf_ml.utils import atomic_json, file_hash, filesystem_path, verify_files
from etf_ml.validation.usage import HoldoutUsageStore


def test_long_atomic_hash_paths_keep_complete_identities(tmp_path):
    root = tmp_path / ("a" * 100) / ("b" * 100)
    name = "c" * 64 + ".json"
    path = root / name
    assert len(str(path.resolve())) > 260
    atomic_json(path, {"value": 42})
    assert json.loads(filesystem_path(path).read_text()) == {"value": 42}
    verify_files(root, {name: file_hash(path)})
    assert list(filesystem_path(root).glob(".write-*.tmp")) == []


def test_long_holdout_claim_and_history_reuse_integrity_and_immutable_completion(tmp_path):
    store = HoldoutUsageStore(tmp_path / ("a" * 100) / ("b" * 100))
    identity = {"start": "2026-01-01", "end": "2026-01-31", "model": "frozen"}
    claim = store.claim(identity, run_id="first")
    assert len(claim["usage_id"]) == 64
    assert store.claim(identity, run_id="retry") == claim
    started = store.record(claim["usage_id"], "started", details={"fixed": True})
    completed = store.record(claim["usage_id"], "completed", details={"proof": "same"})
    assert len(started["event_id"]) == len(completed["event_id"]) == 64
    assert store.record(claim["usage_id"], "completed", details={"proof": "same"}) == completed
    assert [x["status"] for x in store.history(claim["usage_id"])] == ["started", "completed"]
    path = store.root / "history" / claim["usage_id"] / (started["event_id"] + ".json")
    event = json.loads(path.read_text())
    event["details"] = {"changed": True}
    atomic_json(path, event)
    with pytest.raises(IntegrityError, match="changed"):
        store.history(claim["usage_id"])
