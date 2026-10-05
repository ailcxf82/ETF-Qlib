"""Installed RDAgent and LiteLLM receipt path; only the network boundary is replaced."""
import json
import sys
from types import SimpleNamespace

import pytest

from etf_ml.research.llm_worker import disable_rdagent_disk_logging, main
from etf_ml.utils import atomic_json


@pytest.mark.parametrize("case", ["valid", "missing_usage", "partial_usage", "length", "invalid_json", "network_error"])
def test_installed_rdagent_captures_receipt_and_never_continues_or_retries(tmp_path, monkeypatch, case):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    monkeypatch.setenv("OPENAI_API_KEY", "offline-fixture-not-a-credential")
    monkeypatch.setenv("CHAT_MODEL", "openai/offline-fixture")
    from loguru import logger
    logger.remove()
    from rdagent.log import rdagent_logger
    disable_rdagent_disk_logging(rdagent_logger)
    from rdagent.oai.backend import litellm as module
    from rdagent.oai.llm_conf import LLM_SETTINGS
    monkeypatch.setattr(LLM_SETTINGS, "backend", "rdagent.oai.backend.LiteLLMAPIBackend")
    monkeypatch.setattr(LLM_SETTINGS, "retry_wait_seconds", 0)
    monkeypatch.setattr(LLM_SETTINGS, "max_retry", 1)
    monkeypatch.setattr(module.LITELLM_SETTINGS, "chat_model", "openai/offline-fixture")
    monkeypatch.setattr(module.LITELLM_SETTINGS, "chat_stream", False)
    monkeypatch.setattr(module.LITELLM_SETTINGS, "chat_model_map", {})
    monkeypatch.setattr(module, "supports_response_schema", lambda **kwargs: True)
    monkeypatch.setattr(module, "token_counter", lambda **kwargs: 999)
    monkeypatch.setattr(module, "completion_cost", lambda **kwargs: 9999)
    invocations = []
    def network(**kwargs):
        invocations.append(kwargs)
        assert (tmp_path / "provider_receipt.json").is_file()
        assert kwargs["stream"] is False and kwargs["max_retries"] == kwargs["num_retries"] == 0
        if case == "network_error":
            raise TimeoutError("offline fixture")
        from litellm.litellm_core_utils.llm_response_utils.convert_dict_to_response import convert_to_model_response_object
        from litellm.litellm_core_utils.litellm_logging import Logging
        from litellm import ModelResponse
        raw = {"id": "response-test", "request_id": "request-test", "model": "actual-test",
               "usage": None if case == "missing_usage" else {"prompt_tokens": 31, "completion_tokens": 7},
               "choices": [{"message": {"role": "assistant", "content": "not json" if case == "invalid_json" else '{"factor":"test"}'},
                            "finish_reason": "length" if case == "length" else "stop"}]}
        if case == "partial_usage":
            raw["usage"] = {"prompt_tokens": 31}
        # Exercise the installed SDK's real post-call callback and Usage conversion,
        # including its completion_tokens=0 default for a missing output count.
        logging_object = SimpleNamespace(model_call_details={}, litellm_request_debug=False,
            logger_fn=kwargs["logger_fn"], dynamic_input_callbacks=[], start_time=None)
        Logging.post_call(logging_object, original_response=json.dumps(raw))
        return convert_to_model_response_object(response_object=raw, model_response_object=ModelResponse())
    monkeypatch.setattr(module, "completion", network)
    request = tmp_path / "request.json"
    atomic_json(request, {"system_prompt": "rules", "user_prompt": "test", "stage": "hypothesis"})
    monkeypatch.setattr(sys, "argv", ["llm_worker", str(request)])
    if case in {"length", "invalid_json", "network_error"}:
        with pytest.raises(RuntimeError, match="Failed to create chat completion"):
            main()
        assert not (tmp_path / "response.json").exists()
    else:
        main()
        result = json.loads((tmp_path / "response.json").read_text())
        assert result["actual_cost"] is None
        assert result["usage"]["input_tokens"] == (None if case == "missing_usage" else 31)
        assert result["usage"]["output_tokens"] == (None if case in {"missing_usage", "partial_usage"} else 7)
        assert result["usage"]["provider_request_id"] == "request-test"
    assert len(invocations) == 1 and module.completion is network
    receipt = json.loads((tmp_path / "provider_receipt.json").read_text())
    assert receipt["status"] == ("dispatch_started" if case == "network_error" else "response_received")
    if case not in {"network_error", "missing_usage"}:
        assert receipt["usage"]["input_tokens"] == 31  # Never the local estimate 999.
        assert receipt["usage"]["actual_cost"] is None  # Never the SDK price estimate 9999.
