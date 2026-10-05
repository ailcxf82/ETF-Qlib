from __future__ import annotations

import copy
from pathlib import Path

import pandas as pd
from rdagent.core.experiment import Experiment, FBWorkspace, Task, Workspace

from etf_ml.artifacts import RunStore
from etf_ml.errors import ConfigurationError, ETFError, QualityError
from etf_ml.research.execution import execute_baseline
from etf_ml.research.context import FactorSpec
from etf_ml.research.paired import _seed_model
from etf_ml.utils import atomic_json, content_hash, ensure_within


class FactorProposal(FactorSpec):
    # Metadata is validated before implementation. Only a complete FactorSpec
    # with nonempty source may enter execution or the immutable registry.
    source: str = ""
    context_hash: str = ""


class ETFFactorTask(Task):
    def __init__(self, proposal: FactorProposal):
        super().__init__(proposal.factor_id, version=proposal.version,
                         description=proposal.hypothesis)
        self.proposal = proposal
        self.spec = None
        self.artifact = None
        self.failure = None


class ETFWorkspace(FBWorkspace):
    def __init__(self, session, *, task=None, experiment_id="baseline"):
        # Avoid FBWorkspace's global stock workspace and filesystem side effects.
        Workspace.__init__(self, target_task=task)
        self.session, self.experiment_id = session, experiment_id
        self.file_dict, self.ws_ckp, self.change_summary = {}, None, None
        key = content_hash({"experiment": experiment_id, "task": task.name if task else "baseline",
                            "context": session.context.context_hash})[:24]
        self.workspace_path = ensure_within(session.root / "workspaces" / key, session.root)
        self.context = session.context.model_copy(deep=True)

    def prepare(self):
        self.workspace_path.mkdir(parents=True, exist_ok=True)

    def inject_files(self, **files):
        if self.target_task is not None and self.target_task.artifact is not None:
            raise ConfigurationError("Validated implementation is immutable; create a new version")
        if set(files) - {"factor.py"} or any(not isinstance(v, str) for v in files.values()):
            raise ConfigurationError("ETF factor workspace accepts only factor.py source")
        self.file_dict.update(files)

    def create_ws_ckp(self):
        self.ws_ckp = copy.deepcopy(self.file_dict)

    def recover_ws_ckp(self):
        if self.ws_ckp is None:
            raise RuntimeError("No workspace checkpoint")
        if self.target_task is not None and self.target_task.artifact is not None:
            raise ConfigurationError("Cannot rewrite a validated factor version")
        self.file_dict = copy.deepcopy(self.ws_ckp)

    def copy(self):
        result = ETFWorkspace(self.session, task=copy.deepcopy(self.target_task),
                              experiment_id=self.experiment_id + "-copy")
        result.file_dict = copy.deepcopy(self.file_dict)
        result.context = self.context.model_copy(deep=True)
        return result

    def execute(self):
        self.prepare()
        if self.target_task is None:
            rows = []
            for seed in self.session.protocol.research.seeds:
                config = self.session.config.model_copy(deep=True)
                config.models = [_seed_model(self.session.protocol.model, seed)]
                identity = {"protocol_id": self.session.protocol.protocol_id,
                            "seed": seed, "feature_set_id": self.session.baseline.feature_set_id,
                            "kind": "baseline", "candidate_id": None}
                experiment_id = "baseline-" + content_hash(identity)[:28]
                with RunStore(self.session.root / "paired" / "experiments", experiment_id, identity) as run:
                    if run.reused:
                        import json
                        report = json.loads((run.path / "baseline_report.json").read_text(encoding="utf-8"))
                    else:
                        report = execute_baseline(config, self.session.snapshot.path, run.path,
                                              run_id=experiment_id, feature_override=self.session.baseline,
                                              protocol_id=self.session.protocol.protocol_id,
                                              cost_multipliers=tuple(self.session.protocol.cost_multipliers))
                        run.complete(report)
                    rows.extend(report["by_fold"])
            self.running_info.result = {"status": "completed", "by_fold": rows}
            return self.running_info.result
        task = self.target_task
        payload = task.proposal.model_dump(mode="json")
        if task.proposal.trusted_implementation is None:
            payload["source"] = self.file_dict.get("factor.py", "")
        payload["context_hash"] = self.context.context_hash
        task.spec = FactorSpec.model_validate(payload)
        task.spec.validate_context(self.context)
        atomic_json(self.workspace_path / "factor_spec.json", task.spec)
        if task.spec.trusted_implementation is None:
            (self.workspace_path / "factor.py").write_text(task.spec.source, encoding="utf-8")
        current = self.session.registry.register(task.spec, lineage={
            "snapshot_id": self.session.snapshot.snapshot_id,
            "protocol_id": self.session.protocol.protocol_id, "context_hash": self.context.context_hash,
            **({"trusted_implementation": task.spec.trusted_implementation}
               if task.spec.trusted_implementation is not None else {})})
        if (current["state"] == "retired" or (current["state"] == "rejected" and
                not any(e["state"] == "validated" for e in current["events"]))):
            raise QualityError("Factor version already " + current["state"])
        try:
            artifact = self.session.engine.materialize(
                task.spec, self.context, self.session.snapshot.path / "research",
                eligibility=self.session.eligibility)
            proof = {"status": "passed", "factor_version_id": task.spec.version_id,
                     "protocol_id": self.session.protocol.protocol_id,
                     "snapshot_id": self.session.snapshot.snapshot_id,
                     "quality": artifact.manifest["quality"], "checks": artifact.manifest["checks"]}
            atomic_json(self.workspace_path / "quality.json", proof)
            if current["state"] == "proposed":
                self.session.registry.transition(task.name, task.version, "validated",
                    evidence={"quality": self.workspace_path / "quality.json"})
            task.artifact = artifact
            self.running_info.result = artifact
            return artifact
        except ETFError as exc:
            task.failure = {"status": "failed", "reason": exc.reason, "message": str(exc)}
            atomic_json(self.workspace_path / "failure.json", task.failure)
            if current["state"] == "proposed":
                self.session.registry.transition(task.name, task.version, "rejected",
                    evidence={"failure": self.workspace_path / "failure.json"}, reasons=[exc.reason])
            raise


class ETFExperiment(Experiment):
    def __init__(self, session, sub_tasks, *, hypothesis=None, experiment_id=None):
        super().__init__(sub_tasks, hypothesis=hypothesis)
        self.session = session
        self.experiment_id = experiment_id or ("etf-" + content_hash({
            "tasks": [t.proposal for t in sub_tasks], "context": session.context.context_hash})[:24])
        self.experiment_workspace = ETFWorkspace(session, experiment_id=self.experiment_id)
        self.sub_workspace_list = [
            ETFWorkspace(session, task=task, experiment_id=self.experiment_id) for task in sub_tasks]
