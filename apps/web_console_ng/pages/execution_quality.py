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

import hashlib
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
from apps.web_console_ng.ui.trading_layout import apply_compact_grid_options
from libs.platform.web_console_auth.permissions import (
    Permission,
    get_authorized_strategies,
    has_permission,
)


def _stable_hash(value: str) -> int:
    """Generate a stable hash that doesn't change across process restarts.

    Python's built-in hash() is randomized (PYTHONHASHSEED) and not stable.
    Use SHA256 for deterministic demo data generation.
    """
    return int(hashlib.sha256(value.encode()).hexdigest(), 16)

logger = logging.getLogger(__name__)

DEFAULT_RANGE_DAYS = 30
MAX_RANGE_DAYS = 90
# Backend default fill limit for TCA queries (database.py:get_trades_for_tca).
# Used to detect potential data truncation in the benchmark chart.
# Note: a "may be truncated" warning is shown when point count reaches this
# limit. This can false-positive when there are exactly _BACKEND_FILL_LIMIT
# fills, but under-warning is worse than over-warning for TCA accuracy.
_BACKEND_FILL_LIMIT = 500


_DATETIME_MIN_UTC = datetime.min.replace(tzinfo=UTC)


def _parse_utc(value: Any) -> datetime:
    """Parse a timestamp to a UTC-aware datetime.

    Naive values are assumed UTC.  Unparseable values map to
    ``_DATETIME_MIN_UTC`` so they sort first.
    """
    text = str(value)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except ValueError:
        return _DATETIME_MIN_UTC


def _is_valid_timestamp(value: Any) -> bool:
    """Return True if *value* can be parsed to a real UTC datetime."""
    return _parse_utc(value) != _DATETIME_MIN_UTC


def _format_benchmark_timestamp(value: Any, *, include_date: bool = False) -> str:
    """Format a benchmark point timestamp as ``HH:MM:SS UTC``.

    When *include_date* is True the output includes the date prefix
    (``YYYY-MM-DD HH:MM:SS UTC``) for multi-day fill windows.
    """
    dt = _parse_utc(value)
    if dt == _DATETIME_MIN_UTC:
        return str(value)
    fmt = "%Y-%m-%d %H:%M:%S UTC" if include_date else "%H:%M:%S UTC"
    return dt.strftime(fmt)


def _is_valid_price(value: Any) -> bool:
    """Return True if *value* is a finite positive price.

    Rejects zero, negative, NaN, infinity, booleans, and non-numeric values.
    """
    import math

    # Reject booleans explicitly since float(True) == 1.0
    if isinstance(value, bool):
        return False
    try:
        f = float(value)
        return f > 0.0 and math.isfinite(f)
    except (TypeError, ValueError):
        return False


