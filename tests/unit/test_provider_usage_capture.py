import json
from types import SimpleNamespace

import pytest

from etf_ml.errors import BudgetError, ConfigurationError, IntegrityError, QualityError
from etf_ml.contracts import ExecutionResult, ResearchPolicy
from etf_ml.research.llm_worker import complete_with_receipt, provider_response_usage
from etf_ml.research.llm import GuardedLLM, RDAgentTransport, usage_record, CompletionReply
from etf_ml.research.closure import accounting_disposition
from etf_ml.utils import atomic_json, content_hash, file_hash


PROMPT = {"stage": "code", "system_prompt": "rules", "user_prompt": "factor"}


def response():
    return {"id": "response-1", "request_id": "request-1", "model": "actual-model",
            "usage": {"prompt_tokens": 100, "completion_tokens": 20,
                      "prompt_tokens_details": {"cached_tokens": 30},
                      "completion_tokens_details": {"reasoning_tokens": 5}},
            "api_key": "do-not-save", "_hidden_params": {"response_cost": 123},
            "headers": {"Authorization": "do-not-save"}}


def test_capture_whitelists_actual_usage_not_sdk_cost_or_credentials():
    result = provider_response_usage(response(), requested_model="requested-model")
    assert result["input_tokens"] == 100 and result["cached_tokens"] == 30
    assert result["provider_request_id"] == "request-1"
    assert result["provider_model"] == "actual-model" and result["requested_model"] == "requested-model"
    assert result["actual_cost"] is None and result["usage_capture_status"] == "complete"
    assert "do-not-save" not in json.dumps(result) and "response_cost" not in json.dumps(result)
    obj = SimpleNamespace(**response())
    obj.usage = SimpleNamespace(**response()["usage"])
    assert provider_response_usage(obj, requested_model="requested-model") == result


@pytest.mark.parametrize("invalid", [None, True, -1, 1.5, "123", float("nan"), float("inf")])
def test_missing_or_invalid_actual_usage_is_not_replaced_by_estimates(invalid):
    record = response()
    record["usage"]["prompt_tokens"] = invalid
    result = provider_response_usage(record, requested_model="model")
    assert result["input_tokens"] is None and result["usage_capture_status"] == "partial"
    normalized = usage_record(prompt=PROMPT, reply=CompletionReply("{}", None, result, "provider"), stage="code")
    assert normalized["provider_input_tokens"] is None
    assert normalized["estimated_input_tokens"] is not None


def test_missing_usage_zero_usage_and_invalid_optional_detail():
    assert provider_response_usage({}, requested_model=None)["usage_capture_status"] == "unavailable"
    record = response()
    record["usage"].update(prompt_tokens=0, completion_tokens=0)
    result = provider_response_usage(record, requested_model="model")
    assert result["input_tokens"] == result["output_tokens"] == 0
    assert result["cached_tokens"] is None and result["reasoning_tokens"] is None


@pytest.mark.parametrize("second_call,parse_failure", [(False, False), (True, False), (False, True)])
def test_child_hook_captures_before_parser_failure_and_prohibits_continuation(tmp_path, second_call, parse_failure):
    invocations = []
    path = tmp_path / "provider_receipt.json"
    def network(**kwargs):
        assert json.loads(path.read_text())["status"] == "dispatch_started"
        invocations.append(kwargs)
        kwargs["logger_fn"]({"log_event_type": "post_api_call", "original_response": json.dumps(response())})
        return response()
    module = SimpleNamespace(completion=network)
    class Backend:
        def build_messages_and_create_chat_completion(self, **kwargs):
            module.completion(model="requested-model", stream=True, max_retries=9, num_retries=9)
            if second_call:
                module.completion(model="requested-model")
            if parse_failure:
                raise ValueError("invalid JSON")
            return "{}"
    if second_call or parse_failure:
        with pytest.raises((ConfigurationError, ValueError)):
            complete_with_receipt(Backend(), module, PROMPT, path)
    else:
        text, usage = complete_with_receipt(Backend(), module, PROMPT, path)
        assert text == "{}" and usage["input_tokens"] == 100
    assert len(invocations) == 1 and module.completion is network
    assert invocations[0]["stream"] is False
    assert invocations[0]["max_retries"] == invocations[0]["num_retries"] == 0
    receipt = json.loads(path.read_text())
    assert receipt["usage"]["provider_request_id"] == "request-1"
    assert receipt["prompt_hash"] == content_hash(PROMPT)
    assert receipt["status"] == "response_received"


def test_hidden_backend_cache_cannot_masquerade_as_provider_response(tmp_path):
    module = SimpleNamespace(completion=lambda **kwargs: pytest.fail("no call expected"))
    backend = SimpleNamespace(build_messages_and_create_chat_completion=lambda **kwargs: "{}")
    with pytest.raises(IntegrityError, match="without an observed"):
        complete_with_receipt(backend, module, PROMPT, tmp_path / "receipt.json")


@pytest.mark.parametrize("raw", [None, {"usage": {"prompt_tokens": 5}}])
def test_sdk_filled_zero_cannot_become_measured_usage(tmp_path, raw):
    def network(**kwargs):
        if raw is not None:
            kwargs["logger_fn"]({"log_event_type": "post_api_call", "original_response": raw})
        return {"usage": {"prompt_tokens": 5, "completion_tokens": 0}}
    module = SimpleNamespace(completion=network)
    class Backend:
        def build_messages_and_create_chat_completion(self, **kwargs):
            module.completion(model="test")
            return "{}"
    _, usage = complete_with_receipt(Backend(), module, PROMPT, tmp_path / "receipt.json")
    assert usage["input_tokens"] == (None if raw is None else 5)
    assert usage["output_tokens"] is None


