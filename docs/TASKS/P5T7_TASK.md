---
id: P5T7
title: "NiceGUI Migration - Remaining Pages"
phase: P5
task: T7
priority: P1
owner: "@development-team"
state: PLANNING
created: 2025-12-31
dependencies: [P5T1, P5T2, P5T4, P5T5, P5T6]
estimated_effort: "18-25 days"
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P5_PLANNING.md, P5T4_TASK.md, P5T5_TASK.md, P5T6_TASK.md]
features: [T7.1, T7.2, T7.3, T7.4, T7.5, T7.6, T7.7]
---

# P5T7: NiceGUI Migration - Remaining Pages

**Phase:** P5 (Web Console Modernization)
**Status:** PLANNING
**Priority:** P1 (Feature Parity)
**Owner:** @development-team
**Created:** 2025-12-31
**Estimated Effort:** 18-25 days
**Track:** Phase 6 from P5_PLANNING.md
**Dependency:** P5T1 (Foundation), P5T2 (Layout), P5T4 (Dashboard), P5T5 (Manual Controls), P5T6 (Charts)

---

## Objective

Port all remaining Streamlit pages to NiceGUI to achieve complete feature parity.

**Success looks like:**
- All 7 remaining page categories ported to NiceGUI patterns
- Feature flag gating preserved for each page
- Permission checks preserved (RBAC parity)
- Auto-refresh patterns replaced with `ui.timer`
- `st.session_state` patterns replaced with async + app.storage
- `st.stop()` patterns replaced with early returns
- `st.tabs` patterns replaced with NiceGUI tabs
- Real-time updates where applicable
- Audit logging preserved
- Error handling graceful (no crashes)

**Key Pattern Changes:**
| Streamlit | NiceGUI |
|-----------|---------|
| `st_autorefresh(interval=5000)` | `ui.timer(5.0, callback)` |
| `st.tabs(["Tab1", "Tab2"])` | `with ui.tabs(): ui.tab("Tab1")` |
| `st.expander("Title")` | `with ui.expansion("Title"):` |
| `st.form("form_name")` | `with ui.card(): ...` + button validation |
| `st.session_state["key"]` | `app.storage.user["key"]` or async state |
| `st.stop()` | `return` (early exit from async function) |
| `st.rerun()` | `.refresh()` on `@ui.refreshable` sections |
| `st.progress(pct)` | `ui.linear_progress(value=pct)` |
| `st.selectbox()` | `ui.select()` |
| `st.text_area()` | `ui.textarea()` |
| `st.checkbox()` | `ui.checkbox()` |
| `st.download_button()` | `ui.download()` with bytes |
| `asyncio.run()` | Already in async context |

**Common Imports (assumed in all code snippets):**
```python
import asyncio
from nicegui import ui, run
from apps.web_console_ng.auth import get_current_user
```

---

## Acceptance Criteria

### T7.1 Circuit Breaker Dashboard (2 days)

**Port from:** `apps/web_console/pages/circuit_breaker.py`

**Feature Flag:** `FEATURE_CIRCUIT_BREAKER`
**Permission Required:** `VIEW_CIRCUIT_BREAKER` (view), `TRIP_CIRCUIT` (trip), `RESET_CIRCUIT` (reset)

**Deliverables:**
- [ ] Status display with color coding (OPEN=green, TRIPPED=red, QUIET_PERIOD=yellow)
- [ ] Trip details expander (when tripped)
- [ ] Trip count metric card
- [ ] Manual trip control (with reason select/custom input)
- [ ] Manual reset control (with minimum reason length validation)
- [ ] Reset acknowledgment checkbox (step-up confirmation)
- [ ] Rate limiting on reset (1 per minute globally)
- [ ] Trip/reset history table with preferred column ordering
- [ ] Auto-refresh via `ui.timer(5.0, update_status)`
- [ ] Audit logging integration

**Implementation:**
```python
# apps/web_console_ng/pages/circuit_breaker.py
from nicegui import ui, app
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console_ng.auth.middleware import requires_auth
from apps.web_console_ng.auth.permissions import Permission, has_permission
from apps.web_console_ng.config import (
    FEATURE_CIRCUIT_BREAKER,
    MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH,
)
from apps.web_console.services.cb_service import (
    CircuitBreakerService,
    RateLimitExceeded,
    RBACViolation,
    ValidationError,
)
from libs.risk_management.breaker import CircuitBreakerState

# Status color mapping
STATUS_COLORS = {
    CircuitBreakerState.OPEN.value: ("bg-green-100 text-green-800", "Trading is allowed"),
    CircuitBreakerState.TRIPPED.value: ("bg-red-100 text-red-800", "Trading is blocked"),
    CircuitBreakerState.QUIET_PERIOD.value: ("bg-yellow-100 text-yellow-800", "Recovering..."),
}


@ui.page("/circuit-breaker")
@requires_auth
@main_layout
async def circuit_breaker_dashboard() -> None:
    """Circuit breaker monitoring and control page."""
    user = get_current_user()

    # Feature flag check
    if not FEATURE_CIRCUIT_BREAKER:
        ui.label("Circuit Breaker Dashboard feature is disabled.").classes(
            "text-gray-500 text-center p-8"
        )
        return

    # Permission check (view access)
    if not has_permission(user, Permission.VIEW_CIRCUIT_BREAKER):
        ui.notify("Permission denied: VIEW_CIRCUIT_BREAKER required", type="negative")
        return

    # Initialize service (reuse existing sync factory, no await needed)
    # NOTE: _get_cb_service is sync and caches in session_state equivalent
    cb_service = _get_cb_service()

    # State container
    status_data: dict = {}

    async def fetch_status() -> None:
        nonlocal status_data
        try:
            status_data = cb_service.get_status()
        except RuntimeError as e:
            ui.notify(f"Cannot retrieve status: {e}", type="negative")

    await fetch_status()

    with ui.card().classes("w-full max-w-4xl mx-auto p-6"):
        ui.label("Circuit Breaker Dashboard").classes("text-2xl font-bold mb-4")

        # === STATUS SECTION ===
        @ui.refreshable
        def status_section() -> None:
            state = status_data.get("state", "UNKNOWN")
            color_class, description = STATUS_COLORS.get(
                state, ("bg-gray-100 text-gray-800", "Unknown state")
            )

            with ui.card().classes(f"w-full p-4 mb-4 {color_class}"):
                ui.label(f"Status: {state}").classes("text-xl font-bold")
                ui.label(description).classes("text-sm")

            if state == CircuitBreakerState.TRIPPED.value:
                ui.label(f"Reason: {status_data.get('trip_reason', 'Unknown')}").classes(
                    "text-red-600 font-semibold"
                )
                ui.label(f"Tripped at: {status_data.get('tripped_at', 'Unknown')}").classes(
                    "text-sm text-gray-600"
                )
                if status_data.get("trip_details"):
                    with ui.expansion("Trip Details").classes("w-full"):
                        ui.json_editor({"content": status_data["trip_details"]}, on_change=lambda: None)

            elif state == CircuitBreakerState.QUIET_PERIOD.value:
                ui.label(f"Reset at: {status_data.get('reset_at', 'Unknown')}").classes(
                    "text-sm text-gray-600"
                )

            # Trip count metric
            trip_count = status_data.get("trip_count_today", 0)
            if trip_count > 0:
                with ui.card().classes("p-4 mt-2"):
                    ui.label("Trips Today").classes("text-sm text-gray-500")
                    ui.label(str(trip_count)).classes("text-2xl font-bold")

        status_section()

        ui.separator().classes("my-4")

        # === CONTROLS SECTION ===
        with ui.row().classes("w-full gap-4"):
            # Trip control
            with ui.card().classes("flex-1 p-4"):
                ui.label("Manual Trip").classes("text-lg font-semibold mb-2")

                if has_permission(user, Permission.TRIP_CIRCUIT):
                    trip_reason = ui.select(
                        ["MANUAL", "DATA_STALE", "BROKER_ERRORS", "Other"],
                        value="MANUAL",
                        label="Trip Reason",
                    ).classes("w-full")

                    custom_reason = ui.input(
                        "Custom reason",
                        placeholder="Specify reason...",
                    ).classes("w-full")
                    custom_reason.bind_visibility_from(trip_reason, "value", value="Other")

                    async def handle_trip() -> None:
                        final_reason = (
                            custom_reason.value if trip_reason.value == "Other" else trip_reason.value
                        )
                        if not final_reason:
                            ui.notify("Please provide a reason", type="warning")
                            return

                        try:
                            cb_service.trip(final_reason, user, acknowledged=True)
                            ui.notify("Circuit breaker TRIPPED", type="positive")
                            await fetch_status()
                            status_section.refresh()
                            history_section.refresh()
                        except (ValidationError, RBACViolation) as e:
                            ui.notify(str(e), type="negative")

                    ui.button("Trip Circuit Breaker", on_click=handle_trip, color="red").classes(
                        "mt-2"
                    )
                else:
                    ui.label("TRIP_CIRCUIT permission required").classes("text-gray-500")

            # Reset control
            with ui.card().classes("flex-1 p-4"):
                ui.label("Reset Circuit Breaker").classes("text-lg font-semibold mb-2")

                if has_permission(user, Permission.RESET_CIRCUIT):
                    ui.label(
                        "Resetting will enter a 5-minute quiet period before returning to OPEN."
                    ).classes("text-sm text-yellow-600 mb-2")

                    reset_reason = ui.textarea(
                        f"Reset Reason (min {MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH} chars)",
                        placeholder="Explain why it's safe to resume trading...",
                    ).classes("w-full")

                    char_count_label = ui.label(f"0/{MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH}").classes(
                        "text-xs text-gray-400"
                    )

                    def update_char_count() -> None:
                        count = len(reset_reason.value or "")
                        char_count_label.text = f"{count}/{MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH}"

                    reset_reason.on("input", update_char_count)

                    acknowledged = ui.checkbox(
                        "I acknowledge that resetting will allow trading to resume"
                    )

                    async def handle_reset() -> None:
                        reason = reset_reason.value or ""
                        if len(reason) < MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH:
                            ui.notify(
                                f"Reason must be at least {MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH} characters",
                                type="warning",
                            )
                            return
                        if not acknowledged.value:
                            ui.notify("Please acknowledge the reset", type="warning")
                            return

                        try:
                            cb_service.reset(reason, user, acknowledged=True)
                            ui.notify("Circuit breaker RESET - entering quiet period", type="positive")
                            reset_reason.value = ""
                            acknowledged.value = False
                            await fetch_status()
                            status_section.refresh()
                            history_section.refresh()
                        except RateLimitExceeded as e:
                            ui.notify(f"Rate limit exceeded: {e}", type="negative")
                        except (ValidationError, RBACViolation) as e:
                            ui.notify(str(e), type="negative")

                    ui.button("Confirm Reset", on_click=handle_reset, color="green").classes("mt-2")
                else:
                    ui.label("RESET_CIRCUIT permission required").classes("text-gray-500")

        ui.separator().classes("my-4")

        # === HISTORY SECTION ===
        @ui.refreshable
        def history_section() -> None:
            ui.label("Trip/Reset History").classes("text-lg font-semibold mb-2")

            history = cb_service.get_history(limit=50)
            if not history:
                ui.label("No trip history recorded").classes("text-gray-500")
                return

            # Column ordering
            columns = [
                {"name": "tripped_at", "label": "Tripped At", "field": "tripped_at"},
                {"name": "reason", "label": "Reason", "field": "reason"},
                {"name": "reset_at", "label": "Reset At", "field": "reset_at"},
                {"name": "reset_by", "label": "Reset By", "field": "reset_by"},
                {"name": "reset_reason", "label": "Reset Reason", "field": "reset_reason"},
            ]

            ui.table(columns=columns, rows=history).classes("w-full")

        history_section()

    # Auto-refresh every 5 seconds (FIX Rev 5: refresh both status and history)
    async def auto_refresh() -> None:
        await fetch_status()
        status_section.refresh()
        history_section.refresh()  # Include history in case another operator trips/resets

    ui.timer(5.0, auto_refresh)
```

