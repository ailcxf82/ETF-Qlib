from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from etf_ml.contracts import ResearchPolicy
from etf_ml.errors import BudgetError, ConfigurationError, IntegrityError, QualityError
from etf_ml.research.budget import BudgetLedger
from etf_ml.research.prompting import TOKENIZER_VERSION, estimate_tokens, input_limit, output_limit
from etf_ml.runtime.native import NativeBackend
from etf_ml.utils import FileLock, atomic_json, content_hash, ensure_within, file_hash, redact


def _finite_number(value):
    return value if type(value) is int and value >= 0 else None


def usage_record(*, prompt: dict, reply: "CompletionReply", stage: str) -> dict:
    """Normalize observable usage without converting unknowns to zero."""
    raw = dict(reply.usage or {})
    def first(*names):
        for name in names:
            value = _finite_number(raw.get(name))
            if value is not None:
                return value
        return None
    provider_input = first("input_tokens", "prompt_tokens")
    provider_output = first("output_tokens", "completion_tokens")
    provider_cached = first("cached_tokens", "cache_read_input_tokens")
    provider_reasoning = first("reasoning_tokens")
    provider_known = provider_input is not None or provider_output is not None
    raw_attempts = raw.get("_attempt_records")
    if not isinstance(raw_attempts, list) or not raw_attempts:
        # Older transports sometimes exposed only the final reply plus a
        # physical-attempt count.  Preserve the unknown earlier attempts
        # instead of assigning the final usage to every retry.
        count = raw.get("_transport_attempts", 1)
        count = int(count) if isinstance(count, int) and count > 0 else 1
        raw_attempts = ([{"status": "unknown", "usage": {}}] * (count - 1) +
                        [{"status": "succeeded", "usage": raw}])
    attempts = []
    for attempt in raw_attempts:
        data = dict(attempt.get("usage", {})) if isinstance(attempt, dict) else {}
        attempts.append({"status": attempt.get("status", "unknown") if isinstance(attempt, dict) else "unknown",
                         **{key: data.get(key) for key in ("provider_request_id", "provider_response_id",
                            "provider_model", "requested_model", "usage_capture_status", "receipt_sha256")},
                         "provider_input_tokens": _finite_number(data.get("input_tokens", data.get("prompt_tokens"))),
                         "provider_output_tokens": _finite_number(data.get("output_tokens", data.get("completion_tokens"))),
                         "cached_tokens": _finite_number(data.get("cached_tokens")),
                         "reasoning_tokens": _finite_number(data.get("reasoning_tokens"))})
    return {
        "schema_version": "usage-record-v1", "stage": stage, "provider": reply.provider,
        "prompt_hash": content_hash(prompt), "response_hash": content_hash(reply.text),
        "input_chars": len((prompt["system_prompt"] + prompt["user_prompt"]).encode("utf-8")),
        "output_chars": len(reply.text.encode("utf-8")),
        "estimated_input_tokens": estimate_tokens(prompt["system_prompt"] + prompt["user_prompt"]),
        "estimated_output_tokens": estimate_tokens(reply.text),
        "tokenizer_version": TOKENIZER_VERSION,
        "provider_input_tokens": provider_input, "provider_output_tokens": provider_output,
        "cached_tokens": provider_cached, "reasoning_tokens": provider_reasoning,
        "usage_source": "provider" if provider_known else "tokenizer_estimate",
        "transport_attempts": len(attempts), "attempts": attempts,
        "actual_cost": reply.actual_cost,
        **{key: raw.get(key) for key in ("provider_request_id", "provider_response_id", "provider_model",
                                        "requested_model", "usage_capture_status", "receipt_sha256")},
    }


@dataclass(frozen=True)
class CompletionReply:
    text: str
    actual_cost: str | None
    usage: dict
    provider: str


