import json
from types import SimpleNamespace

from etf_ml.adapters.rdagent.proposal import ETFHypothesisGen
from etf_ml.adapters.rdagent.scenario import ETFFactorScenario
from etf_ml.errors import DataNotReady
from etf_ml.research.controller import ResearchController


class _Protocol(SimpleNamespace):
    def model_dump(self, **kwargs):
        return {"protocol_id": self.protocol_id}


def test_controller_preflights_runtime_before_any_llm_dispatch(tmp_path, monkeypatch):
    memory_root = tmp_path / "memory"
    memory_root.mkdir()
    protocol = _Protocol(protocol_id="protocol")
    baseline = SimpleNamespace(feature_set_id="baseline")
    context = SimpleNamespace(context_hash="context", fields={"adj_close": {}})
    session = SimpleNamespace(
        initial_protocol=protocol,
        protocol=protocol,
        initial_baseline=baseline,
        baseline=baseline,
        snapshot=SimpleNamespace(snapshot_id="snapshot"),
        context=context,
        config=SimpleNamespace(research=SimpleNamespace(max_trials=1)),
        root=tmp_path / "research",
        memory_root=memory_root,
        select_baseline=lambda identity: None,
        build_context=lambda feedback: context,
        save=lambda path: None,
        llm=SimpleNamespace(ledger=SimpleNamespace(summary=lambda: {"call_count": 0})),
    )
    calls = []

    def unavailable_runtime(self):
        calls.append("runtime")
        raise DataNotReady("docker daemon unavailable")

    def unexpected_llm_dispatch(*args, **kwargs):
        calls.append("llm")
        raise AssertionError("runtime preflight must prevent LLM dispatch")

    monkeypatch.setattr(ETFFactorScenario, "get_runtime_environment", unavailable_runtime)
    monkeypatch.setattr(ETFHypothesisGen, "gen", unexpected_llm_dispatch)

    state = ResearchController(session, "runtime-preflight").run()

    assert calls == ["runtime"]
    trial = json.loads((tmp_path / "research" / "sessions" / "runtime-preflight" / "trial-00000.json").read_text())
    assert trial["exception_type"] == "DataNotReady"
    assert trial["result"]["reason"] == "data_not_ready"
    assert state["trials"][0]["status"] == "failed"
