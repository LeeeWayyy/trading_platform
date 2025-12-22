"""Streamlit component for API key lifecycle management."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, time
from typing import Any

import redis.asyncio as redis_async
import streamlit as st
from redis.exceptions import RedisError

from apps.web_console.auth.audit_log import AuditLogger
from apps.web_console.components.csrf_protection import generate_csrf_token, verify_csrf_token
from libs.admin.api_keys import REVOKED_KEY_CACHE_TTL, ApiKeyScopes, generate_api_key, hash_api_key
from libs.common.async_utils import run_async
from libs.web_console_auth.db import acquire_connection
from libs.web_console_auth.gateway_auth import AuthenticatedUser
from libs.web_console_auth.permissions import Permission, has_permission

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _async_redis() -> AsyncIterator[redis_async.Redis | None]:
    """Create a fresh async Redis client for this async context.

    IMPORTANT: Async Redis clients bind to the event loop at first use.
    run_async() creates a new event loop per call, so we MUST create the
    client inside the async context (not pass it from sync code).

    Yields:
        Fresh async Redis client or None if connection fails
    """
    host = os.getenv("REDIS_HOST", "localhost")
    port_str = os.getenv("REDIS_PORT", "6379")
    db_str = os.getenv("REDIS_DB", "0")

    try:
        port = int(port_str)
        db = int(db_str)
    except (ValueError, TypeError):
        logger.warning("Invalid REDIS_PORT or REDIS_DB env vars")
        yield None
        return

    password = os.getenv("REDIS_PASSWORD") or None
    client: redis_async.Redis | None = None
    try:
        client = redis_async.Redis(
            host=host, port=port, db=db, password=password, decode_responses=True
        )
        yield client
    except (RedisError, ConnectionError, TimeoutError, OSError) as exc:
        logger.warning("Failed to create async Redis client: %s", exc)
        yield None
    finally:
        if client:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass


_MODAL_STATE_KEY = "api_key_one_time_modal"
_REVOKE_STATE_KEY = "api_key_revoke_target"
_ROTATE_STATE_KEY = "api_key_rotate_target"
_PENDING_REVOKE_KEY = "api_key_pending_revocations"

# Validation constants
_MIN_REVOCATION_REASON_LENGTH = 20

# Timeout constants
_DB_OPERATION_TIMEOUT_SECONDS = 10.0


def render_api_key_manager(
    user: AuthenticatedUser,
    db_pool: Any,
    audit_logger: AuditLogger,
    redis_client: Any,
) -> None:
    """Render API key manager UI."""

    if not has_permission(user, Permission.MANAGE_API_KEYS):
        st.error("Permission denied: MANAGE_API_KEYS required")
        return

    st.title("API Key Manager")
    st.caption("Create, rotate, and revoke API keys for programmatic access.")

    csrf_token = generate_csrf_token()

    _render_one_time_modal()

    _render_create_form(user, db_pool, audit_logger, csrf_token)

    keys = _list_keys_sync(db_pool, user.user_id)

    if _PENDING_REVOKE_KEY not in st.session_state:
        st.session_state[_PENDING_REVOKE_KEY] = set()

    _render_keys_table(
        user=user,
        keys=keys,
        db_pool=db_pool,
        audit_logger=audit_logger,
        redis_client=redis_client,
        csrf_token=csrf_token,
    )

    _render_revoke_dialog(
        user=user,
        db_pool=db_pool,
        audit_logger=audit_logger,
        redis_client=redis_client,
        csrf_token=csrf_token,
    )

    _render_rotate_dialog(
        user=user,
        db_pool=db_pool,
        audit_logger=audit_logger,
        csrf_token=csrf_token,
    )


def _render_create_form(
    user: AuthenticatedUser,
    db_pool: Any,
    audit_logger: AuditLogger,
    csrf_token: str,
) -> None:
    st.subheader("Create New API Key")

    with st.form("create_api_key_form"):
        name = st.text_input(
            "Key Name",
            max_chars=50,
            help="Descriptive name (3-50 characters).",
        )

        scopes = {
            "read_positions": st.checkbox("Read positions"),
            "read_orders": st.checkbox("Read orders"),
            "write_orders": st.checkbox("Write orders"),
            "read_strategies": st.checkbox("Read strategies"),
        }

        set_expiry = st.checkbox("Set expiry date (optional)")
        expires_at = None
        if set_expiry:
            selected_date = st.date_input("Expiry date")
            if selected_date:
                expires_at = datetime.combine(selected_date, time(23, 59, 59, tzinfo=UTC))

        submitted_csrf = st.text_input(
            "csrf",
            value=csrf_token,
            type="password",
            label_visibility="hidden",
        )

        submitted = st.form_submit_button("Create Key", type="primary")

        if not submitted:
            return

        if not verify_csrf_token(submitted_csrf):
            st.error("Invalid form submission. Please refresh and try again.")
            return

        validation_error = _validate_create_input(name, scopes, expires_at)
        if validation_error:
            st.error(validation_error)
            return

        creation = _create_key_sync(
            db_pool=db_pool,
            user_id=user.user_id,
            name=name.strip(),
            scopes=scopes,
            expires_at=expires_at,
        )

        if creation is None:
            st.error("Failed to create API key. Please try again.")
            return

        st.session_state[_MODAL_STATE_KEY] = creation

        run_async(
            audit_logger.log_action(
                user_id=user.user_id,
                action="api_key_created",
                resource_type="api_key",
                resource_id=creation["prefix"],
                outcome="success",
                details={"name": name.strip(), "scopes": _selected_scopes(scopes)},
            )
        )

        st.success("API key created. Copy the key below; it will not be shown again.")


def _render_keys_table(
    user: AuthenticatedUser,
    keys: list[dict[str, Any]],
    db_pool: Any,
    audit_logger: AuditLogger,
    redis_client: Any,
    csrf_token: str,
) -> None:
    st.subheader("Existing Keys")

    if not keys:
        st.info("No API keys yet. Create one to get started.")
        return

    for key in keys:
        cols = st.columns([3, 2, 3, 2, 2, 2])

        status_text, status_color = _compute_status(key)

        scopes_badges = " ".join(f"`{scope}`" for scope in _normalize_scopes(key["scopes"]))

        cols[0].markdown(f"**{key['name']}**")
        cols[1].markdown(f"`{key['key_prefix']}`")
        cols[2].markdown(scopes_badges or "—")
        cols[3].markdown(_format_ts(key.get("last_used_at")) or "Never")
        cols[4].markdown(_format_ts(key.get("created_at")) or "—")
        cols[5].markdown(f":{status_color}[{status_text}]")

        action_cols = st.columns(2)
        if action_cols[0].button("Revoke", key=f"revoke_{key['key_prefix']}"):
            st.session_state[_REVOKE_STATE_KEY] = key
        if action_cols[1].button("Rotate", key=f"rotate_{key['key_prefix']}"):
            st.session_state[_ROTATE_STATE_KEY] = key


def _render_revoke_dialog(
    user: AuthenticatedUser,
    db_pool: Any,
    audit_logger: AuditLogger,
    redis_client: Any,
    csrf_token: str,
) -> None:
    target = st.session_state.get(_REVOKE_STATE_KEY)
    if not target:
        return

    st.warning(
        f"Revoke **{target['name']}** ({target['key_prefix']}). "
        "Revocation is immediate and cannot be undone."
    )

    with st.form("revoke_form"):
        reason = st.text_area(
            f"Reason for revocation (min {_MIN_REVOCATION_REASON_LENGTH} chars)",
            height=80,
        )
        submitted_csrf = st.text_input(
            "csrf",
            value=csrf_token,
            type="password",
            label_visibility="hidden",
        )
        confirm = st.form_submit_button("Confirm Revoke", type="primary")

        if not confirm:
            return

        if not verify_csrf_token(submitted_csrf):
            st.error("Invalid session. Refresh and try again.")
            return

        if len(reason.strip()) < _MIN_REVOCATION_REASON_LENGTH:
            st.error(f"Reason must be at least {_MIN_REVOCATION_REASON_LENGTH} characters.")
            return

        success = _revoke_key_sync(
            db_pool=db_pool,
            user_id=user.user_id,
            prefix=target["key_prefix"],
            redis_client=redis_client,
        )

        if success:
            st.success("API key revoked.")
            run_async(
                audit_logger.log_action(
                    user_id=user.user_id,
                    action="api_key_revoked",
                    resource_type="api_key",
                    resource_id=target["key_prefix"],
                    outcome="success",
                    details={"reason": reason.strip()},
                )
            )
            pending: set[str] = st.session_state.get(_PENDING_REVOKE_KEY, set())
            if target["key_prefix"] in pending:
                pending.discard(target["key_prefix"])
                st.session_state[_PENDING_REVOKE_KEY] = pending
        else:
            st.error("Failed to revoke key. Please retry.")

        st.session_state.pop(_REVOKE_STATE_KEY, None)


def _render_rotate_dialog(
    user: AuthenticatedUser,
    db_pool: Any,
    audit_logger: AuditLogger,
    csrf_token: str,
) -> None:
    target = st.session_state.get(_ROTATE_STATE_KEY)
    if not target:
        return

    st.warning(
        "Rotating will create a new key; the old key remains active until you revoke it. "
        "Make sure clients switch to the new key."
    )

    with st.form("rotate_form"):
        submitted_csrf = st.text_input(
            "csrf",
            value=csrf_token,
            type="password",
            label_visibility="hidden",
        )
        confirm = st.form_submit_button("Create Rotated Key", type="primary")

        if not confirm:
            return

        if not verify_csrf_token(submitted_csrf):
            st.error("Invalid session. Refresh and try again.")
            return

        rotated_name = f"{target['name']}_rotated_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
        expires_at = _coerce_datetime(target.get("expires_at"))
        new_key = _create_key_sync(
            db_pool=db_pool,
            user_id=user.user_id,
            name=rotated_name,
            scopes={scope: True for scope in _normalize_scopes(target["scopes"])},
            expires_at=expires_at,
        )

        if new_key is None:
            st.error("Failed to rotate key.")
            st.session_state.pop(_ROTATE_STATE_KEY, None)
            return

        st.session_state[_MODAL_STATE_KEY] = new_key

        pending: set[str] = st.session_state.get(_PENDING_REVOKE_KEY, set())
        pending.add(target["key_prefix"])
        st.session_state[_PENDING_REVOKE_KEY] = pending

        run_async(
            audit_logger.log_action(
                user_id=user.user_id,
                action="api_key_rotated",
                resource_type="api_key",
                resource_id=target["key_prefix"],
                outcome="success",
                details={"new_prefix": new_key["prefix"]},
            )
        )

        st.success("New key created. Copy it now; the old key remains pending revocation.")
        st.session_state.pop(_ROTATE_STATE_KEY, None)


def _render_one_time_modal() -> None:
    modal_state = st.session_state.get(_MODAL_STATE_KEY)
    if not modal_state:
        return

    st.warning("Copy this key now. It will not be shown again.")
    st.code(modal_state["full_key"])

    copied = st.checkbox("I have copied this key", key="api_key_acknowledged")
    if st.button("Close", disabled=not copied):
        if not copied:
            st.error("Confirm you have copied the key before closing.")
            return
        st.session_state.pop(_MODAL_STATE_KEY, None)
        st.session_state.pop("api_key_acknowledged", None)


def _validate_create_input(
    name: str,
    scopes: dict[str, bool],
    expires_at: datetime | None = None,
) -> str | None:
    if not name or len(name.strip()) < 3:
        return "Name must be at least 3 characters."
    if len(name.strip()) > 50:
        return "Name must be 50 characters or fewer."
    if not any(scopes.values()):
        return "Select at least one scope."
    if expires_at is not None and expires_at <= datetime.now(UTC):
        return "Expiry date must be in the future."
    return None


def _selected_scopes(scopes: dict[str, bool]) -> list[str]:
    return [scope for scope, enabled in scopes.items() if enabled]


def _compute_status(key: dict[str, Any]) -> tuple[str, str]:
    now = datetime.now(UTC)
    pending: set[str] = st.session_state.get(_PENDING_REVOKE_KEY, set())

    expires_at = key.get("expires_at")
    revoked_at = key.get("revoked_at")

    if revoked_at:
        return "Expired", "gray"
    if expires_at and isinstance(expires_at, datetime) and expires_at < now:
        return "Expired", "gray"
    if key["key_prefix"] in pending:
        return "Pending Revocation", "orange"
    return "Active", "green"


def _normalize_scopes(scopes: Any) -> list[str]:
    if scopes is None:
        return []
    if isinstance(scopes, dict):
        return [name for name, enabled in scopes.items() if enabled]
    if isinstance(scopes, list | tuple | set):
        return [str(value) for value in scopes]
    return []


def _format_ts(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _create_key_sync(
    db_pool: Any,
    user_id: str,
    name: str,
    scopes: dict[str, bool],
    expires_at: datetime | None,
) -> dict[str, Any] | None:
    async def _create() -> dict[str, Any] | None:
        full_key, prefix, salt = generate_api_key()
        key_hash = hash_api_key(full_key, salt)
        scopes_model = ApiKeyScopes(**scopes)
        scope_list = [name for name, enabled in scopes_model.model_dump().items() if enabled]

        async with acquire_connection(db_pool) as conn:
            await conn.execute(
                """
                INSERT INTO api_keys (user_id, name, key_hash, key_salt, key_prefix, scopes, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (user_id, name, key_hash, salt, prefix, scope_list, expires_at),
            )

        return {
            "full_key": full_key,
            "prefix": prefix,
        }

    return run_async(_create())


