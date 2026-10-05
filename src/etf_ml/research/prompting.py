"""Deterministic, versioned prompt projections for ETF factor research.

This module intentionally has no provider dependency: it is exercised in the
zero-cost test suite before any prompt is sent to a model.
"""
from __future__ import annotations

import statistics
import math
from typing import Any

from etf_ml.errors import BudgetError, QualityError
from etf_ml.utils import canonical_json, content_hash


PROMPT_POLICY_VERSION = "stage-context-v3"
TOKENIZER_VERSION = "tiktoken-cl100k_base"
SYSTEM_PROMPT_TOKEN_RESERVE = 256
STAGE_LIMITS = {
    "hypothesis": (4_000, 6_000, 1_000),
    "proposal": (3_500, 5_000, 1_400),
    "code": (3_000, 5_000, 2_500),
    "repair": (3_500, 6_000, 2_500),
}
REQUIRED_BY_STAGE = {
    "hypothesis": ("task_contract", "allowed_fields", "relevant_features", "qualification"),
    "proposal": ("current_hypothesis", "allowed_fields", "relevant_features", "task_contract"),
    "code": ("current_proposal", "allowed_fields", "runtime_constraints", "task_contract"),
    "repair": ("task_contract", "runtime_constraints"),
}


def estimate_tokens(value: str) -> int:
    """Local tokenizer estimate, explicitly not provider-reported usage."""
    import tiktoken
    return len(tiktoken.get_encoding("cl100k_base").encode(value))


def _stage(stage: str) -> str:
    if stage.startswith("hypothesis"):
        return "hypothesis" if stage == "hypothesis" else "repair"
    if stage.startswith("proposal"):
        return "proposal" if stage == "proposal" else "repair"
    if stage.startswith("code"):
        return "code" if stage.startswith("code:") else "repair"
    return "repair" if "repair" in stage else stage


def _safe_card(card: dict[str, Any]) -> dict[str, Any]:
    allowed = ("factor_id", "formula", "mechanism", "required_fields", "lookback",
               "research_group", "status", "economic_status", "reasons", "feedback_summary",
               "next_action", "comparable", "evaluation_evidence_verified",
               "attempt_outcome", "failure_category")
    safe = {key: card[key] for key in allowed if key in card}
    if card.get("economic_status") in {"accepted", "rejected"}:
        safe.pop("reasons", None)
        safe.pop("mechanism", None)
    if isinstance(safe.get("feedback_summary"), dict):
        safe["feedback_summary"] = _prompt_feedback_summary(safe["feedback_summary"])
    return safe


def _fold_deltas(rows: Any) -> list[dict[str, Any]]:
    """Keep fold-level return direction and seed coverage for prompt feedback."""
    by_fold: dict[str, list[dict[str, Any]]] = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("fold") is not None:
            by_fold.setdefault(str(row["fold"]), []).append(row)
    result = []
    for fold, values in sorted(by_fold.items()):
        summary = {"fold": fold}
        observed = [float(row["excess_return"]) for row in values
                    if isinstance(row.get("excess_return"), (int, float))]
        if observed:
            summary.update(median_excess_return=statistics.median(observed),
                           positive_seed_count=sum(value > 0 for value in observed),
                           zero_seed_count=sum(value == 0 for value in observed),
                           negative_seed_count=sum(value < 0 for value in observed))
        result.append(summary)
    return result


def _risk_by_scenario(rows: Any) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("fold") is not None:
            grouped.setdefault(str(row.get("scenario", "base")), []).append(row)
    result = []
    for scenario, values in sorted(grouped.items()):
        candidate_values = [float(row["candidate_value"]) for row in values
                            if isinstance(row.get("candidate_value"), (int, float))]
        baseline_values = [float(row["baseline_value"]) for row in values
                           if isinstance(row.get("baseline_value"), (int, float))]
        worst = max((row for row in values
                     if isinstance(row.get("candidate_value"), (int, float))),
                    key=lambda row: float(row["candidate_value"]), default=None)
        result.append({"scenario": scenario, "fold_count": len({str(row["fold"]) for row in values}),
                       "baseline_breach_count": sum(bool(row.get("baseline_breach")) for row in values),
                       "candidate_breach_count": sum(bool(row.get("candidate_breach")) for row in values),
                       **({"max_candidate_value": max(candidate_values),
                           "worst_candidate_fold": str(worst["fold"])} if candidate_values else {}),
                       **({"max_baseline_value": max(baseline_values)} if baseline_values else {})})
    return result


