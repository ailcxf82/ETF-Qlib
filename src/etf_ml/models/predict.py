from __future__ import annotations

import numpy as np
import pandas as pd

from etf_ml.data.source import require_panel
from etf_ml.errors import QualityError


def predict(bundle, features: pd.DataFrame, *, as_of=None) -> pd.Series:
    from qlib.data.dataset import DatasetH
    from qlib.data.dataset.handler import DataHandlerLP
    require_panel(features, numeric=True)
    if as_of is not None:
        features = features[features.index.get_level_values("datetime") <= pd.Timestamp(as_of)]
    if features.empty:
        raise QualityError("No inference rows")
    transformed = bundle.processor.transform(features)
    # Inference never needs future labels or a learning-state dropna.
    table = pd.concat({"feature": transformed}, axis=1)
    dataset = DatasetH(handler=DataHandlerLP.from_df(table),
                       segments={"infer": (features.index.get_level_values("datetime").min(),
                                           features.index.get_level_values("datetime").max())})
    scores = bundle.model.predict(dataset, segment="infer").rename("score")
    if not scores.index.equals(features.index) or not np.isfinite(scores).all():
        raise QualityError("Invalid model prediction index or values")
    scores.attrs.update({key: bundle.manifest[key] for key in
                         ("model_id", "feature_set_id", "snapshot_id", "horizon", "score_type")})
    scores.attrs["as_of"] = str(as_of if as_of is not None else
                              features.index.get_level_values("datetime").max())
    return scores