**Testing:**
- [ ] Status displays correct color for each state
- [ ] Trip control validates reason input
- [ ] Reset control enforces minimum reason length
- [ ] Reset requires acknowledgment checkbox
- [ ] Rate limiting blocks rapid resets
- [ ] History table shows correct column order
- [ ] Auto-refresh updates status every 5 seconds
- [ ] Feature flag gating works
- [ ] Permission checks work (view, trip, reset)
- [ ] Audit logging captures trip/reset events

---

### T7.2 System Health Monitor (2 days)

**Port from:** `apps/web_console/pages/health.py`

**Feature Flag:** `FEATURE_HEALTH_MONITOR`
**Permission Required:** `VIEW_CIRCUIT_BREAKER`

**Deliverables:**
- [ ] Service status grid (3 columns) with status emoji/color
- [ ] Staleness indicators for cached responses
- [ ] Last operation timestamp display
- [ ] Error display with expandable details
- [ ] Infrastructure connectivity panel (Redis, PostgreSQL)
- [ ] Redis version and memory info
- [ ] PostgreSQL latency display
- [ ] Queue depth placeholder (pending C2.1)
- [ ] Latency metrics table (P50/P95/P99)
- [ ] Latency bar chart
- [ ] Auto-refresh via `ui.timer(AUTO_REFRESH_INTERVAL, update)`
- [ ] Concurrent async data fetching

