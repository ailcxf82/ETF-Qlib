"""Preregistered, zero-provider-call screening of trusted library definitions."""
from __future__ import annotations

import hashlib
import statistics
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from etf_ml.artifacts import RUN_ID, RunStore, environment_manifest
from etf_ml.contracts import SegmentSpec
from etf_ml.data.calendar import read_calendar
from etf_ml.data.snapshot import load_snapshot
from etf_ml.data.source import require_panel
from etf_ml.datasets.labels import generate_labels
from etf_ml.datasets.splits import learning_mask, validate_fold
from etf_ml.errors import ConfigurationError, IntegrityError, QualityError
from etf_ml.features.alpha101 import (SOURCE, catalog, compute_alpha101,
                                      library_source_descriptor)
from etf_ml.features.baseline import materialize
from etf_ml.features.validators import check_causality, validate_factor
from etf_ml.backtest.metrics import (positive_icir_gate, predictive_metrics,
                                     select_factor_orientation, summarize_icir_gates)
from etf_ml.research.progress import Progress
from etf_ml.research.context import FactorSpec
from etf_ml.research.qualification import read_object
from etf_ml.utils import FileLock, atomic_json, code_hash, content_hash, ensure_within, file_hash, verify_files


SCREEN_RULE = {
    "version": "library-screen-v4", "max_definitions": 20, "max_shortlist": 5,
    "selection": "positive median and strict majority positive inner-validation incremental RankIC",
    "ranking": "median incremental RankIC descending, definition hash ascending for ties",
    "inner_validation_fraction": .2, "ridge_alpha": 1., "horizons": [1, 5, 10, 20],
    "correlation_cluster_abs_rank_ic": .9, "correlation_cluster_min_fold_share": .5,
    "direction": "training-only direct/reverse orientation; both IC > 0 and ICIR > 0 in at least 3 of 5 folds for signal candidacy; portfolio gates remain separate",
    "factor_signal_rule": "strict_majority",
    "mechanism_slots": {"intraday_momentum": "trend_momentum", "reversal": "reversal",
                        "volume_price": "volume_price", "volatility": "volatility_risk_adjusted",
                        "range": "orthogonal_new"},
    "mechanism_quota": "at_most_one_shortlisted_definition_per_slot; no quota relaxation",
    "formal_acceptance": False, "independent_confirmation": False,
    "correlated_candidates": "not hard rejected; increment measured relative to fixed baseline",
}


def _diverse_shortlist(ranked, definitions_by_hash):
    """Take score-ranked candidates while admitting at most one per frozen mechanism slot."""
    slots = SCREEN_RULE["mechanism_slots"]
    selected, used = [], set()
    for row in ranked:
        definition = definitions_by_hash.get(row["definition_hash"], {})
        slot = slots.get(definition.get("research_group"))
        if slot is None or slot in used:
            continue
        selected.append({**row, "mechanism_slot": slot})
        used.add(slot)
        if len(selected) == SCREEN_RULE["max_shortlist"]:
            break
    return selected


def registered_factor_spec(definition, context, *, version=1):
    """Create an auditable FactorSpec that dispatches only to the vetted local library."""
    if definition.get("definition_hash") != next((row["definition_hash"] for row in catalog()["definitions"]
                                                    if row["number"] == definition.get("number")), None):
        raise IntegrityError("Cannot register a definition outside the published Alpha101 catalogue")
    reference = {"kind": "worldquant_alpha101_etf", "number": definition["number"],
                 "definition_hash": definition["definition_hash"],
                 "source_manifest_hash": content_hash(SOURCE),
                 "implementation_version": SOURCE["implementation_version"]}
    spec = FactorSpec(factor_id=definition["factor_id"], version=version,
        hypothesis=f"Evaluate the ETF adaptation of Alpha101 formula {definition['number']}.",
        formula=definition["formula"], required_fields=definition["required_fields"],
        lookback=definition["lookback"], minimum_observations=definition["minimum_observations"],
        cross_sectional=True, direction=definition["direction"], missing_policy=definition["missing_policy"],
        applicable_scope="eligible domestic-equity ETF cross-section", complexity="registered_formula",
        research_group=definition["research_group"],
        expected_difference=f"Incremental value over the frozen baseline for Alpha101 formula {definition['number']}.",
        source=library_source_descriptor(reference), context_hash=context.context_hash,
        trusted_implementation=reference)
    spec.validate_context(context)
    return spec


def _index_hash(index):
    return hashlib.sha256(pd.util.hash_pandas_object(index).to_numpy().tobytes()).hexdigest()


def _score(prediction, labels):
    return predictive_metrics(prediction, labels)


def _model():
    return make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True),
                         StandardScaler(), Ridge(alpha=SCREEN_RULE["ridge_alpha"]))


def training_splits(panel, calendar, labels, events, eligible, validation):
    """Use only each training segment; neither early_stop nor selection chooses a factor."""
    splits = []
    for fold in validation.folds:
        validate_fold(fold, validation.holdout_start)
        days = calendar[(calendar >= pd.Timestamp(fold.train.start)) & (calendar <= pd.Timestamp(fold.train.end))]
        if len(days) < 10:
            raise QualityError("Too few training sessions for an inner chronological split")
        cut = max(1, int(len(days) * (1 - SCREEN_RULE["inner_validation_fraction"])))
        training = SegmentSpec(start=fold.train.start, end=str(days[cut - 1].date()))
        validating = SegmentSpec(start=str(days[cut].date()), end=fold.train.end)
        left = learning_mask(events, labels, training, next_start=days[cut]) & eligible
        right = learning_mask(events, labels, validating, as_of=days[-1]) & eligible
        if not left.any() or not right.any():
            raise QualityError("Inner split has no mature eligible labels")
        splits.append({"fold": fold.name, "fit": left, "validate": right,
                       "training_window": training.model_dump(mode="json"),
                       "validation_window": validating.model_dump(mode="json"),
                       "fit_index_hash": _index_hash(panel.index[left]),
                       "validation_index_hash": _index_hash(panel.index[right]),
                       "fit_available_before": str(days[cut]), "validation_as_of": str(days[-1])})
    if not splits:
        raise ConfigurationError("Library screening requires frozen development folds")
    return splits


