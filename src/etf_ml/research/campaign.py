"""Append-only, bounded research campaign ledger derived from run checkpoints."""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from etf_ml.artifacts import RUN_ID
from etf_ml.errors import BudgetError, ConfigurationError, IntegrityError
from etf_ml.utils import FileLock, atomic_json, content_hash, ensure_within


FIVE_FACTOR_MECHANISM_PLAN = [
    {"slot": "trend_momentum", "research_group": "trend_momentum",
     "description": "trend continuation or momentum persistence"},
    {"slot": "reversal", "research_group": "reversal",
     "description": "mean reversion or short-horizon reversal"},
    {"slot": "volume_price", "research_group": "volume_price",
     "description": "price-volume interaction or flow confirmation"},
    {"slot": "volatility_risk_adjusted", "research_group": "volatility_risk_adjusted",
     "description": "volatility, downside risk, or risk-adjusted return"},
    {"slot": "orthogonal_new", "research_group": "orthogonal_new",
     "description": "a materially distinct mechanism not covered by the prior four slots"},
]


class CampaignLedger:
    def __init__(self, root: Path, campaign_id: str, max_attempts: int,
                 max_duration_seconds: int | None = None, mechanism_plan: list[dict] | None = None):
        if (not isinstance(campaign_id, str) or not RUN_ID.fullmatch(campaign_id) or
                type(max_attempts) is not int or max_attempts < 1):
            raise ConfigurationError("Campaign needs a valid id and a positive finite attempt cap")
        if max_duration_seconds is not None and (type(max_duration_seconds) is not int or
                                                  max_duration_seconds < 1):
            raise ConfigurationError("Campaign duration must be a positive finite integer")
        base = Path(root).resolve()
        self.root = ensure_within(base / campaign_id, base)
        self.campaign_id, self.max_attempts = campaign_id, int(max_attempts)
        self.max_duration_seconds = max_duration_seconds
        if mechanism_plan is not None:
            plan = [dict(item) for item in mechanism_plan]
            if (len(plan) != len(mechanism_plan) or len(plan) > self.max_attempts or
                    any(set(item) != {"slot", "research_group", "description"} or
                        not all(isinstance(item[key], str) and item[key].strip()
                                for key in ("slot", "research_group", "description")) for item in plan) or
                    len({item["slot"] for item in plan}) != len(plan) or
                    len({item["research_group"] for item in plan}) != len(plan)):
                raise ConfigurationError("Campaign mechanism plan needs unique named slots and research groups")
            self.mechanism_plan = plan
        else:
            self.mechanism_plan = None
        self.events = self.root / "events"
        self.lock = self.root / ".campaign.lock"

    def initialize(self):
        with FileLock(self.lock):
            path = self.root / "campaign.json"
            expected = {"schema_version": "research-campaign-v1", "campaign_id": self.campaign_id,
                        "max_attempts": self.max_attempts}
            if self.mechanism_plan is not None:
                expected["mechanism_plan"] = self.mechanism_plan
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
                if any(existing.get(key) != value for key, value in expected.items()
                       if key != "mechanism_plan"):
                    raise ConfigurationError("Campaign identity or attempt cap is immutable")
                duration = existing.get("max_duration_seconds")
                if (self.max_duration_seconds is not None and
                        duration != self.max_duration_seconds):
                    raise ConfigurationError("Campaign wall-clock limit is immutable")
                self.max_duration_seconds = duration
                if self.mechanism_plan is not None and existing.get("mechanism_plan") != self.mechanism_plan:
                    raise ConfigurationError("Campaign mechanism plan is immutable")
                self.mechanism_plan = existing.get("mechanism_plan")
            else:
                if self.max_duration_seconds is not None:
                    expected["max_duration_seconds"] = self.max_duration_seconds
                atomic_json(path, expected)
            self.events.mkdir(parents=True, exist_ok=True)
            self._read_events()

    def _read_events(self):
        rows = [json.loads(path.read_text(encoding="utf-8")) for path in self.events.glob("*.json")]
        rows.sort(key=lambda row: row.get("committed_sequence", -1))
        previous, result = None, []
        for sequence, row in enumerate(rows, 1):
            event_id = row.get("event_id")
            body = {key: value for key, value in row.items()
                    if key not in {"event_id", "committed_sequence", "previous_event_hash", "event_hash"}}
            if (row.get("committed_sequence") != sequence or row.get("previous_event_hash") != previous or
                    event_id != content_hash(body) or
                    row.get("event_hash") != content_hash({**body, "event_id": event_id,
                        "committed_sequence": sequence, "previous_event_hash": previous})):
                raise IntegrityError("Campaign event chain is invalid")
            if not (self.events / (event_id + ".json")).is_file():
                raise IntegrityError("Campaign event filename does not match event identity")
            previous = row["event_hash"]
            result.append(row)
        return result

    def _append_locked(self, body):
        body = {"campaign_id": self.campaign_id, **body}
        event_id = content_hash(body)
        path = self.events / (event_id + ".json")
        existing = {row["event_id"]: row for row in self._read_events()}.get(event_id)
        if existing:
            return existing
        rows = self._read_events()
        sequence = len(rows) + 1
        previous = rows[-1]["event_hash"] if rows else None
        event = {**body, "event_id": event_id, "committed_sequence": sequence,
                 "previous_event_hash": previous}
        event["event_hash"] = content_hash(event)
        atomic_json(path, event)
        return event

    def _summary_locked(self):
        events = self._read_events()
        started, completed, calls, groups, definitions, evaluations = {}, {}, {}, defaultdict(Counter), set(), set()
        mechanism_slots = Counter()
        for event in events:
            slot = event.get("trial_key")
            group = event.get("compatibility_group_id")
            if event["event_type"] == "trial_started":
                started[slot] = event
                groups[group]["generation_attempts"] += 1
                if event.get("mechanism_slot"):
                    mechanism_slots[event["mechanism_slot"]] += 1
            elif event["event_type"] == "proposal_generated":
                if event.get("definition_id"):
                    definitions.add(event["definition_id"])
                groups[group]["proposal_count"] += 1
            elif event["event_type"] == "trial_committed":
                completed[slot] = event
                candidates = event.get("candidate_evaluations") or [{
                    "definition_id": event.get("definition_id"), "evaluation_id": event.get("evaluation_id"),
                    "outcome": event.get("outcome")}]
                for candidate in candidates:
                    if candidate.get("definition_id"):
                        definitions.add(candidate["definition_id"])
                    if candidate.get("evaluation_id") and candidate.get("outcome") in {
                            "accepted", "rejected", "inconclusive"}:
                        evaluations.add(candidate["evaluation_id"])
                groups[group]["committed_trials"] += 1
                groups[group][event.get("outcome", "unknown")] += 1
                for call in event.get("calls", []):
                    if isinstance(call, dict) and isinstance(call.get("call_id"), str):
                        calls[call["call_id"]] = call
            elif event["event_type"] == "usage_snapshot":
                for call in event.get("calls", []):
                    if isinstance(call, dict) and isinstance(call.get("call_id"), str):
                        calls[call["call_id"]] = call
        known_cost = sum((Decimal(call["actual_cost"]) for call in calls.values()
                          if isinstance(call.get("actual_cost"), str)), Decimal(0))
        attempts = [attempt for call in calls.values()
                    for attempt in call.get("attempts", []) if isinstance(attempt, dict)]
        attempts.extend({"provider_input_tokens": None, "provider_output_tokens": None}
                        for call in calls.values() if not call.get("attempts"))
        provider_complete = sum(row.get("provider_input_tokens") is not None and
                                row.get("provider_output_tokens") is not None for row in attempts)
        unknown_cost = sum(call.get("actual_cost") is None for call in calls.values())
        usage_audit_reasons = []
        if len(started) > len(completed):
            usage_audit_reasons.append("unresolved_trial_usage_slots")
        if unknown_cost:
            usage_audit_reasons.append("provider_cost_unknown")
        if len(attempts) > provider_complete:
            usage_audit_reasons.append("provider_usage_incomplete")
        starts = [event.get("started_at_ns") for event in events
                  if event["event_type"] == "trial_started" and
                  isinstance(event.get("started_at_ns"), int)]
        elapsed = max(0, time.time_ns() - min(starts)) / 1_000_000_000 if starts else 0
        expired = (self.max_duration_seconds is not None and starts and
                   elapsed >= self.max_duration_seconds)
        return {"schema_version": "research-campaign-summary-v2", "campaign_id": self.campaign_id,
                "max_attempts": self.max_attempts, "generation_attempts": len(started),
                "max_duration_seconds": self.max_duration_seconds,
                "elapsed_seconds": elapsed, "wall_clock_expired": bool(expired),
                "open_attempts": len(set(started) - set(completed)),
                "unique_definitions": len(definitions), "completed_unique_evaluations": len(evaluations),
                "reused_evaluations": sum(event.get("outcome") == "reuse" for event in completed.values()),
                "session_attempted_trials": {event.get("run_id"): sum(
                    1 for key in started if key.startswith(event.get("run_id", "") + ":"))
                    for event in started.values()},
                "campaign_attempted_trials": len(started), "by_compatibility_group": {
                    key: dict(value) for key, value in sorted(groups.items())},
                "by_mechanism_slot": dict(sorted(mechanism_slots.items())),
                "cost_known_subtotal": str(known_cost), "unknown_cost_calls": unknown_cost,
                "provider_usage_coverage": provider_complete / len(attempts) if attempts else None,
                "provider_usage_unknown_attempts": len(attempts) - provider_complete,
                "usage_audit_status": "partial" if usage_audit_reasons else "completed",
                "usage_audit_reasons": usage_audit_reasons,
                "unresolved_trial_usage_slots": len(set(started) - set(completed)),
                "event_count": len(events), "event_chain_head": events[-1]["event_hash"] if events else None}

    def summary(self):
        with FileLock(self.lock):
            self.initialize_locked()
            return self._summary_locked()

    def audit_view(self):
        """Read an existing ledger without creating directories or taking a write lock.

        A concurrent append invalidates this observation instead of producing a
        mixed summary. Published events themselves are immutable and atomic.
        """
        self.initialize_locked()
        before = self._read_events()
        summary = self._summary_locked()
        after = self._read_events()
        if before != after:
            raise IntegrityError("Campaign changed during read-only audit")
        return summary, before

    def begin_trial(self, *, run_id: str, trial_index: int, compatibility_group_id: str,
                    protocol_id: str, allow_over_cap: bool = False) -> bool:
        trial_key = f"{run_id}:{trial_index}"
        with FileLock(self.lock):
            self.initialize_locked()
            events = self._read_events()
            if any(row.get("trial_key") == trial_key and row["event_type"] == "trial_started" for row in events):
                return True
            summary = self._summary_locked()
            if summary["wall_clock_expired"]:
                return False
            if summary["generation_attempts"] >= self.max_attempts and not allow_over_cap:
                return False
            body = {"event_type": "trial_started", "trial_key": trial_key,
                                 "run_id": run_id, "trial_index": trial_index,
                                 "started_at_ns": time.time_ns(),
                                 "compatibility_group_id": compatibility_group_id,
                                 "protocol_id": protocol_id}
            if self.mechanism_plan:
                allocated = {row.get("mechanism_slot") for row in events
                             if row["event_type"] == "trial_started"}
                next_slot = next((item for item in self.mechanism_plan
                                  if item["slot"] not in allocated), None)
                if next_slot is None:
                    return False
                body["mechanism_slot"] = next_slot["slot"]
            self._append_locked(body)
            return True

    def trial_mechanism_assignment(self, *, run_id: str, trial_index: int) -> dict | None:
        """Return the frozen slot assigned at trial start, if this plan is enabled."""
        if not self.mechanism_plan:
            return None
        with FileLock(self.lock):
            self.initialize_locked()
            key = f"{run_id}:{trial_index}"
            event = next((row for row in self._read_events()
                          if row["event_type"] == "trial_started" and row.get("trial_key") == key), None)
            if event is None:
                return None
            return next((dict(item) for item in self.mechanism_plan
                         if item["slot"] == event.get("mechanism_slot")), None)

    def require_time_remaining(self):
        with FileLock(self.lock):
            self.initialize_locked()
            if self._summary_locked()["wall_clock_expired"]:
                raise BudgetError("Campaign wall-clock limit reached")

    def time_limit_expired(self) -> bool:
        with FileLock(self.lock):
            self.initialize_locked()
            return self._summary_locked()["wall_clock_expired"]

    def initialize_locked(self):
        path = self.root / "campaign.json"
        expected = {"schema_version": "research-campaign-v1", "campaign_id": self.campaign_id,
                    "max_attempts": self.max_attempts}
        if self.max_duration_seconds is not None:
            expected["max_duration_seconds"] = self.max_duration_seconds
        if not path.is_file():
            raise ConfigurationError("Campaign configuration is missing or changed")
        existing = json.loads(path.read_text(encoding="utf-8"))
        if any(existing.get(key) != value for key, value in expected.items()):
            raise ConfigurationError("Campaign configuration is missing or changed")
        if self.max_duration_seconds is None:
            self.max_duration_seconds = existing.get("max_duration_seconds")
        if self.mechanism_plan is None:
            self.mechanism_plan = existing.get("mechanism_plan")
        elif existing.get("mechanism_plan") != self.mechanism_plan:
            raise ConfigurationError("Campaign mechanism plan is immutable")

    def commit_trial(self, *, run_id: str, trial_index: int, compatibility_group_id: str,
                     protocol_id: str, record: dict):
        trial_key = f"{run_id}:{trial_index}"
        card = record.get("research_card") or {}
        result = record.get("result") or {}
        candidates = record.get("campaign_candidates")
        usage = record.get("usage_summary") or {}
        billing = usage.get("cost") or {}
        calls = []
        for call_id, item in sorted((billing.get("calls") or {}).items()):
            call_usage = item.get("usage") or {}
            calls.append({"call_id": call_id, "status": item.get("status"),
                          "actual_cost": item.get("actual_cost"),
                          "attempts": call_usage.get("attempts", [])})
        body = {"event_type": "trial_committed", "trial_key": trial_key, "run_id": run_id,
                "trial_index": trial_index, "compatibility_group_id": compatibility_group_id,
                "protocol_id": protocol_id, "outcome": result.get("status", card.get("attempt_outcome", "unknown")),
                "definition_id": card.get("definition_id"),
                "evaluation_id": card.get("evaluation_id") or result.get("reused_evaluation_id"),
                "candidate_evaluations": candidates or [],
                "source_trial_sha256": record.get("_committed_sha256"), "calls": calls}
        with FileLock(self.lock):
            self.initialize_locked()
            self._append_locked(body)

    def record_proposal(self, *, run_id: str, trial_index: int, proposal_index: int,
                        definition_id: str | None, research_group: str | None,
                        compatibility_group_id: str, protocol_id: str,
                        mechanism_slot: str | None = None):
        body = {"event_type": "proposal_generated", "trial_key": f"{run_id}:{trial_index}",
                "run_id": run_id, "trial_index": trial_index, "proposal_index": proposal_index,
                "definition_id": definition_id, "research_group": research_group,
                "compatibility_group_id": compatibility_group_id, "protocol_id": protocol_id}
        if mechanism_slot is not None:
            body["mechanism_slot"] = mechanism_slot
        with FileLock(self.lock):
            self.initialize_locked()
            self._append_locked(body)

    def record_usage(self, *, run_id: str, trial_index: int, usage_summary: dict):
        billing = (usage_summary or {}).get("cost") or {}
        calls = []
        for call_id, item in sorted((billing.get("calls") or {}).items()):
            attempts = (item.get("usage") or {}).get("attempts", [])
            calls.append({"call_id": call_id, "status": item.get("status"),
                          "actual_cost": item.get("actual_cost"), "attempts": attempts})
        if not calls:
            return
        body = {"event_type": "usage_snapshot", "trial_key": f"{run_id}:{trial_index}",
                "run_id": run_id, "trial_index": trial_index, "calls": calls}
        with FileLock(self.lock):
            self.initialize_locked()
            self._append_locked(body)
