from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from etf_ml.contracts import ResearchPolicy
from etf_ml.errors import BudgetError, ConfigurationError, IntegrityError, QualityError
from etf_ml.research.budget import BudgetLedger
from etf_ml.runtime.native import NativeBackend
from etf_ml.utils import FileLock, atomic_json, content_hash, ensure_within, file_hash, redact


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

    def __init__(self, root: Path, policy: ResearchPolicy):
        self.root, self.policy = Path(root).resolve(), policy
        self.backend = NativeBackend(self.root)
        from etf_ml.research.provider_config import provider_environment
        self.provider_env = provider_environment()
        self.identity = "rdagent-0.8.0-api-backend-" + content_hash({
            key: value for key, value in self.provider_env.items() if "KEY" not in key})

    def complete(self, prompt: dict) -> CompletionReply:
        workspace = ensure_within(self.root / content_hash(prompt)[:24], self.root)
        workspace.mkdir(parents=True, exist_ok=True)
        atomic_json(workspace / "request.json", prompt)
        result = self.backend.run(
            [sys.executable, "-m", "etf_ml.research.llm_worker",
             str((workspace / "request.json").resolve())],
            workspace, self.provider_env, self.policy.limits, trusted=True)
        atomic_json(workspace / "execution.json", result)
        if result.status != "succeeded":
            raise QualityError("RDAgent API subprocess failed: " + str(result.reason))
        path = workspace / "response.json"
        if not path.is_file():
            raise QualityError("RDAgent API subprocess did not produce a response")
        return CompletionReply(**json.loads(path.read_text(encoding="utf-8")))


class ReplayTransport:
    """Deterministic provider for integration, debugging and zero-cost research."""
    paid = False
    maximum_cost = "0"

    def __init__(self, responses: dict[str, str]):
        self.responses = dict(responses)
        self.identity = "replay-" + content_hash(responses)

    def complete(self, prompt: dict) -> CompletionReply:
        if prompt["stage"] not in self.responses:
            raise ConfigurationError("Replay has no response for stage " + prompt["stage"])
        return CompletionReply(self.responses[prompt["stage"]], "0", {}, self.identity)


class GuardedLLM:
    def __init__(self, root: Path, policy: ResearchPolicy, transport=None):
        self.root, self.policy = Path(root).resolve(), policy.model_copy(deep=True)
        self.transport = transport or RDAgentTransport(self.root / "transport", self.policy)
        self.ledger = BudgetLedger(self.root / "billing.json", self.policy)

    @staticmethod
    def _reply(path: Path, request_hash: str) -> CompletionReply:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if (envelope["request_hash"] != request_hash or
                content_hash(envelope["reply"]) != envelope["reply_hash"]):
            raise IntegrityError("External response envelope integrity failed")
        return CompletionReply(**envelope["reply"])

    def complete(self, *, stage: str, user_prompt: str, system_prompt: str,
                 request_identity: dict) -> str:
        prompt = {"stage": stage, "user_prompt": user_prompt,
                  "system_prompt": system_prompt, "json_mode": True}
        request = {"prompt": prompt, "identity": request_identity,
                   "transport": self.transport.identity}
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
                return self._reply(response, key).text
            if existing is not None:
                # An atomic response envelope can be committed after interruption.
                # A request without a response must never be blindly redispatched.
                if response.exists():
                    reply = self._reply(response, key)
                    self.ledger.complete(call_id, file_hash(response), actual_cost=reply.actual_cost,
                                         usage=reply.usage)
                    atomic_json(manifest_path, {"request_hash": key, "response_hash": file_hash(response)})
                    return reply.text
                raise BudgetError("External call already dispatched; reconcile or recover its result")
            self.ledger.reserve(call_id, key, paid=self.transport.paid,
                                maximum_cost=self.transport.maximum_cost)
            workspace.mkdir(parents=True, exist_ok=True)
            atomic_json(workspace / "request.json", request)
            try:
                reply = self.transport.complete(prompt)
                if len(reply.text.encode("utf-8")) > self.policy.limits.max_output_bytes:
                    raise QualityError("External response exceeds output limit")
                raw_reply = redact(asdict(reply))
                atomic_json(response, {"request_hash": key, "reply": raw_reply,
                                       "reply_hash": content_hash(raw_reply)})
                self.ledger.complete(call_id, file_hash(response), actual_cost=reply.actual_cost,
                                     usage=reply.usage)
                atomic_json(manifest_path, {"request_hash": key, "response_hash": file_hash(response)})
            except BaseException as exc:
                self.ledger.uncertain(call_id, reason=type(exc).__name__)
                raise
            return self._reply(response, key).text

