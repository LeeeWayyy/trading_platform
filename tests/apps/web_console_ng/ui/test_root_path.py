"""Tests for UI-rooted path helper."""

from __future__ import annotations

from types import SimpleNamespace

from apps.web_console_ng.ui.root_path import render_client_redirect, resolve_rooted_path_from_ui


class _DummyElement:
    def classes(self, *_args: object, **_kwargs: object) -> _DummyElement:
        return self


class _DummyColumn:
    def classes(self, *_args: object, **_kwargs: object) -> _DummyColumn:
        return self

    def __enter__(self) -> _DummyColumn:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        return None


class _DummyUI:
    def __init__(self, *, fail_js: bool = False) -> None:
        self.fail_js = fail_js
        self.js_calls: list[str] = []
        self.labels: list[str] = []
        self.links: list[tuple[str, str]] = []

    def run_javascript(self, script: str) -> None:
        if self.fail_js:
            raise RuntimeError("simulated-js-failure")
        self.js_calls.append(script)

    def column(self) -> _DummyColumn:
        return _DummyColumn()

    def label(self, text: str) -> _DummyElement:
        self.labels.append(text)
        return _DummyElement()

    def link(self, text: str, *, target: str) -> _DummyElement:
        self.links.append((text, target))
        return _DummyElement()


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


def test_render_client_redirect_renders_js_and_fallback_link() -> None:
    ui_module = _DummyUI()

    render_client_redirect(
        "/console/data/sql-explorer?tab=history&limit=50",
        ui_module=ui_module,
        message="Redirecting to SQL Explorer...",
    )

    assert ui_module.js_calls == [
        'window.location.replace("/console/data/sql-explorer?tab=history&limit=50");'
    ]
    assert ui_module.labels == ["Redirecting to SQL Explorer..."]
    assert ui_module.links == [("Continue", "/console/data/sql-explorer?tab=history&limit=50")]


def test_render_client_redirect_still_renders_link_when_js_fails() -> None:
    ui_module = _DummyUI(fail_js=True)

    render_client_redirect("/console/data", ui_module=ui_module)

    assert ui_module.js_calls == []
    assert ui_module.labels == ["Redirecting to canonical page..."]
    assert ui_module.links == [("Continue", "/console/data")]


def test_render_client_redirect_escapes_js_payloads() -> None:
    ui_module = _DummyUI()
    target = '/x?q=<script>alert("x")</script>&next=/trade'

    render_client_redirect(target, ui_module=ui_module)

    assert ui_module.js_calls == [
        'window.location.replace("/x?q=<script>alert(\\"x\\")<\\/script>&next=/trade");'
    ]
