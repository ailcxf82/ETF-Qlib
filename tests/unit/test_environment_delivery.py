import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def diagnostic():
    path = Path(__file__).resolve().parents[2] / "scripts/check_etf_environment.py"
    spec = importlib.util.spec_from_file_location("environment_delivery_check", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_environment_pass_does_not_upgrade_framework_and_json_is_not_polluted(diagnostic, monkeypatch, capsys, tmp_path):
    def environment(*args):
        print("private-import-output")
        return [diagnostic.Check("runtime", "PASS", "available")]
    monkeypatch.setattr(diagnostic, "collect_environment_checks", environment)
    assert diagnostic.main(["--data-path", str(tmp_path), "--json"]) == 0
    raw = capsys.readouterr().out
    report = json.loads(raw)
    assert "private-import-output" not in raw
    assert report["environment"]["status"] == "passed"
    assert set(report["framework"]["gate_states"].values()) == {"not_assessed"}
    assert report["framework"]["g0_passed"] is False


def test_actual_config_preflight_blocks_despite_environment_pass(diagnostic, source_spec, tmp_path, monkeypatch, capsys):
    import yaml
    from etf_ml.contracts import AppConfig
    from etf_ml.utils import source_hashes
    config = AppConfig(data=source_spec)
    path = tmp_path / "framework.yaml"
    path.write_text(yaml.safe_dump(config.model_dump(mode="json")))
    before = source_hashes(source_spec.source)
    monkeypatch.setattr(diagnostic, "collect_environment_checks", lambda *a: [diagnostic.Check("runtime", "PASS", "available")])
    assert diagnostic.main(["--data-path", str(source_spec.source), "--config", str(path), "--json"]) == 5
    report = json.loads(capsys.readouterr().out)
    assert report["environment"]["status"] == "passed"
    assert report["framework"]["gate_states"]["G0"] == "blocked_preflight"
    assert report["framework"]["readiness"]["training_runs"] == report["framework"]["external_calls"] == 0
    assert all(report["framework"]["gate_states"][f"G{i}"] == "not_assessed" for i in range(1, 5))
    assert source_hashes(source_spec.source) == before


def test_invalid_config_is_separate_and_does_not_echo_values(diagnostic, tmp_path, monkeypatch, capsys):
    path = tmp_path / "bad.yaml"
    path.write_text("unknown_setting: private-config-value")
    monkeypatch.setattr(diagnostic, "collect_environment_checks", lambda *a: [diagnostic.Check("runtime", "PASS", "available")])
    assert diagnostic.main(["--config", str(path), "--json"]) == 2
    output = capsys.readouterr().out
    assert "private-config-value" not in output
    assert json.loads(output)["framework"]["status"] == "invalid_configuration"


def test_environment_failure_remains_failure_when_config_is_blocked(diagnostic, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(diagnostic, "collect_environment_checks", lambda *a: [diagnostic.Check("runtime", "FAIL", "unavailable")])
    assert diagnostic.main(["--config", str(tmp_path / "absent.yaml"), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["environment"]["status"] == "failed"


def test_adapter_presence_check_does_not_bootstrap_settings_or_change_environment(diagnostic, monkeypatch):
    monkeypatch.setattr(diagnostic, "package_version", lambda *a: "0.8.0")
    monkeypatch.setattr(diagnostic.importlib.util, "find_spec", lambda *a: SimpleNamespace(origin="module.py"))
    before_env, before_modules = dict(os.environ), set(sys.modules)
    checks = diagnostic.check_rdagent({"CHAT_MODEL", "EMBEDDING_MODEL", "OPENAI_API_KEY"})
    assert next(c for c in checks if c.name == "RD-Agent ETF adapter availability").state == "PASS"
    assert dict(os.environ) == before_env
    assert "rdagent.app.qlib_rd_loop.conf" not in (set(sys.modules) - before_modules)


@pytest.mark.parametrize("image", ["rdagent-qlib:latest", "rdagent-qlib@sha256:bad"])
def test_floating_or_invalid_image_does_not_start_docker(diagnostic, monkeypatch, tmp_path, image):
    monkeypatch.setattr(diagnostic.subprocess, "run", lambda *a, **k: pytest.fail("Docker must not start"))
    assert diagnostic.check_docker_qlib(tmp_path, image).state == "FAIL"


def test_fixed_docker_check_cannot_pull_or_network_and_source_is_readonly(diagnostic, monkeypatch, tmp_path):
    seen = []
    def run(command, **kwargs):
        seen.append(command)
        return SimpleNamespace(stdout="1769 instruments; sample dates\n")
    monkeypatch.setattr(diagnostic.subprocess, "run", run)
    assert diagnostic.check_docker_qlib(tmp_path).state == "PASS"
    command = seen[0]
    assert command[command.index("--pull") + 1] == "never"
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--mount") + 1].endswith(",readonly")
    assert diagnostic.DEFAULT_DOCKER_IMAGE in command
