from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from etf_ml.contracts import AppConfig, FoldSpec
from etf_ml.errors import ConfigurationError


def merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        result[key] = merge(result[key], value) if (
            isinstance(value, dict) and isinstance(result.get(key), dict)) else value
    return result


def default_folds() -> list[FoldSpec]:
    rows = [
        ("A", "2022-12-31", "2023-01-01", "2023-06-30", "2023-07-01", "2023-12-31"),
        ("B", "2023-06-30", "2023-07-01", "2023-12-31", "2024-01-01", "2024-06-30"),
        ("C", "2023-12-31", "2024-01-01", "2024-06-30", "2024-07-01", "2024-12-31"),
        ("D", "2024-06-30", "2024-07-01", "2024-12-31", "2025-01-01", "2025-06-30"),
        ("E", "2024-12-31", "2025-01-01", "2025-06-30", "2025-07-01", "2025-12-31"),
    ]
    return [FoldSpec(name=name, train={"start": "2020-01-01", "end": train_end},
                     early_stop={"start": es_start, "end": es_end},
                     selection={"start": sel_start, "end": sel_end})
            for name, train_end, es_start, es_end, sel_start, sel_end in rows]


def load_config(path: Path | None = None, overrides: dict[str, Any] | None = None) -> AppConfig:
    defaults = AppConfig().model_dump(mode="json")
    defaults["validation"]["folds"] = [f.model_dump() for f in default_folds()]
    try:
        if path:
            supplied = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
            if not isinstance(supplied, dict):
                raise ConfigurationError("Config must be a YAML mapping")
            defaults = merge(defaults, supplied)
        defaults = merge(defaults, overrides or {})
        return AppConfig.model_validate(defaults)
    except (ValidationError, OSError, yaml.YAMLError) as exc:
        # Validation strings can include input values; do not log arbitrary secrets.
        raise ConfigurationError(f"Cannot load configuration ({type(exc).__name__})") from exc
