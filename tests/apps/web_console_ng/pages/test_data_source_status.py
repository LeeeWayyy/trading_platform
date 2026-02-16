"""Tests for data source status page (P6T14/T14.2)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from apps.web_console_ng.pages import data_source_status as module
from libs.web_console_services.schemas.data_management import DataSourceStatusDTO


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

    def update(self) -> None:
        self.updated += 1

    async def get_selected_rows(self) -> list[dict[str, Any]]:
        return self.selected_rows


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

    def button(self, _text: str, **_kwargs: Any) -> FakeElement:
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
        self.get_all_calls = 0

    async def get_all_sources(self, _user: Any) -> list[DataSourceStatusDTO]:
        self.get_all_calls += 1
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        now = datetime.now(UTC)
        return [
            DataSourceStatusDTO(
                name="crsp",
                display_name="CRSP Daily",
                provider_type="academic",
                dataset_key="crsp",
                status="ok",
                last_update=now,
                age_seconds=0.0,
                row_count=123,
                error_rate_pct=0.0,
                error_message=None,
                is_production_ready=True,
                tables=["crsp_daily"],
            )
        ]

    async def refresh_source(self, _user: Any, _source_name: str) -> DataSourceStatusDTO:
        now = datetime.now(UTC)
        return DataSourceStatusDTO(
            name="crsp",
            display_name="CRSP Daily",
            provider_type="academic",
            dataset_key="crsp",
            status="ok",
            last_update=now,
            age_seconds=0.0,
            row_count=123,
            error_rate_pct=0.0,
            error_message=None,
            is_production_ready=True,
            tables=["crsp_daily"],
        )


def _unwrap_page() -> Any:
    page_fn = module.data_source_status_page
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
    monkeypatch.setattr(module, "DataSourceStatusService", lambda **_kwargs: service)
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
    monkeypatch.setattr(module, "DataSourceStatusService", lambda **_kwargs: StubService())
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
    monkeypatch.setattr(module, "DataSourceStatusService", lambda **_kwargs: service)
    monkeypatch.setattr(module.ClientLifecycleManager, "get", lambda: lifecycle)
    monkeypatch.setattr(module, "get_or_create_client_id", lambda: "client-1")

    page = _unwrap_page()
    await page()

    # One initial load + one out of the concurrent timer callbacks should execute.
    timer_cb = fake_ui.timers[0].callback
    before = service.get_all_calls
    await asyncio.gather(timer_cb(), timer_cb())
    assert service.get_all_calls == before + 1


def test_format_relative_time_variants() -> None:
    from apps.web_console_ng.utils.time import format_relative_time

    now = datetime.now(UTC)
    assert format_relative_time(now - timedelta(seconds=30), now=now).endswith("s ago")
    assert format_relative_time(now - timedelta(minutes=5), now=now).endswith("m ago")
    assert format_relative_time(now - timedelta(hours=2), now=now).endswith("h ago")
    assert format_relative_time(None, now=now) == "unknown"
