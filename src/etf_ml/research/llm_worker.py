"""Trusted external-call worker. Never loaded in generated-factor containers."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from etf_ml.utils import atomic_json


def main():
    request_path = Path(sys.argv[1]).resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    from rdagent.oai.llm_conf import LLM_SETTINGS
    # This setting belongs to this child process; no host singleton is mutated.
    LLM_SETTINGS.max_retry = 3
    from rdagent.oai.llm_utils import APIBackend
    text = APIBackend().build_messages_and_create_chat_completion(
        user_prompt=request["user_prompt"], system_prompt=request["system_prompt"],
        json_mode=True, json_target_type=dict)
    atomic_json(request_path.parent / "response.json", {
        "text": text, "actual_cost": None, "usage": {},
        "provider": "rdagent-0.8.0-api-backend-text-only-cost-unknown"})


if __name__ == "__main__":
    main()

