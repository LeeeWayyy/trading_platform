"""Step-up MFA verification helpers."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

ALLOWED_AMR_METHODS = {"mfa", "otp", "sms", "push", "webauthn", "hwk"}
MFA_MAX_AGE_SECONDS = int(os.getenv("MFA_MAX_AGE_SECONDS", "60"))


def verify_step_up_auth(id_token_claims: dict[str, Any]) -> tuple[bool, str | None]:
    """Validate that step-up MFA just occurred.

    Returns (valid, error_code)
    """

    auth_time = id_token_claims.get("auth_time")
    amr = id_token_claims.get("amr", []) or []

    if auth_time is None:
        return False, "missing_auth_time"

    try:
        auth_time_dt = datetime.fromtimestamp(int(auth_time), tz=UTC)
    except (ValueError, TypeError, OverflowError):
        return False, "invalid_auth_time"

    age_seconds = (datetime.now(UTC) - auth_time_dt).total_seconds()
    if age_seconds > MFA_MAX_AGE_SECONDS:
        return False, "auth_too_old"

    if not amr:
        return False, "mfa_not_performed"

    if not any(str(method) in ALLOWED_AMR_METHODS for method in amr):
        return False, "mfa_method_not_allowed"

    return True, None


def get_amr_method(id_token_claims: dict[str, Any]) -> str | None:
    amr = id_token_claims.get("amr")
    if isinstance(amr, list | tuple) and amr:
        return str(amr[0])
    return None


async def require_2fa_for_action(
    session_data: Any, action: str, audit_logger: Any | None
) -> tuple[bool, str | None]:
    """Ensure recent step-up authentication before privileged actions."""

    if not session_data:
        return False, "session_missing"

    claims = getattr(session_data, "step_up_claims", None)
    if not claims:
        if audit_logger:
            await audit_logger.log_auth_event(
                user_id=getattr(session_data, "user_id", None),
                action=action,
                outcome="denied",
                details={"reason": "step_up_required"},
            )
        return False, "step_up_required"

    valid, error = verify_step_up_auth(claims)
    if not valid:
        if audit_logger:
            await audit_logger.log_auth_event(
                user_id=getattr(session_data, "user_id", None),
                action=action,
                outcome="denied",
                details={"reason": error},
            )
        return False, error

    return True, None


__all__ = ["verify_step_up_auth", "get_amr_method", "require_2fa_for_action"]
