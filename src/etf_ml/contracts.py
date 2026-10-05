from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from etf_ml.errors import ConfigurationError
from etf_ml.utils import content_hash

class StrictSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, allow_inf_nan=False)


class DataSpec(StrictSpec):
    mode: Literal["formal", "diagnostic"] = "formal"
    source: Path = Path("D:/qlib_data/etf_qlib_data")
    artifact_root: Path = Path("artifacts/data")
    trusted_calendar: Path | None = None
    volume_unit: Literal["shares", "lots"] | None = None
    amount_multiplier: float | None = Field(default=None, gt=0)
    lot_size: int = Field(default=100, gt=0)
    price_mode: Literal["raw", "adjusted"] | None = None
    change_unit: Literal["decimal", "percent"] | None = None
    reference_close_tick: float | None = Field(default=None, gt=0, le=0.001)
    point_in_time_metadata: bool = False
    metadata_path: Path | None = None
    events_path: Path | None = None
    adjustment_path: Path | None = None
    source_revisions_path: Path | None = None
    limits_path: Path | None = None
    dividend_review_path: Path | None = None
    share_events_path: Path | None = None
    income_path: Path | None = None
    benchmark_path: Path | None = None
    benchmark_id: str = "CSI300"
    holdout_start: str = "2026-01-01"
    fields: dict[str, str] = Field(default_factory=lambda: {
        "open": "open", "high": "high", "low": "low", "close": "close",
        "volume": "volume", "amount": "amount", "change": "change",
        "factor": "factor", "reference_close": "pre_close",
    })


class UniversePolicy(StrictSpec):
    minimum_listing_days: int = Field(default=60, ge=0)
    liquidity_lookback: int = Field(default=20, gt=0)
    minimum_average_amount: float = Field(default=0, ge=0)
    allowed_asset_class: str = "domestic_equity"


class LabelSpec(StrictSpec):
    horizon: int = Field(default=5, gt=0)
    execution_lag: int = Field(default=1, gt=0)
    price_column: str = "adj_open"
    availability_delay_days: int = Field(default=0, ge=0)


class SegmentSpec(StrictSpec):
    start: str
    end: str


class FoldSpec(StrictSpec):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
    train: SegmentSpec
    early_stop: SegmentSpec
    selection: SegmentSpec


class ValidationSpec(StrictSpec):
    folds: list[FoldSpec] = Field(default_factory=list)
    holdout_start: str = "2026-01-01"
    holdout_independent: bool = False
    annualization_days: int = Field(default=252, gt=0)
    risk_free_rate: float = 0
    prediction_tolerance: float = Field(default=1e-8, gt=0)


class PortfolioPolicy(StrictSpec):
    initial_cash: float = Field(default=500_000, gt=0)
    k: float = Field(default=0.05, gt=0)
    k_mode: Literal["fraction", "count", "weight_cap"] | None = "fraction"
    commission_rate: float = Field(default=0.003, ge=0, lt=1)
    minimum_commission: float | None = Field(default=None, ge=0)
    slippage_rate: float = Field(default=0.0003, ge=0, lt=1)
    liquidity: float = Field(default=0.20, gt=0, le=1)
    liquidity_mode: Literal["participation"] | None = "participation"
    liquidity_lookback: int = Field(default=20, gt=0)
    risk: float = Field(default=0.12, gt=0, le=1)
    # Risk trigger can be set earlier than the frozen ex-post drawdown gate.
    max_drawdown_limit: float = Field(default=0.12, gt=0, le=1)
    risk_mode: Literal["max_drawdown", "annualized_volatility"] | None = "max_drawdown"
    max_weight: float = Field(default=1, gt=0, le=1)
    max_group_weight: float = Field(default=1, gt=0, le=1)
    max_turnover: float = Field(default=1, gt=0, le=2)
    lot_size: int = Field(default=100, gt=0)
    schedule: Literal["mid_month_month_end"] = "mid_month_month_end"
    mid_month_day: int = Field(default=15, ge=1, le=28)

    @model_validator(mode="after")
    def valid_count(self):
        if self.k_mode == "count" and not float(self.k).is_integer():
            raise ValueError("Count K must be an integer")
        if self.k_mode in ("fraction", "weight_cap") and self.k > 1:
            raise ValueError("Proportion K must be <= 1")
        if self.risk_mode == "max_drawdown" and self.risk > self.max_drawdown_limit:
            raise ValueError("Drawdown trigger cannot exceed its acceptance limit")
        return self

    def require_resolved(self) -> None:
        missing = [key for key in ("k_mode", "minimum_commission",
                                   "liquidity_mode", "risk_mode")
                   if getattr(self, key) is None]
        if missing:
            raise ConfigurationError("Unresolved portfolio rules: " + ", ".join(missing))