**Implementation:**
```python
# apps/web_console_ng/pages/health.py
from nicegui import ui
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console_ng.auth.middleware import requires_auth
from apps.web_console_ng.auth.permissions import Permission, has_permission
from apps.web_console_ng.config import FEATURE_HEALTH_MONITOR, AUTO_REFRESH_INTERVAL
from apps.web_console.services.health_service import HealthMonitorService, ConnectivityStatus

STATUS_EMOJI = {
    "healthy": "check_circle",
    "degraded": "warning",
    "unhealthy": "cancel",
    "stale": "hourglass_empty",
    "unreachable": "block",
}

STATUS_COLOR = {
    "healthy": "text-green-600",
    "degraded": "text-yellow-600",
    "unhealthy": "text-red-600",
    "stale": "text-gray-500",
    "unreachable": "text-red-800",
}


@ui.page("/health")
@requires_auth
@main_layout
async def health_monitor() -> None:
    """System health monitoring page."""
    user = get_current_user()

    # Feature flag check
    if not FEATURE_HEALTH_MONITOR:
        ui.label("System Health Monitor feature is disabled.").classes(
            "text-gray-500 text-center p-8"
        )
        return

    # Permission check
    if not has_permission(user, Permission.VIEW_CIRCUIT_BREAKER):
        ui.notify("Permission denied: VIEW_CIRCUIT_BREAKER required", type="negative")
        return

    # Initialize service (reuse existing sync factory, no await needed)
    # NOTE: _get_health_service is sync and caches in session_state equivalent
    health_service = _get_health_service()
    health_data: dict = {}

    async def fetch_health_data() -> None:
        nonlocal health_data
        try:
            # Concurrent fetching
            statuses, connectivity, latencies = await asyncio.gather(
                health_service.get_all_services_status(),
                health_service.get_connectivity(),
                health_service.get_latency_metrics(),
                return_exceptions=True,
            )
            health_data = {
                "statuses": statuses if not isinstance(statuses, Exception) else {},
                "connectivity": connectivity if not isinstance(connectivity, Exception) else None,
                "latencies": latencies if not isinstance(latencies, Exception) else ({}, False, None),
            }
        except Exception as e:
            ui.notify(f"Error fetching health data: {e}", type="negative")

    await fetch_health_data()

    with ui.card().classes("w-full max-w-6xl mx-auto p-6"):
        ui.label("System Health Monitor").classes("text-2xl font-bold mb-4")

        # === SERVICE STATUS GRID ===
        @ui.refreshable
        def service_grid() -> None:
            ui.label("Service Status").classes("text-lg font-semibold mb-2")

            statuses = health_data.get("statuses", {})
            if not statuses:
                ui.label("No services available").classes("text-gray-500")
                return

            with ui.grid(columns=3).classes("gap-4 w-full"):
                for service, health in statuses.items():
                    with ui.card().classes("p-4"):
                        with ui.row().classes("items-center gap-2"):
                            ui.icon(STATUS_EMOJI.get(health.status, "help")).classes(
                                STATUS_COLOR.get(health.status, "text-gray-500")
                            )
                            ui.label(service).classes("font-semibold")

                        ui.label(f"Status: {health.status.upper()}").classes("text-sm")
                        ui.label(f"Response: {health.response_time_ms:.1f}ms").classes(
                            "text-xs text-gray-500"
                        )

                        if health.is_stale:
                            age = f"{health.stale_age_seconds:.0f}s" if health.stale_age_seconds else "unknown"
                            with ui.card().classes("bg-yellow-50 p-2 mt-2"):
                                ui.label(f"STALE DATA ({age} old)").classes("text-yellow-700 text-xs")

                        if health.last_operation_timestamp:
                            ui.label(f"Last op: {format_relative_time(health.last_operation_timestamp)}").classes(
                                "text-xs text-gray-400"
                            )

                        if health.error and not health.is_stale:
                            ui.label(health.error).classes("text-red-600 text-xs mt-1")

                        if health.details:
                            with ui.expansion("Details").classes("mt-2"):
                                for key, value in health.details.items():
                                    if key not in {"status", "service", "timestamp", "cached_at"}:
                                        ui.label(f"{key}: {value}").classes("text-xs")

        service_grid()

        ui.separator().classes("my-4")

        # === INFRASTRUCTURE CONNECTIVITY ===
        @ui.refreshable
        def connectivity_section() -> None:
            ui.label("Infrastructure").classes("text-lg font-semibold mb-2")

            connectivity = health_data.get("connectivity")
            if connectivity is None:
                ui.label("Connectivity data unavailable").classes("text-gray-500")
                return

            if connectivity.is_stale:
                age = f"{connectivity.stale_age_seconds:.0f}s" if connectivity.stale_age_seconds else "unknown"
                with ui.card().classes("bg-yellow-50 p-2 mb-2"):
                    ui.label(f"STALE DATA ({age} old) - connectivity checks failing").classes(
                        "text-yellow-700 text-sm"
                    )

            with ui.row().classes("gap-8"):
                # Redis
                with ui.card().classes("p-4 flex-1"):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("check_circle" if connectivity.redis_connected else "cancel").classes(
                            "text-green-600" if connectivity.redis_connected else "text-red-600"
                        )
                        ui.label("Redis").classes("font-semibold")

                    if connectivity.redis_info:
                        ui.label(f"Version: {connectivity.redis_info.get('redis_version', 'unknown')}").classes(
                            "text-xs text-gray-500"
                        )
                        ui.label(f"Memory: {connectivity.redis_info.get('used_memory_human', 'unknown')}").classes(
                            "text-xs text-gray-500"
                        )
                    if connectivity.redis_error:
                        ui.label(f"Error: {connectivity.redis_error}").classes("text-xs text-red-600")

                # PostgreSQL
                with ui.card().classes("p-4 flex-1"):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("check_circle" if connectivity.postgres_connected else "cancel").classes(
                            "text-green-600" if connectivity.postgres_connected else "text-red-600"
                        )
                        ui.label("PostgreSQL").classes("font-semibold")

                    if connectivity.postgres_latency_ms:
                        ui.label(f"Latency: {connectivity.postgres_latency_ms:.1f}ms").classes(
                            "text-xs text-gray-500"
                        )
                    if connectivity.postgres_error:
                        ui.label(f"Error: {connectivity.postgres_error}").classes("text-xs text-red-600")

            ui.label(f"Last checked: {connectivity.checked_at.isoformat()}").classes(
                "text-xs text-gray-400 mt-2"
            )

        connectivity_section()

        ui.separator().classes("my-4")

        # === QUEUE DEPTH (Placeholder) ===
        ui.label("Signal Queue Depth").classes("text-lg font-semibold mb-2")
        ui.label("Queue depth metrics pending infrastructure approval").classes("text-gray-500")
        ui.label("Enable after ADR-012 approval and Redis Streams deployment (C2.1)").classes(
            "text-xs text-gray-400"
        )

        ui.separator().classes("my-4")

        # === LATENCY METRICS ===
        @ui.refreshable
        def latency_section() -> None:
            ui.label("Latency Metrics (P50/P95/P99)").classes("text-lg font-semibold mb-2")

            latencies_result = health_data.get("latencies", ({}, False, None))
            if isinstance(latencies_result, tuple):
                latencies, is_stale, stale_age = latencies_result
            else:
                latencies, is_stale, stale_age = {}, False, None

            if not latencies:
                ui.label("No latency data available").classes("text-gray-500")
                return

            # Table data
            rows = []
            for service, metrics in latencies.items():
                if metrics.p50_ms is not None:
                    rows.append({
                        "service": service,
                        "operation": metrics.operation,
                        "p50": f"{metrics.p50_ms:.1f}",
                        "p95": f"{metrics.p95_ms:.1f}" if metrics.p95_ms else "N/A",
                        "p99": f"{metrics.p99_ms:.1f}" if metrics.p99_ms else "N/A",
                    })
                elif metrics.error:
                    rows.append({
                        "service": service,
                        "operation": metrics.operation,
                        "p50": "Error",
                        "p95": "Error",
                        "p99": "Error",
                    })

            if not rows:
                ui.label("Latency metrics unavailable - Prometheus may be unreachable").classes(
                    "text-yellow-600"
                )
                return

            columns = [
                {"name": "service", "label": "Service", "field": "service"},
                {"name": "operation", "label": "Operation", "field": "operation"},
                {"name": "p50", "label": "P50 (ms)", "field": "p50"},
                {"name": "p95", "label": "P95 (ms)", "field": "p95"},
                {"name": "p99", "label": "P99 (ms)", "field": "p99"},
            ]

            ui.table(columns=columns, rows=rows).classes("w-full")

            # Bar chart
            chart_data = [r for r in rows if r["p50"] != "Error"]
            if chart_data:
                import plotly.graph_objects as go

                fig = go.Figure(data=[
                    go.Bar(name="P50", x=[r["service"] for r in chart_data], y=[float(r["p50"]) for r in chart_data]),
                    go.Bar(name="P95", x=[r["service"] for r in chart_data], y=[float(r["p95"]) if r["p95"] != "N/A" else 0 for r in chart_data]),
                    go.Bar(name="P99", x=[r["service"] for r in chart_data], y=[float(r["p99"]) if r["p99"] != "N/A" else 0 for r in chart_data]),
                ])
                fig.update_layout(
                    barmode="group",
                    title="Latency by Service",
                    yaxis_title="Latency (ms)",
                    height=300,
                )
                ui.plotly(fig).classes("w-full")

            if is_stale and stale_age:
                ui.label(f"Latency data is {stale_age:.0f}s old (Prometheus unavailable)").classes(
                    "text-xs text-gray-400 mt-2"
                )

        latency_section()

    # Auto-refresh
    async def auto_refresh() -> None:
        await fetch_health_data()
        service_grid.refresh()
        connectivity_section.refresh()
        latency_section.refresh()

    ui.timer(float(AUTO_REFRESH_INTERVAL), auto_refresh)
```

**Testing:**
- [ ] Service grid displays 3 columns
- [ ] Staleness indicators show for cached data
- [ ] Infrastructure connectivity shows Redis/PostgreSQL status
- [ ] Latency table renders with P50/P95/P99
- [ ] Bar chart renders for valid latency data
- [ ] Auto-refresh updates all sections
- [ ] Feature flag gating works
- [ ] Permission check works

---

### T7.3 Backtest Manager (3-4 days)

**Port from:** `apps/web_console/pages/backtest.py`

**Permissions Required:**
- `VIEW_PNL` - View backtest page and results
- `EXPORT_DATA` - Export backtest results (checked in render_backtest_result)

**User Context:** Must include `role` and `strategies` for export permission check.

**Deliverables:**
- [ ] Tab layout (New Backtest, Running Jobs, Results)
- [ ] **Port `render_backtest_form` component** (alpha validation, date validation, weight method options)
- [ ] Job priority selection (high, normal, low)
- [ ] Running jobs list with progress bars
- [ ] Cancel button per job
- [ ] Progressive polling (2s -> 5s -> 10s -> 30s) with **dynamic interval recalculation**
- [ ] **Polling reset when job set changes** (new job submitted or job completes)
- [ ] Completed results with expandable details
- [ ] Comparison mode with multi-select **(max 5 selections enforced)**
- [ ] Comparison table for selected backtests
- [ ] Export functionality (**EXPORT_DATA permission check, requires role/strategies**)
- [ ] Error display for failed jobs
- [ ] Cancelled job display

**Port Components (CRITICAL - maintain validation parity):**
- `apps/web_console/components/backtest_form.py` - Alpha discovery, date range validation, weight method
- `apps/web_console/components/backtest_results.py` - Result rendering with EXPORT_DATA check

