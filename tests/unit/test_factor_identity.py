from types import SimpleNamespace

from etf_ml.research.factor_identity import canonical_expression, identify_definition


def _spec(**changes):
    value = {"formula": "(adj_close.shift(2) + adj_open) / 2", "required_fields": ["adj_close", "adj_open"],
             "lookback": 3, "minimum_observations": 3, "available_at": "after_daily_ingestion",
             "missing_policy": "insufficient_history_is_missing", "cross_sectional": False,
             "applicable_scope": "domestic_equity", "research_group": "trend"}
    value.update(changes)
    return SimpleNamespace(**value)


def _fields():
    return {name: {"meaning": name, "unit": "adjusted_price", "adjustment": "adjusted",
                   "available_at": "after_daily_ingestion", "missing_policy": "preserve_missing"}
            for name in ("adj_close", "adj_open")}


def test_supported_expression_identity_ignores_whitespace_and_redundant_parentheses():
    first = identify_definition(_spec(), _fields())
    second = identify_definition(_spec(formula=" (( adj_close.shift(2) + adj_open ) / 2) "), _fields())
    assert first.definition_id == second.definition_id
    assert first.hard_matchable and not first.opaque


def test_identity_keeps_timing_missingness_and_field_semantics_distinct():
    first = identify_definition(_spec(), _fields())
    assert first.definition_id != identify_definition(_spec(minimum_observations=2), _fields()).definition_id
    changed = _fields()
    changed["adj_close"] = {**changed["adj_close"], "available_at": "next_open"}
    assert first.definition_id != identify_definition(_spec(), changed).definition_id
    assert first.definition_id != identify_definition(_spec(research_group="volatility"), _fields()).definition_id


def test_unsafe_algebra_and_unknown_semantics_are_never_hard_equated():
    assert canonical_expression("x / x")[1] is False
    left = identify_definition(_spec(formula="adj_close / adj_close"), _fields())
    right = identify_definition(_spec(formula="1"), _fields())
    assert left.definition_id != right.definition_id
    incomplete = _fields()
    incomplete["adj_close"].pop("missing_policy")
    unknown = identify_definition(_spec(), incomplete)
    assert unknown.definition_id and not unknown.hard_matchable


def test_opaque_expression_uses_raw_definition_without_guessing_equivalence():
    first = identify_definition(_spec(formula="custom_indicator(adj_close)"), _fields())
    second = identify_definition(_spec(formula="custom_indicator( adj_close )"), _fields())
    assert first.opaque and second.opaque
    assert first.definition_id != second.definition_id
