from __future__ import annotations

from pathlib import Path
import pandas as pd

from etf_ml.backtest.metrics import predictive_metrics
from etf_ml.backtest.results import evaluate_with_stress
from etf_ml.backtest.auxiliary import run_auxiliary
from etf_ml.contracts import AppConfig, FeatureArtifact
from etf_ml.data.source import require_panel
from etf_ml.errors import ConfigurationError, QualityError
from etf_ml.data.calendar import read_calendar
from etf_ml.data.snapshot import load_snapshot
from etf_ml.datasets import build, generate_labels
from etf_ml.features import materialize
from etf_ml.models import fit, predict, save_bundle
from etf_ml.models.recorders import storage_root
from etf_ml.utils import atomic_json, content_hash, file_hash, FileLock


def run_baseline(config: AppConfig, snapshot_path: Path, output: Path, *,
                 run_id: str, feature_override: FeatureArtifact | None = None,
                 protocol_id: str | None = None,
                 cost_multipliers: tuple[float, ...] = (2.0,),
                 progress=None, progress_fields: dict | None = None,
                 auxiliary_cache_root: Path | None = None) -> dict:
    config.portfolio.require_resolved()
    if not config.models or not config.validation.folds:
        raise ConfigurationError("Baseline requires models and explicit development folds")
    if len({s.name for s in config.models}) != len(config.models):
        raise ConfigurationError("Each baseline model name must be unique")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    snapshot = load_snapshot(snapshot_path)
    from etf_ml.data.diagnostic import require_snapshot_mode
    require_snapshot_mode(config, snapshot)
    root = snapshot.path
    recorder_identity = {"run_id": run_id, "snapshot_id": snapshot.snapshot_id,
                         "config_hash": config.config_hash,
                         "feature_set_id": feature_override.feature_set_id if feature_override else "fixed_baseline"}
    recorder_uri = (storage_root(config.artifact_root) / content_hash(recorder_identity)[:16]).resolve()
    recorder_uri.mkdir(parents=True, exist_ok=True)
    with FileLock(recorder_uri / "owner.lock"):
        owner = recorder_uri / "owner.json"
        if owner.exists():
            import json
            if json.loads(owner.read_text(encoding="utf-8")) != recorder_identity:
                raise ConfigurationError("Recorder workspace identity collision")
        else:
            atomic_json(owner, recorder_identity)
    import qlib
    from qlib.constant import REG_CN
    qlib.init(provider_uri=root / "research" / "provider", region=REG_CN, kernels=1,
              exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
                           "kwargs": {"uri": recorder_uri.as_uri(),
                                      "default_exp_name": "etf-baseline"}})
    panel = pd.read_parquet(root / "research" / "panel.parquet")
    calendar = read_calendar(root / "calendar.txt")
    calendar = calendar[calendar < pd.Timestamp(config.validation.holdout_start)]
    universe = pd.read_parquet(root / "universe.parquet").reindex(panel.index)
    benchmark = pd.read_parquet(root / "benchmark.parquet")
    events = pd.read_parquet(root / "events.parquet")
    # The snapshot retains the full source evidence; execution uses dated rows.
    events.attrs = {}
    from etf_ml.data.income_supplement import snapshot_income
    income, income_rules = snapshot_income(snapshot, "research")
    if not events.empty:
        events = events[events.datetime < pd.Timestamp(config.validation.holdout_start)].copy()
    feature_set = feature_override or materialize({"snapshot_id": snapshot.snapshot_id}, panel)
    require_panel(feature_set.frame, numeric=True)
    if (feature_set.manifest.get("snapshot_id") != snapshot.snapshot_id or
            not feature_set.frame.index.equals(panel.index)):
        raise QualityError("Feature override must preserve the snapshot and complete research index")
    feature_set.frame.to_parquet(output / "baseline_features.parquet")
    atomic_json(output / "feature_manifest.json", {
        **feature_set.manifest, "feature_set_id": feature_set.feature_set_id})
    labels, label_events = generate_labels(panel, calendar, config.label)
    label_events.to_parquet(output / "label_events.parquet")
    rows, auxiliary = [], []
    total_models = len(config.validation.folds) * len(config.models)
    for fold in config.validation.folds:
        valid_days = calendar[(calendar >= pd.Timestamp(fold.selection.start)) &
                              (calendar <= pd.Timestamp(fold.selection.end))]
        if not len(valid_days):
            raise QualityError("Selection fold has no covered trading days")
        common = {"universe": universe, "calendar": calendar, "benchmark": benchmark,
                  "provider": root / "research" / "provider", "recorder_uri": recorder_uri,
                  "start_time": valid_days[0], "end_time": valid_days[-1], "events": events,
                  "income": income, "income_rules": income_rules,
                  "schedule_calendar": read_calendar(root / "execution_calendar.txt"),
                  "annualization_days": config.validation.annualization_days,
                  "risk_free_rate": config.validation.risk_free_rate}
        evaluation_index = None
        for spec in config.models:
            if progress:
                progress.emit("model_started", fold=fold.name, model=spec.name,
                              completed_models=len(rows), total_models=total_models,
                              **(progress_fields or {}))
            identity = (f"{run_id[:24]}-{fold.name[:16]}-{spec.name}-" +
                        content_hash({"parent_run_id": run_id, "fold": fold.name, "model": spec})[:16])
            prepared = build(feature_set, labels, label_events, fold,
                             model_kind=spec.name, eligibility=universe.eligible,
                             holdout_start=config.validation.holdout_start)
            current_index = prepared.features.index[prepared.masks["test"]]
            if evaluation_index is not None and not current_index.equals(evaluation_index):
                raise QualityError("Baseline models changed the common evaluation sample")
            evaluation_index = current_index
            bundle = fit(prepared, spec, feature_set_id=feature_set.feature_set_id,
                         snapshot_id=snapshot.snapshot_id, horizon=config.label.horizon,
                         universe_policy=config.universe.model_dump(), run_id=identity)
            model_path = save_bundle(bundle, config.artifact_root / "models")
            scores = predict(bundle, feature_set.frame)
            fold_path = output / fold.name / spec.name
            fold_path.mkdir(parents=True)
            scores.to_frame().to_parquet(fold_path / "predictions.parquet")
            atomic_json(fold_path / "dataset_manifest.json", prepared.manifest)
            predictive = predictive_metrics(scores.loc[evaluation_index], labels.loc[evaluation_index])
            result, stress = evaluate_with_stress(scores, config.portfolio, panel,
                                                  output=fold_path, cost_multipliers=cost_multipliers,
                                                  **common)
            if not result.daily_returns.index.equals(valid_days):
                raise QualityError("Model backtest changed the common evaluation dates")
            row = {"fold": fold.name, "model": spec.name, "seed": spec.seed,
                   "model_spec": spec.model_dump(mode="json"),
                   "daily_index_hash": content_hash([str(t) for t in result.daily_returns.index]),
                   "cost_stress": stress, "model_id": bundle.manifest["model_id"],
                   "model_path": str(model_path),
                   "model_manifest_hash": file_hash(model_path / "manifest.json"),
                   "recorder_uri": str(recorder_uri), "portfolio": result.metrics,
                   # Immutable local evidence used by development-only V2
                   # diagnostics; it is not used by selection gates.
                   "backtest_path": str(fold_path),
                   "predictive": predictive, "dataset": prepared.manifest}
            atomic_json(fold_path / "metrics.json", row)
            rows.append(row)
            if progress:
                progress.emit("model_completed", fold=fold.name, model=spec.name,
                              completed_models=len(rows), total_models=total_models,
                              **(progress_fields or {}))
        if progress:
            progress.emit("auxiliary_started", fold=fold.name, **(progress_fields or {}))
        auxiliary.extend(run_auxiliary(config, panel, calendar, universe, fold=fold,
            snapshot_id=snapshot.snapshot_id, protocol_id=protocol_id, labels=labels,
            evaluation_index=evaluation_index, output=output / fold.name / "auxiliary",
            cost_multipliers=cost_multipliers, cache_root=auxiliary_cache_root,
            progress=progress, progress_fields=progress_fields, **common))
        if progress:
            progress.emit("fold_completed", fold=fold.name, completed_models=len(rows),
                          total_models=total_models, **(progress_fields or {}))
    report = {"status": "completed", "research_passed": None,
              "qualification": snapshot.manifest.get("qualification", {"mode": "formal"}),
              "snapshot_id": snapshot.snapshot_id, "feature_set_id": feature_set.feature_set_id,
              "protocol_id": protocol_id, "cost_multipliers": list(cost_multipliers),
              "baseline_feature_set_id": feature_set.manifest.get("baseline_feature_set_id"),
              "by_fold": rows, "auxiliary_by_fold": auxiliary,
              "note": "Baseline execution does not imply investment effectiveness"}
    atomic_json(output / "baseline_report.json", report)
    return report
