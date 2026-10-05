import json

import pytest

from etf_ml.errors import ConfigurationError, IntegrityError
from etf_ml.research.campaign import CampaignLedger
from etf_ml.research.campaign import FIVE_FACTOR_MECHANISM_PLAN


def _parallel_begin_campaign_trial(root, index, start_event, ready_queue, result_queue):
    ready_queue.put(index)
    if not start_event.wait(30):
        result_queue.put((index, "start_timeout"))
        return
    campaign = CampaignLedger(root, "parallel-cap", 5)
    result_queue.put((index, campaign.begin_trial(
        run_id=f"parallel-{index}", trial_index=0,
        compatibility_group_id="group", protocol_id="protocol")))


def test_campaign_cap_is_atomic_and_summary_separates_compatibility_groups(tmp_path):
    campaign = CampaignLedger(tmp_path / "campaigns", "bounded-1", 2)
    campaign.initialize()
    assert campaign.begin_trial(run_id="run-a", trial_index=0,
                                compatibility_group_id="group-a", protocol_id="protocol-a")
    assert campaign.begin_trial(run_id="run-b", trial_index=0,
                                compatibility_group_id="group-b", protocol_id="protocol-b")
    assert not campaign.begin_trial(run_id="run-c", trial_index=0,
                                    compatibility_group_id="group-a", protocol_id="protocol-a")

    record = {"trial_index": 0, "research_card": {"definition_id": "definition-a",
        "evaluation_id": "evaluation-a", "evaluation_protocol_id": "protocol-a",
        "evaluation_snapshot_id": "snapshot-a", "evaluation_baseline_id": "baseline-a"},
        "result": {"status": "rejected"}, "campaign_candidates": [
            {"definition_id": "definition-a", "evaluation_id": "evaluation-a", "outcome": "rejected"},
            {"definition_id": "definition-b", "evaluation_id": "evaluation-b", "outcome": "rejected"}],
        "usage_summary": {"cost": {"calls": {
            "call-known": {"status": "completed", "actual_cost": "0.01", "usage": {"attempts": [
                {"provider_input_tokens": 20, "provider_output_tokens": 5}]}},
            "call-unknown": {"status": "cost_unknown", "actual_cost": None, "usage": {"attempts": [
                {"provider_input_tokens": None, "provider_output_tokens": None}]}}}}}}
    campaign.commit_trial(run_id="run-a", trial_index=0, compatibility_group_id="group-a",
                          protocol_id="protocol-a", record=record)
    campaign.commit_trial(run_id="run-a", trial_index=0, compatibility_group_id="group-a",
                          protocol_id="protocol-a", record=record)

    summary = campaign.summary()
    assert summary["generation_attempts"] == summary["campaign_attempted_trials"] == 2
    assert summary["open_attempts"] == 1
    assert summary["usage_audit_status"] == "partial"
    assert summary["unresolved_trial_usage_slots"] == 1
    assert summary["unique_definitions"] == summary["completed_unique_evaluations"] == 2
    assert summary["cost_known_subtotal"] == "0.01"
    assert summary["unknown_cost_calls"] == 1
    assert summary["provider_usage_coverage"] == .5
    assert set(summary["by_compatibility_group"]) == {"group-a", "group-b"}


def test_campaign_attempt_cap_is_atomic_across_processes(tmp_path):
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    campaign_root = tmp_path / "campaigns"
    campaign = CampaignLedger(campaign_root, "parallel-cap", 5)
    campaign.initialize()
    start_event, ready_queue, result_queue = context.Event(), context.Queue(), context.Queue()
    workers = [context.Process(target=_parallel_begin_campaign_trial,
                               args=(campaign_root, index, start_event, ready_queue, result_queue))
               for index in range(8)]
    try:
        for worker in workers:
            worker.start()
        ready = {ready_queue.get(timeout=45) for _ in workers}
        assert ready == set(range(len(workers)))
        start_event.set()
        results = [result_queue.get(timeout=45) for _ in workers]
        for worker in workers:
            worker.join(timeout=15)
        assert all(worker.exitcode == 0 for worker in workers)
        outcomes = [result for _, result in results]
        assert outcomes.count(True) == 5
        assert outcomes.count(False) == 3
        assert campaign.summary()["generation_attempts"] == 5
    finally:
        start_event.set()
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=5)
        ready_queue.close()
        result_queue.close()


def test_campaign_identity_and_event_chain_fail_closed(tmp_path):
    campaign = CampaignLedger(tmp_path / "campaigns", "bounded-2", 1)
    campaign.initialize()
    with pytest.raises(ConfigurationError, match="immutable"):
        CampaignLedger(tmp_path / "campaigns", "bounded-2", 2).initialize()
    campaign.begin_trial(run_id="run-a", trial_index=0,
                         compatibility_group_id="group-a", protocol_id="protocol-a")
    event = next((campaign.events).glob("*.json"))
    payload = json.loads(event.read_text(encoding="utf-8"))
    payload["protocol_id"] = "tampered"
    event.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(IntegrityError, match="chain"):
        campaign.summary()


