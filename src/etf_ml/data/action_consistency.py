"""Reconcile adjustment changes with declared raw-share corporate actions."""
from __future__ import annotations

import numpy as np
import pandas as pd

from etf_ml.data.actions import adjust_mark, cash_per_ex_share, validate_events
from etf_ml.data.calendar import require_calendar
from etf_ml.data.source import require_panel
from etf_ml.errors import QualityError


def adjustment_event_consistency(panel, events, calendar, *, tolerance=5e-4, reference_close_tick=None):
    require_panel(panel)
    require_calendar(calendar)
    if isinstance(tolerance, bool) or not np.isfinite(tolerance) or not 0 < tolerance < 1:
        raise QualityError("Invalid adjustment consistency tolerance")
    if reference_close_tick is not None:
        if (isinstance(reference_close_tick, bool) or not np.isfinite(reference_close_tick)
                or not 0 < reference_close_tick <= 0.001):
            raise QualityError("Invalid reference close tick")
        if "reference_close" not in panel:
            raise QualityError("Reference close rounding policy requires source reference close")
    if not {"raw_close", "adjustment_factor"}.issubset(panel):
        raise QualityError("Adjustment consistency requires raw close and factor")
    if not panel.index.get_level_values("datetime").isin(calendar).all():
        raise QualityError("Adjustment panel outside its calendar")
    instruments = panel.index.get_level_values("instrument").unique()
    actions, _ = validate_events(events, calendar, instruments)
    groups = {(instrument, day): group for (instrument, day), group in
              actions.groupby(["instrument", "datetime"], sort=False)} if len(actions) else {}
    checked, changes, checked_events, maximum = 0, 0, 0, 0.
    mismatches, examples, checked_keys = {}, [], set()
    rounding_days, rounding_examples, effective_maximum = 0, [], 0.
    for instrument, data in panel.groupby(level="instrument", sort=True):
        data = data.droplevel("instrument").reindex(calendar)
        prices, factors = data.raw_close, data.adjustment_factor
        if any(not pd.api.types.is_numeric_dtype(values) or pd.api.types.is_bool_dtype(values)
               for values in (prices, factors)):
            raise QualityError("Raw close and adjustment factor must be numeric")
        valid = prices.notna()
        if ((valid & (~np.isfinite(prices) | prices.le(0) | ~np.isfinite(factors) | factors.le(0))).any()):
            raise QualityError("Invalid quoted raw close or adjustment factor")
        pairs = valid & valid.shift(fill_value=False)
        actual = factors / factors.shift()
        expected = pd.Series(1., index=calendar)
        source_expected = pd.Series(np.nan, index=calendar)
        if reference_close_tick is not None:
            references = data.reference_close
            if (not pd.api.types.is_numeric_dtype(references)
                    or pd.api.types.is_bool_dtype(references)):
                raise QualityError("Source reference close must be numeric")
        for day in calendar[pairs]:
            key = (instrument, day)
            if key not in groups:
                continue
            previous = float(prices.shift().loc[day])
            adjusted = previous
            for action in groups[key].sort_values("sequence").itertuples(index=False):
                adjusted = adjust_mark(adjusted, cash_per_ex_share(action, actions), action.share_multiplier)
            expected.loc[day] = previous / adjusted
            if reference_close_tick is not None:
                reference = float(references.loc[day])
                if not np.isfinite(reference) or reference <= 0:
                    raise QualityError("Invalid source reference close at event")
                # The source reference must independently agree with the exact
                # declared actions within half of its declared price tick.
                # Never infer a share multiplier or alter the cash ledger here.
                slack = np.finfo(np.float32).eps * max(1., abs(previous), abs(reference), abs(adjusted))
                on_grid = abs(reference - round(reference / reference_close_tick) * reference_close_tick) <= slack
                if on_grid and abs(reference - adjusted) <= reference_close_tick / 2 + slack:
                    source_expected.loc[day] = previous / reference
            checked_keys.add(key)
            checked_events += 1
        residual = (actual / expected - 1).loc[pairs]
        checked += len(residual)
        changes += int((actual.loc[pairs] - 1).abs().gt(tolerance).sum())
        maximum = max(maximum, float(residual.abs().max()) if len(residual) else 0.)
        effective = residual.copy()
        for day in residual.index[residual.abs().gt(tolerance)]:
            if pd.notna(source_expected.loc[day]):
                reference_residual = float(actual.loc[day] / source_expected.loc[day] - 1)
                if abs(reference_residual) <= tolerance:
                    effective.loc[day] = reference_residual
                    rounding_days += 1
                    if len(rounding_examples) < 10:
                        rounding_examples.append({"instrument": instrument, "date": day.strftime("%Y-%m-%d"),
                                                  "exact_action_residual": float(residual.loc[day]),
                                                  "source_reference_residual": reference_residual})
        effective_maximum = max(effective_maximum, float(effective.abs().max()) if len(effective) else 0.)
        for day in effective.index[effective.abs().gt(tolerance)]:
            key = (instrument, day)
            code = "event_adjustment_mismatch" if key in groups else "unexplained_adjustment_change"
            mismatches[code] = mismatches.get(code, 0) + 1
            if len(examples) < 10:
                examples.append({"code": code, "instrument": instrument, "date": day.strftime("%Y-%m-%d"),
                                 "actual_ratio": float(actual.loc[day]), "expected_ratio": float(expected.loc[day]),
                                 "relative_residual": float(residual.loc[day])})
    unchecked_events = len(set(groups) - checked_keys)
    return {"status": "failed" if mismatches else "incomplete" if unchecked_events or not checked else "passed",
            "checked_quote_pairs": checked, "adjustment_change_days": changes,
            "checked_event_days": checked_events, "unchecked_event_days": unchecked_events,
            "maximum_relative_residual": maximum, "relative_tolerance": tolerance,
            "maximum_effective_relative_residual": effective_maximum,
            "reference_close_tick": reference_close_tick, "rounding_supported_event_days": rounding_days,
            "reference_rounding_examples": rounding_examples,
            "issues": [{"code": code, "rows": count} for code, count in sorted(mismatches.items())],
            "examples": examples, "source_completeness_verified": False,
            "note": "Checks adjacent quoted trading days; gaps and source completeness need separate coverage evidence."}
