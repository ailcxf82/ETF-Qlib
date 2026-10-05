"""Bounded LightGBM screening on development early-stop segments only."""
from __future__ import annotations

import json
import math
import re
import statistics
import time
from pathlib import Path

import pandas as pd
from pydantic import Field, model_validator

from etf_ml.artifacts import environment_manifest
from etf_ml.backtest.metrics import predictive_metrics
from etf_ml.contracts import ModelSpec, StrictSpec
from etf_ml.data.calendar import read_calendar
from etf_ml.datasets import build, generate_labels
from etf_ml.errors import ConfigurationError, IntegrityError
from etf_ml.features.baseline import materialize
from etf_ml.models import fit, predict, save_bundle
from etf_ml.models.recorders import storage_root
from etf_ml.utils import atomic_json, code_hash, content_hash, file_hash, verify_files


class TuningPlan(StrictSpec):
    variants: dict[str, dict] = Field(min_length=2, max_length=12)
    seeds: list[int] = Field(default_factory=lambda: [42], min_length=1)
    num_threads: int = Field(default=2, ge=1, le=8)
    num_boost_round: int = Field(default=500, ge=1, le=2000)
    early_stopping_rounds: int = Field(default=50, ge=1, le=200)
    max_fits: int = Field(default=20, ge=1, le=180)

    @model_validator(mode="after")
    def bounded_variants(self):
        allowed = {"learning_rate", "num_leaves", "max_depth", "min_data_in_leaf",
                   "lambda_l1", "lambda_l2", "feature_fraction", "bagging_fraction", "bagging_freq"}
        if self.variants.get("baseline") != {}:
            raise ValueError("A baseline with empty overrides is required")
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("Seeds must be distinct")
        for name, parameters in self.variants.items():
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", name) or not set(parameters) <= allowed:
                raise ValueError("Invalid variant name or unsupported tuning parameter")
            for key, value in parameters.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError("Tuning parameters must be finite numbers")
                if key in {"num_leaves", "max_depth", "min_data_in_leaf", "bagging_freq"}:
                    if not isinstance(value, int) or value < (0 if key == "bagging_freq" else 1):
                        raise ValueError("Invalid integer tuning parameter")
                elif key in {"learning_rate", "feature_fraction", "bagging_fraction"}:
                    if not 0 < value <= 1:
                        raise ValueError("Rate or sampling fraction must be in (0, 1]")
                elif value < 0:
                    raise ValueError("Regularization cannot be negative")
            if parameters.get("num_leaves", 15) < 2:
                raise ValueError("A tree requires at least two leaves")
        return self

    def model_spec(self, name: str, seed: int) -> ModelSpec:
        # Explicit baseline, independent of unrelated YAML fragment loading.
        return ModelSpec(name="lightgbm", seed=seed, constructor={
            "loss": "mse", "learning_rate": .03, "num_leaves": 15, "max_depth": 4,
            "min_data_in_leaf": 100, "lambda_l1": 0., "lambda_l2": 10.,
            "feature_fraction": 1., "bagging_fraction": 1., "bagging_freq": 0,
            "num_threads": self.num_threads, "num_boost_round": self.num_boost_round,
            "early_stopping_rounds": self.early_stopping_rounds,
            "deterministic": True, "force_col_wise": True, **self.variants[name]},
            fit={"verbose_eval": 0})


def research_inputs(snapshot_path, config):
    """Verify only the declared research inputs; never open holdout files."""
    root = Path(snapshot_path).resolve()
    manifest = json.loads((root / "snapshot_manifest.json").read_text(encoding="utf-8"))
    if root.name != manifest["snapshot_id"]:
        raise IntegrityError("Snapshot identity differs from directory")
    if (pd.Timestamp(manifest["spec"]["holdout_start"]) != pd.Timestamp(config.validation.holdout_start)
            or manifest["universe_policy"] != config.universe.model_dump()):
        raise ConfigurationError("Tuning must retain snapshot holdout boundary and universe policy")
    names = ("research/panel.parquet", "calendar.txt", "universe.parquet")
    hashes = {name: manifest["files"][name] for name in names}
    verify_files(root, hashes)
    panel = pd.read_parquet(root / "research" / "panel.parquet")
    if (panel.index.get_level_values("datetime") >= pd.Timestamp(config.validation.holdout_start)).any():
        raise ConfigurationError("Research panel includes holdout rows")
    calendar = read_calendar(root / "calendar.txt")
    calendar = calendar[calendar < pd.Timestamp(config.validation.holdout_start)]
    universe = pd.read_parquet(root / "universe.parquet", columns=["eligible"],
        filters=[("datetime", "<", pd.Timestamp(config.validation.holdout_start))]).reindex(panel.index)
    return panel, calendar, universe, manifest, hashes


