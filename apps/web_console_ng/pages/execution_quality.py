"""Execution Quality (TCA) Dashboard page for NiceGUI web console (P6T8).

Provides Transaction Cost Analysis visualization and metrics:
- Summary cards with key TCA metrics
- Shortfall decomposition chart
- Execution vs benchmark timeline
- Filterable orders table with TCA metrics

PARITY: New page for P6T8 - no Streamlit equivalent.

NOTE: Uses demo mode with placeholder data when TCA API is unavailable.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from nicegui import ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.components.grid_export_toolbar import GridExportToolbar
from apps.web_console_ng.components.tca_chart import (
    create_benchmark_comparison_chart,
    create_shortfall_decomposition_chart,
    create_summary_cards,
)
from apps.web_console_ng.config import EXECUTION_GATEWAY_URL, FEATURE_TCA_DASHBOARD
from apps.web_console_ng.ui.layout import main_layout
from libs.platform.web_console_auth.permissions import (
    Permission,
    get_authorized_strategies,
    has_permission,
)

logger = logging.getLogger(__name__)

DEFAULT_RANGE_DAYS = 30
MAX_RANGE_DAYS = 90


async def _fetch_tca_data(
    start_date: date,
    end_date: date,
    symbol: str | None,
    strategy_id: str | None,
    user_id: str,
    role: str,
    strategies: list[str],
) -> dict[str, Any] | None:
    """Fetch TCA data from API.

    Returns None on error, falls back to demo mode.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            params: dict[str, Any] = {
                "start_date": str(start_date),
                "end_date": str(end_date),
            }
            if symbol:
                params["symbol"] = symbol
            if strategy_id:
                params["strategy_id"] = strategy_id

            # Add auth headers
            headers = {
                "X-User-ID": user_id,
                "X-User-Role": role,
                "X-User-Strategies": ",".join(strategies),
            }

            response = await client.get(
                f"{EXECUTION_GATEWAY_URL}/api/v1/tca/analysis",
                params=params,
                headers=headers,
            )
            if response.status_code == 200:
                result: dict[str, Any] = response.json()
                return result
            logger.warning(
                "TCA API returned non-200",
                extra={"status": response.status_code, "body": response.text[:200]},
            )
    except httpx.RequestError as e:
        logger.warning("TCA API unavailable", extra={"error": str(e)})
    return None


