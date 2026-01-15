"""Stress test results visualization component for NiceGUI risk dashboard.

Ported from apps/web_console/components/stress_test_results.py (Streamlit).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import plotly.graph_objects as go
from nicegui import ui

from apps.web_console_ng.components.factor_exposure_chart import FACTOR_DISPLAY_NAMES
from libs.core.common.validators import validate_stress_tests

# Predefined scenarios in display order
SCENARIO_DISPLAY_ORDER = [
    "GFC_2008",
    "COVID_2020",
    "RATE_HIKE_2022",
    "RATE_SHOCK",
]

# Scenario display names and descriptions
SCENARIO_INFO = {
    "GFC_2008": {
        "name": "Global Financial Crisis (2008)",
        "description": "Sep-Nov 2008 market crash scenario",
    },
    "COVID_2020": {
        "name": "COVID-19 Crash (2020)",
        "description": "Feb-Mar 2020 pandemic market crash",
    },
    "RATE_HIKE_2022": {
        "name": "Rate Hike Shock (2022)",
        "description": "2022 Fed rate hikes and inflation",
    },
    "RATE_SHOCK": {
        "name": "Hypothetical Rate Shock",
        "description": "Simulated rate shock with factor rotation",
    },
}


def _get_scenario_display_name(scenario_name: str) -> str:
    """Get human-readable display name for scenario."""
    info = SCENARIO_INFO.get(scenario_name, {})
    return info.get("name", scenario_name)


def _get_factor_display_name(factor_name: str) -> str:
    """Get human-readable display name for factor.

    L-2: Falls back to raw factor_name if not in FACTOR_DISPLAY_NAMES.
    """
    return FACTOR_DISPLAY_NAMES.get(factor_name, factor_name)


def render_stress_tests(results: Sequence[dict[str, Any]] | None) -> None:
    """Render stress test results table and charts.

    Shows:
    - Summary table with scenario name, P&L impact
    - Factor contribution waterfall for selected scenario
    - Worst case scenario highlight

    Args:
        results: List of stress test result dicts with scenario_name, portfolio_pnl,
                 factor_impacts keys
    """
    # Validate results (T6.2 Validator requirement)
    valid_results = validate_stress_tests(list(results or []))

    if not valid_results:
        ui.label("No stress test results available.").classes("text-gray-500 p-4")
        return

    # Render summary table
    ui.label("Stress Test Results").classes("text-xl font-semibold mb-4")
    render_scenario_table(valid_results)

    # M-1 Fix: Filter to scenarios with valid P&L before selecting worst case
    # This prevents invalid/missing P&L (returned as 0.0) from being incorrectly
    # highlighted as the "worst" scenario when all valid scenarios are positive
    valid_pnl_scenarios: list[tuple[dict[str, Any], float]] = []
    for result in valid_results:
        try:
            pnl = float(result.get("portfolio_pnl", ""))
            if math.isfinite(pnl):
                valid_pnl_scenarios.append((result, pnl))
        except (ValueError, TypeError):
            continue  # Skip scenarios with invalid P&L

    # Render waterfall for worst case (only if we have valid P&L data)
    ui.separator().classes("my-4")
    if valid_pnl_scenarios:
        worst_scenario, _ = min(valid_pnl_scenarios, key=lambda x: x[1])
        ui.label(
            f"Factor Impact: {_get_scenario_display_name(worst_scenario.get('scenario_name', ''))}"
        ).classes("text-lg font-semibold mb-4")
        render_factor_waterfall(worst_scenario)
    else:
        ui.label("Insufficient P&L data to determine worst-case scenario.").classes(
            "text-yellow-600 p-4"
        )


def render_scenario_table(results: Sequence[dict[str, Any]]) -> None:
    """Render stress test summary table with scenario details.

    Args:
        results: List of validated stress test results
    """
    # Build results lookup for efficient access
    results_map = {r.get("scenario_name"): r for r in results if r.get("scenario_name")}

    # Build ordered scenario list: predefined order first, then any extras
    predefined_set = set(SCENARIO_DISPLAY_ORDER)
    ordered_scenarios = [s for s in SCENARIO_DISPLAY_ORDER if s in results_map]
    ordered_scenarios.extend(
        s for s in results_map if isinstance(s, str) and s not in predefined_set
    )

    # Build table rows (safe float conversion with N/A for invalid values)
    rows = []
    invalid_pnl_count = 0
    for scenario in ordered_scenarios:
        result = results_map[scenario]
        pnl_str: str
        try:
            raw_pnl = result.get("portfolio_pnl")
            if raw_pnl is None:
                pnl_str = "N/A"
                invalid_pnl_count += 1
            else:
                pnl = float(raw_pnl)
                if not math.isfinite(pnl):
                    pnl_str = "N/A"
                    invalid_pnl_count += 1
                else:
                    pnl_str = f"{pnl:+.2%}"
        except (TypeError, ValueError):
            pnl_str = "N/A"
            invalid_pnl_count += 1
        # Safely coerce scenario_type to string (may be enum/int/None)
        raw_scenario_type = result.get("scenario_type")
        scenario_type = str(raw_scenario_type).title() if raw_scenario_type else "Unknown"

        info = SCENARIO_INFO.get(scenario, {})
        display_name = info.get("name", scenario)
        description = info.get("description", "")

        rows.append(
            {
                "scenario": display_name,
                "type": scenario_type,
                "description": description,
                "pnl": pnl_str,
            }
        )

    # Warn about invalid P&L data
    if invalid_pnl_count > 0:
        ui.label(
            f"Warning: {invalid_pnl_count} scenario(s) have missing or invalid P&L data."
        ).classes("text-yellow-600 text-sm mb-2")

    # Render table with data-testid for E2E testing (H-3)
    columns = [
        {"name": "scenario", "label": "Scenario", "field": "scenario", "align": "left"},
        {"name": "type", "label": "Type", "field": "type", "align": "left"},
        {"name": "description", "label": "Description", "field": "description", "align": "left"},
        {"name": "pnl", "label": "Portfolio P&L", "field": "pnl", "align": "right"},
    ]

    ui.table(
        columns=columns,
        rows=rows,
        row_key="scenario",
    ).classes("w-full").props('data-testid="stress-results-table"')


def render_factor_waterfall(scenario_result: dict[str, Any]) -> None:
    """Render factor contribution waterfall chart for a scenario.

    Shows how each factor contributes to the total scenario P&L.

    Args:
        scenario_result: Single stress test result with factor_impacts
    """
    factor_impacts = scenario_result.get("factor_impacts", {})

    # Guard against non-mapping types (list/None/other unexpected shapes)
    if not isinstance(factor_impacts, Mapping):
        ui.label("Invalid factor contribution data format.").classes(
            "text-yellow-600 p-4"
        )
        return

    if not factor_impacts:
        ui.label("No factor contribution data for this scenario.").classes(
            "text-gray-500 p-4"
        )
        return

    # Sort factors by absolute impact (largest first), with safe float conversion
    safe_impacts: list[tuple[str, float]] = []
    for factor_name, impact_value in factor_impacts.items():
        try:
            impact = float(impact_value)
            if not math.isfinite(impact):
                continue  # Skip NaN/inf values
            safe_impacts.append((factor_name, impact))
        except (TypeError, ValueError):
            continue  # Skip invalid entries

    if not safe_impacts:
        ui.label("No valid factor contribution data for this scenario.").classes(
            "text-gray-500 p-4"
        )
        return

    sorted_factors = sorted(safe_impacts, key=lambda x: abs(x[1]), reverse=True)

    # Use factor display names (L-2: falls back to raw name for unknown factors)
    factors = [_get_factor_display_name(f[0]) for f in sorted_factors]
    impacts = [f[1] for f in sorted_factors]

    # Create waterfall chart
    fig = go.Figure(
        go.Waterfall(
            orientation="v",
            measure=["relative"] * len(factors) + ["total"],
            x=factors + ["Total"],
            y=impacts + [sum(impacts)],
            text=[f"{v:+.2%}" for v in impacts] + [f"{sum(impacts):+.2%}"],
            textposition="outside",
            connector={"line": {"color": "rgba(63, 63, 63, 0.3)"}},
            decreasing={"marker": {"color": "#E74C3C"}},
            increasing={"marker": {"color": "#27AE60"}},
            totals={"marker": {"color": "#3498DB"}},
            hovertemplate="%{x}<br>Impact: %{y:.2%}<extra></extra>",
        )
    )

    fig.update_layout(
        title="Factor Contribution to P&L",
        yaxis_title="P&L Impact",
        yaxis={"tickformat": ".1%"},
        showlegend=False,
        margin={"l": 50, "r": 30, "t": 60, "b": 80},
        xaxis={"tickangle": -45},
    )

    ui.plotly(fig).classes("w-full")


__all__ = [
    "render_stress_tests",
    "render_scenario_table",
    "render_factor_waterfall",
    "SCENARIO_DISPLAY_ORDER",
    "SCENARIO_INFO",
]