def _signal_summary(rows: Any) -> dict[str, Any]:
    values = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    keys = ("matched_decisions", "rank_changed", "selection_changed", "target_weight_changed",
            "candidate_execution_constrained", "baseline_execution_constrained", "missing_decisions",
            "rank_comparisons", "missing_rank_predictions", "missing_signal_ages",
            "prediction_compared_rows", "prediction_changed_rows",
            "baseline_risk_only_decisions", "candidate_risk_only_decisions")
    summary = {"observation_count": len(values),
               "statuses": sorted({str(row.get("status", "unknown")) for row in values})}
    for key in keys:
        observed = [row[key] for row in values if isinstance(row.get(key), (int, float))]
        if observed:
            summary[key] = sum(observed)
    return summary


def _cost_stress_by_fold(rows: Any) -> list[dict[str, Any]]:
    grouped: dict[tuple[float, str], list[dict[str, Any]]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("fold") is None:
            continue
        multiplier = row.get("multiplier")
        if not isinstance(multiplier, (int, float)) or isinstance(multiplier, bool):
            continue
        grouped.setdefault((float(multiplier), str(row["fold"])), []).append(row)
    metrics = ("excess_return_delta", "execution_cost_delta")
    result = []
    for (multiplier, fold), values in sorted(grouped.items()):
        row = {"multiplier": multiplier, "fold": fold, "seed_count": len(values)}
        for metric in metrics:
            observed = [item.get(metric) for item in values
                        if isinstance(item.get(metric), (int, float)) and
                        not isinstance(item.get(metric), bool) and math.isfinite(item[metric])]
            row["median_" + metric] = statistics.median(observed) if observed else None
            row["known_" + metric + "_seeds"] = len(observed)
        result.append(row)
    return result


def _prompt_feedback_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Bound a stored feedback card while retaining its decision evidence.

    Feedback artifacts can contain every fold/seed.  The LLM receives a
    deterministic sample plus medians/counts—not an all-or-nothing card that
    exceeds the prompt cap and silently vanishes.
    """
    rows = summary.get("by_candidate", [])
    output = {key: summary[key] for key in ("schema_version", "status") if key in summary}
    candidates = []
    for row in rows[:1]:
        if not isinstance(row, dict):
            continue
        candidate = {key: row[key] for key in (
            "decision_reasons", "median_excess_return_delta", "delta_count",
            "candidate_max_drawdown", "baseline_max_drawdown", "coverage", "time_check", "next_action", "unknown")
                     if key in row}
        if "unknown_evidence" in row:
            candidate["unknown_evidence"] = row["unknown_evidence"]
        candidate["fold_deltas"] = _fold_deltas(row.get("paired_deltas"))
        candidate["cost_stress_by_fold"] = _cost_stress_by_fold(row.get("cost_stress"))
        ablation = row.get("group_ablation", {})
        if isinstance(ablation, dict):
            candidate["group_ablation"] = {
                **{key: ablation[key] for key in ("status", "group", "reasons") if key in ablation},
                "fold_deltas": _fold_deltas(ablation.get("paired_deltas")),
            }
        signal = row.get("signal_to_execution", {})
        if isinstance(signal, dict):
            summary = _signal_summary(signal.get("by_fold"))
            candidate["signal_to_execution"] = {
                **{key: signal[key] for key in ("status",) if key in signal},
                "summary": {key: summary[key] for key in (
                "observation_count", "selection_changed", "target_weight_changed",
                    "rank_changed", "rank_comparisons", "candidate_execution_constrained",
                    "baseline_execution_constrained", "missing_decisions", "missing_rank_predictions",
                    "missing_signal_ages") if key in summary},
            }
        risk = row.get("risk", {})
        if isinstance(risk, dict):
            candidate["risk"] = {**{key: risk[key] for key in ("mode", "limit") if key in risk},
                                 "by_scenario": _risk_by_scenario(risk.get("by_fold"))}
        candidates.append(candidate)
    output["by_candidate"] = candidates
    return output


def _economic_card(card: dict[str, Any]) -> bool:
    return (card.get("comparable") is True and
            card.get("economic_status", card.get("status")) in {"accepted", "rejected"})


def _without_final_acceptance(value):
    """Prompt projections must not carry final/holdout metadata by accident."""
    if isinstance(value, dict):
        return {key: _without_final_acceptance(item) for key, item in value.items()
                if "holdout" not in str(key).lower() and "final" not in str(key).lower()}
    if isinstance(value, list):
        return [_without_final_acceptance(item) for item in value]
    return value


def select_cards(cards: list[dict] | None, *, current_fields: list[str] | None = None,
                 limit: int = 5, byte_cap: int = 6_400, token_cap: int = 1_600) -> list[dict]:
    """Choose 2 recent + 3 structurally relevant cards with deterministic ties."""
    current_fields = set(current_fields or [])
    rows = [card for card in (cards or []) if isinstance(card, dict)]
    deduped: dict[str, dict] = {}
    for card in rows:
        key = content_hash({"formula": card.get("formula"), "factor_id": card.get("factor_id")})
        deduped.setdefault(key, card)
    ranked = sorted(deduped.values(), key=lambda card: (
        -len(current_fields.intersection(card.get("required_fields", []))),
        str(card.get("factor_id", "")), content_hash(card)))
    recent = sorted(deduped.values(), key=lambda card: tuple(card.get("committed_order", (0, 0))), reverse=True)[:2]
    economic = sorted((card for card in deduped.values() if _economic_card(card)),
                      key=lambda card: tuple(card.get("committed_order", (0, 0))), reverse=True)
    selected = []
    # Guarantee two latest cards and then fill the remaining three slots with
    # relevant historical evidence.  They have different purposes.
    for card in economic[:1] + recent + ranked:
        compact = _safe_card(card)
        if compact in selected or len(selected) >= limit:
            continue
        prospective = selected + [compact]
        if len(canonical_json(prospective).encode("utf-8")) > byte_cap:
            if card in economic[:1]:
                raise BudgetError("feedback_required_context_over_budget")
            continue
        if token_cap is not None and estimate_tokens(canonical_json(prospective)) > token_cap:
            if card in economic[:1]:
                raise BudgetError("feedback_required_context_over_budget")
            continue
        selected = prospective
    return selected


def input_limit(stage: str) -> int:
    return STAGE_LIMITS[_stage(stage)][1]


def build_prompt_context(context, stage: str, *, current_hypothesis=None,
                         current_proposal=None, repair=None) -> dict:
    stage_key = _stage(stage)
    if stage_key not in STAGE_LIMITS:
        raise QualityError("Unknown LLM prompt stage " + stage)
    fields = context.fields
    proposal = (current_proposal.model_dump(mode="json") if hasattr(current_proposal, "model_dump")
                else current_proposal)
    hypothesis = (vars(current_hypothesis) if hasattr(current_hypothesis, "__dict__")
                  else current_hypothesis)
    if stage_key == "proposal" and isinstance(hypothesis, dict):
        hypothesis = {key: hypothesis.get(key, "") for key in ("hypothesis", "reason")}
    descriptors = getattr(context, "feature_descriptors", [])
    relevant = sorted(descriptors, key=lambda row: row.get("feature_id", "")) if descriptors else sorted(getattr(context, "existing_features", []))
    required_fields = (proposal or {}).get("required_fields", []) if isinstance(proposal, dict) else []
    selected = (select_cards(getattr(context, "feedback", []), current_fields=required_fields,
                             token_cap=context.runtime.get("memory_token_cap", 1_600))
                if stage_key == "hypothesis" else [])
    if stage_key in ("code", "repair"):
        allowed_fields = {name: fields[name] for name in sorted(required_fields) if name in fields}
        relevant = [row for row in relevant if isinstance(row, dict) and
                    row.get("feature_id") in set((proposal or {}).get("compared_features", []))]
    else:
        allowed_fields = {name: fields[name] for name in sorted(fields)}
    payload = {
        "schema_version": "prompt-context-v1", "stage": stage_key,
        "prompt_version": getattr(context, "prompt_version", "etf-factor-v1"),
        "projection_policy_version": PROMPT_POLICY_VERSION,
        "snapshot_id": context.snapshot_id, "protocol_id": context.protocol_id,
        "baseline_id": context.baseline_id, "full_context_hash": context.context_hash,
        "task_contract": {
            "causal_only": True, "forbid_labels": True, "forbid_final_acceptance_data": True,
            "one_factor": True, "no_execution_policy_change": True,
            "input_index_contract": "compute(panel) must return exactly panel.index in its incoming order; "
                                    "internal sorting requires a final reindex to panel.index",
            "allowed_libraries": ["numpy", "pandas", "math", "statistics"],
        },
        "qualification": _without_final_acceptance(context.selection_rules),
        "allowed_fields": allowed_fields,
        "relevant_features": relevant,
        "current_hypothesis": hypothesis,
        "current_proposal": proposal,
        "selected_memory_cards": selected if stage_key == "hypothesis" else None,
        "runtime_constraints": context.runtime,
        "output_schema": {"strict_json": True, "stage": stage_key,
                          "requested_max_output_tokens": output_limit(stage)},
    }
    assignment = context.selection_rules.get("campaign_mechanism_assignment")
    if isinstance(assignment, dict):
        payload["task_contract"]["campaign_mechanism_assignment"] = assignment
        payload["task_contract"]["required_research_group"] = assignment["research_group"]
    if stage_key == "hypothesis":
        links = []
        for card in selected:
            if card.get("economic_status") != "rejected" or card.get("comparable") is not True:
                continue
            summary = card.get("feedback_summary")
            summaries = summary.get("by_candidate", []) if isinstance(summary, dict) else []
            reasons = (summaries[0].get("decision_reasons", []) if summaries and
                       isinstance(summaries[0], dict) else [])
            reasons = sorted({reason for reason in reasons if isinstance(reason, str) and reason})
            if card.get("factor_id") and reasons:
                links.append({"factor_id": card["factor_id"], "reason_codes": reasons})
        if links:
            payload["hypothesis_feedback_requirement"] = {
                "must_address_one_comparable_rejection": True,
                "allowed_evidence_links": links,
                "unknown_evidence_is_not_a_fact": True,
            }
            payload["output_schema"]["required_feedback_fields"] = [
                "feedback_reference", "addressed_failure_code"]
    if repair is not None:
        payload["repair"] = repair
    if stage_key in ("code", "repair"):
        # Coding must not receive unrelated strategy results or historical
        # cards.  The current validated definition remains sufficient.
        payload.pop("qualification", None)
        payload.pop("relevant_features", None)
    # Avoid sending unrelated context: null entries are intentionally omitted.
    payload = {key: value for key, value in payload.items() if value not in (None, [], {})}
    required = REQUIRED_BY_STAGE[stage_key]
    missing = [key for key in required if key not in payload]
    if missing:
        raise QualityError("Prompt context missing required information: " + ", ".join(missing))
    encoded = canonical_json(payload)
    hard_limit = input_limit(stage_key) - SYSTEM_PROMPT_TOKEN_RESERVE
    while estimate_tokens(encoded) > hard_limit and payload.get("selected_memory_cards"):
        optional = [index for index, card in enumerate(payload["selected_memory_cards"])
                    if not _economic_card(card)]
        if not optional:
            raise BudgetError("prompt_required_context_over_budget for " + stage_key)
        payload["selected_memory_cards"].pop(optional[-1])
        if not payload["selected_memory_cards"]:
            payload.pop("selected_memory_cards")
        encoded = canonical_json(payload)
    if estimate_tokens(encoded) > hard_limit:
        raise BudgetError("context_budget_exceeded for " + stage_key)
    payload["payload_hash"] = content_hash(payload)
    payload["estimated_input_tokens"] = estimate_tokens(encoded)
    payload["tokenizer_version"] = TOKENIZER_VERSION
    return payload


def output_limit(stage: str) -> int:
    return STAGE_LIMITS[_stage(stage)][2]
