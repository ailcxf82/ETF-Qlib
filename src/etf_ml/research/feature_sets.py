from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import pandas as pd
from pandas.testing import assert_frame_equal

from etf_ml.contracts import FeatureArtifact
from etf_ml.data.source import require_panel
from etf_ml.errors import ConfigurationError, IntegrityError, QualityError
from etf_ml.registry import FactorRegistry
from etf_ml.research.context import FactorSpec
from etf_ml.research.protocol import ComparisonProtocol
from etf_ml.utils import FileLock, atomic_json, content_hash, ensure_within, file_hash, verify_files


class FeatureSetStore:
    """Immutable research compositions, backed by committed accepted evaluations.

    A composition is not a final deployment freeze. Its factors retain their
    candidate states until independent model/group/final acceptance is complete.
    """

    def __init__(self, root: Path, registry: FactorRegistry):
        self.root, self.registry = Path(root).resolve(), registry

    def _path(self, identity):
        if not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise ConfigurationError("Invalid accumulated feature identity")
        return ensure_within(self.root / identity[:32], self.root)

    def _check_entry(self, entry):
        spec = FactorSpec.model_validate(entry["spec"])
        current = self.registry.load(spec.factor_id, spec.version)
        if (current["state"] not in ("candidate", "frozen") or
                current["definition"]["version_id"] != spec.version_id or
                entry["factor_version_id"] != spec.version_id):
            raise QualityError("Accumulated factor is not an active accepted candidate")
        evaluations = [event for event in current["events"] if event["state"] == "candidate"]
        evidence = evaluations[-1]["evidence"]["evaluation"]
        path = ensure_within(Path(current["path"]) / evidence["path"], Path(current["path"]))
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (evidence["sha256"] != entry["evaluation_hash"] or payload.get("status") != "accepted" or
                payload.get("protocol_id") != entry["protocol_id"] or
                payload.get("factor_version_id") != spec.version_id or
                payload.get("baseline_id") != entry["baseline_id"] or
                payload.get("candidate_id") != entry["candidate_id"]):
            raise IntegrityError("Accumulated factor accepted evaluation changed")
        return current

    def publish(self, baseline, factor, spec, protocol):
        from etf_ml.research.paired import combine_features
        if baseline.manifest.get("kind") == "accumulated_research":
            checked = self.load(baseline.feature_set_id)
            try:
                assert_frame_equal(checked.frame, baseline.frame, check_exact=True)
            except AssertionError as exc:
                raise IntegrityError("Accumulated parent differs from committed data") from exc
            if checked.manifest != baseline.manifest:
                raise IntegrityError("Accumulated parent manifest changed")
        combined = combine_features(baseline, factor, protocol)
        if (spec.version_id != factor.feature_set_id or factor.manifest.get("source_hash") != spec.source_hash or
                factor.manifest.get("context_hash") != spec.context_hash or
                factor.manifest.get("research_group") != spec.research_group):
            raise QualityError("Accumulated factor specification differs from validated output")
        current = self.registry.load(spec.factor_id, spec.version)
        candidates = [event for event in current["events"] if event["state"] == "candidate"]
        if not candidates:
            raise QualityError("Only accepted registered candidates may be accumulated")
        entry = {
            "spec": spec.model_dump(mode="json"), "factor_version_id": spec.version_id,
            "column": str(factor.frame.columns[0]), "group": spec.research_group,
            "result_hash": factor.manifest["result_hash"],
            "protocol_id": protocol.protocol_id,
            "evaluation_hash": candidates[-1]["evidence"]["evaluation"]["sha256"],
            "baseline_id": baseline.feature_set_id, "candidate_id": combined.feature_set_id,
        }
        self._check_entry(entry)
        previous = baseline.manifest.get("composition", {})
        entries = list(previous.get("factors", []))
        if any(e["spec"]["factor_id"] == spec.factor_id for e in entries):
            raise QualityError("An accumulated set cannot silently replace a factor version")
        for old in entries:
            self._check_entry(old)
        entries.append(entry)
        self.root.mkdir(parents=True, exist_ok=True)
        stage = ensure_within(self.root / ".staging" / format(time.time_ns(), "x"), self.root)
        stage.mkdir(parents=True, exist_ok=False)
        try:
            combined.frame.to_parquet(stage / "features.parquet")
            composition = {
                "snapshot_id": protocol.snapshot_id, "parent_feature_set_id": baseline.feature_set_id,
                "base_feature_set_id": previous.get("base_feature_set_id", baseline.feature_set_id),
                "base_manifest": previous.get("base_manifest", baseline.manifest),
                "base_columns": previous.get("base_columns", list(baseline.frame.columns)),
                "factors": entries, "columns": list(combined.frame.columns),
                "selection_protocol": protocol.model_dump(mode="json"),
                "result_hash": file_hash(stage / "features.parquet"),
            }
            identity = content_hash(composition)
            destination = self._path(identity)
            manifest = {"kind": "accumulated_research", "feature_set_id": identity,
                        "snapshot_id": protocol.snapshot_id, "composition": composition,
                        "factor_version_ids": [e["factor_version_id"] for e in entries],
                        "columns": composition["columns"], "path": str(destination),
                        "files": {"features.parquet": composition["result_hash"]}}
            atomic_json(stage / "feature_manifest.json", manifest)
            with FileLock(self.root / ".locks" / (identity + ".lock")):
                if destination.exists():
                    return self.load(identity)
                os.replace(stage, destination)
            return self.load(identity)
        finally:
            # Staging contains only files written above; never recurse over computed paths.
            for name in ("features.parquet", "feature_manifest.json"):
                (stage / name).unlink(missing_ok=True)
            if stage.exists():
                stage.rmdir()

    def load(self, identity):
        path = self._path(identity)
        manifest = json.loads((path / "feature_manifest.json").read_text(encoding="utf-8"))
        composition = manifest["composition"]
        if (manifest["feature_set_id"] != identity or content_hash(composition) != identity or
                manifest["snapshot_id"] != composition["snapshot_id"] or
                manifest["files"] != {"features.parquet": composition["result_hash"]} or
                Path(manifest["path"]).resolve() != path):
            raise IntegrityError("Accumulated feature manifest identity mismatch")
        verify_files(path, manifest["files"])
        factors = composition["factors"]
        if manifest["factor_version_ids"] != [e["factor_version_id"] for e in factors]:
            raise IntegrityError("Accumulated feature lineage mismatch")
        for entry in factors:
            self._check_entry(entry)
        columns = composition["base_columns"] + [e["column"] for e in factors]
        if len(set(columns)) != len(columns) or columns != composition["columns"] or manifest["columns"] != columns:
            raise IntegrityError("Accumulated feature columns mismatch")
        frame = pd.read_parquet(path / "features.parquet")
        require_panel(frame, numeric=True)
        if list(frame.columns) != columns:
            raise IntegrityError("Accumulated feature data columns mismatch")
        return FeatureArtifact(identity, frame, manifest)

    def verify_baseline(self, artifact, panel):
        checked = self.load(artifact.feature_set_id)
        try:
            assert_frame_equal(checked.frame, artifact.frame, check_exact=True)
        except AssertionError as exc:
            raise IntegrityError("Accumulated baseline differs from its committed data") from exc
        if checked.manifest != artifact.manifest or not checked.frame.index.equals(panel.index):
            raise IntegrityError("Accumulated baseline manifest or research index mismatch")
        from etf_ml.features.baseline import materialize
        composition = checked.manifest["composition"]
        expected = materialize(composition["base_manifest"]["spec"], panel)
        if expected.feature_set_id != composition["base_feature_set_id"]:
            raise IntegrityError("Accumulated initial feature definition differs from the snapshot")
        try:
            assert_frame_equal(expected.frame, checked.frame.loc[:, composition["base_columns"]], check_exact=True)
        except AssertionError as exc:
            raise IntegrityError("Accumulated initial feature values differ from the snapshot") from exc
        return checked


