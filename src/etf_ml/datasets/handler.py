from __future__ import annotations

from qlib.data.dataset.handler import DataHandlerLP
from qlib.data.dataset.loader import StaticDataLoader


class ETFDataHandler(DataHandlerLP):
    """Qlib handler with an explicitly purged learning view and full inference view."""

    def __init__(self, table, learning_index):
        self.learning_index = learning_index
        super().__init__(data_loader=StaticDataLoader(table),
                         process_type=DataHandlerLP.PTYPE_A)

    def process_data(self, with_fit=False):
        # Preprocessing has already been fit on this fold's mature train rows.
        self._infer = self._data
        self._learn = self._infer.loc[self.learning_index]
