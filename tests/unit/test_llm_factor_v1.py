import json
from types import SimpleNamespace

import pytest

from etf_ml.errors import BudgetError, QualityError
import etf_ml.research.prompting as prompting
from etf_ml.research.context import FactorSpec, ResearchContext
from etf_ml.research.prompting import build_prompt_context, select_cards
from etf_ml.research.research_memory import build_card, exact_duplicate


def context(*, cards=None, selection_rules=None):
    return ResearchContext(
        snapshot_id="snapshot", baseline_id="baseline", protocol_id="protocol",
        visible_start="2020-01-01", visible_end="2020-12-31",
        fields={"adj_close": {"meaning": "adjusted close", "unit": "price",
                              "available_at": "after_daily_ingestion", "allowed_usage": "proposal"},
                "mystery": {"meaning": "unknown", "unit": "dimensionless",
                            "available_at": "after_daily_ingestion", "allowed_usage": "forbidden"}},
        existing_features=["momentum_5"], model={}, selection_rules=selection_rules or {"fixed": True},
        runtime={"maximum_lookback": 20}, feedback=cards or [], prompt_version="etf-factor-v2")


def proposal(**changes):
    value = {"factor_id": "short_trend", "hypothesis": "A three day trend may differ from momentum five",
             "reason": "A shorter horizon tests a distinct response speed.",
             "mechanism": "Short-horizon continuation can be different from five-day momentum.",
             "formula": "adj_close / adj_close.shift(3) - 1", "required_fields": ["adj_close"],
             "lookback": 4, "minimum_observations": 4, "direction": "unknown",
             "direction_reason": "The response sign is not assumed before testing.",
             "expected_difference": "three-day adjusted-close return versus momentum_5",
             "failure_conditions": ["coverage is below the fixed threshold"],
             "compared_features": ["momentum_5"], "research_group": "trend",
             "source": "def compute(panel): return panel['adj_close']", "context_hash": context().context_hash,
             "schema_version": 2}
    value.update(changes)
    return value


def test_stage_prompt_is_deterministic_and_keeps_required_semantics():
    first = build_prompt_context(context(), "proposal", current_hypothesis={"hypothesis": "h", "reason": "r"})
    second = build_prompt_context(context(), "proposal", current_hypothesis={"hypothesis": "h", "reason": "r"})
    assert first == second
    assert first["payload_hash"]
    assert first["allowed_fields"]["adj_close"]["meaning"] == "adjusted close"
    assert first["task_contract"]["forbid_final_acceptance_data"] is True


def test_prompt_rejects_required_context_that_exceeds_hard_budget():
    oversized = context()
    # The prompt budget is measured by the configured tokenizer, not a
    # character heuristic.  Repeated words create an unambiguously oversized
    # token stream for cl100k_base too.
    oversized.fields["adj_close"]["meaning"] = "diagnostic_token " * 10_000
    with pytest.raises(BudgetError, match="context_budget_exceeded"):
        build_prompt_context(oversized, "proposal", current_hypothesis={"hypothesis": "h", "reason": "r"})


def test_prompt_trims_optional_memory_before_budget_error(monkeypatch):
    card = {"factor_id": "old", "formula": "a+b", "required_fields": ["adj_close"]}
    monkeypatch.setattr(prompting, "estimate_tokens",
                        lambda value: 5_001 if '"selected_memory_cards"' in value else 1)
    prompt = build_prompt_context(context(cards=[card]), "proposal",
                                  current_hypothesis={"hypothesis": "h", "reason": "r"})
    assert "selected_memory_cards" not in prompt


def test_prompt_reserves_input_budget_for_system_prompt(monkeypatch):
    card = {"factor_id": "old", "formula": "a+b", "required_fields": ["adj_close"]}
    monkeypatch.setattr(prompting, "estimate_tokens",
                        lambda value: 4_900 if '"selected_memory_cards"' in value else 1)
    prompt = build_prompt_context(context(cards=[card]), "proposal",
                                  current_hypothesis={"hypothesis": "h", "reason": "r"})
    assert "selected_memory_cards" not in prompt


