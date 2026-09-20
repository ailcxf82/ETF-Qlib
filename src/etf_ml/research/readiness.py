"""Fast local first-loop preflight; never a substitute for G0 validation."""
from __future__ import annotations

from pathlib import Path

from etf_ml.contracts import AppConfig
from etf_ml.utils import filesystem_path


def first_loop_readiness(config: AppConfig) -> dict:
    blockers = []

    def block(code, action, **details):
        blockers.append({"priority": "P0", "code": code, "action": action, **details})

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
    if config.data.holdout_start != config.validation.holdout_start:
        block("holdout_boundary_mismatch", "Align snapshot and validation holdout boundaries.")
    return {"status": "blocked" if blockers else "needs_full_validation",
            "purpose": "first_real_rdagent_qlib_loop", "g0_passed": False,
            "instrument_count": count, "blockers": blockers,
            "not_checked": ["bin_values_and_units", "calendar_and_announcement_asof",
                            "sidecar_contents_and_coverage", "corporate_action_adjustment_consistency",
                            "provider_connectivity_and_billing", "docker_runtime", "environment_versions"],
            "next_steps": ["resolve_p0", "build-data", "baseline", "research-factor --max-trials 1"],
            "external_calls": 0, "training_runs": 0}
