"""Schema validation helpers for risk dashboard data.

This module provides validation functions to ensure data from RiskService
has the required structure before rendering in charts/components.

Validation follows a filter-based approach: invalid entries are filtered out
rather than raising exceptions, allowing partial data to be displayed.
"""

from __future__ import annotations

from typing import Any

# Required keys for risk metrics data (original combined set for backwards compatibility)
RISK_METRICS_REQUIRED_KEYS = frozenset({"total_risk", "var_95", "var_99", "cvar_95"})

# Section-specific key sets for more granular validation
RISK_OVERVIEW_REQUIRED_KEYS = frozenset({"total_risk"})  # factor_risk, specific_risk optional
VAR_METRICS_REQUIRED_KEYS = frozenset({"var_95", "var_99", "cvar_95"})

# Required keys for factor exposure entries
EXPOSURE_REQUIRED_KEYS = frozenset({"factor_name", "exposure"})


def _validate_metrics_keys(data: dict[str, Any] | None, required_keys: frozenset[str]) -> bool:
    """Generic validator for required, non-None keys in a metrics dictionary.

    Args:
        data: Dictionary to validate
        required_keys: Set of keys that must be present and non-None

    Returns:
        True if all required keys are present and non-None, False otherwise
    """
    if not data:
        return False
    return all(key in data and data[key] is not None for key in required_keys)


# Required keys for stress test results
STRESS_TEST_REQUIRED_KEYS = frozenset({"scenario_name", "portfolio_pnl"})

# Required keys for VaR history entries
VAR_HISTORY_REQUIRED_KEYS = frozenset({"date", "var_95"})


def validate_risk_metrics(data: dict[str, Any] | None) -> bool:
    """Validate risk_metrics dict has required keys (legacy combined validator).

    Note: Prefer section-specific validators (validate_overview_metrics,
    validate_var_metrics) for NiceGUI components to avoid blocking one section
    when only another is missing data.

    Args:
        data: Risk metrics dictionary from RiskService

    Returns:
        True if all required keys present and non-None, False otherwise
    """
    return _validate_metrics_keys(data, RISK_METRICS_REQUIRED_KEYS)


def validate_overview_metrics(data: dict[str, Any] | None) -> bool:
    """Validate metrics for risk overview section (total_risk display).

    Args:
        data: Risk metrics dictionary from RiskService

    Returns:
        True if total_risk key present and non-None, False otherwise
    """
    return _validate_metrics_keys(data, RISK_OVERVIEW_REQUIRED_KEYS)


def validate_var_metrics(data: dict[str, Any] | None) -> bool:
    """Validate metrics for VaR display section (var_95, var_99, cvar_95).

    Args:
        data: Risk metrics dictionary from RiskService

    Returns:
        True if all VaR keys present and non-None, False otherwise
    """
    return _validate_metrics_keys(data, VAR_METRICS_REQUIRED_KEYS)


def validate_exposure_list(exposures: list[dict[str, Any]] | None) -> bool:
    """Boolean validator for factor exposure lists.

    Empty lists are considered valid so the UI can render an empty chart
    without failing. None is treated as invalid to surface missing data.
    """
    if exposures is None:
        return False
    if not exposures:
        return True
    return all(isinstance(e, dict) and EXPOSURE_REQUIRED_KEYS.issubset(e.keys()) for e in exposures)


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
        e for e in exposures if isinstance(e, dict) and EXPOSURE_REQUIRED_KEYS.issubset(e.keys())
    ]


def validate_stress_test_list(results: list[dict[str, Any]] | None) -> bool:
    """Boolean validator for stress test result lists."""
    if results is None:
        return False
    if not results:
        return True
    return all(
        isinstance(r, dict) and STRESS_TEST_REQUIRED_KEYS.issubset(r.keys()) for r in results
    )


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
        r for r in results if isinstance(r, dict) and STRESS_TEST_REQUIRED_KEYS.issubset(r.keys())
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
        h for h in history if isinstance(h, dict) and VAR_HISTORY_REQUIRED_KEYS.issubset(h.keys())
    ]


__all__ = [
    "RISK_METRICS_REQUIRED_KEYS",
    "RISK_OVERVIEW_REQUIRED_KEYS",
    "VAR_METRICS_REQUIRED_KEYS",
    "EXPOSURE_REQUIRED_KEYS",
    "STRESS_TEST_REQUIRED_KEYS",
    "VAR_HISTORY_REQUIRED_KEYS",
    "validate_risk_metrics",
    "validate_overview_metrics",
    "validate_var_metrics",
    "validate_exposure_list",
    "validate_exposures",
    "validate_stress_test_list",
    "validate_stress_tests",
    "validate_var_history",
]