class RDAgentTransport:
    """The locked RDAgent APIBackend executes in a bounded trusted subprocess.

    RDAgent's text-only API does not return verified billed cost. Thus this
    transport explicitly reports unknown cost and supports unlimited policy;
    it cannot claim to enforce a paid monetary cap.
    """
    paid = True
    maximum_cost = None
    identity = "rdagent-0.8.0-api-backend"
    dispatch_policy = {"id": "rdagent-single-dispatch-usage-v2", "max_physical_attempts": 1,
                       "automatic_ambiguous_retry": False, "max_provider_requests": 1,
                       "automatic_continuation": False, "provider_receipt_schema": "provider-response-receipt-v1"}

    def __init__(self, root: Path, policy: ResearchPolicy):
        self.root, self.policy = Path(root).resolve(), policy
        self.backend = NativeBackend(self.root)
        from etf_ml.research.provider_config import provider_environment
        self.provider_env = provider_environment()
        self.identity = "rdagent-0.8.0-api-backend-" + content_hash({
            "environment": {key: value for key, value in self.provider_env.items() if "KEY" not in key},
            "dispatch_policy": self.dispatch_policy})

    def _uncertain_usage(self, prompt, attempts):
        raw = {**(attempts[-1].get("usage") or {}), "_attempt_records": attempts}
        result = usage_record(prompt=prompt, reply=CompletionReply("", None, raw, self.identity),
                              stage=prompt.get("stage"))
        result.update(estimated_output_tokens=None, output_chars=None, response_hash=None)
        if result["provider_input_tokens"] is None and result["provider_output_tokens"] is None:
            result["usage_source"] = "provider_response_unavailable"
        return result

    def _uncertain_error(self, prompt, workspace, attempts, reason):
        attempts[-1].update(status="unknown", reason=str(reason), outcome="uncertain")
        receipt_path = workspace / "attempt-1" / "provider_receipt.json"
        if receipt_path.is_file():
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                if (isinstance(receipt, dict) and receipt.get("schema_version") == "provider-response-receipt-v1" and
                        receipt.get("prompt_hash") == content_hash(redact(prompt)) and receipt.get("physical_requests") == 1 and
                        receipt.get("status") == "response_received" and isinstance(receipt.get("usage"), dict)):
                    attempts[-1]["usage"] = {**receipt["usage"], "receipt_sha256": file_hash(receipt_path)}
            except (OSError, ValueError):
                pass  # An unreadable receipt is unknown, never authority to redispatch.
        atomic_json(workspace / "attempts.json", {"dispatch_policy": self.dispatch_policy,
                                                    "attempts": attempts})
        error = QualityError("RDAgent dispatch outcome is uncertain; do not retry without reconciliation: " + str(reason))
        error.usage = self._uncertain_usage(prompt, attempts)
        raise error

    def complete(self, prompt: dict) -> CompletionReply:
        workspace = ensure_within(self.root / content_hash(prompt)[:24], self.root)
        workspace.mkdir(parents=True, exist_ok=True)
        atomic_json(workspace / "request.json", prompt)
        attempt_root = ensure_within(workspace / "attempt-1", workspace)
        attempt_root.mkdir(parents=True, exist_ok=True)
        request_path = attempt_root / "request.json"
        atomic_json(request_path, prompt)
        attempts = [{"attempt": 1, "status": "dispatching", "usage": {}}]
        # Commit the dispatch intent before entering the provider-capable subprocess.
        atomic_json(workspace / "attempts.json", {"dispatch_policy": self.dispatch_policy,
                                                    "attempts": attempts})
        try:
            result = self.backend.run(
                [sys.executable, "-m", "etf_ml.research.llm_worker", str(request_path.resolve())],
                attempt_root, self.provider_env, self.policy.limits, trusted=True)
        except BaseException as exc:
            attempts[0].update(status="unknown", reason=type(exc).__name__, outcome="uncertain")
            self._uncertain_error(prompt, workspace, attempts, type(exc).__name__)
        atomic_json(attempt_root / "execution.json", result)
        response_path = attempt_root / "response.json"
        if result.status != "succeeded" or not response_path.is_file():
            attempts[0].update(status="unknown", execution_status=result.status,
                               reason=result.reason or "response_missing", outcome="uncertain")
            self._uncertain_error(prompt, workspace, attempts, result.reason or "response_missing")
        try:
            reply = CompletionReply(**json.loads(response_path.read_text(encoding="utf-8")))
        except BaseException as exc:
            attempts[0].update(status="unknown", execution_status=result.status,
                               reason=type(exc).__name__, outcome="uncertain")
            self._uncertain_error(prompt, workspace, attempts, type(exc).__name__)
        usage = dict(reply.usage or {})
        receipt_path = attempt_root / "provider_receipt.json"
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._uncertain_error(prompt, workspace, attempts, "provider_receipt_unavailable")
        provider_usage = {key: value for key, value in reply.usage.items() if key != "worker_max_retry"}
        if (not isinstance(receipt, dict) or receipt.get("schema_version") != "provider-response-receipt-v1" or
                receipt.get("prompt_hash") != content_hash(redact(prompt)) or receipt.get("usage") != provider_usage or
                receipt.get("status") != "response_received" or receipt.get("physical_requests") != 1):
            self._uncertain_error(prompt, workspace, attempts, "provider_receipt_mismatch")
        usage["receipt_sha256"] = file_hash(receipt_path)
        usage["_transport_attempts"] = 1
        attempts[0].update(status="succeeded", duration_seconds=result.duration_seconds,
                            usage={key: value for key, value in usage.items() if not key.startswith("_")})
        atomic_json(workspace / "attempts.json", {"dispatch_policy": self.dispatch_policy,
                                                    "attempts": attempts})
        usage["_attempt_records"] = attempts
        return replace(reply, usage=usage)


