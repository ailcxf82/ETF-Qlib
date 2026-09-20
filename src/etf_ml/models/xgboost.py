from __future__ import annotations

import pandas as pd
import xgboost as xgb
from qlib.contrib.model.xgboost import XGBModel
from qlib.data.dataset.handler import DataHandlerLP


class ETFXGBModel(XGBModel):
    """Qlib XGBModel with independent fit history and explicit early-stop inference."""

    def fit(self, dataset, *, evals_result=None, **kwargs):
        history = {} if evals_result is None else evals_result
        super().fit(dataset, evals_result=history, **kwargs)
        self.evals_result_ = history
        best = self.model.attr("best_iteration")
        end = int(best) + 1 if best is not None else self.model.num_boosted_rounds()
        self.prediction_iteration_range_ = (0, end)

    def predict(self, dataset, segment="test"):
        if self.model is None:
            raise ValueError("model is not fitted yet")
        features = dataset.prepare(segment, col_set="feature", data_key=DataHandlerLP.DK_I)
        values = self.model.predict(xgb.DMatrix(features.to_numpy()),
                                    iteration_range=self.prediction_iteration_range_)
        return pd.Series(values, index=features.index)
