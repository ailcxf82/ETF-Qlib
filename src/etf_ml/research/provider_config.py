"""Explicit trusted provider configuration; generated workspaces supply none."""
from __future__ import annotations

import os
from pathlib import Path

PROVIDER_KEYS = ("BACKEND", "CHAT_MODEL", "EMBEDDING_MODEL", "OPENAI_API_BASE",
                 "OPENAI_API_KEY", "AZURE_API_KEY", "AZURE_API_BASE", "AZURE_API_VERSION")


def provider_environment(env_file: Path | None = None) -> dict[str, str]:
    path = env_file if env_file is not None else Path(__file__).resolve().parents[3] / ".env"
    configured = {}
    if path.is_file():
        from dotenv import dotenv_values
        configured = dotenv_values(path, interpolate=False)
    return {key: os.environ.get(key, configured.get(key))
            for key in PROVIDER_KEYS if os.environ.get(key, configured.get(key))}