def test_proposal_prompt_projects_hypothesis_to_required_semantics():
    prompt = build_prompt_context(
        context(), "proposal",
        current_hypothesis={
            "hypothesis": "h", "reason": "r", "concise_reason": "duplicated r",
            "concise_justification": "duplicated r",
        },
    )
    assert prompt["current_hypothesis"] == {"hypothesis": "h", "reason": "r"}


@pytest.mark.parametrize("change", [
    {"expected_difference": "new information"},
    {"hypothesis": "short_trend"},
    {"research_group": "unclassified"},
    {"failure_conditions": []},
])
def test_v2_proposal_rejects_placeholder_or_missing_semantics(change):
    with pytest.raises(ValueError):
        FactorSpec.model_validate(proposal(**change))


def test_v2_proposal_rejects_unknown_field_but_old_identity_stays_readable():
    candidate = FactorSpec.model_validate(proposal(required_fields=["mystery"]))
    with pytest.raises(QualityError, match="unavailable"):
        candidate.validate_context(context())
    legacy = FactorSpec.model_validate({k: v for k, v in proposal(schema_version=1).items()
                                        if k not in {"schema_version", "reason", "mechanism", "direction_reason",
                                                     "failure_conditions", "compared_features"}})
    assert legacy.schema_version == 1 and legacy.version_id


def test_research_cards_select_failure_and_exact_duplicate_is_conservative():
    cards = [
        {"factor_id": "old", "formula": " a + b ", "required_fields": ["adj_close"],
         "status": "rejected", "reasons": ["risk_limit"], "next_action": "different"},
        {"factor_id": "other", "formula": "x", "required_fields": [], "status": "rejected"},
    ]
    selected = select_cards(cards, current_fields=["adj_close"])
    assert selected[0]["factor_id"] == "old" and selected[0]["status"] == "rejected"
    assert exact_duplicate("a+b", cards)["factor_id"] == "old"
    assert exact_duplicate("a-b", cards) is None


def test_latest_comparable_economic_card_beats_newer_incomparable_history():
    cards = [
        {"factor_id": "older_comparable", "formula": "a+b", "status": "rejected",
         "economic_status": "rejected", "comparable": True, "committed_order": [1, 1]},
        {"factor_id": "newer_other_protocol", "formula": "a-b", "status": "rejected",
         "economic_status": "rejected", "comparable": False, "committed_order": [2, 1]},
    ]
    selected = select_cards(cards, current_fields=["adj_close"])
    assert selected[0]["factor_id"] == "older_comparable"


def test_next_prompt_receives_compact_feedback_summary_from_committed_card():
    summary = {"schema_version": "feedback-summary-v2", "by_candidate": [{
        "factor_id": "old", "coverage": .91,
        "paired_deltas": [{"fold": "F", "seed": 1, "excess_return": -.01}],
        "next_action": "test_a_distinct_falsifiable_mechanism",
        "signal_to_execution": {"status": "partial", "by_fold": []},
    }]}
    card = build_card(hypothesis=SimpleNamespace(hypothesis="old hypothesis", reason="old reason"),
                      proposal={**proposal(), "factor_id": "old"},
                      feedback={"status": "rejected", "summary": summary,
                                "by_candidate": [{"status": "rejected", "reasons": ["no_increment"]}]},
                      context_hash="context", snapshot_id="snapshot", protocol_id="protocol",
                      baseline_id="baseline")
    card["evaluation_evidence_verified"] = True
    card["evaluation_evidence_hashes"] = [{"name": "evaluation.json", "sha256": "a" * 64}]
    prompt = build_prompt_context(context(cards=[card]), "hypothesis")
    recovered = prompt["selected_memory_cards"][0]["feedback_summary"]["by_candidate"][0]
    assert recovered["coverage"] == .91
    assert recovered["fold_deltas"][0]["median_excess_return"] == -.01
    assert recovered["next_action"] == "test_a_distinct_falsifiable_mechanism"
    assert "evaluation_evidence_hashes" not in prompt["selected_memory_cards"][0]


