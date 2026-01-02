# apps/web_console_ng/core/state_manager.py
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.core.redis_ha import get_redis_store

if TYPE_CHECKING:
    from apps.web_console_ng.core.redis_ha import HARedisStore, SimpleRedisStore

logger = logging.getLogger(__name__)


class TradingJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder for datetime, date, and Decimal types.

    UTC REQUIREMENT: All datetime objects should be UTC-aware before serialization.
    If a naive datetime is encountered, we assume it's UTC and append 'Z'.
    """

    def default(self, obj: object) -> Any:
        if isinstance(obj, datetime):
            # Ensure UTC-aware: if naive, assume UTC and add 'Z' suffix
            if obj.tzinfo is None:
                # Naive datetime - assume UTC, serialize with Z suffix
                return {"__type__": "datetime", "value": obj.isoformat() + "Z"}
            else:
                # Timezone-aware - serialize as-is (isoformat includes offset)
                return {"__type__": "datetime", "value": obj.isoformat()}
        if isinstance(obj, date):
            return {"__type__": "date", "value": obj.isoformat()}
        if isinstance(obj, Decimal):
            return {"__type__": "Decimal", "value": str(obj)}
        return super().default(obj)


def trading_json_object_hook(dct: dict[str, Any]) -> Any:
    """Custom JSON decoder for datetime, date, and Decimal types.

    Python 3.11+ supports 'Z' suffix natively in fromisoformat().
    """
    if "__type__" in dct:
        if dct["__type__"] == "datetime":
            # Python 3.11+ handles 'Z' suffix natively
            return datetime.fromisoformat(dct["value"])
        if dct["__type__"] == "date":
            return date.fromisoformat(dct["value"])
        if dct["__type__"] == "Decimal":
            return Decimal(dct["value"])
    return dct


trading_json_decoder: Callable[[dict[str, Any]], Any] = trading_json_object_hook


class UserStateManager:
    """
    Manages user state with Redis persistence for failover recovery.

    SECURITY: Only instantiate AFTER successful authentication to prevent DoS.
    Unauthenticated connections must NOT create Redis state entries.

    State categories:
    - UI state: ephemeral, re-rendered on reconnect
    - Critical state: persisted in Redis (preferences, pending forms, filters)
    - API data: fetched fresh from backend on reconnect
    """

    STATE_KEY_PREFIX = "user_state:"
    STATE_TTL = 86400  # 24 hours

    def __init__(
        self,
        user_id: str,
        role: str | None = None,
        strategies: list[str] | None = None,
    ) -> None:
        self.user_id = user_id
        self.role = role
        self.strategies = strategies or []
        self.redis: HARedisStore | SimpleRedisStore = get_redis_store()
        self.state_key = f"{self.STATE_KEY_PREFIX}{user_id}"

    async def save_critical_state(self, state: dict[str, Any]) -> None:
        """
        Persist critical state for failover recovery.

        Only save state that improves UX on reconnection:
        - User preferences (theme, layout)
        - Dashboard filters
        - Pending form data (unsaved)

        Uses custom JSON encoder for datetime/Decimal types.
        """
        state_with_meta = {
            "data": state,
            "saved_at": datetime.now(UTC).isoformat(),  # UTC-aware timestamp
            "version": 1,
        }
        master = await self.redis.get_master()
        await master.setex(
            self.state_key, self.STATE_TTL, json.dumps(state_with_meta, cls=TradingJSONEncoder)
        )

    async def restore_state(self) -> dict[str, Any]:
        """Restore state after reconnection. Uses custom decoder for types.

        CRITICAL: Read from MASTER, not replica, to avoid stale data during reconnection.
        Replica lag can cause restored state to miss recent saves (e.g., pending forms).
        """
        master = await self.redis.get_master()
        data = await master.get(self.state_key)

        if not data:
            return {}

        try:
            parsed = json.loads(data, object_hook=trading_json_decoder)
            return parsed.get("data", {})  # type: ignore[no-any-return]
        except json.JSONDecodeError as e:
            # Log corrupted state for debugging (don't expose raw data, could contain PII)
            logger.warning(
                "restore_state: corrupted state detected, returning empty",
                extra={"user_id": self.user_id, "error": str(e)},
            )
            # Increment metric for observability
            try:
                from apps.web_console_ng.core.metrics import record_state_save_error

                record_state_save_error("json_decode_error")
            except ImportError:
                pass  # Metrics not available yet
            return {}

    async def save_preferences(self, key: str, value: Any) -> None:
        """
        Update a single preference (merge into existing state).

        Uses Redis WATCH/MULTI/EXEC for atomic read-modify-write.
        Retries on WatchError (concurrent modification).
        """
        from redis.exceptions import WatchError

        master = await self.redis.get_master()

        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with master.pipeline() as pipe:
                    await pipe.watch(self.state_key)

                    # READ within WATCH
                    data = await pipe.get(self.state_key)
                    state = {}
                    if data:
                        try:
                            parsed = json.loads(data, object_hook=trading_json_object_hook)
                            state = parsed.get("data", {})
                        except json.JSONDecodeError:
                            pass

                    # MODIFY
                    preferences = state.get("preferences", {})
                    preferences[key] = value
                    state["preferences"] = preferences

                    # WRITE atomically
                    state_with_meta = {
                        "data": state,
                        "saved_at": datetime.now(UTC).isoformat(),
                        "version": 1,
                    }
                    pipe.multi()
                    pipe.setex(
                        self.state_key,
                        self.STATE_TTL,
                        json.dumps(state_with_meta, cls=TradingJSONEncoder),
                    )
                    await pipe.execute()
                    return  # Success
            except WatchError:
                if attempt == max_retries - 1:
                    logger.error(
                        "save_preferences failed after retries",
                        extra={"user_id": self.user_id, "key": key},
                    )
                    raise  # Last attempt failed
                logger.warning(
                    "save_preferences retry due to WatchError",
                    extra={"user_id": self.user_id, "key": key, "attempt": attempt + 1},
                )
                continue  # Retry

    async def update_preference(self, key: str, value: Any) -> None:
        """Backward-compatible wrapper for save_preferences."""
        await self.save_preferences(key, value)

    async def save_pending_form(
        self,
        form_id: str,
        form_data: dict[str, Any],
        client_order_id: str | None = None,
    ) -> None:
        """
        Save pending form data (for recovery after disconnect).

        TRADING SAFETY: For order-related forms, pass client_order_id to enable
        idempotent re-submission. The backend (Execution Gateway) will reject
        duplicate client_order_ids, preventing double-execution on reconnection.

        Args:
            form_id: Unique identifier for the form
            form_data: Form field values
            client_order_id: Optional pre-generated UUID for order idempotency.
                            MUST be generated before first submission attempt.

        Uses Redis WATCH/MULTI/EXEC for atomic read-modify-write.
        """
        from redis.exceptions import WatchError

        master = await self.redis.get_master()

        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with master.pipeline() as pipe:
                    await pipe.watch(self.state_key)

                    data = await pipe.get(self.state_key)
                    state = {}
                    if data:
                        try:
                            parsed = json.loads(data, object_hook=trading_json_object_hook)
                            state = parsed.get("data", {})
                        except json.JSONDecodeError:
                            pass

                    pending_forms = state.get("pending_forms", {})
                    pending_forms[form_id] = {
                        "data": form_data,
                        "saved_at": datetime.now(UTC).isoformat(),
                        # TRADING SAFETY: Include client_order_id for idempotent re-submission
                        "client_order_id": client_order_id,
                        # Use sha256 for stable hash (Python's hash() is process-randomized)
                        "original_data_hash": hashlib.sha256(
                            json.dumps(form_data, sort_keys=True).encode("utf-8")
                        ).hexdigest(),
                    }
                    state["pending_forms"] = pending_forms

                    state_with_meta = {
                        "data": state,
                        "saved_at": datetime.now(UTC).isoformat(),
                        "version": 1,
                    }
                    pipe.multi()
                    pipe.setex(
                        self.state_key,
                        self.STATE_TTL,
                        json.dumps(state_with_meta, cls=TradingJSONEncoder),
                    )
                    await pipe.execute()
                    return
            except WatchError:
                if attempt == max_retries - 1:
                    logger.error(
                        "save_pending_form failed after retries",
                        extra={"user_id": self.user_id, "form_id": form_id},
                    )
                    raise
                logger.warning(
                    "save_pending_form retry due to WatchError",
                    extra={"user_id": self.user_id, "form_id": form_id, "attempt": attempt + 1},
                )
                continue

    async def clear_pending_form(self, form_id: str) -> None:
        """
        Clear pending form after successful submission.

        Uses Redis WATCH/MULTI/EXEC for atomic read-modify-write.
        """
        from redis.exceptions import WatchError

        master = await self.redis.get_master()

        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with master.pipeline() as pipe:
                    await pipe.watch(self.state_key)

                    data = await pipe.get(self.state_key)
                    state = {}
                    if data:
                        try:
                            parsed = json.loads(data, object_hook=trading_json_object_hook)
                            state = parsed.get("data", {})
                        except json.JSONDecodeError:
                            pass

                    pending_forms = state.get("pending_forms", {})
                    pending_forms.pop(form_id, None)
                    state["pending_forms"] = pending_forms

                    state_with_meta = {
                        "data": state,
                        "saved_at": datetime.now(UTC).isoformat(),
                        "version": 1,
                    }
                    pipe.multi()
                    pipe.setex(
                        self.state_key,
                        self.STATE_TTL,
                        json.dumps(state_with_meta, cls=TradingJSONEncoder),
                    )
                    await pipe.execute()
                    return
            except WatchError:
                if attempt == max_retries - 1:
                    logger.error(
                        "clear_pending_form failed after retries",
                        extra={"user_id": self.user_id, "form_id": form_id},
                    )
                    raise
                logger.warning(
                    "clear_pending_form retry due to WatchError",
                    extra={"user_id": self.user_id, "form_id": form_id, "attempt": attempt + 1},
                )
                continue

    async def on_reconnect(self, ui_context: Any) -> dict[str, Any]:
        """
        Called when user reconnects after WS drop.

        Returns data needed to restore UI.
        """
        # 1. Load persisted state
        state = await self.restore_state()

        # 2. Fetch fresh API data (use only existing AsyncTradingClient methods)
        # Pass full auth context for production with INTERNAL_TOKEN_SECRET
        client = AsyncTradingClient.get()
        api_data = {
            "positions": await client.fetch_positions(
                self.user_id, role=self.role, strategies=self.strategies
            ),
            "kill_switch": await client.fetch_kill_switch_status(
                self.user_id, role=self.role, strategies=self.strategies
            ),
            # Note: circuit_breaker endpoint not yet available in execution_gateway
        }
        # Note: Add fetch_open_orders() to AsyncTradingClient if order display needed

        # 3. Return combined data for UI restoration
        return {
            "preferences": state.get("preferences", {}),
            "filters": state.get("filters", {}),
            "pending_forms": state.get("pending_forms", {}),
            "api_data": api_data,
        }

    async def delete_state(self) -> None:
        """Delete all state (on logout)."""
        master = await self.redis.get_master()
        await master.delete(self.state_key)


class _StateManagerRegistry:
    """Process-level state manager registry for shutdown hooks."""

    def __init__(self) -> None:
        self._closed = False

    async def close(self) -> None:
        """Close hook used by app shutdown (no-op for now)."""
        self._closed = True


_state_manager_instance: _StateManagerRegistry | None = None


def get_state_manager() -> _StateManagerRegistry:
    """Return singleton state manager registry."""
    global _state_manager_instance
    if _state_manager_instance is None:
        _state_manager_instance = _StateManagerRegistry()
    return _state_manager_instance


__all__ = [
    "TradingJSONEncoder",
    "trading_json_object_hook",
    "trading_json_decoder",
    "UserStateManager",
    "get_state_manager",
]
