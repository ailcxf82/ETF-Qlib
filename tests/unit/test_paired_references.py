import json

import pytest

from etf_ml.errors import ConfigurationError, IntegrityError
from etf_ml.research.paired import _verify_references
from etf_ml.utils import file_hash


def _model_reference(path, model_id):
    path.mkdir(parents=True)
    bundle = path / "bundle.pkl"
    bundle.write_bytes(b"verified model bundle")
    manifest = path / "manifest.json"
    manifest.write_text(json.dumps({
        "model_id": model_id,
        "bundle_sha256": file_hash(bundle),
    }), encoding="utf-8")
    return {
        "model_path": str(path),
        "model_id": model_id,
        "model_manifest_hash": file_hash(manifest),
    }


def test_cached_model_from_another_execution_is_verified_within_execution_root(tmp_path):
    output_root = tmp_path / "research" / "paired"
    model_root = tmp_path / "research" / "executions" / "current" / "artifacts" / "models"
    cached_model = tmp_path / "research" / "executions" / "baseline" / "artifacts" / "models" / "model-1"
    reports = {"baseline": {"child_runs": [], "by_fold": [_model_reference(cached_model, "model-1")]}}

    _verify_references(reports, output_root, model_root)


def test_model_reference_outside_current_or_execution_root_is_rejected(tmp_path):
    output_root = tmp_path / "research" / "paired"
    model_root = tmp_path / "research" / "executions" / "current" / "artifacts" / "models"
    external_model = tmp_path / "outside" / "artifacts" / "models" / "model-2"
    reports = {"candidate": {"child_runs": [], "by_fold": [_model_reference(external_model, "model-2")]}}

    with pytest.raises(ConfigurationError, match="allowed artifact root"):
        _verify_references(reports, output_root, model_root)


def test_model_reference_inside_execution_root_but_outside_model_layout_is_rejected(tmp_path):
    output_root = tmp_path / "research" / "paired"
    model_root = tmp_path / "research" / "executions" / "current" / "artifacts" / "models"
    malformed_model = tmp_path / "research" / "executions" / "other" / "untrusted" / "models" / "model-3"
    reports = {"candidate": {"child_runs": [], "by_fold": [_model_reference(malformed_model, "model-3")]}}

    with pytest.raises(IntegrityError, match="execution models directory"):
        _verify_references(reports, output_root, model_root)
