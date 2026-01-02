from dataclasses import dataclass
from typing import Any


@dataclass
class AuthResult:
    """Result of an authentication attempt.

    Uses cookie_value and csrf_token from P5T1 session store API:
    - cookie_value: Signed session cookie value (session_id.key_id:signature)
    - csrf_token: CSRF protection token for forms
    """

    success: bool
    cookie_value: str | None = None
    csrf_token: str | None = None
    user_data: dict[str, Any] | None = None
    error_message: str | None = None
    warning_message: str | None = None  # Non-fatal warning (e.g., certificate expiring soon)
    requires_mfa: bool = False
    rate_limited: bool = False
    retry_after: int = 0
    locked_out: bool = False
    lockout_remaining: int = 0
