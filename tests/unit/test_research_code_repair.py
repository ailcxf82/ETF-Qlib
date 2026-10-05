import json
from types import SimpleNamespace

from etf_ml.errors import ValidationFailure
from etf_ml.research.context import FactorSpec
from etf_ml.research.session import ResearchSession


def test_recoverable_runtime_validation_triggers_one_code_repair(monkeypatch):
    import etf_ml.adapters.rdagent.coder as module
    from test_llm_factor_v1 import context, proposal

    calls = []

    class Experiment:
        def __init__(self):
            self.session = session
            self.sub_tasks = [task]
            self.sub_workspace_list = [workspace]

    class Workspace:
        def __init__(self):
            self.context = context()
            self.executions = 0

        def inject_files(self, **files):
            assert "factor.py" in files

        def execute(self):
            self.executions += 1
            if self.executions == 1:
                raise ValidationFailure(category="index_shape", check_id="output_index",
                                        message="wrong index", recoverable=True)

    class LLM:
        def complete(self, **kwargs):
            calls.append(kwargs["stage"])
            return json.dumps({"source": "def compute(panel):\n    return panel['adj_close'].to_frame('factor')\n"})

    session = SimpleNamespace(protocol=SimpleNamespace(protocol_id="protocol"), llm=LLM(),
                              claim_repair=lambda stage: stage == "code")
    task = SimpleNamespace(name="short_trend", version=1,
                           proposal=FactorSpec.model_validate(proposal()), failure=None)
    workspace = Workspace()
    monkeypatch.setattr(module, "ETFExperiment", Experiment)
    scenario = SimpleNamespace(session=session, get_runtime_environment=lambda: None)
    module.ETFFactorCoder(scenario).develop(Experiment())

    assert calls == ["code:short_trend", "code_repair:short_trend"]
    assert workspace.executions == 2
    assert task.failure is None


def test_runtime_index_failure_is_projected_as_a_safe_repair_instruction():
    from types import SimpleNamespace
    from etf_ml.research.factor_engine import _runtime_failure_message

    result = SimpleNamespace(
        stderr="ValueError: Candidate must preserve the input index",
        stdout="", reason="nonzero_exit")

    message = _runtime_failure_message(result)

    assert "incoming panel.index" in message
    assert "reindex" in message


def test_repair_quota_is_checkpointed_and_survives_recovery(tmp_path):
    checkpoint = tmp_path / "checkpoint.json"
    state = {"in_progress": {}}
    pending = state["in_progress"]
    session = ResearchSession.__new__(ResearchSession)
    session.config = SimpleNamespace(research=SimpleNamespace(max_repairs_per_trial=2))
    session.bind_repair_state(state, pending, checkpoint)
    assert session.claim_repair("code") is True

    restored = json.loads(checkpoint.read_text(encoding="utf-8"))
    recovered = ResearchSession.__new__(ResearchSession)
    recovered.config = SimpleNamespace(research=SimpleNamespace(max_repairs_per_trial=2))
    recovered.bind_repair_state(restored, restored["in_progress"], checkpoint)
    assert recovered.claim_repair("code") is False
    assert recovered.claim_repair("proposal") is True
    assert recovered.claim_repair("hypothesis") is False


def test_repair_limit_is_configured_and_zero_disables_repair(tmp_path):
    session = ResearchSession.__new__(ResearchSession)
    session.config = SimpleNamespace(research=SimpleNamespace(max_repairs_per_trial=0))
    session.bind_repair_state({"in_progress": {}}, {"repair_state": {}}, tmp_path / "checkpoint.json")
    assert session.claim_repair("hypothesis") is False
    assert session._repair_total == 0


def test_coder_prompt_forbids_forbidden_data_terms_in_source_text():
    from etf_ml.adapters.rdagent.coder import CODE_SYSTEM

    assert "do not emit the words label or holdout anywhere in the generated source" in CODE_SYSTEM
