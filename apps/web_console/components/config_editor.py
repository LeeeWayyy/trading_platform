"""Streamlit component for system configuration management.

Provides admin-only editing of trading hours, position limits, and
system defaults with cache-aware persistence and audit logging.
"""

from __future__ import annotations

import logging
from datetime import time
from decimal import Decimal
from typing import Any, TypeVar

import streamlit as st
from pydantic import BaseModel, Field, ValidationError, ValidationInfo, field_validator
from redis.exceptions import RedisError

from apps.web_console.auth.audit_log import AuditLogger
from apps.web_console.components.csrf_protection import generate_csrf_token, verify_csrf_token
from libs.common.async_utils import run_async
from libs.web_console_auth.db import acquire_connection
from libs.web_console_auth.gateway_auth import AuthenticatedUser
from libs.web_console_auth.permissions import Permission, has_permission

logger = logging.getLogger(__name__)

_T = TypeVar("_T")
_ConfigT = TypeVar("_ConfigT", bound=BaseModel)

CONFIG_CACHE_TTL_SECONDS = 300
CONFIG_KEY_TRADING_HOURS = "trading_hours"
CONFIG_KEY_POSITION_LIMITS = "position_limits"
CONFIG_KEY_SYSTEM_DEFAULTS = "system_defaults"


class TradingHoursConfig(BaseModel):
    """Trading hours and session flags."""

    market_open: time = time(9, 30)
    market_close: time = time(16, 0)
    pre_market_enabled: bool = False
    after_hours_enabled: bool = False

    @field_validator("market_close")
    @classmethod
    def close_after_open(cls, v: time, info: ValidationInfo) -> time:
        if "market_open" in info.data and v <= info.data["market_open"]:
            raise ValueError("market_close must be after market_open")
        return v


class PositionLimitsConfig(BaseModel):
    """Per-symbol and aggregate position limits."""

    max_position_per_symbol: int = Field(default=1000, ge=1, le=100000)
    max_notional_total: Decimal = Field(
        default=Decimal("100000"), ge=Decimal("1000"), le=Decimal("10000000")
    )
    max_open_orders: int = Field(default=10, ge=1, le=1000)


class SystemDefaultsConfig(BaseModel):
    """Global system safety defaults."""

    dry_run: bool = True
    circuit_breaker_enabled: bool = True
    drawdown_threshold: Decimal = Field(
        default=Decimal("0.05"), ge=Decimal("0.01"), le=Decimal("0.50")
    )


async def get_config(
    config_key: str,
    db_pool: Any,
    redis_client: Any,
    config_class: type[_ConfigT],
) -> _ConfigT:
    """Load config with cache-first pattern.

    Falls back to database on cache miss and returns model defaults when no
    record exists. Redis errors are treated as cache misses to keep the UI
    responsive.
    """

    cache_key = f"system_config:{config_key}"
    if redis_client:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                try:
                    return config_class.model_validate_json(cached)
                except (ValidationError, ValueError) as exc:
                    logger.warning(
                        "config_cache_corrupt",
                        extra={"config_key": config_key, "error": str(exc)},
                    )
                    try:
                        await redis_client.delete(cache_key)
                    except RedisError:
                        logger.warning(
                            "config_cache_delete_failed", extra={"config_key": config_key}
                        )
        except RedisError:
            logger.warning("config_cache_get_failed", extra={"config_key": config_key})

    async with acquire_connection(db_pool) as conn:
        row = await _fetchone(
            conn,
            "SELECT config_value FROM system_config WHERE config_key = %s",
            (config_key,),
        )

        if row:
            raw_value = row.get("config_value") if isinstance(row, dict) else row[0]
            try:
                config = config_class.model_validate(raw_value)
            except ValidationError as exc:  # pragma: no cover - defensive path
                logger.warning(
                    "config_validation_failed", extra={"config_key": config_key, "error": str(exc)}
                )
                return config_class()

            if redis_client:
                try:
                    await redis_client.setex(
                        cache_key, CONFIG_CACHE_TTL_SECONDS, config.model_dump_json()
                    )
                except RedisError:
                    logger.warning("config_cache_set_failed", extra={"config_key": config_key})
            return config

    return config_class()