def materialize_library_factor(panel, eligible, definition, *, inputs, root, coverage_threshold):
    """Cache trusted numerical results, binding both data and operator semantics."""
    from etf_ml.features import alpha101, validators
    require_panel(panel)
    if not eligible.index.equals(panel.index) or eligible.isna().any() or not pd.api.types.is_bool_dtype(eligible):
        raise QualityError("Library eligibility must be a complete aligned boolean series")
    key = content_hash({"definition": definition, "source": SOURCE, "inputs": inputs,
                        "implementation_hash": file_hash(Path(alpha101.__file__)),
                        "validator_hash": file_hash(Path(validators.__file__)),
                        "panel_hash": hashlib.sha256(pd.util.hash_pandas_object(panel).to_numpy().tobytes()).hexdigest(),
                        "eligibility_hash": hashlib.sha256(pd.util.hash_pandas_object(eligible).to_numpy().tobytes()).hexdigest(),
                        "environment": environment_manifest(), "coverage_threshold": coverage_threshold})
    root = Path(root)
    path = root / key
    started = time.perf_counter()
    with FileLock(root / ".locks" / (key + ".lock")):
        manifest_path = path / "feature_manifest.json"
        if manifest_path.is_file():
            manifest = read_object(manifest_path)
            body = {k: v for k, v in manifest.items() if k != "manifest_hash"}
            if (manifest.get("cache_key") != key or manifest.get("manifest_hash") != content_hash(body)
                    or manifest.get("definition") != definition or manifest.get("inputs") != inputs
                    or set(manifest.get("files", {})) != {"result.parquet"}):
                raise IntegrityError("Library cache identity changed")
            verify_files(path, manifest["files"])
            frame = pd.read_parquet(path / "result.parquet")
            if not frame.index.equals(panel.index):
                raise IntegrityError("Library cache index changed")
            return frame, manifest, {"cache_hit": True, "seconds": time.perf_counter() - started}
        number = definition["number"]
        compute = lambda view: compute_alpha101(view, number, eligible=eligible.reindex(view.index))
        frame = compute(panel)
        dates = panel.index.get_level_values("datetime").unique()
        cutoffs = sorted({dates[len(dates) // 2], dates[max(0, len(dates) - 2)]})
        for cutoff in cutoffs:
            check_causality(compute, panel, cutoff)
        warm = (panel.groupby(level="instrument").cumcount() + 1 >= definition["minimum_observations"]) & eligible
        quality = validate_factor(frame[warm], panel.index[warm], coverage_threshold=coverage_threshold)
        path.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path / "result.parquet")
        manifest = {"schema_version": "library-feature-cache-v1", "cache_key": key,
                    "definition": definition, "inputs": inputs, "quality": quality,
                    "checks": {"status": "passed", "cutoffs": [str(c) for c in cutoffs],
                               "checks": ["truncation_invariance", "future_perturbation_invariance"]},
                    "files": {"result.parquet": file_hash(path / "result.parquet")}, "path": str(path)}
        manifest["manifest_hash"] = content_hash(manifest)
        atomic_json(manifest_path, manifest)
        return frame, manifest, {"cache_hit": False, "seconds": time.perf_counter() - started}


def exposure_diagnostics(panel, universe, *, pit_verified):
    if not pit_verified or "tracking_group" not in universe:
        return {"status": "bypassed", "reason_codes": ["historical_tracking_groups_not_verified"]}
    active = universe.loc[universe.eligible]
    if active.tracking_group.isna().any():
        return {"status": "partial", "reason_codes": ["eligible_tracking_groups_missing"]}
    counts = active.groupby([active.index.get_level_values("datetime"), "tracking_group"]).size()
    return {"status": "completed", "scope": "eligible_universe_not_portfolio_holdings",
            "by_date": [{"date": str(day.date()), "eligible_etfs": int(group.sum()),
                         "tracking_groups": len(group), "largest_group": int(group.max()),
                         "groups_with_multiple_etfs": int((group > 1).sum())}
                        for day, group in counts.groupby(level=0)],
            "note": "Same-index crowding opportunities only; no unsupported claim of portfolio concentration causality"}


def factor_correlation_diagnostics(factors, splits):
    """Describe cross-sectional signal redundancy on inner-validation dates only.

    Correlation clusters are a screening aid, never an exclusion or acceptance gate.
    """
    factors = {key: value for key, value in sorted(factors.items())}
    by_fold, fold_stats = [], {}
    factor_ids = list(factors)
    dates_by_fold = {}
    threshold = SCREEN_RULE["correlation_cluster_abs_rank_ic"]
    for split in splits:
        fold, mask = split["fold"], split["validate"]
        selected = mask[mask].index
        date_rows = {}
        values = pd.DataFrame({key: series.reindex(selected) for key, series in factors.items()}, index=selected)
        values = values.replace([np.inf, -np.inf], np.nan)
        for day, frame in values.groupby(level="datetime", sort=True):
            date_rows[day] = frame
        pairs = {}
        for i, left in enumerate(factor_ids):
            for right in factor_ids[i + 1:]:
                daily = []
                for frame in date_rows.values():
                    overlap = frame[[left, right]].dropna()
                    if len(overlap) < 3:
                        continue
                    left_rank, right_rank = (overlap[column].rank(method="average")
                                             for column in (left, right))
                    if left_rank.nunique() < 2 or right_rank.nunique() < 2:
                        continue
                    correlation = left_rank.corr(right_rank)
                    if pd.notna(correlation):
                        daily.append(float(correlation))
                metric = {"fold": fold, "left": left, "right": right,
                          "matched_dates": len(daily),
                          "median_rank_correlation": float(np.median(daily)) if daily else None,
                          "median_absolute_rank_correlation": float(np.median(np.abs(daily))) if daily else None,
                          "status": "completed" if daily else "unavailable"}
                by_fold.append(metric)
                pairs[(left, right)] = metric["median_absolute_rank_correlation"]
        fold_stats[fold] = pairs
        dates_by_fold[fold] = len(date_rows)

    available_folds = len(splits)
    required_folds = max(1, int(np.floor(available_folds * SCREEN_RULE["correlation_cluster_min_fold_share"])) + 1)
    edges = []
    for left_index, left in enumerate(factor_ids):
        for right in factor_ids[left_index + 1:]:
            values = [fold_stats[split["fold"]].get((left, right)) for split in splits]
            values = [value for value in values if value is not None]
            linked = [value for value in values if value >= threshold]
            if len(linked) >= required_folds:
                edges.append({"left": left, "right": right,
                              "median_fold_absolute_rank_correlation": float(np.median(linked)),
                              "linked_folds": len(linked), "available_folds": len(values),
                              "threshold": threshold})
    adjacency = {factor_id: set() for factor_id in factor_ids}
    for edge in edges:
        adjacency[edge["left"]].add(edge["right"])
        adjacency[edge["right"]].add(edge["left"])
    clusters, seen = [], set()
    for factor_id in factor_ids:
        if factor_id in seen or not adjacency[factor_id]:
            continue
        stack, members = [factor_id], set()
        while stack:
            current = stack.pop()
            if current in members:
                continue
            members.add(current)
            stack.extend(adjacency[current] - members)
        seen.update(members)
        clusters.append(sorted(members))
    return {"schema_version": "factor-correlation-diagnostics-v1", "status": "completed",
            "scope": "inner_validation_cross_sectional_rank_correlation",
            "by_fold": by_fold, "clusters": clusters,
            "unclustered_factor_ids": [factor_id for factor_id in factor_ids if factor_id not in seen],
            "cluster_rule": {"absolute_median_daily_spearman": threshold,
                             "required_fold_share": "strict majority of folds with available evidence",
                             "required_folds": required_folds,
                             "date_counts": dates_by_fold,
                             "effect": "diagnostic_only_no_candidate_exclusion"}}


def _resume_screening(run, definitions):
    """Recover the last atomically committed prefix from retained RunStore attempts."""
    checkpoints = sorted(run.path.glob("attempt-*/screening_checkpoint.json"), reverse=True)
    if not checkpoints:
        return [], [], None
    path = checkpoints[0]
    checkpoint = read_object(path)
    body = {k: v for k, v in checkpoint.items() if k != "checkpoint_hash"}
    rows, decay = checkpoint.get("completed_definitions"), checkpoint.get("horizon_rows")
    if (checkpoint.get("schema_version") != "library-screening-checkpoint-v1"
            or checkpoint.get("identity") != run.identity or checkpoint.get("checkpoint_hash") != content_hash(body)
            or not isinstance(rows, list) or not isinstance(decay, list)):
        raise IntegrityError("Library screening checkpoint identity or hash changed")
    hashes = [row.get("definition_hash") for row in rows if isinstance(row, dict)]
    if len(hashes) != len(rows) or hashes != [d["definition_hash"] for d in definitions[:len(rows)]]:
        raise IntegrityError("Library screening checkpoint is not the registered definition prefix")
    for row in rows:
        if row.get("technical_status") == "passed":
            manifest_path = ensure_within(Path(row["feature_manifest"]), run.root.parent / "library_cache")
            if file_hash(manifest_path) != row.get("feature_manifest_sha256"):
                raise IntegrityError("Committed library feature manifest changed")
            manifest = read_object(manifest_path)
            verify_files(manifest_path.parent, manifest["files"])
    return rows, decay, {"path": str(path), "sha256": file_hash(path), "recovered_definitions": len(rows)}


def screen_library(config, snapshot_path, output, *, run_id, numbers=None):
    snapshot = load_snapshot(snapshot_path)
    boundary = pd.Timestamp(config.validation.holdout_start)
    if snapshot.manifest["spec"]["holdout_start"] != config.validation.holdout_start:
        raise ConfigurationError("Library snapshot and configured research boundaries differ")
    if snapshot.manifest["universe_policy"] != config.universe.model_dump(mode="json"):
        raise ConfigurationError("Library screening universe differs from frozen snapshot")
    definitions = catalog()["definitions"]
    numbers = [row["number"] for row in definitions] if numbers is None else list(numbers)
    if (not numbers or len(numbers) > 20 or
            any(type(n) is not int or n not in {row["number"] for row in definitions} for n in numbers)
            or len(set(numbers)) != len(numbers)):
        raise ConfigurationError("Screen batch needs 1..20 unique registered integer formula IDs")
    definitions = [row for row in definitions if row["number"] in numbers]
    output = ensure_within(Path(output), config.artifact_root / "library_screening")
    if output.name != run_id or output.parent != (config.artifact_root / "library_screening").resolve():
        raise ConfigurationError("Library output must match its run_id directly under library_screening")
    identity = {"snapshot_id": snapshot.snapshot_id, "source_hash": content_hash(SOURCE),
                "definitions": [row["definition_hash"] for row in definitions], "screen_rule": SCREEN_RULE,
                "configuration": config.model_dump(mode="json"), "code_hash": code_hash(),
                "environment": environment_manifest(), "snapshot_manifest_hash": file_hash(snapshot.path / "snapshot_manifest.json")}
    with RunStore(output.parent, output.name, identity) as run:
        if run.reused:
            return read_object(run.path / "screening_report.json")
        # Publish the selection rule before computing any candidate or label.
        atomic_json(run.path / "screening_manifest.json", {"schema_version": "library-screening-manifest-v1", **identity})
        atomic_json(run.path / "source_manifest.json", SOURCE)
        atomic_json(run.path / "factor_catalog.json", {**catalog(), "definitions": definitions})
        progress = Progress(run.path, run_id, "library_screening")
        rows, decay_rows, recovered = _resume_screening(run, definitions)
        if recovered:
            progress.emit("checkpoint_recovered", **recovered)
        panel_path = snapshot.path / "research" / "panel.parquet"
        panel = pd.read_parquet(panel_path)
        require_panel(panel)
        if panel.empty or (panel.index.get_level_values("datetime") >= boundary).any():
            raise QualityError("Library screening may read only nonempty research data")
        calendar = read_calendar(snapshot.path / "calendar.txt")
        calendar = calendar[calendar < boundary]
        universe = pd.read_parquet(snapshot.path / "universe.parquet",
            filters=[("datetime", "<", boundary)]).reindex(panel.index)
        eligible = universe.eligible
        if eligible.isna().any() or not pd.api.types.is_bool_dtype(eligible):
            raise QualityError("Library eligibility is incomplete")
        baseline = materialize({"snapshot_id": snapshot.snapshot_id}, panel)
        labels, events = generate_labels(panel, calendar, config.label)
        splits = training_splits(panel, calendar, labels, events, eligible, config.validation)
        horizon_targets = {h: generate_labels(panel, calendar, config.label.model_copy(update={"horizon": h}))
                           for h in SCREEN_RULE["horizons"]}
        inputs = {"snapshot_id": snapshot.snapshot_id, "panel_sha256": file_hash(panel_path),
                  "universe_sha256": file_hash(snapshot.path / "universe.parquet"),
                  "universe_policy": config.universe.model_dump(mode="json"), "research_end": str(calendar[-1])}
        baseline_predictions = {}
        for split in splits:
            fit, valid = split["fit"], split["validate"]
            model = _model().fit(baseline.frame[fit], labels[fit])
            baseline_predictions[split["fold"]] = pd.Series(model.predict(baseline.frame[valid]), index=panel.index[valid])
        for definition in definitions[len(rows):]:
            progress.emit("factor_started", factor_id=definition["factor_id"], completed=len(rows), total=len(definitions))
            row = {"factor_id": definition["factor_id"], "definition_hash": definition["definition_hash"],
                   "formal_status": "not_run", "investment_readiness": "not_ready", "by_fold": []}
            try:
                frame, manifest, timing = materialize_library_factor(panel, eligible, definition, inputs=inputs,
                    root=config.artifact_root / "library_cache", coverage_threshold=config.research.coverage_threshold)
                row.update(technical_status="passed", cache=timing, feature_manifest=manifest["path"] + "/feature_manifest.json",
                           feature_manifest_sha256=file_hash(Path(manifest["path"]) / "feature_manifest.json"),
                           quality=manifest["quality"])
                features = pd.concat([baseline.frame, frame], axis=1)
                for split in splits:
                    fit, valid = split["fit"], split["validate"]
                    model = _model().fit(features[fit], labels[fit])
                    scores = pd.Series(model.predict(features[valid]), index=panel.index[valid])
                    before, after = _score(baseline_predictions[split["fold"]], labels[valid]), _score(scores, labels[valid])
                    training_signal = _score(frame.iloc[:, 0][fit], labels[fit])
                    orientation = select_factor_orientation(training_signal)
                    orientation["training_window"] = split["training_window"]
                    raw_signal = _score(frame.iloc[:, 0][valid], labels[valid])
                    oriented_signal = (_score(frame.iloc[:, 0][valid] * orientation["sign"], labels[valid])
                                       if orientation["sign"] is not None else
                                       {"status": "unknown", "reason": "training_ic_ir_direction_unavailable"})
                    factor_signal = {"training_orientation": orientation,
                                     "raw_validation": raw_signal,
                                     "oriented_validation": oriented_signal,
                                     "oriented_validation_gate": positive_icir_gate(oriented_signal)}
                    delta = (after["rank_ic"] - before["rank_ic"]
                             if after["rank_ic"] is not None and before["rank_ic"] is not None else None)
                    row["by_fold"].append({"fold": split["fold"], "baseline": before, "candidate": after,
                                          "factor_signal": factor_signal,
                                          "incremental_rank_ic": delta, **{k: v for k, v in split.items() if k not in {"fit", "validate", "fold"}}})
                    common = valid.copy()
                    for target, target_events in horizon_targets.values():
                        common &= target.notna() & (target_events.available_time <= pd.Timestamp(split["validation_as_of"]) + pd.Timedelta(hours=16))
                    for horizon, (target, _) in horizon_targets.items():
                        decay_rows.append({"factor_id": definition["factor_id"], "fold": split["fold"], "horizon": horizon,
                                           "common_sample_rows": int(common.sum()), "sample_hash": _index_hash(panel.index[common]),
                                           **_score(frame.iloc[:, 0][common], target[common])})
                signal_gate = summarize_icir_gates([
                    item["factor_signal"]["oriented_validation_gate"] for item in row["by_fold"]])
                row["factor_signal_gate"] = signal_gate
                deltas = [item["incremental_rank_ic"] for item in row["by_fold"]]
                if any(d is None for d in deltas) or signal_gate["status"] == "unknown":
                    row.update(screen_status="inconclusive", reason_codes=(
                        ["incremental_metric_unavailable"] if any(d is None for d in deltas) else []) + (
                        ["factor_icir_evidence_inconclusive"] if signal_gate["status"] == "unknown" else []))
                else:
                    median = statistics.median(deltas)
                    positive = sum(d > 0 for d in deltas)
                    increment_passed = median > 0 and positive > len(deltas) / 2
                    passed = increment_passed and signal_gate["status"] == "passed"
                    row.update(screen_status="eligible_shortlist" if passed else "screen_rejected",
                               median_incremental_rank_ic=median, positive_inner_folds=positive,
                               reason_codes=(["inner_increment_confirmed"] if passed else
                                   ([] if increment_passed else ["no_consistent_inner_increment"]) +
                                   ([] if signal_gate["status"] == "passed" else ["factor_icir_insufficient_passing_folds"])))
            except QualityError as exc:
                row.update(technical_status="failed", screen_status="not_scored", reason_codes=[str(exc)])
            rows.append(row)
            checkpoint = {"schema_version": "library-screening-checkpoint-v1", "identity": run.identity,
                          "completed_definitions": rows, "horizon_rows": decay_rows}
            atomic_json(run.path / "screening_checkpoint.json", {**checkpoint, "checkpoint_hash": content_hash(checkpoint)})
        ranked = sorted((row for row in rows if row["screen_status"] == "eligible_shortlist"),
                        key=lambda row: (-row["median_incremental_rank_ic"], row["definition_hash"]))
        definitions_by_hash = {row["definition_hash"]: row for row in catalog()["definitions"]}
        selected = _diverse_shortlist(ranked, definitions_by_hash)
        selected_hashes = {row["definition_hash"] for row in selected}
        selected_slots = {row["definition_hash"]: row["mechanism_slot"] for row in selected}
        for row in rows:
            if row["screen_status"] == "eligible_shortlist":
                if row["definition_hash"] in selected_hashes:
                    row["screen_status"] = "shortlisted"
                    row["mechanism_slot"] = selected_slots[row["definition_hash"]]
                else:
                    row["screen_status"] = "deferred_mechanism_quota"
        factor_values = {}
        for row in rows:
            if row.get("technical_status") != "passed":
                continue
            manifest_path = ensure_within(Path(row["feature_manifest"]), config.artifact_root / "library_cache")
            manifest = read_object(manifest_path)
            verify_files(manifest_path.parent, manifest["files"])
            factor_values[row["factor_id"]] = pd.read_parquet(
                manifest_path.parent / "result.parquet").iloc[:, 0]
        correlations = factor_correlation_diagnostics(factor_values, splits)
        atomic_json(run.path / "correlation_diagnostics.json", correlations)
        shortlist = {"schema_version": "library-shortlist-v1", "formal_acceptance": False,
                     "selected": [{"factor_id": row["factor_id"], "definition_hash": row["definition_hash"],
                                   "mechanism_slot": row["mechanism_slot"],
                                   "reason_codes": row["reason_codes"]} for row in selected]}
        report = {"schema_version": "library-screening-v1", "status": "completed", "run_id": run_id,
                  "snapshot_id": snapshot.snapshot_id, "baseline_feature_set_id": baseline.feature_set_id,
                  "attempted_definitions": len(rows), "by_factor": rows, "shortlist_count": len(selected),
                  "external_calls": 0, "formal_evaluations": 0, "holdout_evaluated": False,
                  "recovery": recovered,
                  "correlation_diagnostics": {"path": str(run.path / "correlation_diagnostics.json"),
                      "sha256": file_hash(run.path / "correlation_diagnostics.json"),
                      "cluster_count": len(correlations["clusters"]),
                      "effect": "diagnostic_only_no_candidate_exclusion"},
                  "model_scope": "fixed Ridge incremental proxy inside training segments only",
                  "search_bias": "20-or-fewer correlated trials; shortlist is not independent confirmation"}
        atomic_json(run.path / "horizon_diagnostics.json", {"status": "completed", "by_factor_fold_horizon": decay_rows,
                                                           "selection_horizon_changed": False})
        atomic_json(run.path / "exposure_diagnostics.json", exposure_diagnostics(panel, universe,
            pit_verified=snapshot.manifest.get("qualification", {}).get("historical_membership_verified") is True))
        atomic_json(run.path / "shortlist.json", shortlist)
        atomic_json(run.path / "screening_report.json", report)
        progress.emit("finished", status="completed", attempted_definitions=len(rows), shortlist_count=len(selected))
        run.complete({"screening_report": str(run.path / "screening_report.json"),
                      "attempted_definitions": len(rows), "shortlist_count": len(selected), "external_calls": 0})
        return report


def evaluate_library_shortlist(config, snapshot_path, screening_run, output, *, campaign_id,
                               campaign_max_trials=5):
    """Register the top frozen shortlist entries under a finite attempt ledger."""
    from types import SimpleNamespace

    from etf_ml.artifacts import RunStore
    from etf_ml.data.snapshot import load_snapshot
    from etf_ml.errors import BudgetError
    from etf_ml.features.baseline import materialize
    from etf_ml.research.campaign import CampaignLedger
    from etf_ml.research.context import FactorSpec
    from etf_ml.research.protocol import ComparisonProtocol
    from etf_ml.research.session import ResearchSession
    from etf_ml.adapters.rdagent.experiment import ETFExperiment, ETFFactorTask, FactorProposal
    from etf_ml.adapters.rdagent.runner import ETFFactorRunner

    snapshot = load_snapshot(snapshot_path)
    if config.research.budget_mode != "unlimited":
        raise ConfigurationError("Formal library campaign must use the frozen unlimited monetary policy")
    if (not isinstance(campaign_max_trials, int) or isinstance(campaign_max_trials, bool)
            or campaign_max_trials <= 0 or campaign_max_trials > 5):
        raise ConfigurationError("Formal library campaign cap must be between one and five")
    screening_run = ensure_within(Path(screening_run), config.artifact_root / "library_screening")
    screen_status = read_object(screening_run / "status.json")
    screen_manifest = read_object(screening_run / "manifest.json")
    if screen_status.get("status") != "completed":
        raise ConfigurationError("Only a completed, hash-verified library screen can enter formal evaluation")
    verify_files(screening_run, screen_manifest["files"])
    screen_identity = read_object(screening_run / "screening_manifest.json")
    screen_report = read_object(screening_run / "screening_report.json")
    shortlist = read_object(screening_run / "shortlist.json")
    if (screen_report.get("snapshot_id") != snapshot.snapshot_id
            or screen_report.get("status") != "completed"
            or screen_identity.get("source_hash") != content_hash(SOURCE)
            or screen_identity.get("snapshot_manifest_hash") != file_hash(snapshot.path / "snapshot_manifest.json")
            or shortlist.get("formal_acceptance") is not False
            or shortlist.get("schema_version") != "library-shortlist-v1"):
        raise ConfigurationError("Shortlist evidence does not match the selected snapshot or schema")
    screened_hashes = set(screen_identity.get("definitions", []))
    selected = shortlist.get("selected")
    if not isinstance(selected, list) or len(selected) > 5:
        raise ConfigurationError("Formal shortlist is capped at five registered candidates")
    deferred_by_campaign_cap = selected[campaign_max_trials:]
    selected = selected[:campaign_max_trials]
    by_factor = {row.get("factor_id"): row for row in screen_report.get("by_factor", [])}
    definitions_by_hash = {row["definition_hash"]: row for row in catalog()["definitions"]}
    selected_slots = set()
    for row in selected:
        screen_row = by_factor.get(row.get("factor_id"))
        definition = definitions_by_hash.get(row.get("definition_hash"))
        expected_slot = SCREEN_RULE["mechanism_slots"].get(
            definition.get("research_group")) if definition else None
        if (not screen_row or screen_row.get("screen_status") != "shortlisted"
                or screen_row.get("definition_hash") != row["definition_hash"]
                or row["definition_hash"] not in screened_hashes
                or definition is None or screen_row.get("technical_status") != "passed"
                or expected_slot is None or row.get("mechanism_slot") != expected_slot
                or screen_row.get("mechanism_slot") != expected_slot or expected_slot in selected_slots):
            raise IntegrityError("Shortlist entry differs from the completed screening report")
        selected_slots.add(expected_slot)

    primary = [model for model in config.models if model.name == "lightgbm"]
    if len(primary) != 1:
        raise ConfigurationError("Library formal evaluation requires one frozen LightGBM model")
    panel = pd.read_parquet(snapshot.path / "research" / "panel.parquet")
    if panel.empty or (panel.index.get_level_values("datetime") >= pd.Timestamp(config.validation.holdout_start)).any():
        raise QualityError("Formal library evaluation may use only the research panel")
    baseline = materialize({"snapshot_id": snapshot.snapshot_id}, panel)
    if baseline.feature_set_id != screen_report.get("baseline_feature_set_id"):
        raise IntegrityError("Screening baseline differs from the formal paired baseline")
    protocol = ComparisonProtocol(snapshot_id=snapshot.snapshot_id,
        baseline_feature_set_id=baseline.feature_set_id, label=config.label,
        universe=config.universe, validation=config.validation, portfolio=config.portfolio,
        research=config.research, model=primary[0], benchmarks=config.benchmarks,
        stress_min_excess_return=config.research.stress_min_excess_return)
    if protocol.source_code_hash != code_hash():
        raise IntegrityError("Formal library protocol must bind the current source tree")

    output = ensure_within(Path(output), config.artifact_root / "library_evaluations")
    if output.name != campaign_id or output.parent != (config.artifact_root / "library_evaluations").resolve():
        raise ConfigurationError("Formal library output must match its campaign id")
    identity = {"snapshot_id": snapshot.snapshot_id, "screening_manifest_sha256": file_hash(screening_run / "manifest.json"),
                "shortlist_sha256": file_hash(screening_run / "shortlist.json"),
                "protocol_id": protocol.protocol_id, "campaign_id": campaign_id,
                "campaign_attempt_cap": campaign_max_trials, "external_calls_allowed": False,
                "source_code_hash": code_hash(), "environment": environment_manifest()}
    with RunStore(output.parent, output.name, identity) as run:
        if run.reused:
            return read_object(run.path / "formal_evaluation_report.json")
        progress = Progress(run.path, campaign_id, "library_formal_evaluation")
        atomic_json(run.path / "formal_evaluation_manifest.json", {"schema_version": "library-formal-evaluation-v2",
                    **identity, "shortlist_count": len(selected), "holdout_read": False})
        ledger = CampaignLedger(config.artifact_root / "research_campaigns", campaign_id,
                                campaign_max_trials,
                                config.research.max_campaign_wall_seconds)
        ledger.initialize()
        _, campaign_events = ledger.audit_view()
        committed_trials = {event["trial_key"]: event for event in campaign_events
                            if event.get("event_type") == "trial_committed"}
        session = ResearchSession(config, snapshot.path, protocol, root=run.path / "research", llm=object())
        if session.baseline.feature_set_id != baseline.feature_set_id:
            raise IntegrityError("Research session rebuilt a different baseline")
        session.context.runtime["trusted_library_source_hash"] = content_hash(SOURCE)
        session.attempted_trials = screen_report["attempted_definitions"] + len(selected)
        runner = ETFFactorRunner(SimpleNamespace(session=session))
        rows = []
        for trial_index, selected_row in enumerate(selected):
            definition = definitions_by_hash[selected_row["definition_hash"]]
            spec = registered_factor_spec(definition, session.context)
            trial_id = "alpha101-" + content_hash({"campaign": campaign_id,
                "protocol_id": protocol.protocol_id, "factor_version_id": spec.version_id})[:24]
            progress.emit("candidate_started", factor_id=spec.factor_id, trial_index=trial_index,
                          total=len(selected), attempt_cap=campaign_max_trials)
            prior = committed_trials.get(f"{trial_id}:0")
            if prior is not None:
                prior_evaluation_id = prior.get("evaluation_id")
                prior_paths = sorted(run.path.glob(
                    f"attempt-*/research/paired/runs/{trial_id}-{spec.factor_id[:24]}/evaluation.json"))
                prior_path = next((path for path in prior_paths
                                   if prior_evaluation_id and file_hash(path) == prior_evaluation_id), None)
                if prior_evaluation_id and prior_path is None:
                    raise IntegrityError("Committed campaign evaluation artifact is missing or changed")
                rows.append({"factor_id": spec.factor_id, "factor_version_id": spec.version_id,
                    "screening_status": "shortlisted", "formal_status": prior.get("outcome", "failed"),
                    "evaluation_id": prior_evaluation_id,
                    "evaluation_report": str(prior_path) if prior_path else None,
                    "failure_stage": None if prior_path else "previously_committed_without_evaluation_artifact",
                    "reasons": [], "external_calls": 0, "holdout_evaluated": False,
                    "reused_committed_attempt": True})
                progress.emit("candidate_reused", factor_id=spec.factor_id, trial_index=trial_index,
                              status=prior.get("outcome", "failed"), evaluation_id=prior_evaluation_id)
                continue
            if not ledger.begin_trial(run_id=trial_id, trial_index=0,
                    compatibility_group_id=protocol.protocol_id, protocol_id=protocol.protocol_id):
                raise BudgetError("Formal library campaign attempt cap is exhausted")
            ledger.record_proposal(run_id=trial_id, trial_index=0, proposal_index=0,
                    definition_id=spec.version_id, research_group=spec.research_group,
                    compatibility_group_id=protocol.protocol_id, protocol_id=protocol.protocol_id)
            proposal = FactorProposal.model_validate(spec.model_dump(mode="json"))
            task = ETFFactorTask(proposal)
            experiment = ETFExperiment(session, [task], experiment_id=trial_id)
            workspace = experiment.sub_workspace_list[0]
            try:
                workspace.execute()
                runner.develop(experiment)
                result = experiment.result["by_candidate"][0]
            except Exception as exc:
                result = {"factor_id": spec.factor_id, "factor_version_id": spec.version_id,
                          "status": "failed", "failure_stage": "trusted_library_evaluation",
                          "reasons": [getattr(exc, "reason", None) or str(exc) or type(exc).__name__]}
            evaluation_path = session.root / "paired" / "runs" / (trial_id + "-" + spec.factor_id[:24]) / "evaluation.json"
            evaluation_id = file_hash(evaluation_path) if evaluation_path.is_file() else None
            outcome = result.get("status", "failed")
            rows.append({"factor_id": spec.factor_id, "factor_version_id": spec.version_id,
                         "screening_status": "shortlisted", "formal_status": outcome,
                         "evaluation_id": evaluation_id,
                         "evaluation_report": str(evaluation_path) if evaluation_id else None,
                         "failure_stage": result.get("failure_stage"), "reasons": result.get("reasons", []),
                         "external_calls": 0, "holdout_evaluated": False})
            ledger.commit_trial(run_id=trial_id, trial_index=0,
                compatibility_group_id=protocol.protocol_id, protocol_id=protocol.protocol_id,
                record={"result": {"status": outcome},
                        "research_card": {"definition_id": spec.version_id},
                        "campaign_candidates": [{"definition_id": spec.version_id,
                            "evaluation_id": evaluation_id, "outcome": outcome}]})
            progress.emit("candidate_completed", factor_id=spec.factor_id, trial_index=trial_index,
                          status=outcome, evaluation_id=evaluation_id)
        campaign = ledger.summary()
        report = {"schema_version": "library-formal-evaluation-v2", "status": "completed",
                  "run_id": campaign_id, "snapshot_id": snapshot.snapshot_id,
                  "label_horizon": protocol.label.horizon,
                  "protocol_id": protocol.protocol_id, "screening_run_id": screen_report["run_id"],
                  "attempted_formal_candidates": campaign["campaign_attempted_trials"],
                  "campaign_max_attempts": campaign["max_attempts"],
                  "campaign_summary": campaign, "by_factor": rows,
                  "deferred_by_campaign_cap": deferred_by_campaign_cap,
                  "external_calls": 0, "provider_cost": "not_applicable_no_provider_dispatch",
                  "holdout_evaluated": False, "investment_readiness": "not_ready"}
        atomic_json(run.path / "formal_evaluation_report.json", report)
        progress.emit("finished", status="completed", attempted_candidates=len(rows))
        run.complete({"formal_evaluation_report": str(run.path / "formal_evaluation_report.json"),
                      "attempted_formal_candidates": len(rows), "external_calls": 0})
        return report


def compare_library_horizons(config, snapshot_path, screening_run, *, run_id):
    """Run the frozen shortlist under four separately identified label protocols.

    Results remain development-only: the report deliberately selects no winning
    horizon, and every horizon gets its own protocol, baseline, and five-slot ledger.
    """
    if not RUN_ID.fullmatch(run_id):
        raise ConfigurationError("Invalid horizon-comparison run id")
    if config.research.budget_mode != "unlimited":
        raise ConfigurationError("Horizon comparison must preserve the frozen unlimited monetary policy")
    snapshot = load_snapshot(snapshot_path)
    screening_run = ensure_within(Path(screening_run), config.artifact_root / "library_screening")
    screening_status = read_object(screening_run / "status.json")
    screening_manifest = read_object(screening_run / "manifest.json")
    if screening_status.get("status") != "completed":
        raise ConfigurationError("Horizon comparison requires a completed library screen")
    verify_files(screening_run, screening_manifest["files"])
    screen_identity = read_object(screening_run / "screening_manifest.json")
    screen_report = read_object(screening_run / "screening_report.json")
    shortlist = read_object(screening_run / "shortlist.json")
    selected = shortlist.get("selected")
    if (screen_report.get("status") != "completed" or screen_report.get("snapshot_id") != snapshot.snapshot_id
            or screen_identity.get("source_hash") != content_hash(SOURCE)
            or screen_identity.get("snapshot_manifest_hash") != file_hash(snapshot.path / "snapshot_manifest.json")
            or shortlist.get("schema_version") != "library-shortlist-v1"
            or shortlist.get("formal_acceptance") is not False
            or not isinstance(selected, list) or not 1 <= len(selected) <= 5
            or any(not isinstance(row, dict) for row in selected)):
        raise IntegrityError("Horizon comparison shortlist is incomplete or has a different source identity")
    definition_hashes = [row.get("definition_hash") for row in selected]
    if (any(not isinstance(value, str) for value in definition_hashes)
            or len(set(definition_hashes)) != len(definition_hashes)
            or not set(definition_hashes).issubset(set(screen_identity.get("definitions", [])))):
        raise IntegrityError("Horizon comparison shortlist definitions differ from its frozen screen")

    horizons = [1, 5, 10, 20]
    identity = {"schema_version": "library-horizon-comparison-v1", "run_id": run_id,
        "snapshot_id": snapshot.snapshot_id, "snapshot_manifest_hash": file_hash(snapshot.path / "snapshot_manifest.json"),
        "screening_manifest_hash": file_hash(screening_run / "manifest.json"),
        "shortlist_hash": file_hash(screening_run / "shortlist.json"),
        "definition_hashes": definition_hashes, "horizons": horizons,
        "base_config": config.model_dump(mode="json"), "source_code_hash": code_hash(),
        "environment": environment_manifest(), "external_calls_allowed": False}
    output_root = config.artifact_root / "library_horizon_comparisons"

    def verify_completed_report(report):
        if (report.get("schema_version") != "library-horizon-comparison-v1"
                or report.get("status") != "completed" or report.get("snapshot_id") != snapshot.snapshot_id
                or [item.get("horizon") for item in report.get("by_horizon", [])] != horizons
                or len({item.get("protocol_id") for item in report.get("by_horizon", [])}) != len(horizons)
                or report.get("definition_hashes") != definition_hashes
                or report.get("campaign_count") != 4 or report.get("per_campaign_trial_cap") != 5
                or report.get("total_formal_trial_cap") != 20
                or report.get("holdout_read") is not False or report.get("external_calls") != 0
                or report.get("horizon_selection") != "none_development_results_are_not_independent_confirmation"):
            raise IntegrityError("Completed horizon comparison report violates its frozen contract")
        for item in report["by_horizon"]:
            path = ensure_within(Path(item["report_path"]), config.artifact_root / "library_evaluations")
            if file_hash(path) != item.get("report_sha256"):
                raise IntegrityError("Referenced horizon evaluation report changed")
            campaign = read_object(path)
            if (campaign.get("schema_version") != "library-formal-evaluation-v2"
                    or campaign.get("run_id") != item.get("campaign_id")
                    or campaign.get("label_horizon") != item.get("horizon")
                    or campaign.get("protocol_id") != item.get("protocol_id")
                    or campaign.get("snapshot_id") != snapshot.snapshot_id
                    or campaign.get("holdout_evaluated") is not False
                    or campaign.get("external_calls") != 0
                    or len(campaign.get("by_factor", [])) != len(definition_hashes)):
                raise IntegrityError("Referenced horizon evaluation identity changed")

    with RunStore(output_root, run_id, identity) as run:
        if run.reused:
            report = read_object(run.path / "horizon_comparison_report.json")
            verify_completed_report(report)
            return report
        by_horizon = []
        for horizon in horizons:
            horizon_config = config.model_copy(deep=True)
            horizon_config.label.horizon = horizon
            suffix = f"-h{horizon}"
            campaign_id = run_id + suffix
            if len(campaign_id) > 101:
                campaign_id = run_id[:88] + "-" + content_hash(run_id)[:8] + suffix
            campaign = evaluate_library_shortlist(horizon_config, snapshot.path, screening_run,
                config.artifact_root / "library_evaluations" / campaign_id, campaign_id=campaign_id)
            if (campaign.get("schema_version") != "library-formal-evaluation-v2"
                    or campaign.get("label_horizon") != horizon
                    or campaign.get("snapshot_id") != snapshot.snapshot_id
                    or campaign.get("holdout_evaluated") is not False
                    or campaign.get("external_calls") != 0 or campaign.get("campaign_max_attempts") != 5
                    or type(campaign.get("attempted_formal_candidates")) is not int
                    or campaign["attempted_formal_candidates"] != len(selected)
                    or not 0 <= campaign["attempted_formal_candidates"] <= 5
                    or not campaign.get("protocol_id")
                    or [row.get("factor_id") for row in campaign.get("by_factor", [])]
                       != [row.get("factor_id") for row in selected]):
                raise IntegrityError("Horizon campaign violated the frozen development-only contract")
            if any(item.get("formal_status") not in {"accepted", "rejected", "inconclusive", "failed"}
                   for item in campaign.get("by_factor", [])):
                raise IntegrityError("Horizon campaign contains an invalid candidate status")
            report_path = config.artifact_root / "library_evaluations" / campaign_id / "formal_evaluation_report.json"
            by_horizon.append({"horizon": horizon, "campaign_id": campaign_id,
                "protocol_id": campaign["protocol_id"], "campaign_status": campaign["status"],
                "attempted_formal_candidates": campaign["attempted_formal_candidates"],
                "by_factor": campaign["by_factor"], "report_path": str(report_path),
                "report_sha256": file_hash(report_path)})
            atomic_json(run.path / "horizon_comparison_progress.json", {
                **identity, "completed_horizons": by_horizon})
        if len({row["protocol_id"] for row in by_horizon}) != len(horizons):
            raise IntegrityError("Label horizons did not produce distinct frozen protocols")
        report = {"schema_version": "library-horizon-comparison-v1", "status": "completed",
            "snapshot_id": snapshot.snapshot_id, "screening_run_id": screen_report["run_id"],
            "screening_manifest_hash": identity["screening_manifest_hash"],
            "definition_hashes": definition_hashes, "campaign_count": 4,
            "per_campaign_trial_cap": 5, "total_formal_trial_cap": 20,
            "by_horizon": by_horizon,
            "horizon_selection": "none_development_results_are_not_independent_confirmation",
            "external_calls": 0, "holdout_read": False, "investment_readiness": "not_ready",
            "note": "Each horizon has its own paired baseline and protocol; compare all results, do not select a winner from this development set."}
        verify_completed_report(report)
        atomic_json(run.path / "horizon_comparison_report.json", report)
        run.complete({"report": str(run.path / "horizon_comparison_report.json"),
                      "horizons": horizons, "external_calls": 0, "holdout_read": False})
        return report