def summarize(rows, plan, folds):
    baseline = {(r["fold"], r["seed"]): r for r in rows if r["variant"] == "baseline"}
    result = []
    expected = len(folds) * len(plan.seeds)
    for variant in plan.variants:
        selected = [r for r in rows if r["variant"] == variant]
        rank_deltas, mse_ratios = [], []
        for row in selected:
            reference = baseline.get((row["fold"], row["seed"]))
            if reference is None:
                continue
            if row["validation_index_hash"] != reference["validation_index_hash"]:
                raise IntegrityError("Tuning comparison changed the validation sample")
            rank, original = row["validation"]["rank_ic"], reference["validation"]["rank_ic"]
            if rank is not None and original is not None:
                rank_deltas.append(rank - original)
            base_mse = reference["validation"]["mse"]
            if base_mse > 0:
                mse_ratios.append(row["validation"]["mse"] / base_mse)
        result.append({"variant": variant, "completed_fits": len(selected), "expected_fits": expected,
            "valid_rank_comparisons": len(rank_deltas),
            "positive_rank_comparisons": sum(v > 0 for v in rank_deltas),
            "median_rank_ic_delta": statistics.median(rank_deltas) if rank_deltas else None,
            "median_mse_ratio": statistics.median(mse_ratios) if mse_ratios else None,
            "best_at_first_round": sum(r["training"]["best_iteration"] == 1 for r in selected)})
    return result


