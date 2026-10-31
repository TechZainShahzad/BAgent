class JiraError(Exception):
    """Base for Jira failures. Always surfaced."""


class JiraAuthError(JiraError): ...


class JiraNotFoundError(JiraError): ...


class JiraValidationError(JiraError):
    """4xx rejection with a definitive outcome: nothing was created/changed."""


class JiraRateLimitedError(JiraError):
    def __init__(self, retry_after: float) -> None:
        super().__init__(f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


class JiraTransientError(JiraError):
    """Read failed transiently (timeout/5xx on a GET). Safe to retry."""


class JiraUnknownOutcomeError(JiraError):
    """A WRITE timed out / dropped / 5xx: the change may or may not have been applied.
    Callers must reconcile by marker search before any retry."""


class JiraProjectNotAllowedError(JiraError):
    """Project is not in JIRA_PROJECT_ALLOWLIST."""
