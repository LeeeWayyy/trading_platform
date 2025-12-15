"""Stress test results visualization component for risk dashboard."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import plotly.graph_objects as go
import streamlit as st

from apps.web_console.utils.validators import validate_stress_tests

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


def _get_pnl_color(pnl: float) -> str:
    """Get color based on P&L: red for losses, green for gains."""
    return "#E74C3C" if pnl < 0 else "#27AE60"


def render_stress_tests(results: Sequence[dict[str, Any]]) -> None:
    """Render stress test results table and charts.

    Shows:
    - Summary table with scenario name, P&L impact
    - Factor contribution waterfall for selected scenario
    - Worst case scenario highlight

    Args:
        results: List of stress test result dicts with scenario_name, portfolio_pnl,
                 factor_impacts keys
    """
    valid_results = validate_stress_tests(list(results))

    if not valid_results:
        st.info("No stress test results available.")
        return

    # Render summary table
    st.subheader("Stress Test Results")
    render_scenario_table(valid_results)

    # Find worst scenario for highlighting
    worst_scenario = min(valid_results, key=lambda r: float(r.get("portfolio_pnl", 0)))

    # Render waterfall for worst case
    st.divider()
    st.subheader(
        f"Factor Impact: {_get_scenario_display_name(worst_scenario.get('scenario_name', ''))}"
    )
    render_factor_waterfall(worst_scenario)


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
    # Type guard: keys are str (filtered in comprehension above), but mypy needs explicit check
    ordered_scenarios.extend(
        s for s in results_map if isinstance(s, str) and s not in predefined_set
    )

    # Single-pass table data construction
    table_data = []
    for scenario in ordered_scenarios:
        result = results_map[scenario]
        pnl = float(result.get("portfolio_pnl", 0))
        scenario_type = result.get("scenario_type") or "unknown"

        info = SCENARIO_INFO.get(scenario, {})
        display_name = info.get("name", scenario)
        description = info.get("description", "")

        table_data.append(
            {
                "Scenario": display_name,
                "Type": scenario_type.title(),
                "Description": description,
                "Portfolio P&L": f"{pnl:+.2%}",
            }
        )

    st.dataframe(
        table_data,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Portfolio P&L": st.column_config.TextColumn(
                "Portfolio P&L",
                help="Estimated portfolio impact under this scenario",
            ),
        },
    )


def render_factor_waterfall(scenario_result: dict[str, Any]) -> go.Figure | None:
    """Render factor contribution waterfall chart for a scenario.

    Shows how each factor contributes to the total scenario P&L.

    Args:
        scenario_result: Single stress test result with factor_impacts

    Returns:
        Plotly Figure or None if no factor data
    """
    factor_impacts = scenario_result.get("factor_impacts", {})

    if not factor_impacts:
        st.info("No factor contribution data for this scenario.")
        return None

    # Sort factors by absolute impact (largest first)
    sorted_factors = sorted(
        factor_impacts.items(),
        key=lambda x: abs(x[1]),
        reverse=True,
    )

    factors = [f[0] for f in sorted_factors]
    impacts = [float(f[1]) for f in sorted_factors]

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

    st.plotly_chart(fig, use_container_width=True, theme="streamlit")
    return fig


__all__ = [
    "render_stress_tests",
    "render_scenario_table",
    "render_factor_waterfall",
    "SCENARIO_DISPLAY_ORDER",
    "SCENARIO_INFO",
]
