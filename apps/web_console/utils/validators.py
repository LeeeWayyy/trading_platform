"""Schema validation helpers for risk dashboard data.

This module provides validation functions to ensure data from RiskService
has the required structure before rendering in charts/components.

Validation follows a filter-based approach: invalid entries are filtered out
rather than raising exceptions, allowing partial data to be displayed.
"""

from __future__ import annotations

from typing import Any

# Required keys for risk metrics data
RISK_METRICS_REQUIRED_KEYS = frozenset({"total_risk", "var_95", "var_99", "cvar_95"})

# Required keys for factor exposure entries
EXPOSURE_REQUIRED_KEYS = frozenset({"factor_name", "exposure"})

# Required keys for stress test results
STRESS_TEST_REQUIRED_KEYS = frozenset({"scenario_name", "portfolio_pnl"})

# Required keys for VaR history entries
VAR_HISTORY_REQUIRED_KEYS = frozenset({"date", "var_95"})


def validate_risk_metrics(data: dict[str, Any] | None) -> bool:
    """Validate risk_metrics dict has required keys.

    Args:
        data: Risk metrics dictionary from RiskService

    Returns:
        True if all required keys present and non-None, False otherwise
    """
    if not data:
        return False
    return all(
        key in data and data[key] is not None
        for key in RISK_METRICS_REQUIRED_KEYS
    )


def validate_exposures(exposures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter exposures to only valid entries.

    Args:
        exposures: List of factor exposure dicts from RiskService

    Returns:
        Filtered list containing only entries with all required keys
    """
    if not exposures:
        return []
    return [
        e for e in exposures
        if isinstance(e, dict) and EXPOSURE_REQUIRED_KEYS.issubset(e.keys())
    ]


def validate_stress_tests(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter stress tests to only valid entries.

    Args:
        results: List of stress test result dicts from RiskService

    Returns:
        Filtered list containing only entries with all required keys
    """
    if not results:
        return []
    return [
        r for r in results
        if isinstance(r, dict) and STRESS_TEST_REQUIRED_KEYS.issubset(r.keys())
    ]


def validate_var_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter VaR history to only valid entries.

    Args:
        history: List of VaR history dicts from RiskService

    Returns:
        Filtered list containing only entries with all required keys
    """
    if not history:
        return []
    return [
        h for h in history
        if isinstance(h, dict) and VAR_HISTORY_REQUIRED_KEYS.issubset(h.keys())
    ]


def validate_exposure_list(exposures: list[dict[str, Any]] | None) -> bool:
    """Check if exposure list is valid (not None, entries have required keys).

    Args:
        exposures: List of factor exposure dicts

    Returns:
        True if list is valid (empty list is valid), False if None or has invalid entries
    """
    if exposures is None:
        return False
    if not exposures:
        return True  # Empty list is valid
    return all(
        isinstance(e, dict) and EXPOSURE_REQUIRED_KEYS.issubset(e.keys())
        for e in exposures
    )


def validate_stress_test_list(results: list[dict[str, Any]] | None) -> bool:
    """Check if stress test list is valid (not None, entries have required keys).

    Args:
        results: List of stress test result dicts

    Returns:
        True if list is valid (empty list is valid), False if None or has invalid entries
    """
    if results is None:
        return False
    if not results:
        return True  # Empty list is valid
    return all(
        isinstance(r, dict) and STRESS_TEST_REQUIRED_KEYS.issubset(r.keys())
        for r in results
    )


__all__ = [
    "RISK_METRICS_REQUIRED_KEYS",
    "EXPOSURE_REQUIRED_KEYS",
    "STRESS_TEST_REQUIRED_KEYS",
    "VAR_HISTORY_REQUIRED_KEYS",
    "validate_risk_metrics",
    "validate_exposures",
    "validate_stress_tests",
    "validate_var_history",
    "validate_exposure_list",
    "validate_stress_test_list",
]
