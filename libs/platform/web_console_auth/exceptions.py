"""Authentication exceptions for the Web Console and Gateway."""


class AuthError(Exception):
    """Base exception for all authentication errors."""


class InvalidTokenError(AuthError):
    """Raised when a token is invalid (malformed, wrong signature, wrong type, etc.)."""


class InvalidSignatureError(InvalidTokenError):
    """Raised when JWT signature verification fails."""


class InvalidIssuerError(InvalidTokenError):
    """Raised when the token issuer is not trusted."""


class InvalidAudienceError(InvalidTokenError):
    """Raised when the token audience does not match the expected value."""


class ImmatureSignatureError(InvalidTokenError):
    """Raised when the token is not yet valid (nbf in the future)."""


class TokenExpiredError(AuthError):
    """Raised when a token has expired."""


class TokenRevokedError(AuthError):
    """Raised when a token has been revoked (blacklisted)."""


class TokenReplayedError(AuthError):
    """Raised when a one-time-use token (by jti) is replayed."""


class SubjectMismatchError(AuthError):
    """Raised when JWT subject does not match bound user/header."""


class SessionExpiredError(AuthError):
    """Raised when session_version does not match stored version."""


class MissingJtiError(AuthError):
    """Raised when a required jti claim is missing."""


class SessionLimitExceededError(AuthError):
    """Raised when a user exceeds the maximum concurrent session limit."""


class RateLimitExceededError(AuthError):
    """Raised when rate limit is exceeded for an action.

    HTTP handlers should catch this and return HTTP 429 Too Many Requests.
    """


__all__ = [
    "AuthError",
    "InvalidTokenError",
    "InvalidSignatureError",
    "InvalidIssuerError",
    "InvalidAudienceError",
    "ImmatureSignatureError",
    "TokenExpiredError",
    "TokenRevokedError",
    "TokenReplayedError",
    "SubjectMismatchError",
    "SessionExpiredError",
    "MissingJtiError",
    "SessionLimitExceededError",
    "RateLimitExceededError",
]
