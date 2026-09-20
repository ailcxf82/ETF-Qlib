from __future__ import annotations

import ast

from etf_ml.errors import QualityError

ALLOWED_MODULES = {"numpy", "pandas", "math", "statistics"}
FORBIDDEN_CALLS = {"eval", "exec", "compile", "open", "__import__", "globals", "locals",
                   "getattr", "setattr", "delattr", "input", "breakpoint"}


def validate_source(source: str) -> None:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise QualityError("Candidate syntax error") from exc
    if not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "compute"
               for node in tree.body):
        raise QualityError("Candidate must define compute(panel)")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] not in ALLOWED_MODULES for alias in node.names):
                raise QualityError("Candidate import not allowed")
        if isinstance(node, ast.ImportFrom):
            if not node.module or node.module.split(".")[0] not in ALLOWED_MODULES:
                raise QualityError("Candidate import not allowed")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise QualityError("Candidate dunder access not allowed")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_CALLS:
            raise QualityError("Candidate dynamic execution or file access not allowed")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered = node.value.lower()
            if "label" in lowered or "holdout" in lowered or ".env" in lowered:
                raise QualityError("Candidate references forbidden data")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("shift", "pct_change") and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.UnaryOp) and isinstance(arg.op, ast.USub):
                    raise QualityError("Candidate references future values")
            if any(keyword.arg == "center" and isinstance(keyword.value, ast.Constant) and
                   keyword.value.value is True for keyword in node.keywords):
                raise QualityError("Candidate uses a centered window")
            if node.func.attr == "rolling" and isinstance(node.func.value, ast.Call):
                grouped = node.func.value
                if isinstance(grouped.func, ast.Attribute) and grouped.func.attr == "groupby":
                    raise QualityError("Candidate groupby rolling changes the required input index")