**Implementation:**
```python
# apps/web_console_ng/pages/backtest.py
from nicegui import ui
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console_ng.auth.middleware import requires_auth
from apps.web_console_ng.auth.permissions import Permission, has_permission
# Import PORTED NiceGUI components (not Streamlit originals)
from apps.web_console_ng.components.backtest_form import render_backtest_form_ng
from apps.web_console_ng.components.backtest_results import render_backtest_result_ng, render_comparison_table_ng
from apps.web_console.utils.sync_db_pool import get_sync_db_pool
from libs.backtest.job_queue import BacktestJobConfig, JobPriority
from libs.backtest.result_storage import BacktestResultStorage

# Polling intervals (progressive backoff)
POLL_INTERVALS = {
    30: 2.0,    # < 30s: 2s
    60: 5.0,    # < 60s: 5s
    300: 10.0,  # < 5min: 10s
    None: 30.0,  # > 5min: 30s
}

MAX_COMPARISON_SELECTIONS = 5


@ui.page("/backtest")
@requires_auth
@main_layout
async def backtest_manager() -> None:
    """Backtest configuration and results page."""
    user = get_current_user()

    # Permission check
    if not has_permission(user, Permission.VIEW_PNL):
        ui.notify("Permission denied: VIEW_PNL required", type="negative")
        return

    with ui.card().classes("w-full max-w-6xl mx-auto p-6"):
        ui.label("Backtest Manager").classes("text-2xl font-bold mb-4")

        with ui.tabs().classes("w-full") as tabs:
            tab_new = ui.tab("New Backtest")
            tab_running = ui.tab("Running Jobs")
            tab_results = ui.tab("Results")

        with ui.tab_panels(tabs, value=tab_new).classes("w-full"):
            # === NEW BACKTEST TAB ===
            with ui.tab_panel(tab_new):
                await render_new_backtest_form(user)

            # === RUNNING JOBS TAB ===
            with ui.tab_panel(tab_running):
                await render_running_jobs(user)

            # === RESULTS TAB ===
            with ui.tab_panel(tab_results):
                await render_backtest_results(user)


async def render_new_backtest_form(user: dict) -> None:
    """Render the new backtest submission form.

    CRITICAL: Port the existing render_backtest_form component for parity.
    DO NOT simplify - preserve all validation and UX from Streamlit version.
    """
    # PARITY: Call the ported render_backtest_form component
    # This component handles:
    # - Alpha discovery/validation
    # - Date range validation with calendar picker
    # - Weight method selection with descriptions
    # - Priority selection
    # - Form submission with proper error handling
    await render_backtest_form_ng(
        on_submit=submit_backtest_job,
        get_current_username=lambda: user["username"],
    )

    # NOTE: The render_backtest_form_ng component must be ported from:
    # apps/web_console/components/backtest_form.py
    # It should maintain all validation logic from the Streamlit version.


async def render_running_jobs(user: dict) -> None:
    """Render list of running/queued jobs with progress.

    Progressive polling with dynamic interval recalculation:
    - Reset elapsed time when job set changes (new job or completion)
    - Interval backs off: 2s (<30s) -> 5s (<60s) -> 10s (<5min) -> 30s
    """
    jobs_data: list = []
    poll_elapsed = 0.0
    last_job_ids: set = set()
    last_newest_created: str | None = None

    async def fetch_jobs() -> None:
        nonlocal jobs_data
        jobs_data = await get_user_jobs(
            created_by=user["username"],
            status=["pending", "running"],
        )

    await fetch_jobs()

    @ui.refreshable
    def jobs_list() -> None:
        if not jobs_data:
            ui.label("No running or queued jobs").classes("text-gray-500")
            return

        for job in jobs_data:
            with ui.card().classes("w-full p-4 mb-2"):
                with ui.row().classes("w-full items-center"):
                    # Job info
                    with ui.column().classes("flex-1"):
                        status_icon = "sync" if job["status"] == "running" else "hourglass_empty"
                        with ui.row().classes("items-center gap-2"):
                            ui.icon(status_icon).classes(
                                "text-blue-600" if job["status"] == "running" else "text-gray-500"
                            )
                            ui.label(job["alpha_name"]).classes("font-semibold")
                        ui.label(f"{job['start_date']} to {job['end_date']}").classes(
                            "text-xs text-gray-500"
                        )

                    # Progress
                    with ui.column().classes("w-32"):
                        ui.linear_progress(value=job["progress_pct"] / 100).classes("w-full")
                        ui.label(f"{job['progress_pct']:.0f}%").classes("text-xs text-center")

                    # Cancel button
                    async def cancel(job_id: str = job["job_id"]) -> None:
                        await cancel_job(job_id)
                        await fetch_jobs()
                        jobs_list.refresh()

                    ui.button("Cancel", on_click=cancel, color="red").props("flat")

    jobs_list()

    # Progressive polling with dynamic interval and reset on job change
    def get_poll_interval() -> float:
        for threshold, interval in sorted(POLL_INTERVALS.items(), key=lambda x: (x[0] or float("inf"))):
            if threshold is None or poll_elapsed < threshold:
                return interval
        return 30.0

    async def poll() -> None:
        nonlocal poll_elapsed, last_job_ids, last_newest_created
        await fetch_jobs()
        jobs_list.refresh()

        # Check if job set changed (reset polling on change)
        current_job_ids = {j["job_id"] for j in jobs_data}
        newest_created = max((j["created_at"] for j in jobs_data), default=None)

        if current_job_ids != last_job_ids or newest_created != last_newest_created:
            # Job set changed - reset elapsed time for fast polling
            poll_elapsed = 0.0
        else:
            # No change - increment elapsed time
            poll_elapsed += get_poll_interval()

        last_job_ids = current_job_ids
        last_newest_created = newest_created

        # Dynamically update timer interval
        timer.interval = get_poll_interval()

    # Start with fast polling (2s)
    timer = ui.timer(get_poll_interval(), poll)


async def render_backtest_results(user: dict) -> None:
    """Render completed backtest results.

    CRITICAL: user dict must include role and strategies for export permission.
    The render_backtest_result component checks EXPORT_DATA permission.
    """
    # Build user_info with role/strategies for export permission check
    user_info = {
        "user_id": user.get("user_id"),
        "username": user.get("username"),
        "role": user.get("role"),  # CRITICAL for export permission
        "strategies": user.get("strategies", []),  # CRITICAL for export permission
    }

    results_data: list = []

    async def fetch_results() -> None:
        nonlocal results_data
        results_data = await get_user_jobs(
            created_by=user["username"],
            status=["completed", "failed", "cancelled"],
        )

    await fetch_results()

    comparison_mode = ui.checkbox("Enable Comparison Mode")
    selected_jobs: list = []

    @ui.refreshable
    def results_list() -> None:
        if not results_data:
            ui.label("No completed backtests yet. Submit a new backtest to get started.").classes(
                "text-gray-500"
            )
            return

        if comparison_mode.value:
            # Comparison mode
            completed_jobs = [j for j in results_data if j["status"] == "completed"]
            if len(completed_jobs) < 2:
                ui.label("Need at least 2 completed backtests for comparison").classes(
                    "text-yellow-600"
                )
            else:
                options = {
                    f"{j['alpha_name']} ({j['start_date']} - {j['end_date']})": j["job_id"]
                    for j in completed_jobs
                }

                # Track the select component for UI update
                comparison_select = ui.select(
                    list(options.keys()),
                    multiple=True,
                    label=f"Select backtests to compare (max {MAX_COMPARISON_SELECTIONS})",
                ).classes("w-full")

                # FIX (Rev 4): Separate refreshable for comparison table
                # Avoids recreating select component on every selection change
                @ui.refreshable
                def comparison_table_section() -> None:
                    if len(selected_jobs) >= 2:
                        render_comparison_table_ng(selected_jobs)

                # FIX (Rev 5): Single combined handler (avoid double registration)
                def on_select(e) -> None:
                    nonlocal selected_jobs
                    # Enforce max selection limit
                    if len(e.value) > MAX_COMPARISON_SELECTIONS:
                        # Truncate to max and UPDATE UI selection
                        truncated = e.value[:MAX_COMPARISON_SELECTIONS]
                        comparison_select.value = truncated
                        selected_jobs = [options[label] for label in truncated]
                        ui.notify(f"Maximum {MAX_COMPARISON_SELECTIONS} backtests can be compared", type="warning")
                    else:
                        selected_jobs = [options[label] for label in e.value]
                    # Refresh comparison table after selection change
                    comparison_table_section.refresh()

                comparison_select.on("change", on_select)
                comparison_table_section()

        else:
            # Single result view
            for job in results_data:
                status_icon = (
                    "check_circle" if job["status"] == "completed"
                    else "cancel" if job["status"] == "failed"
                    else "warning"
                )
                status_color = (
                    "text-green-600" if job["status"] == "completed"
                    else "text-red-600" if job["status"] == "failed"
                    else "text-yellow-600"
                )

                with ui.expansion(
                    f"{job['alpha_name']} ({job['start_date']} - {job['end_date']})"
                ).classes("w-full mb-2"):
                    with ui.row().classes("items-center gap-2 mb-2"):
                        ui.icon(status_icon).classes(status_color)
                        ui.label(job["status"].upper()).classes(status_color)

                    if job["status"] == "failed":
                        ui.label(f"Error: {job.get('error_message', 'Unknown error')}").classes(
                            "text-red-600"
                        )
                    elif job["status"] == "cancelled":
                        ui.label("Cancelled by user").classes("text-yellow-600")
                    else:
                        # Show result metrics using ported component
                        # CRITICAL: Use render_backtest_result (ported from Streamlit component)
                        # This component checks EXPORT_DATA permission internally
                        pool = get_sync_db_pool()
                        storage = BacktestResultStorage(pool)
                        # FIX (Rev 4): Use run.io_bound to prevent blocking event loop
                        # storage.get_result reads Parquet files - blocking I/O
                        result = await run.io_bound(storage.get_result, job["job_id"])
                        await render_backtest_result_ng(result, user_info=user_info)

    comparison_mode.on("change", lambda: results_list.refresh())
    results_list()
```

