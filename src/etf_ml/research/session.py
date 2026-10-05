from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from etf_ml.contracts import AppConfig
from etf_ml.data.snapshot import load_snapshot
from etf_ml.errors import ConfigurationError, QualityError
from etf_ml.features.baseline import feature_descriptors, materialize
from etf_ml.registry import FactorRegistry
from etf_ml.research.context import ResearchContext
from etf_ml.research.factor_engine import FactorEngine
from etf_ml.research.llm import GuardedLLM
from etf_ml.research.protocol import ComparisonProtocol
from etf_ml.utils import atomic_json, content_hash, ensure_within


FIELD_DESCRIPTORS = {
    "adj_open": ("split/dividend-adjusted opening price", "adjusted_price"),
    "adj_high": ("split/dividend-adjusted session high", "adjusted_price"),
    "adj_low": ("split/dividend-adjusted session low", "adjusted_price"),
    "adj_close": ("split/dividend-adjusted closing price", "adjusted_price"),
    "raw_open": ("unadjusted opening price", "raw_price"),
    "raw_high": ("unadjusted session high", "raw_price"),
    "raw_low": ("unadjusted session low", "raw_price"),
    "raw_close": ("unadjusted closing price", "raw_price"),
    "volume_shares": ("session trading volume in shares", "shares"),
    "amount_currency": ("session trading amount in currency", "currency"),
}


