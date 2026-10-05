"""Small deterministic admission decisions for completed research evidence."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from etf_ml.utils import content_hash


@dataclass(frozen=True)
class DedupDecision:
    decision: str
    definition_id: str | None
    admission_id: str | None
    matched_trial_ids: list[str]
    reusable_evaluation_id: str | None
    reason_codes: list[str]
    compatibility_report: dict
    retest_reason: str | None = None
    reused_economic_status: str | None = None
    reuse_package_id: str | None = None

    @property
    def decision_hash(self) -> str:
        return content_hash(asdict(self))

    def to_dict(self) -> dict:
        value = asdict(self)
        value["decision_hash"] = self.decision_hash
        return value


def admission_id(identity, *, snapshot_id: str, baseline_id: str, protocol_id: str,
                 policy_version: str = "search-policy-v1") -> str | None:
    if not identity.hard_matchable:
        return None
    return content_hash({"definition_id": identity.definition_id, "snapshot_id": snapshot_id,
                         "baseline_id": baseline_id, "protocol_id": protocol_id,
                         "policy_version": policy_version})


def decide_admission(identity, cards: list[dict], *, snapshot_id: str, baseline_id: str,
                     protocol_id: str, reuse_context_id: str | None = None,
                     attempted_trials: int | None = None) -> DedupDecision:
    """Inspect every matching card; never let the first record decide."""
    aid = admission_id(identity, snapshot_id=snapshot_id, baseline_id=baseline_id, protocol_id=protocol_id)
    matches = [card for card in cards if card.get("definition_id") == identity.definition_id]
    trials = sorted(str(card.get("trial_id", "")) for card in matches if card.get("trial_id"))
    if not identity.hard_matchable:
        return DedupDecision("new", identity.definition_id, aid, trials, None,
                             ["identity_semantics_incomplete"], {"hard_matchable": False})
    comparable = [card for card in matches if card.get("evaluation_snapshot_id", card.get("snapshot_id")) == snapshot_id and
                  card.get("evaluation_baseline_id", card.get("baseline_id")) == baseline_id and
                  (card.get("evaluation_protocol_id", card.get("protocol_id")) == protocol_id or
                   bool(reuse_context_id and card.get("reuse_context_id") == reuse_context_id)) and
                  (not card.get("reuse_package_id") or attempted_trials is None or
                   card.get("evaluation_attempted_trials") == attempted_trials)]
    statuses = {card.get("economic_status", card.get("status")) for card in comparable}
    if len(statuses.intersection({"accepted", "rejected"})) > 1:
        return DedupDecision("blocked", identity.definition_id, aid, trials, None,
                             ["conflicting_complete_evaluations"], {"comparable_count": len(comparable)})
    reusable = next((card for card in comparable if card.get("evaluation_evidence_verified") and card.get("evaluation_id") and
                     card.get("economic_status", card.get("status")) in {"accepted", "rejected"}), None)
    if reusable:
        return DedupDecision("reuse", identity.definition_id, aid, trials, reusable["evaluation_id"],
                             ["complete_same_evaluation"], {"comparable_count": len(comparable)},
                             reused_economic_status=reusable.get("economic_status", reusable.get("status")),
                             reuse_package_id=reusable.get("reuse_package_id"))
    if any(card.get("attempt_outcome") == "in_progress" for card in matches):
        return DedupDecision("wait_existing", identity.definition_id, aid, trials, None,
                             ["matching_admission_in_progress"], {"comparable_count": len(comparable)})
    if any(card.get("failure_category") in {"implementation_error", "data_contract_failure"} for card in matches):
        return DedupDecision("repair", identity.definition_id, aid, trials, None,
                             ["bounded_technical_repair"], {"comparable_count": len(comparable)})
    return DedupDecision("new", identity.definition_id, aid, trials, None,
                         ["no_reusable_complete_evaluation"], {"comparable_count": len(comparable)})
