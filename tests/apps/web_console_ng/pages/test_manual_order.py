from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from apps.web_console_ng.pages import manual_order as manual_order_module


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


@pytest.mark.asyncio()
async def test_manual_order_route_redirects_to_trade(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ui = FakeUI()
    monkeypatch.setattr(manual_order_module, "ui", fake_ui)

    client = MagicMock()
    await _unwrap_page(manual_order_module.manual_order_page)(client)

    assert fake_ui.navigations == ["/trade"]
    assert fake_ui.notifications == [
        ("Manual Controls moved to Trade Workspace. Redirecting...", "info")
    ]
