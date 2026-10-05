"""One portable numerical baseline per calculation identity, shared by runs."""
from __future__ import annotations

from pathlib import Path

from etf_ml.artifacts import environment_manifest
from etf_ml.errors import IntegrityError
from etf_ml.research.reuse_identity import evaluation_code_hash
from etf_ml.research.reuse_store import ReuseStore, read_object
from etf_ml.utils import FileLock, atomic_json, content_hash, ensure_within, file_hash


def baseline_key(config, snapshot_id, *, feature_set_id=None, cost_multipliers=(2.0,)):
    settings = config.model_dump(mode="json")
    return content_hash({"schema": "shared-baseline-v1", "snapshot_id": snapshot_id,
        "feature_set_id": feature_set_id, "cost_multipliers": list(cost_multipliers),
        "settings": {key: settings[key] for key in (
            "universe", "label", "validation", "portfolio", "research", "benchmarks", "models")},
        "engine": evaluation_code_hash(), "environment": environment_manifest()})


def _map_values(value, transform):
    if isinstance(value, dict):
        return {key: _map_values(item, transform) for key, item in value.items()}
    if isinstance(value, list):
        return [_map_values(item, transform) for item in value]
    return transform(value) if isinstance(value, str) else value


class SharedBaseline:
    def __init__(self, root):
        self.store = ReuseStore(root)

    def load(self, package_id):
        payload, manifest = self.store.load(package_id, "baselines")
        root = self.store.package_path(package_id, "baselines")
        def resolve(value):
            return str(ensure_within(root / value[8:], root)) if value.startswith("reuse://") else value
        report = _map_values(payload["report"], resolve)
        return report, {"package_id": package_id, "path": str(root / "files"),
                        "manifest_hash": file_hash(root / "manifest.json"),
                        "cache_key": payload["cache_key"]}

    def get(self, key):
        ref = self.store.root / "baseline_index" / (key + ".json")
        if not ref.is_file():
            return None
        value = read_object(ref)
        package_id = value["package_id"]
        payload, _ = self.store.load(package_id, "baselines")
        if payload.get("cache_key") != key:
            raise IntegrityError("Shared baseline identity differs")
        return self.load(package_id)

    def rebuild_index(self):
        entries = {}
        with FileLock(self.store.root / "store.lock"):
            for path in sorted((self.store.root / "baselines").glob("*/manifest.json")):
                payload, _ = self.store.load(path.parent.name, "baselines")
                key = payload["cache_key"]
                if key in entries and entries[key] != path.parent.name:
                    raise IntegrityError("Conflicting shared baselines require review")
                entries[key] = path.parent.name
            for key, package_id in entries.items():
                target = ensure_within(self.store.root / "baseline_index" / (key + ".json"),
                                       self.store.root / "baseline_index")
                atomic_json(target, {"package_id": package_id})
        return len(entries)

    def publish(self, key, report, output):
        output = Path(output).resolve()
        files = {}
        for path in output.rglob("*"):
            if path.is_file() and path.name not in {
                    "baseline_features.parquet", "frozen_feature_input.parquet", "manifest.json",
                    "status.json", "baseline_report.json", "progress.json", "progress.jsonl"}:
                path = ensure_within(path, output)
                files["files/" + path.relative_to(output).as_posix()] = path
        mappings = [(output, "files")]
        for row in report["by_fold"]:
            model = Path(row["model_path"]).resolve()
            manifest = read_object(model / "manifest.json")
            if (file_hash(model / "manifest.json") != row["model_manifest_hash"] or
                    file_hash(model / "bundle.pkl") != manifest["bundle_sha256"]):
                raise IntegrityError("Shared baseline model changed before publication")
            prefix = "models/" + content_hash(str(model))[:24]
            mappings.append((model, prefix))
            for path in model.rglob("*"):
                if path.is_file():
                    path = ensure_within(path, model)
                    files[prefix + "/" + path.relative_to(model).as_posix()] = path
        mappings.sort(key=lambda pair: len(str(pair[0])), reverse=True)
        def portable(value):
            # Only rewrite absolute references to files owned by this package.
            candidate = Path(value)
            if candidate.is_absolute():
                for old_root, new_root in mappings:
                    if candidate.is_relative_to(old_root):
                        return "reuse://" + new_root + "/" + candidate.relative_to(old_root).as_posix()
            return value
        payload = {"schema_version": "shared-baseline-v1", "cache_key": key,
                   "report": _map_values(report, portable),
                   "capabilities": {"paired_comparison": True, "raw_training_replay": False}}
        package_id = self.store.publish(payload, kind="baselines", extra_files=files)
        atomic_json(self.store.root / "baseline_index" / (key + ".json"), {"package_id": package_id})
        return self.load(package_id)

    def get_or_run(self, key, output, run):
        with FileLock(self.store.root / "baseline_locks" / (key + ".lock"), timeout=86400):
            cached = self.get(key)
            if cached:
                return *cached, True
            report = run()
            report, reference = self.publish(key, report, output)
            return report, reference, False
