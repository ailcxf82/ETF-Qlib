from __future__ import annotations

from typing import Literal
from pydantic import Field, model_validator

from etf_ml.contracts import StrictSpec
from etf_ml.errors import QualityError
from etf_ml.utils import content_hash


class ResearchContext(StrictSpec):
    snapshot_id: str
    baseline_id: str
    protocol_id: str
    visible_start: str
    visible_end: str
    horizon: int = Field(default=5, gt=0)
    fields: dict[str, dict]
    existing_features: list[str]
    model: dict
    selection_rules: dict
    runtime: dict
    feedback: list[dict] = Field(default_factory=list)
    prompt_version: str = "etf-factor-v1"

    @property
    def context_hash(self):
        return content_hash(self)


class FactorSpec(StrictSpec):
    factor_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,80}$")
    version: int = Field(default=1, ge=1)
    hypothesis: str = Field(min_length=1)
    formula: str = Field(min_length=1)
    required_fields: list[str] = Field(min_length=1)
    lookback: int = Field(gt=0, le=120)
    minimum_observations: int = Field(gt=0)
    available_at: str = "after_daily_ingestion"
    cross_sectional: bool = False
    direction: Literal["positive", "negative", "unknown"] = "unknown"
    missing_policy: str = "insufficient_history_is_missing"
    applicable_scope: str = "domestic_equity"
    complexity: str = "linear"
    research_group: str = Field(default="unclassified", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,60}$")
    expected_difference: str = Field(min_length=1)
    source: str = Field(min_length=1)
    context_hash: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid_window(self):
        if self.minimum_observations > self.lookback:
            raise ValueError("minimum_observations exceeds declared lookback")
        if any("label" in f.lower() or "target" in f.lower() for f in self.required_fields):
            raise ValueError("Factors cannot read labels")
        return self

    @property
    def source_hash(self):
        return content_hash(self.source)

    @property
    def version_id(self):
        return content_hash(self)

    def validate_context(self, context: ResearchContext):
        if self.context_hash != context.context_hash:
            raise QualityError("Candidate ResearchContext mismatch")
        if not set(self.required_fields).issubset(context.fields):
            raise QualityError("Candidate requires unavailable fields")
        if self.lookback > context.runtime.get("maximum_lookback", 120):
            raise QualityError("Candidate exceeds frozen lookback")
