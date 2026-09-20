from __future__ import annotations

import json

from rdagent.core.developer import Developer

from etf_ml.adapters.rdagent.experiment import ETFExperiment
from etf_ml.errors import ETFError, QualityError, BudgetError
from etf_ml.research.code_checks import validate_source
from etf_ml.utils import canonical_json, content_hash


CODE_SYSTEM = (
    "Return JSON with source: one Python compute(panel) function. Output one numeric column and preserve "
    "the full sorted (datetime, instrument) index exactly. For temporal windows use "
    "groupby(level='instrument').transform(lambda x: x.rolling(...).<operation>()) so the original "
    "two-level index is retained. Never call groupby(...).rolling(...) directly because it adds an index level. "
    "No files, network, labels or future data access."
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
                for repair_attempt in range(2):
                    stage = "code:" + task.name if repair_attempt == 0 else "code_repair:" + task.name
                    prompt = {"context": workspace.context, "factor": task.proposal,
                              "allowed_libraries": ["numpy", "pandas", "math", "statistics"]}
                    if repair_attempt:
                        prompt.update({
                            "original_source": source,
                            "validation_error": validation_error,
                            "instruction": "Return corrected JSON only. Preserve the factor's intended "
                                           "causal definition and the original two-level index exactly.",
                        })
                    text = self.scen.session.llm.complete(
                        stage=stage,
                        system_prompt=CODE_SYSTEM,
                        user_prompt=canonical_json(prompt),
                        request_identity={"context_hash": workspace.context.context_hash,
                                          "factor_id": task.name, "version": task.version,
                                          "repair_attempt": repair_attempt,
                                          **({"original_source_hash": content_hash(source)} if repair_attempt else {})},
                    )
                    source = json.loads(text).get("source")
                    if not isinstance(source, str) or not source:
                        validation_error = "Coder did not return a compute implementation"
                    else:
                        try:
                            validate_source(source)
                        except QualityError as exc:
                            validation_error = str(exc)
                        else:
                            workspace.inject_files(**{"factor.py": source})
                            workspace.execute()
                            break
                    if repair_attempt:
                        raise QualityError(validation_error)
            except BudgetError:
                raise
            except (ETFError, ValueError, KeyError) as exc:
                task.failure = {"status": "failed", "reason": getattr(exc, "reason", "invalid_factor"),
                                "exception_type": type(exc).__name__}
        return exp
