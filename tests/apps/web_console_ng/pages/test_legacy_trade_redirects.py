from __future__ import annotations

import inspect
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from apps.web_console_ng.pages import legacy_trade_redirects as legacy_trade_module


class FakeUI:
    def __init__(self) -> None:
        self.notifications: list[tuple[str, str | None]] = []
        self.navigations: list[str] = []
        self.navigate = SimpleNamespace(to=self._navigate_to)

    def _navigate_to(self, path: str) -> None:
        self.navigations.append(path)

    def notify(self, message: str, *, type: str | None = None) -> None:
        self.notifications.append((message, type))


def _unwrap_page(func: Callable[..., Any]) -> Callable[..., Any]:
    while hasattr(func, "__wrapped__"):
        func = func.__wrapped__  # type: ignore[attr-defined]
    return func


def test_legacy_trade_handlers_require_client_param() -> None:
    manual = _unwrap_page(legacy_trade_module.legacy_manual_order_redirect)
    position = _unwrap_page(legacy_trade_module.legacy_position_management_redirect)

    for fn in (manual, position):
        params = list(inspect.signature(fn).parameters.values())
        assert len(params) == 1
        assert params[0].name == "client"
        assert params[0].default is inspect.Parameter.empty


@pytest.mark.asyncio()
async def test_legacy_manual_order_redirect_to_trade(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ui = FakeUI()
    monkeypatch.setattr(legacy_trade_module, "ui", fake_ui)

    await _unwrap_page(legacy_trade_module.legacy_manual_order_redirect)(SimpleNamespace())

    assert fake_ui.navigations == ["/trade"]
    assert fake_ui.notifications == [
        ("Manual Controls moved to Trade Workspace. Redirecting...", "info")
    ]


@pytest.mark.asyncio()
async def test_legacy_position_management_redirect_to_trade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ui = FakeUI()
    monkeypatch.setattr(legacy_trade_module, "ui", fake_ui)

    await _unwrap_page(legacy_trade_module.legacy_position_management_redirect)(SimpleNamespace())

    assert fake_ui.navigations == ["/trade"]
    assert fake_ui.notifications == [
        ("Position Management moved to Trade Workspace. Redirecting...", "info")
    ]
