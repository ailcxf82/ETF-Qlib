"""Portable immutable evaluation receipts. Export verifies raw evidence once.

Receipts support historical conclusions and admission, not raw replay or model
promotion. The index is derived and can be rebuilt without original run folders.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

from etf_ml.errors import ConfigurationError, IntegrityError
from etf_ml.utils import FileLock, atomic_json, content_hash, ensure_within, file_hash, source_hashes, verify_files


def read_object(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise IntegrityError("Expected an object: " + str(path))
    return value


class ReuseStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.index_path = self.root / "index.json"

    def package_path(self, package_id, kind="evaluations"):
        if not isinstance(package_id, str) or not re.fullmatch(r"[0-9a-f]{64}", package_id):
            raise IntegrityError("Invalid reuse package id")
        return ensure_within(self.root / kind / package_id, self.root)

    def load(self, package_id, kind="evaluations"):
        root = self.package_path(package_id, kind)
        manifest = read_object(root / "manifest.json")
        if (manifest.get("schema_version") != "reuse-package-v1" or
                content_hash({k: v for k, v in manifest.items() if k != "package_id"}) != package_id):
            raise IntegrityError("Reuse package manifest identity changed")
        verify_files(root, manifest["files"])
        payload = read_object(root / "result.json")
        return payload, manifest

    def publish(self, payload, *, kind="evaluations", extra_files=None):
        stage = self.root / ".staging" / uuid.uuid4().hex
        stage.mkdir(parents=True)
        atomic_json(stage / "result.json", payload)
        if extra_files:
            import shutil
            for relative, source in extra_files.items():
                target = ensure_within(stage / relative, stage)
                target.parent.mkdir(parents=True, exist_ok=True)
                expected_hash = file_hash(source)
                shutil.copyfile(source, target)
                if file_hash(target) != expected_hash or file_hash(source) != expected_hash:
                    raise IntegrityError("Source changed while publishing reuse package")
        manifest = {"schema_version": "reuse-package-v1", "kind": kind, "files": source_hashes(stage)}
        package_id = content_hash(manifest)
        atomic_json(stage / "manifest.json", {**manifest, "package_id": package_id})
        destination = self.package_path(package_id, kind)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(self.root / "store.lock"):
            if destination.exists():
                self.load(package_id, kind)
                # Only our checked, unpublished staging directory is disposable.
                import shutil
                shutil.rmtree(ensure_within(stage, self.root / ".staging"))
            else:
                os.replace(stage, destination)
            if kind == "evaluations":
                index = self._read_index()
                index["packages"][package_id] = self._entry(payload)
                self._write_index(index)
        return package_id

    @staticmethod
    def _entry(payload):
        card = payload["card"]
        return {"source_key": payload["source_key"], "trial_sha256": payload["trial_sha256"],
                "trial_key": payload["trial_key"], "card": card}

    def _read_index(self):
        if not self.index_path.exists():
            return {"schema_version": "reuse-index-v1", "packages": {}}
        value = read_object(self.index_path)
        if value.get("identity") != content_hash({k: v for k, v in value.items() if k != "identity"}):
            raise IntegrityError("Reuse index changed; rebuild it from packages")
        return value

    def _write_index(self, value):
        value = {k: v for k, v in value.items() if k != "identity"}
        atomic_json(self.index_path, {**value, "identity": content_hash(value)})

    def index(self):
        if not self.index_path.exists() and (self.root / "evaluations").exists():
            return self.rebuild()
        return self._read_index()

    def rebuild(self):
        with FileLock(self.root / "store.lock"):
            entries = {}
            for path in sorted((self.root / "evaluations").glob("*/manifest.json")):
                payload, _ = self.load(path.parent.name)
                entries[path.parent.name] = self._entry(payload)
            result = {"schema_version": "reuse-index-v1", "packages": entries}
            self._write_index(result)
            return self._read_index()

    def cards(self, *, definition_id=None):
        rows = []
        for package_id, item in self.index()["packages"].items():
            if definition_id is not None and item["card"].get("definition_id") != definition_id:
                continue
            payload, _ = self.load(package_id)
            if self._entry(payload) != item:
                raise IntegrityError("Reuse index differs from committed package")
            rows.append({**payload["card"], "reuse_package_id": package_id})
        return rows

    def export_trial(self, trial, checkpoint, *, source, allowed_root):
        from etf_ml.research.memory_index import ResearchMemoryIndex, _safe_card, _memory_feedback_summary
        from etf_ml.research.reuse_identity import reuse_context
        source = ensure_within(Path(source), allowed_root)
        trial, checkpoint = ensure_within(Path(trial), source), ensure_within(Path(checkpoint), source)
        state = read_object(checkpoint)
        trial_hash = file_hash(trial)
        relative = trial.relative_to(checkpoint.parent).as_posix()
        if not any(row.get("path") == relative and row.get("sha256") == trial_hash for row in state.get("trials", [])):
            raise IntegrityError("Only checkpoint-committed trials can be exported")
        source_key = content_hash(str(source))
        trial_key = trial.relative_to(source).as_posix()
        for package_id, entry in self.index()["packages"].items():
            if entry["source_key"] == source_key and entry["trial_key"] == trial_key:
                if entry["trial_sha256"] != trial_hash:
                    raise IntegrityError("Previously exported trial changed")
                self.load(package_id)
                return package_id
        record = read_object(trial)
        card = record.get("research_card")
        if not isinstance(card, dict):
            raise ConfigurationError("Trial has no research card")
        verified, reason = ResearchMemoryIndex._evaluation_evidence_status(card, Path(allowed_root), reuse_root=self.root)
        # A reused trial inherits only from a verified independent package.
        prior_id = (record.get("result") or {}).get("reuse_package_id")
        if prior_id:
            previous, _ = self.load(prior_id)
            prior = previous["card"]
            if record["result"].get("reused_evaluation_id") != prior.get("evaluation_id"):
                raise IntegrityError("Reuse receipt points to a different evaluation")
            verified, reason = bool(prior.get("evaluation_evidence_verified")), "verified_compact_receipt"
        summary, rebuilt = _memory_feedback_summary(record, card)
        safe = _safe_card({**card, "feedback_summary": summary, "feedback_summary_rebuilt": rebuilt})
        safe.update(evaluation_evidence_verified=verified, evaluation_evidence_reason=reason,
                    committed_order=[state.get("created_at_ns", 0), record.get("trial_index", 0)])
        if prior_id:
            for key in ("evaluation_id", "reuse_context_id", "evaluation_attempted_trials"):
                safe[key] = prior.get(key)
        evidence_files = {}
        evidence_hashes = []
        protocol = {}
        if verified and not prior_id:
            for number, item in enumerate(card["evaluation_evidence"]):
                path = ensure_within(Path(item["path"]), allowed_root)
                if file_hash(path) != item["sha256"]:
                    raise IntegrityError("Evidence changed during compact export")
                if "evidence/" + path.name in evidence_files:
                    raise IntegrityError("Ambiguous compact evidence names")
                evidence_files["evidence/" + path.name] = path
                evidence_hashes.append({"name": path.name, "sha256": item["sha256"]})
                if path.name == "paired_report.json":
                    paired = read_object(path)
                    safe["evaluation_attempted_trials"] = paired.get("attempted_trials")
                    if safe["evaluation_attempted_trials"] is None:
                        counts = {row.get("attempted_trials") for row in
                                  paired.get("time_block_statistics", [])}
                        if len(counts) == 1:
                            count = counts.pop()
                            if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                                safe["evaluation_attempted_trials"] = count
                    protocol_file = path.parent / "protocol.json"
                    if protocol_file.is_file():
                        protocol = read_object(protocol_file)
                        # Bind the portable compatibility projection to the actual
                        # frozen protocol, not a card supplied by a caller.
                        frozen = {k: v for k, v in protocol.items() if k != "protocol_id"}
                        if content_hash(frozen) != card.get("evaluation_protocol_id"):
                            raise IntegrityError("Frozen evaluation protocol differs")
                        safe["reuse_context_id"] = reuse_context(frozen)
                        evidence_files["evidence/protocol.json"] = protocol_file
        if prior_id:
            _, prior_manifest = self.load(prior_id)
            for name in prior_manifest["files"]:
                if name.startswith("evidence/"):
                    evidence_files[name] = self.package_path(prior_id) / name
            evidence_hashes = prior.get("evaluation_evidence_hashes", [])
        # Preserve the tiny implementation/definition files for future factor
        # reconstruction; no factor matrix or trained model is needed here.
        factor_id = card.get("factor_id")
        if isinstance(factor_id, str) and re.fullmatch(r"[A-Za-z0-9_-]+", factor_id):
            for file in (source / "registry" / factor_id).glob("v*/*"):
                if file.is_file() and file.name in {"source.py", "spec.json", "definition.json", "head.json"}:
                    evidence_files["implementation/" + file.parent.name + "/" + file.name] = ensure_within(file, source)
        safe["evaluation_evidence_hashes"] = evidence_hashes
        safe["evidence_id"] = content_hash({"trial": trial_key, "source": source_key, "hash": trial_hash})
        payload = {"schema_version": "compact-evaluation-v1", "card": safe,
                   "source_key": source_key, "trial_key": trial_key, "trial_sha256": trial_hash,
                   "source": str(source), "checkpoint_sha256_at_export": file_hash(checkpoint),
                   "record": record, "protocol": protocol,
                   "capabilities": {"historical_feedback": True, "same_evaluation_reuse": verified,
                                    "model_inference": False, "raw_replay": False, "promotion": False}}
        # All exports (including failure/unknown cards) remain independently
        # readable. No numerical conclusion is invented for missing evidence.
        return self.publish(payload, extra_files=evidence_files)

    def decision(self, package_id):
        payload, _ = self.load(package_id)
        card = payload["card"]
        if not card.get("evaluation_evidence_verified") or card.get("economic_status") not in {"accepted", "rejected"}:
            raise IntegrityError("Package cannot supply a reusable economic conclusion")
        root = self.package_path(package_id) / "evidence"
        reports = {name: str(root / (name + "_report.json")) for name in
                   ("baseline", "candidate", "ablation", "group_ablation")
                   if (root / (name + "_report.json")).is_file()}
        if not {"baseline", "candidate"} <= reports.keys():
            raise IntegrityError("Compact package is missing paired reports")
        evaluation = read_object(root / "evaluation.json")
        if evaluation.get("status") != card.get("economic_status"):
            raise IntegrityError("Compact conclusion differs from evaluation")
        return {"factor_id": card["factor_id"], "status": card["economic_status"],
                "evaluation": evaluation, "reports": reports, "reuse_package_id": package_id,
                "evaluation_id": card["evaluation_id"], "promotion_materialized": False}

    def export_source(self, source, *, allowed_root):
        source = ensure_within(Path(source), allowed_root)
        packages = []
        for checkpoint in sorted((source / "sessions").glob("*/checkpoint.json")):
            state = read_object(checkpoint)
            for row in state.get("trials", []):
                trial = ensure_within(checkpoint.parent / row["path"], checkpoint.parent)
                packages.append(self.export_trial(trial, checkpoint, source=source, allowed_root=allowed_root))
        return {"source": str(source), "package_ids": packages, "count": len(packages)}

    def export_run(self, run, *, allowed_root):
        run = ensure_within(Path(run), allowed_root)
        status = read_object(run / "status.json")
        if status.get("status") not in {"completed", "failed", "cancelled", "incomplete"}:
            raise ConfigurationError("Export requires a terminal run")
        result = self.export_source(run / "research", allowed_root=allowed_root)
        if not result["package_ids"]:
            raise ConfigurationError("No committed research trials to export")
        # Preserve small run records without rewriting or claiming a full raw archive.
        files = {"run/" + name: run / name for name in (
            "status.json", "config.json", "manifest.json", "metrics.json", "first_loop_report.json",
            "qualification_report.json", "baseline_reference.json") if (run / name).is_file()}
        receipt = {"schema_version": "compact-run-v1", "run_id": run.name, "source": str(run),
                   "source_key": content_hash(str(run / "research")), "packages": result["package_ids"],
                   "status": status}
        receipt_id = self.publish(receipt, kind="runs", extra_files=files)
        return {**result, "run_receipt_id": receipt_id, "reuse_root": str(self.root)}

    def cleanup_preview(self, run, *, allowed_root):
        """A read-only candidate list, never a recursive deletion command."""
        run = ensure_within(Path(run), allowed_root)
        status = read_object(run / "status.json")
        blockers = []
        if status.get("status") not in {"completed", "failed", "cancelled", "incomplete"}:
            blockers.append("run_not_terminal")
        ids = []
        entries = self.index()["packages"]
        for checkpoint in sorted((run / "research" / "sessions").glob("*/checkpoint.json")):
            state = read_object(checkpoint)
            if state.get("in_progress"):
                blockers.append("checkpoint_has_pending_work")
            for row in state.get("trials", []):
                trial = ensure_within(checkpoint.parent / row["path"], checkpoint.parent)
                matches = [pid for pid, e in entries.items() if e["source_key"] == content_hash(str(run / "research"))
                           and e["trial_key"] == trial.relative_to(run / "research").as_posix()
                           and e["trial_sha256"] == row["sha256"]]
                if file_hash(trial) != row["sha256"] or not matches:
                    blockers.append("trial_not_exported_or_changed")
                    continue
                payload, _ = self.load(matches[0])
                if not payload["card"].get("evaluation_evidence_verified"):
                    blockers.append("raw_evidence_still_needed")
                ids.extend(matches)
        if not ids:
            blockers.append("no_verified_packages")
        candidates = []
        external_references = []
        # Inspect common cross-run pointers. This is deliberately a preview;
        # arbitrary external scripts cannot be certified by a repository scan.
        for sibling in (run.parent).iterdir():
            if sibling == run or not sibling.is_dir():
                continue
            for name in ("baseline_reference.json", "first_loop_report.json", "metrics.json"):
                pointer = sibling / name
                if pointer.is_file():
                    value = json.dumps(read_object(pointer), ensure_ascii=False).replace("\\\\", "/").lower()
                    if run.as_posix().lower() in value:
                        external_references.append(str(pointer))
        if external_references:
            blockers.append("external_run_references")
        # Keep the identity/status/checkpoint/registry/ledger skeleton. Only name
        # bulk areas; external consumers must still be checked before removal.
        for relative in ("research/workspaces", "research/paired", "research/executions", "baseline"):
            path = run / relative
            if path.is_dir():
                candidates.append({"path": str(path), "bytes": sum(p.stat().st_size for p in path.rglob("*") if p.is_file()),
                                   "action": "archive_candidate", "reference_check_required": True})
        return {"status": "blocked" if blockers else "review_required", "run": str(run),
                "blockers": sorted(set(blockers)), "packages": ids, "candidates": candidates,
                "external_references": external_references,
                "reference_scan_scope": "sibling run summaries and baseline pointers; external consumers require review",
                "automatic_delete": False,
                "keep": ["status/config/manifest", "checkpoint/trials", "campaign/billing", "registry/feature_sets"],
                "note": "Existing raw RunStore replay requires restoration; compact reuse is independent."}
