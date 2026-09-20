from __future__ import annotations

import json
import re
import time
from pathlib import Path

from etf_ml.errors import ConfigurationError, IntegrityError, QualityError
from etf_ml.utils import FileLock, atomic_json, content_hash, ensure_within, file_hash


class ModelVersionRegistry:
    """Immutable package identities with append-only, hash-linked decisions."""
    transitions = {"frozen": {"accepted", "rejected", "retired"},
                   "accepted": {"retired"}, "rejected": {"retired"}, "retired": set()}

    def __init__(self, root):
        self.root = Path(root).resolve()

    def _path(self, version_id):
        if not re.fullmatch(r"[0-9a-f]{64}", version_id):
            raise ConfigurationError("Invalid model version identity")
        return ensure_within(self.root / version_id, self.root)

    def register(self, package_path):
        package_path = Path(package_path).resolve()
        manifest_path = package_path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        version_id = manifest["version_id"]
        if (manifest.get("kind") != "frozen_model_version" or
                content_hash({k: v for k, v in manifest.items() if k != "version_id"}) != version_id):
            raise IntegrityError("Model version package identity invalid")
        path = self._path(version_id)
        definition = {"schema_version": 1, "version_id": version_id,
                      "package_path": str(package_path), "manifest_sha256": file_hash(manifest_path)}
        with FileLock(self.root / ".locks" / (version_id + ".lock")):
            if (path / "head.json").exists():
                current = self.load(version_id)
                if current["definition"] != definition:
                    raise IntegrityError("Immutable model registration changed")
                return current
            path.mkdir(parents=True, exist_ok=True)
            # A crash before the head is published may leave this same definition.
            if (path / "definition.json").exists():
                if json.loads((path / "definition.json").read_text(encoding="utf-8")) != definition:
                    raise IntegrityError("Incomplete model registration identity changed")
            atomic_json(path / "definition.json", definition)
            event = {"state": "frozen", "sequence": 0, "previous": None,
                     "evidence": None, "reasons": ["explicit_development_selection"],
                     "created_at_ns": time.time_ns()}
            self._publish(path, event)
            return self.load(version_id)

    @staticmethod
    def _publish(path, event):
        identity = content_hash(event)
        atomic_json(path / "events" / (identity + ".json"), event)
        atomic_json(path / "head.json", {"event_id": identity,
                                        "definition_sha256": file_hash(path / "definition.json")})

    def load(self, version_id):
        path = self._path(version_id)
        head = json.loads((path / "head.json").read_text(encoding="utf-8"))
        if file_hash(path / "definition.json") != head["definition_sha256"]:
            raise IntegrityError("Model version definition changed")
        definition = json.loads((path / "definition.json").read_text(encoding="utf-8"))
        if definition.get("schema_version") != 1 or definition["version_id"] != version_id:
            raise IntegrityError("Model registry identity changed")
        if file_hash(Path(definition["package_path"]) / "manifest.json") != definition["manifest_sha256"]:
            raise IntegrityError("Registered model package manifest changed")
        events, seen, current = [], set(), head["event_id"]
        while current is not None:
            if current in seen or not re.fullmatch(r"[0-9a-f]{64}", current):
                raise IntegrityError("Model version event identity invalid")
            seen.add(current)
            event = json.loads((path / "events" / (current + ".json")).read_text(encoding="utf-8"))
            if content_hash(event) != current:
                raise IntegrityError("Model version history changed")
            if event["evidence"] is not None:
                proof = ensure_within(path / event["evidence"]["path"], path)
                if file_hash(proof) != event["evidence"]["sha256"]:
                    raise IntegrityError("Model version acceptance evidence changed")
            events.append({"event_id": current, **event})
            current = event["previous"]
        events.reverse()
        if not events or events[0]["state"] != "frozen":
            raise IntegrityError("Model version history must start frozen")
        for sequence, event in enumerate(events):
            if (event["sequence"] != sequence or event["state"] not in self.transitions or
                    (sequence and event["state"] not in self.transitions[events[sequence - 1]["state"]])):
                raise IntegrityError("Model version state history invalid")
        return {"definition": definition, "state": events[-1]["state"], "events": events,
                "path": str(path)}

    def transition(self, version_id, state, *, evidence=None, reasons=None):
        path = self._path(version_id)
        with FileLock(self.root / ".locks" / (version_id + ".lock")):
            current = self.load(version_id)
            if state not in self.transitions[current["state"]]:
                raise ConfigurationError("Illegal model version state transition")
            reasons = list(reasons or [])
            proof = None
            if state == "retired" and not reasons:
                raise QualityError("Model retirement requires an explicit reason")
            if state in ("accepted", "rejected"):
                if evidence is None:
                    raise QualityError("Model decision requires independent holdout evidence")
                raw = Path(evidence).read_bytes()
                payload = json.loads(raw.decode("utf-8"))
                package = json.loads((Path(current["definition"]["package_path"]) / "manifest.json").read_text(encoding="utf-8"))
                if (payload.get("version_id") != version_id or payload.get("stage") != "independent_holdout" or
                        payload.get("status") != ("passed" if state == "accepted" else "failed") or
                        payload.get("protocol_id") != package["protocol_id"] or
                        payload.get("acceptance") != package["acceptance"]):
                    raise QualityError("Independent model acceptance evidence identity mismatch")
                import hashlib
                digest = hashlib.sha256(raw).hexdigest()
                target = path / "evidence" / (digest + ".json")
                target.parent.mkdir(exist_ok=True)
                if not target.exists():
                    temporary = target.with_name(".copy-" + str(time.time_ns()) + ".tmp")
                    try:
                        temporary.write_bytes(raw)
                        temporary.replace(target)
                    finally:
                        temporary.unlink(missing_ok=True)
                if file_hash(target) != digest:
                    raise IntegrityError("Model decision evidence copy changed")
                proof = {"path": target.relative_to(path).as_posix(), "sha256": digest}
            event = {"state": state, "sequence": len(current["events"]),
                     "previous": current["events"][-1]["event_id"], "evidence": proof,
                     "reasons": reasons, "created_at_ns": time.time_ns()}
            self._publish(path, event)
            return self.load(version_id)