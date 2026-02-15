"""Tests for shadow results page (P6T14/T14.4)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from apps.web_console_ng.pages import shadow_results as module
from libs.web_console_services.schemas.data_management import (
    ShadowResultDTO,
    ShadowTrendDTO,
    ShadowTrendPointDTO,
)


class FakeElement:
    def __init__(self) -> None:
        self.classes_calls: list[str] = []

    def __enter__(self) -> FakeElement:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def classes(self, classes: str) -> FakeElement:
        self.classes_calls.append(classes)
        return self

    def clear(self) -> None:
        return None


class FakeTimer:
    def __init__(self, interval: float, callback: Any) -> None:
        self.interval = interval
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class FakeAgGrid(FakeElement):
    def __init__(self, options: dict[str, Any]) -> None:
        super().__init__()
        self.options = options
        self.updated = 0
        self.selected_rows: list[dict[str, Any]] = []
        self.handlers: dict[str, Any] = {}

    def update(self) -> None:
        self.updated += 1

    async def get_selected_rows(self) -> list[dict[str, Any]]:
        return self.selected_rows

    def on(self, event_name: str, handler: Any) -> None:
        self.handlers[event_name] = handler


class FakeUI:
    def __init__(self) -> None:
        self.labels: list[str] = []
        self.notifications: list[tuple[str, str | None]] = []
        self.timers: list[FakeTimer] = []
        self.aggrid_calls: list[FakeAgGrid] = []

    def label(self, text: str = "") -> FakeElement:
        self.labels.append(text)
        return FakeElement()

    def column(self) -> FakeElement:
        return FakeElement()

    def row(self) -> FakeElement:
        return FakeElement()

    def card(self) -> FakeElement:
        return FakeElement()

    def separator(self) -> FakeElement:
        return FakeElement()

    def aggrid(self, options: dict[str, Any]) -> FakeAgGrid:
        grid = FakeAgGrid(options)
        self.aggrid_calls.append(grid)
        return grid

    def plotly(self, _figure: Any) -> FakeElement:
        return FakeElement()

    def timer(self, interval: float, callback: Any) -> FakeTimer:
        timer = FakeTimer(interval, callback)
        self.timers.append(timer)
        return timer

    def notify(self, message: str, *, type: str | None = None) -> None:
        self.notifications.append((message, type))


class DummyLifecycle:
    def __init__(self) -> None:
        self.registered_clients: list[str] = []
        self.cleanup_callbacks: list[tuple[str, str | None, Any]] = []

    async def register_client(self, client_id: str) -> None:
        self.registered_clients.append(client_id)

    async def register_cleanup_callback(
        self,
        client_id: str,
        callback: Any,
        owner_key: str | None = None,
    ) -> None:
        self.cleanup_callbacks.append((client_id, owner_key, callback))


class StubService:
    def __init__(self, *, delay: float = 0.0) -> None:
        self.delay = delay
        self.recent_calls = 0
        self.trend_calls = 0

    async def get_recent_results(self, _user: Any, strategy: str | None = None, limit: int = 50):
        self.recent_calls += 1
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        now = datetime.now(UTC)
        return [
            ShadowResultDTO(
                id="shadow-1",
                model_version="v1.1",
                strategy=strategy or "alpha_baseline",
                validation_time=now,
                passed=True,
                correlation=0.82,
                mean_abs_diff_ratio=0.21,
                sign_change_rate=0.1,
                sample_count=1200,
                old_range=0.4,
                new_range=0.5,
                message="ok",
                correlation_threshold=0.5,
                divergence_threshold=0.5,
            )
        ]

    async def get_trend(self, _user: Any, strategy: str | None = None, days: int = 30):
        self.trend_calls += 1
        now = datetime.now(UTC)
        return ShadowTrendDTO(
            strategy=strategy or "alpha_baseline",
            period_days=days,
            data_points=[
                ShadowTrendPointDTO(
                    date=now,
                    correlation=0.82,
                    mean_abs_diff_ratio=0.21,
                    sign_change_rate=0.1,
                    passed=True,
                )
            ],
            total_validations=1,
            pass_rate=100.0,
            avg_correlation=0.82,
            avg_divergence=0.21,
        )


def _unwrap_page() -> Any:
    page_fn = module.shadow_results_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__
    return page_fn


@pytest.mark.asyncio()
async def test_timer_cleanup_registered_with_owner_key(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ui = FakeUI()
    lifecycle = DummyLifecycle()
    service = StubService()

    monkeypatch.setattr(module, "ui", fake_ui)
    monkeypatch.setattr(module, "get_current_user", lambda: {"role": "operator", "user_id": "u1"})
    monkeypatch.setattr(module, "has_permission", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(module, "ShadowResultsService", lambda: service)
    monkeypatch.setattr(module.ClientLifecycleManager, "get", lambda: lifecycle)
    monkeypatch.setattr(module, "get_or_create_client_id", lambda: "client-1")

    page = _unwrap_page()
    await page()

    assert len(fake_ui.timers) == 1
    assert lifecycle.cleanup_callbacks
    client_id, owner_key, callback = lifecycle.cleanup_callbacks[0]
    assert client_id == "client-1"
    assert owner_key == module._CLEANUP_OWNER_KEY
    callback()
    assert fake_ui.timers[0].cancelled is True


@pytest.mark.asyncio()
async def test_preview_data_badge_rendered(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ui = FakeUI()
    lifecycle = DummyLifecycle()

    monkeypatch.setattr(module, "ui", fake_ui)
    monkeypatch.setattr(module, "get_current_user", lambda: {"role": "operator", "user_id": "u1"})
    monkeypatch.setattr(module, "has_permission", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(module, "ShadowResultsService", lambda: StubService())
    monkeypatch.setattr(module.ClientLifecycleManager, "get", lambda: lifecycle)
    monkeypatch.setattr(module, "get_or_create_client_id", lambda: "client-1")

    page = _unwrap_page()
    await page()

    assert "Preview Data" in fake_ui.labels


@pytest.mark.asyncio()
async def test_overlap_guard_prevents_concurrent_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ui = FakeUI()
    lifecycle = DummyLifecycle()
    service = StubService(delay=0.05)

    monkeypatch.setattr(module, "ui", fake_ui)
    monkeypatch.setattr(module, "get_current_user", lambda: {"role": "operator", "user_id": "u1"})
    monkeypatch.setattr(module, "has_permission", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(module, "ShadowResultsService", lambda: service)
    monkeypatch.setattr(module.ClientLifecycleManager, "get", lambda: lifecycle)
    monkeypatch.setattr(module, "get_or_create_client_id", lambda: "client-1")

    page = _unwrap_page()
    await page()

    timer_cb = fake_ui.timers[0].callback
    before_recent = service.recent_calls
    before_trend = service.trend_calls
    await asyncio.gather(timer_cb(), timer_cb())

    assert service.recent_calls == before_recent + 1
    assert service.trend_calls == before_trend + 1