class ResearchSession:
    def __init__(self, config: AppConfig, snapshot_path: Path,
                 protocol: ComparisonProtocol, *, root: Path, llm=None, selected_feature_set_id=None,
                 memory_root: Path | None = None):
        if config.data.mode == "formal" and protocol.stress_min_excess_return is None:
            raise ConfigurationError("Formal research session requires stress_min_excess_return")
        self.config, self.protocol = config.model_copy(deep=True), protocol.model_copy(deep=True)
        self.initial_protocol = protocol.model_copy(deep=True)
        self.root = Path(root).resolve()
        self.memory_root = (Path(memory_root).resolve() if memory_root else
                            (Path(config.artifact_root).resolve() / "research_memory" / "v2"))
        self.snapshot = load_snapshot(snapshot_path)
        from etf_ml.data.diagnostic import require_snapshot_mode
        require_snapshot_mode(config, self.snapshot)
        if self.snapshot.snapshot_id != protocol.snapshot_id:
            raise ConfigurationError("ETF session snapshot differs from its protocol")
        protocol.require_runtime()
        for key in ("label", "universe", "validation", "portfolio", "research", "benchmarks"):
            if getattr(config, key).model_dump(mode="json") != getattr(protocol, key).model_dump(mode="json"):
                raise ConfigurationError("ETF session changed frozen " + key)
        self.panel = pd.read_parquet(self.snapshot.path / "research" / "panel.parquet")
        if self.panel.empty or (self.panel.index.get_level_values("datetime") >= pd.Timestamp(config.validation.holdout_start)).any():
            raise QualityError("ETF research view is empty or includes holdout")
        self.baseline = materialize({"snapshot_id": self.snapshot.snapshot_id}, self.panel)
        if self.baseline.feature_set_id != protocol.baseline_feature_set_id:
            raise ConfigurationError("ETF session baseline differs from frozen features")
        self.eligibility = pd.read_parquet(self.snapshot.path / "universe.parquet").eligible.reindex(self.panel.index)
        self.initial_baseline = self.baseline
        self.registry = FactorRegistry(self.root / "registry")
        from etf_ml.research.feature_sets import FeatureSetStore
        self.feature_store = FeatureSetStore(self.root / "feature_sets", self.registry)
        self.select_baseline(selected_feature_set_id or self.initial_baseline.feature_set_id)
        self.engine = FactorEngine(self.root / "factors", config.research)
        self.llm = llm or GuardedLLM(self.root / "llm", config.research)
        self._repair_stages: set[str] = set()
        self._repair_total = 0
        self._repair_checkpoint = None
        self._repair_state = None
        self.context = self.build_context()

    def bind_repair_state(self, state: dict, pending: dict, checkpoint: Path):
        repair = pending.get("repair_state", {})
        self._repair_stages = set(repair.get("stages", []))
        self._repair_total = int(repair.get("total", 0))
        self._repair_checkpoint, self._repair_state = Path(checkpoint), (state, pending)
        pending["repair_state"] = {"stages": sorted(self._repair_stages), "total": self._repair_total}

    def claim_repair(self, stage: str) -> bool:
        """One repair per stage and a frozen per-trial repair cap."""
        limit = self.config.research.max_repairs_per_trial
        if stage in self._repair_stages or self._repair_total >= limit:
            return False
        self._repair_stages.add(stage)
        self._repair_total += 1
        if self._repair_state is not None:
            state, pending = self._repair_state
            pending["repair_state"] = {"stages": sorted(self._repair_stages), "total": self._repair_total}
            atomic_json(self._repair_checkpoint, state)
        return True

    def select_baseline(self, identity):
        if identity == self.initial_baseline.feature_set_id:
            self.baseline = self.initial_baseline
        else:
            loaded = self.feature_store.load(identity)
            if loaded.manifest["composition"]["base_feature_set_id"] != self.initial_baseline.feature_set_id:
                raise ConfigurationError("Accumulated set belongs to another initial research baseline")
            frozen = {k: v for k, v in self.initial_protocol.model_dump(mode="json").items() if k != "baseline_feature_set_id"}
            published = {k: v for k, v in loaded.manifest["composition"]["selection_protocol"].items() if k != "baseline_feature_set_id"}
            if frozen != published:
                raise ConfigurationError("Accumulated set changed the frozen learning or execution protocol")
            self.baseline = self.feature_store.verify_baseline(loaded, self.panel)
        payload = self.initial_protocol.model_dump(mode="json")
        payload["baseline_feature_set_id"] = self.baseline.feature_set_id
        self.protocol = ComparisonProtocol.model_validate(payload)

    def promote_trial(self, exp):
        import statistics
        accepted = [r for r in exp.result["by_candidate"] if r["status"] == "accepted" and
                    r.get("group_ablation", {}).get("status") == "accepted"]
        if not accepted:
            return None
        def rank(row):
            deltas = row["evaluation"]["paired_deltas"]
            folds = [statistics.median(d["excess_return"] for d in deltas if d["fold"] == f.name)
                     for f in self.protocol.validation.folds]
            return (-statistics.median(folds), row["factor_id"])
        winner = sorted(accepted, key=rank)[0]
        task = next(t for t in exp.sub_tasks if t.name == winner["factor_id"])
        selected = self.feature_store.publish(self.baseline, task.artifact, task.spec, self.protocol)
        self.select_baseline(selected.feature_set_id)
        return {"factor_id": task.name, "factor_version_id": task.spec.version_id,
                "feature_set_id": selected.feature_set_id,
                "rule": "one_per_trial_highest_median_fold_net_excess_increment_then_factor_id"}

    def build_context(self, feedback=None):
        from etf_ml.research.reuse_identity import reuse_context
        dates = self.panel.index.get_level_values("datetime")
        fields = {}
        for name in self.panel.select_dtypes("number"):
            meaning, unit = FIELD_DESCRIPTORS.get(name, (
                "derived numeric research input; semantic meaning has not been audited", "dimensionless"))
            fields[name] = {"meaning": meaning, "unit": unit,
                            "adjustment": "adjusted" if name.startswith("adj_") else "raw_or_not_applicable",
                            "available_at": "after_daily_ingestion",
                            "missing_policy": "preserve_missing",
                            # Unknown fields are visible for provenance but cannot be proposed as inputs.
                            "allowed_usage": "proposal" if name in FIELD_DESCRIPTORS else "forbidden",
                            "evidence_id": "panel-contract-v1"}
        return ResearchContext(
            snapshot_id=self.snapshot.snapshot_id, baseline_id=self.baseline.feature_set_id,
            protocol_id=self.protocol.protocol_id, visible_start=str(dates.min()), visible_end=str(dates.max()),
            horizon=self.config.label.horizon, fields=fields,
            existing_features=list(self.baseline.frame.columns),
            feature_descriptors=feature_descriptors(
                list(self.baseline.frame.columns), composition=self.baseline.manifest.get("composition")),
            model=self.protocol.model.model_dump(),
            selection_rules={"reuse_context_id": reuse_context(self.protocol),
                             "development_folds": [f.model_dump(mode="json") for f in self.protocol.validation.folds],
                             "factor_signal_rule": self.protocol.factor_signal_rule,
                             "factor_signal_min_pass_folds": len(self.protocol.validation.folds) // 2 + 1,
                             "factor_signal_condition": "training-only orientation; validation IC > 0 and ICIR > 0 in the same fold",
                             "label_spec": self.protocol.label.model_dump(mode="json"),
                             "benchmark_policy": self.protocol.benchmarks.model_dump(mode="json"),
                             "universe_policy": self.protocol.universe.model_dump(mode="json"),
                             "portfolio_policy": self.protocol.portfolio.model_dump(mode="json"),
                             "seeds": self.protocol.research.seeds,
                             "candidate_groups": {e["column"]: e["group"] for e in self.baseline.manifest.get("composition", {}).get("factors", [])},
                             "promotion_rule": "one_per_trial_highest_median_fold_net_excess_increment_then_factor_id",
                             "cost_multipliers": self.protocol.cost_multipliers,
                             "stress_min_excess_return": self.protocol.stress_min_excess_return,
                             "risk_mode": self.protocol.portfolio.risk_mode,
                             "risk_trigger_limit": self.protocol.portfolio.risk,
                             "max_drawdown_acceptance_limit": self.protocol.portfolio.max_drawdown_limit,
                             "max_drawdown_deterioration": self.protocol.research.max_drawdown_deterioration,
                             "max_turnover_deterioration": self.protocol.research.max_turnover_deterioration},
            runtime={**self.protocol.research.limits.model_dump(mode="json"),
                     "maximum_lookback": self.protocol.research.maximum_lookback,
                     "prompt_policy_version": "stage-context-v3",
                     "schema_version": "factor-proposal-v2",
                     "summary_version": "feedback-summary-v3",
                     "max_repairs_per_stage": 1,
                     "max_repairs_per_trial": self.protocol.research.max_repairs_per_trial,
                     "max_dispatches_per_trial": self.protocol.research.max_dispatches_per_trial,
                     "memory_token_cap": 1600},
            feedback=list(feedback or []), prompt_version="etf-factor-v2")

    def next_version(self, factor_id):
        import re
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,80}", factor_id):
            raise ConfigurationError("Invalid factor proposal identity")
        versions = [int(p.name[1:]) for p in (self.root / "registry" / factor_id).glob("v*")
                    if p.name[1:].isdigit() and (p / "head.json").exists()]
        return max(versions, default=0) + 1

    def save(self, path: Path):
        path = ensure_within(path, self.root)
        atomic_json(path, {"config": self.config.model_dump(mode="json"),
                           "snapshot_path": str(self.snapshot.path),
                           "protocol": self.initial_protocol.model_dump(mode="json"),
                           "selected_feature_set_id": self.baseline.feature_set_id,
                           "root": str(self.root), "memory_root": str(self.memory_root)})

    @classmethod
    def from_file(cls, path: Path):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(AppConfig.model_validate(payload["config"]), Path(payload["snapshot_path"]),
                   ComparisonProtocol.model_validate(payload["protocol"]), root=Path(payload["root"]),
                   selected_feature_set_id=payload.get("selected_feature_set_id"),
                   memory_root=Path(payload["memory_root"]) if payload.get("memory_root") else None)
