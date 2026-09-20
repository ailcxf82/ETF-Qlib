from __future__ import annotations

import json
from pathlib import Path

from etf_ml.artifacts import RUN_ID
from etf_ml.errors import BudgetError, ConfigurationError, ETFError
from etf_ml.research.llm import GuardedLLM, ReplayTransport
from etf_ml.utils import FileLock, atomic_json, content_hash, ensure_within, file_hash


class ResearchController:
    """Serialized research trials with explicit checkpoints and guarded dispatch."""

    def __init__(self, session, run_id: str, *, replay_trials: list[dict] | None = None):
        if not RUN_ID.fullmatch(run_id):
            raise ConfigurationError("Invalid research run_id")
        self.session, self.run_id, self.replay_trials = session, run_id, replay_trials
        self.root = ensure_within(session.root / "sessions" / run_id, session.root)
        self.identity = content_hash({"protocol_id": session.initial_protocol.protocol_id,
                                      "replay_trials": replay_trials})

    def run(self):
        from rdagent.core.proposal import Hypothesis, Trace
        from etf_ml.adapters.rdagent.scenario import ETFFactorScenario
        from etf_ml.adapters.rdagent.experiment import ETFExperiment, ETFFactorTask, FactorProposal
        from etf_ml.adapters.rdagent.proposal import ETFHypothesisGen, ETFHypothesis2Experiment
        from etf_ml.adapters.rdagent.coder import ETFFactorCoder
        from etf_ml.adapters.rdagent.runner import ETFFactorRunner
        from etf_ml.adapters.rdagent.feedback import ETFFeedback

        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "checkpoint.json"
        with FileLock(self.root / "session.lock"):
            if path.exists():
                state = json.loads(path.read_text(encoding="utf-8"))
                if state["identity"] != self.identity:
                    raise ConfigurationError("Research checkpoint belongs to another frozen session")
                for trial in state["trials"]:
                    evidence = ensure_within(self.root / trial["path"], self.root)
                    if file_hash(evidence) != trial["sha256"]:
                        raise ConfigurationError("Research trial evidence changed")
                self.session.select_baseline(state["feature_set_id"])
                if self.session.protocol.protocol_id != state["active_protocol_id"]:
                    raise ConfigurationError("Research accumulated protocol changed")
                expected = self.session.initial_baseline.feature_set_id
                for trial in state["trials"]:
                    record = json.loads((self.root / trial["path"]).read_text(encoding="utf-8"))
                    expected = record["selected_feature_set_id"]
                if expected != state["feature_set_id"]:
                    raise ConfigurationError("Research accumulated set differs from committed trial history")
                if state["status"] == "completed":
                    return state
            else:
                state = {"identity": self.identity, "run_id": self.run_id,
                         "protocol_id": self.session.protocol.protocol_id, "status": "running",
                         "trials": [], "in_progress": None,
                         "feature_set_id": self.session.baseline.feature_set_id,
                         "active_protocol_id": self.session.protocol.protocol_id}
                atomic_json(self.root / "protocol.json", self.session.protocol)
                self.session.save(self.session.root / "session.json")
            maximum = self.session.config.research.max_trials
            while maximum is None or len(state["trials"]) < maximum:
                if (self.root / "cancel.request").exists():
                    state["status"] = "cancelled"
                    atomic_json(path, state)
                    return state
                index = len(state["trials"])
                self.session.attempted_trials = index + 1
                if self.replay_trials is not None and index >= len(self.replay_trials):
                    state["status"] = "completed"
                    state["stop_reason"] = "replay_exhausted"
                    atomic_json(path, state)
                    return state
                public_feedback = []
                for trial in state["trials"]:
                    record = json.loads((self.root / trial["path"]).read_text(encoding="utf-8"))
                    public_feedback.append(record["feedback"])
                self.session.context = self.session.build_context(public_feedback)
                if self.replay_trials is not None:
                    replay = self.replay_trials[index]
                    responses = {
                        "hypothesis": json.dumps({"hypothesis": replay["hypothesis"], "reason": "deterministic replay"}),
                        "proposal": json.dumps({"factors": [
                            {k: v for k, v in f.items() if k != "source"} for f in replay["factors"]]}),
                        **{"code:" + f["factor_id"]: json.dumps({"source": f["source"]})
                           for f in replay["factors"]},
                    }
                    self.session.llm = GuardedLLM(self.session.root / "llm",
                        self.session.config.research, ReplayTransport(responses))
                scen = ETFFactorScenario(self.session)
                trace = Trace(scen)
                state["status"] = "running"
                if state["in_progress"] is None:
                    state["in_progress"] = {"phase": "proposing", "trial_index": index,
                                           "context_hash": self.session.context.context_hash}
                    atomic_json(path, state)
                try:
                    pending = state["in_progress"]
                    if pending["context_hash"] != self.session.context.context_hash:
                        raise ConfigurationError("In-progress research context changed")
                    if pending["phase"] == "proposing":
                        hypothesis = ETFHypothesisGen(scen).gen(trace)
                        exp = ETFHypothesis2Experiment().convert(hypothesis, trace)
                        pending.update({"phase": "coding", "hypothesis": vars(hypothesis),
                                        "proposals": [t.proposal.model_dump(mode="json") for t in exp.sub_tasks],
                                        "experiment_id": exp.experiment_id})
                        atomic_json(path, state)
                    else:
                        hypothesis = Hypothesis(**pending["hypothesis"])
                        tasks = [ETFFactorTask(FactorProposal.model_validate(p)) for p in pending["proposals"]]
                        exp = ETFExperiment(self.session, tasks, hypothesis=hypothesis,
                                            experiment_id=pending["experiment_id"])
                        exp.based_experiments = [ETFExperiment(self.session, [])]
                    exp = ETFFactorCoder(scen).develop(exp)
                    pending["phase"] = "running"
                    atomic_json(path, state)
                    exp = ETFFactorRunner(scen).develop(exp)
                    feedback = ETFFeedback(scen).generate_feedback(exp, trace)
                    promotion = self.session.promote_trial(exp)
                    record = {"trial_index": index, "experiment_id": exp.experiment_id,
                              "context_hash": self.session.context.context_hash,
                              "proposals": pending["proposals"], "feedback": feedback.structured,
                              "result": exp.result, "promotion": promotion,
                              "selected_feature_set_id": self.session.baseline.feature_set_id,
                              "active_protocol_id": self.session.protocol.protocol_id}
                    target = self.root / f"trial-{index:05d}.json"
                    atomic_json(target, record)
                    state["trials"].append({"path": target.name, "sha256": file_hash(target),
                                            "status": exp.result["status"]})
                    state["in_progress"] = None
                    state["feature_set_id"] = self.session.baseline.feature_set_id
                    state["active_protocol_id"] = self.session.protocol.protocol_id
                    state["billing"] = self.session.llm.ledger.summary()
                    atomic_json(path, state)
                    self.session.save(self.session.root / "session.json")
                except BudgetError as exc:
                    state["status"], state["stop_reason"] = "paused_budget", str(exc)
                    state["billing"] = self.session.llm.ledger.summary()
                    atomic_json(path, state)
                    return state
                except KeyboardInterrupt:
                    state["status"] = "cancelled"
                    atomic_json(path, state)
                    raise
                except ConfigurationError:
                    raise
                except (ETFError, ValueError, KeyError) as exc:
                    failure = {"status": "failed", "stage": "factor_selection",
                               "protocol_id": self.session.protocol.protocol_id,
                               "by_candidate": [], "reason": getattr(exc, "reason", "invalid_proposal")}
                    target = self.root / f"trial-{index:05d}.json"
                    atomic_json(target, {"trial_index": index, "feedback": failure,
                                         "result": failure, "exception_type": type(exc).__name__,
                                         "selected_feature_set_id": self.session.baseline.feature_set_id,
                                         "active_protocol_id": self.session.protocol.protocol_id})
                    state["trials"].append({"path": target.name, "sha256": file_hash(target), "status": "failed"})
                    state["in_progress"] = None
                    state["feature_set_id"] = self.session.baseline.feature_set_id
                    state["active_protocol_id"] = self.session.protocol.protocol_id
                    state["billing"] = self.session.llm.ledger.summary()
                    atomic_json(path, state)
            state["status"], state["stop_reason"] = "completed", "trial_limit"
            atomic_json(path, state)
            return state
