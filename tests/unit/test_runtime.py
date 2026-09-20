import os
import sys
from pathlib import Path

import pytest
from etf_ml.contracts import RuntimeLimits, UniversePolicy
from etf_ml.data.snapshot import build_snapshot
from etf_ml.errors import ConfigurationError
from etf_ml.runtime import NativeBackend, DockerBackend

def test_generated_code_cannot_use_native_backend(tmp_path):
    with pytest.raises(ConfigurationError, match="Docker"):
        NativeBackend(tmp_path).run([sys.executable, "-c", "print(1)"],
                                    tmp_path / "work", {}, RuntimeLimits())

def test_trusted_native_argv_and_redacted_output(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-must-not-be-in-logs")
    result = NativeBackend(tmp_path).run(
        [sys.executable, "-c", "import os; print(os.environ['OPENAI_API_KEY'])"],
        tmp_path / "trusted", {}, RuntimeLimits(), trusted=True)
    assert result.status == "succeeded"
    assert "secret-must-not" not in result.stdout
    assert "[REDACTED]" in result.stdout
    assert "secret-must-not" not in next((tmp_path / "trusted").glob("*/stdout.txt")).read_text()

def test_T15_native_timeout_cleans_process(tmp_path):
    result = NativeBackend(tmp_path).run(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        tmp_path / "timeout", {}, RuntimeLimits(timeout_seconds=.3), trusted=True)
    assert result.status == "failed" and result.reason == "timeout"
    assert result.duration_seconds < 5

def test_native_output_budget_marks_failure(tmp_path):
    result = NativeBackend(tmp_path).run(
        [sys.executable, "-c", "print('x' * 100000)"],
        tmp_path / "output", {}, RuntimeLimits(max_output_bytes=100), trusted=True)
    assert result.reason == "output_limit"
    assert len(result.stdout.encode()) <= 100

def test_docker_requires_digest_not_latest(tmp_path):
    with pytest.raises(ConfigurationError, match="digest"):
        DockerBackend(tmp_path).preflight("rdagent-qlib:latest")

def test_T14_docker_mounts_only_research_without_secrets(tmp_path, source_spec):
    snapshot = build_snapshot(source_spec.source, source_spec,
                              UniversePolicy(minimum_listing_days=0, liquidity_lookback=1))
    backend = DockerBackend(tmp_path / "workspaces")
    limits = RuntimeLimits(image="etf-worker@sha256:" + "a" * 64)
    argv = backend.command(["python", "worker.py"], tmp_path / "workspaces/job",
                           snapshot.path / "research", limits, container_name="etf-test")
    text = " ".join(argv)
    assert "--network none" in text and "--cap-drop ALL" in text
    assert "/research,readonly" in text
    assert "holdout" not in text and ".env" not in text and str(source_spec.source) not in text
    with pytest.raises(ConfigurationError):
        backend.command(["python", "worker.py"], tmp_path / "workspaces/job",
                        snapshot.path / "holdout", limits, container_name="etf-test")


def test_native_cpu_affinity_is_inherited_by_children(tmp_path):
    import json
    script = ("import psutil,subprocess,sys,json; "
              "parent=psutil.Process().cpu_affinity(); "
              "child=json.loads(subprocess.check_output([sys.executable,'-c',"
              "'import psutil,json; print(json.dumps(psutil.Process().cpu_affinity()))'],text=True)); "
              "print(json.dumps([parent,child]))")
    result = NativeBackend(tmp_path).run([sys.executable, "-c", script],
        tmp_path / "cpu", {}, RuntimeLimits(cpu_count=1), trusted=True)
    assert result.status == "succeeded", result
    parent, child = json.loads(result.stdout)
    assert len(parent) == 1 and parent == child
