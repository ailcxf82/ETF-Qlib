"""Calculation identities independent of the compact store's physical location."""
from pathlib import Path

from etf_ml.utils import content_hash, file_hash


def evaluation_code_hash():
    # Keep numerical, policy, proposal and orchestration code conservative. Only
    # the CLI and storage/index implementations are deliberately excluded.
    root = Path(__file__).parents[1]
    excluded = {"cli.py", "research/memory_index.py", "research/reuse_store.py",
                "research/reuse_baseline.py"}
    return content_hash({p.relative_to(root).as_posix(): file_hash(p)
                         for p in sorted(root.rglob("*.py"))
                         if p.relative_to(root).as_posix() not in excluded})


def reuse_context(protocol):
    value = protocol.model_dump(mode="json") if hasattr(protocol, "model_dump") else dict(protocol)
    # Legacy protocols do not prove a scoped engine identity. Never upgrade them.
    if not value.get("evaluation_code_hash"):
        return None
    value.pop("source_code_hash", None)
    value.pop("protocol_id", None)
    return content_hash(value)


def reuse_root(config):
    return Path(config.reuse_root or config.artifact_root / "reuse").resolve()


def local_reuse_root(config):
    """Active reuse storage always stays with the project's artifacts."""
    return Path(config.artifact_root / "reuse").resolve()