def test_oversized_stored_feedback_is_projected_not_silently_dropped():
    summary = {"schema_version": "feedback-summary-v2", "by_candidate": [{
        "factor_id": "old", "coverage": .91, "next_action": "test_a_distinct_falsifiable_mechanism",
        "paired_deltas": [{"fold": str(index % 5), "seed": index, "excess_return": .01}
                          for index in range(15)],
    }]}
    card = {"factor_id": "old", "formula": "a+b", "required_fields": ["adj_close"],
            "feedback_summary": summary}
    selected = select_cards([card], current_fields=["adj_close"], byte_cap=1_500)
    assert selected[0]["feedback_summary"]["by_candidate"][0]["coverage"] == .91
    assert len(selected[0]["feedback_summary"]["by_candidate"][0]["fold_deltas"]) == 5


def test_comparable_economic_feedback_fits_memory_cap_with_fold_and_risk_evidence():
    paired = [{"fold": "ABCDE"[index % 5], "seed": index // 5,
               "excess_return": .01, "max_drawdown": -.001, "turnover": .02,
               "annualized_volatility": .001} for index in range(15)]
    risks = [{"scenario": scenario, "fold": fold, "incremental_change": .001,
              "candidate_value": .13, "baseline_value": .14,
              "candidate_breach": True, "baseline_breach": True}
             for scenario in ("base", "2.0") for fold in "ABCDE"]
    card = {"factor_id": "rejected", "formula": "a+b", "mechanism": "a distinct testable mechanism",
            "required_fields": ["adj_close"], "status": "rejected", "economic_status": "rejected",
            "comparable": True, "reasons": ["risk_limit"], "next_action": "test a distinct mechanism",
            "feedback_summary": {"schema_version": "feedback-summary-v2", "status": "rejected",
                "by_candidate": [{"factor_id": "rejected", "status": "rejected",
                    "decision_reasons": ["risk_limit", "drawdown_deterioration"],
                    "median_excess_return_delta": .001, "delta_count": 15,
                    "candidate_max_drawdown": .15, "baseline_max_drawdown": .14,
                    "coverage": .99, "paired_deltas": paired,
                    "risk": {"mode": "max_drawdown", "limit": .12, "by_fold": risks}}]}}

    selected = select_cards([card])
    evidence = selected[0]["feedback_summary"]["by_candidate"][0]
    assert [row["fold"] for row in evidence["fold_deltas"]] == list("ABCDE")
    assert len(evidence["risk"]["by_scenario"]) == 2
    assert all(row["fold_count"] == 5 and row["candidate_breach_count"] == 5 and
               row["max_candidate_value"] == .13 and row["worst_candidate_fold"] == "A"
               for row in evidence["risk"]["by_scenario"])


def test_proposal_uses_hypothesis_stage_feedback_without_repeating_cards():
    card = {"factor_id": "rejected", "formula": "a+b", "status": "rejected",
            "economic_status": "rejected", "comparable": True, "required_fields": ["adj_close"]}
    proposal_prompt = build_prompt_context(
        context(cards=[card]), "proposal",
        current_hypothesis={"hypothesis": "h", "reason": "r"})
    hypothesis_prompt = build_prompt_context(context(cards=[card]), "hypothesis")
    assert "selected_memory_cards" not in proposal_prompt
    assert hypothesis_prompt["selected_memory_cards"][0]["factor_id"] == "rejected"


def test_required_economic_feedback_fails_closed_at_memory_token_cap():
    card = {"factor_id": "rejected", "formula": "a+b", "status": "rejected",
            "economic_status": "rejected", "comparable": True}
    with pytest.raises(BudgetError, match="feedback_required_context_over_budget"):
        select_cards([card], token_cap=1)


