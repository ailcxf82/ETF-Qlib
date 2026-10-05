"""Read-only research preparation checks; never independent acceptance."""
from __future__ import annotations

from pathlib import Path

from etf_ml.contracts import AppConfig
from etf_ml.errors import ConfigurationError, ETFError
from etf_ml.utils import filesystem_path


def _source_presence(config, block):
    spec = config.data
    definition = spec.source / "instruments" / "all.txt"
    count = 0
    missing = {}
    if not definition.is_file():
        block("missing_source_instruments", "Provide the existing ETF provider.")
    else:
        instruments = []
        for line in definition.read_text(encoding="utf-8-sig").splitlines():
            parts = line.split("\t")
            instrument = parts[0] if parts else ""
            if len(parts) != 3 or not instrument or not all(c.isalnum() or c == "." for c in instrument):
                block("invalid_source_instruments", "Repair instrument definitions before reading bins.")
                break
            instruments.append(instrument)
        else:
            count = len(instruments)
            if not count or len(set(instruments)) != count:
                block("invalid_source_instruments", "Provide nonempty unique instrument definitions.")
            for name in ("open", "high", "low", "close", "volume", "amount", "change", "factor", "reference_close"):
                if name == "factor" and spec.adjustment_path is not None:
                    if not spec.adjustment_path.is_file():
                        block("missing_adjustment_path", "Provide the declared real adjustment supplement.")
                    continue
                field = spec.fields.get(name)
                if not field or not field.replace("_", "").isalnum():
                    block("invalid_required_field_mapping", "Declare a safe required field mapping.", field=name)
                    continue
                absent = sum(not (spec.source / "features" / instrument.lower() / (field + ".day.bin")).is_file()
                             for instrument in instruments)
                if absent:
                    missing[name] = absent
            if missing:
                block("missing_required_bins", "Import missing real fields; do not fill adjustment factors with one.", counts=missing)
    if not (spec.source / "calendars" / "day.txt").is_file():
        block("missing_source_calendar", "Provide the original bin calendar.")
    for name in ("trusted_calendar", "metadata_path", "events_path", "benchmark_path"):
        path = getattr(spec, name)
        if path is None or not Path(path).is_file():
            block("missing_" + name, "Provide the real source sidecar or explicit event coverage evidence.")
    for name in ("limits_path", "dividend_review_path", "share_events_path", "source_revisions_path", "income_path"):
        path = getattr(spec, name)
        if path is not None and not filesystem_path(path).is_file():
            block("missing_" + name, "Provide the declared source supplement or review evidence.")
    if not spec.point_in_time_metadata:
        block("unverified_historical_metadata", "Reconstruct known-at-time membership; current classification cannot be backdated.")
    for name in ("volume_unit", "amount_multiplier", "price_mode", "change_unit"):
        if getattr(spec, name) is None:
            block("unresolved_field_semantics", "Reuse verified Tushare export semantics.", field=name)
    return count


