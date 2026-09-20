from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from etf_ml.errors import ConfigurationError, IntegrityError

SENSITIVE_KEYS = ("api_key", "credential", "password", "secret", "token", "authorization")


def json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, (Path, date, datetime)):
        return str(value)
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Not JSON serializable: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(value, default=json_default, ensure_ascii=False,
                      allow_nan=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with filesystem_path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def code_hash() -> str:
    root = Path(__file__).parent
    return content_hash({p.relative_to(root).as_posix(): file_hash(p)
                         for p in sorted(root.rglob("*.py"))})


def source_hashes(root: Path) -> dict[str, str]:
    # Enumeration and file predicates need the extended path too, not only open().
    # Relative manifest identities remain independent of the physical prefix.
    root = filesystem_path(root)
    return {p.relative_to(root).as_posix(): file_hash(p)
            for p in sorted(root.rglob("*")) if p.is_file()}


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): "[REDACTED]" if any(s in str(k).lower() for s in SENSITIVE_KEYS)
                else redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        for key, secret in os.environ.items():
            if any(s in key.lower() for s in SENSITIVE_KEYS) and len(secret) >= 8:
                value = value.replace(secret, "[REDACTED]")
    return value


def filesystem_path(path: Path) -> Path:
    """Use Windows extended paths without shortening artifact identities."""
    path = Path(path)
    if os.name != "nt":
        return path
    absolute = str(path.resolve())
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def atomic_json(path: Path, value: Any) -> None:
    path = filesystem_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique temp names support concurrent writers; publication is one rename.
    temp = path.with_name(f".write-{os.getpid()}-{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical_json(redact(value)) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def ensure_within(path: Path, root: Path) -> Path:
    path, root = Path(path).resolve(), Path(root).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ConfigurationError("Output must be a child of the allowed artifact root")
    return path


def verify_files(root: Path, hashes: dict[str, str]) -> None:
    for relative, expected in hashes.items():
        path = ensure_within(Path(root) / relative, root)
        if not filesystem_path(path).is_file() or file_hash(path) != expected:
            raise IntegrityError(f"Artifact hash mismatch: {relative}")


class FileLock:
    """OS-owned lock: a crashed process automatically releases the lock."""

    def __init__(self, path: Path, timeout: float = 10):
        self.path, self.timeout, self.stream = filesystem_path(path), timeout, None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        self.stream.seek(0, os.SEEK_END)
        if not self.stream.tell():
            self.stream.write(b"0")
            self.stream.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    self.stream.close()
                    raise ConfigurationError("Another process owns this artifact")
                time.sleep(0.02)

    def __exit__(self, *_):
        if self.stream is not None:
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_UN)
            self.stream.close()
