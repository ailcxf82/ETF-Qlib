import importlib.util
import json
import sys
import types
from pathlib import Path

import pandas as pd
import pytest


def setup_collector(tmp_path, monkeypatch, response=None):
    spec = importlib.util.spec_from_file_location("collector", Path(__file__).resolve().parents[2] / "scripts/fetch_etf_supplements.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    instruments = tmp_path / "all.txt"
    instruments.write_text("510300.SH\t2020-01-01\t2025-12-31\n")
    monkeypatch.setenv("TUSHARE_TOKEN", "private-test-token")
    monkeypatch.setattr(sys, "argv", ["collector", "--instruments", str(instruments),
                                     "--start", "20200101", "--end", "20251231", "--interval-seconds", "0",
                                     "--output-root", str(tmp_path / "output")])
    calls = []

    def fetch(**kwargs):
        calls.append(kwargs)
        if isinstance(response, Exception):
            raise response
        return response if response is not None else pd.DataFrame({"ts_code": ["510300.SH"], "adj_factor": [1.]})

    fake = types.SimpleNamespace(pro_api=lambda *args, **kwargs: types.SimpleNamespace(fund_adj=fetch, fund_div=fetch))
    monkeypatch.setitem(sys.modules, "tushare", fake)
    return module, calls


def test_collection_reuses_immutable_cache_and_detects_tampering(tmp_path, monkeypatch, capsys):
    module, calls = setup_collector(tmp_path, monkeypatch)
    assert module.main() == 0
    assert len(calls) == 2
    assert module.main() == 0
    assert len(calls) == 2
    assert "private-test-token" not in capsys.readouterr().out
    next((tmp_path / "output").rglob("raw.parquet")).write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        module.main()
    assert len(calls) == 2


def test_request_limit_preserves_resume_state(tmp_path, monkeypatch, capsys):
    module, calls = setup_collector(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", sys.argv + ["--max-calls", "1"])
    assert module.main() == 0
    assert len(calls) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "partial"
    assert module.main() == 0
    assert len(calls) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "collected"


def test_provider_failure_never_logs_credential_or_false_completion(tmp_path, monkeypatch, capsys):
    module, _ = setup_collector(tmp_path, monkeypatch, RuntimeError("private-test-token"))
    assert module.main() == 5
    output = capsys.readouterr().out
    assert "private-test-token" not in output
    assert json.loads(output)["status"] == "failed"
    assert not list((tmp_path / "output").rglob("manifest.json"))


def test_possible_truncation_not_published(tmp_path, monkeypatch, capsys):
    raw = pd.DataFrame({"ts_code": ["510300.SH"] * 2000})
    module, _ = setup_collector(tmp_path, monkeypatch, raw)
    assert module.main() == 5
    assert not list((tmp_path / "output").rglob("manifest.json"))


@pytest.mark.parametrize("workers", [2, 4])
def test_parallel_limit_cache_resume_and_secret_free_reporting(tmp_path, monkeypatch, capsys, workers):
    module, calls = setup_collector(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", sys.argv + ["--workers", str(workers), "--max-calls", "1"])
    assert module.main() == 0
    assert len(calls) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "partial" and report["calls"] == 1
    assert module.main() == 0
    assert len(calls) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "collected" and report["completed_instruments"] == 1
    assert module.main() == 0
    assert len(calls) == 2
    assert json.loads(capsys.readouterr().out)["calls"] == 0


def test_parallel_provider_failure_stops_pending_work_without_false_completion(tmp_path, monkeypatch, capsys):
    module, calls = setup_collector(tmp_path, monkeypatch, RuntimeError("private-test-token"))
    monkeypatch.setattr(sys, "argv", sys.argv + ["--workers", "4"])
    assert module.main() == 5
    output = capsys.readouterr().out
    assert "private-test-token" not in output
    report = json.loads(output)
    assert report["status"] == "failed" and report["completed_instruments"] == 0
    assert report["calls"] == len(calls)
    assert not list((tmp_path / "output").rglob("manifest.json"))