def _frozen_snapshot(config, path):
    import pandas as pd
    from etf_ml.data.snapshot import load_snapshot
    from etf_ml.data.diagnostic import require_snapshot_mode
    from etf_ml.research.qualification import data_qualification, evidence

    snapshot = load_snapshot(path)  # Hashes bytes, including holdout files; never decodes holdout values.
    require_snapshot_mode(config, snapshot)
    if (snapshot.manifest["spec"] != config.data.model_dump(mode="json") or
            snapshot.manifest["universe_policy"] != config.universe.model_dump(mode="json")):
        raise ConfigurationError("Frozen snapshot specification or universe differs")
    required = {"research/panel.parquet", "research/daily_pv.h5", "data_quality.json",
                "calendar.txt", "execution_calendar.txt", "metadata.parquet", "universe.parquet",
                "benchmark.parquet", "events.parquet"}
    if not required <= snapshot.manifest["files"].keys():
        raise ConfigurationError("Frozen snapshot lacks required research files")
    qualification = data_qualification(snapshot)
    # Source-completeness uncertainty already limits this project to research.
    # It cannot waive structural/PIT failures or an explicitly failed completeness check.
    if any(value != "passed" and (value == "failed" or not key.endswith("source_completeness"))
           for key, value in qualification["checks"].items()):
        raise ConfigurationError("Frozen snapshot does not satisfy structural research checks")
    index = pd.read_parquet(snapshot.path / "research/panel.parquet", columns=[]).index
    if (not isinstance(index, pd.MultiIndex) or index.names != ["datetime", "instrument"] or
            index.empty or not index.is_unique):
        raise ConfigurationError("Frozen research index is empty or invalid")
    dates = pd.DatetimeIndex(index.get_level_values("datetime"))
    if dates.hasnans or (dates >= pd.Timestamp(config.validation.holdout_start)).any():
        raise ConfigurationError("Frozen research index contains invalid or holdout dates")
    return snapshot, {"status": "verified_frozen_research", "mode": "frozen_snapshot",
        "snapshot_id": snapshot.snapshot_id, "manifest": evidence(snapshot.path / "snapshot_manifest.json"),
        "instrument_count": index.get_level_values("instrument").nunique(),
        "data_qualification": qualification["status"],
        "qualification_reasons": qualification["reason_codes"],
        "raw_source_required_for_this_run": False, "holdout_values_decoded": False}