def test_rejected_risk_evidence_reaches_actual_next_hypothesis_request(tmp_path):
    """Exercise the adapter and guarded dispatch, not only an in-memory prompt builder."""
    from etf_ml.adapters.rdagent.proposal import ETFHypothesisGen
    from etf_ml.contracts import ResearchPolicy
    from etf_ml.adapters.rdagent.feedback import feedback_summary
    from etf_ml.research.llm import GuardedLLM, ReplayTransport
    from etf_ml.research.qualification import read_object

    reasons = ["risk_limit", "cost_stress_excess_return", "turnover_deterioration"]
    paired = [{"fold": fold, "seed": seed, "excess_return": -.01, "turnover": .02}
              for fold in "ABCDE" for seed in [42, 43, 44]]
    cost_stress = [{"fold": fold, "seed": seed, "multiplier": 2.,
                "baseline_excess_return": .01, "candidate_excess_return": -.02,
                "excess_return_delta": -.03, "baseline_execution_cost": 20.,
                "candidate_execution_cost": 25., "execution_cost_delta": 5.}
                for fold in "ABCDE" for seed in [42, 43, 44]]
    observations = {"data_quality": {"coverage": .99, "time_check": "passed"},
        "portfolio_metrics": {"by_fold": [{"fold": fold, "seed": seed,
            "baseline": {"max_drawdown": .14}, "candidate": {"max_drawdown": .15}}
            for fold in "ABCDE" for seed in [42, 43, 44]]},
        "robustness": {"cost_stress": [{"fold": row["fold"], "seed": row["seed"],
            "multiplier": row["multiplier"],
            "baseline": {"excess_return": row["baseline_excess_return"],
                         "max_drawdown": .14, "total_execution_cost": row["baseline_execution_cost"]},
            "candidate": {"excess_return": row["candidate_excess_return"],
                          "max_drawdown": .15, "total_execution_cost": row["candidate_execution_cost"]},
            "excess_return_delta": row["excess_return_delta"]} for row in cost_stress]},
        "signal_to_execution": {"status": "partial", "by_fold": [{"fold": fold,
            "rank_changed": 2, "rank_comparisons": 10, "candidate_execution_constrained": 1,
            "baseline_execution_constrained": 0, "missing_rank_predictions": 1,
            "missing_signal_ages": 0} for fold in "ABCDE"]}}
    summary = feedback_summary({"status": "rejected",
        "risk_policy": {"mode": "max_drawdown", "limit": .12},
        "by_candidate": [{"factor_id": "old", "status": "rejected", "reasons": reasons,
            "paired_deltas": paired, "observations": observations,
            "group_ablation": {"status": "rejected", "group": "trend",
                "reasons": ["ablation_not_confirmed"]},
            "group_paired_deltas": [{"fold": fold, "seed": seed, "excess_return": -.005}
                                    for fold in "ABCDE" for seed in [42, 43, 44]]}]})
    card = build_card(hypothesis={"hypothesis": "old hypothesis", "reason": "old reason"},
                      proposal={**proposal(), "factor_id": "old"},
                      feedback={"status": "rejected", "summary": summary,
                                "by_candidate": [{"status": "rejected", "reasons": reasons}]},
                      context_hash="context", snapshot_id="snapshot", protocol_id="protocol", baseline_id="baseline")

    class ReceivingReplay(ReplayTransport):
        def complete(self, prompt):
            self.received = json.loads(prompt["user_prompt"])
            return super().complete(prompt)

    transport = ReceivingReplay({"hypothesis": json.dumps({"hypothesis": "Test slower signal decay",
        "reason": "Respond to the measured cost failure with a distinct mechanism",
        "feedback_reference": "old", "addressed_failure_code": "cost_stress_excess_return"})})
    llm = GuardedLLM(tmp_path / "llm", ResearchPolicy(budget_mode="free_only"), transport)
    session = SimpleNamespace(context=context(cards=[card]), llm=llm)
    result = ETFHypothesisGen(SimpleNamespace(session=session)).gen(SimpleNamespace())
    assert result.hypothesis == "Test slower signal decay"
    assert "Evidence link: old/cost_stress_excess_return" in result.reason
    requirement = transport.received["hypothesis_feedback_requirement"]
    assert requirement["must_address_one_comparable_rejection"] is True
    assert requirement["unknown_evidence_is_not_a_fact"] is True
    evidence = transport.received["selected_memory_cards"][0]["feedback_summary"]["by_candidate"][0]
    assert evidence["decision_reasons"] == reasons
    assert evidence["candidate_max_drawdown"] == .15 and evidence["baseline_max_drawdown"] == .14
    assert [row["fold"] for row in evidence["fold_deltas"]] == list("ABCDE")
    assert all(row["negative_seed_count"] == 3 for row in evidence["fold_deltas"])
    assert evidence["signal_to_execution"]["status"] == "partial"
    assert evidence["signal_to_execution"]["summary"]["rank_changed"] == 10
    assert evidence["signal_to_execution"]["summary"]["missing_rank_predictions"] == 5
    assert evidence["cost_stress_by_fold"][0]["multiplier"] == 2.
    assert evidence["cost_stress_by_fold"][0]["median_excess_return_delta"] == -.03
    assert evidence["cost_stress_by_fold"][0]["median_execution_cost_delta"] == 5.
    assert evidence["cost_stress_by_fold"][0]["known_excess_return_delta_seeds"] == 3
    assert evidence["group_ablation"]["status"] == "rejected"
    assert evidence["group_ablation"]["fold_deltas"][0]["negative_seed_count"] == 3
    assert evidence["unknown_evidence"] == ["signal_to_execution_evidence_partial"]
    # This legacy boolean means the candidate summary is broadly unknown; granular gaps
    # are carried separately in unknown_evidence and must not be conflated with it.
    assert evidence["unknown"] is False
    assert evidence["risk"]["limit"] == .12
    requests = list((tmp_path / "llm" / "calls").glob("*/request.json"))
    assert len(requests) == 1
    assert json.loads(read_object(requests[0])["prompt"]["user_prompt"]) == transport.received
    assert len(llm.ledger.summary()["calls"]) == 1
    assert card["economic_status"] == "rejected"  # A hypothesis never rewrites a formal decision.


