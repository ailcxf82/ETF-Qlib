from __future__ import annotations

import json

from rdagent.core.developer import Developer

from etf_ml.adapters.rdagent.experiment import ETFExperiment
from etf_ml.errors import ETFError, QualityError, BudgetError, ValidationFailure
from etf_ml.research.code_checks import validate_source
from etf_ml.research.prompting import build_prompt_context
from etf_ml.utils import canonical_json, content_hash


CODE_SYSTEM = (
    "Return JSON with source: one Python compute(panel) function. Output one numeric column whose index "
    "equals the incoming panel.index exactly, including its order. Do not sort the input and return that "
    "sorted index: if sorting is needed internally for a temporal calculation, reindex the final result to "
    "panel.index before returning. For temporal windows use "
    "groupby(level='instrument').transform(lambda x: x.rolling(...).<operation>()) so the original "
    "two-level index is retained. Never call groupby(...).rolling(...) directly because it adds an index level. "
    "No files, network, labels, future data access, or dynamic builtins such as getattr/setattr. "
    "Because source validation rejects forbidden-data terms even in docstrings, comments, and string literals, "
    "do not emit the words label or holdout anywhere in the generated source."
)


class ETFFactorCoder(Developer):
    def develop(self, exp):
        if not isinstance(exp, ETFExperiment):
            raise QualityError("ETF coder cannot execute a stock experiment")
        if exp.session.protocol.protocol_id != self.scen.session.protocol.protocol_id:
            raise QualityError("ETF coder experiment protocol mismatch")
        self.scen.get_runtime_environment()
        for task, workspace in zip(exp.sub_tasks, exp.sub_workspace_list):
            source = None
            try:
                failure = None
                for repair_attempt in range(2):
                    stage = "code:" + task.name if repair_attempt == 0 else "code_repair:" + task.name
                    v2 = getattr(workspace.context, "prompt_version", "etf-factor-v1") == "etf-factor-v2"
                    prompt = (build_prompt_context(workspace.context, stage, current_proposal=task.proposal)
                              if v2 else {"context": workspace.context, "factor": task.proposal,
                                          "allowed_libraries": ["numpy", "pandas", "math", "statistics"]})
                    if repair_attempt:
                        if hasattr(self.scen.session, "claim_repair") and not self.scen.session.claim_repair("code"):
                            raise QualityError("Repair quota exhausted for code")
                        repair = {"source": source, "failure": failure.prompt_payload(),
                                  "instruction": "Return corrected JSON only. Preserve the factor's intended "
                                                 "causal definition and the incoming panel.index exactly, including "
                                                 "order. If computation sorts internally, reindex its final output "
                                                 "to panel.index before returning."}
                        prompt = (build_prompt_context(workspace.context, stage, current_proposal=task.proposal,
                                                       repair=repair) if v2 else {**prompt, "original_source": source,
                                                       "validation_error": validation_error,
                                                       "instruction": repair["instruction"]})
                    text = self.scen.session.llm.complete(
                        stage=stage,
                        system_prompt=CODE_SYSTEM,
                        user_prompt=canonical_json(prompt),
                        request_identity={"context_hash": workspace.context.context_hash,
                                          "factor_id": task.name, "version": task.version,
                                          "repair_attempt": repair_attempt,
                                          **({"original_source_hash": content_hash(source)} if repair_attempt else {})},
                    )
                    try:
                        payload = json.loads(text)
                        source = payload.get("source")
                        if not isinstance(source, str) or not source:
                            raise ValidationFailure(category="json_format", check_id="coder_source",
                                                    message="Coder did not return a compute implementation")
                        try:
                            validate_source(source)
                        except QualityError as exc:
                            message = str(exc)
                            fatal = any(marker in message.lower() for marker in
                                        ("future", "forbidden", "file access", "dynamic execution", "centered"))
                            raise ValidationFailure(category="causal_violation" if fatal else "syntax",
                                                    check_id="source_validation", message=message,
                                                    recoverable=not fatal) from exc
                        workspace.inject_files(**{"factor.py": source})
                        workspace.execute()
                        break
                    except ValidationFailure as exc:
                        failure = exc
                    except (ValueError, KeyError, TypeError) as exc:
                        failure = ValidationFailure(category="json_format", check_id="coder_response",
                                                    message="Coder response is not valid JSON", recoverable=True)
                    if not failure.recoverable or repair_attempt:
                        raise failure
            except BudgetError:
                raise
            except (ETFError, ValueError, KeyError) as exc:
                task.failure = {"status": "failed", "reason": getattr(exc, "reason", "invalid_factor"),
                                "exception_type": type(exc).__name__}
        return exp
