from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

from etf_ml.data.calendar import read_calendar, require_calendar
from etf_ml.errors import DataNotReady, QualityError

INDEX_NAMES = ["datetime", "instrument"]


def require_panel(frame: pd.DataFrame | pd.Series, *, numeric: bool = False) -> None:
    if not isinstance(frame.index, pd.MultiIndex) or list(frame.index.names) != INDEX_NAMES:
        raise QualityError("Panel requires (datetime, instrument) index")
    if frame.index.has_duplicates:
        raise QualityError("Duplicate date/instrument keys")
    dates = frame.index.get_level_values("datetime")
    if not isinstance(dates, pd.DatetimeIndex) or dates.isna().any():
        raise QualityError("Panel datetime keys must be valid timestamps")
    if not frame.index.is_monotonic_increasing:
        raise QualityError("Panel must be sorted")
    if isinstance(frame, pd.DataFrame) and frame.columns.has_duplicates:
        raise QualityError("Duplicate feature columns")
    if numeric:
        values = frame.to_frame() if isinstance(frame, pd.Series) else frame
        if not all(pd.api.types.is_numeric_dtype(dtype) for dtype in values.dtypes):
            raise QualityError("Features must be numeric")


class QlibBinSource:
    """Decode positions using the original calendar; never mutate the provider."""

    def __init__(self, root: Path):
        self.root = Path(root)
        if not self.root.is_dir():
            raise DataNotReady("ETF provider directory not found")
        self.calendar = read_calendar(self.root / "calendars" / "day.txt")
        path = self.root / "instruments" / "all.txt"
        if not path.is_file():
            raise DataNotReady("Instrument history missing")
        self.instruments = pd.read_csv(path, sep="\t", header=None,
                                       names=["instrument", "start", "end"], dtype=str)
        if self.instruments["instrument"].duplicated().any():
            raise QualityError("Duplicate instrument definitions")
        for column in ("start", "end"):
            self.instruments[column] = pd.to_datetime(self.instruments[column], errors="raise")
        if (self.instruments["start"] > self.instruments["end"]).any():
            raise QualityError("Invalid instrument validity interval")

    def read(self, fields: dict[str, str], instruments: list[str] | None = None) -> pd.DataFrame:
        requested = set(instruments) if instruments is not None else None
        pieces = []
        for row in self.instruments.itertuples(index=False):
            if requested is not None and row.instrument not in requested:
                continue
            active = (self.calendar >= row.start) & (self.calendar <= row.end)
            selected = self.calendar[active]
            if selected.empty:
                continue
            columns = {}
            for output, field in fields.items():
                if not field.replace("_", "").isalnum():
                    raise QualityError("Unsafe provider field name")
                path = self.root / "features" / row.instrument.lower() / (field + ".day.bin")
                full = np.full(len(self.calendar), np.nan)
                if path.is_file():
                    if path.stat().st_size % 4:
                        raise QualityError("Malformed bin byte length")
                    values = np.fromfile(path, dtype="<f4")
                    if not len(values) or not np.isfinite(values[0]):
                        raise QualityError("Missing bin offset")
                    offset = int(values[0])
                    if values[0] != offset or offset < 0 or offset + len(values) - 1 > len(full):
                        raise QualityError("Bin offset exceeds original calendar")
                    full[offset:offset + len(values) - 1] = values[1:]
                columns[output] = full[active]
            part = pd.DataFrame(columns, index=selected)
            part["instrument"] = row.instrument.upper()
            pieces.append(part.reset_index().set_index(INDEX_NAMES))
        if not pieces:
            raise DataNotReady("No rows in requested ETF history")
        return pd.concat(pieces).sort_index()


def encode_provider(frame: pd.DataFrame, calendar: pd.DatetimeIndex,
                    root: Path, fields: dict[str, str],
                    future_calendar: pd.DatetimeIndex | None = None) -> None:
    """Encode a new provider. Caller supplies normalized Qlib-compatible fields."""
    require_panel(frame, numeric=True)
    require_calendar(calendar)
    if not frame.index.get_level_values("datetime").isin(calendar).all():
        raise QualityError("Cannot encode panel outside target calendar")
    root = Path(root)
    if root.exists():
        raise QualityError("Provider output must be new")
    (root / "calendars").mkdir(parents=True)
    (root / "instruments").mkdir()
    (root / "features").mkdir()
    (root / "calendars" / "day.txt").write_text(
        "\n".join(calendar.strftime("%Y-%m-%d")) + "\n", encoding="utf-8")
    if future_calendar is not None:
        require_calendar(future_calendar)
        if not calendar.isin(future_calendar).all():
            raise QualityError("Future calendar must cover all provider dates")
        (root / "calendars" / "day_future.txt").write_text(
            "\n".join(future_calendar.strftime("%Y-%m-%d")) + "\n", encoding="utf-8")
    definitions = []
    for instrument, group in frame.groupby(level="instrument", sort=True):
        if not isinstance(instrument, str) or not all(c.isalnum() or c == "." for c in instrument):
            raise QualityError("Unsafe instrument identifier")
        dates = group.index.get_level_values("datetime")
        start, end = calendar.get_indexer([dates.min(), dates.max()])
        definitions.append(f"{instrument}\t{dates.min():%Y-%m-%d}\t{dates.max():%Y-%m-%d}")
        directory = root / "features" / instrument.lower()
        directory.mkdir()
        aligned = group.droplevel("instrument").reindex(calendar[start:end + 1])
        for field, column in fields.items():
            values = np.concatenate([[start], aligned[column].to_numpy(dtype=float)])
            values.astype("<f4").tofile(directory / (field + ".day.bin"))
    (root / "instruments" / "all.txt").write_text(
        "\n".join(definitions) + "\n", encoding="utf-8")
