"""Run a bounded development-only parameter screen; see docs/24-lgbm-tuning.md."""
from __future__ import annotations

import argparse
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import yaml

from etf_ml.config import load_config
from etf_ml.research.model_tuning import TuningPlan, run_screen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = TuningPlan.model_validate(yaml.safe_load(args.plan.read_text(encoding="utf-8")))
    def progress(value):
        print(json.dumps(value), file=sys.stderr, flush=True)
    with redirect_stdout(sys.stderr):
        report = run_screen(load_config(args.config), args.snapshot, plan, args.output, progress=progress)
    print(json.dumps({"status": report["status"], "report": str(args.output / "report.md")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