def _list_keys_sync(db_pool: Any, user_id: str) -> list[dict[str, Any]]:
    async def _list() -> list[dict[str, Any]]:
        query = """
        SELECT id, name, key_prefix, scopes, expires_at, last_used_at, created_at, revoked_at
        FROM api_keys WHERE user_id = %s ORDER BY created_at DESC
        """
        async with acquire_connection(db_pool) as conn:
            cursor = await conn.execute(query, (user_id,))
            rows = await cursor.fetchall()

        normalized: list[dict[str, Any]] = []
        for row in rows or []:
            if isinstance(row, dict):
                normalized.append(row)
            else:
                (
                    key_id,
                    name,
                    key_prefix,
                    scopes,
                    expires_at,
                    last_used_at,
                    created_at,
                    revoked_at,
                ) = row
                normalized.append(
                    {
                        "id": key_id,
                        "name": name,
                        "key_prefix": key_prefix,
                        "scopes": scopes,
                        "expires_at": expires_at,
                        "last_used_at": last_used_at,
                        "created_at": created_at,
                        "revoked_at": revoked_at,
                    }
                )
        return normalized

    return run_async(_list(), timeout=_DB_OPERATION_TIMEOUT_SECONDS) or []


def _revoke_key_sync(
    db_pool: Any,
    user_id: str,
    prefix: str,
    redis_client: Any,  # Ignored - kept for API compatibility, see _async_redis() docstring
) -> bool:
    """Revoke an API key and add to Redis blacklist.

    Note: The redis_client parameter is ignored. We create a fresh async Redis
    client inside this coroutine because async clients bind to the event loop
    at first use, and run_async() creates a new loop per call.
    """

    async def _revoke() -> bool:
        async with acquire_connection(db_pool) as conn:
            cursor = await conn.execute(
                "UPDATE api_keys SET revoked_at = NOW() WHERE key_prefix = %s AND user_id = %s",
                (prefix, user_id),
            )

        rowcount = getattr(cursor, "rowcount", None)
        if rowcount is not None and rowcount == 0:
            logger.warning("api_key_revoke_noop", extra={"prefix": prefix, "user_id": user_id})
            return False

        # Create fresh Redis client inside async context (see _async_redis docstring)
        async with _async_redis() as rclient:
            if rclient:
                try:
                    await rclient.setex(f"api_key_revoked:{prefix}", REVOKED_KEY_CACHE_TTL, "1")
                except RedisError as exc:  # pragma: no cover - defensive logging
                    logger.warning(
                        "redis_blacklist_failed", extra={"prefix": prefix, "error": str(exc)}
                    )
        return True

    result = run_async(_revoke(), timeout=_DB_OPERATION_TIMEOUT_SECONDS)
    return bool(result)


__all__ = ["render_api_key_manager"]
