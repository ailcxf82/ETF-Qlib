from scripts.run_mechanism_campaign import _terminal_decision


def test_campaign_runner_continues_only_after_a_complete_research_decision():
    assert _terminal_decision({"status": "completed", "candidate_decisions": [
        {"status": "rejected"}]}) == ("continue", "rejected")
    assert _terminal_decision({"status": "completed", "candidate_decisions": [
        {"status": "inconclusive"}]}) == ("continue", "inconclusive")
    assert _terminal_decision({"status": "completed", "candidate_decisions": [
        {"status": "accepted"}]}) == ("accepted", "accepted")


def test_campaign_runner_stops_on_incomplete_or_ambiguous_results():
    assert _terminal_decision({"status": "incomplete", "candidate_decisions": []}) == ("stop", None)
    assert _terminal_decision({"status": "completed", "candidate_decisions": []}) == ("stop", None)
    assert _terminal_decision({"status": "completed", "candidate_decisions": [
        {"status": "failed"}]}) == ("stop", "failed")
