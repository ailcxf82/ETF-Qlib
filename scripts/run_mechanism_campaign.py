"""Run a finite, one-proposal-per-process RDAgent mechanism campaign.

Each candidate is a separate first-loop run against the same frozen snapshot
and matched baseline. A rejected or inconclusive completed evaluation consumes
its slot and allows the next slot; infrastructure/incomplete outcomes stop.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def _terminal_decision(report: dict) -> tuple[str, str | None]:
    if report.get("status") != "completed":
        return "stop", None
    decisions = report.get("candidate_decisions")
    if not isinstance(decisions, list) or len(decisions) != 1:
        return "stop", None
    status = decisions[0].get("status")
    if status == "accepted":
        return "accepted", status
    if status in {"rejected", "inconclusive"}:
        return "continue", status
    return "stop", status if isinstance(status, str) else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--campaign-id", required=True)
    args = parser.parse_args(argv)

    repo = Path(__file__).resolve().parents[1]
    artifact_root = repo / "artifacts"
    log_root = artifact_root / "research_campaign_logs" / args.campaign_id
    log_root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    src = str(repo / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    for ordinal in range(1, 6):
        run_id = f"{args.campaign_id}-r{ordinal:02d}"
        report_path = artifact_root / "runs" / run_id / "first_loop_report.json"
        if report_path.exists():
            print(json.dumps({"status": "stopped", "reason": "run_id_already_exists",
                              "run_id": run_id, "report": str(report_path)}))
            return 5
        command = [sys.executable, "-m", "etf_ml.cli", "first-loop",
                   "--config", str(args.config), "--snapshot", str(args.snapshot),
                   "--baseline-root", str(args.baseline_root), "--run-id", run_id,
                   "--campaign-id", args.campaign_id, "--campaign-max-trials", "5",
                   "--mechanism-plan", "five_factor_v1"]
        log_path = log_root / f"{run_id}.log"
        with log_path.open("w", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=repo, env=env, stdout=log,
                                    stderr=subprocess.STDOUT, check=False)
        if result.returncode != 0 or not report_path.is_file():
            print(json.dumps({"status": "stopped", "reason": "run_failed_or_incomplete",
                              "run_id": run_id, "returncode": result.returncode,
                              "log": str(log_path)}))
            return result.returncode or 5
        report = json.loads(report_path.read_text(encoding="utf-8"))
        decision, candidate_status = _terminal_decision(report)
        print(json.dumps({"run_id": run_id, "status": report.get("status"),
                          "candidate_status": candidate_status, "decision": decision,
                          "report": str(report_path), "log": str(log_path)}, ensure_ascii=False))
        if decision == "accepted":
            return 0
        if decision != "continue":
            return 5
    print(json.dumps({"status": "completed", "reason": "five_attempts_exhausted",
                      "campaign_id": args.campaign_id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
