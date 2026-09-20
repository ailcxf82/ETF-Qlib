"""Failures are categories, never fabricated successful metrics."""

class ETFError(Exception):
    exit_code = 4
    reason = "execution_failed"


class ConfigurationError(ETFError):
    exit_code = 2
    reason = "configuration_error"


class DataNotReady(ETFError):
    exit_code = 3
    reason = "data_not_ready"


class QualityError(ETFError):
    exit_code = 5
    reason = "quality_not_passed"


class IntegrityError(QualityError):
    reason = "artifact_integrity"


class ExecutionError(ETFError):
    exit_code = 4
    reason = "execution_failed"


class BudgetError(ETFError):
    exit_code = 6
    reason = "budget_exhausted"