def test_hypothesis_feedback_link_must_match_a_comparable_rejection():
    from etf_ml.adapters.rdagent.proposal import hypothesis_from_response

    requirement = {"allowed_evidence_links": [
        {"factor_id": "rejected_factor", "reason_codes": ["risk_limit"]}]}
    with pytest.raises(QualityError, match="allowed rejection"):
        hypothesis_from_response(json.dumps({"hypothesis": "new idea", "reason": "because",
            "feedback_reference": "technical_failure", "addressed_failure_code": "risk_limit"}),
            require_reason=True, feedback_requirement=requirement)
    valid = hypothesis_from_response(json.dumps({"hypothesis": "new idea", "reason": "because",
        "feedback_reference": "rejected_factor", "addressed_failure_code": "risk_limit"}),
        require_reason=True, feedback_requirement=requirement)
    assert "Evidence link: rejected_factor/risk_limit" in valid.reason


def test_invalid_feedback_link_uses_bounded_repair_and_preserves_evidence_constraint(tmp_path):
    from etf_ml.adapters.rdagent.proposal import ETFHypothesisGen
    from etf_ml.contracts import ResearchPolicy
    from etf_ml.research.llm import GuardedLLM, ReplayTransport

    reasons = ["cost_stress_excess_return"]
    summary = {"schema_version": "feedback-summary-v2", "by_candidate": [{
        "decision_reasons": reasons}]}
    card = {"factor_id": "rejected_factor", "formula": "a+b", "required_fields": ["adj_close"],
            "status": "rejected", "economic_status": "rejected", "comparable": True,
            "feedback_summary": summary}

    class ReceivingReplay(ReplayTransport):
        def __init__(self, responses):
            super().__init__(responses)
            self.prompts = []

        def complete(self, prompt):
            self.prompts.append((prompt["stage"], json.loads(prompt["user_prompt"])))
            return super().complete(prompt)

    transport = ReceivingReplay({
        "hypothesis": json.dumps({"hypothesis": "new test", "reason": "bad citation",
            "feedback_reference": "other", "addressed_failure_code": reasons[0]}),
        "hypothesis_repair": json.dumps({"hypothesis": "new test", "reason": "fix cost robustness",
            "feedback_reference": "rejected_factor", "addressed_failure_code": reasons[0]}),
    })
    session = SimpleNamespace(context=context(cards=[card]),
        llm=GuardedLLM(tmp_path / "llm", ResearchPolicy(budget_mode="free_only"), transport),
        claim_repair=lambda stage: stage == "hypothesis")
    result = ETFHypothesisGen(SimpleNamespace(session=session)).gen(SimpleNamespace())
    assert "Evidence link: rejected_factor/cost_stress_excess_return" in result.reason
    assert [stage for stage, _ in transport.prompts] == ["hypothesis", "hypothesis_repair"]
    repair_requirement = transport.prompts[1][1]["repair"]["hypothesis_feedback_requirement"]
    assert repair_requirement["allowed_evidence_links"] == [
        {"factor_id": "rejected_factor", "reason_codes": reasons}]


