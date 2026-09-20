from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from etf_ml.artifacts import RUN_ID
from etf_ml.contracts import ResearchPolicy
from etf_ml.errors import BudgetError, ConfigurationError, IntegrityError
from etf_ml.utils import FileLock, atomic_json, content_hash


def money(value) -> Decimal:
    try:
        if isinstance(value, bool):
            raise InvalidOperation
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise InvalidOperation
        return result
    except (InvalidOperation, TypeError, ValueError):
        raise ConfigurationError("Costs must be finite nonnegative monetary amounts") from None


class BudgetLedger:
    """Reserve before dispatch; never refund a possibly billed uncertain request."""

    def __init__(self, path: Path, policy: ResearchPolicy, *, currency="USD"):
        self.path, self.policy, self.currency = Path(path), policy.model_copy(deep=True), currency
        self.lock_path = self.path.with_suffix(".lock")
        self.identity = content_hash({"mode": policy.budget_mode, "cap": policy.api_budget,
                                      "currency": currency})

    def _read(self):
        if not self.path.exists():
            return {"policy_id": self.identity, "currency": self.currency, "calls": {}}
        state = json.loads(self.path.read_text(encoding="utf-8"))
        if state["policy_id"] != self.identity:
            raise ConfigurationError("Budget ledger policy is immutable")
        return state

    @staticmethod
    def _totals(state):
        measured, reserved, unknown = Decimal(0), Decimal(0), 0
        for call in state["calls"].values():
            if call["actual_cost"] is not None:
                measured += money(call["actual_cost"])
            else:
                reserved += money(call["maximum_cost"] or 0)
                unknown += int(call["status"] in ("uncertain", "cost_unknown"))
        return measured, reserved, unknown

    def summary(self):
        with FileLock(self.lock_path):
            state = self._read()
            measured, reserved, unknown = self._totals(state)
            return {"currency": self.currency, "measured_cost": str(measured),
                    "outstanding_reservations": str(reserved), "unknown_cost_calls": unknown,
                    "call_count": len(state["calls"]),
                    "calls": state["calls"]}

    def reserve(self, call_id: str, request_hash: str, *, paid: bool,
                maximum_cost=None):
        if not RUN_ID.fullmatch(call_id):
            raise ConfigurationError("Invalid external call_id")
        bound = money(maximum_cost) if maximum_cost is not None else None
        if paid and self.policy.budget_mode is None:
            raise BudgetError("Research monetary policy is unresolved")
        if paid and self.policy.budget_mode == "free_only":
            raise BudgetError("Free-only policy prohibits paid calls")
        if paid and self.policy.budget_mode == "capped" and (bound is None or bound <= 0):
            raise BudgetError("Capped calls require a defensible maximum billed cost")
        if not paid and bound not in (None, Decimal(0)):
            raise ConfigurationError("A free transport must have zero billed cost")
        with FileLock(self.lock_path):
            state = self._read()
            if call_id in state["calls"]:
                call = state["calls"][call_id]
                if (call["request_hash"] != request_hash or call["paid"] != paid or
                        call["maximum_cost"] != (str(bound) if bound is not None else None)):
                    raise IntegrityError("External call_id already belongs to another request")
                return call
            measured, outstanding, _ = self._totals(state)
            if self.policy.budget_mode == "capped" and measured + outstanding + (bound or 0) > money(self.policy.api_budget):
                raise BudgetError("Research monetary cap reached")
            call = {"status": "reserved", "request_hash": request_hash, "paid": paid,
                    "maximum_cost": str(bound) if bound is not None else None,
                    "actual_cost": None, "response_hash": None, "usage": {}}
            state["calls"][call_id] = call
            atomic_json(self.path, state)
            return call

    def complete(self, call_id: str, response_hash: str, *, actual_cost,
                 usage: dict | None = None):
        amount = money(actual_cost) if actual_cost is not None else None
        with FileLock(self.lock_path):
            state = self._read()
            call = state["calls"][call_id]
            if call["status"] in ("completed", "cost_unknown", "overrun"):
                if call["response_hash"] != response_hash or call["actual_cost"] != (str(amount) if amount is not None else None):
                    raise IntegrityError("Committed external result is immutable")
                return call
            if not call["paid"] and amount != Decimal(0):
                raise IntegrityError("Free transport reported a nonzero or unknown cost")
            call.update({"status": "completed" if amount is not None else "cost_unknown",
                         "actual_cost": str(amount) if amount is not None else None,
                         "response_hash": response_hash, "usage": dict(usage or {})})
            overrun = (call["maximum_cost"] is not None and amount is not None and
                       amount > money(call["maximum_cost"]))
            if overrun:
                call["status"] = "overrun"
            atomic_json(self.path, state)
            if overrun:
                raise BudgetError("Actual billed cost exceeded the transport's reserved bound")
            return call

    def uncertain(self, call_id: str, *, reason: str):
        with FileLock(self.lock_path):
            state = self._read()
            call = state["calls"][call_id]
            if call["status"] == "reserved":
                call.update({"status": "uncertain", "reason": reason})
                atomic_json(self.path, state)

    def reconcile(self, call_id: str, *, actual_cost, billing_reference: str):
        if not billing_reference:
            raise ConfigurationError("Billing reconciliation needs an explicit reference")
        with FileLock(self.lock_path):
            state = self._read()
            call = state["calls"][call_id]
            if call["status"] not in ("uncertain", "cost_unknown"):
                raise ConfigurationError("Only uncertain billing may be reconciled")
            amount = money(actual_cost)
            call.update({"status": "completed", "actual_cost": str(amount),
                         "billing_reference": billing_reference})
            overrun = call["maximum_cost"] is not None and amount > money(call["maximum_cost"])
            if overrun:
                call["status"] = "overrun"
            atomic_json(self.path, state)
            if overrun:
                raise BudgetError("Reconciled billing exceeded the reserved bound")
