"""Shared theme constants for consistent UI styling.

This module centralizes status colors and CSS classes used across web console
components to ensure visual consistency and simplify future theme updates.
"""

from __future__ import annotations

# =============================================================================
# Status Badge Colors
# =============================================================================

# Connection status colors (used by ConnectionMonitor)
CONNECTION_CONNECTED = "bg-green-500 text-white"
CONNECTION_DEGRADED = "bg-yellow-500 text-black"
CONNECTION_RECONNECTING = "bg-gray-500 text-white"
CONNECTION_DISCONNECTED = "bg-red-500 text-white"
CONNECTION_STALE = "bg-yellow-500 text-black"

# All connection badge classes for removal during state transitions
CONNECTION_BADGE_REMOVE_CLASSES = (
    "bg-green-500 bg-yellow-500 bg-red-500 bg-gray-500 text-white text-black"
)

# Latency status colors (used by LatencyMonitor)
LATENCY_GOOD = "bg-green-600 text-white"
LATENCY_DEGRADED = "bg-orange-500 text-white"
LATENCY_POOR = "bg-red-600 text-white"
LATENCY_DISCONNECTED = "bg-gray-500 text-white"

# All latency badge classes for removal during state transitions
LATENCY_BADGE_REMOVE_CLASSES = "bg-green-600 bg-orange-500 bg-red-600 bg-gray-500 text-white"

# =============================================================================
# Header Metrics Colors
# =============================================================================

# Leverage indicator colors
LEVERAGE_GREEN = "bg-green-600 text-white"
LEVERAGE_YELLOW = "bg-yellow-500 text-black"
LEVERAGE_RED = "bg-red-600 text-white"
LEVERAGE_NEUTRAL = "bg-gray-600 text-white"

# Day change colors
DAY_CHANGE_POSITIVE = "text-green-400"
DAY_CHANGE_NEGATIVE = "text-red-400"

# =============================================================================
# Market Clock Colors
# =============================================================================

# Market session state colors
MARKET_CRYPTO = "bg-blue-600 text-white"
MARKET_OPEN = "bg-green-600 text-white"
MARKET_PRE_MARKET = "bg-yellow-500 text-black"
MARKET_POST_MARKET = "bg-yellow-500 text-black"
MARKET_CLOSED = "bg-gray-600 text-white"
MARKET_DEFAULT = "bg-slate-700 text-white"

# All market clock classes for removal during state transitions
MARKET_CLOCK_REMOVE_CLASSES = (
    "bg-slate-700 bg-blue-600 bg-green-600 bg-yellow-500 bg-gray-600 " "text-white text-black"
)


__all__ = [
    # Connection
    "CONNECTION_BADGE_REMOVE_CLASSES",
    "CONNECTION_CONNECTED",
    "CONNECTION_DEGRADED",
    "CONNECTION_DISCONNECTED",
    "CONNECTION_RECONNECTING",
    "CONNECTION_STALE",
    # Day change
    "DAY_CHANGE_NEGATIVE",
    "DAY_CHANGE_POSITIVE",
    # Latency
    "LATENCY_BADGE_REMOVE_CLASSES",
    "LATENCY_DEGRADED",
    "LATENCY_DISCONNECTED",
    "LATENCY_GOOD",
    "LATENCY_POOR",
    # Leverage
    "LEVERAGE_GREEN",
    "LEVERAGE_NEUTRAL",
    "LEVERAGE_RED",
    "LEVERAGE_YELLOW",
    # Market clock
    "MARKET_CLOCK_REMOVE_CLASSES",
    "MARKET_CLOSED",
    "MARKET_CRYPTO",
    "MARKET_DEFAULT",
    "MARKET_OPEN",
    "MARKET_POST_MARKET",
    "MARKET_PRE_MARKET",
]