def _build_tca_auth_headers(
    user_id: str,
    role: str,
    strategies: list[str],
) -> dict[str, str]:
    """Build auth headers for TCA API calls."""
    return {
        "X-User-ID": user_id,
        "X-User-Role": role,
        "X-User-Strategies": ",".join(strategies),
    }


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

            response = await client.get(
                f"{EXECUTION_GATEWAY_URL}/api/v1/tca/analysis",
                params=params,
                headers=_build_tca_auth_headers(user_id, role, strategies),
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


async def _fetch_tca_benchmarks(
    client_order_id: str,
    user_id: str,
    role: str,
    strategies: list[str],
    benchmark: str = "vwap",
    *,
    symbol: str = "",
    strategy_id: str = "",
) -> dict[str, Any] | None:
    """Fetch benchmark time series for an order.

    Returns None on error so callers can hide the benchmark chart instead of
    rendering synthetic data.
    """
    log_ctx: dict[str, Any] = {
        "client_order_id": client_order_id,
        "benchmark": benchmark,
        "symbol": symbol,
        "strategy_id": strategy_id,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{EXECUTION_GATEWAY_URL}/api/v1/tca/benchmarks",
                params={"client_order_id": client_order_id, "benchmark": benchmark},
                headers=_build_tca_auth_headers(user_id, role, strategies),
            )
            if response.status_code == 200:
                raw = response.json()
                if not isinstance(raw, dict):
                    logger.warning(
                        "TCA benchmarks API returned non-dict JSON",
                        extra=log_ctx,
                    )
                    return None
                result: dict[str, Any] = raw
                points = result.get("points")
                if not isinstance(points, list):
                    logger.warning(
                        "TCA benchmarks API returned non-list points",
                        extra={**log_ctx, "points_type": type(points).__name__},
                    )
                    return None
                if not points:
                    return None
                # Discard entries that are not dicts to guard against
                # schema drift from the upstream API.
                result["points"] = [p for p in points if isinstance(p, dict)]
                return result if result["points"] else None
            logger.warning(
                "TCA benchmarks API returned non-200",
                extra={
                    **log_ctx,
                    "status": response.status_code,
                    "body": response.text[:200],
                },
            )
    except httpx.RequestError as e:
        logger.warning(
            "TCA benchmarks API unavailable",
            extra={**log_ctx, "error": str(e)},
        )
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        logger.warning(
            "TCA benchmarks API returned malformed response",
            extra={**log_ctx, "error": str(e)},
        )
    return None


def _generate_demo_data(
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Generate demo TCA data for display when API unavailable."""
    import random

    # Use stable hash for deterministic demo data across process restarts
    random.seed(_stable_hash(f"{start_date}-{end_date}"))

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
            "execution_date": order_date.isoformat(),
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
    # Guard against zero division
    avg_is: float = sum(is_values) / n if n > 0 else 0.0  # type: ignore[arg-type]
    avg_vwap: float = sum(vwap_values) / n if n > 0 else 0.0  # type: ignore[arg-type]
    avg_impact: float = sum(impact_values) / n if n > 0 else 0.0  # type: ignore[arg-type]
    avg_fill: float = sum(fill_values) / n if n > 0 else 0.0  # type: ignore[arg-type]
    total_notional: float = sum(notional_values)  # type: ignore[arg-type]
    total_shares: int = sum(filled_qty_values)  # type: ignore[arg-type]

    return {
        "summary": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "computation_timestamp": datetime.now(UTC).isoformat(),
            "total_orders": n,
            "total_fills": n * random.randint(2, 5) if n > 0 else 0,
            "total_notional": total_notional,
            "total_shares": total_shares,
            "avg_fill_rate": avg_fill,
            "avg_implementation_shortfall_bps": avg_is,
            "avg_price_shortfall_bps": sum(price_values) / n if n > 0 else 0.0,  # type: ignore[arg-type]
            "avg_vwap_slippage_bps": avg_vwap,
            "avg_fee_cost_bps": sum(fee_values) / n if n > 0 else 0.0,  # type: ignore[arg-type]
            "avg_opportunity_cost_bps": sum(opp_values) / n if n > 0 else 0.0,  # type: ignore[arg-type]
            "avg_market_impact_bps": avg_impact,
            "avg_timing_cost_bps": sum(timing_values) / n if n > 0 else 0.0,  # type: ignore[arg-type]
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

    # Get authorized strategies — VIEW_ALL users may have empty provisioned
    # lists but should not be denied (they see all strategies globally).
    authorized_strategies = get_authorized_strategies(user)
    if not authorized_strategies and not has_permission(user, Permission.VIEW_ALL_STRATEGIES):
        with ui.card().classes("w-full p-6"):
            ui.label("You don't have access to any strategies. Contact administrator.").classes(
                "text-amber-600 text-center"
            )
        return

    # Render the dashboard - use authorized_strategies for API calls (not session strategies)
    await _render_tca_dashboard(
        user_id=user_id,
        role=user_role,
        authorized_strategies=authorized_strategies,
    )


async def _render_tca_dashboard(
    user_id: str,
    role: str,
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
        "_load_version": 0,
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
        """Load TCA data and update UI.

        Uses a version counter to discard results from stale requests
        when rapid filter changes overlap async fetches.
        """
        state["_load_version"] += 1
        current_version = state["_load_version"]

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
            # Update UI to reflect capped date
            start_input.value = str(start_dt)

        state["start_date"] = start_dt
        state["end_date"] = end_dt

        # Get filter values
        symbol = symbol_input.value.strip().upper() if symbol_input.value else None
        strategy = strategy_select.value if strategy_select.value != "All" else None
        state["symbol"] = symbol
        state["strategy_id"] = strategy

        # Fetch data - use authorized_strategies for API auth (not raw session strategies)
        data = await _fetch_tca_data(
            start_dt, end_dt, symbol, strategy, user_id, role, authorized_strategies
        )

        # Discard stale results if a newer load was triggered
        if state["_load_version"] != current_version:
            return

        # Check for demo mode: API failure OR API returned demo data
        summary = data.get("summary", {}) if data else {}
        warnings = summary.get("warnings", [])
        is_demo = data is None or any("Demo data" in str(w) for w in warnings)

        if data is None:
            # API failure - generate local demo data
            ui.notify("TCA API unavailable - showing demo data", type="warning")
            data = _generate_demo_data(start_dt, end_dt)

        if is_demo:
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

            # Benchmark comparison (real TCA benchmark data for first order)
            if orders:
                with ui.card().classes("w-full p-4"):
                    first_order = orders[0]
                    client_order_id = str(first_order.get("client_order_id", "")).strip()
                    order_symbol = str(first_order.get("symbol", ""))
                    order_label = order_symbol or client_order_id or "first order"
                    ui.label(
                        f"Execution vs Benchmark ({order_label})"
                    ).classes("text-lg font-bold mb-2")

                    benchmark_data = None
                    if not state["demo_mode"] and client_order_id:
                        benchmark_data = await _fetch_tca_benchmarks(
                            client_order_id=client_order_id,
                            user_id=user_id,
                            role=role,
                            strategies=authorized_strategies,
                            symbol=str(first_order.get("symbol", "")),
                            strategy_id=str(first_order.get("strategy_id", "")),
                        )

                    # Discard stale results if a newer load was triggered
                    if state["_load_version"] != current_version:
                        return

                    # Verify the response matches the requested order to
                    # guard against upstream cache/routing bugs.
                    if benchmark_data:
                        resp_order_id = str(
                            benchmark_data.get("client_order_id", "")
                        ).strip()
                        if resp_order_id and resp_order_id != client_order_id:
                            logger.warning(
                                "TCA benchmarks response order mismatch",
                                extra={
                                    "requested": client_order_id,
                                    "received": resp_order_id,
                                },
                            )
                            benchmark_data = None

                    if benchmark_data:
                        raw_points = benchmark_data.get("points", [])
                        raw_summary = benchmark_data.get("summary")
                        summary = raw_summary if isinstance(raw_summary, dict) else {}
                        benchmark_type = str(
                            benchmark_data.get("benchmark_type") or "vwap"
                        ).upper()

                        # Drop points with invalid timestamps or price fields
                        # to avoid rendering misleading data.
                        valid_points = [
                            p for p in raw_points
                            if _is_valid_timestamp(p.get("timestamp"))
                            and _is_valid_price(p.get("execution_price"))
                            and _is_valid_price(p.get("benchmark_price"))
                        ]

                        # Sort by normalized UTC datetime for correct
                        # chronological plotting regardless of offset format.
                        valid_points.sort(
                            key=lambda p: _parse_utc(p.get("timestamp", ""))
                        )

                        if valid_points:
                            # Detect multi-day fills to include date in labels
                            first_dt = _parse_utc(valid_points[0].get("timestamp", ""))
                            last_dt = _parse_utc(valid_points[-1].get("timestamp", ""))
                            multi_day = first_dt.date() != last_dt.date()

                            # Warn if data may be truncated.  Prefer the
                            # ``truncated`` flag from the API response when
                            # available; fall back to heuristic comparison
                            # against _BACKEND_FILL_LIMIT.
                            # TODO: Add ``truncated`` field to
                            # TCABenchmarkResponse so UI does not depend on
                            # hardcoded backend limit.
                            is_truncated = benchmark_data.get("truncated")
                            if is_truncated is None:
                                is_truncated = len(raw_points) >= _BACKEND_FILL_LIMIT
                            if is_truncated:
                                ui.label(
                                    "Note: benchmark data may be truncated "
                                    f"(showing first {len(raw_points)} fills)."
                                ).classes("text-amber-500 text-xs mb-1")

                            create_benchmark_comparison_chart(
                                timestamps=[
                                    _format_benchmark_timestamp(
                                        p.get("timestamp", ""),
                                        include_date=multi_day,
                                    )
                                    for p in valid_points
                                ],
                                execution_prices=[
                                    float(p["execution_price"]) for p in valid_points
                                ],
                                benchmark_prices=[
                                    float(p["benchmark_price"]) for p in valid_points
                                ],
                                benchmark_type=benchmark_type,
                                symbol=str(
                                    benchmark_data.get("symbol")
                                    or summary.get("symbol")
                                    or first_order.get("symbol", "")
                                ),
                            )
                        else:
                            ui.label(
                                "Benchmark chart unavailable: no valid data points."
                            ).classes("text-gray-500 p-4")
                    elif state["demo_mode"]:
                        ui.label(
                            "Benchmark chart hidden in demo mode to avoid showing synthetic data."
                        ).classes("text-gray-500 p-4")
                    else:
                        ui.label(
                            "Benchmark chart unavailable — the TCA benchmarks "
                            "API did not return data for this order. Check logs "
                            "for details."
                        ).classes("text-gray-500 p-4")

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
                    # Define AG Grid columns
                    column_defs = [
                        {"field": "execution_date", "headerName": "Date", "sortable": True},
                        {"field": "symbol", "headerName": "Symbol", "sortable": True, "width": 100},
                        {"field": "side", "headerName": "Side", "sortable": True, "width": 80},
                        {"field": "filled_qty", "headerName": "Filled", "sortable": True, "width": 100},
                        {"field": "fill_rate_pct", "headerName": "Fill %", "width": 100},
                        {"field": "is_bps", "headerName": "IS (bps)", "sortable": True, "width": 100},
                        {"field": "vwap_bps", "headerName": "VWAP (bps)", "sortable": True, "width": 100},
                        {"field": "impact_bps", "headerName": "Impact (bps)", "width": 100},
                        {"field": "notional", "headerName": "Notional", "width": 120},
                    ]

                    rows = []
                    for idx, order in enumerate(orders[:50]):  # Limit to 50 rows
                        notional = order.get("total_notional", 0)
                        if notional >= 1000:
                            notional_str = f"${notional / 1000:.1f}K"
                        else:
                            notional_str = f"${notional:.0f}"

                        rows.append({
                            "client_order_id": order.get("client_order_id", f"order-{idx}"),
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

                    # Create AG Grid with compact styling and global window registration
                    grid_options = apply_compact_grid_options({
                        "columnDefs": column_defs,
                        "rowData": rows,
                        "domLayout": "autoHeight",
                        "rowSelection": "single",
                        ":getRowId": "params => params.data.client_order_id",
                        # Register API on window for GridExportToolbar
                        ":onGridReady": "params => { window['tca-orders-grid'] = params.api; params.api.sizeColumnsToFit(); }",
                    })

                    ui.aggrid(grid_options).classes("w-full ag-theme-alpine-dark")
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
