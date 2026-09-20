import json
import pytest

from etf_ml.contracts import RuntimeLimits, UniversePolicy
from etf_ml.data.snapshot import build_snapshot
from etf_ml.runtime import DockerBackend

IMAGE = "rdagent-qlib@sha256:97e456451ae9b3aa7c74456cf76afa2a6fd336b7b7cc96c5b98416a4de0bc373"
pytestmark = pytest.mark.docker

@pytest.fixture
def backend_data(source_spec, tmp_path):
    snapshot = build_snapshot(source_spec.source, source_spec,
                              UniversePolicy(minimum_listing_days=0, liquidity_lookback=1))
    return DockerBackend(tmp_path / "jobs"), snapshot.path / "research", tmp_path / "jobs/work"

def test_T14_actual_container_cannot_write_data_read_holdout_or_see_credentials(backend_data, monkeypatch):
    backend, view, workspace = backend_data
    monkeypatch.setenv("OPENAI_API_KEY", "private-host-only-key")
    code = (
        "import os,json; from pathlib import Path; "
        "result={'holdout_exists':Path('/research/../holdout/panel.parquet').exists(),"
        "'credential_visible':bool(os.getenv('OPENAI_API_KEY'))};\n"
        "try:\n Path('/research/illegal.txt').write_text('bad'); result['write_blocked']=False\n"
        "except OSError:\n result['write_blocked']=True\n"
        "print(json.dumps(result))")
    result = backend.run(["python", "-c", code], workspace, view,
                         RuntimeLimits(image=IMAGE, timeout_seconds=30))
    assert result.status == "succeeded"
    observed = json.loads(result.stdout.strip())
    assert observed == {"holdout_exists": False, "credential_visible": False, "write_blocked": True}

def test_T15_actual_container_memory_limit_records_oom(backend_data):
    backend, view, workspace = backend_data
    result = backend.run(["python", "-c", "memory = bytearray(300 * 1024 * 1024)"],
                         workspace, view, RuntimeLimits(image=IMAGE, memory_mb=64, timeout_seconds=30))
    assert result.status == "failed"
    assert result.reason == "memory_limit"

def test_T15_actual_container_timeout_stops_workload(backend_data):
    backend, view, workspace = backend_data
    result = backend.run(["python", "-c", "while True: pass"], workspace, view,
                         RuntimeLimits(image=IMAGE, timeout_seconds=1))
    assert result.status == "failed" and result.reason == "timeout"
    assert result.duration_seconds < 10
