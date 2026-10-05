"""Conservative, hashable factor-definition identities.

This deliberately recognises only a small arithmetic expression subset.  An
unknown expression is still addressable by its complete raw definition, but is
never claimed to be algebraically equivalent to another expression.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass

from etf_ml.utils import content_hash


CANONICALIZER_VERSION = "factor-definition-v1"
_ALIASES = {"close": "adj_close", "open": "adj_open", "high": "adj_high", "low": "adj_low"}
_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow)
_UNARYOPS = (ast.UAdd, ast.USub)
_METHODS = {"shift", "rolling_mean", "rolling_std"}
_SEMANTIC_FIELDS = ("meaning", "unit", "adjustment", "available_at", "missing_policy")


@dataclass(frozen=True)
class FactorIdentity:
    definition_id: str | None
    projection: dict
    opaque: bool
    hard_matchable: bool


def _node(node: ast.AST) -> object:
    if isinstance(node, ast.Name):
        return {"name": _ALIASES.get(node.id, node.id)}
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str, bool, type(None))):
        return {"constant": node.value}
    if isinstance(node, ast.BinOp) and isinstance(node.op, _BINOPS):
        return {"binop": type(node.op).__name__, "left": _node(node.left), "right": _node(node.right)}
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, _UNARYOPS):
        return {"unary": type(node.op).__name__, "value": _node(node.operand)}
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and
            node.func.attr in _METHODS and not node.keywords):
        return {"call": node.func.attr, "receiver": _node(node.func.value),
                "args": [_node(arg) for arg in node.args]}
    raise ValueError("unsupported expression syntax")


def canonical_expression(formula: str) -> tuple[object, bool]:
    """Return a safe AST projection; keep unrecognised syntax opaque."""
    try:
        tree = ast.parse(formula.strip(), mode="eval")
        return _node(tree.body), False
    except (SyntaxError, ValueError, TypeError):
        return formula.strip(), True


def identify_definition(spec, fields: dict[str, dict], *, canonicalizer_version: str = CANONICALIZER_VERSION) -> FactorIdentity:
    """Project every numeric/timing semantic into one conservative identity."""
    expression, opaque = canonical_expression(spec.formula)
    field_semantics = {}
    complete = True
    for name in sorted(spec.required_fields):
        detail = fields.get(name)
        if not isinstance(detail, dict) or any(not detail.get(key) for key in _SEMANTIC_FIELDS):
            complete = False
            field_semantics[name] = None
        else:
            field_semantics[name] = {key: detail[key] for key in _SEMANTIC_FIELDS}
    projection = {
        "identity_schema": "factor-definition-v1",
        "canonicalizer_version": canonicalizer_version,
        "expression": expression,
        "opaque": opaque,
        "required_fields": field_semantics,
        "lookback": spec.lookback,
        "minimum_observations": spec.minimum_observations,
        "available_at": spec.available_at,
        "missing_policy": spec.missing_policy,
        "cross_sectional": spec.cross_sectional,
        "applicable_scope": spec.applicable_scope,
        # Group membership changes the ablation experiment, so it is part of
        # the research definition even when the numeric expression is unchanged.
        "research_group": getattr(spec, "research_group", ""),
    }
    # Opaque definitions only match their complete raw projection.  Missing
    # field semantics remain readable but cannot be used as a hard rejection.
    return FactorIdentity(content_hash(projection), projection, opaque, complete)
