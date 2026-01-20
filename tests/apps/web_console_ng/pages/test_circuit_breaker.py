"""Unit tests for circuit_breaker page."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from typing import Any

import pytest

os.environ["WEB_CONSOLE_NG_DEBUG"] = "true"
os.environ.setdefault("NICEGUI_STORAGE_SECRET", "test-secret")

from apps.web_console_ng.pages import circuit_breaker as cb_module
from libs.trading.risk_management.breaker import CircuitBreakerState
from tests.apps.web_console_ng.pages.ui_test_utils import DummyUI


class DummyService:
    """Mock service for testing."""

    def __init__(
        self,
        status: dict[str, Any] | None = None,
        history: list[dict[str, Any]] | None = None,
        status_error: Exception | None = None,
        history_error: Exception | None = None,
        trip_error: Exception | None = None,
        reset_error: Exception | None = None,
    ) -> None:
        self.trip_calls: list[tuple[str, dict[str, Any]]] = []
        self.reset_calls: list[tuple[str, dict[str, Any]]] = []
        self._status = status or {"state": "OPEN", "trip_count_today": 0}
        self._history = history or []
        self._status_error = status_error
        self._history_error = history_error
        self._trip_error = trip_error
        self._reset_error = reset_error

    def get_status(self) -> dict[str, Any]:
        if self._status_error:
            raise self._status_error
        return self._status

    def get_history(self, _limit: int) -> list[dict[str, Any]]:
        if self._history_error:
            raise self._history_error
        return self._history

    def trip(self, reason: str, user: dict[str, Any], acknowledged: bool = True) -> None:
        if self._trip_error:
            raise self._trip_error
        self.trip_calls.append((reason, user))

    def reset(self, reason: str, user: dict[str, Any], acknowledged: bool = True) -> None:
        if self._reset_error:
            raise self._reset_error
        self.reset_calls.append((reason, user))


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    ui = DummyUI()
    monkeypatch.setattr(cb_module, "ui", ui)
    return ui


@pytest.fixture()
def io_bound_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _io_bound(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(cb_module.run, "io_bound", _io_bound)


def test_get_cb_service_returns_none_when_redis_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dummy_app = SimpleNamespace(storage=SimpleNamespace())
    monkeypatch.setattr(cb_module, "app", dummy_app)
    monkeypatch.setattr(cb_module, "get_sync_db_pool", lambda: None)

    def _raise_redis():
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(cb_module, "get_sync_redis_client", _raise_redis)

    assert cb_module._get_cb_service() is None


def test_get_cb_service_caches_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy_app = SimpleNamespace(storage=SimpleNamespace())
    monkeypatch.setattr(cb_module, "app", dummy_app)
    monkeypatch.setattr(cb_module, "get_sync_db_pool", lambda: None)
    monkeypatch.setattr(cb_module, "get_sync_redis_client", lambda: object())

    dummy_cb_module = SimpleNamespace(
        CircuitBreakerService=lambda *_args, **_kwargs: DummyService()
    )
    monkeypatch.setitem(sys.modules, "libs.web_console_services.cb_service", dummy_cb_module)

    first = cb_module._get_cb_service()
    second = cb_module._get_cb_service()

    assert first is second


@pytest.mark.asyncio()
async def test_trip_and_reset_callbacks(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    dummy_service = DummyService()

    monkeypatch.setattr(cb_module, "_get_cb_service", lambda: dummy_service)
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    def _has_permission(_user, permission):
        return True

    monkeypatch.setattr(cb_module, "has_permission", _has_permission)

    class DummyLifecycle:
        async def register_cleanup_callback(self, _client_id, _cb):
            return None

    monkeypatch.setattr(cb_module.ClientLifecycleManager, "get", lambda: DummyLifecycle())

    dummy_ui.context.client.storage["client_id"] = "client-1"

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    trip_button = next(b for b in dummy_ui.buttons if b.text == "Trip Circuit Breaker")
    reset_button = next(b for b in dummy_ui.buttons if b.text == "Confirm Reset")

    trip_select = dummy_ui.selects[0]
    trip_select.value = "Other"
    assert trip_select.on_value_change_cb is not None
    trip_select.on_value_change_cb()

    reason_input = next(
        e for e in dummy_ui.elements if getattr(e, "label", None) == "Custom reason"
    )
    reason_input.value = "manual check"

    assert trip_button.on_click_cb is not None
    await trip_button.on_click_cb()
    assert dummy_service.trip_calls

    min_len = cb_module.config.MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH
    reset_label = f"Reset Reason (minimum {min_len} characters)"
    reset_textarea = next(e for e in dummy_ui.elements if getattr(e, "label", "") == reset_label)
    reset_textarea.value = "reset reason long enough"
    checkbox = next(
        e
        for e in dummy_ui.elements
        if getattr(e, "label", "") == "I acknowledge that resetting will allow trading to resume"
    )
    checkbox.value = True

    assert reset_button.on_click_cb is not None
    await reset_button.on_click_cb()
    assert dummy_service.reset_calls


@pytest.mark.asyncio()
async def test_feature_flag_disabled(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test page shows message when feature flag is disabled."""
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", False)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    # Check that feature disabled message is displayed
    labels = [text for text, _ in dummy_ui.labels]
    assert any("feature is disabled" in label.lower() for label in labels)


