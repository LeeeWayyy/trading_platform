"""Dark theme constants and utilities for professional trading terminal."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class SurfaceLevels:
    """Material Design-inspired surface elevation for dark mode."""

    LEVEL_0: ClassVar[str] = "#121212"  # App background
    LEVEL_1: ClassVar[str] = "#1E1E1E"  # Cards, panels, sidebar
    LEVEL_2: ClassVar[str] = "#2D2D2D"  # Popups, modals, dropdowns
    LEVEL_3: ClassVar[str] = "#383838"  # Tooltips, overlays
    LEVEL_4: ClassVar[str] = "#454545"  # Elevated buttons, active states


@dataclass(frozen=True)
class SemanticColors:
    """Trading-specific semantic colors with high contrast for dark backgrounds."""

    PROFIT: ClassVar[str] = "#00E676"
    LOSS: ClassVar[str] = "#FF5252"
    BUY: ClassVar[str] = "#00E676"
    SELL: ClassVar[str] = "#FF5252"
    WARNING: ClassVar[str] = "#FFB300"
    INFO: ClassVar[str] = "#2196F3"
    NEUTRAL: ClassVar[str] = "#90A4AE"
    ACTIVE: ClassVar[str] = "#00E676"
    INACTIVE: ClassVar[str] = "#757575"
    PENDING: ClassVar[str] = "#FFB300"
    TEXT_PRIMARY: ClassVar[str] = "#FFFFFF"
    TEXT_SECONDARY: ClassVar[str] = "#B0B0B0"
    TEXT_DISABLED: ClassVar[str] = "#757575"


@dataclass(frozen=True)
class DarkTheme:
    """Complete dark theme configuration."""

    surface: ClassVar[type[SurfaceLevels]] = SurfaceLevels
    semantic: ClassVar[type[SemanticColors]] = SemanticColors

    @classmethod
    def get_tailwind_config(cls) -> dict[str, str]:
        """Return Tailwind-compatible color mappings."""
        return {
            "bg-surface-0": f"background-color: {SurfaceLevels.LEVEL_0}",
            "bg-surface-1": f"background-color: {SurfaceLevels.LEVEL_1}",
            "bg-surface-2": f"background-color: {SurfaceLevels.LEVEL_2}",
            "bg-surface-3": f"background-color: {SurfaceLevels.LEVEL_3}",
            "text-profit": f"color: {SemanticColors.PROFIT}",
            "text-loss": f"color: {SemanticColors.LOSS}",
            "text-warning": f"color: {SemanticColors.WARNING}",
            "text-info": f"color: {SemanticColors.INFO}",
        }


def enable_dark_mode() -> None:
    """Enable dark mode globally via NiceGUI."""
    from nicegui import ui

    ui.dark_mode().enable()


def get_pnl_color(value: float) -> str:
    """Return appropriate color for P&L value."""
    if value >= 0:
        return SemanticColors.PROFIT
    return SemanticColors.LOSS


def get_side_color(side: str) -> str:
    """Return color for buy/sell side."""
    if side.lower() == "buy":
        return SemanticColors.BUY
    return SemanticColors.SELL


__all__ = [
    "SurfaceLevels",
    "SemanticColors",
    "DarkTheme",
    "enable_dark_mode",
    "get_pnl_color",
    "get_side_color",
]
