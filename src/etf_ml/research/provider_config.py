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
        configured = dotenv_values(path, interpolate=True)
    environment = {key: os.environ.get(key, configured.get(key))
                   for key in PROVIDER_KEYS if os.environ.get(key, configured.get(key))}
    # Zhipu/Z.ai's OpenAI-compatible endpoint still expects the credential
    # under OPENAI_API_KEY in RDAgent/LiteLLM.  Prefer an explicitly rotated
    # process credential over a possibly stale value retained in .env, without
    # exposing the provider-specific name to generated workspaces.
    zhipu_model = os.environ.get("ZHIPU_MODEL") or configured.get("ZHIPU_MODEL")
    if "CHAT_MODEL" not in environment and zhipu_model:
        environment["CHAT_MODEL"] = zhipu_model if "/" in zhipu_model else "zai/" + zhipu_model
    zhipu_key = os.environ.get("ZHIPUAI_API_KEY")
    if not os.environ.get("OPENAI_API_KEY") and zhipu_key:
        environment["OPENAI_API_KEY"] = zhipu_key
    return environment