**Testing:**
- [ ] Tab navigation works
- [ ] Form validates required fields (port render_backtest_form parity)
- [ ] Job submission creates job
- [ ] Running jobs show progress
- [ ] Progressive polling backs off correctly (2s -> 5s -> 10s -> 30s)
- [ ] **Polling resets when job set changes**
- [ ] Cancel button works
- [ ] Comparison mode toggles correctly
- [ ] **Multi-select enforces max 5 selections**
- [ ] Comparison table renders for selected jobs
- [ ] Failed jobs show error message
- [ ] VIEW_PNL permission check works
- [ ] **Export button requires EXPORT_DATA permission**
- [ ] **Export requires user role/strategies in context**

---

### T7.4 Admin Dashboard (3-4 days)

**Port from:** `apps/web_console/pages/admin.py`

**Permissions Required:** At least one of: `MANAGE_API_KEYS`, `MANAGE_SYSTEM_CONFIG`, `VIEW_AUDIT`

**Service Dependencies (CRITICAL - must be wired):**
- `db_pool` - Database connection pool for all operations
- `redis_client` - Redis client for API key caching
- `audit_logger` - AuditLogger instance for action logging

**Deliverables:**
- [ ] Tab layout (API Keys, System Config, Audit Logs)
- [ ] API Key manager (create, revoke, list) - **requires db_pool, redis_client, audit_logger**
- [ ] System config editor (view, update) - **requires db_pool, redis_client, audit_logger**
- [ ] Audit log viewer with filters (user, action, event type, outcome, date range) - **requires db_pool**
- [ ] Audit log pagination
- [ ] Audit log CSV export
- [ ] Details expanders for audit entries
- [ ] Sensitive field masking (via sanitize_dict)
- [ ] RBAC enforcement per tab

**Port components:**
- `apps/web_console/components/api_key_manager.py`
- `apps/web_console/components/config_editor.py`
- `apps/web_console/components/audit_log_viewer.py`

**Implementation:**
```python
# apps/web_console_ng/pages/admin.py
from nicegui import ui
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console_ng.auth.middleware import requires_auth
from apps.web_console_ng.auth.permissions import Permission, has_permission
from apps.web_console.auth.audit_log import AuditLogger
from apps.web_console.utils.db_pool import get_db_pool, get_redis_client
from libs.common.log_sanitizer import sanitize_dict

ADMIN_PERMISSIONS = {
    Permission.MANAGE_API_KEYS,
    Permission.MANAGE_SYSTEM_CONFIG,
    Permission.VIEW_AUDIT,
}

PAGE_SIZE = 50
MAX_EXPORT_RECORDS = 10000


@ui.page("/admin")
@requires_auth
@main_layout
async def admin_dashboard() -> None:
    """Admin dashboard with API keys, config, and audit logs.

    CRITICAL: Components require db_pool, redis_client, and audit_logger.
    These must be wired correctly for audit logging and cache operations.
    """
    user = get_current_user()

    # Check for any admin permission
    if not any(has_permission(user, p) for p in ADMIN_PERMISSIONS):
        perm_names = ", ".join(p.value for p in ADMIN_PERMISSIONS)
        ui.notify(f"Access denied: Requires one of: {perm_names}", type="negative")
        return

    # Initialize service dependencies (CRITICAL for component parity)
    db_pool = get_db_pool()
    redis_client = get_redis_client()
    audit_logger = AuditLogger(db_pool)

    with ui.card().classes("w-full max-w-6xl mx-auto p-6"):
        ui.label("Admin Dashboard").classes("text-2xl font-bold mb-4")

        with ui.tabs().classes("w-full") as tabs:
            tab_api = ui.tab("API Keys")
            tab_config = ui.tab("System Config")
            tab_audit = ui.tab("Audit Logs")

        with ui.tab_panels(tabs, value=tab_api).classes("w-full"):
            with ui.tab_panel(tab_api):
                # Pass all required dependencies for api_key_manager
                await render_api_key_manager(user, db_pool, redis_client, audit_logger)

            with ui.tab_panel(tab_config):
                # Pass all required dependencies for config_editor
                await render_config_editor(user, db_pool, redis_client, audit_logger)

            with ui.tab_panel(tab_audit):
                # Audit log only needs db_pool
                await render_audit_log_viewer(user, db_pool)


async def render_audit_log_viewer(user: dict, db_pool: Any) -> None:
    """Render audit log with filters, pagination, and export.

    Args:
        user: User dict for permission checks
        db_pool: Database connection pool for audit queries
    """
    if not has_permission(user, Permission.VIEW_AUDIT):
        ui.label("Permission denied: VIEW_AUDIT required").classes("text-red-600")
        return

    ui.label("Audit Log").classes("text-lg font-semibold mb-2")
    ui.label("Query audit events with masking applied to sensitive fields.").classes(
        "text-xs text-gray-500 mb-4"
    )

    # Filter state
    filters = {
        "user_id": "",
        "action": "All",
        "event_type": "All",
        "outcome": "All",
        "start_date": None,
        "end_date": None,
    }
    current_page = 0
    audit_data: list = []
    total_count = 0

    # Filter form
    with ui.card().classes("w-full p-4 mb-4"):
        with ui.row().classes("gap-4 flex-wrap"):
            user_id_input = ui.input("User ID", placeholder="Exact match").classes("w-48")
            action_select = ui.select(
                ["All", "config_saved", "api_key_created", "api_key_revoked", "role_changed", "login", "logout"],
                value="All",
                label="Action",
            ).classes("w-40")
            event_type_select = ui.select(
                ["All", "admin", "auth", "action"],
                value="All",
                label="Event Type",
            ).classes("w-32")
            outcome_select = ui.select(
                ["All", "success", "failure"],
                value="All",
                label="Outcome",
            ).classes("w-32")

        use_date_filter = ui.checkbox("Filter by date range")
        with ui.row().classes("gap-4").bind_visibility_from(use_date_filter, "value"):
            start_date_input = ui.input("Start Date", placeholder="YYYY-MM-DD").classes("w-40")
            end_date_input = ui.input("End Date", placeholder="YYYY-MM-DD").classes("w-40")

        async def apply_filters() -> None:
            nonlocal filters, current_page, audit_data, total_count
            filters = {
                "user_id": user_id_input.value or None,
                "action": None if action_select.value == "All" else action_select.value,
                "event_type": None if event_type_select.value == "All" else event_type_select.value,
                "outcome": None if outcome_select.value == "All" else outcome_select.value,
                "start_date": start_date_input.value if use_date_filter.value else None,
                "end_date": end_date_input.value if use_date_filter.value else None,
            }
            current_page = 0
            audit_data, total_count = await fetch_audit_logs(filters, PAGE_SIZE, 0)
            log_table.refresh()
            pagination.refresh()

        ui.button("Apply Filters", on_click=apply_filters, color="primary").classes("mt-2")

    # Initial fetch
    audit_data, total_count = await fetch_audit_logs(filters, PAGE_SIZE, 0)

    # Table
    @ui.refreshable
    def log_table() -> None:
        ui.label(f"Showing {len(audit_data)} of {total_count} records (page {current_page + 1})").classes(
            "text-xs text-gray-500 mb-2"
        )

        if not audit_data:
            ui.label("No audit events found for the selected filters.").classes("text-gray-500")
            return

        columns = [
            {"name": "timestamp", "label": "Timestamp", "field": "timestamp"},
            {"name": "user_id", "label": "User ID", "field": "user_id"},
            {"name": "action", "label": "Action", "field": "action"},
            {"name": "event_type", "label": "Event Type", "field": "event_type"},
            {"name": "resource_type", "label": "Resource Type", "field": "resource_type"},
            {"name": "outcome", "label": "Outcome", "field": "outcome"},
        ]

        rows = [
            {
                "timestamp": log["timestamp"].isoformat() if hasattr(log["timestamp"], "isoformat") else str(log["timestamp"]),
                "user_id": log["user_id"],
                "action": log["action"],
                "event_type": log["event_type"],
                "resource_type": log.get("resource_type", ""),
                "outcome": log["outcome"],
            }
            for log in audit_data
        ]

        ui.table(columns=columns, rows=rows).classes("w-full")

        # Expandable details
        for log in audit_data:
            with ui.expansion(f"Details: {log['action']} @ {log['timestamp']}").classes("w-full mt-1"):
                # Apply masking to sensitive fields
                sanitized = sanitize_dict(log.get("details", {}) or {})
                ui.json_editor({"content": sanitized}, on_change=lambda: None)

    log_table()

    # Pagination
    @ui.refreshable
    def pagination() -> None:
        max_page = (total_count - 1) // PAGE_SIZE if total_count > 0 else 0

        with ui.row().classes("gap-4 mt-4"):
            async def prev_page() -> None:
                nonlocal current_page, audit_data, total_count
                current_page = max(0, current_page - 1)
                audit_data, total_count = await fetch_audit_logs(
                    filters, PAGE_SIZE, current_page * PAGE_SIZE
                )
                log_table.refresh()
                pagination.refresh()

            async def next_page() -> None:
                nonlocal current_page, audit_data, total_count
                current_page = min(max_page, current_page + 1)
                audit_data, total_count = await fetch_audit_logs(
                    filters, PAGE_SIZE, current_page * PAGE_SIZE
                )
                log_table.refresh()
                pagination.refresh()

            ui.button("Previous", on_click=prev_page).props(
                f"{'disabled' if current_page <= 0 else ''}"
            )
            ui.button("Next", on_click=next_page).props(
                f"{'disabled' if current_page >= max_page else ''}"
            )

    pagination()

    # Export
    if total_count > 0:
        async def export_csv() -> None:
            all_logs, _ = await fetch_audit_logs(filters, MAX_EXPORT_RECORDS, 0)
            csv_data = build_audit_csv(all_logs)
            # Trigger download
            ui.download(csv_data, "audit_logs.csv")

        ui.button("Download CSV", on_click=export_csv, icon="download").classes("mt-4")
```

