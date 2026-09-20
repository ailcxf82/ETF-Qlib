import os
import sys

from etf_ml.contracts import RuntimeLimits
from etf_ml.research.provider_config import PROVIDER_KEYS, provider_environment
from etf_ml.runtime.native import NativeBackend


def test_explicit_dotenv_allowlist_and_environment_precedence(tmp_path, monkeypatch):
    for key in PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / ".env"
    path.write_text('CHAT_MODEL="openai/local-model"\nOPENAI_API_KEY=private-value\nUNTRUSTED_OPTION=danger\n')
    monkeypatch.setenv("CHAT_MODEL", "openai/environment-model")
    result = provider_environment(path)
    assert result == {"CHAT_MODEL": "openai/environment-model", "OPENAI_API_KEY": "private-value"}
    assert "OPENAI_API_KEY" not in os.environ


def test_missing_dotenv_does_not_search_generated_directories(tmp_path, monkeypatch):
    for key in PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert provider_environment(tmp_path / "absent") == {}


def test_child_only_credentials_are_redacted_in_results_and_persisted_logs(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = NativeBackend(tmp_path).run(
        [sys.executable, "-c", "import os,sys; print(os.environ['OPENAI_API_KEY']); print(os.environ['OPENAI_API_KEY'], file=sys.stderr)"],
        tmp_path / "job", {"OPENAI_API_KEY": "private-child-only-secret"}, RuntimeLimits(), trusted=True)
    assert result.status == "succeeded"
    assert "private-child-only-secret" not in result.stdout + result.stderr
    assert "[REDACTED]" in result.stdout
    for path in tmp_path.rglob("*.txt"):
        assert "private-child-only-secret" not in path.read_text()