class ReplayTransport:
    """Deterministic provider for integration, debugging and zero-cost research."""
    paid = False
    local_replay = True
    maximum_cost = "0"
    dispatch_policy = {"id": "replay-single-call-v1", "max_physical_attempts": 1,
                       "automatic_ambiguous_retry": False}

    def __init__(self, responses: dict[str, str]):
        self.responses = dict(responses)
        self.identity = "replay-" + content_hash(responses)

    def preflight(self, prompt: dict) -> None:
        """Reject an unavailable local replay before creating a billing reservation."""
        if prompt["stage"] not in self.responses:
            raise ConfigurationError("Replay has no response for stage " + prompt["stage"])

    def complete(self, prompt: dict) -> CompletionReply:
        self.preflight(prompt)
        return CompletionReply(self.responses[prompt["stage"]], "0", {
            "_transport_attempts": 1, "_attempt_records": [{"status": "succeeded", "usage": {}}]}, self.identity)


class GuardedLLM:
    def __init__(self, root: Path, policy: ResearchPolicy, transport=None):
        self.root, self.policy = Path(root).resolve(), policy.model_copy(deep=True)
        self.transport = transport or RDAgentTransport(self.root / "transport", self.policy)
        self.ledger = BudgetLedger(self.root / "billing.json", self.policy)
        self.dispatch_scope = None
        self.dispatch_guard = None
        self.root.mkdir(parents=True, exist_ok=True)
        dispatch_policy = {"schema_version": "llm-dispatch-policy-v1",
            "transport_class": type(self.transport).__module__ + "." + type(self.transport).__qualname__,
            "transport_paid": self.transport.paid,
            "maximum_cost": self.transport.maximum_cost,
            "physical_dispatch": getattr(self.transport, "dispatch_policy", {
                "id": "transport-policy-unreported", "max_physical_attempts": None,
                "automatic_ambiguous_retry": None}),
            "max_dispatches_per_trial": self.policy.max_dispatches_per_trial,
            "max_repairs_per_trial": self.policy.max_repairs_per_trial,
            "max_campaign_wall_seconds": self.policy.max_campaign_wall_seconds,
            "timeout_seconds_per_dispatch": self.policy.limits.timeout_seconds,
            "max_output_bytes": self.policy.limits.max_output_bytes}
        self.dispatch_policy = dispatch_policy
        policy_path = self.root / "dispatch_policy.json"
        if policy_path.exists() and json.loads(policy_path.read_text(encoding="utf-8")) != dispatch_policy:
            raise ConfigurationError("LLM dispatch policy is immutable for this run")

    def _freeze_dispatch_policy(self) -> None:
        """Persist policy at first real dispatch, not for an unused session."""
        if getattr(self.transport, "local_replay", False):
            return
        policy_path = self.root / "dispatch_policy.json"
        if policy_path.exists():
            if json.loads(policy_path.read_text(encoding="utf-8")) != self.dispatch_policy:
                raise ConfigurationError("LLM dispatch policy is immutable for this run")
        else:
            atomic_json(policy_path, self.dispatch_policy)

    @staticmethod
    def _reply(path: Path, request_hash: str) -> CompletionReply:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if (envelope["request_hash"] != request_hash or
                content_hash(envelope["reply"]) != envelope["reply_hash"]):
            raise IntegrityError("External response envelope integrity failed")
        return CompletionReply(**envelope["reply"])

    def complete(self, *, stage: str, user_prompt: str, system_prompt: str,
                 request_identity: dict) -> str:
        self._freeze_dispatch_policy()
        prompt = {"stage": stage, "user_prompt": user_prompt,
                  "system_prompt": system_prompt, "json_mode": True,
                  "requested_max_output_tokens": output_limit(stage),
                  "generation_limit_status": "backend_unsupported_not_enforced"}
        if estimate_tokens(system_prompt + user_prompt) > input_limit(stage):
            raise BudgetError("context_budget_exceeded for " + stage)
        request = {"prompt": prompt, "identity": request_identity,
                   "transport": self.transport.identity,
                   "dispatch_scope": self.dispatch_scope}
        key = content_hash(request)
        call_id = "call-" + key[:32]
        workspace = ensure_within(self.root / "calls" / key[:32], self.root)
        with FileLock(self.root / ".locks" / (key + ".lock")):
            response, manifest_path = workspace / "response.json", workspace / "manifest.json"
            existing = self.ledger.summary()["calls"].get(call_id)
            if existing is not None and existing["status"] == "overrun":
                raise BudgetError("Reserved billing bound was exceeded; do not reuse this result")
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if (manifest["request_hash"] != key or file_hash(response) != manifest["response_hash"] or
                        existing is None or existing["response_hash"] != manifest["response_hash"]):
                    raise IntegrityError("External response cache or billing commit changed")
                atomic_json(workspace / "cache_hit.json", {"request_hash": key, "kind": "application_cache"})
                return self._reply(response, key).text
            if existing is not None:
                # An atomic response envelope can be committed after interruption.
                # A request without a response must never be blindly redispatched.
                if response.exists():
                    reply = self._reply(response, key)
                    self.ledger.complete(call_id, file_hash(response), actual_cost=reply.actual_cost,
                                         usage=usage_record(prompt=prompt, reply=reply, stage=stage))
                    atomic_json(manifest_path, {"request_hash": key, "response_hash": file_hash(response)})
                    return reply.text
                raise BudgetError("External call already dispatched; reconcile or recover its result")
            if self.dispatch_guard is not None:
                self.dispatch_guard()
            preflight = getattr(self.transport, "preflight", None)
            if preflight is not None:
                preflight(prompt)
            self.ledger.reserve(call_id, key, paid=self.transport.paid,
                                maximum_cost=self.transport.maximum_cost,
                                dispatch_scope=self.dispatch_scope,
                                max_dispatches=(self.policy.max_dispatches_per_trial
                                                if self.dispatch_scope is not None else None))
            workspace.mkdir(parents=True, exist_ok=True)
            atomic_json(workspace / "request.json", request)
            reply = None
            try:
                reply = self.transport.complete(prompt)
                if len(reply.text.encode("utf-8")) > self.policy.limits.max_output_bytes:
                    raise QualityError("External response exceeds output limit")
                raw_reply = redact(asdict(reply))
                atomic_json(response, {"request_hash": key, "reply": raw_reply,
                                       "reply_hash": content_hash(raw_reply)})
                self.ledger.complete(call_id, file_hash(response), actual_cost=reply.actual_cost,
                                     usage=usage_record(prompt=prompt, reply=reply, stage=stage))
                atomic_json(manifest_path, {"request_hash": key, "response_hash": file_hash(response)})
            except BaseException as exc:
                usage = getattr(exc, "usage", None)
                if usage is None and reply is not None:
                    usage = usage_record(prompt=prompt, reply=reply, stage=stage)
                self.ledger.uncertain(call_id, reason=type(exc).__name__,
                                      usage=usage)
                raise
            return self._reply(response, key).text

    def usage_summary(self) -> dict:
        """Report local estimates and verified provider data separately."""
        calls = self.ledger.summary()["calls"]
        rows = [call.get("usage", {}) for call in calls.values()]
        attempts = [attempt for row in rows for attempt in row.get("attempts", [])]
        # Usage ledgers written before attempt records existed remain readable
        # but explicitly unknown, rather than disappearing from the denominator.
        attempts.extend({"status": "unknown"} for row in rows if not row.get("attempts"))
        physical = len(attempts)
        provider_complete = sum(attempt.get("provider_input_tokens") is not None and
                                attempt.get("provider_output_tokens") is not None for attempt in attempts)
        def total(key):
            values = [attempt.get(key) for attempt in attempts]
            known = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
            return sum(known) if known else None
        def logical_total(key):
            values = [row.get(key) for row in rows]
            known = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
            return sum(known) if known else None
        cache_hits = len(list((self.root / "calls").glob("*/cache_hit.json")))
        return {"schema_version": "usage-summary-v1", "logical_calls": len(rows),
                "transport_attempts": physical, "repairs": sum("repair" in str(row.get("stage")) for row in rows),
                "application_cache_hits": cache_hits,
                "estimated_input_tokens": logical_total("estimated_input_tokens"),
                "estimated_output_tokens": logical_total("estimated_output_tokens"),
                "provider_input_tokens": total("provider_input_tokens"),
                "provider_output_tokens": total("provider_output_tokens"),
                "cached_tokens": total("cached_tokens"), "reasoning_tokens": total("reasoning_tokens"),
                "usage_coverage": provider_complete / physical if physical else None,
                "unknown_attempts": physical - provider_complete,
                "tokenizer_version": TOKENIZER_VERSION,
                "cost": self.ledger.summary()}