def render_report(report):
    def number(value):
        return "unknown" if value is None else f"{value:.6f}"
    lines = ["# LightGBM 有限调参诊断", "",
        f"状态：{report['status']}；用途：研发验证段预筛。", "",
        "所有参数版本使用相同特征、标签和时间折。验证段同时参与早停与本次参数比较，结果有选择偏差。",
        "未评价 selection、独立 holdout 或组合收益；没有自动替换正式模型。", "",
        f"数据快照 G0 状态：{report['qualification'].get('formal_g0_passed', 'unknown')}。", "",
        "| 参数版本 | 完成/计划 | RankIC 改善配对数 | RankIC 增量中位数 | MSE 比值中位数 | 最佳仅一轮 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for row in report["summary"]:
        lines.append(f"| {row['variant']} | {row['completed_fits']}/{row['expected_fits']} | "
            f"{row['positive_rank_comparisons']}/{row['valid_rank_comparisons']} | "
            f"{number(row['median_rank_ic_delta'])} | {number(row['median_mse_ratio'])} | "
            f"{row['best_at_first_round']} |")
    lines += ["", "MSE 比值小于 1 表示低于相同折、相同种子的基线误差；RankIC 增量大于 0 表示排序改善。",
        "缺少有效相关性的配对仍计入计划总数，不视为改善。不同折不直接比较原始 MSE 大小。", "",
        "| 版本 | 折 | 种子 | 最佳轮数 | 实际评估轮数 | 验证 RankIC | 验证 MSE |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for row in report["rows"]:
        lines.append(f"| {row['variant']} | {row['fold']} | {row['seed']} | "
            f"{row['training']['best_iteration']} | {row['training']['evaluated_rounds']} | "
            f"{number(row['validation']['rank_ic'])} | {number(row['validation']['mse'])} |")
    lines += ["", "下一阶段应冻结少量候选，再做多种子、selection 和成本/风险验证。当前表格不能证明投资有效。", ""]
    return "\n".join(lines)


def run_screen(config, snapshot_path, plan: TuningPlan, output: Path, *, progress=None):
    total = len(config.validation.folds) * len(plan.seeds) * len(plan.variants)
    if total == 0 or total > plan.max_fits:
        raise ConfigurationError("Tuning fit count exceeds the predeclared budget or has no folds")
    output, snapshot_path = Path(output).resolve(), Path(snapshot_path).resolve()
    if output == snapshot_path or output.is_relative_to(snapshot_path):
        raise ConfigurationError("Tuning output cannot modify the source snapshot")
    if output.exists():
        raise ConfigurationError("Tuning output already exists; use a new run directory")
    panel, calendar, universe, snapshot, hashes = research_inputs(snapshot_path, config)
    features = materialize({"snapshot_id": snapshot["snapshot_id"]}, panel)
    labels, events = generate_labels(panel, calendar, config.label)
    identity = {"plan": plan.model_dump(), "config": config.model_dump(mode="json"),
        "snapshot_id": snapshot["snapshot_id"], "snapshot_manifest_sha256": file_hash(snapshot_path / "snapshot_manifest.json"),
        "research_input_hashes": hashes, "feature_set_id": features.feature_set_id,
        "source_code_hash": code_hash(), "environment": environment_manifest()}
    output.mkdir(parents=True, exist_ok=False)
    atomic_json(output / "protocol.json", identity)
    report = {"status": "running", "purpose": "development_early_stop_screen",
        "protocol_id": content_hash(identity), "qualification": snapshot.get("qualification", {}),
        "selection_evaluated": False, "holdout_evaluated": False, "portfolio_evaluated": False,
        "investment_accepted": False, "rows": [], "summary": []}
    atomic_json(output / "report.json", report)
    try:
        import qlib
        from qlib.constant import REG_CN
        recorder = storage_root(output) / content_hash(identity)[:16]
        recorder.mkdir(parents=True, exist_ok=True)
        qlib.init(region=REG_CN, kernels=1, exp_manager={"class": "MLflowExpManager",
            "module_path": "qlib.workflow.expm", "kwargs": {"uri": recorder.as_uri(), "default_exp_name": "model-tuning"}})
        for fold in config.validation.folds:
            prepared = build(features, labels, events, fold, model_kind="lightgbm",
                eligibility=universe.eligible, holdout_start=config.validation.holdout_start)
            valid_features = features.frame.loc[prepared.masks["valid"]]
            valid_labels = labels.loc[valid_features.index]
            for seed in plan.seeds:
                for variant in plan.variants:
                    if code_hash() != identity["source_code_hash"]:
                        raise IntegrityError("Source changed during tuning; start a new protocol")
                    started = time.monotonic()
                    spec = plan.model_spec(variant, seed)
                    bundle = fit(prepared, spec, feature_set_id=features.feature_set_id,
                        snapshot_id=snapshot["snapshot_id"], horizon=config.label.horizon,
                        universe_policy=config.universe.model_dump(),
                        run_id=f"tune-{content_hash(identity)[:12]}-{fold.name}-{seed}-{variant}")
                    model_path = save_bundle(bundle, output / "models")
                    scores = predict(bundle, valid_features)
                    metrics = predictive_metrics(scores, valid_labels)
                    metrics["mse"] = float(((scores - valid_labels) ** 2).mean())
                    prediction_path = output / f"{fold.name}-{seed}-{variant}-validation.parquet"
                    scores.to_frame().to_parquet(prediction_path)
                    row = {"variant": variant, "fold": fold.name, "seed": seed,
                        "model_id": bundle.manifest["model_id"], "model_path": str(model_path),
                        "model_manifest_sha256": file_hash(model_path / "manifest.json"),
                        "prediction_sha256": file_hash(prediction_path),
                        "validation_index_hash": prepared.manifest["sample_index_hashes"]["valid"],
                        "training": {k: bundle.manifest["training"][k] for k in
                            ("best_iteration", "evaluated_rounds", "retained_rounds", "stop_reason", "diagnostics")},
                        "validation": {k: v for k, v in metrics.items() if k != "by_date"},
                        "elapsed_seconds": time.monotonic() - started}
                    report["rows"].append(row)
                    report["summary"] = summarize(report["rows"], plan, config.validation.folds)
                    atomic_json(output / "report.json", report)
                    if progress:
                        progress({"completed_fits": len(report["rows"]), "total_fits": total,
                                  "variant": variant, "fold": fold.name, "seed": seed})
        if code_hash() != identity["source_code_hash"]:
            raise IntegrityError("Source changed during tuning")
        verify_files(snapshot_path, hashes)
        if file_hash(snapshot_path / "snapshot_manifest.json") != identity["snapshot_manifest_sha256"]:
            raise IntegrityError("Snapshot manifest changed during tuning")
        report["status"] = "completed"
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__)
        raise
    finally:
        atomic_json(output / "report.json", report)
        (output / "report.md").write_text(render_report(report), encoding="utf-8")
    return report