**Testing:**
- [ ] Tab navigation works
- [ ] API key creation/revocation works (with permission)
- [ ] Config editor loads/saves (with permission)
- [ ] Audit log filters work
- [ ] Pagination navigates correctly
- [ ] CSV export downloads
- [ ] Sensitive fields masked in details
- [ ] RBAC enforced per tab
- [ ] No access without any admin permission

---

### T7.5 Alerts Configuration (2-3 days)

**Port from:** `apps/web_console/pages/alerts.py`

**Feature Flag:** `FEATURE_ALERTS`
**Permissions Required:** `VIEW_ALERTS` (view), `CREATE_ALERT_RULE`, `UPDATE_ALERT_RULE`, `DELETE_ALERT_RULE`, `ACKNOWLEDGE_ALERT`

**Deliverables:**
- [ ] Tab layout (Alert Rules, Alert History, Channels)
- [ ] Alert rules list with expandable JSON details
- [ ] Rule create/edit form
- [ ] Rule delete button (with permission)
- [ ] Alert history table
- [ ] Alert acknowledgment (with permission)
- [ ] Notification channels display
- [ ] PII masking on recipient display
- [ ] Async service calls via AlertConfigService

**Implementation:**
```python
# apps/web_console_ng/pages/alerts.py
from nicegui import ui
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console_ng.auth.middleware import requires_auth
from apps.web_console_ng.auth.permissions import Permission, has_permission
from apps.web_console_ng.config import FEATURE_ALERTS
from apps.web_console.services.alert_service import AlertConfigService, AlertRuleCreate, AlertRuleUpdate
from libs.alerts.pii import mask_recipient


@ui.page("/alerts")
@requires_auth
@main_layout
async def alerts_configuration() -> None:
    """Alert configuration and history page."""
    user = get_current_user()

    # Feature flag check
    if not FEATURE_ALERTS:
        ui.label("Alert configuration is disabled.").classes("text-gray-500 text-center p-8")
        return

    # Permission check
    if not has_permission(user, Permission.VIEW_ALERTS):
        ui.notify("Permission denied: VIEW_ALERTS required", type="negative")
        return

    alert_service = await get_alert_service()

    with ui.card().classes("w-full max-w-6xl mx-auto p-6"):
        ui.label("Alert Configuration").classes("text-2xl font-bold mb-4")

        with ui.tabs().classes("w-full") as tabs:
            tab_rules = ui.tab("Alert Rules")
            tab_history = ui.tab("Alert History")
            tab_channels = ui.tab("Channels")

        with ui.tab_panels(tabs, value=tab_rules).classes("w-full"):
            with ui.tab_panel(tab_rules):
                await render_alert_rules(user, alert_service)

            with ui.tab_panel(tab_history):
                await render_alert_history(user, alert_service)

            with ui.tab_panel(tab_channels):
                await render_channels(user, alert_service)


async def render_alert_rules(user: dict, service: AlertConfigService) -> None:
    """Render alert rules list with CRUD operations."""
    rules = await service.get_rules()
    editing_rule = None

    @ui.refreshable
    def rules_list() -> None:
        ui.label("Alert Rules").classes("text-lg font-semibold mb-2")

        if not rules:
            ui.label("No alert rules configured.").classes("text-gray-500")
        else:
            for rule in rules:
                with ui.expansion(f"{rule.name} ({rule.condition_type})").classes("w-full mb-2"):
                    # Rule details as JSON
                    rule_data = {
                        "threshold_value": str(rule.threshold_value),
                        "comparison": rule.comparison,
                        "enabled": rule.enabled,
                        "channels": [
                            {
                                "type": c.type.value,
                                "recipient": mask_recipient(c.recipient, c.type.value),
                                "enabled": c.enabled,
                            }
                            for c in rule.channels
                        ],
                    }
                    ui.json_editor({"content": rule_data}, on_change=lambda: None)

                    with ui.row().classes("gap-2 mt-2"):
                        # Delete button
                        if has_permission(user, Permission.DELETE_ALERT_RULE):
                            async def delete_rule(rule_id: str = str(rule.id)) -> None:
                                try:
                                    await service.delete_rule(rule_id, user)
                                    ui.notify("Rule deleted", type="positive")
                                    nonlocal rules
                                    rules = await service.get_rules()
                                    rules_list.refresh()
                                except Exception as e:
                                    ui.notify(f"Failed to delete: {e}", type="negative")

                            ui.button("Delete", on_click=delete_rule, color="red").props("flat")

                        # Edit button
                        if has_permission(user, Permission.UPDATE_ALERT_RULE):
                            async def edit_rule(r=rule) -> None:
                                nonlocal editing_rule
                                editing_rule = r
                                rule_form.refresh()

                            ui.button("Edit", on_click=edit_rule, color="primary").props("flat")

    rules_list()

    ui.separator().classes("my-4")

    # Create/Edit form
    @ui.refreshable
    def rule_form() -> None:
        if editing_rule:
            ui.label(f"Edit Rule: {editing_rule.name}").classes("text-lg font-semibold mb-2")
        elif has_permission(user, Permission.CREATE_ALERT_RULE):
            ui.label("Create Rule").classes("text-lg font-semibold mb-2")
        else:
            ui.label("You do not have permission to create rules.").classes("text-gray-500")
            return

        # Form fields
        name_input = ui.input(
            "Rule Name",
            value=editing_rule.name if editing_rule else "",
        ).classes("w-full")

        condition_type = ui.select(
            ["drawdown_breach", "var_breach", "position_limit", "loss_threshold"],
            value=editing_rule.condition_type if editing_rule else "drawdown_breach",
            label="Condition Type",
        ).classes("w-full")

        threshold = ui.number(
            "Threshold Value",
            value=float(editing_rule.threshold_value) if editing_rule else 0.0,
        ).classes("w-full")

        comparison = ui.select(
            ["gt", "lt", "gte", "lte", "eq"],
            value=editing_rule.comparison if editing_rule else "gt",
            label="Comparison",
        ).classes("w-full")

        enabled = ui.checkbox(
            "Enabled",
            value=editing_rule.enabled if editing_rule else True,
        )

        async def save_rule() -> None:
            nonlocal rules, editing_rule
            try:
                if editing_rule:
                    update = AlertRuleUpdate(
                        name=name_input.value,
                        condition_type=condition_type.value,
                        threshold_value=threshold.value,
                        comparison=comparison.value,
                        enabled=enabled.value,
                    )
                    await service.update_rule(str(editing_rule.id), update, user)
                    ui.notify("Rule updated", type="positive")
                else:
                    create = AlertRuleCreate(
                        name=name_input.value,
                        condition_type=condition_type.value,
                        threshold_value=threshold.value,
                        comparison=comparison.value,
                        enabled=enabled.value,
                    )
                    await service.create_rule(create, user)
                    ui.notify("Rule created", type="positive")

                editing_rule = None
                rules = await service.get_rules()
                rules_list.refresh()
                rule_form.refresh()
            except Exception as e:
                ui.notify(f"Failed to save: {e}", type="negative")

        ui.button("Save Rule", on_click=save_rule, color="primary").classes("mt-2")

        if editing_rule:
            async def cancel_edit() -> None:
                nonlocal editing_rule
                editing_rule = None
                rule_form.refresh()

            ui.button("Cancel", on_click=cancel_edit).props("flat").classes("ml-2")

    rule_form()


async def render_alert_history(user: dict, service: AlertConfigService) -> None:
    """Render alert event history with acknowledgment."""
    ui.label("Alert History").classes("text-lg font-semibold mb-2")

    try:
        events = await service.get_alert_events()
    except Exception as e:
        ui.label(f"Alert history unavailable: {e}").classes("text-gray-500")
        return

    can_ack = has_permission(user, Permission.ACKNOWLEDGE_ALERT)

    if not events:
        ui.label("No alert events recorded.").classes("text-gray-500")
        return

    for event in events:
        with ui.card().classes("w-full p-4 mb-2"):
            with ui.row().classes("items-center justify-between"):
                with ui.column():
                    ui.label(f"{event.rule_name}: {event.message}").classes("font-semibold")
                    ui.label(f"Triggered: {event.triggered_at}").classes("text-xs text-gray-500")

                if can_ack and not event.acknowledged:
                    ack_note = ui.input("Note", placeholder="Acknowledgment note").classes("w-48")

                    async def acknowledge(event_id: str = event.id) -> None:
                        try:
                            await service.acknowledge_alert(event_id, ack_note.value or "", user)
                            ui.notify("Alert acknowledged", type="positive")
                        except Exception as e:
                            ui.notify(f"Failed: {e}", type="negative")

                    ui.button("Acknowledge", on_click=acknowledge, color="green").props("flat")
                elif event.acknowledged:
                    ui.label("Acknowledged").classes("text-green-600 text-sm")
```

