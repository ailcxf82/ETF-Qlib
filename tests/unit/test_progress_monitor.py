import json
import sys

from etf_ml.cli import main
from etf_ml.config import load_config
from etf_ml.research.progress import Progress


def test_progress_events_snapshot_and_monitor(tmp_path, capsys):
    root = tmp_path / "run"
    progress = Progress(root, "demo", "paired_research")
    progress.emit("seed_started", kind="baseline", seed=42)
    progress.emit("model_completed", kind="baseline", seed=42, fold="one",
                  completed_models=1, total_models=2)
    events = [json.loads(line) for line in (root / "progress.jsonl").read_text().splitlines()]
    assert [event["phase"] for event in events] == ["seed_started", "model_completed"]
    assert json.loads((root / "progress.json").read_text()) == events[-1]
    assert main(["monitor-run", "--path", str(root), "--once"]) == 0
    assert json.loads(capsys.readouterr().out)["completed_models"] == 1
    progress.emit("finished", status="completed")
    assert main(["monitor-run", "--path", str(root), "--interval", "0.01"]) == 0
    assert json.loads(capsys.readouterr().out)["phase"] == "finished"


def test_monitor_reports_failed_run_without_waiting(tmp_path, capsys):
    root = tmp_path / "run"
    root.mkdir()
    (root / "status.json").write_text(json.dumps({"status": "failed", "reason": "worker_failed"}))
    assert main(["monitor-run", "--path", str(root)]) == 4
    assert json.loads(capsys.readouterr().out)["reason"] == "worker_failed"


def test_cancel_run_marks_interrupted_run_terminal(tmp_path, capsys):
    root = tmp_path / "run"
    root.mkdir()
    (root / "status.json").write_text(json.dumps({"status": "running", "run_id": "demo"}))
    assert main(["cancel-run", "--path", str(root), "--reason", "operator_stop",
                 "--process-exited"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "cancelled"
    assert main(["monitor-run", "--path", str(root)]) == 4
    assert json.loads(capsys.readouterr().out)["status"] == "cancelled"


def test_first_loop_progress_records_data_preparation(tmp_path, monkeypatch):
    from etf_ml.research import first_loop

    config = load_config(overrides={"artifact_root": tmp_path / "artifacts",
                                    "research": {"budget_mode": "unlimited", "max_trials": 1}})
    # This synthetic progress-flow test does not evaluate investment quality;
    # explicitly configure the formal stress gate introduced for real runs.
    config.research.stress_min_excess_return = 0
    output = config.artifact_root / "runs" / "demo"
    output.mkdir(parents=True)

    class Snapshot:
        snapshot_id = "snapshot"
        path = tmp_path / "snapshot"
        manifest = {"spec": config.data.model_dump(mode="json"),
                    "universe_policy": config.universe.model_dump(mode="json"),
                    "qualification": {"mode": "diagnostic"}}

    snapshot = Snapshot()
    snapshot.path.mkdir()
    (snapshot.path / "research").mkdir()
    from etf_ml.utils import atomic_json, file_hash
    atomic_json(snapshot.path / "data_quality.json", {"status": "diagnostic",
                "qualification": snapshot.manifest["qualification"]})
    snapshot.manifest["files"] = {"data_quality.json": file_hash(snapshot.path / "data_quality.json")}
    atomic_json(snapshot.path / "snapshot_manifest.json", snapshot.manifest)
    monkeypatch.setattr(first_loop.pd, "read_parquet", lambda path: __import__("pandas").DataFrame({"close": [1.0]}))
    monkeypatch.setattr(first_loop, "materialize", lambda *_: type("Features", (), {
        "feature_set_id": "features", "frame": __import__("pandas").DataFrame({"f": [1.0]})})())
    def fake_baseline(*args, **kwargs):
        baseline_root = args[2]
        baseline_root.mkdir(parents=True)
        report = {"status": "completed", "by_fold": []}
        (baseline_root / "baseline_report.json").write_text(json.dumps(report))
        return report
    monkeypatch.setattr(first_loop, "execute_baseline", fake_baseline)
    monkeypatch.setattr(first_loop, "ResearchSession", lambda *args, **kwargs: object())
    class Controller:
        def __init__(self, *args, **kwargs):
            assert kwargs["campaign_id"] == "formal-five"
            assert kwargs["campaign_max_trials"] == 5
            self.root = output / "research"
            self.root.mkdir()
            (self.root / "checkpoint.json").write_text("{}")

        def run(self):
            return {"status": "completed", "trials": [], "billing": {"calls": {}}}
    campaign_summary = {"campaign_id": "formal-five", "max_attempts": 5}
    assert first_loop.closure_evidence({"status": "completed", "billing": {},
                                        "campaign_summary": campaign_summary}, [], {"by_fold": []})[
        "campaign_summary"] == campaign_summary
    monkeypatch.setattr(first_loop, "ResearchController", Controller)
    monkeypatch.setattr(first_loop, "closure_evidence", lambda *args, **kwargs: {"status": "incomplete"})

    first_loop.execute_first_loop(config, output, run_id="demo", snapshot=snapshot,
                                  campaign_id="formal-five", campaign_max_trials=5)
    phases = [json.loads(line)["phase"] for line in (output / "progress.jsonl").read_text().splitlines()]
    assert "snapshot_ready" in phases
    assert "loading_research_panel" in phases
    assert "materializing_baseline_features" in phases
    assert "baseline_features_ready" in phases
    assert "creating_research_session" in phases
    assert "research_session_ready" in phases


def test_experiment_worker_publishes_model_progress(tmp_path, monkeypatch):
    from etf_ml.research import experiment_worker

    config = load_config(overrides={"artifact_root": tmp_path / "artifacts"})
    root = tmp_path / "worker"
    root.mkdir()
    request_file = root / "pipeline_request.json"
    request_file.write_text(json.dumps({"mode": "baseline", "run_id": "demo",
                                        "config": config.model_dump(mode="json"),
                                        "snapshot_path": str(tmp_path / "snapshot"),
                                        "output_root": str(root)}), encoding="utf-8")

    def fake_baseline(*args, **kwargs):
        kwargs["progress"].emit("model_completed", fold="one", model="lightgbm")
        return {}

    monkeypatch.setattr(experiment_worker, "run_baseline", fake_baseline)
    monkeypatch.setattr(sys, "argv", ["experiment_worker", str(request_file)])
    experiment_worker.main()
    events = [json.loads(line) for line in (root / "progress.jsonl").read_text().splitlines()]
    assert [event["phase"] for event in events] == ["started", "model_completed", "finished"]
    assert events[-1]["status"] == "completed"


def test_monitor_run_id_includes_research_tree(tmp_path, monkeypatch, capsys):
    config = load_config(overrides={"artifact_root": tmp_path / "artifacts"})
    monkeypatch.setattr("etf_ml.cli.load_config", lambda *args: config)
    (config.artifact_root / "runs" / "demo").mkdir(parents=True)
    paired = config.artifact_root / "research" / "demo" / "paired" / "runs" / "pair"
    Progress(paired, "pair", "paired_research").emit("seed_started", kind="baseline")
    assert main(["monitor-run", "--run-id", "demo", "--once"]) == 0
    assert json.loads(capsys.readouterr().out)["component"] == "paired_research"