def test_hypothesis_prompt_does_not_promote_technical_or_unknown_failures_to_economic_evidence():
    technical = {"factor_id": "broken", "status": "failed", "economic_status": None,
                 "comparable": True, "feedback_summary": {"by_candidate": [{
                     "decision_reasons": ["runtime_error"]}]}}
    unknown = {"factor_id": "partial", "status": "rejected", "economic_status": "rejected",
               "comparable": True, "feedback_summary": {"by_candidate": [{"decision_reasons": []}]}}
    prompt = build_prompt_context(context(cards=[technical, unknown]), "hypothesis")
    assert "hypothesis_feedback_requirement" not in prompt


def test_code_prompt_uses_minimal_implementation_projection():
    prompt = build_prompt_context(context(cards=[{"factor_id": "old", "formula": "a+b"}]),
                                  "code:short_trend", current_proposal=proposal())
    assert set(prompt["allowed_fields"]) == {"adj_close"}
    assert prompt["output_schema"]["requested_max_output_tokens"] == 2500
    assert "qualification" not in prompt
    assert "relevant_features" not in prompt
    assert "selected_memory_cards" not in prompt


def test_code_prompt_does_not_select_or_budget_irrelevant_economic_memory():
    oversized = {"factor_id": "economic", "formula": "a+b", "status": "rejected",
                 "economic_status": "rejected", "comparable": True,
                 "feedback_summary": {"by_candidate": [{"paired_deltas": [
                     {"fold": str(i % 5), "seed": i, "excess_return": -0.01}
                     for i in range(1000)]}]}}
    prompt = build_prompt_context(context(cards=[oversized]), "code:short_trend",
                                  current_proposal=proposal())
    assert "selected_memory_cards" not in prompt


def test_card_selection_guarantees_two_most_recent_committed_cards():
    cards = [{"factor_id": f"old_{number}", "formula": str(number), "required_fields": [],
              "committed_order": [1, number]} for number in range(5)]
    cards.extend([
        {"factor_id": "recent_a", "formula": "recent-a", "required_fields": [], "committed_order": [2, 0]},
        {"factor_id": "recent_b", "formula": "recent-b", "required_fields": [], "committed_order": [3, 0]},
    ])
    selected = select_cards(cards, current_fields=["adj_close"])
    assert {row["factor_id"] for row in selected[:2]} == {"recent_a", "recent_b"}


def test_v2_proposal_context_includes_hypothesis_reason_without_placeholder_format():
    from etf_ml.adapters.rdagent.proposal import ETFHypothesis2Experiment
    hypothesis = SimpleNamespace(hypothesis="short trend", reason="A concise economic mechanism")
    trace = SimpleNamespace(scen=SimpleNamespace(session=SimpleNamespace(context=context())))
    payload, _ = ETFHypothesis2Experiment().prepare_context(hypothesis, trace)
    assert payload["current_hypothesis"]["reason"] == "A concise economic mechanism"
    assert "economics" not in json.dumps(payload["output_schema"])


