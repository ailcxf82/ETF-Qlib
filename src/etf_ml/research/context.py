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
    feature_descriptors: list[dict] = Field(default_factory=list)
    model: dict
    selection_rules: dict
    runtime: dict
    feedback: list[dict] = Field(default_factory=list)
    # A prompt is part of a research protocol.  v1 contexts remain readable,
    # while all newly created sessions use the explicit v2 projection.
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
    # v2 semantic fields are deliberately optional for v1 registry records.
    # Their absence is not silently filled with an economic story when an old
    # record is read.  New proposals set schema_version=2 and must satisfy the
    # checks below.
    schema_version: int = Field(default=1, ge=1)
    reason: str | None = None
    mechanism: str | None = None
    direction_reason: str | None = None
    failure_conditions: list[str] | None = None
    compared_features: list[str] | None = None
    trusted_implementation: dict | None = None

    @model_validator(mode="after")
    def valid_window(self):
        if self.minimum_observations > self.lookback:
            raise ValueError("minimum_observations exceeds declared lookback")
        if any("label" in f.lower() or "target" in f.lower() for f in self.required_fields):
            raise ValueError("Factors cannot read labels")
        if self.schema_version >= 2:
            required_text = {
                "reason": self.reason,
                "mechanism": self.mechanism,
                "direction_reason": self.direction_reason,
            }
            missing = [name for name, value in required_text.items()
                       if not isinstance(value, str) or not value.strip()]
            if missing:
                raise ValueError("Factor proposal v2 missing semantic fields: " + ", ".join(missing))
            if not self.failure_conditions or not all(isinstance(item, str) and item.strip()
                                                      for item in self.failure_conditions):
                raise ValueError("Factor proposal v2 needs observable failure_conditions")
            placeholders = {"economics", "algorithm", "new information", "unclassified"}
            if self.hypothesis.strip().lower() == self.factor_id.strip().lower():
                raise ValueError("Factor hypothesis cannot only repeat factor_id")
            if self.expected_difference.strip().lower() in placeholders:
                raise ValueError("Factor proposal v2 expected_difference is a placeholder")
            if self.research_group.strip().lower() == "unclassified":
                raise ValueError("Factor proposal v2 requires a reviewed research_group")
        return self

    @property
    def source_hash(self):
        return content_hash(self.source)

    @property
    def version_id(self):
        # Preserve identities of v1 registry definitions exactly.  This is
        # important because a registry must be able to verify old immutable
        # specifications after v2 fields are introduced.
        payload = self.model_dump(mode="json")
        if self.schema_version == 1:
            for key in ("schema_version", "reason", "mechanism", "direction_reason",
                        "failure_conditions", "compared_features"):
                payload.pop(key, None)
            if self.trusted_implementation is None:
                payload.pop("trusted_implementation", None)
        return content_hash(payload)

    def validate_context(self, context: ResearchContext):
        if self.context_hash != context.context_hash:
            raise QualityError("Candidate ResearchContext mismatch")
        if not set(self.required_fields).issubset(context.fields):
            raise QualityError("Candidate requires unavailable fields")
        if self.lookback > context.runtime.get("maximum_lookback", 120):
            raise QualityError("Candidate exceeds frozen lookback")
        if self.schema_version >= 2:
            unavailable = [field for field in self.required_fields
                           if context.fields[field].get("allowed_usage", "proposal") != "proposal"]
            if unavailable:
                raise QualityError("Candidate requires fields unavailable for proposals: " + ", ".join(unavailable))
        if self.trusted_implementation is not None:
            from etf_ml.features.alpha101 import SOURCE, validate_library_reference
            definition = validate_library_reference(self.trusted_implementation)
            from etf_ml.utils import content_hash
            if context.runtime.get("trusted_library_source_hash") != content_hash(SOURCE):
                raise QualityError("Trusted library execution was not enabled by the controller")
            if (not self.cross_sectional or self.formula != definition["formula"]
                    or set(self.required_fields) != set(definition["required_fields"])
                    or self.lookback != definition["lookback"]
                    or self.minimum_observations != definition["minimum_observations"]):
                raise QualityError("Trusted factor specification differs from the registered definition")
            from etf_ml.features.alpha101 import library_source_descriptor
            if self.source != library_source_descriptor(self.trusted_implementation):
                raise QualityError("Trusted library source descriptor differs from its reference")
