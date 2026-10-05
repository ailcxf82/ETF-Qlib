from __future__ import annotations

import json
import os
import pickle
import tempfile
from pathlib import Path

from etf_ml.artifacts import environment_manifest
from etf_ml.errors import IntegrityError
from etf_ml.utils import FileLock, atomic_json, ensure_within, file_hash, code_hash


def save_bundle(bundle, registry_root: Path) -> Path:
    root = Path(registry_root).resolve()
    final = ensure_within(root / bundle.manifest["model_id"], root)
    root.mkdir(parents=True, exist_ok=True)
    with FileLock(root / ".locks" / (bundle.manifest["model_id"] + ".lock")):
        if final.exists():
            existing = load_bundle(final, trusted_root=root)
            if existing.manifest != bundle.manifest:
                raise IntegrityError("Immutable model identity conflict")
            return final
        temp = ensure_within(Path(tempfile.mkdtemp(prefix=".tmp-", dir=root)), root)
        with (temp / "bundle.pkl").open("wb") as stream:
            pickle.dump(bundle, stream, protocol=5)
        atomic_json(temp / "manifest.json", {
            **bundle.manifest, "bundle_sha256": file_hash(temp / "bundle.pkl")})
        os.replace(temp, final)
    return final


def load_bundle(path: Path, *, trusted_root: Path):
    # Pickle is executable: only load from the explicitly trusted model registry.
    path = ensure_within(path, trusted_root)
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or path.name != manifest.get("model_id"):
        raise IntegrityError("Model schema or identity mismatch")
    if file_hash(path / "bundle.pkl") != manifest["bundle_sha256"]:
        raise IntegrityError("Corrupt model bundle")
    if manifest.get("code_hash") != code_hash():
        raise IntegrityError("Model code version differs")
    current = environment_manifest()
    required = manifest["environment"]
    if current["python"] != required["python"] or current["packages"] != required["packages"]:
        raise IntegrityError("Model dependency versions differ")
    # Qlib's optional-model imports print diagnostics during first unpickling.
    # Keep those diagnostics on stderr so CLI stdout remains one JSON document.
    from contextlib import redirect_stdout
    import sys
    with (path / "bundle.pkl").open("rb") as stream, redirect_stdout(sys.stderr):
        bundle = pickle.load(stream)
    if bundle.manifest != {k: v for k, v in manifest.items() if k != "bundle_sha256"}:
        raise IntegrityError("Bundle and manifest disagree")
    return bundle