**Testing:**
- [ ] Tab navigation works
- [ ] Rules list displays with expandable details
- [ ] Create rule works (with permission)
- [ ] Edit rule works (with permission)
- [ ] Delete rule works (with permission)
- [ ] Alert history displays events
- [ ] Acknowledgment works (with permission)
- [ ] PII masked in recipient display
- [ ] Feature flag gating works
- [ ] RBAC enforced per operation

---

### T7.6 Audit Log Viewer (Standalone) (2 days)

**Note:** Audit log viewer is also embedded in Admin Dashboard (T7.4). This task covers the standalone page if needed, or can be consolidated with T7.4.

**Port from:** `apps/web_console/components/audit_log_viewer.py`

**Permission Required:** `VIEW_AUDIT`

**Deliverables:**
- [ ] All features from T7.4 audit tab
- [ ] Standalone page route `/audit`
- [ ] Full-page layout (wider than tab)

**Implementation:** See T7.4 `render_audit_log_viewer()` - can be reused as standalone page.

---

### T7.7 Data Management Pages (3-4 days)

**Port from:**
- `apps/web_console/pages/data_sync.py`
- `apps/web_console/pages/data_explorer.py`
- `apps/web_console/pages/data_quality.py`

**Permissions Required:**
- Data Sync: `VIEW_DATA_SYNC`, `TRIGGER_DATA_SYNC`
- Data Explorer: `QUERY_DATA`, `EXPORT_DATA` (for exports)
- Data Quality: `VIEW_DATA_QUALITY`

**Rate Limits (from service layer):**
- Data Explorer queries: 10 per minute
- Data Explorer exports: 5 per hour

**T7.7a Data Sync Dashboard**

**Timeout Constants (parity with Streamlit):**
```python
_FETCH_TIMEOUT_SECONDS = 10.0  # Dataset status fetch
_TRIGGER_TIMEOUT_SECONDS = 10.0  # Sync trigger operation
```

**Deliverables:**
- [ ] Tab layout (Sync Status, Sync Logs, Schedule Config)
- [ ] Manual sync sidebar with dataset selection
- [ ] Rate limiting on manual sync
- [ ] **Timeout handling** (10s for fetch, 10s for trigger)
- [ ] Sync status table
- [ ] Sync logs viewer
- [ ] Schedule editor

**T7.7b Dataset Explorer**

**Rate-Limited Operations:**
- `execute_query()` - 10 queries per minute
- `export_data()` - 5 exports per hour

**Deliverables:**
- [ ] Dataset browser sidebar
- [ ] Schema viewer
- [ ] Data preview (sample rows)
- [ ] Query editor with SQL input
- [ ] **Query rate limiting (10/min)**
- [ ] Export dialog (**EXPORT_DATA permission required**)
- [ ] **Export rate limiting (5/hour)**
- [ ] Coverage timeline chart

**T7.7c Data Quality Reports**

**Deliverables:**
- [ ] Tab layout (Validation Results, Anomaly Alerts, Trends, Coverage)
- [ ] Validation results table
- [ ] Anomaly alert feed
- [ ] Quality trend chart
- [ ] Coverage chart

**Implementation Pattern (Data Sync example with timeouts):**
```python
# apps/web_console_ng/pages/data_sync.py
import asyncio
from nicegui import ui
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console_ng.auth.middleware import requires_auth
from apps.web_console_ng.auth.permissions import Permission, has_permission
from apps.web_console.services.data_sync_service import DataSyncService, RateLimitExceeded

# Timeout constants (parity with Streamlit)
_FETCH_TIMEOUT_SECONDS = 10.0
_TRIGGER_TIMEOUT_SECONDS = 10.0


@ui.page("/data-sync")
@requires_auth
@main_layout
async def data_sync_dashboard() -> None:
    """Data sync monitoring and control page."""
    user = get_current_user()

    # Permission check
    if not has_permission(user, Permission.VIEW_DATA_SYNC):
        ui.notify("Permission denied: VIEW_DATA_SYNC required", type="negative")
        return

    service = DataSyncService()

    with ui.row().classes("w-full"):
        # === SIDEBAR: Manual Sync ===
        with ui.card().classes("w-64 p-4 mr-4"):
            ui.label("Manual Sync").classes("text-lg font-semibold mb-2")

            if not has_permission(user, Permission.TRIGGER_DATA_SYNC):
                ui.label("Permission required: TRIGGER_DATA_SYNC").classes("text-gray-500 text-sm")
            else:
                # Fetch datasets with timeout
                try:
                    statuses = await asyncio.wait_for(
                        service.get_sync_status(user),
                        timeout=_FETCH_TIMEOUT_SECONDS,
                    )
                    datasets = sorted({s.dataset for s in statuses})
                except asyncio.TimeoutError:
                    ui.label("Dataset fetch timed out").classes("text-red-600 text-sm")
                    datasets = []
                except Exception as e:
                    ui.label(f"Failed to load datasets: {e}").classes("text-red-600 text-sm")
                    datasets = []

                if not datasets:
                    ui.label("No datasets available for manual sync.").classes("text-gray-500 text-sm")
                else:
                    dataset_select = ui.select(
                        datasets,
                        label="Dataset",
                    ).classes("w-full")

                    reason_input = ui.input(
                        "Reason",
                        placeholder="Why run this sync now?",
                    ).classes("w-full")

                    async def trigger_sync() -> None:
                        if not reason_input.value:
                            ui.notify("Please provide a reason for audit logging", type="warning")
                            return

                        try:
                            job = await asyncio.wait_for(
                                service.trigger_sync(
                                    user=user,
                                    dataset=dataset_select.value,
                                    reason=reason_input.value,
                                ),
                                timeout=_TRIGGER_TIMEOUT_SECONDS,
                            )
                            ui.notify(f"Sync queued: {job.id}", type="positive")
                            reason_input.value = ""
                        except asyncio.TimeoutError:
                            ui.notify("Sync trigger timed out", type="negative")
                        except RateLimitExceeded as e:
                            ui.notify(str(e), type="negative")
                        except Exception as e:
                            ui.notify(f"Failed to trigger sync: {e}", type="negative")

                    ui.button("Trigger Sync", on_click=trigger_sync, color="primary").classes("mt-2")

        # === MAIN CONTENT ===
        with ui.card().classes("flex-1 p-6"):
            ui.label("Data Sync Dashboard").classes("text-2xl font-bold mb-4")

            with ui.tabs().classes("w-full") as tabs:
                tab_status = ui.tab("Sync Status")
                tab_logs = ui.tab("Sync Logs")
                tab_schedule = ui.tab("Schedule Config")

            with ui.tab_panels(tabs, value=tab_status).classes("w-full"):
                with ui.tab_panel(tab_status):
                    await render_sync_status_table(service, user)

                with ui.tab_panel(tab_logs):
                    await render_sync_logs_viewer(service, user)

                with ui.tab_panel(tab_schedule):
                    await render_sync_schedule_editor(service, user)
```

**Testing:**
- [ ] Data Sync: Manual sync triggers correctly
- [ ] Data Sync: Rate limiting prevents spam
- [ ] Data Sync: **Timeout handling** (10s fetch, 10s trigger)
- [ ] Data Sync: Status table shows all datasets
- [ ] Data Explorer: Schema loads for selected dataset
- [ ] Data Explorer: Query executes and shows results
- [ ] Data Explorer: **Query rate limiting (10/min)**
- [ ] Data Explorer: Export works (**EXPORT_DATA permission required**)
- [ ] Data Explorer: **Export rate limiting (5/hour)**
- [ ] Data Quality: Validation results table displays
- [ ] Data Quality: Anomaly alerts show severity
- [ ] Data Quality: Trend chart renders
- [ ] All pages: Permission checks enforced

