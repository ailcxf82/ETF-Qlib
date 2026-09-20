from __future__ import annotations

from pydantic import Field, model_validator

from etf_ml.artifacts import environment_manifest
from etf_ml.contracts import (StrictSpec, LabelSpec, ModelSpec, PortfolioPolicy,
                              ResearchPolicy, UniversePolicy, ValidationSpec, BenchmarkPolicy)
from etf_ml.errors import ConfigurationError
from etf_ml.utils import code_hash, content_hash


class ComparisonProtocol(StrictSpec):
    """Freeze all experimental variables before looking at a candidate result."""
    snapshot_id: str
    baseline_feature_set_id: str
    label: LabelSpec
    universe: UniversePolicy
    validation: ValidationSpec
    portfolio: PortfolioPolicy
    research: ResearchPolicy
    model: ModelSpec
    benchmarks: BenchmarkPolicy = Field(default_factory=BenchmarkPolicy)
    cost_multipliers: list[float] = Field(default_factory=lambda: [2.0])
    stress_min_excess_return: float | None = None
    time_block_length: int = Field(default=20, gt=0)
    bootstrap_repetitions: int = Field(default=1000, ge=100)
    bootstrap_seed: int = 42
    environment: dict = Field(default_factory=environment_manifest)
    source_code_hash: str = Field(default_factory=code_hash)

    @model_validator(mode="after")
    def check_matrix(self):
        if self.time_block_length < self.label.horizon:
            raise ValueError("Time blocks must cover at least the overlapping label horizon")
        if self.model.name != "lightgbm":
            raise ValueError("Factor selection must use the fixed LightGBM primary model")
        if not self.validation.folds:
            raise ValueError("Comparison requires explicit development folds")
        if len({f.name for f in self.validation.folds}) != len(self.validation.folds):
            raise ValueError("Fold names must be unique")
        if len(self.research.seeds) < 2 or len(set(self.research.seeds)) != len(self.research.seeds):
            raise ValueError("Comparison needs at least two distinct frozen seeds")
        if not self.cost_multipliers or any(m <= 1 for m in self.cost_multipliers):
            raise ValueError("Cost pressure multipliers must exceed one")
        if len(set(self.cost_multipliers)) != len(self.cost_multipliers):
            raise ValueError("Cost pressure scenarios must be unique")
        # These rates must remain legal in every predeclared pressure scenario.
        if max(self.cost_multipliers) * max(self.portfolio.commission_rate,
                                          self.portfolio.slippage_rate) >= 1:
            raise ValueError("Cost pressure rate is invalid")
        return self

    @property
    def protocol_id(self) -> str:
        return content_hash(self)

    def require_runtime(self):
        self.portfolio.require_resolved()
        if self.source_code_hash != code_hash() or self.environment != environment_manifest():
            raise ConfigurationError("Frozen comparison runtime changed")