class RuntimeLimits(StrictSpec):
    timeout_seconds: float = Field(default=120, gt=0)
    memory_mb: int = Field(default=1024, ge=64)
    cpu_count: int = Field(default=2, gt=0)
    max_output_bytes: int = Field(default=1_000_000, gt=0)
    image: str | None = None


class ResearchPolicy(StrictSpec):
    budget_mode: Literal["unlimited", "free_only", "capped"] | None = None
    api_budget: float | None = Field(default=None, ge=0)
    max_trials: int | None = Field(default=1, gt=0)
    max_dispatches_per_trial: int = Field(default=5, ge=1)
    max_repairs_per_trial: int = Field(default=2, ge=0)
    max_campaign_wall_seconds: int = Field(default=28_800, ge=1)
    maximum_lookback: int = Field(default=120, gt=0)
    coverage_threshold: float = Field(default=0.95, gt=0, le=1)
    seeds: list[int] = Field(default_factory=lambda: [42, 43, 44])
    max_drawdown_deterioration: float = Field(default=0, ge=0)
    max_turnover_deterioration: float = Field(default=0, ge=0)
    # Formal first-loop entry points reject an unresolved value before any
    # model/provider work.  Legacy protocols retain their original null.
    stress_min_excess_return: float | None = None
    limits: RuntimeLimits = Field(default_factory=RuntimeLimits)

    @model_validator(mode="after")
    def valid_budget(self):
        if self.budget_mode == "capped" and self.api_budget is None:
            raise ValueError("Capped research needs api_budget")
        if self.budget_mode == "free_only" and self.api_budget not in (None, 0):
            raise ValueError("Free-only research cannot have a paid budget")
        if self.budget_mode == "unlimited" and self.api_budget is not None:
            raise ValueError("Unlimited research must not have a monetary cap")
        return self


class ModelSpec(StrictSpec):
    name: Literal["ridge", "lightgbm", "xgboost"] = "lightgbm"
    seed: int = 42
    constructor: dict[str, Any] = Field(default_factory=dict)
    fit: dict[str, Any] = Field(default_factory=dict)


class BenchmarkPolicy(StrictSpec):
    momentum_lookback: int = Field(default=20, gt=0, le=120)


class AcceptancePolicy(StrictSpec):
    minimum_net_return: float | None = Field(default=None, gt=-1)
    minimum_excess_return: float | None = None
    maximum_drawdown: float | None = Field(default=None, ge=0, le=1)
    maximum_annualized_volatility: float | None = Field(default=None, ge=0)
    maximum_execution_cost_over_initial_equity: float | None = Field(default=None, ge=0)
    minimum_effective_dates: int | None = Field(default=None, gt=0)

    def require_resolved(self):
        missing = [name for name in type(self).model_fields if getattr(self, name) is None]
        if missing:
            raise ConfigurationError("Unresolved final acceptance thresholds: " + ", ".join(missing))


class AppConfig(StrictSpec):
    data: DataSpec = Field(default_factory=DataSpec)
    universe: UniversePolicy = Field(default_factory=UniversePolicy)
    label: LabelSpec = Field(default_factory=LabelSpec)
    validation: ValidationSpec = Field(default_factory=ValidationSpec)
    portfolio: PortfolioPolicy = Field(default_factory=PortfolioPolicy)
    research: ResearchPolicy = Field(default_factory=ResearchPolicy)
    benchmarks: BenchmarkPolicy = Field(default_factory=BenchmarkPolicy)
    acceptance: AcceptancePolicy = Field(default_factory=AcceptancePolicy)
    models: list[ModelSpec] = Field(default_factory=lambda: [
        ModelSpec(name="ridge"), ModelSpec(name="lightgbm")])
    artifact_root: Path = Path("artifacts")
    reuse_root: Path | None = None

    @property
    def config_hash(self) -> str:
        return content_hash(self)


@dataclass(frozen=True)
class DataSnapshot:
    snapshot_id: str
    path: Path
    manifest: dict[str, Any]


@dataclass(frozen=True)
class FeatureArtifact:
    feature_set_id: str
    frame: Any
    manifest: dict[str, Any]


@dataclass
class EvaluationResult:
    status: Literal["accepted", "rejected", "inconclusive", "failed"]
    stage: str
    run_id: str
    baseline_id: str | None = None
    candidate_id: str | None = None
    by_fold: list[dict[str, Any]] = field(default_factory=list)
    paired_deltas: list[dict[str, Any]] = field(default_factory=list)
    factor_signal_by_fold: list[dict[str, Any]] = field(default_factory=list)
    factor_signal_gate: dict[str, Any] = field(default_factory=dict)
    gate_details: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    returncode: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    reason: str | None = None
