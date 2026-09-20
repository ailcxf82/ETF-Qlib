from __future__ import annotations

from dataclasses import dataclass
import pandas as pd

from etf_ml.contracts import FoldSpec
from etf_ml.data.source import require_panel
from etf_ml.datasets.processors import InputProcessor
from etf_ml.datasets.splits import learning_mask, validate_fold
from etf_ml.errors import ConfigurationError, QualityError
from etf_ml.utils import content_hash


@dataclass
class PreparedDataset:
    dataset: object
    processor: InputProcessor
    manifest: dict
    features: pd.DataFrame
    labels: pd.Series
    masks: dict[str, pd.Series]


def build(feature_set, labels: pd.Series, events: pd.DataFrame, split_spec: FoldSpec,
          *, model_kind="lightgbm", eligibility: pd.Series | None = None,
          holdout_start="2026-01-01") -> PreparedDataset:
    from qlib.data.dataset import DatasetH
    from etf_ml.datasets.handler import ETFDataHandler
    features = feature_set.frame if hasattr(feature_set, "frame") else feature_set
    require_panel(features, numeric=True)
    validate_fold(split_spec, holdout_start)
    if not features.index.equals(labels.index) or not features.index.equals(events.index):
        raise QualityError("Features, labels and events must have identical indices")
    if (features.index.get_level_values("datetime") >= pd.Timestamp(holdout_start)).any():
        raise ConfigurationError("Development dataset includes holdout rows")
    eligible = pd.Series(True, index=features.index) if eligibility is None else eligibility
    if not eligible.index.equals(features.index) or eligible.isna().any():
        raise QualityError("Eligibility index differs from fixed target")
    masks = {}
    segments = {
        "train": (split_spec.train, split_spec.early_stop.start),
        "valid": (split_spec.early_stop, split_spec.selection.start),
        "test": (split_spec.selection, holdout_start)}
    for role, (segment, next_start) in segments.items():
        masks[role] = learning_mask(events, labels, segment,
                                    next_start=next_start) & eligible
        if not masks[role].any():
            raise QualityError("No mature rows in " + role)
    processor = InputProcessor(model_kind).fit(features[masks["train"]])
    transformed = processor.transform(features)
    table = pd.concat({"feature": transformed, "label": labels.to_frame()}, axis=1)
    learn_mask = masks["train"] | masks["valid"] | masks["test"]
    handler = ETFDataHandler(table, table.index[learn_mask])
    dataset = DatasetH(handler=handler, segments={
        role: (segment.start, segment.end) for role, (segment, _) in segments.items()})
    manifest = {"fold": split_spec.model_dump(), "holdout_start": holdout_start,
                "qlib_roles": {"valid": "early_stop", "test": "selection"},
                "features": list(features.columns), "processor_kind": model_kind,
                "processor_fit_index_hash": processor.fitted_index_hash,
                "counts": {role: int(mask.sum()) for role, mask in masks.items()},
                "sample_index_hashes": {role: content_hash([[str(t), i] for t, i in features.index[mask]])
                                        for role, mask in masks.items()},
                "evaluation_index_hash": content_hash([
                    [str(t), i] for t, i in features.index[masks["test"]]])}
    return PreparedDataset(dataset, processor, manifest, features, labels, masks)