---

## Prerequisites Checklist

**Must verify before starting implementation:**

- [ ] **P5T1 complete:** Foundation with async patterns
- [ ] **P5T2 complete:** Layout and navigation
- [ ] **P5T4 complete:** Real-Time Dashboard patterns
- [ ] **P5T5 complete:** Manual Trading Controls patterns
- [ ] **P5T6 complete:** Charts patterns
- [ ] **Services available:**
  - [ ] `CircuitBreakerService` - Trip/reset/history
  - [ ] `HealthMonitorService` - Service status, connectivity, latencies
  - [ ] `BacktestJobQueue` - Job submission
  - [ ] `BacktestResultStorage` - Result retrieval
  - [ ] `AlertConfigService` - Alert CRUD
  - [ ] `DataSyncService` - Sync operations
  - [ ] `DataExplorerService` - Query execution
  - [ ] `DataQualityService` - Quality reports
- [ ] **Config flags available:**
  - [ ] `FEATURE_CIRCUIT_BREAKER`
  - [ ] `FEATURE_HEALTH_MONITOR`
  - [ ] `FEATURE_ALERTS`
  - [ ] `AUTO_REFRESH_INTERVAL`
  - [ ] `MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH`

---

## Approach

### High-Level Plan

1. **C0: Circuit Breaker Dashboard** (2 days)
   - Status display with color coding
   - Trip/reset controls with validation
   - History table with auto-refresh

2. **C1: System Health Monitor** (2 days)
   - Service grid with staleness indicators
   - Infrastructure connectivity panel
   - Latency metrics with chart

3. **C2: Backtest Manager** (3-4 days)
   - Tab layout (New, Running, Results)
   - Job submission and progress tracking
   - Comparison mode

4. **C3: Admin Dashboard** (3-4 days)
   - API key management
   - Config editor
   - Audit log with export

5. **C4: Alerts Configuration** (2-3 days)
   - Rule CRUD
   - Alert history
   - Channel display

6. **C5: Data Management** (3-4 days)
   - Data Sync dashboard
   - Dataset Explorer
   - Data Quality reports

---

## Component Breakdown

### C0: Circuit Breaker Dashboard

**Files to Create:**
```
apps/web_console_ng/pages/
└── circuit_breaker.py
tests/apps/web_console_ng/
└── test_circuit_breaker.py
```

### C1: System Health Monitor

**Files to Create:**
```
apps/web_console_ng/pages/
└── health.py
tests/apps/web_console_ng/
└── test_health.py
```

### C2: Backtest Manager

**Files to Create:**
```
apps/web_console_ng/pages/
└── backtest.py
apps/web_console_ng/components/
├── backtest_form.py
└── backtest_results.py
tests/apps/web_console_ng/
└── test_backtest.py
```

### C3: Admin Dashboard

**Files to Create:**
```
apps/web_console_ng/pages/
└── admin.py
apps/web_console_ng/components/
├── api_key_manager.py
├── config_editor.py
└── audit_log_viewer.py
tests/apps/web_console_ng/
└── test_admin.py
```

### C4: Alerts Configuration

**Files to Create:**
```
apps/web_console_ng/pages/
└── alerts.py
apps/web_console_ng/components/
├── alert_rule_editor.py
└── alert_history.py
tests/apps/web_console_ng/
└── test_alerts.py
```

### C5: Data Management

**Files to Create:**
```
apps/web_console_ng/pages/
├── data_sync.py
├── data_explorer.py
└── data_quality.py
apps/web_console_ng/components/
├── sync_status_table.py
├── sync_logs_viewer.py
├── dataset_browser.py
├── schema_viewer.py
├── query_editor.py
├── validation_results.py
└── anomaly_feed.py
tests/apps/web_console_ng/
├── test_data_sync.py
├── test_data_explorer.py
└── test_data_quality.py
```

---

## Testing Strategy

### Unit Tests (CI - Automated)
- `test_circuit_breaker.py`: Status display, trip/reset logic
- `test_health.py`: Service grid, connectivity, latencies
- `test_backtest.py`: Form validation, job state
- `test_admin.py`: RBAC per tab, audit filters
- `test_alerts.py`: Rule CRUD, history display
- `test_data_*.py`: Sync, explorer, quality

### Integration Tests (CI - Docker)
- `test_*_integration.py`: Full page with real services

### E2E Tests (CI - Playwright)
- `test_pages_e2e.py`: Navigation, basic flows

---

## Dependencies

### External
- `nicegui>=2.0`: UI framework
- `plotly>=5.0`: Charting (health latencies)

### Internal
- `apps/web_console_ng/auth/`: Auth middleware (P5T2)
- `apps/web_console_ng/ui/layout.py`: Main layout (P5T2)
- `apps/web_console/services/`: Existing services (reuse)
- `libs/`: Common utilities (sanitize_dict, validators)

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Service interface changes | Low | Medium | Reuse existing services unchanged |
| Complex form state management | Medium | Medium | Use `@ui.refreshable` for isolated state |
| Async/await event loop issues | Medium | High | Test all async paths, use consistent patterns |
| Permission check gaps | Low | High | Comprehensive RBAC tests per page |
| Feature flag inconsistency | Low | Medium | Verify all flags exist in config |

---

## Implementation Notes

**Address during development:**

1. **Tab Pattern:**
   - Use `ui.tabs()` + `ui.tab_panels()` for tabbed layouts
   - Each tab panel is a separate context

2. **Form Pattern:**
   - NiceGUI doesn't have `st.form()` - use cards with validation
   - Validate on button click, not on input change

3. **Auto-Refresh Pattern:**
   - Replace `st_autorefresh()` with `ui.timer(seconds, callback)`
   - Timer callback should be async if fetching data

4. **Expander Pattern:**
   - Replace `st.expander()` with `ui.expansion()`
   - Content inside `with ui.expansion():` block

5. **Progress Pattern:**
   - Replace `st.progress()` with `ui.linear_progress(value=)`
   - Value is 0.0-1.0, not 0-100

6. **Download Pattern:**
   - Replace `st.download_button()` with `ui.download(bytes, filename)`
   - Build CSV as bytes before triggering

7. **Service Reuse:**
   - Reuse existing Streamlit services unchanged
   - They're async-compatible (use `await`)

8. **RBAC Enforcement:**
   - Check page-level permission first (early return)
   - Check operation-level permission at each button

9. **Backtest Form Parity:** (Rev 2)
   - Port `render_backtest_form` component, don't simplify
   - Preserve alpha validation, date validation, weight method options
   - Export requires `EXPORT_DATA` permission with `role` and `strategies` in user context

10. **Progressive Polling Reset:** (Rev 2)
    - Reset elapsed time when job set changes (new job or completion)
    - Dynamically update timer interval

11. **Admin Service Dependencies:** (Rev 2)
    - Components require `db_pool`, `redis_client`, `audit_logger`
    - Must be wired correctly for audit logging and cache operations

12. **Data Operation Timeouts:** (Rev 2)
    - Data Sync: 10s fetch timeout, 10s trigger timeout
    - Use `asyncio.wait_for()` to prevent UI hangs

13. **Data Explorer Rate Limits:** (Rev 2)
    - Query: 10 per minute
    - Export: 5 per hour (also requires `EXPORT_DATA` permission)

14. **Backtest Component Naming:** (Rev 3)
    - Ported NiceGUI components use `_ng` suffix: `render_backtest_form_ng`, `render_backtest_result_ng`
    - This avoids import confusion with original Streamlit components
    - Components must maintain parity with Streamlit validation/UX

15. **Comparison Selection UI Sync:** (Rev 3)
    - When truncating selections, must update `comparison_select.value` to reflect actual selections
    - Prevents UI showing more selections than enforced limit

16. **Service Factory Patterns:** (Rev 5)
    - Reuse existing sync service factories (`_get_cb_service`, `_get_health_service`, etc.)
    - These are sync functions that cache in session_state equivalent - no `await` needed
    - For NiceGUI, use `app.storage.user` instead of `st.session_state` for caching

17. **Single Event Handler:** (Rev 5)
    - Register only ONE change handler per component
    - Combine selection logic and refresh in a single handler to avoid double-firing

---

## Definition of Done

- [ ] All acceptance criteria met
- [ ] All 7 page categories ported
- [ ] Unit tests pass with >90% coverage
- [ ] Integration tests pass
- [ ] E2E tests pass
- [ ] Feature flag gating verified per page
- [ ] Permission checks verified per operation
- [ ] Auto-refresh working on all applicable pages
- [ ] Audit logging preserved
- [ ] Error states handled gracefully
- [ ] No regressions in P5T1-P5T6 tests
- [ ] Code reviewed and approved
- [ ] Merged to feature branch

---

**Last Updated:** 2025-12-31 (Rev 5)
**Status:** PLANNING
