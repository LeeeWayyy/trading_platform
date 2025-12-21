"""Threshold configuration component for alert rules."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import streamlit as st

PRESET_THRESHOLDS: dict[str, dict[str, Any]] = {
    "drawdown": {
        "label": "Drawdown Alert",
        "description": "Alert when drawdown exceeds threshold",
        "default_value": Decimal("-0.05"),
        "comparison": "lt",
        "unit": "%",
    },
    "position_limit": {
        "label": "Position Limit Alert",
        "description": "Alert when position exceeds limit",
        "default_value": Decimal("100000"),
        "comparison": "gt",
        "unit": "$",
    },
    "latency": {
        "label": "Latency Alert",
        "description": "Alert when P95 latency exceeds threshold",
        "default_value": Decimal("1000"),
        "comparison": "gt",
        "unit": "ms",
    },
}


def render_threshold_config(condition_type: str) -> tuple[Decimal, str]:
    """Render threshold configuration for condition type."""

    preset = PRESET_THRESHOLDS.get(condition_type, {})
    st.caption(preset.get("description", "Configure alert threshold"))

    default_value = preset.get("default_value", Decimal("0"))
    unit = preset.get("unit", "")

    value = st.number_input(
        "Threshold",
        value=float(default_value),
        help=f"Unit: {unit}" if unit else None,
    )

    comparison_default = preset.get("comparison", "gt")
    comparison = st.selectbox(
        "Comparison",
        options=["gt", "lt", "gte", "lte", "eq"],
        index=["gt", "lt", "gte", "lte", "eq"].index(comparison_default),
    )

    return Decimal(str(value)), comparison


__all__ = ["render_threshold_config", "PRESET_THRESHOLDS"]
