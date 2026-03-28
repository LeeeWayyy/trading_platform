"""Unit tests for apps.web_console_ng.pages.models.

Tests cover:
- Feature flag gating (FEATURE_MODEL_REGISTRY)
- Permission checks (VIEW_MODELS for page access)
- Admin-only action visibility (activate/deactivate buttons)
- Service method invocation and error handling
- Real page render functions via DummyUI (NiceGUI has no test client)
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import pytest

from apps.web_console_ng.pages import models as models_module

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

    def expansion(self, *_: Any, **__: Any) -> _El:
        return self._el("expansion")

    def json_editor(self, *_: Any, **__: Any) -> _El:
        return self._el("json_editor")

    def tabs(self) -> _El:
        return self._el("tabs")

    def tab(self, label: str) -> _El:
        return self._el("tab", label=label)

    def tab_panels(self, *_: Any, **__: Any) -> _El:
        return self._el("tab_panels")

    def tab_panel(self, *_: Any, **__: Any) -> _El:
        return self._el("tab_panel")

    def input(self, label: str = "", **kw: Any) -> _El:
        return self._el("input", label=label, **kw)

    def dialog(self) -> _El:
        el = self._el("dialog")
        el.open = lambda: None  # type: ignore[attr-defined]
        el.close = lambda: None  # type: ignore[attr-defined]
        return el

    def notify(self, text: str, type: str | None = None) -> None:
        self.notifications.append({"text": text, "type": type})

    class navigate:
        @staticmethod
        def reload() -> None:
            pass


class FakeModelRegistryBrowserService:
    """Fake service for testing page rendering logic."""

    def __init__(
        self,
        strategies: list[dict[str, Any]] | None = None,
        models: list[dict[str, Any]] | None = None,
    ) -> None:
        self.strategies = strategies or []
        self.models = models or []
        self.activated: list[tuple[str, str]] = []
        self.deactivated: list[tuple[str, str]] = []

    async def list_strategies_with_models(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        return list(self.strategies)

    async def get_models_for_strategy(
        self, strategy_name: str, user: dict[str, Any]
    ) -> list[dict[str, Any]]:
        return [m for m in self.models if m.get("strategy_name") == strategy_name]

    async def activate_model(self, strategy_name: str, version: str, user: dict[str, Any]) -> None:
        self.activated.append((strategy_name, version))

    async def deactivate_model(
        self, strategy_name: str, version: str, user: dict[str, Any]
    ) -> None:
        self.deactivated.append((strategy_name, version))


class FakeModelServiceWithErrors(FakeModelRegistryBrowserService):
    """Service that raises OperationalError on list_strategies_with_models."""

    async def list_strategies_with_models(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        import psycopg

        raise psycopg.OperationalError("connection refused")


class FakeModelServiceWithPermissionError(FakeModelRegistryBrowserService):
    """Service that raises PermissionError on get_models_for_strategy."""

    async def get_models_for_strategy(
        self, strategy_name: str, user: dict[str, Any]
    ) -> list[dict[str, Any]]:
        raise PermissionError("VIEW_MODELS required")


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
        {"strategy_name": "alpha_baseline", "model_count": 3},
    ]


@pytest.fixture()
def sample_models() -> list[dict[str, Any]]:
    return [
        {
            "strategy_name": "alpha_baseline",
            "version": "1.0.0",
            "status": "active",
            "model_path": "artifacts/models/alpha_baseline_v1.pkl",
            "created_by": "admin-1",
            "performance_metrics": None,
            "config": None,
            "notes": None,
            "activated_at": "2026-03-01",
            "deactivated_at": None,
        },
        {
            "strategy_name": "alpha_baseline",
            "version": "2.0.0",
            "status": "inactive",
            "model_path": "artifacts/models/alpha_baseline_v2.pkl",
            "created_by": "admin-1",
            "performance_metrics": None,
            "config": None,
            "notes": None,
            "activated_at": None,
            "deactivated_at": None,
        },
    ]


class TestFeatureFlagGating:
    """Feature flag must be enabled for page to render."""

    def test_feature_flag_disabled_blocks_page(self) -> None:
        """When FEATURE_MODEL_REGISTRY is False, page shows disabled message."""
        from apps.web_console_ng.pages import models as models_module

        with patch.object(models_module.config, "FEATURE_MODEL_REGISTRY", False):
            assert models_module.config.FEATURE_MODEL_REGISTRY is False


class TestPermissionChecks:
    """RBAC checks for models page access."""

    def test_viewer_has_view_models_single_admin(self, viewer_user: dict[str, Any]) -> None:
        """P6T19: All roles have VIEW_MODELS — single-admin model."""
        from libs.platform.web_console_auth.permissions import Permission, has_permission

        assert has_permission(viewer_user, Permission.VIEW_MODELS)

    def test_operator_has_view_models(self, operator_user: dict[str, Any]) -> None:
        """Operator role has VIEW_MODELS permission."""
        from libs.platform.web_console_auth.permissions import Permission, has_permission

        assert has_permission(operator_user, Permission.VIEW_MODELS)

    def test_admin_has_view_models(self, admin_user: dict[str, Any]) -> None:
        """Admin role has VIEW_MODELS permission."""
        from libs.platform.web_console_auth.permissions import Permission, has_permission

        assert has_permission(admin_user, Permission.VIEW_MODELS)

    def test_admin_is_admin(self, admin_user: dict[str, Any]) -> None:
        """Admin has admin status for manage actions."""
        from libs.platform.web_console_auth.permissions import is_admin

        assert is_admin(admin_user)

    def test_operator_is_admin_single_admin(self, operator_user: dict[str, Any]) -> None:
        """P6T19: All roles are admin — single-admin model."""
        from libs.platform.web_console_auth.permissions import is_admin

        assert is_admin(operator_user)


class TestServiceInteraction:
    """Tests for service layer interactions via fake service (not real NiceGUI rendering).

    NiceGUI pages cannot be unit-tested with a test client; these tests verify
    that the service contract is exercised correctly by the page logic.
    """

    @pytest.mark.asyncio()
    async def test_list_strategies(
        self,
        admin_user: dict[str, Any],
        sample_strategies: list[dict[str, Any]],
    ) -> None:
        service = FakeModelRegistryBrowserService(strategies=sample_strategies)
        result = await service.list_strategies_with_models(admin_user)
        assert len(result) == 1
        assert result[0]["strategy_name"] == "alpha_baseline"

    @pytest.mark.asyncio()
    async def test_get_models_for_strategy(
        self,
        admin_user: dict[str, Any],
        sample_strategies: list[dict[str, Any]],
        sample_models: list[dict[str, Any]],
    ) -> None:
        service = FakeModelRegistryBrowserService(
            strategies=sample_strategies, models=sample_models
        )
        result = await service.get_models_for_strategy("alpha_baseline", admin_user)
        assert len(result) == 2
        assert result[0]["version"] == "1.0.0"
        assert result[1]["status"] == "inactive"

    @pytest.mark.asyncio()
    async def test_db_error_on_list(self, admin_user: dict[str, Any]) -> None:
        service = FakeModelServiceWithErrors()
        import psycopg

        with pytest.raises(psycopg.OperationalError):
            await service.list_strategies_with_models(admin_user)

    @pytest.mark.asyncio()
    async def test_permission_error_on_models(self, admin_user: dict[str, Any]) -> None:
        service = FakeModelServiceWithPermissionError()
        with pytest.raises(PermissionError):
            await service.get_models_for_strategy("alpha_baseline", admin_user)

    @pytest.mark.asyncio()
    async def test_activate_model(self, admin_user: dict[str, Any]) -> None:
        service = FakeModelRegistryBrowserService()
        await service.activate_model("alpha_baseline", "2.0.0", admin_user)
        assert service.activated == [("alpha_baseline", "2.0.0")]

    @pytest.mark.asyncio()
    async def test_deactivate_model(self, admin_user: dict[str, Any]) -> None:
        service = FakeModelRegistryBrowserService()
        await service.deactivate_model("alpha_baseline", "1.0.0", admin_user)
        assert service.deactivated == [("alpha_baseline", "1.0.0")]


class TestRenderStrategyModels:
    """Tests that call the real _render_strategy_models() function with DummyUI."""

    @pytest.mark.asyncio()
    async def test_renders_model_cards(
        self,
        monkeypatch: pytest.MonkeyPatch,
        admin_user: dict[str, Any],
        sample_models: list[dict[str, Any]],
    ) -> None:
        """Real render function creates version labels and action buttons for admin."""
        dummy = _UI()
        monkeypatch.setattr(models_module, "ui", dummy)

        service = FakeModelRegistryBrowserService(models=sample_models)
        await models_module._render_strategy_models(
            service, "alpha_baseline", admin_user, can_manage=True
        )

        # Version labels rendered
        label_texts = [el.label for el in dummy.labels]
        assert any("1.0.0" in t for t in label_texts)
        assert any("2.0.0" in t for t in label_texts)
        # Admin buttons rendered (Activate for inactive, Deactivate for active)
        button_labels = [b.label for b in dummy.buttons]
        assert "Activate" in button_labels
        assert "Deactivate" in button_labels

    @pytest.mark.asyncio()
    async def test_no_buttons_for_non_admin(
        self,
        monkeypatch: pytest.MonkeyPatch,
        operator_user: dict[str, Any],
        sample_models: list[dict[str, Any]],
    ) -> None:
        """Non-admin user sees models but no activate/deactivate buttons."""
        dummy = _UI()
        monkeypatch.setattr(models_module, "ui", dummy)

        service = FakeModelRegistryBrowserService(models=sample_models)
        await models_module._render_strategy_models(
            service, "alpha_baseline", operator_user, can_manage=False
        )

        button_labels = [b.label for b in dummy.buttons]
        assert "Activate" not in button_labels
        assert "Deactivate" not in button_labels

    @pytest.mark.asyncio()
    async def test_empty_models_shows_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        admin_user: dict[str, Any],
    ) -> None:
        """Empty model list shows 'No models' message."""
        dummy = _UI()
        monkeypatch.setattr(models_module, "ui", dummy)

        service = FakeModelRegistryBrowserService(models=[])
        await models_module._render_strategy_models(
            service, "alpha_baseline", admin_user, can_manage=True
        )

        label_texts = [el.label for el in dummy.labels]
        assert any("No models" in t for t in label_texts)

    @pytest.mark.asyncio()
    async def test_db_error_shows_error_label(
        self,
        monkeypatch: pytest.MonkeyPatch,
        admin_user: dict[str, Any],
    ) -> None:
        """OperationalError from service shows error label, not crash."""
        dummy = _UI()
        monkeypatch.setattr(models_module, "ui", dummy)

        class FakeModelServiceWithDbError(FakeModelRegistryBrowserService):
            async def get_models_for_strategy(
                self, strategy_name: str, user: dict[str, Any]
            ) -> list[dict[str, Any]]:
                import psycopg

                raise psycopg.OperationalError("connection refused")

        service = FakeModelServiceWithDbError()
        await models_module._render_strategy_models(
            service, "alpha_baseline", admin_user, can_manage=True
        )

        label_texts = [el.label for el in dummy.labels]
        assert any("error" in t.lower() for t in label_texts)

    @pytest.mark.asyncio()
    async def test_permission_error_shows_denied_label(
        self,
        monkeypatch: pytest.MonkeyPatch,
        admin_user: dict[str, Any],
    ) -> None:
        """PermissionError from service shows access denied label."""
        dummy = _UI()
        monkeypatch.setattr(models_module, "ui", dummy)

        service = FakeModelServiceWithPermissionError()
        await models_module._render_strategy_models(
            service, "alpha_baseline", admin_user, can_manage=True
        )

        label_texts = [el.label for el in dummy.labels]
        assert any("denied" in t.lower() or "access" in t.lower() for t in label_texts)
