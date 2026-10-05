from types import SimpleNamespace

from etf_ml.research.factor_identity import identify_definition
from etf_ml.research.search_policy import decide_admission


def _identity():
    spec = SimpleNamespace(formula="adj_close.shift(2)", required_fields=["adj_close"], lookback=3,
                           minimum_observations=3, available_at="after_daily_ingestion",
                           missing_policy="insufficient_history_is_missing", cross_sectional=False,
                           applicable_scope="domestic_equity", research_group="trend")
    fields = {"adj_close": {"meaning": "adjusted close", "unit": "price", "adjustment": "adjusted",
                             "available_at": "after_daily_ingestion", "missing_policy": "preserve_missing"}}
    return identify_definition(spec, fields)


def _card(identity, **changes):
    value = {"trial_id": "trial", "definition_id": identity.definition_id, "evaluation_id": "evaluation",
             "evaluation_snapshot_id": "snapshot", "evaluation_baseline_id": "baseline",
             "evaluation_protocol_id": "protocol", "economic_status": "rejected", "attempt_outcome": "completed",
             "evaluation_evidence_verified": True}
    value.update(changes)
    return value


def test_complete_same_context_evaluation_reuses_and_order_does_not_matter():
    identity = _identity()
    old = _card(identity, trial_id="old", evaluation_baseline_id="other", economic_status="failed")
    current = _card(identity, trial_id="current")
    first = decide_admission(identity, [old, current], snapshot_id="snapshot", baseline_id="baseline", protocol_id="protocol")
    second = decide_admission(identity, [current, old], snapshot_id="snapshot", baseline_id="baseline", protocol_id="protocol")
    assert first.decision == second.decision == "reuse"
    assert first.reusable_evaluation_id == "evaluation"
    assert first.reused_economic_status == "rejected"


def test_conflicting_complete_evidence_blocks_and_technical_failure_repairs():
    identity = _identity()
    conflict = decide_admission(identity, [_card(identity), _card(identity, trial_id="accepted", economic_status="accepted")],
                                snapshot_id="snapshot", baseline_id="baseline", protocol_id="protocol")
    assert conflict.decision == "blocked"
    repair = decide_admission(identity, [_card(identity, evaluation_id=None, economic_status=None,
                                               failure_category="implementation_error")],
                             snapshot_id="snapshot", baseline_id="baseline", protocol_id="protocol")
    assert repair.decision == "repair"


def test_unverified_summary_card_cannot_authorize_reuse():
    identity = _identity()
    result = decide_admission(identity, [_card(identity, evaluation_evidence_verified=False)],
                              snapshot_id="snapshot", baseline_id="baseline", protocol_id="protocol")
    assert result.decision == "new"
