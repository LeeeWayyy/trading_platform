"""TCA Chart components for P6T8.

Provides visualization components for Transaction Cost Analysis:
- Shortfall decomposition stacked bar chart
- Execution vs benchmark line chart
- Cost summary cards

Uses ECharts via NiceGUI for consistent styling with existing charts.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

# TCA metric color palette
TCA_COLORS = {
    "price_shortfall": "#3b82f6",  # Blue - price slippage
    "fee_cost": "#10b981",  # Green - fees
    "opportunity_cost": "#f59e0b",  # Amber - unfilled
    "market_impact": "#ef4444",  # Red - impact
    "timing_cost": "#8b5cf6",  # Purple - timing
    "vwap_slippage": "#06b6d4",  # Cyan - VWAP
    "positive": "#22c55e",  # Green - good
    "negative": "#ef4444",  # Red - bad
    "neutral": "#6b7280",  # Gray - neutral
}


def create_metric_card(
    label: str,
    value: float,
    unit: str = "bps",
    is_cost: bool = True,
    description: str | None = None,
) -> ui.card:
    """Create a TCA metric card.

    Args:
        label: Metric name
        value: Metric value
        unit: Unit label (bps, %, $)
        is_cost: If True, positive = bad (red), negative = good (green)
        description: Optional tooltip description

    Returns:
        ui.card element
    """
    # Determine color based on value and is_cost
    if abs(value) < 0.1:
        color_class = "text-gray-400"
    elif is_cost:
        color_class = "text-red-500" if value > 0 else "text-green-500"
    else:
        color_class = "text-green-500" if value > 0 else "text-red-500"

    # Format value
    sign = "+" if value > 0 else ""
    formatted = f"{sign}{value:.2f} {unit}"

    with ui.card().classes("p-3 min-w-32") as card:
        ui.label(label).classes("text-xs text-gray-500 uppercase tracking-wide")
        ui.label(formatted).classes(f"text-lg font-bold {color_class}")
        if description:
            card.tooltip(description)

    return card


def create_summary_cards(
    avg_is_bps: float,
    avg_vwap_bps: float,
    avg_impact_bps: float,
    fill_rate: float,
    total_notional: float,
    total_orders: int,
) -> ui.row:
    """Create TCA summary cards row.

    Args:
        avg_is_bps: Average implementation shortfall (bps)
        avg_vwap_bps: Average VWAP slippage (bps)
        avg_impact_bps: Average market impact (bps)
        fill_rate: Average fill rate (0-1)
        total_notional: Total notional traded
        total_orders: Total orders analyzed

    Returns:
        ui.row with metric cards
    """
    with ui.row().classes("gap-4 flex-wrap") as row:
        create_metric_card(
            "Avg IS",
            avg_is_bps,
            "bps",
            is_cost=True,
            description="Average Implementation Shortfall - total execution cost",
        )
        create_metric_card(
            "VWAP Slip",
            avg_vwap_bps,
            "bps",
            is_cost=True,
            description="Average slippage vs market VWAP benchmark",
        )
        create_metric_card(
            "Market Impact",
            avg_impact_bps,
            "bps",
            is_cost=True,
            description="Estimated permanent market impact",
        )
        create_metric_card(
            "Fill Rate",
            fill_rate * 100,
            "%",
            is_cost=False,
            description="Average fill rate (higher is better)",
        )

        # Notional and orders (non-cost metrics)
        with ui.card().classes("p-3 min-w-32"):
            ui.label("Notional").classes("text-xs text-gray-500 uppercase tracking-wide")
            if total_notional >= 1_000_000:
                formatted = f"${total_notional / 1_000_000:.1f}M"
            elif total_notional >= 1_000:
                formatted = f"${total_notional / 1_000:.1f}K"
            else:
                formatted = f"${total_notional:,.0f}"
            ui.label(formatted).classes("text-lg font-bold text-white")

        with ui.card().classes("p-3 min-w-32"):
            ui.label("Orders").classes("text-xs text-gray-500 uppercase tracking-wide")
            ui.label(str(total_orders)).classes("text-lg font-bold text-white")

    return row


def create_shortfall_decomposition_chart(
    labels: list[str],
    price_shortfall: list[float],
    fee_cost: list[float],
    opportunity_cost: list[float],
    timing_cost: list[float],
    height: int = 350,
) -> ui.echart:
    """Create stacked bar chart for shortfall decomposition.

    Args:
        labels: X-axis labels (dates or order IDs)
        price_shortfall: Price slippage component (bps)
        fee_cost: Fee component (bps)
        opportunity_cost: Opportunity cost component (bps)
        timing_cost: Timing/spread cost component (bps)
        height: Chart height in pixels

    Returns:
        ui.echart element
    """
    options: dict[str, Any] = {
        "tooltip": {
            "trigger": "axis",
            "axisPointer": {"type": "shadow"},
            "formatter": "{b}<br/>"
            + "Price: {c0} bps<br/>"
            + "Fees: {c1} bps<br/>"
            + "Opportunity: {c2} bps<br/>"
            + "Timing: {c3} bps",
        },
        "legend": {
            "data": ["Price Shortfall", "Fees", "Opportunity", "Timing"],
            "textStyle": {"color": "#9ca3af"},
            "top": 10,
        },
        "grid": {
            "left": "3%",
            "right": "4%",
            "bottom": "3%",
            "containLabel": True,
        },
        "xAxis": {
            "type": "category",
            "data": labels,
            "axisLabel": {"color": "#9ca3af"},
            "axisLine": {"lineStyle": {"color": "#374151"}},
        },
        "yAxis": {
            "type": "value",
            "name": "Cost (bps)",
            "nameTextStyle": {"color": "#9ca3af"},
            "axisLabel": {"color": "#9ca3af"},
            "axisLine": {"lineStyle": {"color": "#374151"}},
            "splitLine": {"lineStyle": {"color": "#374151", "type": "dashed"}},
        },
        "series": [
            {
                "name": "Price Shortfall",
                "type": "bar",
                "stack": "total",
                "data": price_shortfall,
                "itemStyle": {"color": TCA_COLORS["price_shortfall"]},
            },
            {
                "name": "Fees",
                "type": "bar",
                "stack": "total",
                "data": fee_cost,
                "itemStyle": {"color": TCA_COLORS["fee_cost"]},
            },
            {
                "name": "Opportunity",
                "type": "bar",
                "stack": "total",
                "data": opportunity_cost,
                "itemStyle": {"color": TCA_COLORS["opportunity_cost"]},
            },
            {
                "name": "Timing",
                "type": "bar",
                "stack": "total",
                "data": timing_cost,
                "itemStyle": {"color": TCA_COLORS["timing_cost"]},
            },
        ],
    }

    return ui.echart(options).classes("w-full").style(f"height: {height}px")


def create_benchmark_comparison_chart(
    timestamps: list[str],
    execution_prices: list[float],
    benchmark_prices: list[float],
    benchmark_type: str = "VWAP",
    symbol: str = "",
    height: int = 350,
) -> ui.echart:
    """Create line chart comparing execution vs benchmark.

    Args:
        timestamps: X-axis timestamps
        execution_prices: Execution VWAP over time
        benchmark_prices: Benchmark price over time
        benchmark_type: Benchmark label (VWAP, TWAP, Arrival)
        symbol: Symbol for title
        height: Chart height in pixels

    Returns:
        ui.echart element
    """
    title = f"Execution vs {benchmark_type}"
    if symbol:
        title = f"{symbol}: {title}"

    options: dict[str, Any] = {
        "title": {
            "text": title,
            "textStyle": {"color": "#e5e7eb", "fontSize": 14},
            "left": "center",
        },
        "tooltip": {
            "trigger": "axis",
            "axisPointer": {"type": "cross"},
        },
        "legend": {
            "data": ["Execution", benchmark_type],
            "textStyle": {"color": "#9ca3af"},
            "top": 30,
        },
        "grid": {
            "left": "3%",
            "right": "4%",
            "bottom": "3%",
            "top": 60,
            "containLabel": True,
        },
        "xAxis": {
            "type": "category",
            "data": timestamps,
            "axisLabel": {"color": "#9ca3af", "rotate": 45},
            "axisLine": {"lineStyle": {"color": "#374151"}},
        },
        "yAxis": {
            "type": "value",
            "name": "Price ($)",
            "nameTextStyle": {"color": "#9ca3af"},
            "axisLabel": {"color": "#9ca3af"},
            "axisLine": {"lineStyle": {"color": "#374151"}},
            "splitLine": {"lineStyle": {"color": "#374151", "type": "dashed"}},
            "scale": True,  # Auto-scale to data range
        },
        "series": [
            {
                "name": "Execution",
                "type": "line",
                "data": execution_prices,
                "itemStyle": {"color": "#3b82f6"},
                "lineStyle": {"width": 2},
                "symbol": "circle",
                "symbolSize": 6,
            },
            {
                "name": benchmark_type,
                "type": "line",
                "data": benchmark_prices,
                "itemStyle": {"color": "#10b981"},
                "lineStyle": {"width": 2, "type": "dashed"},
                "symbol": "diamond",
                "symbolSize": 6,
            },
        ],
    }

    return ui.echart(options).classes("w-full").style(f"height: {height}px")


def create_slippage_timeline_chart(
    timestamps: list[str],
    slippage_bps: list[float],
    cumulative_qty: list[int],
    height: int = 300,
) -> ui.echart:
    """Create dual-axis chart showing slippage and cumulative quantity.

    Args:
        timestamps: X-axis timestamps
        slippage_bps: Slippage at each point (bps)
        cumulative_qty: Cumulative quantity filled
        height: Chart height in pixels

    Returns:
        ui.echart element
    """
    options: dict[str, Any] = {
        "tooltip": {
            "trigger": "axis",
            "axisPointer": {"type": "cross"},
        },
        "legend": {
            "data": ["Slippage", "Cum. Qty"],
            "textStyle": {"color": "#9ca3af"},
            "top": 10,
        },
        "grid": {
            "left": "3%",
            "right": "4%",
            "bottom": "3%",
            "containLabel": True,
        },
        "xAxis": {
            "type": "category",
            "data": timestamps,
            "axisLabel": {"color": "#9ca3af", "rotate": 45},
            "axisLine": {"lineStyle": {"color": "#374151"}},
        },
        "yAxis": [
            {
                "type": "value",
                "name": "Slippage (bps)",
                "nameTextStyle": {"color": "#9ca3af"},
                "axisLabel": {"color": "#9ca3af"},
                "axisLine": {"lineStyle": {"color": "#3b82f6"}},
                "splitLine": {"lineStyle": {"color": "#374151", "type": "dashed"}},
            },
            {
                "type": "value",
                "name": "Quantity",
                "nameTextStyle": {"color": "#9ca3af"},
                "axisLabel": {"color": "#9ca3af"},
                "axisLine": {"lineStyle": {"color": "#10b981"}},
                "splitLine": {"show": False},
            },
        ],
        "series": [
            {
                "name": "Slippage",
                "type": "bar",
                "data": slippage_bps,
                "itemStyle": {
                    "color": {
                        "type": "linear",
                        "x": 0,
                        "y": 0,
                        "x2": 0,
                        "y2": 1,
                        "colorStops": [
                            {"offset": 0, "color": "#ef4444"},
                            {"offset": 1, "color": "#fca5a5"},
                        ],
                    }
                },
                "yAxisIndex": 0,
            },
            {
                "name": "Cum. Qty",
                "type": "line",
                "data": cumulative_qty,
                "itemStyle": {"color": "#10b981"},
                "lineStyle": {"width": 2},
                "yAxisIndex": 1,
                "areaStyle": {"opacity": 0.1},
            },
        ],
    }

    return ui.echart(options).classes("w-full").style(f"height: {height}px")


__all__ = [
    "TCA_COLORS",
    "create_benchmark_comparison_chart",
    "create_metric_card",
    "create_shortfall_decomposition_chart",
    "create_slippage_timeline_chart",
    "create_summary_cards",
]
