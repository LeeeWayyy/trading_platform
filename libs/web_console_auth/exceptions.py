"""Authentication exceptions for the Web Console."""


class AuthError(Exception):
    """Base exception for all authentication errors."""

    pass


class TokenExpiredError(AuthError):
    """Raised when a token has expired."""

    pass


class TokenRevokedError(AuthError):
    """Raised when a token has been revoked (blacklisted)."""

    pass


class InvalidTokenError(AuthError):
    """Raised when a token is invalid (malformed, wrong signature, wrong type, etc.)."""

    pass


class SessionLimitExceededError(AuthError):
    """Raised when a user exceeds the maximum concurrent session limit."""

    pass


class RateLimitExceededError(AuthError):
    """Raised when rate limit is exceeded for an action.

    HTTP handlers should catch this and return HTTP 429 Too Many Requests.
    """

    pass
