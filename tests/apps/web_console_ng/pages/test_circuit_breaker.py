"""Unit tests for circuit_breaker page."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

os.environ["WEB_CONSOLE_NG_DEBUG"] = "true"
os.environ.setdefault("NICEGUI_STORAGE_SECRET", "test-secret")

from apps.web_console_ng.pages import circuit_breaker as cb_module
from tests.apps.web_console_ng.pages.ui_test_utils import DummyUI


class DummyService:
    def __init__(self) -> None:
        self.trip_calls: list[tuple[str, dict]] = []
        self.reset_calls: list[tuple[str, dict]] = []

    def get_status(self):
        return {"state": "OPEN", "trip_count_today": 0}

    def get_history(self, _limit: int):
        return []

    def trip(self, reason: str, user: dict, acknowledged: bool = True):
        self.trip_calls.append((reason, user))

    def reset(self, reason: str, user: dict, acknowledged: bool = True):
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

    dummy_cb_module = SimpleNamespace(CircuitBreakerService=lambda *_args, **_kwargs: DummyService())
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

    reason_input = next(e for e in dummy_ui.elements if getattr(e, "label", None) == "Custom reason")
    reason_input.value = "manual check"

    assert trip_button.on_click_cb is not None
    await trip_button.on_click_cb()
    assert dummy_service.trip_calls

    min_len = cb_module.config.MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH
    reset_label = f"Reset Reason (minimum {min_len} characters)"
    reset_textarea = next(
        e for e in dummy_ui.elements if getattr(e, "label", "") == reset_label
    )
    reset_textarea.value = "reset reason long enough"
    checkbox = next(
        e
        for e in dummy_ui.elements
        if getattr(e, "label", "")
        == "I acknowledge that resetting will allow trading to resume"
    )
    checkbox.value = True

    assert reset_button.on_click_cb is not None
    await reset_button.on_click_cb()
    assert dummy_service.reset_calls
