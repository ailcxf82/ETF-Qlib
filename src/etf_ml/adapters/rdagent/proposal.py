from __future__ import annotations

import json

from rdagent.core.proposal import Hypothesis, HypothesisGen, Hypothesis2Experiment

from etf_ml.adapters.rdagent.experiment import ETFExperiment, ETFFactorTask, FactorProposal
from etf_ml.errors import QualityError
from etf_ml.utils import canonical_json, content_hash

SYSTEM = ("You propose causally available ETF features for the fixed project protocol. "
          "Return strict JSON. Never read labels, change execution policy or use final acceptance data.")
HYPOTHESIS_SYSTEM = (SYSTEM + " Return an object with a single nonempty hypothesis string and a reason string. "
                     "Do not return a list of factors.")
PROPOSAL_SYSTEM = (SYSTEM + " Return exactly one factor only. Each factor must include factor_id, "
                   "hypothesis, formula, required_fields, lookback, minimum_observations and "
                   "expected_difference. Do not include source code.")


def hypothesis_from_response(text):
    payload = json.loads(text)
    if not isinstance(payload.get("hypothesis"), str) or not payload["hypothesis"].strip():
        raise QualityError("ETF research hypothesis must be a nonempty string")
    reason = payload.get("reason", "")
    if not isinstance(reason, str):
        raise QualityError("ETF research reason must be a string")
    return Hypothesis(hypothesis=payload["hypothesis"], reason=reason,
                      concise_reason=payload.get("concise_reason", reason),
                      concise_observation=payload.get("concise_observation", ""),
                      concise_justification=payload.get("concise_justification", reason),
                      concise_knowledge=payload.get("concise_knowledge", ""))


class ETFHypothesisGen(HypothesisGen):
    def gen(self, trace, plan=None):
        session = self.scen.session
        text = session.llm.complete(stage="hypothesis",
            system_prompt=HYPOTHESIS_SYSTEM,
            user_prompt=self.scen.get_scenario_all_desc() + "\nReturn an object with hypothesis and reason.",
            request_identity={"context_hash": session.context.context_hash})
        try:
            return hypothesis_from_response(text)
        except (QualityError, ValueError, KeyError, TypeError) as exc:
            repair = session.llm.complete(
                stage="hypothesis_repair",
                system_prompt=HYPOTHESIS_SYSTEM,
                user_prompt=canonical_json({
                    "original_response": text,
                    "validation_error": str(exc),
                    "instruction": "Return a corrected JSON object only. hypothesis must be one concise "
                                   "nonempty string, with an optional reason string.",
                }),
                request_identity={"context_hash": session.context.context_hash,
                                  "original_response_hash": content_hash(text),
                                  "repair_attempt": 1},
            )
            return hypothesis_from_response(repair)


class ETFHypothesis2Experiment(Hypothesis2Experiment):
    def prepare_context(self, hypothesis, trace):
        return {"target_hypothesis": hypothesis.hypothesis,
                "scenario": trace.scen.get_scenario_all_desc(),
                "experiment_output_format": {"factors": [
                    {"factor_id": "name", "hypothesis": "economics", "formula": "algorithm",
                     "required_fields": ["adj_close"], "lookback": 20,
                     "minimum_observations": 20, "expected_difference": "new information"}]}}, True

    def convert(self, hypothesis, trace):
        context, _ = self.prepare_context(hypothesis, trace)
        session = trace.scen.session
        text = session.llm.complete(stage="proposal", system_prompt=PROPOSAL_SYSTEM,
            user_prompt=canonical_json(context),
            request_identity={"context_hash": session.context.context_hash,
                              "hypothesis": hypothesis.hypothesis})
        try:
            return self.convert_response(text, hypothesis, trace)
        except (QualityError, ValueError, KeyError, TypeError) as exc:
            # A syntactically successful provider reply can still violate the
            # frozen proposal envelope (for example, returning four factors).
            # Give the provider one separately identified repair request rather
            # than silently truncating or accepting an invalid proposal.
            repair = session.llm.complete(
                stage="proposal_repair",
                system_prompt=PROPOSAL_SYSTEM,
                user_prompt=canonical_json({
                    "original_response": text,
                    "validation_error": str(exc),
                    "instruction": "Return a corrected JSON object only. Preserve the intended "
                                   "causal proposals where possible, but satisfy every requirement.",
                }),
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
        for item in factors:
            item = dict(item)
            if item.get("source"):
                raise QualityError("Implementation must come from the isolated coder stage")
            item["context_hash"] = session.context.context_hash
            item["version"] = session.next_version(item["factor_id"])
            proposal = FactorProposal.model_validate(item)
            proposal.validate_context(session.context)
            tasks.append(ETFFactorTask(proposal))
        if len({t.name for t in tasks}) != len(tasks):
            raise QualityError("Duplicate factor proposal names")
        exp = ETFExperiment(session, tasks, hypothesis=hypothesis)
        # Both the empty baseline and prior accepted experiments are project ETF workspaces.
        exp.based_experiments = [ETFExperiment(session, [])] + [
            previous for previous, feedback in trace.hist
            if bool(feedback) and isinstance(previous, ETFExperiment) and
               previous.session.protocol.protocol_id == session.protocol.protocol_id]
        return exp

