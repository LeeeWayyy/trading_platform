"""Tests for user_table NiceGUI component (T16.2)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from apps.web_console_ng.components import user_table as table_module

# ============================================================================
# Mocks
# ============================================================================


class DummyElement:
    """Minimal NiceGUI element mock with class/props/style chaining."""

    def __init__(self, text: str = "", **kwargs: Any) -> None:
        self.text = text
        self.kwargs = kwargs

    def classes(self, *args: Any, **kwargs: Any) -> DummyElement:
        return self

    def props(self, _p: str) -> DummyElement:
        return self

    def style(self, _s: str) -> DummyElement:
        return self

    def __enter__(self) -> DummyElement:
        return self

    def __exit__(self, *a: Any) -> bool:
        return False


class DummyUI:
    """Capture NiceGUI calls for assertions."""

    def __init__(self) -> None:
        self.aggrid_calls: list[dict[str, Any]] = []
        self.button_calls: list[dict[str, Any]] = []
        self.row_calls: list[DummyElement] = []
        self.notify_calls: list[tuple[str, dict[str, Any]]] = []

    def aggrid(self, options: dict[str, Any]) -> DummyElement:
        self.aggrid_calls.append(options)
        return DummyElement()

    def button(self, text: str = "", **kwargs: Any) -> DummyElement:
        self.button_calls.append({"text": text, **kwargs})
        return DummyElement()

    def row(self) -> DummyElement:
        el = DummyElement()
        self.row_calls.append(el)
        return el

    def notify(self, message: str, **kwargs: Any) -> None:
        self.notify_calls.append((message, kwargs))


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    ui = DummyUI()
    monkeypatch.setattr(table_module, "ui", ui)
    return ui


# ============================================================================
# Helpers
# ============================================================================


def _make_user(
    user_id: str = "user-1",
    role: str = "viewer",
    strategy_count: int = 0,
    updated_at: str = "2026-03-01T00:00:00Z",
    updated_by: str = "admin-1",
) -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        role=role,
        strategy_count=strategy_count,
        updated_at=updated_at,
        updated_by=updated_by,
    )


# ============================================================================
# Tests
# ============================================================================


@pytest.mark.unit()
class TestRenderUserTable:
    """Tests for render_user_table."""

    def test_aggrid_columns(self, dummy_ui: DummyUI) -> None:
        """AG Grid is created with the expected column definitions."""
        users = [_make_user()]
        table_module.render_user_table(users)

        assert len(dummy_ui.aggrid_calls) == 1
        opts = dummy_ui.aggrid_calls[0]
        fields = [col["field"] for col in opts["columnDefs"]]
        assert fields == ["user_id", "role", "strategy_count", "updated_at", "updated_by"]

    def test_aggrid_rows(self, dummy_ui: DummyUI) -> None:
        """Row data matches supplied user objects."""
        users = [
            _make_user(user_id="alice", role="admin", strategy_count=3, updated_by="bob"),
            _make_user(user_id="charlie", role="viewer", strategy_count=0, updated_by=""),
        ]
        table_module.render_user_table(users)

        opts = dummy_ui.aggrid_calls[0]
        rows = opts["rowData"]
        assert len(rows) == 2
        assert rows[0]["user_id"] == "alice"
        assert rows[0]["role"] == "admin"
        assert rows[0]["strategy_count"] == 3
        assert rows[0]["updated_by"] == "bob"
        # Empty updated_by becomes "-"
        assert rows[1]["updated_by"] == "-"

    def test_empty_user_list(self, dummy_ui: DummyUI) -> None:
        """Empty user list renders an AG Grid with zero rows."""
        table_module.render_user_table([])

        assert len(dummy_ui.aggrid_calls) == 1
        opts = dummy_ui.aggrid_calls[0]
        assert opts["rowData"] == []
        # Columns are still present
        assert len(opts["columnDefs"]) == 5

    def test_action_buttons_present_with_callbacks(self, dummy_ui: DummyUI) -> None:
        """All four action buttons appear when callbacks are provided."""
        users = [_make_user()]
        table_module.render_user_table(
            users,
            on_role_change=lambda uid: None,  # type: ignore[arg-type,return-value]
            on_view_strategies=lambda uid: None,  # type: ignore[arg-type,return-value]
            on_view_activity=lambda uid: None,  # type: ignore[arg-type,return-value]
            on_force_logout=lambda uid: None,  # type: ignore[arg-type,return-value]
        )

        button_texts = [b["text"] for b in dummy_ui.button_calls]
        assert "Change Role" in button_texts
        assert "Strategies" in button_texts
        assert "Activity" in button_texts
        assert "Force Logout" in button_texts

    def test_no_buttons_without_callbacks(self, dummy_ui: DummyUI) -> None:
        """No action buttons rendered when no callbacks provided."""
        users = [_make_user()]
        table_module.render_user_table(users)

        assert len(dummy_ui.button_calls) == 0

    def test_partial_callbacks(self, dummy_ui: DummyUI) -> None:
        """Only buttons for provided callbacks are rendered."""
        users = [_make_user()]
        table_module.render_user_table(
            users,
            on_role_change=lambda uid: None,  # type: ignore[arg-type,return-value]
            on_view_activity=lambda uid: None,  # type: ignore[arg-type,return-value]
        )

        button_texts = [b["text"] for b in dummy_ui.button_calls]
        assert "Change Role" in button_texts
        assert "Activity" in button_texts
        assert "Strategies" not in button_texts
        assert "Force Logout" not in button_texts

    def test_grid_options(self, dummy_ui: DummyUI) -> None:
        """Grid options include row selection and resizable columns."""
        table_module.render_user_table([_make_user()])

        opts = dummy_ui.aggrid_calls[0]
        assert opts["rowSelection"] == "multiple"
        assert opts["defaultColDef"]["resizable"] is True


__all__: list[str] = []
