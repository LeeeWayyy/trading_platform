"""Handler for Auth0 step-up MFA callback."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, cast

from apps.web_console.auth.jwks_validator import JWKSValidator
from apps.web_console.auth.mfa_verification import get_amr_method, verify_step_up_auth
from apps.web_console.auth.session_invalidation import validate_session_version

logger = logging.getLogger(__name__)

STEP_UP_CALLBACK_TIMEOUT_SECONDS = 300


class SecurityError(Exception):
    pass


async def handle_step_up_callback(
    *,
    code: str,
    state: str,
    session_store: Any,
    session_id: str,
    audit_logger: Any,
    jwks_validator: JWKSValidator | None = None,
    expected_audience: str | None = None,
    expected_issuer: str | None = None,
    db_pool: Any | None = None,
    validate_state: Callable[[str, str], bool] | None = None,
    exchange_code: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Process the step-up callback and update session state.

    Returns dict with either ``redirect_to`` or ``error`` keys.
    """

    session_data = await session_store.get_session(session_id, update_activity=False)
    if not session_data:
        await session_store.clear_step_up_state(session_id)
        if audit_logger:
            await audit_logger.log_auth_event(
                user_id=None,
                action="step_up_callback_failed",
                outcome="denied",
                details={"reason": "session_not_found"},
            )
        return {
            "error": "session_not_found",
            "message": "Session not found. Please sign in again.",
            "redirect_to": "/login",
        }

    # Fail closed: require db_pool for session_version validation
    if db_pool is None:
        await session_store.delete_session(session_id)
        await session_store.clear_step_up_state(session_id)
        if audit_logger:
            await audit_logger.log_auth_event(
                user_id=session_data.user_id,
                action="step_up_callback_failed",
                outcome="denied",
                details={"reason": "db_pool_unavailable"},
            )
        return {
            "error": "session_validation_unavailable",
            "message": "Unable to verify session. Please sign in again.",
            "redirect_to": "/login",
        }

    is_current = await validate_session_version(
        session_data.user_id, session_data.session_version, db_pool
    )
    if not is_current:
        await session_store.delete_session(session_id)
        await session_store.clear_step_up_state(session_id)
        if audit_logger:
            await audit_logger.log_auth_event(
                user_id=session_data.user_id,
                action="step_up_session_invalidated",
                outcome="denied",
                details={"reason": "session_version_mismatch"},
            )
        return {
            "error": "session_invalidated",
            "message": "Your session has been revoked. Please sign in again.",
            "redirect_to": "/login",
        }

    # Timeout enforcement
    if session_data.step_up_requested_at:
        elapsed = (datetime.now(UTC) - session_data.step_up_requested_at).total_seconds()
        if elapsed > STEP_UP_CALLBACK_TIMEOUT_SECONDS:
            await session_store.clear_step_up_state(session_id)
            if audit_logger:
                await audit_logger.log_auth_event(
                    user_id=session_data.user_id,
                    action="step_up_timeout",
                    outcome="denied",
                    details={"elapsed_seconds": elapsed},
                )
            return {
                "error": "step_up_timeout",
                "message": "Step-up authentication timed out. Please try again.",
                "redirect_to": "/dashboard",
            }

    pending_action = getattr(session_data, "pending_action", None)

    if validate_state is None:
        await session_store.clear_step_up_state(session_id)
        if audit_logger:
            await audit_logger.log_auth_event(
                user_id=session_data.user_id,
                action="step_up_state_validation",
                outcome="denied",
                details={
                    "reason": "missing_validator",
                    "state": state,
                    "pending_action": pending_action,
                },
            )
        return {
            "error": "state_validation_required",
            "message": "Authentication state could not be validated. Please try again.",
            "redirect_to": pending_action or "/login",
        }

    if not validate_state(state, session_id):
        await session_store.clear_step_up_state(session_id)
        if audit_logger:
            await audit_logger.log_auth_event(
                user_id=session_data.user_id,
                action="step_up_state_validation",
                outcome="denied",
                details={
                    "reason": "state_mismatch",
                    "state": state,
                    "pending_action": pending_action,
                },
            )
        return {
            "error": "invalid_state",
            "message": "Authentication request could not be verified. Please start again.",
            "redirect_to": pending_action or "/login",
        }

    if not exchange_code:
        await session_store.clear_step_up_state(session_id)
        if audit_logger:
            await audit_logger.log_auth_event(
                user_id=session_data.user_id,
                action="step_up_callback_failed",
                outcome="denied",
                details={"reason": "exchange_code_missing"},
            )
        return {
            "error": "step_up_configuration_error",
            "message": "Authentication could not be completed. Please try again.",
            "redirect_to": pending_action or "/login",
        }

    if jwks_validator is None:
        await session_store.clear_step_up_state(session_id)
        if audit_logger:
            await audit_logger.log_auth_event(
                user_id=session_data.user_id,
                action="step_up_callback_failed",
                outcome="denied",
                details={"reason": "jwks_validator_missing"},
            )
        return {
            "error": "step_up_configuration_error",
            "message": "Authentication could not be completed. Please try again.",
            "redirect_to": pending_action or "/login",
        }

    issuer = expected_issuer if expected_issuer is not None else None
    if issuer is None and getattr(jwks_validator, "auth0_domain", None):
        issuer = f"https://{jwks_validator.auth0_domain}/"

    if expected_audience is None or issuer is None:
        await session_store.clear_step_up_state(session_id)
        if audit_logger:
            await audit_logger.log_auth_event(
                user_id=session_data.user_id,
                action="step_up_callback_failed",
                outcome="denied",
                details={
                    "reason": "issuer_or_audience_missing",
                    "has_expected_audience": bool(expected_audience),
                    "has_issuer": bool(issuer),
                },
            )
        return {
            "error": "step_up_configuration_error",
            "message": "Authentication could not be completed. Please try again.",
            "redirect_to": pending_action or "/login",
        }

    tokens = await exchange_code(code)

    id_token = tokens.get("id_token")
    if not id_token:
        await session_store.clear_step_up_state(session_id)
        if audit_logger:
            await audit_logger.log_auth_event(
                user_id=session_data.user_id,
                action="step_up_callback_failed",
                outcome="denied",
                details={"reason": "id_token_missing"},
            )
        return {
            "error": "id_token_missing",
            "message": "Step-up response was invalid. Please try again.",
            "redirect_to": pending_action or "/login",
        }

    try:
        id_token_claims = await jwks_validator.validate_id_token(
            id_token=id_token,
            expected_nonce=None,
            expected_audience=expected_audience,
            expected_issuer=issuer,
        )
    except Exception as exc:
        await session_store.clear_step_up_state(session_id)
        if audit_logger:
            await audit_logger.log_auth_event(
                user_id=session_data.user_id,
                action="step_up_callback_failed",
                outcome="denied",
                details={"error": str(exc)},
            )
        return {
            "error": "id_token_validation_failed",
            "message": "Step-up validation failed. Please try again.",
            "redirect_to": "/dashboard",
        }

    if id_token_claims.get("sub") != session_data.user_id:
        await session_store.delete_session(session_id)
        await session_store.clear_step_up_state(session_id)
        if audit_logger:
            await audit_logger.log_auth_event(
                user_id=id_token_claims.get("sub"),
                action="step_up_callback_failed",
                outcome="denied",
                details={
                    "error": "subject_mismatch",
                    "expected": session_data.user_id,
                    "received": id_token_claims.get("sub"),
                },
            )
        return {
            "error": "subject_mismatch",
            "message": "Identity mismatch detected. Please sign in again.",
            "redirect_to": "/login",
        }

    valid, error = verify_step_up_auth(id_token_claims)
    if not valid:
        await session_store.clear_step_up_state(session_id)
        if audit_logger:
            await audit_logger.log_auth_event(
                user_id=id_token_claims.get("sub"),
                action="step_up_callback_failed",
                outcome="denied",
                details={"error": error},
            )
        return {
            "error": error,
            "message": _error_message(error),
            "redirect_to": "/dashboard",
        }

    await session_store.update_step_up_claims(session_id, id_token_claims)
    await session_store.clear_step_up_request_timestamp(session_id)

    if audit_logger:
        await audit_logger.log_auth_event(
            user_id=id_token_claims.get("sub"),
            action="step_up_success",
            outcome="success",
            details={"amr_method": get_amr_method(id_token_claims)},
        )

    pending_action = getattr(session_data, "pending_action", None) or "/dashboard"
    return {"redirect_to": pending_action}


def clear_step_up_state(session_store: Any, session_id: str) -> Awaitable[bool]:
    return cast(Awaitable[bool], session_store.clear_step_up_state(session_id))


def _error_message(error: str | None) -> str:
    if error == "auth_too_old":
        return "Recent MFA is required. Please try again."
    if error == "mfa_not_performed":
        return "Multi-factor authentication was not completed."
    return "Step-up authentication failed."


__all__ = [
    "handle_step_up_callback",
    "clear_step_up_state",
    "STEP_UP_CALLBACK_TIMEOUT_SECONDS",
    "SecurityError",
]
