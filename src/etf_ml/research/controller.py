from __future__ import annotations

import json
import time
from pathlib import Path

from etf_ml.artifacts import RUN_ID
from etf_ml.errors import BudgetError, ConfigurationError, DuplicateProposal, ETFError
from etf_ml.research.llm import GuardedLLM, ReplayTransport
from etf_ml.utils import FileLock, atomic_json, content_hash, ensure_within, file_hash
from etf_ml.research.campaign import FIVE_FACTOR_MECHANISM_PLAN


class ResearchController:
    """Serialized research trials with explicit checkpoints and guarded dispatch."""

    def __init__(self, session, run_id: str, *, replay_trials: list[dict] | None = None,
                 campaign_id: str | None = None, campaign_max_trials: int | None = None,
                 mechanism_plan: str | None = None):
        if not RUN_ID.fullmatch(run_id):
            raise ConfigurationError("Invalid research run_id")
        if (campaign_id is None) != (campaign_max_trials is None):
            raise ConfigurationError("Campaign id and finite campaign max trials must be supplied together")
        self.session, self.run_id, self.replay_trials = session, run_id, replay_trials
        self.root = ensure_within(session.root / "sessions" / run_id, session.root)
        if mechanism_plan not in (None, "five_factor_v1"):
            raise ConfigurationError("Unknown campaign mechanism plan")
        plan = FIVE_FACTOR_MECHANISM_PLAN if mechanism_plan == "five_factor_v1" else None
        identity = {"protocol_id": session.initial_protocol.protocol_id, "replay_trials": replay_trials}
        if campaign_id is not None:
            identity.update(campaign_id=campaign_id, campaign_max_trials=campaign_max_trials,
                            mechanism_plan=mechanism_plan)
            from etf_ml.research.campaign import CampaignLedger
            self.campaign = CampaignLedger(session.config.artifact_root / "research_campaigns",
                                           campaign_id, campaign_max_trials,
                                           session.config.research.max_campaign_wall_seconds,
                                           mechanism_plan=plan)
        else:
            self.campaign = None
        self.identity = content_hash(identity)

    def _compatibility_group(self):
        return content_hash({"snapshot_id": self.session.snapshot.snapshot_id,
                             "baseline_id": self.session.baseline.feature_set_id,
                             "protocol_id": self.session.protocol.protocol_id})

    def _record_compatibility_group(self, record):
        card = record.get("research_card") or {}
        return content_hash({"snapshot_id": card.get("evaluation_snapshot_id", self.session.snapshot.snapshot_id),
                             "baseline_id": card.get("evaluation_baseline_id", self.session.initial_baseline.feature_set_id),
                             "protocol_id": card.get("evaluation_protocol_id", record.get(
                                 "active_protocol_id", self.session.protocol.protocol_id))})

    def _sync_campaign(self, state):
        if self.campaign is None:
            return
        self.campaign.initialize()
        for trial in state.get("trials", []):
            path = ensure_within(self.root / trial["path"], self.root)
            record = json.loads(path.read_text(encoding="utf-8"))
            group = self._record_compatibility_group(record)
            index = int(record["trial_index"])
            self.campaign.begin_trial(run_id=self.run_id, trial_index=index,
                                      compatibility_group_id=group,
                                      protocol_id=(record.get("research_card", {}).get("evaluation_protocol_id") or
                                                   record.get("active_protocol_id", self.session.protocol.protocol_id)),
                                      allow_over_cap=True)
            record["_committed_sha256"] = trial["sha256"]
            self.campaign.commit_trial(run_id=self.run_id, trial_index=index,
                                       compatibility_group_id=group,
                                       protocol_id=record.get("active_protocol_id", self.session.protocol.protocol_id),
                                       record=record)
        pending = state.get("in_progress")
        if pending is not None:
            self.campaign.begin_trial(run_id=self.run_id, trial_index=int(pending["trial_index"]),
                                      compatibility_group_id=self._compatibility_group(),
                                      protocol_id=self.session.protocol.protocol_id)
            self.campaign.record_usage(run_id=self.run_id, trial_index=int(pending["trial_index"]),
                                       usage_summary=self.session.llm.usage_summary())
        state["campaign_summary"] = self.campaign.summary()

    @staticmethod
    def _evaluation_evidence(result: dict, paired_root: Path) -> list[dict]:
        row = next((item for item in result.get("by_candidate", []) if item.get("reports")), None)
        if row is None:
            return []
        paths = [ensure_within(Path(item), paired_root) for item in row["reports"].values()]
        parent = paths[0].parent
        paths.extend(parent / name for name in ("evaluation.json", "paired_report.json"))
        return [{"path": str(path), "sha256": file_hash(path)} for path in paths if path.is_file()]

    @staticmethod
    def _definition_id(proposal: dict | None, fields: dict) -> str | None:
        if not proposal:
            return None
        from etf_ml.adapters.rdagent.experiment import FactorProposal
        from etf_ml.research.factor_identity import identify_definition
        try:
            return identify_definition(FactorProposal.model_validate(proposal), fields).definition_id
        except (ValueError, KeyError, TypeError):
            return None

    @classmethod
    def _campaign_candidate_evaluations(cls, result: dict, proposals: list[dict], fields: dict,
                                        snapshot_id: str, baseline_id: str, protocol_id: str):
        by_factor = {row.get("factor_id"): row for row in proposals if isinstance(row, dict)}
        candidates = []
        for row in result.get("by_candidate", []):
            proposal = by_factor.get(row.get("factor_id"), {})
            definition = cls._definition_id(proposal, fields)
            outcome = row.get("status", "unknown")
            evaluation = row.get("evaluation")
            evaluation_id = (content_hash({"definition_id": definition, "evaluation": evaluation,
                "snapshot_id": snapshot_id, "baseline_id": baseline_id, "protocol_id": protocol_id})
                if definition and isinstance(evaluation, dict) and outcome in {
                    "accepted", "rejected", "inconclusive"} else None)
            candidates.append({"factor_id": row.get("factor_id"), "definition_id": definition,
                               "evaluation_id": evaluation_id, "outcome": outcome})
        return candidates

    @staticmethod
    def _refresh_index(state: dict, path: Path, memory_index) -> bool:
        if hasattr(memory_index, "active_source"):
            try:
                source, allowed_root = memory_index.active_source
                memory_index.reuse_store.export_source(source, allowed_root=allowed_root)
                state.pop("reuse_export_pending", None)
            except (ETFError, OSError, ValueError, KeyError, TypeError) as exc:
                # Raw checkpoint is already committed. Keep it usable and retain
                # all raw files when compact publication needs an explicit retry.
                state["reuse_export_pending"] = type(exc).__name__
        try:
            state["search_ledger"] = memory_index.rebuild()["ledger"]
            state.pop("memory_index_stale", None)
        except Exception as exc:
            state.update(status="paused_index", stop_reason="memory_index_refresh_failed",
                         memory_index_stale=True, memory_index_error=type(exc).__name__)
            atomic_json(path, state)
            return False
        atomic_json(path, state)
        return True

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
            from etf_ml.research.memory_index import ResearchMemoryIndex
            from etf_ml.research.reuse_identity import local_reuse_root, reuse_context
            compact_root = (local_reuse_root(self.session.config)
                            if hasattr(self.session.config, "artifact_root") else None)
            memory_index = ResearchMemoryIndex(self.session.memory_root, reuse_root=compact_root)
            memory_index.register_source(self.session.root,
                                         allowed_root=getattr(self.session.config, "artifact_root", self.session.root))
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
                if self.campaign is not None and (state.get("campaign_id") != self.campaign.campaign_id or
                        state.get("campaign_max_trials") != self.campaign.max_attempts):
                    raise ConfigurationError("Research checkpoint belongs to another campaign")
                if state.get("memory_index_stale"):
                    state["search_ledger"] = memory_index.rebuild()["ledger"]
                    state.pop("memory_index_stale", None)
                    atomic_json(path, state)
                if state["status"] == "completed":
                    self._sync_campaign(state)
                    if self.campaign is not None:
                        atomic_json(path, state)
                    return state
            else:
                state = {"identity": self.identity, "run_id": self.run_id,
                         "protocol_id": self.session.protocol.protocol_id, "status": "running",
                         "created_at_ns": time.time_ns(),
                         "trials": [], "in_progress": None,
                         "feature_set_id": self.session.baseline.feature_set_id,
                         "active_protocol_id": self.session.protocol.protocol_id}
                if self.campaign is not None:
                    self.campaign.initialize()
                    state.update(campaign_id=self.campaign.campaign_id,
                                  campaign_max_trials=self.campaign.max_attempts)
                atomic_json(self.root / "protocol.json", self.session.protocol)
                self.session.save(self.session.root / "session.json")
            self._sync_campaign(state)
            maximum = self.session.config.research.max_trials
            while maximum is None or len(state["trials"]) < maximum:
                if (self.root / "cancel.request").exists():
                    state["status"] = "cancelled"
                    atomic_json(path, state)
                    return state
                if self.campaign is not None and self.campaign.time_limit_expired():
                    state.update(status="paused_budget", stop_reason="campaign_wall_clock_limit",
                                 billing=self.session.llm.ledger.summary())
                    self.campaign.record_usage(run_id=self.run_id,
                        trial_index=(state["in_progress"] or {}).get("trial_index", len(state["trials"])),
                        usage_summary=self.session.llm.usage_summary())
                    state["campaign_summary"] = self.campaign.summary()
                    atomic_json(path, state)
                    return state
                index = len(state["trials"])
                self.session.attempted_trials = index + 1
                if self.replay_trials is not None and index >= len(self.replay_trials):
                    state["status"] = "completed"
                    state["stop_reason"] = "replay_exhausted"
                    atomic_json(path, state)
                    return state
                pending = state["in_progress"]
                public_feedback = pending.get("memory_cards") if pending else None
                if not isinstance(public_feedback, list):
                    public_feedback = memory_index.query(
                        snapshot_id=self.session.snapshot.snapshot_id,
                        protocol_id=self.session.protocol.protocol_id,
                        baseline_id=self.session.baseline.feature_set_id,
                        fields=list(self.session.context.fields),
                        reuse_context_id=reuse_context(self.session.protocol))
                elif pending.get("memory_snapshot_hash") != content_hash(public_feedback):
                    raise ConfigurationError("In-progress research memory snapshot changed")
                self.session.context = self.session.build_context(public_feedback)
                if self.campaign is not None and self.campaign.mechanism_plan:
                    assignment = self.campaign.trial_mechanism_assignment(run_id=self.run_id,
                                                                          trial_index=index)
                    if assignment is None:
                        started = self.campaign.begin_trial(run_id=self.run_id, trial_index=index,
                            compatibility_group_id=self._compatibility_group(),
                            protocol_id=self.session.protocol.protocol_id)
                        if not started:
                            state["in_progress"] = None
                            state.update(status="completed", stop_reason="campaign_attempt_limit")
                            state["campaign_summary"] = self.campaign.summary()
                            atomic_json(path, state)
                            return state
                        assignment = self.campaign.trial_mechanism_assignment(run_id=self.run_id,
                                                                              trial_index=index)
                    if not assignment:
                        raise ConfigurationError("Campaign trial has no valid frozen mechanism assignment")
                    if pending and pending.get("mechanism_assignment") not in (None, assignment):
                        raise ConfigurationError("In-progress checkpoint mechanism slot differs from campaign ledger")
                    self.session.context = self.session.context.model_copy(update={
                        "selection_rules": {**self.session.context.selection_rules,
                            "campaign_mechanism_assignment": assignment,
                            "campaign_mechanism_plan": self.campaign.mechanism_plan}})
                if self.replay_trials is not None:
                    replay = self.replay_trials[index]
                    replay_factors = []
                    for factor in replay["factors"]:
                        # Historic deterministic fixtures predate proposal v2.
                        # Enrich only the in-memory replay envelope; no old
                        # artifact or live provider response is rewritten.
                        row = {k: v for k, v in factor.items() if k != "source"}
                        row.setdefault("mechanism", "deterministic replay fixture mechanism")
                        row.setdefault("direction", "unknown")
                        row.setdefault("direction_reason", "Replay fixture does not assume a sign.")
                        row.setdefault("failure_conditions", ["Replay quality gate fails."])
                        row.setdefault("compared_features", [])
                        replay_factors.append(row)
                    responses = {
                        "hypothesis": json.dumps({"hypothesis": replay["hypothesis"], "reason": "deterministic replay"}),
                        "proposal": json.dumps({"factors": replay_factors}),
                        **{"code:" + f["factor_id"]: json.dumps({"source": f["source"]})
                           for f in replay["factors"]},
                    }
                    self.session.llm = GuardedLLM(self.session.root / "llm",
                        self.session.config.research, ReplayTransport(responses))
                self.session.llm.dispatch_scope = f"{self.run_id}:{index}"
                self.session.llm.dispatch_guard = (self.campaign.require_time_remaining
                                                    if self.campaign is not None else None)
                scen = ETFFactorScenario(self.session)
                trace = Trace(scen)
                state["status"] = "running"
                if state["in_progress"] is None:
                    state["in_progress"] = {"phase": "proposing", "trial_index": index,
                                           "context_hash": self.session.context.context_hash,
                                           "memory_cards": public_feedback,
                                           "memory_snapshot_hash": content_hash(public_feedback)}
                    if self.campaign is not None and self.campaign.mechanism_plan:
                        state["in_progress"]["mechanism_assignment"] = assignment
                    atomic_json(path, state)
                pending = state["in_progress"]
                if self.campaign is not None:
                    if not self.campaign.begin_trial(run_id=self.run_id, trial_index=index,
                            compatibility_group_id=self._compatibility_group(),
                            protocol_id=self.session.protocol.protocol_id):
                        state["in_progress"] = None
                        state.update(status="completed", stop_reason=(
                            "campaign_wall_clock_limit" if self.campaign.time_limit_expired()
                            else "campaign_attempt_limit"))
                        state["campaign_summary"] = self.campaign.summary()
                        atomic_json(path, state)
                        return state
                    self.campaign.record_usage(run_id=self.run_id, trial_index=index,
                                               usage_summary=self.session.llm.usage_summary())
                try:
                    # A generated hypothesis or proposal is a paid external call
                    # in a formal run. Verify the isolated execution runtime
                    # before any such dispatch so an unavailable Docker daemon
                    # fails closed without consuming LLM budget.
                    scen.get_runtime_environment()
                    if pending["context_hash"] != self.session.context.context_hash:
                        raise ConfigurationError("In-progress research context changed")
                    self.session.bind_repair_state(state, pending, path)
                    if pending["phase"] == "proposing":
                        hypothesis = ETFHypothesisGen(scen).gen(trace)
                        if self.campaign is not None:
                            self.campaign.record_usage(run_id=self.run_id, trial_index=index,
                                                       usage_summary=self.session.llm.usage_summary())
                        # Preserve the paid/Replay hypothesis before proposal
                        # validation can fail, so the next run has auditable
                        # evidence instead of inventing a replacement history.
                        pending.update({"phase": "proposing", "hypothesis": vars(hypothesis)})
                        atomic_json(path, state)
                        exp = ETFHypothesis2Experiment().convert(hypothesis, trace)
                        if self.campaign is not None:
                            self.campaign.record_usage(run_id=self.run_id, trial_index=index,
                                                       usage_summary=self.session.llm.usage_summary())
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
                    if self.campaign is not None:
                        for proposal_index, proposal in enumerate(pending.get("proposals", [])):
                            self.campaign.record_proposal(run_id=self.run_id, trial_index=index,
                                proposal_index=proposal_index,
                                definition_id=self._definition_id(proposal, self.session.context.fields),
                                research_group=proposal.get("research_group"),
                                compatibility_group_id=self._compatibility_group(),
                                protocol_id=self.session.protocol.protocol_id,
                                mechanism_slot=(assignment["slot"] if self.campaign.mechanism_plan else None))
                    exp = ETFFactorCoder(scen).develop(exp)
                    if self.campaign is not None:
                        self.campaign.require_time_remaining()
                    pending["phase"] = "running"
                    atomic_json(path, state)
                    exp = ETFFactorRunner(scen).develop(exp)
                    if self.campaign is not None:
                        self.campaign.require_time_remaining()
                    feedback = ETFFeedback(scen).generate_feedback(exp, trace)
                    if self.campaign is not None:
                        self.campaign.require_time_remaining()
                    evaluation_snapshot_id = self.session.snapshot.snapshot_id
                    evaluation_baseline_id = self.session.baseline.feature_set_id
                    evaluation_protocol_id = self.session.protocol.protocol_id
                    proposal = pending["proposals"][0] if pending["proposals"] else {}
                    definition_id = self._definition_id(proposal, self.session.context.fields)
                    evaluation_evidence = self._evaluation_evidence(exp.result, self.session.root / "paired")
                    evaluation_id = content_hash({"definition_id": definition_id,
                                                  "result": exp.result,
                                                  "snapshot_id": evaluation_snapshot_id,
                                                  "baseline_id": evaluation_baseline_id,
                                                  "protocol_id": evaluation_protocol_id})
                    promotion = self.session.promote_trial(exp)
                    from etf_ml.research.research_memory import build_card
                    summary = feedback.structured["summary"]
                    card = build_card(hypothesis=hypothesis, proposal=proposal,
                                      feedback=feedback.structured,
                                      context_hash=self.session.context.context_hash,
                                      snapshot_id=evaluation_snapshot_id,
                                      protocol_id=evaluation_protocol_id,
                                      baseline_id=evaluation_baseline_id,
                                      feedback_summary=summary, trial_id=f"{self.run_id}:{index}",
                                      definition_id=definition_id, evaluation_id=evaluation_id,
                                      resulting_baseline_id=self.session.baseline.feature_set_id,
                                      resulting_protocol_id=self.session.protocol.protocol_id,
                                      evaluation_evidence=evaluation_evidence)
                    record = {"trial_index": index, "experiment_id": exp.experiment_id,
                              "context_hash": self.session.context.context_hash,
                              "hypothesis": vars(hypothesis), "proposals": pending["proposals"],
                              "feedback": feedback.structured, "feedback_summary": summary,
                              "research_card": card,
                              "result": exp.result, "promotion": promotion,
                              "usage_summary": self.session.llm.usage_summary(),
                              "selected_feature_set_id": self.session.baseline.feature_set_id,
                              "active_protocol_id": self.session.protocol.protocol_id}
                    if self.campaign is not None:
                        record["campaign_candidates"] = self._campaign_candidate_evaluations(
                            exp.result, pending["proposals"], self.session.context.fields,
                            evaluation_snapshot_id, evaluation_baseline_id, evaluation_protocol_id)
                    target = self.root / f"trial-{index:05d}.json"
                    atomic_json(target, record)
                    state["trials"].append({"path": target.name, "sha256": file_hash(target),
                                            "status": exp.result["status"]})
                    state["in_progress"] = None
                    state["feature_set_id"] = self.session.baseline.feature_set_id
                    state["active_protocol_id"] = self.session.protocol.protocol_id
                    state["billing"] = self.session.llm.ledger.summary()
                    # The checkpoint is authoritative.  Publish it before the
                    # derived index so an interruption can only leave a stale
                    # view, never a missing committed trial.
                    atomic_json(path, state)
                    if self.campaign is not None:
                        record["_committed_sha256"] = file_hash(target)
                        self.campaign.commit_trial(run_id=self.run_id, trial_index=index,
                            compatibility_group_id=self._record_compatibility_group(record),
                            protocol_id=(record.get("research_card", {}).get("evaluation_protocol_id") or
                                         self.session.protocol.protocol_id), record=record)
                        state["campaign_summary"] = self.campaign.summary()
                        atomic_json(path, state)
                    if not self._refresh_index(state, path, memory_index):
                        return state
                    self.session.save(self.session.root / "session.json")
                except BudgetError as exc:
                    state["status"], state["stop_reason"] = "paused_budget", str(exc)
                    state["billing"] = self.session.llm.ledger.summary()
                    if self.campaign is not None:
                        self.campaign.record_usage(run_id=self.run_id, trial_index=index,
                                                   usage_summary=self.session.llm.usage_summary())
                        state["campaign_summary"] = self.campaign.summary()
                    atomic_json(path, state)
                    return state
                except KeyboardInterrupt:
                    state["status"] = "cancelled"
                    if self.campaign is not None:
                        self.campaign.record_usage(run_id=self.run_id, trial_index=index,
                                                   usage_summary=self.session.llm.usage_summary())
                        state["campaign_summary"] = self.campaign.summary()
                    atomic_json(path, state)
                    raise
                except ConfigurationError:
                    raise
                except DuplicateProposal as exc:
                    decision = exc.decision
                    outcome = decision["decision"]
                    from etf_ml.research.research_memory import build_card
                    proposal = getattr(exc, "proposal", (pending.get("proposals") or [{}])[0])
                    feedback = {"status": outcome, "by_candidate": [{"status": outcome,
                                "reasons": decision.get("reason_codes", [])}]}
                    record = {"trial_index": index, "hypothesis": pending.get("hypothesis"),
                              "proposals": [proposal], "feedback": feedback,
                              "result": {"status": outcome, "stage": "factor_admission",
                                         "reused_evaluation_id": decision.get("reusable_evaluation_id"),
                                         "reused_economic_status": decision.get("reused_economic_status")},
                              "dedup_decision": decision,
                              "research_card": build_card(hypothesis=pending.get("hypothesis"), proposal=proposal,
                                  feedback=feedback, context_hash=self.session.context.context_hash,
                                  snapshot_id=self.session.snapshot.snapshot_id,
                                  protocol_id=self.session.protocol.protocol_id,
                                  baseline_id=self.session.baseline.feature_set_id,
                                  trial_id=f"{self.run_id}:{index}",
                                  definition_id=decision.get("definition_id"), attempt_outcome=outcome,
                                  economic_status=decision.get("reused_economic_status"),
                                  dedup_decision=decision),
                              "selected_feature_set_id": self.session.baseline.feature_set_id,
                              "active_protocol_id": self.session.protocol.protocol_id}
                    package_id = decision.get("reuse_package_id")
                    if outcome == "reuse" and package_id:
                        candidate = memory_index.reuse_store.decision(package_id)
                        if candidate["evaluation_id"] != decision.get("reusable_evaluation_id"):
                            raise ConfigurationError("Compact admission evaluation changed")
                        record["result"].update(reuse_package_id=package_id, by_candidate=[candidate])
                        record["promotion"] = None
                        record["promotion_materialized"] = False
                        record["research_card"]["evaluation_id"] = candidate["evaluation_id"]
                        record["research_card"]["card_hash"] = content_hash({
                            k: v for k, v in record["research_card"].items() if k != "card_hash"})
                    if self.campaign is not None:
                        record["usage_summary"] = self.session.llm.usage_summary()
                    target = self.root / f"trial-{index:05d}.json"
                    atomic_json(target, record)
                    state["trials"].append({"path": target.name, "sha256": file_hash(target), "status": outcome})
                    state["in_progress"] = None
                    state["billing"] = self.session.llm.ledger.summary()
                    atomic_json(path, state)
                    if self.campaign is not None:
                        record["_committed_sha256"] = file_hash(target)
                        self.campaign.commit_trial(run_id=self.run_id, trial_index=index,
                            compatibility_group_id=self._record_compatibility_group(record),
                            protocol_id=(record.get("research_card", {}).get("evaluation_protocol_id") or
                                         self.session.protocol.protocol_id), record=record)
                        state["campaign_summary"] = self.campaign.summary()
                        atomic_json(path, state)
                    if not self._refresh_index(state, path, memory_index):
                        return state
                    if outcome != "reuse":
                        state.update(status=outcome, stop_reason="factor_admission")
                        atomic_json(path, state)
                        return state
                except (ETFError, ValueError, KeyError) as exc:
                    failure = {"status": "failed", "stage": pending.get("phase", "factor_selection"),
                               "protocol_id": self.session.protocol.protocol_id,
                               "by_candidate": [], "reason": getattr(exc, "reason", "invalid_proposal")}
                    target = self.root / f"trial-{index:05d}.json"
                    from etf_ml.research.research_memory import build_card
                    proposal = (pending.get("proposals") or [{}])[0]
                    card = build_card(hypothesis=pending.get("hypothesis"), proposal=proposal,
                                      feedback=failure, context_hash=self.session.context.context_hash,
                                      snapshot_id=self.session.snapshot.snapshot_id,
                                      protocol_id=self.session.protocol.protocol_id,
                                      baseline_id=self.session.baseline.feature_set_id,
                                      trial_id=f"{self.run_id}:{index}",
                                      definition_id=self._definition_id(proposal, self.session.context.fields),
                                      attempt_outcome="failed", failure_category=getattr(exc, "category", getattr(exc, "reason", None)),
                                      dedup_decision=getattr(exc, "decision", None))
                    record = {"trial_index": index, "hypothesis": pending.get("hypothesis"),
                              "proposals": pending.get("proposals"), "feedback": failure,
                              "result": failure, "exception_type": type(exc).__name__,
                              "dedup_decision": getattr(exc, "decision", None),
                              "research_card": card,
                              "selected_feature_set_id": self.session.baseline.feature_set_id,
                              "active_protocol_id": self.session.protocol.protocol_id}
                    if self.campaign is not None:
                        record["usage_summary"] = self.session.llm.usage_summary()
                    atomic_json(target, record)
                    state["trials"].append({"path": target.name, "sha256": file_hash(target), "status": "failed"})
                    state["in_progress"] = None
                    state["feature_set_id"] = self.session.baseline.feature_set_id
                    state["active_protocol_id"] = self.session.protocol.protocol_id
                    state["billing"] = self.session.llm.ledger.summary()
                    atomic_json(path, state)
                    if self.campaign is not None:
                        record = json.loads(target.read_text(encoding="utf-8"))
                        record["_committed_sha256"] = file_hash(target)
                        self.campaign.commit_trial(run_id=self.run_id, trial_index=index,
                            compatibility_group_id=self._record_compatibility_group(record),
                            protocol_id=(record.get("research_card", {}).get("evaluation_protocol_id") or
                                         self.session.protocol.protocol_id), record=record)
                        state["campaign_summary"] = self.campaign.summary()
                        atomic_json(path, state)
                    if not self._refresh_index(state, path, memory_index):
                        return state
            state["status"], state["stop_reason"] = "completed", "trial_limit"
            if self.campaign is not None:
                state["campaign_summary"] = self.campaign.summary()
            atomic_json(path, state)
            return state
