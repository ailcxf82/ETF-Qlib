import pytest
from etf_ml.errors import QualityError
from etf_ml.research.code_checks import validate_source

@pytest.mark.parametrize("source", [
    "import os\ndef compute(panel): return panel",
    "def compute(panel): return panel['label']",
    "def compute(panel): return panel.shift(-1)",
    "def compute(panel): return panel.rolling(5, center=True).mean()",
    "def compute(panel): return eval('1')",
])
def test_forbidden_candidate_code_rejected(source):
    with pytest.raises(QualityError):
        validate_source(source)

def test_temporal_grouped_code_allowed():
    validate_source("import numpy as np\ndef compute(panel):\n    return panel.groupby(level='instrument').shift(3)")


def test_invalid_proposal_gets_one_identified_repair_request(monkeypatch):
    from types import SimpleNamespace
    from etf_ml.adapters.rdagent.proposal import ETFHypothesis2Experiment

    calls = []

    class LLM:
        def complete(self, **kwargs):
            calls.append(kwargs)
            return '{"factors":[{"factor_id":"too_many"}]}' if kwargs["stage"] == "proposal" else '{"factors":[{"factor_id":"repaired"}]}'

    session = SimpleNamespace(context=SimpleNamespace(context_hash="context"), llm=LLM())
    trace = SimpleNamespace(scen=SimpleNamespace(session=session, get_scenario_all_desc=lambda: "scenario"))
    converter = ETFHypothesis2Experiment()

    def validate(response, hypothesis, received_trace):
        assert received_trace is trace
        if "too_many" in response:
            raise QualityError("Each ETF experiment must propose one to three factors")
        return "repaired-experiment"

    monkeypatch.setattr(converter, "convert_response", validate)
    assert converter.convert(SimpleNamespace(hypothesis="causal feature"), trace) == "repaired-experiment"
    assert [call["stage"] for call in calls] == ["proposal", "proposal_repair"]
    assert calls[1]["request_identity"]["repair_attempt"] == 1
    assert "one to three factors" in calls[1]["user_prompt"]


def test_invalid_hypothesis_gets_one_identified_repair_request():
    from types import SimpleNamespace
    from etf_ml.adapters.rdagent.proposal import ETFHypothesisGen

    calls = []

    class LLM:
        def complete(self, **kwargs):
            calls.append(kwargs)
            return '{"hypothesis":["not a string"]}' if kwargs["stage"] == "hypothesis" else '{"hypothesis":"A causal gap feature may add information.","reason":"test"}'

    scen = SimpleNamespace(
        session=SimpleNamespace(context=SimpleNamespace(context_hash="context"), llm=LLM()),
        get_scenario_all_desc=lambda: "scenario",
    )
    result = ETFHypothesisGen(scen).gen(SimpleNamespace())
    assert result.hypothesis == "A causal gap feature may add information."
    assert [call["stage"] for call in calls] == ["hypothesis", "hypothesis_repair"]
    assert calls[1]["request_identity"]["repair_attempt"] == 1


def test_groupby_rolling_source_rejected_before_container_execution():
    with pytest.raises(QualityError, match="groupby rolling"):
        validate_source("def compute(panel):\n    return panel['adj_close'].groupby(level='instrument').rolling(5).mean()")


def test_groupby_transform_rolling_source_allowed():
    validate_source("def compute(panel):\n    return panel['adj_close'].groupby(level='instrument').transform(lambda x: x.rolling(5).mean())")


def test_proposal_prompt_requires_the_converter_envelope():
    from etf_ml.adapters.rdagent.proposal import PROPOSAL_SYSTEM
    assert '"factors"' in PROPOSAL_SYSTEM
    assert 'bare factor' in PROPOSAL_SYSTEM
    assert 'failure_conditions must be a JSON list' in PROPOSAL_SYSTEM
    assert 'minimum_observations must be a positive integer' in PROPOSAL_SYSTEM
    assert 'never use a numeric sign' in PROPOSAL_SYSTEM


def test_llm_worker_disables_rdagent_object_storage_without_replacing_logger():
    from types import SimpleNamespace
    from etf_ml.research.llm_worker import disable_rdagent_disk_logging

    logger = SimpleNamespace(storage=object(), other_storages=[object()], _tag="kept")
    disable_rdagent_disk_logging(logger)
    assert logger._tag == "kept"
    assert logger.other_storages == []
    assert logger.storage.log("settings", tag="LITELLM_SETTINGS") is None
