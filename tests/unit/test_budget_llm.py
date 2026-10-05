from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from etf_ml.contracts import ResearchPolicy, RuntimeLimits
from etf_ml.errors import BudgetError, ConfigurationError, IntegrityError, QualityError
from etf_ml.research.budget import BudgetLedger
from etf_ml.research.llm import GuardedLLM, CompletionReply, ReplayTransport


class Transport:
    paid = True
    maximum_cost = ".10"
    identity = "bounded-test-provider"
    def __init__(self, cost=".05", fail=False):
        self.cost, self.fail, self.calls = cost, fail, 0
    def complete(self, prompt):
        self.calls += 1
        if self.fail:
            raise RuntimeError("transport interrupted")
        return CompletionReply('{"source":"valid"}', self.cost,
                               {"input_units": 20, "output_units": 10}, self.identity)


def call(client):
    return client.complete(stage="code", system_prompt="rules", user_prompt="factor",
                           request_identity={"trial": 1})


@pytest.mark.parametrize("policy", [
    ResearchPolicy(), ResearchPolicy(budget_mode="free_only"),
    ResearchPolicy(budget_mode="capped", api_budget=0)])
def test_disallowed_policy_stops_before_external_dispatch(tmp_path, policy):
    transport = Transport()
    with pytest.raises(BudgetError):
        call(GuardedLLM(tmp_path, policy, transport))
    assert transport.calls == 0


def test_default_text_only_transport_cannot_claim_a_paid_cap(tmp_path):
    with pytest.raises(BudgetError, match="maximum billed cost"):
        call(GuardedLLM(tmp_path, ResearchPolicy(budget_mode="capped", api_budget=1)))


def test_exact_decimal_reservations_are_atomic_across_threads(tmp_path):
    ledger = BudgetLedger(tmp_path / "billing.json", ResearchPolicy(budget_mode="capped", api_budget=1))
    def reserve(i):
        try:
            ledger.reserve("call-" + str(i), str(i), paid=True, maximum_cost=".1")
            return "call-" + str(i)
        except BudgetError:
            return None
    with ThreadPoolExecutor(max_workers=10) as pool:
        successful = [item for item in pool.map(reserve, range(20)) if item is not None]
    assert len(successful) == 10
    state = ledger.summary()
    assert set(state["calls"]) == set(successful)
    assert state["outstanding_reservations"] == "1.0"
    # Thread scheduling does not guarantee that call-0 wins a reservation.
    ledger.complete(successful[0], "response", actual_cost=".03")
    assert ledger.summary()["measured_cost"] == "0.03"
    ledger.reserve("extra", "extra", paid=True, maximum_cost=".07")
    with pytest.raises(BudgetError):
        ledger.reserve("over", "over", paid=True, maximum_cost=".01")


def test_uncertain_billing_keeps_reservation_until_explicit_reconciliation(tmp_path):
    ledger = BudgetLedger(tmp_path / "billing.json", ResearchPolicy(budget_mode="capped", api_budget=.1))
    ledger.reserve("request", "hash", paid=True, maximum_cost=".1")
    ledger.uncertain("request", reason="timeout")
    with pytest.raises(BudgetError):
        ledger.reserve("next", "next", paid=True, maximum_cost=".01")
    with pytest.raises(ConfigurationError):
        ledger.reconcile("request", actual_cost=0, billing_reference="")
    ledger.reconcile("request", actual_cost=".04", billing_reference="provider-receipt-1")
    ledger.reserve("next", "next", paid=True, maximum_cost=".06")
    assert ledger.summary()["unknown_cost_calls"] == 0


def test_response_reuse_has_no_second_dispatch_and_detects_corruption(tmp_path):
    transport = Transport()
    client = GuardedLLM(tmp_path, ResearchPolicy(budget_mode="capped", api_budget=1), transport)
    first = call(client)
    assert call(client) == first and transport.calls == 1
    assert client.ledger.summary()["measured_cost"] == "0.05"
    response = next((tmp_path / "calls").glob("*/response.json"))
    response.write_text('{"forged":true}')
    with pytest.raises(IntegrityError):
        call(client)


def test_atomic_response_resumes_after_interruption_before_billing_commit(tmp_path, monkeypatch):
    transport = Transport()
    client = GuardedLLM(tmp_path, ResearchPolicy(budget_mode="capped", api_budget=1), transport)
    original = client.ledger.complete
    def fail_commit(*args, **kwargs):
        raise OSError("interrupted billing commit")
    monkeypatch.setattr(client.ledger, "complete", fail_commit)
    with pytest.raises(OSError):
        call(client)
    monkeypatch.setattr(client.ledger, "complete", original)
    assert call(client) == '{"source":"valid"}' and transport.calls == 1
    assert client.ledger.summary()["measured_cost"] == "0.05"


