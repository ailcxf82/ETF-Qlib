from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import re
import time
from pathlib import Path

from etf_ml.errors import ConfigurationError, IntegrityError
from etf_ml.utils import FileLock, atomic_json, content_hash, ensure_within, source_hashes, verify_files

RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}$")


def environment_manifest() -> dict:
    names = ("pyqlib", "rdagent", "lightgbm", "pandas", "numpy", "scikit-learn", "pyarrow", "tables", "psutil", "xgboost")
    versions = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return {"python": platform.python_version(), "platform": platform.platform(),
            "packages": versions}


class RunStore:
    def __init__(self, root: Path, run_id: str, config: dict, *, preserve_completed_on_error: bool = False):
        if not RUN_ID.fullmatch(run_id):
            raise ConfigurationError("Invalid run_id")
        self.root, self.run_id = Path(root), run_id
        self.path = ensure_within(self.root / run_id, self.root)
        self.config = config
        self.identity = content_hash(config)
        self.lock = FileLock(self.root / ".locks" / (run_id + ".lock"))
        self.reused = False
        self.preserve_completed_on_error = preserve_completed_on_error

    def __enter__(self):
        self.lock.__enter__()
        try:
            self.path.mkdir(parents=True, exist_ok=True)
            status_path = self.path / "status.json"
            if status_path.exists():
                status = json.loads(status_path.read_text(encoding="utf-8"))
                if status["config_hash"] != self.identity:
                    raise ConfigurationError("run_id already belongs to another configuration")
                if status["status"] == "completed":
                    manifest = json.loads((self.path / "manifest.json").read_text(encoding="utf-8"))
                    verify_files(self.path, manifest["files"])
                    self.reused = True
                    return self
                # An incomplete attempt is retained, never mixed with a new attempt.
                attempt = self.path / ("attempt-" + str(time.time_ns()))
                attempt.mkdir()
                for item in list(self.path.iterdir()):
                    if item != attempt and not item.name.startswith("attempt-"):
                        os.replace(item, attempt / item.name)
            atomic_json(self.path / "config.json", self.config)
            atomic_json(self.path / "environment.json", environment_manifest())
            atomic_json(status_path, {"status": "running", "config_hash": self.identity,
                                      "run_id": self.run_id})
            return self
        except BaseException:
            self.lock.__exit__()
            raise

    def complete(self, metrics: dict | None = None) -> None:
        if self.reused:
            return
        if metrics is not None:
            atomic_json(self.path / "metrics.json", metrics)
        hashes = source_hashes(self.path)
        hashes = {k: v for k, v in hashes.items()
                  if not k.startswith("attempt-") and k not in ("status.json", "manifest.json")}
        atomic_json(self.path / "manifest.json", {"run_id": self.run_id, "files": hashes,
                                                 "config_hash": self.identity})
        atomic_json(self.path / "status.json", {"status": "completed", "run_id": self.run_id,
                                               "config_hash": self.identity})

    def __exit__(self, exc_type, exc, tb):
        try:
            if not self.reused:
                status = json.loads((self.path / "status.json").read_text(encoding="utf-8"))
                # Independent data calculation may already be committed while
                # a later registry/usage decision publication is interrupted.
                if ((exc is not None and not (self.preserve_completed_on_error and status["status"] == "completed"))
                        or status["status"] == "running"):
                    atomic_json(self.path / "status.json", {
                        "status": "failed", "run_id": self.run_id,
                        "config_hash": self.identity,
                        "reason": getattr(exc, "reason", "incomplete_execution"),
                        "exception_type": type(exc).__name__ if exc else None})
        finally:
            self.lock.__exit__()
