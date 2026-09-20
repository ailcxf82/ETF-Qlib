from concurrent.futures import ThreadPoolExecutor

import pytest

from etf_ml.contracts import ResearchPolicy
from etf_ml.errors import BudgetError, ConfigurationError, IntegrityError
from etf_ml.research.budget import BudgetLedger
from etf_ml.research.llm import GuardedLLM, CompletionReply


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


def test_unknown_cost_is_reported_explicitly_for_unlimited_policy(tmp_path):
    transport = Transport(cost=None)
    transport.maximum_cost = None
    client = GuardedLLM(tmp_path, ResearchPolicy(budget_mode="unlimited"), transport)
    call(client)
    summary = client.ledger.summary()
    assert summary["unknown_cost_calls"] == 1
    assert next(iter(summary["calls"].values()))["actual_cost"] is None


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
