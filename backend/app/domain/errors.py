class DomainError(Exception):
    """Base class for domain rule violations."""


class ScopeError(DomainError):
    """Raised when an operation lacks or mismatches client scope."""
