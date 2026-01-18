"""Tests for dark theme utilities."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.web_console_ng.ui import dark_theme as theme_module


class _DummyDarkMode:
    def __init__(self) -> None:
        self.enabled = False

    def enable(self) -> None:
        self.enabled = True


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> _DummyDarkMode:
    dark_mode = _DummyDarkMode()
    dummy = SimpleNamespace(dark_mode=lambda: dark_mode)
    # Mock nicegui.ui since enable_dark_mode imports it inside the function
    monkeypatch.setattr("nicegui.ui", dummy)
    return dark_mode


def test_tailwind_config_contains_expected_colors() -> None:
    config = theme_module.DarkTheme.get_tailwind_config()

    assert config["bg-surface-0"] == f"background-color: {theme_module.SurfaceLevels.LEVEL_0}"
    assert config["bg-surface-1"] == f"background-color: {theme_module.SurfaceLevels.LEVEL_1}"
    assert config["bg-surface-2"] == f"background-color: {theme_module.SurfaceLevels.LEVEL_2}"
    assert config["bg-surface-3"] == f"background-color: {theme_module.SurfaceLevels.LEVEL_3}"
    assert config["text-profit"] == f"color: {theme_module.SemanticColors.PROFIT}"
    assert config["text-loss"] == f"color: {theme_module.SemanticColors.LOSS}"
    assert config["text-warning"] == f"color: {theme_module.SemanticColors.WARNING}"
    assert config["text-info"] == f"color: {theme_module.SemanticColors.INFO}"


def test_enable_dark_mode_calls_nicegui(dummy_ui: _DummyDarkMode) -> None:
    theme_module.enable_dark_mode()

    assert dummy_ui.enabled is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (10.0, theme_module.SemanticColors.PROFIT),
        (0.0, theme_module.SemanticColors.PROFIT),
        (-0.01, theme_module.SemanticColors.LOSS),
    ],
)
def test_get_pnl_color(value: float, expected: str) -> None:
    assert theme_module.get_pnl_color(value) == expected


@pytest.mark.parametrize(
    ("side", "expected"),
    [
        ("buy", theme_module.SemanticColors.BUY),
        ("BUY", theme_module.SemanticColors.BUY),
        ("sell", theme_module.SemanticColors.SELL),
    ],
)
def test_get_side_color(side: str, expected: str) -> None:
    assert theme_module.get_side_color(side) == expected
