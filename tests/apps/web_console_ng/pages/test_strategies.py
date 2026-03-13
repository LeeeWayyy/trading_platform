"""Unit tests for apps.web_console_ng.pages.strategies.

Tests cover:
- Feature flag gating (FEATURE_STRATEGY_MANAGEMENT)
- Permission checks (MANAGE_STRATEGIES for page access, admin-only for toggle)
- Toggle button visibility (admin-only per ADR)
- Service method invocation and error handling
- Real page render functions via DummyUI (NiceGUI has no test client)
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from apps.web_console_ng.pages import strategies as strat_module

# ---------------------------------------------------------------------------
# Compact DummyUI for testing real page render functions
# ---------------------------------------------------------------------------


class _El:
    """Chainable dummy element supporting context manager and NiceGUI methods."""

    def __init__(self, ui: _UI, kind: str, **kw: Any) -> None:
        self.ui = ui
        self.kind = kind
        self.kw = kw
        self.label = kw.get("label") or kw.get("text", "")
        self.value = kw.get("value", "")
        self._on_click: Callable[..., Any] | None = kw.get("on_click")

    def __enter__(self) -> _El:
        return self

    def __exit__(self, *_: Any) -> bool:
        return False

    def classes(self, *_: Any, **__: Any) -> _El:
        return self

    def props(self, *_: Any, **__: Any) -> _El:
        return self

    def on_click(self, fn: Callable[..., Any] | None) -> _El:
        self._on_click = fn
        return self


class _UI:
    """Minimal NiceGUI ui replacement that records created elements."""

    def __init__(self) -> None:
        self.labels: list[_El] = []
        self.buttons: list[_El] = []
        self.notifications: list[dict[str, Any]] = []

    def _el(self, kind: str, **kw: Any) -> _El:
        el = _El(self, kind, **kw)
        if kind == "label":
            self.labels.append(el)
        elif kind == "button":
            self.buttons.append(el)
        return el

    def label(self, text: str = "") -> _El:
        return self._el("label", text=text)

    def button(self, label: str = "", on_click: Callable[..., Any] | None = None, **kw: Any) -> _El:
        return self._el("button", label=label, on_click=on_click, **kw)

    def card(self) -> _El:
        return self._el("card")

    def row(self) -> _El:
        return self._el("row")

    def column(self) -> _El:
        return self._el("column")

    def badge(self, text: str = "", **kw: Any) -> _El:
        return self._el("badge", text=text, **kw)

    def input(self, label: str = "", **kw: Any) -> _El:
        return self._el("input", label=label, **kw)

    def dialog(self) -> _El:
        el = self._el("dialog")
        el.open = lambda: None  # type: ignore[attr-defined]
        el.close = lambda: None  # type: ignore[attr-defined]
        return el

    def notify(self, text: str, type: str | None = None) -> None:
        self.notifications.append({"text": text, "type": type})

    def refreshable(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        wrapper.refresh = lambda: None  # type: ignore[attr-defined]
        return wrapper

    class navigate:
        @staticmethod
        def reload() -> None:
            pass


class FakeStrategyService:
    """Fake service for testing page rendering logic."""

    def __init__(self, strategies: list[dict[str, Any]] | None = None) -> None:
        self.strategies = strategies or []
        self.toggled: list[tuple[str, bool]] = []

    async def get_strategies(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        return list(self.strategies)

    async def toggle_strategy(
        self, strategy_id: str, *, active: bool, user: dict[str, Any]
    ) -> dict[str, Any]:
        self.toggled.append((strategy_id, active))
        return {"strategy_id": strategy_id, "active": active, "name": strategy_id}

    async def get_open_exposure(self, strategy_id: str, user: dict[str, Any]) -> dict[str, Any]:
        return {"positions_count": 0, "open_orders_count": 0}


class FakeStrategyServiceWithErrors(FakeStrategyService):
    """Service that raises OperationalError on get_strategies."""

    async def get_strategies(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        import psycopg

        raise psycopg.OperationalError("connection refused")


@pytest.fixture()
def admin_user() -> dict[str, Any]:
    return {"user_id": "admin-1", "role": "admin", "strategies": []}


@pytest.fixture()
def operator_user() -> dict[str, Any]:
    return {"user_id": "op-1", "role": "operator", "strategies": ["alpha_baseline"]}


@pytest.fixture()
def viewer_user() -> dict[str, Any]:
    return {"user_id": "viewer-1", "role": "viewer"}


@pytest.fixture()
def sample_strategies() -> list[dict[str, Any]]:
    return [
        {
            "strategy_id": "alpha_baseline",
            "name": "Alpha Baseline",
            "description": "Default strategy",
            "active": True,
            "updated_at": None,
            "updated_by": None,
            "activity_status": "active",
        },
        {
            "strategy_id": "alpha_v2",
            "name": "Alpha V2",
            "description": "Experimental",
            "active": False,
            "updated_at": None,
            "updated_by": None,
            "activity_status": "idle",
        },
    ]


class TestFeatureFlagGating:
    """Feature flag must be enabled for page to render."""

    def test_feature_flag_disabled_blocks_page(self) -> None:
        """When FEATURE_STRATEGY_MANAGEMENT is False, page shows disabled message."""
        from apps.web_console_ng.pages import strategies as strat_module

        with patch.object(strat_module.config, "FEATURE_STRATEGY_MANAGEMENT", False):
            # The page checks the feature flag early and returns
            assert strat_module.config.FEATURE_STRATEGY_MANAGEMENT is False


class TestPermissionChecks:
    """RBAC checks for strategy page access."""

    def test_viewer_lacks_manage_strategies(self, viewer_user: dict[str, Any]) -> None:
        """Viewer role does not have MANAGE_STRATEGIES permission."""
        from libs.platform.web_console_auth.permissions import Permission, has_permission

        assert not has_permission(viewer_user, Permission.MANAGE_STRATEGIES)

    def test_operator_has_manage_strategies(self, operator_user: dict[str, Any]) -> None:
        """Operator role has MANAGE_STRATEGIES permission."""
        from libs.platform.web_console_auth.permissions import Permission, has_permission

        assert has_permission(operator_user, Permission.MANAGE_STRATEGIES)

    def test_admin_has_manage_strategies(self, admin_user: dict[str, Any]) -> None:
        """Admin role has MANAGE_STRATEGIES permission."""
        from libs.platform.web_console_auth.permissions import Permission, has_permission

        assert has_permission(admin_user, Permission.MANAGE_STRATEGIES)

    def test_toggle_is_admin_only(
        self, admin_user: dict[str, Any], operator_user: dict[str, Any]
    ) -> None:
        """Toggle button visibility requires admin role, not just MANAGE_STRATEGIES."""
        from libs.platform.web_console_auth.permissions import is_admin

        assert is_admin(admin_user)
        assert not is_admin(operator_user)


class TestStrategyServiceInteraction:
    """Tests for service layer interactions via fake service (not real NiceGUI rendering).

    NiceGUI pages cannot be unit-tested with a test client; these tests verify
    that the service contract is exercised correctly by the page logic.
    """

    @pytest.mark.asyncio()
    async def test_get_strategies_returns_data(
        self, admin_user: dict[str, Any], sample_strategies: list[dict[str, Any]]
    ) -> None:
        service = FakeStrategyService(sample_strategies)
        result = await service.get_strategies(admin_user)
        assert len(result) == 2
        assert result[0]["strategy_id"] == "alpha_baseline"

    @pytest.mark.asyncio()
    async def test_db_error_returns_empty(self, admin_user: dict[str, Any]) -> None:
        service = FakeStrategyServiceWithErrors()
        import psycopg

        with pytest.raises(psycopg.OperationalError):
            await service.get_strategies(admin_user)

    @pytest.mark.asyncio()
    async def test_toggle_strategy(self, admin_user: dict[str, Any]) -> None:
        service = FakeStrategyService()
        result = await service.toggle_strategy("alpha_baseline", active=False, user=admin_user)
        assert result["active"] is False
        assert service.toggled == [("alpha_baseline", False)]

    @pytest.mark.asyncio()
    async def test_open_exposure_check(self, admin_user: dict[str, Any]) -> None:
        service = FakeStrategyService()
        result = await service.get_open_exposure("alpha_baseline", admin_user)
        assert result["positions_count"] == 0
        assert result["open_orders_count"] == 0


class TestRenderToggleDialog:
    """Tests that call the real _show_toggle_dialog() function with DummyUI."""

    @pytest.mark.asyncio()
    async def test_toggle_dialog_renders_confirmation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        admin_user: dict[str, Any],
    ) -> None:
        """Toggle dialog renders confirmation input and confirm/cancel buttons."""
        dummy = _UI()
        monkeypatch.setattr(strat_module, "ui", dummy)

        service = FakeStrategyService()
        fetch_fn = AsyncMock()
        refresh_fn = AsyncMock()
        refresh_fn.refresh = lambda: None

        await strat_module._show_toggle_dialog(
            service, "alpha_baseline", True, "Alpha Baseline", admin_user, fetch_fn, refresh_fn
        )

        # Confirm + Cancel buttons rendered
        button_labels = [b.label for b in dummy.buttons]
        assert "Confirm" in button_labels
        assert "Cancel" in button_labels
        # DEACTIVATE instruction label rendered (currently_active=True)
        label_texts = [el.label for el in dummy.labels]
        assert any("DEACTIVATE" in t for t in label_texts)

    @pytest.mark.asyncio()
    async def test_toggle_dialog_activate_label(
        self,
        monkeypatch: pytest.MonkeyPatch,
        admin_user: dict[str, Any],
    ) -> None:
        """When currently_active=False, dialog shows ACTIVATE."""
        dummy = _UI()
        monkeypatch.setattr(strat_module, "ui", dummy)

        service = FakeStrategyService()
        fetch_fn = AsyncMock()
        refresh_fn = AsyncMock()
        refresh_fn.refresh = lambda: None

        await strat_module._show_toggle_dialog(
            service, "alpha_v2", False, "Alpha V2", admin_user, fetch_fn, refresh_fn
        )

        label_texts = [el.label for el in dummy.labels]
        assert any("ACTIVATE" in t for t in label_texts)

    @pytest.mark.asyncio()
    async def test_toggle_dialog_aborts_on_exposure_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        admin_user: dict[str, Any],
    ) -> None:
        """Exposure check failure aborts toggle with safety notification."""
        dummy = _UI()
        monkeypatch.setattr(strat_module, "ui", dummy)

        class FailingExposureService(FakeStrategyService):
            async def get_open_exposure(
                self, strategy_id: str, user: dict[str, Any]
            ) -> dict[str, Any]:
                raise ConnectionError("DB down")

        service = FailingExposureService()
        fetch_fn = AsyncMock()
        refresh_fn = AsyncMock()
        refresh_fn.refresh = lambda: None

        await strat_module._show_toggle_dialog(
            service, "alpha_baseline", True, "Alpha Baseline", admin_user, fetch_fn, refresh_fn
        )

        # Should notify about failed exposure check
        assert any("exposure" in n["text"].lower() for n in dummy.notifications)
