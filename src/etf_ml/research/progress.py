"""Append-only run events and an atomic latest-state view."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from etf_ml.utils import atomic_json, canonical_json, redact


TERMINAL_STATUSES = {"completed", "failed", "incomplete", "cancelled"}


class Progress:
    def __init__(self, root: Path, run_id: str, component: str):
        self.root = Path(root)
        self.run_id = run_id
        self.component = component

    def emit(self, phase: str, *, status: str = "running", **details) -> dict:
        event = redact({"timestamp": datetime.now(timezone.utc).isoformat(),
                        "run_id": self.run_id, "component": self.component,
                        "phase": phase, "status": status, **details})
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "progress.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical_json(event) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        atomic_json(self.root / "progress.json", event)
        return event


def monitor_run(root: Path, *, interval: float = 1.0, once: bool = False,
                related_root: Path | None = None) -> int:
    """Print changed progress snapshots until the selected run reaches a terminal state."""
    if interval <= 0:
        raise ValueError("Monitor interval must be positive")
    root = Path(root).resolve()
    roots = [root] + ([Path(related_root).resolve()] if related_root else [])
    seen: dict[Path, str] = {}
    while True:
        if not root.exists() and once:
            print(json.dumps({"status": "not_found", "path": str(root)}, ensure_ascii=False), flush=True)
            return 2
        status_path = root / "status.json"
        try:
            status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else None
        except (OSError, json.JSONDecodeError):
            status = None
        if status and status.get("status") in TERMINAL_STATUSES:
            print(json.dumps({"path": str(root), **status}, ensure_ascii=False), flush=True)
            return 0 if status["status"] == "completed" else 4
        paths = sorted(path for source in roots if source.exists()
                       for path in source.rglob("progress.json"))
        for path in paths:
            try:
                payload = path.read_text(encoding="utf-8")
                event = json.loads(payload)
            except (OSError, json.JSONDecodeError):
                continue
            if seen.get(path) != payload:
                print(json.dumps({"path": str(path.parent), **event}, ensure_ascii=False), flush=True)
                seen[path] = payload
        if once:
            return 0
        if not status and root.exists() and seen and all(
                json.loads(value)["status"] in TERMINAL_STATUSES
                for value in seen.values()):
            return 0
        time.sleep(interval)


def mark_cancelled(root: Path, *, reason: str = "operator_stop") -> dict:
    """Record an operator-stopped run as terminal after its process has exited."""
    root = Path(root).resolve()
    status_path = root / "status.json"
    if not status_path.is_file():
        raise FileNotFoundError(f"Run status does not exist: {status_path}")
    status = json.loads(status_path.read_text(encoding="utf-8"))
    if status.get("status") in TERMINAL_STATUSES:
        return status
    status.update({"status": "cancelled", "reason": reason,
                   "cancelled_at": datetime.now(timezone.utc).isoformat()})
    atomic_json(status_path, status)
    Progress(root, str(status.get("run_id", root.name)), "run").emit(
        "cancelled", status="cancelled", reason=reason)
    return status
