import json
import pytest

from etf_ml.cli import main

def test_audit_cli_has_clear_quality_exit_and_artifacts(tmp_path, capsys):
    config = tmp_path / "invalid.yaml"
    config.write_text("unknown: true\n", encoding="utf-8")
    assert main(["audit-data", "--config", str(config), "--run-id", "invalid-config"]) == 2
    message = json.loads(capsys.readouterr().err)
    assert message["reason"] == "configuration_error"

def test_cli_build_does_not_guess_unverified_semantics(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text(f"artifact_root: '{tmp_path.as_posix()}/artifacts'\n", encoding="utf-8")
    assert main(["build-data", "--config", str(config), "--run-id", "unverified"]) == 2
    status = json.loads((tmp_path / "artifacts/runs/unverified/status.json").read_text())
    assert status["status"] == "failed"
