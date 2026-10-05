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


class DuplicateProposal(ETFError):
    """A completed semantic duplicate; it must not enter a repair loop."""
    reason = "duplicate"


class ValidationFailure(QualityError):
    """Trusted, prompt-safe description of a failed implementation check."""
    reason = "validation_failure"

    def __init__(self, *, category: str, check_id: str, message: str,
                 expected=None, observed=None, recoverable: bool = True):
        super().__init__(message)
        self.category, self.check_id = category, check_id
        self.expected, self.observed, self.recoverable = expected, observed, recoverable

    def prompt_payload(self):
        return {"category": self.category, "check_id": self.check_id,
                "expected": self.expected, "observed": self.observed,
                "sanitized_message": str(self), "recoverable": self.recoverable}