def test_frozen_campaign_mechanism_is_required_in_prompt_and_proposal(monkeypatch):
    import etf_ml.adapters.rdagent.proposal as module
    assignment = {"slot": "volume_price", "research_group": "volume_price",
                  "description": "price-volume interaction"}
    c = context(selection_rules={"campaign_mechanism_assignment": assignment})
    prompt = build_prompt_context(c, "proposal", current_hypothesis={"hypothesis": "h", "reason": "r"})
    assert prompt["task_contract"]["required_research_group"] == "volume_price"
    assert prompt["task_contract"]["campaign_mechanism_assignment"] == assignment

    class Experiment:
        def __init__(self, session, tasks, hypothesis=None):
            self.session, self.sub_tasks, self.hypothesis = session, tasks, hypothesis
            self.based_experiments = []

    monkeypatch.setattr(module, "ETFExperiment", Experiment)
    session = SimpleNamespace(context=c, next_version=lambda _: 1,
                              protocol=SimpleNamespace(protocol_id="protocol"))
    trace = SimpleNamespace(scen=SimpleNamespace(session=session), hist=[])
    response = json.dumps({"factors": [{key: value for key, value in proposal(research_group="trend").items()
                                         if key not in {"source", "context_hash", "schema_version", "reason"}}]})
    with pytest.raises(QualityError, match="frozen campaign mechanism slot"):
        module.ETFHypothesis2Experiment().convert_response(
            response, SimpleNamespace(hypothesis="h", reason="r"), trace)


def test_v2_replay_proposal_contract_is_accepted_before_coder(monkeypatch):
    import etf_ml.adapters.rdagent.proposal as module
    class Experiment:
        def __init__(self, session, tasks, hypothesis=None):
            self.session, self.sub_tasks, self.hypothesis = session, tasks, hypothesis
            self.based_experiments = []
    monkeypatch.setattr(module, "ETFExperiment", Experiment)
    c = context()
    session = SimpleNamespace(context=c, next_version=lambda _: 1, protocol=SimpleNamespace(protocol_id="protocol"))
    trace = SimpleNamespace(scen=SimpleNamespace(session=session), hist=[])
    response = json.dumps({"factors": [{key: value for key, value in proposal().items()
                                         if key not in {"source", "context_hash", "schema_version", "reason"}}]})
    result = module.ETFHypothesis2Experiment().convert_response(
        response, SimpleNamespace(hypothesis="short trend", reason="replay reason"), trace)
    assert result.sub_tasks[0].proposal.schema_version == 2


def test_noncomparable_duplicate_is_retrieved_but_does_not_block_new_evaluation(monkeypatch):
    import etf_ml.adapters.rdagent.proposal as module

    class Experiment:
        def __init__(self, session, tasks, hypothesis=None):
            self.session, self.sub_tasks, self.hypothesis = session, tasks, hypothesis
            self.based_experiments = []

    monkeypatch.setattr(module, "ETFExperiment", Experiment)
    c = context(cards=[{"factor_id": "old", "formula": proposal()["formula"], "status": "rejected",
                       "comparable": False}])
    session = SimpleNamespace(context=c, next_version=lambda _: 1, protocol=SimpleNamespace(protocol_id="protocol"))
    trace = SimpleNamespace(scen=SimpleNamespace(session=session), hist=[])
    response = json.dumps({"factors": [{key: value for key, value in proposal().items()
                                         if key not in {"source", "context_hash", "schema_version", "reason"}}]})
    result = module.ETFHypothesis2Experiment().convert_response(
        response, SimpleNamespace(hypothesis="short trend", reason="replay reason"), trace)
    assert result.sub_tasks[0].name == "short_trend"
