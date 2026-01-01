from __future__ import annotations

from typing import Any

from apps.web_console_ng.pages import forgot_password as forgot_password_module


class _FakeContext:
    def __init__(self, ui: _FakeUI) -> None:
        self.ui = ui

    def __enter__(self) -> _FakeContext:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def classes(self, *_args: Any, **_kwargs: Any) -> _FakeContext:
        return self

    def props(self, *_args: Any, **_kwargs: Any) -> _FakeContext:
        return self


class _FakeUI:
    def __init__(self) -> None:
        self.labels: list[str] = []
        self.links: list[tuple[str, str]] = []

    def card(self, *_args: Any, **_kwargs: Any) -> _FakeContext:
        return _FakeContext(self)

    def label(self, text: str) -> _FakeContext:
        self.labels.append(text)
        return _FakeContext(self)

    def link(self, text: str, target: str) -> _FakeContext:
        self.links.append((text, target))
        return _FakeContext(self)


def test_forgot_password_page() -> None:
    fake_ui = _FakeUI()
    forgot_password_module.ui = fake_ui  # type: ignore[assignment]

    forgot_password_module.forgot_password_page()

    assert "Forgot Password" in fake_ui.labels
    assert "Password reset is not available yet." in fake_ui.labels
    assert ("Back to login", "/login") in fake_ui.links
