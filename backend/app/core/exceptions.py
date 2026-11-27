"""Domain exceptions and the consistent API error envelope."""
from __future__ import annotations


class AppError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str = "", *, details: dict | None = None):
        super().__init__(message or self.code)
        self.message = message or self.code
        self.details = details or {}


class NotFoundError(AppError):
    status_code, code = 404, "not_found"


class ConflictError(AppError):
    status_code, code = 409, "conflict"


class ValidationFailure(AppError):
    status_code, code = 422, "validation_failed"


class ForbiddenError(AppError):
    status_code, code = 403, "forbidden"


class RateLimited(AppError):
    status_code, code = 429, "rate_limited"


class CycleError(ValidationFailure):
    code = "dependency_cycle"


class UnsafeURLError(ValidationFailure):
    code = "unsafe_url"


class ProviderError(AppError):
    status_code, code = 503, "ai_provider_error"
    #: recoverable errors may be retried / failed over; others must not be.
    recoverable = True  # may fail over to another provider
    retryable = True  # worth retrying the SAME provider


class ProviderStallError(ProviderError):
    """The model server accepted the request but produced no output in time (overloaded, swapping, or queueing it)."""
    code = "ai_provider_stalled"


class ProviderRateLimitError(ProviderError):
    """429: the provider is healthy but asks us to slow down. Waited out with backoff; never counted against its circuit breaker."""
    code = "ai_provider_rate_limited"

    def __init__(self, message: str = "", *, retry_after_s: float | None = None, details: dict | None = None):
        super().__init__(message, details=details)
        self.retry_after_s = retry_after_s


class ProviderAccountError(ProviderError):
    """401/402/403: bad key, no billing/quota. Retrying is pointless, but another provider may work."""
    code = "ai_provider_account_error"
    retryable = False


class NonRecoverableProviderError(ProviderError):
    recoverable = False


class CapabilityError(ProviderError):
    """No provider satisfies the required capabilities; never silently downgraded."""
    recoverable = False


class CircuitOpenError(ProviderError):
    code = "circuit_open"


class StructuredOutputError(NonRecoverableProviderError):
    code = "structured_output_invalid"


def error_body(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}
