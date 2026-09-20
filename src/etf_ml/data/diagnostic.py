"""Explicit current-universe assumptions for research; never PIT evidence."""
from __future__ import annotations

from etf_ml.errors import ConfigurationError, QualityError


def validate_diagnostic_metadata(metadata):
    if metadata.attrs.get("metadata_mode") != "diagnostic_current_universe":
        raise QualityError("Diagnostic metadata must explicitly declare its current-universe origin")
    if not metadata.attrs.get("diagnostic_assumptions"):
        raise QualityError("Diagnostic metadata requires recorded assumptions")
    if not metadata.attrs.get("source_hashes"):
        raise QualityError("Diagnostic metadata requires original source hashes")


def require_snapshot_mode(config, snapshot):
    mode = snapshot.manifest.get("spec", {}).get("mode", "formal")
    if mode != config.data.mode:
        raise ConfigurationError("Snapshot qualification differs from configured data mode")


def require_formal(config, snapshot=None):
    if config.data.mode != "formal" or (snapshot is not None and
            snapshot.manifest.get("spec", {}).get("mode", "formal") != "formal"):
        raise ConfigurationError("Diagnostic research cannot enter formal acceptance or deployment")