@pytest.mark.asyncio()
async def test_permission_denied(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test page shows permission denied when VIEW_CIRCUIT_BREAKER missing."""
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    def _deny_permission(_user: dict[str, Any], permission: Any) -> bool:
        from libs.platform.web_console_auth.permissions import Permission

        return permission != Permission.VIEW_CIRCUIT_BREAKER

    monkeypatch.setattr(cb_module, "has_permission", _deny_permission)

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    labels = [text for text, _ in dummy_ui.labels]
    assert any("permission denied" in label.lower() for label in labels)


@pytest.mark.asyncio()
async def test_redis_unavailable(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test page shows error when Redis/cb_service is unavailable."""
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})
    monkeypatch.setattr(cb_module, "_get_cb_service", lambda: None)

    def _has_permission(_user: dict[str, Any], permission: Any) -> bool:
        return True

    monkeypatch.setattr(cb_module, "has_permission", _has_permission)

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    labels = [text for text, _ in dummy_ui.labels]
    assert any("unavailable" in label.lower() for label in labels)


@pytest.mark.asyncio()
async def test_fetch_status_error(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test status_section displays error when fetch_status fails."""
    dummy_service = DummyService(status_error=RuntimeError("redis error"))

    monkeypatch.setattr(cb_module, "_get_cb_service", lambda: dummy_service)
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    def _has_permission(_user: dict[str, Any], permission: Any) -> bool:
        return True

    monkeypatch.setattr(cb_module, "has_permission", _has_permission)

    class DummyLifecycle:
        async def register_cleanup_callback(self, _client_id: str, _cb: Any) -> None:
            return None

    monkeypatch.setattr(cb_module.ClientLifecycleManager, "get", lambda: DummyLifecycle())
    dummy_ui.context.client.storage["client_id"] = "client-1"

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    labels = [text for text, _ in dummy_ui.labels]
    assert any("cannot retrieve status" in label.lower() for label in labels)


@pytest.mark.asyncio()
async def test_fetch_history_runtime_error(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test history fetch handles RuntimeError gracefully."""
    dummy_service = DummyService(history_error=RuntimeError("history fetch error"))

    monkeypatch.setattr(cb_module, "_get_cb_service", lambda: dummy_service)
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    def _has_permission(_user: dict[str, Any], permission: Any) -> bool:
        return True

    monkeypatch.setattr(cb_module, "has_permission", _has_permission)

    class DummyLifecycle:
        async def register_cleanup_callback(self, _client_id: str, _cb: Any) -> None:
            return None

    monkeypatch.setattr(cb_module.ClientLifecycleManager, "get", lambda: DummyLifecycle())
    dummy_ui.context.client.storage["client_id"] = "client-1"

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    # Page should render without crashing, history is empty
    labels = [text for text, _ in dummy_ui.labels]
    assert any("no trip history" in label.lower() for label in labels)


@pytest.mark.asyncio()
async def test_fetch_history_value_error(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test history fetch handles ValueError gracefully."""
    dummy_service = DummyService(history_error=ValueError("invalid data"))

    monkeypatch.setattr(cb_module, "_get_cb_service", lambda: dummy_service)
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    def _has_permission(_user: dict[str, Any], permission: Any) -> bool:
        return True

    monkeypatch.setattr(cb_module, "has_permission", _has_permission)

    class DummyLifecycle:
        async def register_cleanup_callback(self, _client_id: str, _cb: Any) -> None:
            return None

    monkeypatch.setattr(cb_module.ClientLifecycleManager, "get", lambda: DummyLifecycle())
    dummy_ui.context.client.storage["client_id"] = "client-1"

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    # Page should render without crashing, history is empty
    labels = [text for text, _ in dummy_ui.labels]
    assert any("no trip history" in label.lower() for label in labels)


@pytest.mark.asyncio()
async def test_status_section_tripped_state(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test status section displays TRIPPED state correctly."""
    dummy_service = DummyService(
        status={
            "state": CircuitBreakerState.TRIPPED.value,
            "trip_reason": "DATA_STALE",
            "tripped_at": "2025-01-01T12:00:00Z",
            "trip_details": {"foo": "bar"},
            "trip_count_today": 2,
        }
    )

    monkeypatch.setattr(cb_module, "_get_cb_service", lambda: dummy_service)
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    def _has_permission(_user: dict[str, Any], permission: Any) -> bool:
        return True

    monkeypatch.setattr(cb_module, "has_permission", _has_permission)

    class DummyLifecycle:
        async def register_cleanup_callback(self, _client_id: str, _cb: Any) -> None:
            return None

    monkeypatch.setattr(cb_module.ClientLifecycleManager, "get", lambda: DummyLifecycle())
    dummy_ui.context.client.storage["client_id"] = "client-1"

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    labels = [text for text, _ in dummy_ui.labels]
    assert any("TRIPPED" in label for label in labels)
    assert any("DATA_STALE" in label for label in labels)


@pytest.mark.asyncio()
async def test_status_section_quiet_period_state(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test status section displays QUIET_PERIOD state correctly."""
    dummy_service = DummyService(
        status={
            "state": CircuitBreakerState.QUIET_PERIOD.value,
            "reset_at": "2025-01-01T12:05:00Z",
            "trip_count_today": 1,
        }
    )

    monkeypatch.setattr(cb_module, "_get_cb_service", lambda: dummy_service)
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    def _has_permission(_user: dict[str, Any], permission: Any) -> bool:
        return True

    monkeypatch.setattr(cb_module, "has_permission", _has_permission)

    class DummyLifecycle:
        async def register_cleanup_callback(self, _client_id: str, _cb: Any) -> None:
            return None

    monkeypatch.setattr(cb_module.ClientLifecycleManager, "get", lambda: DummyLifecycle())
    dummy_ui.context.client.storage["client_id"] = "client-1"

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    labels = [text for text, _ in dummy_ui.labels]
    assert any("QUIET_PERIOD" in label for label in labels)


@pytest.mark.asyncio()
async def test_status_section_unknown_state(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test status section displays unknown state correctly."""
    dummy_service = DummyService(status={"state": "UNKNOWN_STATE", "trip_count_today": 0})

    monkeypatch.setattr(cb_module, "_get_cb_service", lambda: dummy_service)
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    def _has_permission(_user: dict[str, Any], permission: Any) -> bool:
        return True

    monkeypatch.setattr(cb_module, "has_permission", _has_permission)

    class DummyLifecycle:
        async def register_cleanup_callback(self, _client_id: str, _cb: Any) -> None:
            return None

    monkeypatch.setattr(cb_module.ClientLifecycleManager, "get", lambda: DummyLifecycle())
    dummy_ui.context.client.storage["client_id"] = "client-1"

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    labels = [text for text, _ in dummy_ui.labels]
    assert any("UNKNOWN_STATE" in label for label in labels)


@pytest.mark.asyncio()
async def test_trip_empty_reason(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test trip with empty reason shows notification."""
    dummy_service = DummyService()

    monkeypatch.setattr(cb_module, "_get_cb_service", lambda: dummy_service)
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    def _has_permission(_user: dict[str, Any], permission: Any) -> bool:
        return True

    monkeypatch.setattr(cb_module, "has_permission", _has_permission)

    class DummyLifecycle:
        async def register_cleanup_callback(self, _client_id: str, _cb: Any) -> None:
            return None

    monkeypatch.setattr(cb_module.ClientLifecycleManager, "get", lambda: DummyLifecycle())
    dummy_ui.context.client.storage["client_id"] = "client-1"

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    # Set trip reason to "Other" with empty custom reason
    trip_select = dummy_ui.selects[0]
    trip_select.value = "Other"
    if trip_select.on_value_change_cb:
        trip_select.on_value_change_cb()

    reason_input = next(
        e for e in dummy_ui.elements if getattr(e, "label", None) == "Custom reason"
    )
    reason_input.value = ""

    trip_button = next(b for b in dummy_ui.buttons if b.text == "Trip Circuit Breaker")
    assert trip_button.on_click_cb is not None
    await trip_button.on_click_cb()

    assert any("provide a reason" in msg.lower() for msg, _ in dummy_ui.notifications)
    assert not dummy_service.trip_calls


@pytest.mark.asyncio()
async def test_trip_validation_error(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test trip handles ValidationError."""
    from libs.web_console_services.cb_service import ValidationError

    dummy_service = DummyService(trip_error=ValidationError("invalid"))

    monkeypatch.setattr(cb_module, "_get_cb_service", lambda: dummy_service)
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    def _has_permission(_user: dict[str, Any], permission: Any) -> bool:
        return True

    monkeypatch.setattr(cb_module, "has_permission", _has_permission)

    class DummyLifecycle:
        async def register_cleanup_callback(self, _client_id: str, _cb: Any) -> None:
            return None

    monkeypatch.setattr(cb_module.ClientLifecycleManager, "get", lambda: DummyLifecycle())
    dummy_ui.context.client.storage["client_id"] = "client-1"

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    trip_button = next(b for b in dummy_ui.buttons if b.text == "Trip Circuit Breaker")
    assert trip_button.on_click_cb is not None
    await trip_button.on_click_cb()

    assert any("validation error" in msg.lower() for msg, _ in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_trip_rbac_violation(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test trip handles RBACViolation."""
    from libs.web_console_services.cb_service import RBACViolation

    dummy_service = DummyService(trip_error=RBACViolation("no permission"))

    monkeypatch.setattr(cb_module, "_get_cb_service", lambda: dummy_service)
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    def _has_permission(_user: dict[str, Any], permission: Any) -> bool:
        return True

    monkeypatch.setattr(cb_module, "has_permission", _has_permission)

    class DummyLifecycle:
        async def register_cleanup_callback(self, _client_id: str, _cb: Any) -> None:
            return None

    monkeypatch.setattr(cb_module.ClientLifecycleManager, "get", lambda: DummyLifecycle())
    dummy_ui.context.client.storage["client_id"] = "client-1"

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    trip_button = next(b for b in dummy_ui.buttons if b.text == "Trip Circuit Breaker")
    assert trip_button.on_click_cb is not None
    await trip_button.on_click_cb()

    assert any("permission denied" in msg.lower() for msg, _ in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_trip_runtime_error(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test trip handles RuntimeError."""
    dummy_service = DummyService(trip_error=RuntimeError("service error"))

    monkeypatch.setattr(cb_module, "_get_cb_service", lambda: dummy_service)
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    def _has_permission(_user: dict[str, Any], permission: Any) -> bool:
        return True

    monkeypatch.setattr(cb_module, "has_permission", _has_permission)

    class DummyLifecycle:
        async def register_cleanup_callback(self, _client_id: str, _cb: Any) -> None:
            return None

    monkeypatch.setattr(cb_module.ClientLifecycleManager, "get", lambda: DummyLifecycle())
    dummy_ui.context.client.storage["client_id"] = "client-1"

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    trip_button = next(b for b in dummy_ui.buttons if b.text == "Trip Circuit Breaker")
    assert trip_button.on_click_cb is not None
    await trip_button.on_click_cb()

    assert any("service error" in msg.lower() for msg, _ in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_trip_value_error(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test trip handles ValueError."""
    dummy_service = DummyService(trip_error=ValueError("bad value"))

    monkeypatch.setattr(cb_module, "_get_cb_service", lambda: dummy_service)
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    def _has_permission(_user: dict[str, Any], permission: Any) -> bool:
        return True

    monkeypatch.setattr(cb_module, "has_permission", _has_permission)

    class DummyLifecycle:
        async def register_cleanup_callback(self, _client_id: str, _cb: Any) -> None:
            return None

    monkeypatch.setattr(cb_module.ClientLifecycleManager, "get", lambda: DummyLifecycle())
    dummy_ui.context.client.storage["client_id"] = "client-1"

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    trip_button = next(b for b in dummy_ui.buttons if b.text == "Trip Circuit Breaker")
    assert trip_button.on_click_cb is not None
    await trip_button.on_click_cb()

    assert any("invalid data" in msg.lower() for msg, _ in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_reset_rate_limit_exceeded(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test reset handles RateLimitExceeded."""
    from libs.web_console_services.cb_service import RateLimitExceeded

    dummy_service = DummyService(reset_error=RateLimitExceeded("rate limited"))

    monkeypatch.setattr(cb_module, "_get_cb_service", lambda: dummy_service)
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    def _has_permission(_user: dict[str, Any], permission: Any) -> bool:
        return True

    monkeypatch.setattr(cb_module, "has_permission", _has_permission)

    class DummyLifecycle:
        async def register_cleanup_callback(self, _client_id: str, _cb: Any) -> None:
            return None

    monkeypatch.setattr(cb_module.ClientLifecycleManager, "get", lambda: DummyLifecycle())
    dummy_ui.context.client.storage["client_id"] = "client-1"

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    # Set up reset with valid inputs
    min_len = cb_module.config.MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH
    reset_label = f"Reset Reason (minimum {min_len} characters)"
    reset_textarea = next(e for e in dummy_ui.elements if getattr(e, "label", "") == reset_label)
    reset_textarea.value = "x" * min_len

    checkbox = next(
        e
        for e in dummy_ui.elements
        if getattr(e, "label", "") == "I acknowledge that resetting will allow trading to resume"
    )
    checkbox.value = True

    reset_button = next(b for b in dummy_ui.buttons if b.text == "Confirm Reset")
    assert reset_button.on_click_cb is not None
    await reset_button.on_click_cb()

    assert any("rate limit exceeded" in msg.lower() for msg, _ in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_reset_validation_error(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test reset handles ValidationError."""
    from libs.web_console_services.cb_service import ValidationError

    dummy_service = DummyService(reset_error=ValidationError("invalid reset"))

    monkeypatch.setattr(cb_module, "_get_cb_service", lambda: dummy_service)
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    def _has_permission(_user: dict[str, Any], permission: Any) -> bool:
        return True

    monkeypatch.setattr(cb_module, "has_permission", _has_permission)

    class DummyLifecycle:
        async def register_cleanup_callback(self, _client_id: str, _cb: Any) -> None:
            return None

    monkeypatch.setattr(cb_module.ClientLifecycleManager, "get", lambda: DummyLifecycle())
    dummy_ui.context.client.storage["client_id"] = "client-1"

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    min_len = cb_module.config.MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH
    reset_label = f"Reset Reason (minimum {min_len} characters)"
    reset_textarea = next(e for e in dummy_ui.elements if getattr(e, "label", "") == reset_label)
    reset_textarea.value = "x" * min_len

    checkbox = next(
        e
        for e in dummy_ui.elements
        if getattr(e, "label", "") == "I acknowledge that resetting will allow trading to resume"
    )
    checkbox.value = True

    reset_button = next(b for b in dummy_ui.buttons if b.text == "Confirm Reset")
    assert reset_button.on_click_cb is not None
    await reset_button.on_click_cb()

    assert any("validation error" in msg.lower() for msg, _ in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_reset_rbac_violation(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test reset handles RBACViolation."""
    from libs.web_console_services.cb_service import RBACViolation

    dummy_service = DummyService(reset_error=RBACViolation("no reset permission"))

    monkeypatch.setattr(cb_module, "_get_cb_service", lambda: dummy_service)
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    def _has_permission(_user: dict[str, Any], permission: Any) -> bool:
        return True

    monkeypatch.setattr(cb_module, "has_permission", _has_permission)

    class DummyLifecycle:
        async def register_cleanup_callback(self, _client_id: str, _cb: Any) -> None:
            return None

    monkeypatch.setattr(cb_module.ClientLifecycleManager, "get", lambda: DummyLifecycle())
    dummy_ui.context.client.storage["client_id"] = "client-1"

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    min_len = cb_module.config.MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH
    reset_label = f"Reset Reason (minimum {min_len} characters)"
    reset_textarea = next(e for e in dummy_ui.elements if getattr(e, "label", "") == reset_label)
    reset_textarea.value = "x" * min_len

    checkbox = next(
        e
        for e in dummy_ui.elements
        if getattr(e, "label", "") == "I acknowledge that resetting will allow trading to resume"
    )
    checkbox.value = True

    reset_button = next(b for b in dummy_ui.buttons if b.text == "Confirm Reset")
    assert reset_button.on_click_cb is not None
    await reset_button.on_click_cb()

    assert any("permission denied" in msg.lower() for msg, _ in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_reset_runtime_error(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test reset handles RuntimeError."""
    dummy_service = DummyService(reset_error=RuntimeError("reset service error"))

    monkeypatch.setattr(cb_module, "_get_cb_service", lambda: dummy_service)
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    def _has_permission(_user: dict[str, Any], permission: Any) -> bool:
        return True

    monkeypatch.setattr(cb_module, "has_permission", _has_permission)

    class DummyLifecycle:
        async def register_cleanup_callback(self, _client_id: str, _cb: Any) -> None:
            return None

    monkeypatch.setattr(cb_module.ClientLifecycleManager, "get", lambda: DummyLifecycle())
    dummy_ui.context.client.storage["client_id"] = "client-1"

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    min_len = cb_module.config.MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH
    reset_label = f"Reset Reason (minimum {min_len} characters)"
    reset_textarea = next(e for e in dummy_ui.elements if getattr(e, "label", "") == reset_label)
    reset_textarea.value = "x" * min_len

    checkbox = next(
        e
        for e in dummy_ui.elements
        if getattr(e, "label", "") == "I acknowledge that resetting will allow trading to resume"
    )
    checkbox.value = True

    reset_button = next(b for b in dummy_ui.buttons if b.text == "Confirm Reset")
    assert reset_button.on_click_cb is not None
    await reset_button.on_click_cb()

    assert any("service error" in msg.lower() for msg, _ in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_reset_value_error(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test reset handles ValueError."""
    dummy_service = DummyService(reset_error=ValueError("bad reset value"))

    monkeypatch.setattr(cb_module, "_get_cb_service", lambda: dummy_service)
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    def _has_permission(_user: dict[str, Any], permission: Any) -> bool:
        return True

    monkeypatch.setattr(cb_module, "has_permission", _has_permission)

    class DummyLifecycle:
        async def register_cleanup_callback(self, _client_id: str, _cb: Any) -> None:
            return None

    monkeypatch.setattr(cb_module.ClientLifecycleManager, "get", lambda: DummyLifecycle())
    dummy_ui.context.client.storage["client_id"] = "client-1"

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    min_len = cb_module.config.MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH
    reset_label = f"Reset Reason (minimum {min_len} characters)"
    reset_textarea = next(e for e in dummy_ui.elements if getattr(e, "label", "") == reset_label)
    reset_textarea.value = "x" * min_len

    checkbox = next(
        e
        for e in dummy_ui.elements
        if getattr(e, "label", "") == "I acknowledge that resetting will allow trading to resume"
    )
    checkbox.value = True

    reset_button = next(b for b in dummy_ui.buttons if b.text == "Confirm Reset")
    assert reset_button.on_click_cb is not None
    await reset_button.on_click_cb()

    assert any("invalid data" in msg.lower() for msg, _ in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_history_section_with_data(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test history section renders table with data."""
    dummy_service = DummyService(
        history=[
            {
                "tripped_at": "2025-01-01T12:00:00Z",
                "reason": "DATA_STALE",
                "reset_at": "2025-01-01T12:30:00Z",
                "reset_by": "admin",
                "reset_reason": "conditions normalized",
            },
            {
                "tripped_at": "2025-01-02T08:00:00Z",
                "reason": "MANUAL",
                "reset_at": "",
                "reset_by": "",
                "reset_reason": "",
            },
        ]
    )

    monkeypatch.setattr(cb_module, "_get_cb_service", lambda: dummy_service)
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    def _has_permission(_user: dict[str, Any], permission: Any) -> bool:
        return True

    monkeypatch.setattr(cb_module, "has_permission", _has_permission)

    class DummyLifecycle:
        async def register_cleanup_callback(self, _client_id: str, _cb: Any) -> None:
            return None

    monkeypatch.setattr(cb_module.ClientLifecycleManager, "get", lambda: DummyLifecycle())
    dummy_ui.context.client.storage["client_id"] = "client-1"

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    # Should have rendered a table with history
    assert len(dummy_ui.tables) > 0
    columns, rows = dummy_ui.tables[0]
    assert len(rows) == 2
    assert rows[0]["reason"] == "DATA_STALE"
    assert rows[1]["reason"] == "MANUAL"


@pytest.mark.asyncio()
async def test_permission_checks_trip_and_reset_denied(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test page shows permission messages when trip/reset denied."""
    from libs.platform.web_console_auth.permissions import Permission

    dummy_service = DummyService()

    monkeypatch.setattr(cb_module, "_get_cb_service", lambda: dummy_service)
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    def _only_view_permission(_user: dict[str, Any], permission: Any) -> bool:
        return permission == Permission.VIEW_CIRCUIT_BREAKER

    monkeypatch.setattr(cb_module, "has_permission", _only_view_permission)

    class DummyLifecycle:
        async def register_cleanup_callback(self, _client_id: str, _cb: Any) -> None:
            return None

    monkeypatch.setattr(cb_module.ClientLifecycleManager, "get", lambda: DummyLifecycle())
    dummy_ui.context.client.storage["client_id"] = "client-1"

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    labels = [text for text, _ in dummy_ui.labels]
    assert any("TRIP_CIRCUIT permission required" in label for label in labels)
    assert any("RESET_CIRCUIT permission required" in label for label in labels)


@pytest.mark.asyncio()
async def test_reset_reason_input_change_handler(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test reset reason input change handler updates char count and button state."""
    dummy_service = DummyService()

    monkeypatch.setattr(cb_module, "_get_cb_service", lambda: dummy_service)
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    def _has_permission(_user: dict[str, Any], permission: Any) -> bool:
        return True

    monkeypatch.setattr(cb_module, "has_permission", _has_permission)

    class DummyLifecycle:
        async def register_cleanup_callback(self, _client_id: str, _cb: Any) -> None:
            return None

    monkeypatch.setattr(cb_module.ClientLifecycleManager, "get", lambda: DummyLifecycle())
    dummy_ui.context.client.storage["client_id"] = "client-1"

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    min_len = cb_module.config.MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH
    reset_label = f"Reset Reason (minimum {min_len} characters)"
    reset_textarea = next(e for e in dummy_ui.elements if getattr(e, "label", "") == reset_label)
    reset_textarea.value = "short"

    if reset_textarea.on_value_change_cb:
        reset_textarea.on_value_change_cb()

    # Check that char count label was created
    labels = [text for text, _ in dummy_ui.labels]
    assert any("characters" in label.lower() for label in labels)


@pytest.mark.asyncio()
async def test_acknowledgment_checkbox_change_handler(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test acknowledgment checkbox change handler updates button state."""
    dummy_service = DummyService()

    monkeypatch.setattr(cb_module, "_get_cb_service", lambda: dummy_service)
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    def _has_permission(_user: dict[str, Any], permission: Any) -> bool:
        return True

    monkeypatch.setattr(cb_module, "has_permission", _has_permission)

    class DummyLifecycle:
        async def register_cleanup_callback(self, _client_id: str, _cb: Any) -> None:
            return None

    monkeypatch.setattr(cb_module.ClientLifecycleManager, "get", lambda: DummyLifecycle())
    dummy_ui.context.client.storage["client_id"] = "client-1"

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    min_len = cb_module.config.MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH
    reset_label = f"Reset Reason (minimum {min_len} characters)"
    reset_textarea = next(e for e in dummy_ui.elements if getattr(e, "label", "") == reset_label)
    reset_textarea.value = "x" * min_len

    checkbox = next(
        e
        for e in dummy_ui.elements
        if getattr(e, "label", "") == "I acknowledge that resetting will allow trading to resume"
    )
    checkbox.value = True
    if checkbox.on_value_change_cb:
        checkbox.on_value_change_cb()

    reset_button = next(b for b in dummy_ui.buttons if b.text == "Confirm Reset")
    # Button should be enabled after checkbox is checked and reason is long enough
    assert reset_button.enabled is True


@pytest.mark.asyncio()
async def test_auto_refresh_timer(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test auto-refresh timer is created and registered."""
    dummy_service = DummyService()

    monkeypatch.setattr(cb_module, "_get_cb_service", lambda: dummy_service)
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    def _has_permission(_user: dict[str, Any], permission: Any) -> bool:
        return True

    monkeypatch.setattr(cb_module, "has_permission", _has_permission)

    cleanup_callbacks: list[tuple[str, Any]] = []

    class DummyLifecycle:
        async def register_cleanup_callback(self, client_id: str, cb: Any) -> None:
            cleanup_callbacks.append((client_id, cb))

    monkeypatch.setattr(cb_module.ClientLifecycleManager, "get", lambda: DummyLifecycle())
    dummy_ui.context.client.storage["client_id"] = "client-1"

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    # Timer should be created
    assert len(dummy_ui.timers) > 0
    interval, callback, timer = dummy_ui.timers[0]
    assert interval == cb_module.config.AUTO_REFRESH_INTERVAL

    # Cleanup callback should be registered
    assert len(cleanup_callbacks) == 1
    assert cleanup_callbacks[0][0] == "client-1"


@pytest.mark.asyncio()
async def test_no_client_id_no_lifecycle_cleanup(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that no cleanup is registered when client_id is missing."""
    dummy_service = DummyService()

    monkeypatch.setattr(cb_module, "_get_cb_service", lambda: dummy_service)
    monkeypatch.setattr(cb_module.config, "FEATURE_CIRCUIT_BREAKER", True)
    monkeypatch.setattr(cb_module, "get_current_user", lambda: {"user_id": "u1"})

    def _has_permission(_user: dict[str, Any], permission: Any) -> bool:
        return True

    monkeypatch.setattr(cb_module, "has_permission", _has_permission)

    cleanup_callbacks: list[tuple[str, Any]] = []

    class DummyLifecycle:
        async def register_cleanup_callback(self, client_id: str, cb: Any) -> None:
            cleanup_callbacks.append((client_id, cb))

    monkeypatch.setattr(cb_module.ClientLifecycleManager, "get", lambda: DummyLifecycle())
    # No client_id in storage
    dummy_ui.context.client.storage = {}

    page_fn = cb_module.circuit_breaker_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    # No cleanup callback should be registered
    assert len(cleanup_callbacks) == 0


def test_get_cb_service_with_db_pool_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _get_cb_service handles db pool unavailable gracefully."""
    dummy_app = SimpleNamespace(storage=SimpleNamespace())
    monkeypatch.setattr(cb_module, "app", dummy_app)

    def _raise_db_pool() -> None:
        raise RuntimeError("db pool unavailable")

    monkeypatch.setattr(cb_module, "get_sync_db_pool", _raise_db_pool)
    monkeypatch.setattr(cb_module, "get_sync_redis_client", lambda: object())

    dummy_cb_module = SimpleNamespace(
        CircuitBreakerService=lambda *_args, **_kwargs: DummyService()
    )
    monkeypatch.setitem(sys.modules, "libs.web_console_services.cb_service", dummy_cb_module)

    service = cb_module._get_cb_service()
    # Service should be created even without db pool (for audit logging)
    assert service is not None