async def save_config(
    *,
    config_key: str,
    config_value: BaseModel,
    config_type: str,
    user: AuthenticatedUser,
    db_pool: Any,
    audit_logger: AuditLogger,
    redis_client: Any,
) -> bool:
    """Save config with cache invalidation, DB upsert, and audit logging."""

    cache_key = f"system_config:{config_key}"

    if redis_client:
        try:
            await redis_client.delete(cache_key)
        except RedisError:
            logger.warning("config_cache_delete_failed", extra={"config_key": config_key})

    payload = config_value.model_dump(mode="json")

    try:
        async with acquire_connection(db_pool) as conn:
            await conn.execute(
                """
                INSERT INTO system_config (config_key, config_value, config_type, updated_by, updated_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (config_key) DO UPDATE SET
                    config_value = EXCLUDED.config_value,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW()
                """,
                (config_key, payload, config_type, user.user_id),
            )
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("config_save_failed", extra={"config_key": config_key, "error": str(exc)})
        return False

    try:
        await audit_logger.log_action(
            user_id=user.user_id,
            action="config_saved",
            resource_type="system_config",
            resource_id=config_key,
            outcome="success",
            details={"config_type": config_type},
        )
    except Exception as exc:  # pragma: no cover - audit is best-effort
        logger.warning("config_audit_failed", extra={"config_key": config_key, "error": str(exc)})

    return True


def render_config_editor(
    user: AuthenticatedUser,
    db_pool: Any,
    audit_logger: AuditLogger,
    redis_client: Any,
) -> None:
    """Render the system configuration editor UI."""

    if not has_permission(user, Permission.MANAGE_SYSTEM_CONFIG):
        st.error("Permission denied: MANAGE_SYSTEM_CONFIG required")
        return

    st.title("System Configuration")
    st.caption("Admin-only settings for trading windows, limits, and defaults.")

    csrf_token = generate_csrf_token()

    trading_tab, limits_tab, defaults_tab = st.tabs(
        ["Trading Hours", "Position Limits", "System Defaults"]
    )

    with trading_tab:
        _render_trading_hours_form(
            csrf_token=csrf_token,
            user=user,
            db_pool=db_pool,
            audit_logger=audit_logger,
            redis_client=redis_client,
        )

    with limits_tab:
        _render_position_limits_form(
            csrf_token=csrf_token,
            user=user,
            db_pool=db_pool,
            audit_logger=audit_logger,
            redis_client=redis_client,
        )

    with defaults_tab:
        _render_system_defaults_form(
            csrf_token=csrf_token,
            user=user,
            db_pool=db_pool,
            audit_logger=audit_logger,
            redis_client=redis_client,
        )


def _render_trading_hours_form(
    *,
    csrf_token: str,
    user: AuthenticatedUser,
    db_pool: Any,
    audit_logger: AuditLogger,
    redis_client: Any,
) -> None:
    config = run_async(
        get_config(CONFIG_KEY_TRADING_HOURS, db_pool, redis_client, TradingHoursConfig)
    )

    with st.form("trading_hours_form"):
        market_open = st.time_input("Market open", value=config.market_open)
        market_close = st.time_input("Market close", value=config.market_close)
        pre_market_enabled = st.checkbox("Enable pre-market", value=config.pre_market_enabled)
        after_hours_enabled = st.checkbox("Enable after-hours", value=config.after_hours_enabled)

        submitted_csrf = st.text_input(
            "csrf", value=csrf_token, type="password", label_visibility="hidden"
        )
        submitted = st.form_submit_button("Save Trading Hours", type="primary")

    if not submitted:
        return

    if not verify_csrf_token(submitted_csrf):
        st.error("Invalid session. Refresh and try again.")
        return

    try:
        updated = TradingHoursConfig(
            market_open=market_open,
            market_close=market_close,
            pre_market_enabled=pre_market_enabled,
            after_hours_enabled=after_hours_enabled,
        )
    except ValidationError as exc:
        st.error(_format_validation_error(exc))
        return

    success = run_async(
        save_config(
            config_key=CONFIG_KEY_TRADING_HOURS,
            config_value=updated,
            config_type=CONFIG_KEY_TRADING_HOURS,
            user=user,
            db_pool=db_pool,
            audit_logger=audit_logger,
            redis_client=redis_client,
        )
    )

    _notify_result(success, "Trading hours updated.")


