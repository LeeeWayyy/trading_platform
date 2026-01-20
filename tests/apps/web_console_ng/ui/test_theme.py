"""Tests for shared theme constants."""

from __future__ import annotations

from apps.web_console_ng.ui import theme as theme_module


def test_connection_badge_remove_classes_cover_all_states() -> None:
    expected = {
        theme_module.CONNECTION_CONNECTED,
        theme_module.CONNECTION_DEGRADED,
        theme_module.CONNECTION_RECONNECTING,
        theme_module.CONNECTION_DISCONNECTED,
        theme_module.CONNECTION_STALE,
    }
    remove_classes = set(theme_module.CONNECTION_BADGE_REMOVE_CLASSES.split())
    for value in expected:
        # Check that all classes in this state are covered by remove classes
        for cls in value.split():
            assert cls in remove_classes, f"{cls} from {value} not in remove classes"


def test_latency_badge_remove_classes_cover_all_states() -> None:
    expected = {
        theme_module.LATENCY_GOOD,
        theme_module.LATENCY_DEGRADED,
        theme_module.LATENCY_POOR,
        theme_module.LATENCY_DISCONNECTED,
    }
    remove_classes = set(theme_module.LATENCY_BADGE_REMOVE_CLASSES.split())
    for value in expected:
        for cls in value.split():
            assert cls in remove_classes, f"{cls} from {value} not in remove classes"


def test_market_clock_remove_classes_cover_all_states() -> None:
    expected = {
        theme_module.MARKET_DEFAULT,
        theme_module.MARKET_CRYPTO,
        theme_module.MARKET_OPEN,
        theme_module.MARKET_PRE_MARKET,
        theme_module.MARKET_POST_MARKET,
        theme_module.MARKET_CLOSED,
    }
    remove_classes = set(theme_module.MARKET_CLOCK_REMOVE_CLASSES.split())
    for value in expected:
        for cls in value.split():
            assert cls in remove_classes, f"{cls} from {value} not in remove classes"


def test_theme_exports_are_complete() -> None:
    for name in [
        "CONNECTION_BADGE_REMOVE_CLASSES",
        "CONNECTION_CONNECTED",
        "CONNECTION_DEGRADED",
        "CONNECTION_DISCONNECTED",
        "CONNECTION_RECONNECTING",
        "CONNECTION_STALE",
        "DAY_CHANGE_NEGATIVE",
        "DAY_CHANGE_POSITIVE",
        "LATENCY_BADGE_REMOVE_CLASSES",
        "LATENCY_DEGRADED",
        "LATENCY_DISCONNECTED",
        "LATENCY_GOOD",
        "LATENCY_POOR",
        "LEVERAGE_GREEN",
        "LEVERAGE_NEUTRAL",
        "LEVERAGE_RED",
        "LEVERAGE_YELLOW",
        "MARKET_CLOCK_REMOVE_CLASSES",
        "MARKET_CLOSED",
        "MARKET_CRYPTO",
        "MARKET_DEFAULT",
        "MARKET_OPEN",
        "MARKET_POST_MARKET",
        "MARKET_PRE_MARKET",
    ]:
        assert name in theme_module.__all__
