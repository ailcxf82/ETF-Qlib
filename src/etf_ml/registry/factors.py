from __future__ import annotations

import json
import os
import time
from pathlib import Path

from etf_ml.errors import ConfigurationError, IntegrityError, QualityError
from etf_ml.research.context import FactorSpec
from etf_ml.utils import FileLock, atomic_json, content_hash, ensure_within, file_hash

TRANSITIONS = {
    "proposed": {"validated", "rejected"},
    "validated": {"evaluated", "rejected"},
    "evaluated": {"candidate", "rejected"},
    "candidate": {"frozen", "rejected"},
    "frozen": {"retired"},
    "rejected": set(), "retired": set(),
}
REQUIRED_EVIDENCE = {
    "validated": "quality", "evaluated": "evaluation",
    "candidate": "evaluation", "frozen": "feature_set",
}


class FactorRegistry:
    """Append-only definitions and hash-linked states, published by an atomic head.

    A name/version is bound once. Evidence is copied into the trusted registry,
    so subsequent changes to a research workspace cannot alter an old decision.
    Unpublished events after a crash do not change the current committed state.
    """

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def _path(self, factor_id: str, version: int) -> Path:
        # Validate identifiers through the same schema used by the researcher.
        import re
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,80}", factor_id) or version < 1:
            raise ConfigurationError("Invalid factor identity")
        return ensure_within(self.root / factor_id / f"v{version}", self.root)

    def _lock(self, factor_id: str, version: int):
        return FileLock(self.root / ".locks" / f"{factor_id}-v{version}.lock")

    def register(self, spec: FactorSpec, *, lineage: dict) -> dict:
        path = self._path(spec.factor_id, spec.version)
        with self._lock(spec.factor_id, spec.version):
            if (path / "head.json").exists():
                current = self.load(spec.factor_id, spec.version)
                if current["definition"]["version_id"] != spec.version_id:
                    raise ConfigurationError("Factor definition changed: create a new version")
                if current["definition"]["lineage"] != lineage:
                    raise ConfigurationError("Registered lineage is immutable")
                return current
            # A failed initial publication cannot be confused with a new definition.
            if path.exists() and any(path.iterdir()):
                raise IntegrityError("Incomplete factor registration requires explicit recovery")
            destination = path
            path = ensure_within(self.root / ".staging" / (spec.version_id[:16] + "-" + format(time.time_ns(), "x")), self.root)
            path.mkdir(parents=True, exist_ok=False)
            (path / "source.py").write_text(spec.source, encoding="utf-8")
            atomic_json(path / "spec.json", spec.model_dump(mode="json"))
            definition = {
                "schema_version": 1, "factor_id": spec.factor_id, "version": spec.version,
                "version_id": spec.version_id, "lineage": lineage,
                "files": {"source.py": file_hash(path / "source.py"),
                          "spec.json": file_hash(path / "spec.json")},
            }
            atomic_json(path / "definition.json", definition)
            event = self._event("proposed", 0, None, {}, ["registered"])
            event_id = content_hash(event)
            atomic_json(path / "events" / (event_id + ".json"), event)
            atomic_json(path / "head.json", {
                "event_id": event_id, "definition_hash": file_hash(path / "definition.json")})
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, destination)
            return self.load(spec.factor_id, spec.version)

    @staticmethod
    def _event(state, sequence, previous, evidence, reasons):
        return {"state": state, "sequence": sequence, "previous": previous,
                "evidence": evidence, "reasons": reasons, "created_at_ns": time.time_ns()}

    def load(self, factor_id: str, version: int) -> dict:
        path = self._path(factor_id, version)
        head = json.loads((path / "head.json").read_text(encoding="utf-8"))
        if file_hash(path / "definition.json") != head["definition_hash"]:
            raise IntegrityError("Factor definition integrity failed")
        definition = json.loads((path / "definition.json").read_text(encoding="utf-8"))
        if definition["factor_id"] != factor_id or definition["version"] != version:
            raise IntegrityError("Factor identity mismatch")
        for name, expected in definition["files"].items():
            target = ensure_within(path / name, path)
            if file_hash(target) != expected:
                raise IntegrityError("Registered factor source or specification changed")
        spec = FactorSpec.model_validate_json((path / "spec.json").read_text(encoding="utf-8"))
        if spec.version_id != definition["version_id"]:
            raise IntegrityError("Factor version identity mismatch")
        events, event_id, seen = [], head["event_id"], set()
        while event_id is not None:
            if event_id in seen:
                raise IntegrityError("Factor history cycle")
            seen.add(event_id)
            target = ensure_within(path / "events" / (event_id + ".json"), path)
            event = json.loads(target.read_text(encoding="utf-8"))
            if content_hash(event) != event_id:
                raise IntegrityError("Factor history integrity failed")
            for item in event["evidence"].values():
                proof = ensure_within(path / item["path"], path)
                if file_hash(proof) != item["sha256"]:
                    raise IntegrityError("Registered decision evidence changed")
            events.append({"event_id": event_id, **event})
            event_id = event["previous"]
        events.reverse()
        for index, event in enumerate(events):
            if event["sequence"] != index:
                raise IntegrityError("Factor history sequence mismatch")
            if index == 0 and event["state"] != "proposed":
                raise IntegrityError("Factor history must start at proposed")
            if index and event["state"] not in TRANSITIONS[events[index - 1]["state"]]:
                raise IntegrityError("Factor history transition mismatch")
        return {"definition": definition, "state": events[-1]["state"],
                "events": events, "path": str(path)}

    def transition(self, factor_id: str, version: int, state: str, *,
                   evidence: dict[str, Path] | None = None,
                   reasons: list[str] | None = None) -> dict:
        path = self._path(factor_id, version)
        with self._lock(factor_id, version):
            current = self.load(factor_id, version)
            if state not in TRANSITIONS[current["state"]]:
                raise ConfigurationError(f"Illegal factor transition: {current['state']} -> {state}")
            reasons = list(reasons or [])
            if state in ("rejected", "retired") and not reasons:
                raise QualityError("Rejected or retired versions require an explicit reason")
            evidence = dict(evidence or {})
            evidence_bytes = {kind: Path(source).read_bytes() for kind, source in evidence.items()}
            required = REQUIRED_EVIDENCE.get(state)
            if required and required not in evidence:
                raise QualityError("Missing decision evidence: " + required)
            if required:
                payload = json.loads(evidence_bytes[required].decode("utf-8"))
                version_id = current["definition"]["version_id"]
                if payload.get("factor_version_id") != version_id:
                    raise QualityError("Decision evidence belongs to another factor version")
                if state == "validated" and payload.get("status") != "passed":
                    raise QualityError("Factor validation has not passed")
                if state in ("evaluated", "candidate"):
                    if payload.get("status") not in ("accepted", "rejected", "inconclusive", "failed"):
                        raise QualityError("Invalid evaluation status")
                    if state == "candidate" and payload["status"] != "accepted":
                        raise QualityError("Only accepted evaluations may enter the candidate library")
                    if state == "candidate":
                        import hashlib
                        evaluated_hash = current["events"][-1]["evidence"]["evaluation"]["sha256"]
                        if hashlib.sha256(evidence_bytes[required]).hexdigest() != evaluated_hash:
                            raise QualityError("Candidate decision must reuse the committed evaluation")
                if state == "frozen" and version_id not in payload.get("factor_version_ids", []):
                    raise QualityError("Frozen feature set does not include this version")
            stored = {}
            for kind, source in evidence.items():
                import re
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,60}", kind):
                    raise ConfigurationError("Invalid evidence kind")
                raw = evidence_bytes[kind]
                import hashlib
                digest = hashlib.sha256(raw).hexdigest()
                destination = path / "evidence" / (digest + ".json")
                destination.parent.mkdir(exist_ok=True)
                if not destination.exists():
                    temporary = destination.with_name(f".{digest}.{os.getpid()}.tmp")
                    try:
                        with temporary.open("wb") as stream:
                            stream.write(raw)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temporary, destination)
                    finally:
                        temporary.unlink(missing_ok=True)
                if file_hash(destination) != digest:
                    raise IntegrityError("Evidence copy integrity failed")
                stored[kind] = {"path": destination.relative_to(path).as_posix(), "sha256": digest}
            previous = current["events"][-1]["event_id"]
            event = self._event(state, len(current["events"]), previous, stored, reasons)
            event_id = content_hash(event)
            atomic_json(path / "events" / (event_id + ".json"), event)
            atomic_json(path / "head.json", {
                "event_id": event_id, "definition_hash": file_hash(path / "definition.json")})
            return self.load(factor_id, version)
