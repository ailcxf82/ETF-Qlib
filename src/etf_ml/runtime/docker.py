from __future__ import annotations

import re
import json
import subprocess
from dataclasses import replace
import uuid
from pathlib import Path

from etf_ml.contracts import RuntimeLimits
from etf_ml.data.snapshot import load_snapshot
from etf_ml.errors import ConfigurationError, DataNotReady
from etf_ml.runtime.native import NativeBackend
from etf_ml.utils import ensure_within

PINNED_IMAGE = re.compile(r"^[A-Za-z0-9_./:-]+@sha256:[a-f0-9]{64}$")


class DockerBackend:
    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()
        self.native = NativeBackend(self.workspace_root)

    def preflight(self, image: str) -> dict:
        if not image or not PINNED_IMAGE.fullmatch(image):
            raise ConfigurationError("Agent execution needs an image pinned by sha256 digest")
        try:
            info = subprocess.run(["docker", "info", "--format", "{{.OSType}}"],
                                  check=True, capture_output=True, text=True, timeout=20)
            if info.stdout.strip() != "linux":
                raise DataNotReady("Agent execution requires Linux containers")
            inspect = subprocess.run(["docker", "image", "inspect", image],
                                     check=True, capture_output=True, text=True, timeout=20)
            return {"status": "passed", "image": image}
        except (OSError, subprocess.SubprocessError) as exc:
            raise DataNotReady("Docker daemon or pinned image unavailable") from exc

    def command(self, argv, workspace: Path, research_view: Path,
                limits: RuntimeLimits, *, container_name: str) -> list[str]:
        if not limits.image or not PINNED_IMAGE.fullmatch(limits.image):
            raise ConfigurationError("Agent execution needs a pinned image")
        workspace = ensure_within(workspace, self.workspace_root)
        research_view = Path(research_view).resolve()
        snapshot = load_snapshot(research_view.parent)
        if research_view != snapshot.path / "research":
            raise ConfigurationError("Only the verified research view may be mounted")
        if (workspace / ".env").exists() or workspace == research_view or research_view.is_relative_to(workspace):
            raise ConfigurationError("Unsafe Agent workspace")
        if not isinstance(argv, list) or not argv or not all(isinstance(v, str) for v in argv):
            raise ConfigurationError("Agent execution requires argv")
        return ["docker", "run", "--pull", "never", "--name", container_name,
                "--network", "none", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges:true", "--user", "1000:1000",
                "--pids-limit", "64", "--memory", f"{limits.memory_mb}m",
                "--memory-swap", f"{limits.memory_mb}m", "--cpus", str(limits.cpu_count),
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
                "--mount", f"type=bind,source={research_view},target=/research,readonly",
                "--mount", f"type=bind,source={workspace},target=/work",
                "--workdir", "/work", "--env", "PYTHONDONTWRITEBYTECODE=1",
                "--env", "OMP_NUM_THREADS=1", limits.image, *argv]

    def run(self, argv, workspace, research_view, limits):
        self.preflight(limits.image)
        name = "etf-factor-" + uuid.uuid4().hex
        command = self.command(argv, workspace, research_view, limits, container_name=name)
        # Docker enforces workload memory; host monitoring covers only the CLI wrapper.
        wrapper_limits = RuntimeLimits(timeout_seconds=limits.timeout_seconds,
                                       memory_mb=max(256, limits.memory_mb),
                                       cpu_count=limits.cpu_count,
                                       max_output_bytes=limits.max_output_bytes)
        try:
            result = self.native.run(command, workspace, {}, wrapper_limits, trusted=True)
            try:
                state = subprocess.run(["docker", "inspect", name, "--format", "{{json .State}}"],
                                       check=True, capture_output=True, text=True, timeout=10)
                if json.loads(state.stdout).get("OOMKilled"):
                    result = replace(result, status="failed", reason="memory_limit")
            except (OSError, subprocess.SubprocessError, ValueError):
                pass
            return result
        finally:
            try:
                subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=10,
                               creationflags=subprocess.CREATE_NO_WINDOW if __import__("os").name == "nt" else 0)
            except (OSError, subprocess.SubprocessError):
                pass
