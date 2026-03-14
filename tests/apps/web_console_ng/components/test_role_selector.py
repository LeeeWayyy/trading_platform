"""Tests for role_selector NiceGUI component (T16.2)."""

from __future__ import annotations

from typing import Any

import pytest

from apps.web_console_ng.components import role_selector as selector_module

# ============================================================================
# Mocks
# ============================================================================


class DummyElement:
    """Minimal NiceGUI element mock with class/props chaining."""

    def __init__(self, text: str = "", value: Any = None, **kwargs: Any) -> None:
        self.text = text
        self.value = value
        self.kwargs = kwargs
        self._event_handlers: dict[str, Any] = {}

    def classes(self, *args: Any, **kwargs: Any) -> DummyElement:
        return self

    def props(self, _p: str) -> DummyElement:
        return self

    def on(self, event: str, handler: Any) -> DummyElement:
        self._event_handlers[event] = handler
        return self

    def set_text(self, text: str) -> None:
        self.text = text

    def __enter__(self) -> DummyElement:
        return self

    def __exit__(self, *a: Any) -> bool:
        return False


class DummyDialog:
    """Mock dialog with open/close tracking."""

    def __init__(self) -> None:
        self.is_open = False
        self._closed = False

    def open(self) -> None:
        self.is_open = True

    def close(self) -> None:
        self._closed = True
        self.is_open = False

    def __enter__(self) -> DummyDialog:
        return self

    def __exit__(self, *a: Any) -> bool:
        return False


class DummyUI:
    """Capture NiceGUI calls for assertions."""

    def __init__(self) -> None:
        self.labels: list[DummyElement] = []
        self.selects: list[DummyElement] = []
        self.inputs: list[DummyElement] = []
        self.buttons: list[dict[str, Any]] = []
        self.rows: list[DummyElement] = []
        self.dialog_instance = DummyDialog()
        self.notify_calls: list[tuple[str, dict[str, Any]]] = []

    def dialog(self) -> DummyDialog:
        return self.dialog_instance

    def card(self) -> DummyElement:
        return DummyElement()

    def label(self, text: str = "") -> DummyElement:
        el = DummyElement(text=text)
        self.labels.append(el)
        return el

    def select(self, *, options: Any = None, value: Any = None, label: str = "") -> DummyElement:
        el = DummyElement(value=value)
        el.kwargs["options"] = options
        self.selects.append(el)
        return el

    def input(self, label: str = "") -> DummyElement:
        el = DummyElement()
        self.inputs.append(el)
        return el

    def button(self, text: str = "", **kwargs: Any) -> DummyElement:
        self.buttons.append({"text": text, **kwargs})
        return DummyElement()

    def row(self) -> DummyElement:
        el = DummyElement()
        self.rows.append(el)
        return el

    def notify(self, message: str, **kwargs: Any) -> None:
        self.notify_calls.append((message, kwargs))


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    ui = DummyUI()
    monkeypatch.setattr(selector_module, "ui", ui)
    return ui


# ============================================================================
# Tests
# ============================================================================


@pytest.mark.unit()
class TestRenderRoleChangeDialog:
    """Tests for render_role_change_dialog."""

    def test_dialog_opens(self, dummy_ui: DummyUI) -> None:
        """Dialog is opened after rendering."""

        async def _noop(uid: str, new_role: str, reason: str) -> None:
            pass

        selector_module.render_role_change_dialog("user-1", "viewer", on_confirm=_noop)

        assert dummy_ui.dialog_instance.is_open

    def test_labels_contain_user_id_and_role(self, dummy_ui: DummyUI) -> None:
        """Dialog labels show the target user_id and current role."""

        async def _noop(uid: str, new_role: str, reason: str) -> None:
            pass

        selector_module.render_role_change_dialog("alice", "researcher", on_confirm=_noop)

        label_texts = [el.text for el in dummy_ui.labels]
        assert any("alice" in t for t in label_texts)
        assert any("researcher" in t for t in label_texts)

    def test_all_four_canonical_roles_offered(self, dummy_ui: DummyUI) -> None:
        """The role select offers all 4 canonical roles: viewer, researcher, operator, admin."""

        async def _noop(uid: str, new_role: str, reason: str) -> None:
            pass

        selector_module.render_role_change_dialog("user-1", "viewer", on_confirm=_noop)

        assert len(dummy_ui.selects) == 1
        options = dummy_ui.selects[0].kwargs["options"]
        assert "viewer" in options
        assert "researcher" in options
        assert "operator" in options
        assert "admin" in options
        assert len(options) == 4

    def test_current_role_is_preselected(self, dummy_ui: DummyUI) -> None:
        """The select widget has the current role as its initial value."""

        async def _noop(uid: str, new_role: str, reason: str) -> None:
            pass

        selector_module.render_role_change_dialog("user-1", "operator", on_confirm=_noop)

        assert dummy_ui.selects[0].value == "operator"

    def test_reason_input_present(self, dummy_ui: DummyUI) -> None:
        """A text input for the change reason is rendered."""

        async def _noop(uid: str, new_role: str, reason: str) -> None:
            pass

        selector_module.render_role_change_dialog("user-1", "viewer", on_confirm=_noop)

        assert len(dummy_ui.inputs) == 1

    def test_cancel_and_confirm_buttons(self, dummy_ui: DummyUI) -> None:
        """Both Cancel and Confirm buttons are rendered."""

        async def _noop(uid: str, new_role: str, reason: str) -> None:
            pass

        selector_module.render_role_change_dialog("user-1", "viewer", on_confirm=_noop)

        button_texts = [b["text"] for b in dummy_ui.buttons]
        assert "Cancel" in button_texts
        assert "Confirm" in button_texts


__all__: list[str] = []
