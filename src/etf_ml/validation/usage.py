from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pandas as pd

from etf_ml.errors import ConfigurationError, IntegrityError
from etf_ml.artifacts import RUN_ID
from etf_ml.utils import FileLock, atomic_json, content_hash, ensure_within, filesystem_path


class HoldoutUsageStore:
    """A project-wide immutable claim prevents overlapping holdout reuse."""
    def __init__(self, root):
        self.root = filesystem_path(Path(root).resolve())

    def claim(self, identity, *, run_id):
        if not RUN_ID.fullmatch(run_id):
            raise ConfigurationError("Invalid holdout run identity")
        start, end = pd.Timestamp(identity["start"]), pd.Timestamp(identity["end"])
        if start > end:
            raise ConfigurationError("Empty independent holdout interval")
        with FileLock(self.root / ".lock"):
            for path in sorted((self.root / "claims").glob("*.json")):
                claim = json.loads(path.read_text(encoding="utf-8"))
                if claim.get("usage_id") != path.stem or path.stem != content_hash({k: v for k, v in claim.items() if k != "usage_id"}):
                    raise IntegrityError("Holdout usage claim changed")
                previous = claim["identity"]
                if start <= pd.Timestamp(previous["end"]) and end >= pd.Timestamp(previous["start"]):
                    if previous != identity:
                        raise ConfigurationError("Holdout interval already consumed by another frozen candidate or protocol")
                    return claim
            payload = {"identity": identity, "run_id": run_id, "created_at_ns": time.time_ns()}
            usage_id = content_hash(payload)
            claim = {**payload, "usage_id": usage_id}
            atomic_json(self.root / "claims" / (usage_id + ".json"), claim)
            return claim

    def history(self, usage_id):
        if not re.fullmatch(r"[0-9a-f]{64}", usage_id):
            raise ConfigurationError("Invalid holdout usage identity")
        root = ensure_within(self.root / "history" / usage_id, self.root)
        head = root / "head.json"
        if not head.exists():
            return []
        current = json.loads(head.read_text(encoding="utf-8"))["event_id"]
        events, seen = [], set()
        while current is not None:
            if current in seen or not re.fullmatch(r"[0-9a-f]{64}", current):
                raise IntegrityError("Holdout usage history identity invalid")
            seen.add(current)
            event = json.loads((root / (current + ".json")).read_text(encoding="utf-8"))
            if content_hash(event) != current:
                raise IntegrityError("Holdout usage history changed")
            events.append({"event_id": current, **event})
            current = event["previous"]
        events.reverse()
        for sequence, event in enumerate(events):
            if event["sequence"] != sequence or event["status"] not in {"started", "technical_failed", "completed"}:
                raise IntegrityError("Holdout usage history sequence invalid")
        return events

    def record(self, usage_id, status, *, details):
        if status not in {"started", "technical_failed", "completed"}:
            raise ConfigurationError("Invalid holdout usage state")
        with FileLock(self.root / ".lock"):
            history = self.history(usage_id)
            if history and history[-1]["status"] == "completed":
                if status == "completed" and history[-1]["details"] == details:
                    return history[-1]
                raise ConfigurationError("Completed holdout usage cannot be restarted")
            event = {"status": status, "sequence": len(history),
                     "previous": history[-1]["event_id"] if history else None,
                     "details": details, "created_at_ns": time.time_ns()}
            event_id = content_hash(event)
            root = ensure_within(self.root / "history" / usage_id, self.root)
            atomic_json(root / (event_id + ".json"), event)
            atomic_json(root / "head.json", {"event_id": event_id})
            return {"event_id": event_id, **event}