def _render_position_limits_form(
    *,
    csrf_token: str,
    user: AuthenticatedUser,
    db_pool: Any,
    audit_logger: AuditLogger,
    redis_client: Any,
) -> None:
    config = run_async(
        get_config(CONFIG_KEY_POSITION_LIMITS, db_pool, redis_client, PositionLimitsConfig)
    )

    with st.form("position_limits_form"):
        max_position_per_symbol = st.number_input(
            "Max position per symbol",
            min_value=1,
            max_value=100000,
            value=int(config.max_position_per_symbol),
            step=1,
        )
        max_notional_total_raw = st.number_input(
            "Max total notional ($)",
            min_value=float(Decimal("1000")),
            max_value=float(Decimal("10000000")),
            value=float(config.max_notional_total),
            step=float(Decimal("1000")),
        )
        max_open_orders = st.number_input(
            "Max open orders",
            min_value=1,
            max_value=1000,
            value=int(config.max_open_orders),
            step=1,
        )

        submitted_csrf = st.text_input(
            "csrf", value=csrf_token, type="password", label_visibility="hidden"
        )
        submitted = st.form_submit_button("Save Limits", type="primary")

    if not submitted:
        return

    if not verify_csrf_token(submitted_csrf):
        st.error("Invalid session. Refresh and try again.")
        return

    try:
        updated = PositionLimitsConfig(
            max_position_per_symbol=int(max_position_per_symbol),
            max_notional_total=Decimal(str(max_notional_total_raw)),
            max_open_orders=int(max_open_orders),
        )
    except ValidationError as exc:
        st.error(_format_validation_error(exc))
        return

    success = run_async(
        save_config(
            config_key=CONFIG_KEY_POSITION_LIMITS,
            config_value=updated,
            config_type=CONFIG_KEY_POSITION_LIMITS,
            user=user,
            db_pool=db_pool,
            audit_logger=audit_logger,
            redis_client=redis_client,
        )
    )

    _notify_result(success, "Position limits saved.")


def _render_system_defaults_form(
    *,
    csrf_token: str,
    user: AuthenticatedUser,
    db_pool: Any,
    audit_logger: AuditLogger,
    redis_client: Any,
) -> None:
    config = run_async(
        get_config(CONFIG_KEY_SYSTEM_DEFAULTS, db_pool, redis_client, SystemDefaultsConfig)
    )

    with st.form("system_defaults_form"):
        dry_run = st.checkbox("Dry run mode", value=config.dry_run)
        circuit_breaker_enabled = st.checkbox(
            "Circuit breaker enabled", value=config.circuit_breaker_enabled
        )
        drawdown_threshold_raw = st.number_input(
            "Drawdown threshold",
            min_value=float(Decimal("0.01")),
            max_value=float(Decimal("0.50")),
            value=float(config.drawdown_threshold),
            step=float(Decimal("0.01")),
            format="%.2f",
        )

        submitted_csrf = st.text_input(
            "csrf", value=csrf_token, type="password", label_visibility="hidden"
        )
        submitted = st.form_submit_button("Save Defaults", type="primary")

    if not submitted:
        return

    if not verify_csrf_token(submitted_csrf):
        st.error("Invalid session. Refresh and try again.")
        return

    try:
        updated = SystemDefaultsConfig(
            dry_run=bool(dry_run),
            circuit_breaker_enabled=bool(circuit_breaker_enabled),
            drawdown_threshold=Decimal(str(drawdown_threshold_raw)),
        )
    except ValidationError as exc:
        st.error(_format_validation_error(exc))
        return

    success = run_async(
        save_config(
            config_key=CONFIG_KEY_SYSTEM_DEFAULTS,
            config_value=updated,
            config_type=CONFIG_KEY_SYSTEM_DEFAULTS,
            user=user,
            db_pool=db_pool,
            audit_logger=audit_logger,
            redis_client=redis_client,
        )
    )

    _notify_result(success, "System defaults updated.")


async def _fetchone(conn: Any, query: str, params: tuple[Any, ...]) -> Any:
    """Compatibility helper handling connection or cursor fetchone styles."""

    if hasattr(conn, "fetchone"):
        return await conn.fetchone(query, params)

    cursor = await conn.execute(query, params)
    fetchone = getattr(cursor, "fetchone", None)
    if fetchone:
        return await fetchone()
    return None


def _notify_result(success: bool, message: str) -> None:
    notifier = getattr(st, "toast", None)
    if success:
        if callable(notifier):
            notifier(message)
        st.success(message)
    else:
        if callable(notifier):
            notifier("Save failed. Please retry.")
        st.error("Save failed. Please retry.")


def _format_validation_error(exc: ValidationError) -> str:
    messages = [err.get("msg", "Invalid input") for err in exc.errors()]
    return "; ".join(messages)


__all__ = [
    "render_config_editor",
    "get_config",
    "save_config",
    "TradingHoursConfig",
    "PositionLimitsConfig",
    "SystemDefaultsConfig",
]
