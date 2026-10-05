from __future__ import annotations

import json

from rdagent.core.proposal import Hypothesis, HypothesisGen, Hypothesis2Experiment

from etf_ml.adapters.rdagent.experiment import ETFExperiment, ETFFactorTask, FactorProposal
from etf_ml.errors import DuplicateProposal, QualityError
from etf_ml.research.factor_identity import identify_definition
from etf_ml.research.prompting import build_prompt_context
from etf_ml.research.search_policy import decide_admission
from etf_ml.utils import atomic_json, canonical_json, content_hash

SYSTEM = ("You propose causally available ETF features for the fixed project protocol. "
          "Return strict JSON. Never read labels, change execution policy or use final acceptance data.")
HYPOTHESIS_SYSTEM = (SYSTEM + " Return an object with a single nonempty hypothesis string and a reason string. "
                     "Do not return a list of factors.")
PROPOSAL_SYSTEM = (SYSTEM + ' Return exactly one JSON envelope: {"factors":[{...}]}. The factors array must contain exactly one factor. '
                   'Do not return a bare factor and do not wrap this envelope in another key. Each factor must include factor_id, '
                   'hypothesis, reason, mechanism, formula, required_fields, lookback, minimum_observations, direction, '
                   'direction_reason, expected_difference, failure_conditions, compared_features and research_group. '
                   'failure_conditions must be a JSON list of strings. minimum_observations must be a positive integer '
                   'no greater than lookback. '
                   'direction must be exactly one of the strings "positive", "negative" or "unknown"; never use a numeric sign. '
                   'Do not include source code. Do not use placeholder text such as economics, algorithm, new information or unclassified.')


def hypothesis_from_response(text, *, require_reason=False, feedback_requirement=None):
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise QualityError("ETF research hypothesis response must be a JSON object")
    if not isinstance(payload.get("hypothesis"), str) or not payload["hypothesis"].strip():
        raise QualityError("ETF research hypothesis must be a nonempty string")
    reason = payload.get("reason", "")
    if not isinstance(reason, str) or (require_reason and not reason.strip()):
        raise QualityError("ETF research reason must be a string")
    if feedback_requirement:
        reference, code = payload.get("feedback_reference"), payload.get("addressed_failure_code")
        allowed = feedback_requirement.get("allowed_evidence_links", [])
        valid = any(row.get("factor_id") == reference and code in row.get("reason_codes", [])
                    for row in allowed if isinstance(row, dict))
        if not valid:
            raise QualityError("ETF research hypothesis must cite one allowed rejection and failure code")
        reason = reason.rstrip() + f"\nEvidence link: {reference}/{code}"
    return Hypothesis(hypothesis=payload["hypothesis"], reason=reason,
                      concise_reason=payload.get("concise_reason", reason),
                      concise_observation=payload.get("concise_observation", ""),
                      concise_justification=payload.get("concise_justification", reason),
                      concise_knowledge=payload.get("concise_knowledge", ""))


class ETFHypothesisGen(HypothesisGen):
    def gen(self, trace, plan=None):
        session = self.scen.session
        v2 = getattr(session.context, "prompt_version", "etf-factor-v1") == "etf-factor-v2"
        prompt_context = build_prompt_context(session.context, "hypothesis") if v2 else None
        requirement = prompt_context.get("hypothesis_feedback_requirement") if prompt_context else None
        prompt = (canonical_json(prompt_context) if v2 else
                  self.scen.get_scenario_all_desc() + "\nReturn an object with hypothesis and reason.")
        system_prompt = HYPOTHESIS_SYSTEM
        if requirement:
            system_prompt += (" A comparable economic rejection is present. You must address exactly one listed "
                              "factor/reason pair; return feedback_reference and addressed_failure_code exactly "
                              "as listed. Do not treat unknown evidence as a fact.")
        text = session.llm.complete(stage="hypothesis",
            system_prompt=system_prompt,
            user_prompt=prompt,
            request_identity={"context_hash": session.context.context_hash})
        try:
            return hypothesis_from_response(text, require_reason=v2,
                                            feedback_requirement=requirement)
        except (QualityError, ValueError, KeyError, TypeError) as exc:
            if hasattr(session, "claim_repair") and not session.claim_repair("hypothesis"):
                raise QualityError("Repair quota exhausted for hypothesis") from exc
            instruction = "Return corrected JSON only. hypothesis and reason must be nonempty."
            if requirement:
                instruction += (" Include feedback_reference and addressed_failure_code matching one exact "
                                "allowed evidence link. Do not treat unknown evidence as a fact.")
            repair_context = ({"original_response": text, "validation_error": str(exc),
                               "instruction": instruction}
                              if not v2 else build_prompt_context(session.context, "hypothesis_repair", repair={
                                  "original_response": text, "validation_error": str(exc),
                                  "instruction": instruction,
                                  **({"hypothesis_feedback_requirement": requirement} if requirement else {})}))
            repair = session.llm.complete(
                stage="hypothesis_repair",
                system_prompt=system_prompt,
                user_prompt=canonical_json(repair_context),
                request_identity={"context_hash": session.context.context_hash,
                                  "original_response_hash": content_hash(text),
                                  "repair_attempt": 1},
            )
            return hypothesis_from_response(repair, require_reason=v2,
                                            feedback_requirement=requirement)


