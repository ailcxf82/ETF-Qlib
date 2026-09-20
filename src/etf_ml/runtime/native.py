from __future__ import annotations

import os
import subprocess
import time
import uuid
from pathlib import Path

import psutil

from etf_ml.contracts import ExecutionResult, RuntimeLimits
from etf_ml.errors import ConfigurationError
from etf_ml.utils import ensure_within, redact


def kill_tree(process):
    try:
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        parent.kill()
        psutil.wait_procs(children + [parent], timeout=3)
    except psutil.NoSuchProcess:
        pass


class NativeBackend:
    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()

    def run(self, argv: list[str], workspace: Path, env: dict[str, str],
            limits: RuntimeLimits, *, trusted=False) -> ExecutionResult:
        if not trusted:
            raise ConfigurationError("Generated code requires the Docker backend")
        if not isinstance(argv, list) or not argv or not all(isinstance(v, str) for v in argv):
            raise ConfigurationError("Execution requires an explicit argv list")
        workspace = ensure_within(workspace, self.workspace_root)
        workspace.mkdir(parents=True, exist_ok=True)
        job = workspace / ("execution-" + uuid.uuid4().hex)
        job.mkdir()
        stdout_path, stderr_path = job / "stdout.txt", job / "stderr.txt"
        started = time.monotonic()
        environment = dict(os.environ)
        environment.update(env)
        reason = None
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            try:
                process = subprocess.Popen(argv, cwd=workspace, env=environment,
                                           stdout=stdout, stderr=stderr, shell=False,
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            except OSError:
                return ExecutionResult("failed", None, "", "", time.monotonic() - started,
                                       "launch_failed")
            try:
                try:
                    tracked_cpu = psutil.Process(process.pid)
                    allowed_cpu = tracked_cpu.cpu_affinity()
                    tracked_cpu.cpu_affinity(allowed_cpu[:limits.cpu_count])
                except psutil.NoSuchProcess:
                    pass
                except (psutil.AccessDenied, AttributeError, OSError):
                    kill_tree(process)
                    reason = "cpu_limit_unavailable"
                while process.poll() is None:
                    if time.monotonic() - started > limits.timeout_seconds:
                        reason = "timeout"
                    elif stdout_path.stat().st_size + stderr_path.stat().st_size > limits.max_output_bytes:
                        reason = "output_limit"
                    else:
                        try:
                            tracked = psutil.Process(process.pid)
                            rss = tracked.memory_info().rss + sum(
                                p.memory_info().rss for p in tracked.children(recursive=True)
                                if p.is_running())
                            if rss > limits.memory_mb * 1024 * 1024:
                                reason = "memory_limit"
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                    if reason:
                        kill_tree(process)
                        break
                    time.sleep(0.02)
                process.wait(timeout=5)
            except BaseException:
                kill_tree(process)
                raise
        if stdout_path.stat().st_size + stderr_path.stat().st_size > limits.max_output_bytes:
            reason = reason or "output_limit"
        stdout_text = stdout_path.read_bytes()[:limits.max_output_bytes].decode("utf-8", errors="replace")
        stderr_text = stderr_path.read_bytes()[:max(0, limits.max_output_bytes - len(stdout_text.encode("utf-8")))].decode("utf-8", errors="replace")
        # Child-only provider credentials may not exist in the parent environment.
        for key, secret in environment.items():
            if secret and any(marker in key.lower() for marker in ("key", "token", "secret", "password", "authorization", "credential")):
                stdout_text = stdout_text.replace(secret, "[REDACTED]")
                stderr_text = stderr_text.replace(secret, "[REDACTED]")
        stdout_text, stderr_text = redact(stdout_text), redact(stderr_text)
        # Persist only the redacted and bounded logs.
        stdout_path.write_text(stdout_text, encoding="utf-8")
        stderr_path.write_text(stderr_text, encoding="utf-8")
        return ExecutionResult("succeeded" if process.returncode == 0 and reason is None else "failed",
                               process.returncode, stdout_text, stderr_text,
                               time.monotonic() - started, reason or (
                                   "nonzero_exit" if process.returncode else None))
