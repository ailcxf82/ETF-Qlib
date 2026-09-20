from __future__ import annotations

import os
import sys
from pathlib import Path

from etf_ml.errors import ConfigurationError

EXTENSIONS = {
    "QLIB_FACTOR_SCEN": "etf_ml.adapters.rdagent.scenario.ETFFactorScenario",
    "QLIB_FACTOR_HYPOTHESIS_GEN": "etf_ml.adapters.rdagent.proposal.ETFHypothesisGen",
    "QLIB_FACTOR_HYPOTHESIS2EXPERIMENT": "etf_ml.adapters.rdagent.proposal.ETFHypothesis2Experiment",
    "QLIB_FACTOR_CODER": "etf_ml.adapters.rdagent.coder.ETFFactorCoder",
    "QLIB_FACTOR_RUNNER": "etf_ml.adapters.rdagent.runner.ETFFactorRunner",
    "QLIB_FACTOR_SUMMARIZER": "etf_ml.adapters.rdagent.feedback.ETFFeedback",
}


def configure(session_file: Path):
    if "rdagent.app.qlib_rd_loop.conf" in sys.modules:
        raise ConfigurationError("Configure ETF extensions before importing RDAgent factor settings")
    if not Path(session_file).is_file():
        raise ConfigurationError("Explicit ETF research session file is required")
    os.environ.update(EXTENSIONS)
    os.environ["ETF_RESEARCH_SESSION_FILE"] = str(Path(session_file).resolve())
    return dict(EXTENSIONS)
