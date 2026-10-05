from types import SimpleNamespace

from etf_ml.models import persistence


def test_save_bundle_uses_short_temporary_directory_name(tmp_path, monkeypatch):
    moved_from = []
    replace = persistence.os.replace

    def capture_replace(source, destination):
        if source.name.startswith(".tmp-"):
            moved_from.append(source)
        replace(source, destination)

    monkeypatch.setattr(persistence.os, "replace", capture_replace)
    bundle = SimpleNamespace(manifest={"model_id": "a" * 64})

    saved = persistence.save_bundle(bundle, tmp_path / "models")

    assert saved.is_dir()
    assert len(moved_from) == 1
    assert len(moved_from[0].name) <= 16
    assert not moved_from[0].exists()