def remove_candidate_group(baseline, candidate, factor, protocol):
    """Remove the new candidate and all accumulated factors in its declared group."""
    old = baseline.manifest.get("composition", {}).get("factors", [])
    new_column = str(factor.frame.columns[0])
    # The new specification is part of the validated registry, supplied by caller.
    group = factor.manifest.get("research_group", "unclassified")
    columns = [new_column] + [e["column"] for e in old if e["group"] == group]
    if columns == [new_column]:
        return baseline, group, columns
    frame = candidate.frame.drop(columns=columns)
    composition = baseline.manifest["composition"]
    if list(frame.columns) == composition["base_columns"]:
        manifest = composition["base_manifest"]
        return FeatureArtifact(composition["base_feature_set_id"], frame, manifest), group, columns
    manifest = {"snapshot_id": protocol.snapshot_id, "source_feature_set_id": candidate.feature_set_id,
                "group_removed": group, "removed_columns": columns, "columns": list(frame.columns),
                "protocol_id": protocol.protocol_id, "source_manifest": candidate.manifest}
    return FeatureArtifact(content_hash(manifest), frame, manifest), group, columns


def group_ablation_evaluation(full, removed, protocol):
    import statistics
    from etf_ml.research.selection import _validate_report, SAMPLE_KEYS
    try:
        right, left = _validate_report(full, protocol), _validate_report(removed, protocol)
        deltas = []
        for key in sorted(right):
            a, b = right[key], left[key]
            if a["daily_index_hash"] != b["daily_index_hash"] or any(
                    a["dataset"][k] != b["dataset"][k] for k in SAMPLE_KEYS):
                raise ValueError("group_ablation_sample_mismatch")
            deltas.append({"fold": key[0], "seed": key[1],
                           "excess_return": a["portfolio"]["excess_return"] - b["portfolio"]["excess_return"]})
        folds = [statistics.median(d["excess_return"] for d in deltas if d["fold"] == f.name)
                 for f in protocol.validation.folds]
        passed = (statistics.median(folds) > 0 and sum(d > 0 for d in folds) > len(folds) / 2 and
                  all(statistics.median(d["excess_return"] for d in deltas if d["seed"] == seed) > 0
                      for seed in protocol.research.seeds))
        return {"status": "accepted" if passed else "rejected", "paired_deltas": deltas,
                "reasons": ["group_ablation_confirmed" if passed else "group_ablation_not_confirmed"]}
    except (ValueError, KeyError, TypeError, statistics.StatisticsError) as exc:
        return {"status": "failed", "paired_deltas": [], "reasons": [str(exc)]}