def test_failed_transport_is_not_blindly_retried(tmp_path):
    transport = Transport(fail=True)
    client = GuardedLLM(tmp_path, ResearchPolicy(budget_mode="capped", api_budget=1), transport)
    with pytest.raises(RuntimeError):
        call(client)
    with pytest.raises(BudgetError, match="already dispatched"):
        call(client)
    assert transport.calls == 1
    assert client.ledger.summary()["outstanding_reservations"] == "0.10"


def test_missing_local_replay_never_creates_a_billing_reservation(tmp_path):
    client = GuardedLLM(tmp_path, ResearchPolicy(budget_mode="free_only"),
                        ReplayTransport({"code": '{"source":"valid"}'}))
    with pytest.raises(ConfigurationError, match="Replay has no response"):
        client.complete(stage="proposal", system_prompt="rules", user_prompt="factor",
                        request_identity={"trial": 1})
    assert client.ledger.summary()["call_count"] == 0


def test_per_trial_dispatch_cap_is_atomic_and_counts_unknown_calls(tmp_path):
    client = GuardedLLM(tmp_path, ResearchPolicy(budget_mode="free_only",
        max_dispatches_per_trial=2), ReplayTransport({
            "proposal": "{}", "code": "{}", "hypothesis": "{}"}))
    client.dispatch_scope = "run:trial-0"
    client.complete(stage="proposal", system_prompt="rules", user_prompt="one",
                    request_identity={"trial": 0})
    client.complete(stage="code", system_prompt="rules", user_prompt="two",
                    request_identity={"trial": 0})
    with pytest.raises(BudgetError, match="Per-trial physical dispatch limit"):
        client.complete(stage="hypothesis", system_prompt="rules", user_prompt="three",
                        request_identity={"trial": 0})
    calls = client.ledger.summary()["calls"].values()
    assert len(list(calls)) == 2


def test_campaign_time_guard_stops_before_provider_dispatch(tmp_path):
    transport = Transport()
    client = GuardedLLM(tmp_path, ResearchPolicy(budget_mode="unlimited"), transport)
    def expired():
        raise BudgetError("Campaign wall-clock limit reached")
    client.dispatch_guard = expired
    with pytest.raises(BudgetError, match="wall-clock limit"):
        call(client)
    assert transport.calls == 0
    assert client.ledger.summary()["call_count"] == 0


def test_unknown_cost_is_reported_explicitly_for_unlimited_policy(tmp_path):
    transport = Transport(cost=None)
    transport.maximum_cost = None
    client = GuardedLLM(tmp_path, ResearchPolicy(budget_mode="unlimited"), transport)
    call(client)
    summary = client.ledger.summary()
    assert summary["unknown_cost_calls"] == 1
    assert next(iter(summary["calls"].values()))["actual_cost"] is None


def test_usage_summary_separates_local_estimates_unknown_provider_usage_and_cache(tmp_path):
    client = GuardedLLM(tmp_path, ResearchPolicy(budget_mode="free_only"),
                        ReplayTransport({"code": '{"source":"valid"}'}))
    assert call(client) == '{"source":"valid"}'
    assert call(client) == '{"source":"valid"}'
    report = client.usage_summary()
    assert report["logical_calls"] == 1
    assert report["transport_attempts"] == 1
    assert report["application_cache_hits"] == 1
    assert report["usage_coverage"] == 0
    assert report["unknown_attempts"] == 1
    assert report["estimated_input_tokens"] > 0
    assert report["provider_input_tokens"] is None


def test_usage_summary_counts_each_physical_attempt_once(tmp_path):
    class RetriedTransport:
        paid = False
        maximum_cost = "0"
        identity = "retried-transport"

        def complete(self, prompt):
            return CompletionReply("{}", "0", {
                "_attempt_records": [
                    {"status": "failed", "usage": {}},
                    {"status": "succeeded", "usage": {"input_tokens": 100, "output_tokens": 20}},
                ]}, self.identity)

    client = GuardedLLM(tmp_path, ResearchPolicy(budget_mode="free_only"), RetriedTransport())
    client.complete(stage="proposal", system_prompt="rules", user_prompt="factor",
                    request_identity={"trial": 1})
    report = client.usage_summary()
    assert report["transport_attempts"] == 2
    assert report["usage_coverage"] == .5
    assert report["unknown_attempts"] == 1
    assert report["provider_input_tokens"] == 100


def test_stage_output_cap_is_requested_and_explicitly_marked_when_backend_cannot_enforce(tmp_path):
    class CaptureTransport:
        paid = False
        maximum_cost = "0"
        identity = "capture-transport"

        def __init__(self):
            self.prompt = None

        def complete(self, prompt):
            self.prompt = prompt
            return CompletionReply("{}", "0", {}, self.identity)

    transport = CaptureTransport()
    GuardedLLM(tmp_path, ResearchPolicy(budget_mode="free_only"), transport).complete(
        stage="proposal", system_prompt="rules", user_prompt="factor", request_identity={"trial": 1})
    assert transport.prompt["requested_max_output_tokens"] == 1400
    assert transport.prompt["generation_limit_status"] == "backend_unsupported_not_enforced"