class ETFHypothesis2Experiment(Hypothesis2Experiment):
    def prepare_context(self, hypothesis, trace):
        context = trace.scen.session.context
        if getattr(context, "prompt_version", "etf-factor-v1") == "etf-factor-v2":
            return build_prompt_context(context, "proposal", current_hypothesis=hypothesis), True
        return {"target_hypothesis": hypothesis.hypothesis,
                "hypothesis_reason": getattr(hypothesis, "reason", ""),
                "scenario": trace.scen.get_scenario_all_desc(),
                "experiment_output_format": {"factors": [
                    {"factor_id": "example_name", "hypothesis": "concrete_testable_mechanism", "formula": "formula using allowed fields",
                     "required_fields": ["adj_close"], "lookback": 20,
                     "minimum_observations": 20, "expected_difference": "specific difference from named baseline feature"}]}}, True

    def convert(self, hypothesis, trace):
        context, _ = self.prepare_context(hypothesis, trace)
        session = trace.scen.session
        text = session.llm.complete(stage="proposal", system_prompt=PROPOSAL_SYSTEM,
            user_prompt=canonical_json(context),
            request_identity={"context_hash": session.context.context_hash,
                              "hypothesis": hypothesis.hypothesis,
                              "hypothesis_reason_hash": content_hash(getattr(hypothesis, "reason", ""))})
        try:
            return self.convert_response(text, hypothesis, trace)
        except (QualityError, ValueError, KeyError, TypeError) as exc:
            if hasattr(session, "claim_repair") and not session.claim_repair("proposal"):
                raise QualityError("Repair quota exhausted for proposal") from exc
            # A syntactically successful provider reply can still violate the
            # frozen proposal envelope (for example, returning four factors).
            # Give the provider one separately identified repair request rather
            # than silently truncating or accepting an invalid proposal.
            repair_payload = {"original_response": text, "validation_error": str(exc),
                              "instruction": "Return a corrected JSON object only, using exactly the envelope "
                              "{\"factors\":[{...}]} with exactly one factor. Preserve the intended causal proposal "
                              "where possible, but satisfy every requirement."}
            if getattr(session.context, "prompt_version", "etf-factor-v1") == "etf-factor-v2":
                repair_payload = build_prompt_context(session.context, "proposal_repair",
                                                      current_hypothesis=hypothesis, repair=repair_payload)
            if hasattr(session, "root"):
                atomic_json(session.root / "llm" / "repair_history" /
                            ("proposal-" + content_hash({"context": session.context.context_hash,
                                                         "response": text})[:16] + ".json"),
                            {"stage": "proposal", "validation_error": str(exc),
                             "repair_payload": repair_payload})
            repair = session.llm.complete(
                stage="proposal_repair",
                system_prompt=PROPOSAL_SYSTEM,
                user_prompt=canonical_json(repair_payload),
                request_identity={"context_hash": session.context.context_hash,
                                  "hypothesis": hypothesis.hypothesis,
                                  "original_response_hash": content_hash(text),
                                  "repair_attempt": 1},
            )
            return self.convert_response(repair, hypothesis, trace)

    def convert_response(self, response, hypothesis, trace):
        session = trace.scen.session
        payload = json.loads(response)
        factors = payload.get("factors")
        if not isinstance(factors, list) or len(factors) != 1:
            raise QualityError("Each ETF experiment must propose exactly one factor")
        tasks = []
        mechanism_assignment = session.context.selection_rules.get("campaign_mechanism_assignment")
        for item in factors:
            item = dict(item)
            if (isinstance(mechanism_assignment, dict) and
                    item.get("research_group") != mechanism_assignment.get("research_group")):
                raise QualityError("Factor research_group must match the frozen campaign mechanism slot "
                                   + str(mechanism_assignment.get("slot")))
            if "trusted_implementation" in item:
                raise QualityError("LLM proposals cannot select a trusted library implementation")
            if item.get("source"):
                raise QualityError("Implementation must come from the isolated coder stage")
            item["context_hash"] = session.context.context_hash
            item["version"] = session.next_version(item["factor_id"])
            if getattr(session.context, "prompt_version", "etf-factor-v1") == "etf-factor-v2":
                item["schema_version"] = 2
                item["reason"] = getattr(hypothesis, "reason", "")
            proposal = FactorProposal.model_validate(item)
            proposal.validate_context(session.context)
            identity = identify_definition(proposal, session.context.fields)
            decision = decide_admission(identity, getattr(session.context, "feedback", []),
                                        snapshot_id=session.context.snapshot_id,
                                        baseline_id=session.context.baseline_id,
                                        protocol_id=session.context.protocol_id,
                                        reuse_context_id=session.context.selection_rules.get("reuse_context_id"),
                                        attempted_trials=getattr(session, "attempted_trials", 1))
            # This is deliberately not a QualityError: only malformed provider
            # output may consume proposal_repair capacity.
            if decision.decision in {"reuse", "wait_existing", "blocked"}:
                error = DuplicateProposal("Dedup admission " + decision.decision + ": " +
                                         ",".join(decision.reason_codes))
                error.decision = decision.to_dict()
                error.proposal = proposal.model_dump(mode="json")
                raise error
            task = ETFFactorTask(proposal)
            task.definition_identity, task.dedup_decision = identity, decision
            tasks.append(task)
        if len({t.name for t in tasks}) != len(tasks):
            raise QualityError("Duplicate factor proposal names")
        exp = ETFExperiment(session, tasks, hypothesis=hypothesis)
        # Both the empty baseline and prior accepted experiments are project ETF workspaces.
        exp.based_experiments = [ETFExperiment(session, [])] + [
            previous for previous, feedback in trace.hist
            if bool(feedback) and isinstance(previous, ETFExperiment) and
               previous.session.protocol.protocol_id == session.protocol.protocol_id]
        return exp