def test_campaign_wall_clock_is_frozen_and_blocks_later_trials_and_dispatch(tmp_path, monkeypatch):
    import etf_ml.research.campaign as module
    from etf_ml.errors import BudgetError

    clock = {"now": 1_000_000_000}
    monkeypatch.setattr(module.time, "time_ns", lambda: clock["now"])
    campaign = CampaignLedger(tmp_path / "campaigns", "timed", 5, max_duration_seconds=10)
    campaign.initialize()
    assert campaign.begin_trial(run_id="run-a", trial_index=0,
        compatibility_group_id="group", protocol_id="protocol")
    assert campaign.time_limit_expired() is False
    clock["now"] += 10_000_000_000
    assert campaign.time_limit_expired() is True
    with pytest.raises(BudgetError, match="wall-clock limit"):
        campaign.require_time_remaining()
    assert not campaign.begin_trial(run_id="run-b", trial_index=0,
        compatibility_group_id="group", protocol_id="protocol")
    with pytest.raises(ConfigurationError, match="wall-clock limit is immutable"):
        CampaignLedger(tmp_path / "campaigns", "timed", 5, max_duration_seconds=11).initialize()


@pytest.mark.parametrize("cap", [True, 1.5, float("nan"), float("inf"), "5", None, 0, -1])
def test_campaign_attempt_cap_requires_a_positive_integer(tmp_path, cap):
    with pytest.raises(ConfigurationError, match="positive finite"):
        CampaignLedger(tmp_path, "strict-cap", cap)


def test_campaign_audit_view_is_read_only_and_preserves_uncommitted_slots(tmp_path):
    from etf_ml.utils import source_hashes
    campaign = CampaignLedger(tmp_path, "audited", 1)
    campaign.initialize()
    campaign.begin_trial(run_id="run-a", trial_index=0, compatibility_group_id="group", protocol_id="protocol")
    before = source_hashes(campaign.root)
    summary, events = campaign.audit_view()
    assert summary["open_attempts"] == summary["generation_attempts"] == 1
    assert len(events) == 1
    assert source_hashes(campaign.root) == before


def test_campaign_mechanism_slots_are_unique_frozen_and_attempts_are_not_released(tmp_path):
    campaign = CampaignLedger(tmp_path, "mechanism-plan", 5, mechanism_plan=FIVE_FACTOR_MECHANISM_PLAN)
    campaign.initialize()
    assigned = []
    for index in range(5):
        run_id = f"candidate-{index}"
        assert campaign.begin_trial(run_id=run_id, trial_index=0,
            compatibility_group_id="group", protocol_id="protocol")
        assigned.append(campaign.trial_mechanism_assignment(run_id=run_id, trial_index=0))
    assert [row["slot"] for row in assigned] == [row["slot"] for row in FIVE_FACTOR_MECHANISM_PLAN]
    assert campaign.summary()["by_mechanism_slot"] == {row["slot"]: 1 for row in FIVE_FACTOR_MECHANISM_PLAN}
    assert not campaign.begin_trial(run_id="candidate-six", trial_index=0,
        compatibility_group_id="group", protocol_id="protocol")
    with pytest.raises(ConfigurationError, match="mechanism plan"):
        CampaignLedger(tmp_path, "mechanism-plan", 5,
            mechanism_plan=list(reversed(FIVE_FACTOR_MECHANISM_PLAN))).initialize()


@pytest.mark.parametrize("actual_cost,attempts,expected_reasons", [
    (None, [{"provider_input_tokens": 12, "provider_output_tokens": 4}], ["provider_cost_unknown"]),
    ("0.02", [{"provider_input_tokens": None, "provider_output_tokens": None}], ["provider_usage_incomplete"]),
    ("0.02", [{"provider_input_tokens": 12, "provider_output_tokens": 4}], []),
])
def test_usage_audit_status_includes_provider_cost_and_token_completeness(
        tmp_path, actual_cost, attempts, expected_reasons):
    campaign = CampaignLedger(tmp_path, "usage-audit", 1)
    campaign.initialize()
    campaign.begin_trial(run_id="run-a", trial_index=0,
        compatibility_group_id="group", protocol_id="protocol")
    record = {"result": {"status": "rejected"}, "research_card": {"definition_id": "factor"},
        "usage_summary": {"cost": {"calls": {"provider-call": {
            "status": "completed" if actual_cost is not None else "cost_unknown",
            "actual_cost": actual_cost, "usage": {"attempts": attempts}}}}}}
    campaign.commit_trial(run_id="run-a", trial_index=0, compatibility_group_id="group",
        protocol_id="protocol", record=record)

    summary = campaign.summary()
    assert summary["schema_version"] == "research-campaign-summary-v2"
    assert summary["usage_audit_reasons"] == expected_reasons
    assert summary["usage_audit_status"] == ("partial" if expected_reasons else "completed")