def test_rdagent_transport_does_not_retry_ambiguous_connection_failure(tmp_path):
    from etf_ml.contracts import ExecutionResult
    from etf_ml.research.llm import RDAgentTransport

    class Backend:
        def __init__(self):
            self.calls = 0
            self.pre_dispatch_state = None

        def run(self, argv, workspace, env, limits, *, trusted):
            self.calls += 1
            self.pre_dispatch_state = json.loads((workspace.parent / "attempts.json").read_text())
            if self.calls == 1:
                return ExecutionResult("failed", 1, "", "APIConnectionError: incomplete chunked read",
                                       0.1, "nonzero_exit")
            (workspace / "response.json").write_text(json.dumps({
                "text": "{}", "actual_cost": None, "usage": {}, "provider": "test"}), encoding="utf-8")
            return ExecutionResult("succeeded", 0, "", "", 0.1)

    transport = RDAgentTransport.__new__(RDAgentTransport)
    transport.root = tmp_path / "transport"
    transport.backend = Backend()
    transport.provider_env = {}
    transport.policy = ResearchPolicy(budget_mode="unlimited")
    transport.identity = "single-dispatch-test"
    client = GuardedLLM(tmp_path / "research", ResearchPolicy(budget_mode="unlimited"), transport)
    with pytest.raises(QualityError, match="outcome is uncertain"):
        call(client)
    with pytest.raises(BudgetError, match="already dispatched"):
        call(client)
    assert transport.backend.calls == 1
    assert transport.backend.pre_dispatch_state["attempts"][0]["status"] == "dispatching"
    evidence = json.loads(next(transport.root.glob("*/attempts.json")).read_text())
    assert evidence["dispatch_policy"]["max_physical_attempts"] == 1
    assert evidence["dispatch_policy"]["automatic_ambiguous_retry"] is False
    assert [row["status"] for row in evidence["attempts"]] == ["unknown"]
    policy_manifest = json.loads((tmp_path / "research" / "dispatch_policy.json").read_text())
    assert policy_manifest["physical_dispatch"] == evidence["dispatch_policy"]
    usage = client.usage_summary()
    assert usage["transport_attempts"] == usage["unknown_attempts"] == 1
    assert usage["provider_input_tokens"] is None
    assert usage["cost"]["unknown_cost_calls"] == 1
    transport.dispatch_policy = {"id": "changed", "max_physical_attempts": 2,
                                 "automatic_ambiguous_retry": True}
    with pytest.raises(ConfigurationError, match="dispatch policy is immutable"):
        GuardedLLM(tmp_path / "research", ResearchPolicy(budget_mode="unlimited"), transport)


def test_rejected_provider_response_keeps_physical_usage_in_uncertain_ledger(tmp_path):
    class OversizedTransport(Transport):
        def complete(self, prompt):
            self.calls += 1
            return CompletionReply("x" * 11, None,
                                   {"input_tokens": 20, "output_tokens": 2000}, self.identity)

    transport = OversizedTransport()
    client = GuardedLLM(tmp_path, ResearchPolicy(budget_mode="unlimited",
        limits=RuntimeLimits(max_output_bytes=10)), transport)
    with pytest.raises(QualityError, match="exceeds output limit"):
        call(client)
    summary = client.usage_summary()
    assert summary["transport_attempts"] == 1 and summary["usage_coverage"] == 1
    assert summary["provider_input_tokens"] == 20 and summary["provider_output_tokens"] == 2000
    assert summary["cost"]["unknown_cost_calls"] == 1


def test_billing_overrun_is_recorded_and_result_cannot_be_reused(tmp_path):
    transport = Transport(cost=".15")
    client = GuardedLLM(tmp_path, ResearchPolicy(budget_mode="capped", api_budget=.1), transport)
    with pytest.raises(BudgetError, match="exceeded"):
        call(client)
    assert client.ledger.summary()["measured_cost"] == "0.15"
    with pytest.raises(BudgetError):
        call(client)
    assert transport.calls == 1


def test_reusing_request_or_changing_ledger_policy_is_rejected(tmp_path):
    ledger = BudgetLedger(tmp_path / "bill.json", ResearchPolicy(budget_mode="unlimited"))
    ledger.reserve("request", "one", paid=True)
    with pytest.raises(IntegrityError):
        ledger.reserve("request", "two", paid=True)
    changed = BudgetLedger(tmp_path / "bill.json", ResearchPolicy(budget_mode="free_only"))
    with pytest.raises(ConfigurationError, match="immutable"):
        changed.summary()


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-.1", True])
def test_invalid_amounts_are_never_used_as_costs(tmp_path, value):
    ledger = BudgetLedger(tmp_path / "bill.json", ResearchPolicy(budget_mode="unlimited"))
    with pytest.raises(ConfigurationError):
        ledger.reserve("request", "hash", paid=True, maximum_cost=value)
