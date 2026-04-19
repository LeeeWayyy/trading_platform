"""Tests for UI-rooted path helper."""

from __future__ import annotations

from types import SimpleNamespace

from apps.web_console_ng.ui.root_path import resolve_rooted_path_from_ui


def test_resolve_rooted_path_from_ui_uses_request_root_path() -> None:
    ui_module = SimpleNamespace(
        context=SimpleNamespace(
            client=SimpleNamespace(
                request=SimpleNamespace(scope={"root_path": "/console"}),
            ),
        ),
    )

    assert resolve_rooted_path_from_ui("/login", ui_module=ui_module) == "/console/login"


def test_resolve_rooted_path_from_ui_handles_missing_context() -> None:
    ui_module = SimpleNamespace()

    assert resolve_rooted_path_from_ui("/login", ui_module=ui_module) == "/login"


def test_resolve_rooted_path_from_ui_handles_lookup_error() -> None:
    class _LookupContext:
        @property
        def client(self) -> object:
            raise LookupError("request context unavailable")

    ui_module = SimpleNamespace(context=_LookupContext())

    assert resolve_rooted_path_from_ui("/login", ui_module=ui_module) == "/login"


def test_resolve_rooted_path_from_ui_handles_scope_without_root_path() -> None:
    ui_module = SimpleNamespace(
        context=SimpleNamespace(
            client=SimpleNamespace(
                request=SimpleNamespace(scope={"path": "/"}),
            ),
        ),
    )

    assert resolve_rooted_path_from_ui("/login", ui_module=ui_module) == "/login"

