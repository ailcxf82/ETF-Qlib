from __future__ import annotations

import pandas as pd
from rdagent.core.developer import Developer

from etf_ml.adapters.rdagent.experiment import ETFExperiment
from etf_ml.errors import ETFError, QualityError
from etf_ml.research.paired import combine_features
from etf_ml.research.execution import execute_research
from etf_ml.utils import atomic_json


class ETFFactorRunner(Developer):
    def develop(self, exp):
        if not isinstance(exp, ETFExperiment):
            raise QualityError("ETF runner cannot execute a stock experiment")
        session = self.scen.session
        if exp.session.protocol.protocol_id != session.protocol.protocol_id:
            raise QualityError("ETF runner experiment protocol mismatch")
        if not exp.sub_tasks:
            exp.result = exp.experiment_workspace.execute()
            return exp
        rows = []
        for task, workspace in zip(exp.sub_tasks, exp.sub_workspace_list):
            if task.failure is not None or task.artifact is None:
                rows.append({"factor_id": task.name, "status": "failed", "failure_stage": "factor_validation",
                             "reasons": [task.failure["reason"] if task.failure else "factor_not_validated"]})
                continue
            run_id = exp.experiment_id + "-" + task.name[:24]
            try:
                combined = combine_features(session.baseline, task.artifact, session.protocol)
                feature_path = workspace.workspace_path / "combined_factors_df.parquet"
                pd.concat({"feature": combined.frame}, axis=1).to_parquet(feature_path)
                result = execute_research(session, task.artifact, run_id=run_id)
                proof = session.root / "paired" / "runs" / run_id / "evaluation.json"
                current = session.registry.load(task.name, task.version)
                if current["state"] == "validated":
                    current = session.registry.transition(task.name, task.version, "evaluated",
                                                         evidence={"evaluation": proof})
                if current["state"] == "evaluated" and result["status"] == "accepted":
                    session.registry.transition(task.name, task.version, "candidate",
                                                evidence={"evaluation": proof})
                elif current["state"] == "evaluated" and result["status"] in ("rejected", "failed"):
                    session.registry.transition(task.name, task.version, "rejected",
                        evidence={"evaluation": proof}, reasons=result["evaluation"]["reasons"])
                rows.append({"factor_id": task.name, "factor_version_id": task.spec.version_id,
                             "direction": task.spec.direction, "applicable_scope": task.spec.applicable_scope,
                             **result})
            except ETFError as exc:
                rows.append({"factor_id": task.name, "status": "failed", "failure_stage": "model_evaluation",
                             "reasons": [exc.reason]})
        statuses = [row["status"] for row in rows]
        status = ("accepted" if "accepted" in statuses else
                  "inconclusive" if "inconclusive" in statuses else
                  "rejected" if "rejected" in statuses else "failed")
        exp.result = {"status": status, "stage": "factor_selection",
                      "protocol_id": session.protocol.protocol_id, "by_candidate": rows}
        exp.experiment_workspace.prepare()
        atomic_json(exp.experiment_workspace.workspace_path / "feedback_input.json", exp.result)
        return exp
