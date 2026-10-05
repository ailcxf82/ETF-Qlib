"""Trusted external-call worker. Never loaded in generated-factor containers."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import datetime, timezone

from etf_ml.errors import ConfigurationError, IntegrityError
from etf_ml.utils import atomic_json, content_hash


def provider_response_usage(response, *, requested_model):
    """Whitelist response evidence; never serialize SDK settings, headers or estimated cost."""
    def field(value, name):
        return value.get(name) if isinstance(value, dict) else getattr(value, name, None)

    def count(value):
        return value if type(value) is int and value >= 0 else None

    def identifier(value):
        return value if isinstance(value, str) and 0 < len(value) <= 256 else None

    usage = field(response, "usage")
    incoming, outgoing = field(usage, "prompt_tokens"), field(usage, "completion_tokens")
    result = {
        "provider_response_id": identifier(field(response, "id")),
        "provider_request_id": identifier(field(response, "request_id")),
        "provider_model": identifier(field(response, "model")),
        "requested_model": identifier(requested_model),
        "input_tokens": count(incoming), "output_tokens": count(outgoing),
        "cached_tokens": count(field(field(usage, "prompt_tokens_details"), "cached_tokens")),
        "reasoning_tokens": count(field(field(usage, "completion_tokens_details"), "reasoning_tokens")),
        "actual_cost": None, "billing_source": "unavailable",
        "usage_source": "provider_response",
    }
    # Inconsistent optional details cannot be treated as measured usage.
    for detail, total in (("cached_tokens", "input_tokens"), ("reasoning_tokens", "output_tokens")):
        if result[detail] is not None and result[total] is not None and result[detail] > result[total]:
            result[detail] = None
    result["usage_capture_status"] = ("complete" if all(result[k] is not None for k in
        ("input_tokens", "output_tokens")) else "partial" if any(result[k] is not None for k in
        ("input_tokens", "output_tokens")) else "unavailable")
    return result


def complete_with_receipt(backend, module, request, receipt_path):
    """Child-local hook into the pinned backend, preserving its message/JSON handling."""
    original = module.completion
    called, captured = False, None

    def observed_completion(*args, **kwargs):
        nonlocal called, captured
        if called:
            raise ConfigurationError("One provider request per dispatch; automatic continuation prohibited")
        called = True
        kwargs.update(stream=False, max_retries=0, num_retries=0)
        receipt = {"schema_version": "provider-response-receipt-v1", "prompt_hash": content_hash(request),
                   "status": "dispatch_started", "observed_at": datetime.now(timezone.utc).isoformat(),
                   "physical_requests": 1, "usage": {}}
        atomic_json(receipt_path, receipt)  # Fail before dispatch if intent cannot be persisted.

        def observe_raw_response(event):
            nonlocal captured
            if not isinstance(event, dict) or event.get("log_event_type") != "post_api_call":
                return
            raw = event.get("original_response")
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except ValueError:
                    return
            if not isinstance(raw, dict):
                return
            captured = provider_response_usage(raw, requested_model=kwargs.get("model"))
            atomic_json(receipt_path, {**receipt, "status": "response_received", "usage": captured})

        # The SDK's normalized Usage can fill missing fields with zero. Capture
        # the raw response event before that transformation; never persist the event itself.
        kwargs["logger_fn"] = observe_raw_response
        response = original(*args, **kwargs)
        if captured is None:
            captured = provider_response_usage({}, requested_model=kwargs.get("model"))
            captured["usage_source"] = "raw_provider_response_unavailable"
        atomic_json(receipt_path, {**receipt, "status": "response_received", "usage": captured})
        return response

    module.completion = observed_completion
    try:
        text = backend.build_messages_and_create_chat_completion(
            user_prompt=request["user_prompt"], system_prompt=request["system_prompt"],
            json_mode=True, json_target_type=dict)
        if captured is None:
            raise IntegrityError("Backend returned text without an observed provider response")
        return text, captured
    finally:
        module.completion = original


class _DiscardStorage:
    def log(self, *args, **kwargs) -> None:
        """Drop RDAgent object logs that can contain provider configuration."""


def disable_rdagent_disk_logging(rdagent_logger) -> None:
    """Keep RDAgent's tag API while preventing serialized credential settings."""
    rdagent_logger.storage = _DiscardStorage()
    rdagent_logger.other_storages = []


def main():
    request_path = Path(sys.argv[1]).resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    # RDAgent's LiteLLM backend logs its full configuration at INFO level.  That
    # configuration may contain provider credentials, while the transport's
    # request/response hashes already provide the required audit trail.
    from loguru import logger
    # This worker's response envelope is the audit record.  RDAgent's own
    # object storage serializes LITELLM_SETTINGS, including credentials.
    logger.remove()
    from rdagent.log import rdagent_logger
    disable_rdagent_disk_logging(rdagent_logger)
    from rdagent.oai.llm_conf import LLM_SETTINGS
    # This setting belongs to this child process; no host singleton is mutated.
    # One child invocation equals one auditable physical provider attempt.
    # Retry policy is centralized in RDAgentTransport.
    LLM_SETTINGS.max_retry = 1
    LLM_SETTINGS.log_llm_chat_content = False
    from rdagent.oai.llm_utils import APIBackend
    from rdagent.oai.backend import litellm as backend_module
    backend_module.LITELLM_SETTINGS.chat_stream = False
    backend_module.LITELLM_SETTINGS.log_llm_chat_content = False
    backend = APIBackend(use_chat_cache=False, dump_chat_cache=False,
                         use_embedding_cache=False, dump_embedding_cache=False)
    if type(backend) is not backend_module.LiteLLMAPIBackend:
        raise ConfigurationError("Provider receipt capture supports only the pinned LiteLLM backend")
    text, usage = complete_with_receipt(backend, backend_module, request,
                                      request_path.parent / "provider_receipt.json")
    atomic_json(request_path.parent / "response.json", {
        "text": text, "actual_cost": None, "usage": {**usage, "worker_max_retry": 1},
        "provider": "rdagent-0.8.0-litellm-response-usage-cost-unknown"})


if __name__ == "__main__":
    main()