@pytest.mark.parametrize("case", ["absent", "malformed", "wrong_prompt"])
def test_success_without_a_matching_receipt_is_uncertain_and_never_redispatched(tmp_path, case):
    class Backend:
        calls = 0
        def run(self, argv, workspace, env, limits, *, trusted):
            self.calls += 1
            usage = provider_response_usage(response(), requested_model="model")
            if case != "absent":
                atomic_json(workspace / "provider_receipt.json", [] if case == "malformed" else {
                    "schema_version": "provider-response-receipt-v1", "prompt_hash": "other",
                    "physical_requests": 1, "status": "response_received", "usage": usage})
            atomic_json(workspace / "response.json", {"text": "{}", "actual_cost": None,
                "usage": usage, "provider": "test"})
            return ExecutionResult("succeeded", 0, "", "", .1)
    transport = RDAgentTransport.__new__(RDAgentTransport)
    transport.root, transport.backend, transport.provider_env = tmp_path / "transport", Backend(), {}
    transport.policy, transport.identity = ResearchPolicy(budget_mode="unlimited"), "invalid-receipt-test"
    client = GuardedLLM(tmp_path / "llm", transport.policy, transport)
    def invoke():
        return client.complete(stage="code", user_prompt="factor", system_prompt="rules", request_identity={"trial": 1})
    with pytest.raises(QualityError): invoke()
    with pytest.raises(BudgetError): invoke()
    assert transport.backend.calls == 1
    assert client.usage_summary()["provider_input_tokens"] is None


@pytest.mark.parametrize("worker_failed", [False, True])
def test_actual_usage_reaches_guarded_billing_even_when_worker_parser_fails(tmp_path, worker_failed):
    class Backend:
        calls = 0
        def run(self, argv, workspace, env, limits, *, trusted):
            self.calls += 1
            prompt = json.loads((workspace / "request.json").read_text())
            usage = provider_response_usage(response(), requested_model="requested-model")
            atomic_json(workspace / "provider_receipt.json", {"schema_version": "provider-response-receipt-v1",
                "prompt_hash": content_hash(prompt), "status": "response_received", "physical_requests": 1,
                "usage": usage})
            if worker_failed:
                return ExecutionResult("failed", 1, "", "invalid JSON", .1, "nonzero_exit")
            atomic_json(workspace / "response.json", {"text": "{}", "actual_cost": None,
                "usage": {**usage, "worker_max_retry": 1}, "provider": "captured-provider"})
            return ExecutionResult("succeeded", 0, "", "", .1)
    transport = RDAgentTransport.__new__(RDAgentTransport)
    transport.root, transport.backend, transport.provider_env = tmp_path / "transport", Backend(), {}
    transport.policy, transport.identity = ResearchPolicy(budget_mode="unlimited"), "capture-test"
    client = GuardedLLM(tmp_path / "llm", transport.policy, transport)
    def invoke():
        return client.complete(stage="code", user_prompt="factor", system_prompt="rules", request_identity={"trial": 1})
    if worker_failed:
        with pytest.raises(QualityError): invoke()
        with pytest.raises(BudgetError): invoke()
    else:
        assert invoke() == invoke() == "{}"
    assert transport.backend.calls == 1
    summary = client.usage_summary()
    assert summary["provider_input_tokens"] == 100 and summary["provider_output_tokens"] == 20
    assert summary["usage_coverage"] == 1 and summary["cost"]["unknown_cost_calls"] == 1
    call = next(iter(summary["cost"]["calls"].values()))
    assert call["actual_cost"] is None
    assert call["usage"]["provider_request_id"] == "request-1"
    assert call["usage"]["attempts"][0]["provider_model"] == "actual-model"
    assert call["status"] == ("uncertain" if worker_failed else "cost_unknown")


def test_fee_deferral_is_scoped_and_never_changes_unknowns_or_research_gates(tmp_path):
    path = tmp_path / "policy.json"
    policy = {"schema_version": "historical-accounting-policy-v1", "campaign_id": "campaign",
        "event_chain_head": "head", "decision": "defer_historical_reconciliation",
        "unknown_cost_action": "retain_unknown", "approved_by": "user", "approved_on": "2026-09-26",
        "approval_text": "defer historical fees, preserve gates", "release_attempt_slots": False,
        "relax_research_gates": False}
    atomic_json(path, policy)
    result = accounting_disposition(path, campaign_id="campaign", event_chain_head="head")
    assert result["historical_backfill_required"] is False and result["accounting_complete"] is False
    assert result["attempt_slots_released"] == 0 and result["research_gates_unchanged"] is True
    assert result["evidence"]["sha256"] == file_hash(path)
    for key, value in [("event_chain_head", "different"), ("campaign_id", "other"),
                       ("release_attempt_slots", True), ("relax_research_gates", True),
                       ("unknown_cost_action", "assume_zero")]:
        atomic_json(path, {**policy, key: value})
        with pytest.raises(IntegrityError):
            accounting_disposition(path, campaign_id="campaign", event_chain_head="head")
