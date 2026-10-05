"""Hash-bound research cards and conservative exact duplicate detection."""
from __future__ import annotations

from etf_ml.utils import content_hash


def _value(subject, name):
    return subject.get(name) if isinstance(subject, dict) else getattr(subject, name, None)


def normalize_formula(formula: str) -> str:
    # Compatibility helper for legacy cards only.  New hard admission uses
    # factor_identity's complete definition projection instead.
    return "".join((formula or "").split())


def exact_duplicate(formula: str, cards: list[dict]) -> dict | None:
    target = normalize_formula(formula)
    for card in cards:
        if normalize_formula(str(card.get("formula", ""))) == target:
            return card
    return None


def build_card(*, hypothesis, proposal: dict, feedback: dict, context_hash: str,
               snapshot_id: str | None = None, protocol_id: str | None = None,
               baseline_id: str | None = None, feedback_summary: dict | None = None,
               trial_id: str | None = None, definition_id: str | None = None,
               evaluation_id: str | None = None, resulting_baseline_id: str | None = None,
               resulting_protocol_id: str | None = None, attempt_outcome: str = "completed",
               economic_status: str | None = None, failure_category: str | None = None,
               dedup_decision: dict | None = None, evaluation_evidence: list[dict] | None = None) -> dict:
    row = (feedback.get("by_candidate") or [{}])[0]
    card = {
        "schema_version": "research-card-v2", "factor_id": proposal.get("factor_id"),
        "formula": proposal.get("formula"), "mechanism": proposal.get("mechanism"),
        "required_fields": proposal.get("required_fields", []), "lookback": proposal.get("lookback"),
        "research_group": proposal.get("research_group"), "hypothesis": _value(hypothesis, "hypothesis"),
        "reason": _value(hypothesis, "reason"), "status": row.get("status", feedback.get("status")),
        "reasons": row.get("reasons", []), "context_hash": context_hash,
        "snapshot_id": snapshot_id, "protocol_id": protocol_id, "baseline_id": baseline_id,
        "trial_id": trial_id, "definition_id": definition_id, "evaluation_id": evaluation_id,
        "evaluation_snapshot_id": snapshot_id, "evaluation_protocol_id": protocol_id,
        "evaluation_baseline_id": baseline_id, "resulting_baseline_id": resulting_baseline_id,
        "resulting_protocol_id": resulting_protocol_id,
        "attempt_outcome": attempt_outcome,
        "economic_status": economic_status if economic_status is not None else (
            row.get("status", feedback.get("status")) if attempt_outcome == "completed" else None),
        "failure_category": failure_category, "dedup_decision": dedup_decision,
        "evaluation_evidence": evaluation_evidence or [],
        "feedback_summary": feedback_summary or feedback.get("summary", {}),
        "comparable": True,
    }
    card["card_hash"] = content_hash(card)
    return card