def first_loop_readiness(config: AppConfig, *, campaign_id=None, campaign_max_trials=None,
                         snapshot_path=None, baseline_root=None, check_runtime=False,
                         mechanism_plan=None) -> dict:
    blockers = []
    campaign_bounds_not_checked = campaign_id is None and campaign_max_trials is None
    campaign_validated, campaign_observation = False, None

    def block(code, action, **details):
        blockers.append({"priority": "P0", "code": code, "action": action, **details})

    snapshot = None
    data_route = {"mode": "build_from_source", "status": "presence_only"}
    if snapshot_path is None:
        count = _source_presence(config, block)
    else:
        count = None
        data_route = {"mode": "frozen_snapshot", "status": "blocked"}
        try:
            snapshot, data_route = _frozen_snapshot(config, snapshot_path)
            count = data_route["instrument_count"]
        except (ETFError, OSError, ValueError, KeyError, TypeError) as exc:
            block("invalid_frozen_snapshot", "Restore the exact verified snapshot; do not substitute synthetic data.",
                  reason=str(exc))
    baseline = {"status": "build_new", "action": "Build a baseline under this configuration and runtime identity."}
    if baseline_root is not None:
        baseline = {"status": "blocked"}
        if snapshot is None:
            block("baseline_requires_snapshot", "Provide a verified snapshot with the reusable baseline.")
        else:
            try:
                from etf_ml.research.first_loop import load_reusable_baseline
                _, provenance = load_reusable_baseline(config, snapshot, baseline_root)
                baseline = {"status": "reusable", **provenance}
            except (ETFError, OSError, ValueError, KeyError, TypeError) as exc:
                block("incompatible_baseline", "Preserve the old baseline and build a new matched baseline.", reason=str(exc))
    unresolved = [name for name in ("k_mode", "minimum_commission", "liquidity_mode", "risk_mode")
                  if getattr(config.portfolio, name) is None]
    if unresolved:
        block("unresolved_portfolio", "Confirm parameter meanings and the minimum commission.", fields=unresolved)
    mode = config.research.budget_mode
    if mode is None:
        block("unresolved_research_budget", "Confirm whether no budget means zero paid calls or unlimited paid research.")
    elif mode != "unlimited":
        block("unsupported_real_transport_budget", "The current RDAgent transport has unknown billed cost; configure an authorized compatible transport.", budget_mode=mode)
    if not config.research.limits.image or "@sha256:" not in config.research.limits.image:
        block("missing_fixed_factor_image", "Configure the already-verified Docker image digest.")
    if not config.validation.folds:
        block("missing_development_folds", "Freeze development time folds before observing candidates.")
    if config.data.mode == "formal" and config.research.stress_min_excess_return is None:
        block("unresolved_stress_min_excess_return",
              "Predeclare the cost-stress excess-return threshold before dispatching formal research.")
    if not campaign_bounds_not_checked:
        if campaign_id is None or campaign_max_trials is None:
            block("incomplete_campaign_bounds", "Supply campaign id and finite max-trials cap together.")
        else:
            try:
                from etf_ml.research.campaign import CampaignLedger
                from etf_ml.research.campaign import FIVE_FACTOR_MECHANISM_PLAN
                plans = {"five_factor_v1": FIVE_FACTOR_MECHANISM_PLAN}
                if mechanism_plan not in (None, *plans):
                    raise ConfigurationError("Unknown campaign mechanism plan")
                ledger = CampaignLedger(config.artifact_root / "research_campaigns", campaign_id,
                                        campaign_max_trials,
                                        mechanism_plan=plans.get(mechanism_plan))
                campaign_validated = True
                if ledger.root.exists():
                    summary, _ = ledger.audit_view()
                    campaign_observation = summary
                    if summary["campaign_attempted_trials"] >= campaign_max_trials:
                        block("campaign_exhausted", "Use a separately authorized new finite campaign; never release old slots.")
                    if summary["wall_clock_expired"]:
                        block("campaign_expired", "Keep the expired campaign closed; a new campaign needs a new identity.")
                    if summary["max_duration_seconds"] != config.research.max_campaign_wall_seconds:
                        block("campaign_duration_mismatch", "Do not change the old campaign's immutable wall-clock policy.")
                else:
                    campaign_observation = {"status": "not_created", "campaign_attempted_trials": 0}
            except (ConfigurationError, TypeError) as exc:
                block("invalid_campaign_bounds", "Use a valid campaign id and positive finite attempt cap.",
                      reason=str(exc))
            except (ETFError, OSError, ValueError, KeyError) as exc:
                block("invalid_campaign_ledger", "Repair evidence integrity without resetting the campaign.", reason=str(exc))
    if config.data.holdout_start != config.validation.holdout_start:
        block("holdout_boundary_mismatch", "Align snapshot and validation holdout boundaries.")
    runtime = {"status": "not_checked"}
    if check_runtime:
        try:
            from etf_ml.runtime.docker import DockerBackend
            runtime = DockerBackend(config.artifact_root).preflight(config.research.limits.image)
        except ETFError as exc:
            runtime = {"status": "blocked"}
            block("runtime_unavailable", "Restore Docker Linux and the pinned local image before dispatch.", reason=str(exc))
    not_checked = ["provider_connectivity_and_billing", "environment_versions", "baseline_training_execution"]
    if snapshot is None:
        not_checked.extend(["bin_values_and_units", "calendar_and_announcement_asof",
                            "sidecar_contents_and_coverage", "corporate_action_adjustment_consistency"])
    else:
        not_checked.append("independent_source_completeness")
    if not check_runtime:
        not_checked.append("docker_runtime")
    if campaign_bounds_not_checked:
        not_checked.append("campaign_bounds")
    return {"status": "blocked" if blockers else "needs_full_validation",
            "purpose": "first_real_rdagent_qlib_loop", "scope": "research_only", "g0_passed": False,
            "instrument_count": count, "blockers": blockers,
            "data_route": data_route, "baseline": baseline, "runtime": runtime,
            "campaign_observation": campaign_observation,
            "deferred_stages": [{"stage": "independent_confirmation", "status": "not_run",
                "reason": "Requires an accepted frozen candidate, independent data and reviewed access history.",
                "blocks_research_preparation": False,
                "unresolved_acceptance_fields": [key for key, value in config.acceptance.model_dump().items()
                                                 if value is None]}],
            "investment_ready": False, "holdout_values_decoded": False,
            "campaign": ({"campaign_id": campaign_id, "max_attempts": campaign_max_trials,
                          **({"mechanism_plan": mechanism_plan} if mechanism_plan else {})}
                         if campaign_validated else None),
            "not_checked": not_checked,
            "next_steps": ["resolve_p0", "reuse_verified_snapshot" if snapshot else "build-data", "baseline",
                           "first-loop --campaign-id <id> --campaign-max-trials <n>"],
            "external_calls": 0, "training_runs": 0}
