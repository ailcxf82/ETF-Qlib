from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler

from etf_ml.data.source import require_panel
from etf_ml.errors import QualityError


@dataclass
class InputProcessor:
    kind: str
    columns: list[str] | None = None
    imputer: object | None = None
    scaler: object | None = None
    fitted_index_hash: str | None = None

    def fit(self, train: pd.DataFrame):
        from etf_ml.utils import content_hash
        require_panel(train, numeric=True)
        if train.empty:
            raise QualityError("No mature training rows")
        self.columns = list(train.columns)
        self.fitted_index_hash = content_hash([[str(t), i] for t, i in train.index])
        values = train.replace([np.inf, -np.inf], np.nan)
        if self.kind == "ridge":
            self.imputer = SimpleImputer(strategy="median", keep_empty_features=True)
            self.scaler = StandardScaler()
            self.scaler.fit(self.imputer.fit_transform(values))
        return self

    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        require_panel(frame, numeric=True)
        if self.columns is None or set(frame.columns) != set(self.columns):
            raise QualityError("Feature schema mismatch")
        values = frame.loc[:, self.columns].replace([np.inf, -np.inf], np.nan)
        if self.kind == "ridge":
            values = pd.DataFrame(self.scaler.transform(self.imputer.transform(values)),
                                  index=frame.index, columns=self.columns)
        return values