def _generate_demo_data(
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Generate demo TCA data for display when API unavailable."""
    import random

    random.seed(hash((start_date, end_date)))

    num_days = (end_date - start_date).days + 1
    num_orders = max(10, num_days * 3)

    orders = []
    symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "META"]

    for i in range(min(num_orders, 30)):
        order_date = start_date + timedelta(days=random.randint(0, num_days - 1))
        symbol = random.choice(symbols)
        side = random.choice(["buy", "sell"])

        orders.append({
            "client_order_id": f"demo-{i:04d}",
            "symbol": symbol,
            "side": side,
            "execution_date": str(order_date),
            "target_qty": random.randint(100, 2000),
            "filled_qty": random.randint(80, 2000),
            "fill_rate": random.uniform(0.85, 1.0),
            "implementation_shortfall_bps": random.uniform(-5, 15),
            "price_shortfall_bps": random.uniform(-3, 8),
            "vwap_slippage_bps": random.uniform(-2, 5),
            "fee_cost_bps": random.uniform(0.5, 2),
            "opportunity_cost_bps": random.uniform(0, 3),
            "timing_cost_bps": random.uniform(0.5, 2),
            "market_impact_bps": random.uniform(0, 4),
            "total_notional": random.uniform(10000, 100000),
            "warnings": ["Demo data"],
        })

    # Compute averages - collect values explicitly to avoid mypy issues with dict typing
    is_values = [o["implementation_shortfall_bps"] for o in orders]
    vwap_values = [o["vwap_slippage_bps"] for o in orders]
    impact_values = [o["market_impact_bps"] for o in orders]
    fill_values = [o["fill_rate"] for o in orders]
    notional_values = [o["total_notional"] for o in orders]
    filled_qty_values = [o["filled_qty"] for o in orders]
    price_values = [o["price_shortfall_bps"] for o in orders]
    fee_values = [o["fee_cost_bps"] for o in orders]
    opp_values = [o["opportunity_cost_bps"] for o in orders]
    timing_values = [o["timing_cost_bps"] for o in orders]

    n = len(orders)
    avg_is: float = sum(is_values) / n  # type: ignore[arg-type]
    avg_vwap: float = sum(vwap_values) / n  # type: ignore[arg-type]
    avg_impact: float = sum(impact_values) / n  # type: ignore[arg-type]
    avg_fill: float = sum(fill_values) / n  # type: ignore[arg-type]
    total_notional: float = sum(notional_values)  # type: ignore[arg-type]
    total_shares: int = sum(filled_qty_values)  # type: ignore[arg-type]

    return {
        "summary": {
            "start_date": str(start_date),
            "end_date": str(end_date),
            "computation_timestamp": datetime.now(UTC).isoformat(),
            "total_orders": n,
            "total_fills": n * random.randint(2, 5),
            "total_notional": total_notional,
            "total_shares": total_shares,
            "avg_fill_rate": avg_fill,
            "avg_implementation_shortfall_bps": avg_is,
            "avg_price_shortfall_bps": sum(price_values) / n,  # type: ignore[arg-type]
            "avg_vwap_slippage_bps": avg_vwap,
            "avg_fee_cost_bps": sum(fee_values) / n,  # type: ignore[arg-type]
            "avg_opportunity_cost_bps": sum(opp_values) / n,  # type: ignore[arg-type]
            "avg_market_impact_bps": avg_impact,
            "avg_timing_cost_bps": sum(timing_values) / n,  # type: ignore[arg-type]
            "warnings": ["Demo data - TCA API unavailable"],
        },
        "orders": sorted(orders, key=lambda x: str(x["execution_date"]), reverse=True),
    }


@ui.page("/execution-quality")
@requires_auth
@main_layout
async def execution_quality_page() -> None:
    """Execution Quality (TCA) Dashboard page."""
    user = get_current_user()
    user_id = str(user.get("user_id") or user.get("username", "unknown"))
    user_role = str(user.get("role", "viewer"))
    user_strategies: list[str] = list(user.get("strategies", []))

    # Page title
    ui.label("Execution Quality Dashboard").classes("text-2xl font-bold mb-2")
    ui.label("Transaction Cost Analysis (TCA) - Measure execution performance").classes(
        "text-gray-600 mb-4"
    )

    # Feature flag check
    if not FEATURE_TCA_DASHBOARD:
        with ui.card().classes("w-full p-6"):
            ui.label("Execution Quality Dashboard is not available.").classes(
                "text-gray-500 text-center"
            )
            ui.label("Set FEATURE_TCA_DASHBOARD=true to enable this feature.").classes(
                "text-gray-400 text-sm text-center"
            )
        return

    # Permission check
    if not has_permission(user, Permission.VIEW_TCA):
        ui.notify("Permission denied: VIEW_TCA required", type="negative")
        with ui.card().classes("w-full p-6"):
            ui.label("Permission denied: VIEW_TCA required.").classes("text-red-500 text-center")
        return

    # Get authorized strategies
    authorized_strategies = get_authorized_strategies(user)
    if not authorized_strategies:
        with ui.card().classes("w-full p-6"):
            ui.label("You don't have access to any strategies. Contact administrator.").classes(
                "text-amber-600 text-center"
            )
        return

    # Render the dashboard
    await _render_tca_dashboard(
        user_id=user_id,
        role=user_role,
        strategies=user_strategies,
        authorized_strategies=authorized_strategies,
    )


async def _render_tca_dashboard(
    user_id: str,
    role: str,
    strategies: list[str],
    authorized_strategies: list[str],
) -> None:
    """Render the full TCA dashboard."""
    today = date.today()

    # State
    state: dict[str, Any] = {
        "start_date": today - timedelta(days=DEFAULT_RANGE_DAYS),
        "end_date": today,
        "symbol": None,
        "strategy_id": None,
        "data": None,
        "demo_mode": False,
    }

    # Filters section
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("Filters").classes("text-lg font-bold mb-2")

        with ui.row().classes("gap-4 flex-wrap items-end"):
            # Date range
            with ui.column().classes("gap-1"):
                ui.label("Start Date").classes("text-xs text-gray-500")
                start_input = ui.date(
                    value=str(state["start_date"]),
                ).classes("w-36")

            with ui.column().classes("gap-1"):
                ui.label("End Date").classes("text-xs text-gray-500")
                end_input = ui.date(
                    value=str(state["end_date"]),
                ).classes("w-36")

            # Symbol filter
            with ui.column().classes("gap-1"):
                ui.label("Symbol").classes("text-xs text-gray-500")
                symbol_input = ui.input(placeholder="All symbols").classes("w-28")

            # Strategy filter
            with ui.column().classes("gap-1"):
                ui.label("Strategy").classes("text-xs text-gray-500")
                strategy_select = ui.select(
                    options=["All"] + authorized_strategies,
                    value="All",
                ).classes("w-40")

            # Preset buttons
            with ui.column().classes("gap-1"):
                ui.label("Presets").classes("text-xs text-gray-500")
                with ui.row().classes("gap-1"):
                    btn_7d = ui.button("7D").props("size=sm flat")
                    btn_30d = ui.button("30D").props("size=sm")
                    btn_90d = ui.button("90D").props("size=sm flat")

    # Demo mode banner (shown when using demo data)
    demo_banner = ui.row().classes("w-full hidden")
    with demo_banner:
        with ui.card().classes("w-full p-2 bg-amber-900"):
            ui.label("⚠️ Demo Mode: Showing sample data (TCA API unavailable)").classes(
                "text-amber-200 text-sm text-center"
            )

    # Summary cards container
    summary_container = ui.row().classes("w-full mb-4")

    # Charts container
    charts_container = ui.column().classes("w-full gap-4 mb-4")

    # Orders table container
    orders_container = ui.column().classes("w-full")

    async def load_data() -> None:
        """Load TCA data and update UI."""
        summary_container.clear()
        charts_container.clear()
        orders_container.clear()

        # Parse dates
        try:
            start_dt = (
                date.fromisoformat(start_input.value)
                if start_input.value
                else state["start_date"]
            )
            end_dt = (
                date.fromisoformat(end_input.value)
                if end_input.value
                else state["end_date"]
            )
        except ValueError:
            ui.notify("Invalid date format", type="negative")
            return

        if start_dt > end_dt:
            ui.notify("Start date must be before end date", type="negative")
            return

        if (end_dt - start_dt).days > MAX_RANGE_DAYS:
            ui.notify(f"Date range capped to {MAX_RANGE_DAYS} days", type="warning")
            start_dt = end_dt - timedelta(days=MAX_RANGE_DAYS)

        state["start_date"] = start_dt
        state["end_date"] = end_dt

        # Get filter values
        symbol = symbol_input.value.strip().upper() if symbol_input.value else None
        strategy = strategy_select.value if strategy_select.value != "All" else None
        state["symbol"] = symbol
        state["strategy_id"] = strategy

        # Fetch data
        data = await _fetch_tca_data(
            start_dt, end_dt, symbol, strategy, user_id, role, strategies
        )

        if data is None:
            # Fall back to demo mode
            data = _generate_demo_data(start_dt, end_dt)
            state["demo_mode"] = True
            demo_banner.classes(remove="hidden")
        else:
            state["demo_mode"] = False
            demo_banner.classes(add="hidden")

        state["data"] = data
        summary = data.get("summary", {})
        orders = data.get("orders", [])

        # Render summary cards
        with summary_container:
            create_summary_cards(
                avg_is_bps=summary.get("avg_implementation_shortfall_bps", 0),
                avg_vwap_bps=summary.get("avg_vwap_slippage_bps", 0),
                avg_impact_bps=summary.get("avg_market_impact_bps", 0),
                fill_rate=summary.get("avg_fill_rate", 0),
                total_notional=summary.get("total_notional", 0),
                total_orders=summary.get("total_orders", 0),
            )

        # Render charts
        with charts_container:
            # Shortfall decomposition chart
            with ui.card().classes("w-full p-4"):
                ui.label("Cost Decomposition by Date").classes("text-lg font-bold mb-2")

                if orders:
                    # Aggregate by date
                    date_data: dict[str, dict[str, float]] = {}
                    for order in orders:
                        d = order.get("execution_date", "")
                        if d not in date_data:
                            date_data[d] = {
                                "price": 0,
                                "fee": 0,
                                "opportunity": 0,
                                "timing": 0,
                                "count": 0,
                            }
                        date_data[d]["price"] += order.get("price_shortfall_bps", 0)
                        date_data[d]["fee"] += order.get("fee_cost_bps", 0)
                        date_data[d]["opportunity"] += order.get("opportunity_cost_bps", 0)
                        date_data[d]["timing"] += order.get("timing_cost_bps", 0)
                        date_data[d]["count"] += 1

                    # Average per date
                    sorted_dates = sorted(date_data.keys())
                    labels = sorted_dates
                    price_shortfall = [
                        round(date_data[d]["price"] / date_data[d]["count"], 2)
                        for d in sorted_dates
                    ]
                    fee_cost = [
                        round(date_data[d]["fee"] / date_data[d]["count"], 2)
                        for d in sorted_dates
                    ]
                    opportunity_cost = [
                        round(date_data[d]["opportunity"] / date_data[d]["count"], 2)
                        for d in sorted_dates
                    ]
                    timing_cost = [
                        round(date_data[d]["timing"] / date_data[d]["count"], 2)
                        for d in sorted_dates
                    ]

                    create_shortfall_decomposition_chart(
                        labels=labels,
                        price_shortfall=price_shortfall,
                        fee_cost=fee_cost,
                        opportunity_cost=opportunity_cost,
                        timing_cost=timing_cost,
                    )
                else:
                    ui.label("No order data available").classes("text-gray-500 p-4")

            # Benchmark comparison (show for first order as example)
            if orders:
                with ui.card().classes("w-full p-4"):
                    ui.label("Sample Execution vs VWAP").classes("text-lg font-bold mb-2")

                    # Generate sample benchmark data for first order
                    first_order = orders[0]
                    import random

                    random.seed(hash(first_order.get("client_order_id", "")))

                    num_points = 10
                    base_price = 150.0
                    timestamps = [f"10:{i * 5:02d}" for i in range(num_points)]
                    exec_prices = [
                        round(base_price * (1 + random.uniform(-0.002, 0.003)), 2)
                        for _ in range(num_points)
                    ]
                    bench_prices = [
                        round(base_price * (1 + random.uniform(-0.001, 0.001)), 2)
                        for _ in range(num_points)
                    ]

                    create_benchmark_comparison_chart(
                        timestamps=timestamps,
                        execution_prices=exec_prices,
                        benchmark_prices=bench_prices,
                        benchmark_type="VWAP",
                        symbol=first_order.get("symbol", ""),
                    )

        # Render orders table
        with orders_container:
            with ui.card().classes("w-full p-4"):
                with ui.row().classes("w-full justify-between items-center mb-2"):
                    ui.label("Order Details").classes("text-lg font-bold")

                    # Export toolbar
                    export_toolbar = GridExportToolbar(
                        grid_id="tca-orders-grid",
                        grid_name="tca",
                        filename_prefix="tca_analysis",
                    )
                    export_toolbar.create()

                if orders:
                    columns: list[dict[str, Any]] = [
                        {"name": "date", "label": "Date", "field": "execution_date", "sortable": True},
                        {"name": "symbol", "label": "Symbol", "field": "symbol", "sortable": True},
                        {"name": "side", "label": "Side", "field": "side", "sortable": True},
                        {"name": "qty", "label": "Filled", "field": "filled_qty", "sortable": True},
                        {"name": "fill_rate", "label": "Fill %", "field": "fill_rate_pct"},
                        {"name": "is", "label": "IS (bps)", "field": "is_bps", "sortable": True},
                        {"name": "vwap", "label": "VWAP (bps)", "field": "vwap_bps", "sortable": True},
                        {"name": "impact", "label": "Impact (bps)", "field": "impact_bps"},
                        {"name": "notional", "label": "Notional", "field": "notional"},
                    ]

                    rows = []
                    for order in orders[:50]:  # Limit to 50 rows
                        notional = order.get("total_notional", 0)
                        if notional >= 1000:
                            notional_str = f"${notional / 1000:.1f}K"
                        else:
                            notional_str = f"${notional:.0f}"

                        rows.append({
                            "execution_date": order.get("execution_date", ""),
                            "symbol": order.get("symbol", ""),
                            "side": order.get("side", "").upper(),
                            "filled_qty": order.get("filled_qty", 0),
                            "fill_rate_pct": f"{order.get('fill_rate', 0) * 100:.1f}%",
                            "is_bps": f"{order.get('implementation_shortfall_bps', 0):+.2f}",
                            "vwap_bps": f"{order.get('vwap_slippage_bps', 0):+.2f}",
                            "impact_bps": f"{order.get('market_impact_bps', 0):+.2f}",
                            "notional": notional_str,
                        })

                    ui.table(
                        columns=columns,
                        rows=rows,
                        row_key="execution_date",
                    ).classes("w-full").props("id=tca-orders-grid dense")
                else:
                    ui.label("No orders found for selected filters").classes(
                        "text-gray-500 p-4"
                    )

    # Preset button handlers
    async def set_preset(days: int) -> None:
        end_input.value = str(today)
        start_input.value = str(today - timedelta(days=days))
        btn_7d.props("color=" if days != 7 else "color=primary")
        btn_30d.props("color=" if days != 30 else "color=primary")
        btn_90d.props("color=" if days != 90 else "color=primary")
        await load_data()

    btn_7d.on_click(lambda: set_preset(7))
    btn_30d.on_click(lambda: set_preset(30))
    btn_90d.on_click(lambda: set_preset(90))

    # Filter change handlers
    async def on_filter_change(_: Any = None) -> None:
        await load_data()

    start_input.on_value_change(on_filter_change)
    end_input.on_value_change(on_filter_change)
    symbol_input.on_value_change(on_filter_change)
    strategy_select.on_value_change(on_filter_change)

    # Initial load
    await load_data()


__all__ = ["execution_quality_page"]
