from __future__ import annotations

import os
from pathlib import Path

from rdagent.core.scenario import Scenario

from etf_ml.errors import ConfigurationError
from etf_ml.research.session import ResearchSession
from etf_ml.runtime.docker import DockerBackend
from etf_ml.utils import canonical_json


class ETFFactorScenario(Scenario):
    def __init__(self, session: ResearchSession | None = None):
        if session is None:
            source = os.environ.get("ETF_RESEARCH_SESSION_FILE")
            if not source:
                raise ConfigurationError("ETF research session must be explicit; no stock template fallback")
            session = ResearchSession.from_file(Path(source))
        self.session = session

    @property
    def background(self):
        return ("Research causally available ETF features for fixed LightGBM, h=5 open-to-open "
                "prediction, long-only rotation with cash. Signals use prior close, execution "
                "is mid-month/month-end. Model scores are rankings. Portfolio policy is fixed.")

    @property
    def rich_style_description(self):
        return "ETF factor research with isolated execution and paired development evaluation"

    def get_source_data_desc(self, task=None):
        context = self.session.context
        return canonical_json({"visible_start": context.visible_start, "visible_end": context.visible_end,
                               "fields": context.fields, "snapshot_id": context.snapshot_id})

    def get_scenario_all_desc(self, task=None, filtered_tag=None, simple_background=None):
        return self.background + "\n" + canonical_json(self.session.context)

    def get_runtime_environment(self):
        limits = self.session.config.research.limits
        result = DockerBackend(self.session.root / "factors").preflight(limits.image)
        return canonical_json({**result, "limits": limits.model_dump(mode="json")})

    @property
    def experiment_setting(self):
        return canonical_json(self.session.context.selection_rules)
