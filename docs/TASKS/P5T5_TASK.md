---
id: P5T5
title: "NiceGUI Migration - Manual Trading Controls"
phase: P5
task: T5
priority: P0
owner: "@development-team"
state: PLANNING
created: 2025-12-31
dependencies: [P5T1, P5T2, P5T3, P5T4]
estimated_effort: "5-7 days"
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P5_PLANNING.md, P5T1_DONE.md, P5T2_DONE.md, P5T3_DONE.md, P5T4_TASK.md]
features: [T5.1, T5.2, T5.3]
---

# P5T5: NiceGUI Migration - Manual Trading Controls

**Phase:** P5 (Web Console Modernization)
**Status:** PLANNING
**Priority:** P0 (Critical Safety)
**Owner:** @development-team
**Created:** 2025-12-31
**Estimated Effort:** 5-7 days
**Track:** Phase 4 from P5_PLANNING.md
**Dependency:** P5T4 (Real-Time Dashboard) must be complete with Phase 3.5 security gate passed

---

## Objective

Port manual order entry, kill switch management, and position management controls to NiceGUI with safety confirmations. These are the most critical trading actions in the system.

**Success looks like:**
- Manual orders submitted with idempotent client_order_id
- Kill switch engage/disengage with two-factor confirmation
- Position close and flatten operations with safety dialogs
- All destructive actions require confirmation
- Real-time kill switch status check before any order submission
- Full audit logging for all trading actions
- Permission-based UI controls (viewer vs trader vs admin)

**Critical Safety Requirements:**
- NEVER submit orders without kill switch check
- Idempotent client_order_id prevents duplicate orders
- Two-factor confirmation for destructive actions
- Rate limiting on repeated actions
- Audit trail for all trading operations

---

## Acceptance Criteria

### T5.1 Manual Order Entry

**Deliverables:**
- [ ] Order form with symbol, qty, side, order type inputs
- [ ] Limit price input (visible only for limit orders)
- [ ] Reason field (required, min 10 characters)
- [ ] Preview dialog with order summary
- [ ] Kill switch check BEFORE showing preview dialog
- [ ] FRESH kill switch check at confirmation time
- [ ] Idempotent client_order_id generation
- [ ] Form validation with error messages
- [ ] Submit button disabled during API call
- [ ] Success/error notification
- [ ] Form reset after successful submission
- [ ] Audit logging of order submission
- [ ] Rate limiting (10 orders per minute per user)

**Idempotent client_order_id Pattern:**
```python
# DETERMINISTIC idempotent client_order_id generation
# MUST MATCH apps/execution_gateway/order_id_generator.py EXACTLY
#
# Format: {symbol}|{side}|{qty}|{limit_price}|{stop_price}|{order_type}|{time_in_force}|{strategy_id}|{date}
#
# For manual orders, strategy_id = f"manual_{reason_hash}" to:
# - Allow intentional repeat trades (different reason = different ID)
# - Same inputs + same reason = same ID (retry protection)
# - Maintain compatibility with existing system

from decimal import Decimal, ROUND_HALF_UP
from datetime import UTC, datetime, date

PRICE_PRECISION = Decimal("0.01")
ORDER_ID_MAX_LENGTH = 24


def _format_price_for_id(price: Decimal | None) -> str:
    """Format price to fixed precision for idempotency."""
    if price is None:
        return "None"
    return str(price.quantize(PRICE_PRECISION, rounding=ROUND_HALF_UP))


def generate_manual_order_id(
    symbol: str,
    side: str,
    qty: int,
    order_type: str,
    limit_price: Decimal | None,
    stop_price: Decimal | None,
    time_in_force: str,
    reason: str,
    as_of_date: date | None = None,
) -> str:
    """
    Generate deterministic client_order_id for manual orders.

    CRITICAL: Matches apps/execution_gateway/order_id_generator.py format.
    Uses strategy_id = f"manual_{reason_hash}" for manual-specific differentiation.
    """
    order_date = as_of_date or datetime.now(UTC).date()
    reason_hash = hashlib.sha256(reason.encode()).hexdigest()[:8]
    strategy_id = f"manual_{reason_hash}"

    # Format: {symbol}|{side}|{qty}|{limit_price}|{stop_price}|{order_type}|{time_in_force}|{strategy_id}|{date}
    raw = (
        f"{symbol}|"
        f"{side}|"
        f"{qty}|"
        f"{_format_price_for_id(limit_price)}|"
        f"{_format_price_for_id(stop_price)}|"
        f"{order_type}|"
        f"{time_in_force}|"
        f"{strategy_id}|"
        f"{order_date.isoformat()}"
    )

    return hashlib.sha256(raw.encode()).hexdigest()[:ORDER_ID_MAX_LENGTH]
```

**Implementation:**
```python
# apps/web_console_ng/pages/manual_order.py
from nicegui import ui, Client
from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console_ng.auth.middleware import requires_auth, has_permission
from apps.web_console_ng.core.audit import audit_log
from apps.web_console_ng.core.rate_limiter import RateLimiter
import hashlib
from datetime import date

# Rate limiter: 10 orders per minute per user
order_rate_limiter = RateLimiter(max_requests=10, window_seconds=60)


@ui.page("/manual")
@requires_auth
@main_layout
async def manual_order(client: Client) -> None:
    """Manual order entry page with safety confirmations."""
    trading_client = AsyncTradingClient.get()
    user_id = get_current_user_id()

    # Permission check - only traders and admins can submit orders
    if not has_permission("trade:submit_order"):
        ui.label("You don't have permission to submit orders").classes(
            "text-red-600 text-lg"
        )
        return

    # Track submit button state to prevent double-click
    submit_in_progress = False

    with ui.card().classes("w-full max-w-md mx-auto"):
        ui.label("Manual Order Entry").classes("text-xl font-bold mb-4")

        # Form inputs
        symbol = ui.input(
            "Symbol",
            placeholder="AAPL",
            validation={"Required": lambda v: bool(v)},
        ).classes("w-full")

        qty = ui.number(
            "Quantity",
            value=10,
            min=1,
            step=1,
            validation={"Must be positive": lambda v: v > 0},
        ).classes("w-full")

        side = ui.select(
            ["buy", "sell"],
            value="buy",
            label="Side",
        ).classes("w-full")

        order_type = ui.select(
            ["market", "limit"],
            value="market",
            label="Order Type",
        ).classes("w-full")

        time_in_force = ui.select(
            ["day", "gtc", "ioc", "fok"],
            value="day",
            label="Time In Force",
        ).classes("w-full")

        limit_price = ui.number(
            "Limit Price",
            value=0,
            min=0.01,
            step=0.01,
            validation={"Must be positive": lambda v: v > 0},
        ).classes("w-full")
        limit_price.bind_visibility_from(order_type, "value", value="limit")

        reason = ui.textarea(
            "Reason (required)",
            placeholder="Why this trade? (min 10 characters)",
            validation={
                "Min 10 characters": lambda v: len(v or "") >= 10
            },
        ).classes("w-full")

        ui.separator().classes("my-4")

        async def preview_order() -> None:
            nonlocal submit_in_progress
            if submit_in_progress:
                return

            # Rate limit check
            if not order_rate_limiter.allow(user_id):
                ui.notify("Rate limited: too many orders per minute", type="warning")
                return

            # Validate all fields
            if not symbol.value:
                ui.notify("Symbol is required", type="warning")
                return
            if qty.value < 1:
                ui.notify("Quantity must be positive", type="warning")
                return
            if order_type.value == "limit" and limit_price.value <= 0:
                ui.notify("Limit price must be positive", type="warning")
                return
            if len(reason.value or "") < 10:
                ui.notify("Reason must be at least 10 characters", type="warning")
                return

            # Generate idempotent client_order_id (matches execution gateway format)
            order_id = generate_manual_order_id(
                symbol=symbol.value.upper(),
                side=side.value,
                qty=int(qty.value),
                order_type=order_type.value,
                limit_price=Decimal(str(limit_price.value)) if order_type.value == "limit" else None,
                stop_price=None,  # Manual orders don't use stop prices currently
                time_in_force=time_in_force.value,
                reason=reason.value,
            )

            # Check kill switch BEFORE showing preview dialog
            try:
                ks_status = await trading_client.fetch_kill_switch_status()
                if ks_status.get("state") == "ENGAGED":
                    ui.notify("Cannot submit: Kill Switch is ENGAGED", type="negative")
                    return
            except Exception as e:
                ui.notify(f"Cannot verify kill switch: {e}", type="negative")
                return

            # Confirmation dialog
            with ui.dialog() as dialog, ui.card().classes("p-6 min-w-[400px]"):
                ui.label("Confirm Order").classes("text-xl font-bold mb-4")

                with ui.column().classes("gap-2"):
                    ui.label(f"Symbol: {symbol.value.upper()}").classes("font-mono")
                    ui.label(f"Side: {side.value.upper()}").classes(
                        "font-bold " + ("text-green-600" if side.value == "buy" else "text-red-600")
                    )
                    ui.label(f"Quantity: {int(qty.value):,}")
                    ui.label(f"Type: {order_type.value.upper()}")
                    if order_type.value == "limit":
                        ui.label(f"Limit Price: ${float(limit_price.value):,.2f}")
                    ui.label(f"Order ID: {order_id[:12]}...").classes("text-gray-500 text-sm")

                    ui.separator()
                    ui.label(f"Reason: {reason.value}").classes("text-gray-600 text-sm italic")

                ui.separator().classes("my-4")

                with ui.row().classes("gap-4 justify-end"):
                    confirm_btn = ui.button(
                        "Confirm Order",
                        on_click=lambda: confirm_order(dialog, order_id),
                    ).classes("bg-green-600 text-white")

                    ui.button("Cancel", on_click=dialog.close).classes("bg-gray-400")

            dialog.open()

        async def confirm_order(dialog, order_id: str) -> None:
            nonlocal submit_in_progress
            if submit_in_progress:
                return

            submit_in_progress = True

            try:
                # FRESH kill switch check at confirmation time (critical safety)
                ks_status = await trading_client.fetch_kill_switch_status()
                if ks_status.get("state") == "ENGAGED":
                    ui.notify("Order BLOCKED: Kill Switch engaged", type="negative")
                    dialog.close()
                    return

                # Build order request
                order_req = {
                    "symbol": symbol.value.upper(),
                    "qty": int(qty.value),
                    "side": side.value,
                    "type": order_type.value,
                    "time_in_force": time_in_force.value,
                    "client_order_id": order_id,
                    "reason": reason.value,
                }
                if order_type.value == "limit":
                    order_req["limit_price"] = float(limit_price.value)

                # Submit order
                result = await trading_client.submit_order(order_req, user_id)

                # Audit log
                await audit_log(
                    action="order_submitted",
                    user_id=user_id,
                    details={
                        "client_order_id": order_id,
                        "symbol": symbol.value.upper(),
                        "side": side.value,
                        "qty": int(qty.value),
                        "type": order_type.value,
                        "reason": reason.value,
                    },
                )

                ui.notify(
                    f"Order submitted: {result.get('client_order_id', order_id)[:12]}...",
                    type="positive",
                )
                dialog.close()

                # Reset form
                symbol.value = ""
                qty.value = 10
                order_type.value = "market"
                limit_price.value = 0
                reason.value = ""

            except Exception as e:
                ui.notify(f"Order failed: {e}", type="negative")
                # Audit log failure
                await audit_log(
                    action="order_failed",
                    user_id=user_id,
                    details={"error": str(e), "client_order_id": order_id},
                )
            finally:
                submit_in_progress = False

        ui.button(
            "Preview Order",
            on_click=preview_order,
        ).classes("bg-blue-600 text-white w-full")

# NOTE: generate_manual_order_id() is defined above and MUST be used.
# It follows the exact format from apps/execution_gateway/order_id_generator.py
```

**Testing:**
- [ ] Order form validation (symbol, qty, reason length)
- [ ] Limit price visibility toggle
- [ ] Kill switch blocks order (engaged state)
- [ ] client_order_id deterministic (same inputs = same ID)
- [ ] client_order_id unique per reason (different reason = different ID)
- [ ] Preview dialog shows correct values
- [ ] Confirm submits order to API
- [ ] Form resets after success
- [ ] Error notification on failure
- [ ] Audit log recorded
- [ ] Rate limiting blocks rapid submissions (10/min)

---

### T5.2 Kill Switch Management

**Deliverables:**
- [ ] Kill switch status display (large, prominent)
- [ ] Status indicator with color (ENGAGED=red, DISENGAGED=green)
- [ ] Engage button with single confirmation
- [ ] Disengage button with two-factor confirmation
- [ ] Reason input required for both actions
- [ ] Rate limiting (1 action per minute per user)
- [ ] Real-time status updates via Redis Pub/Sub
- [ ] Full audit trail display
- [ ] Permission check (admin only for disengage)

**Two-Factor Confirmation Pattern:**
```python
# For high-risk actions (kill switch disengage, flatten all):
# 1. First dialog: "Are you sure?"
# 2. Second dialog: "Type CONFIRM to proceed"
# Both must pass before action executes.
```

**Implementation:**
```python
# apps/web_console_ng/pages/kill_switch.py
from nicegui import ui, Client
from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.core.realtime import RealtimeUpdater, kill_switch_channel
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console_ng.auth.middleware import requires_auth, has_permission
from apps.web_console_ng.core.audit import audit_log
from apps.web_console_ng.core.rate_limiter import RateLimiter
import time

# Rate limiter: 1 action per minute per user
kill_switch_rate_limiter = RateLimiter(max_requests=1, window_seconds=60)


@ui.page("/kill-switch")
@requires_auth
@main_layout
async def kill_switch_page(client: Client) -> None:
    """Kill switch management page with safety confirmations."""
    trading_client = AsyncTradingClient.get()
    user_id = get_current_user_id()
    client_id = client.storage.get("client_id")

    # State tracking
    current_status: dict = {}
    action_in_progress = False

    # ===== Status Display =====
    with ui.card().classes("w-full max-w-2xl mx-auto p-6"):
        ui.label("Kill Switch Control").classes("text-2xl font-bold mb-6")

        # Status indicator (large and prominent)
        with ui.row().classes("items-center gap-4 mb-6"):
            status_icon = ui.icon("power_settings_new").classes("text-6xl")
            status_label = ui.label("Loading...").classes("text-4xl font-bold")

        # Last changed info
        last_changed_label = ui.label("").classes("text-gray-500 text-sm mb-6")

        ui.separator()

        # ===== Action Buttons =====
        with ui.row().classes("gap-4 mt-6"):
            engage_btn = ui.button(
                "ENGAGE Kill Switch",
                on_click=lambda: engage_kill_switch(),
            ).classes("bg-red-600 text-white text-lg px-6 py-3")

            disengage_btn = ui.button(
                "DISENGAGE Kill Switch",
                on_click=lambda: disengage_kill_switch(),
            ).classes("bg-green-600 text-white text-lg px-6 py-3")

        # Permission warning for non-admins
        if not has_permission("admin:kill_switch_disengage"):
            ui.label(
                "Note: Only admins can disengage the kill switch"
            ).classes("text-yellow-600 text-sm mt-4")

    # ===== Audit Trail =====
    with ui.card().classes("w-full max-w-2xl mx-auto mt-6 p-6"):
        ui.label("Audit Trail").classes("text-xl font-bold mb-4")
        audit_table = ui.table(
            columns=[
                {"name": "time", "label": "Time", "field": "time"},
                {"name": "action", "label": "Action", "field": "action"},
                {"name": "user", "label": "User", "field": "user"},
                {"name": "reason", "label": "Reason", "field": "reason"},
            ],
            rows=[],
        ).classes("w-full")

    # ===== Status Update Functions =====
    def update_status_display(status: dict) -> None:
        nonlocal current_status
        current_status = status

        state = status.get("state", "UNKNOWN")

        if state == "ENGAGED":
            status_icon.classes("text-red-600", remove="text-green-600 text-gray-400")
            status_label.set_text("ENGAGED")
            status_label.classes("text-red-600", remove="text-green-600 text-gray-400")
            engage_btn.disable()
            disengage_btn.enable()
        elif state == "DISENGAGED":
            status_icon.classes("text-green-600", remove="text-red-600 text-gray-400")
            status_label.set_text("DISENGAGED")
            status_label.classes("text-green-600", remove="text-red-600 text-gray-400")
            engage_btn.enable()
            disengage_btn.disable()
        else:
            status_icon.classes("text-gray-400", remove="text-green-600 text-red-600")
            status_label.set_text("UNKNOWN")
            status_label.classes("text-gray-400", remove="text-green-600 text-red-600")

        # Update last changed
        if "changed_at" in status:
            last_changed_label.set_text(
                f"Last changed: {status['changed_at']} by {status.get('changed_by', 'unknown')}"
            )

    async def load_initial_status() -> None:
        try:
            status = await trading_client.fetch_kill_switch_status()
            update_status_display(status)

            # Load audit trail
            audit_data = await trading_client.fetch_kill_switch_audit(limit=20)
            audit_table.rows = audit_data.get("events", [])
            audit_table.update()
        except Exception as e:
            ui.notify(f"Failed to load status: {e}", type="negative")

    await load_initial_status()

    # ===== Real-Time Updates =====
    realtime = RealtimeUpdater(client_id, client)

    async def on_kill_switch_update(data: dict) -> None:
        update_status_display(data)
        # Refresh audit trail
        try:
            audit_data = await trading_client.fetch_kill_switch_audit(limit=20)
            audit_table.rows = audit_data.get("events", [])
            audit_table.update()
        except Exception:
            pass

    await realtime.subscribe(kill_switch_channel(), on_kill_switch_update)

    # ===== Engage Action =====
    async def engage_kill_switch() -> None:
        nonlocal action_in_progress
        if action_in_progress:
            return

        # Rate limit check
        if not kill_switch_rate_limiter.allow(user_id):
            ui.notify("Rate limited: wait before next action", type="warning")
            return

        # Single confirmation dialog
        with ui.dialog() as dialog, ui.card().classes("p-6 min-w-[400px]"):
            ui.label("Engage Kill Switch?").classes("text-xl font-bold text-red-600 mb-4")
            ui.label(
                "This will BLOCK all new order submissions and alert the team."
            ).classes("mb-4")

            reason_input = ui.textarea(
                "Reason (required)",
                placeholder="Why are you engaging the kill switch?",
            ).classes("w-full mb-4")

            with ui.row().classes("gap-4 justify-end"):
                async def confirm_engage():
                    nonlocal action_in_progress
                    if len(reason_input.value or "") < 5:
                        ui.notify("Please provide a reason", type="warning")
                        return

                    action_in_progress = True
                    try:
                        # API: KillSwitchEngageRequest(operator, reason, details)
                        await trading_client.engage_kill_switch(
                            operator=user_id,
                            reason=reason_input.value,
                            details={},
                        )
                        await audit_log(
                            action="kill_switch_engaged",
                            user_id=user_id,
                            details={"reason": reason_input.value},
                        )
                        ui.notify("Kill Switch ENGAGED", type="warning")
                        dialog.close()
                    except Exception as e:
                        ui.notify(f"Failed: {e}", type="negative")
                    finally:
                        action_in_progress = False

                ui.button(
                    "ENGAGE",
                    on_click=confirm_engage,
                ).classes("bg-red-600 text-white")
                ui.button("Cancel", on_click=dialog.close)

        dialog.open()

    # ===== Disengage Action (Two-Factor) =====
    async def disengage_kill_switch() -> None:
        nonlocal action_in_progress
        if action_in_progress:
            return

        # Permission check
        if not has_permission("admin:kill_switch_disengage"):
            ui.notify("Only admins can disengage the kill switch", type="negative")
            return

        # Rate limit check
        if not kill_switch_rate_limiter.allow(user_id):
            ui.notify("Rate limited: wait before next action", type="warning")
            return

        # First confirmation dialog
        with ui.dialog() as dialog1, ui.card().classes("p-6 min-w-[400px]"):
            ui.label("Disengage Kill Switch?").classes("text-xl font-bold text-yellow-600 mb-4")
            ui.label(
                "WARNING: This will allow trading to resume. "
                "Ensure the issue that triggered engagement is resolved."
            ).classes("text-red-600 mb-4")

            reason_input = ui.textarea(
                "Reason (required)",
                placeholder="Why are you disengaging? What was resolved?",
            ).classes("w-full mb-4")

            with ui.row().classes("gap-4 justify-end"):
                async def proceed_to_confirm():
                    if len(reason_input.value or "") < 10:
                        ui.notify("Please provide detailed reason (min 10 chars)", type="warning")
                        return
                    dialog1.close()
                    await show_final_confirmation(reason_input.value)

                ui.button("Proceed", on_click=proceed_to_confirm).classes("bg-yellow-600 text-white")
                ui.button("Cancel", on_click=dialog1.close)

        dialog1.open()

    async def show_final_confirmation(reason: str) -> None:
        """Second confirmation: type CONFIRM to proceed."""
        nonlocal action_in_progress

        with ui.dialog() as dialog2, ui.card().classes("p-6 min-w-[400px]"):
            ui.label("Final Confirmation").classes("text-xl font-bold text-red-600 mb-4")
            ui.label(
                "Type CONFIRM below to disengage the kill switch:"
            ).classes("mb-4")

            confirm_input = ui.input(
                "Type CONFIRM",
                placeholder="CONFIRM",
            ).classes("w-full mb-4 font-mono")

            with ui.row().classes("gap-4 justify-end"):
                async def final_confirm():
                    nonlocal action_in_progress
                    if confirm_input.value != "CONFIRM":
                        ui.notify("Type CONFIRM exactly to proceed", type="warning")
                        return

                    action_in_progress = True
                    try:
                        # API: KillSwitchDisengageRequest(operator, notes)
                        await trading_client.disengage_kill_switch(
                            operator=user_id,
                            notes=reason,
                        )
                        await audit_log(
                            action="kill_switch_disengaged",
                            user_id=user_id,
                            details={"reason": reason},
                        )
                        ui.notify("Kill Switch DISENGAGED - Trading resumed", type="positive")
                        dialog2.close()
                    except Exception as e:
                        ui.notify(f"Failed: {e}", type="negative")
                    finally:
                        action_in_progress = False

                ui.button("DISENGAGE", on_click=final_confirm).classes("bg-green-600 text-white")
                ui.button("Cancel", on_click=dialog2.close)

        dialog2.open()
```

**Testing:**
- [ ] Status display shows correct state and color
- [ ] Engage button shows single confirmation
- [ ] Engage requires reason
- [ ] Engage API call succeeds
- [ ] Disengage requires admin permission
- [ ] Disengage shows two-factor confirmation
- [ ] Disengage requires typing CONFIRM
- [ ] Rate limiting prevents rapid actions
- [ ] Real-time updates reflect changes
- [ ] Audit trail displays recent events

---

### T5.3 Position Management

**Deliverables:**
- [ ] Close single position button (from dashboard grid)
- [ ] Flatten all positions button with two-factor confirmation
- [ ] Cancel all open orders button with confirmation
- [ ] Force position adjustment with reason
- [ ] Kill switch check before position close/flatten (order submissions)
- [ ] Cancel-all orders BYPASSES kill switch (risk-reducing action)
- [ ] Progress indicator for bulk operations
- [ ] Success/failure summary for bulk operations
- [ ] Rate limiting (5 close/flatten per minute per user)
- [ ] Audit logging for all actions

**Implementation:**
```python
# apps/web_console_ng/pages/position_management.py
from nicegui import ui, Client
from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console_ng.auth.middleware import requires_auth, has_permission
from apps.web_console_ng.core.audit import audit_log
from apps.web_console_ng.core.rate_limiter import RateLimiter
import asyncio

# Rate limiter: 5 close/flatten per minute per user
position_rate_limiter = RateLimiter(max_requests=5, window_seconds=60)


@ui.page("/positions")
@requires_auth
@main_layout
async def position_management(client: Client) -> None:
    """Position management page with bulk operations."""
    trading_client = AsyncTradingClient.get()
    user_id = get_current_user_id()

    # Permission check
    if not has_permission("trade:manage_positions"):
        ui.label("You don't have permission to manage positions").classes(
            "text-red-600 text-lg"
        )
        return

    action_in_progress = False

    # ===== Current Positions Summary =====
    with ui.card().classes("w-full max-w-4xl mx-auto p-6"):
        ui.label("Position Management").classes("text-2xl font-bold mb-4")

        # Summary row
        with ui.row().classes("gap-8 mb-6"):
            total_positions_label = ui.label("Positions: --")
            total_value_label = ui.label("Total Value: $--")
            unrealized_pnl_label = ui.label("Unrealized P&L: $--")

    # ===== Bulk Action Buttons =====
    with ui.card().classes("w-full max-w-4xl mx-auto mt-4 p-6"):
        ui.label("Bulk Actions").classes("text-xl font-bold mb-4")

        ui.label(
            "WARNING: These actions affect ALL positions/orders. Use with caution."
        ).classes("text-yellow-600 mb-4")

        with ui.row().classes("gap-4"):
            flatten_btn = ui.button(
                "Flatten All Positions",
                on_click=lambda: flatten_all_positions(),
            ).classes("bg-red-600 text-white")

            cancel_all_btn = ui.button(
                "Cancel All Orders",
                on_click=lambda: cancel_all_orders(),
            ).classes("bg-orange-600 text-white")

    # ===== Positions Table =====
    with ui.card().classes("w-full max-w-4xl mx-auto mt-4 p-6"):
        ui.label("Current Positions").classes("text-xl font-bold mb-4")

        positions_grid = ui.aggrid({
            "columnDefs": [
                {"field": "symbol", "headerName": "Symbol", "sortable": True},
                {"field": "qty", "headerName": "Qty", "sortable": True},
                {
                    "field": "avg_entry_price",
                    "headerName": "Avg Entry",
                    "valueFormatter": "x => '$' + x.value.toFixed(2)",
                },
                {
                    "field": "current_price",
                    "headerName": "Current",
                    "valueFormatter": "x => '$' + x.value.toFixed(2)",
                },
                {
                    "field": "unrealized_pl",
                    "headerName": "P&L",
                    "cellStyle": {
                        "function": "params.value >= 0 ? {color: '#16a34a'} : {color: '#dc2626'}"
                    },
                    "valueFormatter": "x => '$' + x.value.toFixed(2)",
                },
                {
                    "field": "actions",
                    "headerName": "Actions",
                    "width": 120,
                    "cellRenderer": "actionRenderer",
                },
            ],
            "rowData": [],
            "domLayout": "autoHeight",
            "getRowId": "data => data.symbol",
        }).classes("w-full")

    # ===== Load Data =====
    async def load_positions() -> None:
        try:
            data = await trading_client.fetch_positions(user_id)
            positions = data.get("positions", [])

            # Update summary
            total_positions_label.set_text(f"Positions: {len(positions)}")
            total_value = sum(p.get("market_value", 0) for p in positions)
            total_value_label.set_text(f"Total Value: ${total_value:,.2f}")
            unrealized = sum(p.get("unrealized_pl", 0) for p in positions)
            unrealized_pnl_label.set_text(f"Unrealized P&L: ${unrealized:,.2f}")

            # Update grid
            positions_grid.options["rowData"] = positions
            positions_grid.update()

        except Exception as e:
            ui.notify(f"Failed to load positions: {e}", type="negative")

    await load_positions()

    # Refresh timer
    ui.timer(10.0, load_positions)

    # ===== Close Single Position =====
    async def close_position(symbol: str, qty: int) -> None:
        nonlocal action_in_progress
        if action_in_progress:
            return

        # Rate limit check
        if not position_rate_limiter.allow(user_id):
            ui.notify("Rate limited: too many position actions per minute", type="warning")
            return

        # Pre-dialog kill switch check
        try:
            ks = await trading_client.fetch_kill_switch_status()
            if ks.get("state") == "ENGAGED":
                ui.notify("Cannot close: Kill Switch engaged", type="negative")
                return
        except Exception as e:
            ui.notify(f"Cannot verify kill switch: {e}", type="negative")
            return

        with ui.dialog() as dialog, ui.card().classes("p-6"):
            ui.label(f"Close {symbol} Position?").classes("text-xl font-bold mb-4")
            ui.label(f"Quantity: {qty:,} shares")

            reason_input = ui.textarea(
                "Reason (required)",
                placeholder="Why closing this position? (min 10 chars)",
                validation={"Min 10 characters": lambda v: len(v or "") >= 10},
            ).classes("w-full my-4")

            with ui.row().classes("gap-4 justify-end"):
                async def confirm_close():
                    nonlocal action_in_progress

                    # Validate reason
                    if len(reason_input.value or "") < 10:
                        ui.notify("Reason required (min 10 characters)", type="warning")
                        return

                    action_in_progress = True
                    try:
                        # FRESH kill switch check before submit (TOCTOU prevention)
                        ks = await trading_client.fetch_kill_switch_status()
                        if ks.get("state") == "ENGAGED":
                            ui.notify("Order BLOCKED: Kill Switch engaged", type="negative")
                            dialog.close()
                            return

                        # Generate idempotent order ID with reason hash
                        order_id = generate_close_order_id(symbol, qty, reason_input.value)

                        await trading_client.submit_order({
                            "symbol": symbol,
                            "qty": abs(qty),
                            "side": "sell" if qty > 0 else "buy",
                            "type": "market",
                            "time_in_force": "day",
                            "client_order_id": order_id,
                            "reason": f"Position close: {reason_input.value}",
                        }, user_id)

                        await audit_log(
                            action="position_closed",
                            user_id=user_id,
                            details={
                                "symbol": symbol,
                                "qty": qty,
                                "reason": reason_input.value,
                            },
                        )
                        ui.notify(f"Closing {symbol}", type="positive")
                        dialog.close()
                        await load_positions()
                    except Exception as e:
                        ui.notify(f"Failed: {e}", type="negative")
                        # Audit log failure
                        await audit_log(
                            action="position_close_failed",
                            user_id=user_id,
                            details={
                                "symbol": symbol,
                                "qty": qty,
                                "error": str(e),
                            },
                        )
                    finally:
                        action_in_progress = False

                ui.button("Close Position", on_click=confirm_close).classes("bg-red-600 text-white")
                ui.button("Cancel", on_click=dialog.close)

        dialog.open()

    # ===== Flatten All Positions (Two-Factor) =====
    async def flatten_all_positions() -> None:
        nonlocal action_in_progress
        if action_in_progress:
            return

        # Permission check - ADMIN ONLY for flatten all
        if not has_permission("admin:flatten_all"):
            ui.notify("Admin permission required for flatten all", type="negative")
            return

        # Rate limit check
        if not position_rate_limiter.allow(user_id):
            ui.notify("Rate limited: too many position actions per minute", type="warning")
            return

        # Kill switch check
        try:
            ks = await trading_client.fetch_kill_switch_status()
            if ks.get("state") == "ENGAGED":
                ui.notify("Cannot flatten: Kill Switch engaged", type="negative")
                return
        except Exception:
            ui.notify("Cannot verify kill switch", type="negative")
            return

        # First confirmation
        with ui.dialog() as dialog1, ui.card().classes("p-6 min-w-[450px]"):
            ui.label("Flatten ALL Positions?").classes("text-xl font-bold text-red-600 mb-4")
            ui.label(
                "This will submit MARKET orders to close ALL positions. "
                "This action CANNOT be undone."
            ).classes("text-red-600 mb-4")

            positions_data = await trading_client.fetch_positions(user_id)
            position_count = len(positions_data.get("positions", []))
            ui.label(f"Positions to close: {position_count}").classes("font-bold mb-4")

            reason_input = ui.textarea(
                "Reason (required)",
                placeholder="Why flattening all positions?",
            ).classes("w-full mb-4")

            with ui.row().classes("gap-4 justify-end"):
                async def proceed():
                    if len(reason_input.value or "") < 10:
                        ui.notify("Provide detailed reason (min 10 chars)", type="warning")
                        return
                    dialog1.close()
                    await show_flatten_confirmation(reason_input.value, positions_data)

                ui.button("Proceed", on_click=proceed).classes("bg-yellow-600 text-white")
                ui.button("Cancel", on_click=dialog1.close)

        dialog1.open()

    async def show_flatten_confirmation(reason: str, positions_data: dict) -> None:
        """Second confirmation for flatten all."""
        nonlocal action_in_progress

        with ui.dialog() as dialog2, ui.card().classes("p-6 min-w-[450px]"):
            ui.label("FINAL CONFIRMATION").classes("text-xl font-bold text-red-600 mb-4")
            ui.label("Type FLATTEN to confirm:").classes("mb-4")

            confirm_input = ui.input("Type FLATTEN").classes("w-full mb-4 font-mono")

            progress_label = ui.label("").classes("text-sm text-gray-500")

            with ui.row().classes("gap-4 justify-end"):
                async def execute_flatten():
                    nonlocal action_in_progress
                    if confirm_input.value != "FLATTEN":
                        ui.notify("Type FLATTEN exactly", type="warning")
                        return

                    action_in_progress = True
                    success_count = 0
                    fail_count = 0

                    try:
                        # FRESH kill switch check before execution
                        progress_label.set_text("Verifying kill switch...")
                        ks = await trading_client.fetch_kill_switch_status()
                        if ks.get("state") == "ENGAGED":
                            ui.notify("BLOCKED: Kill Switch engaged", type="negative")
                            dialog2.close()
                            return

                        # RE-FETCH positions at execution time (prevents stale data)
                        progress_label.set_text("Fetching current positions...")
                        fresh_positions_data = await trading_client.fetch_positions(user_id)
                        positions = fresh_positions_data.get("positions", [])

                        if len(positions) == 0:
                            ui.notify("No positions to flatten", type="info")
                            dialog2.close()
                            return

                        for i, pos in enumerate(positions):
                            progress_label.set_text(
                                f"Processing {i+1}/{len(positions)}: {pos['symbol']}"
                            )

                            try:
                                # Kill switch check per batch (every 5 orders)
                                if i > 0 and i % 5 == 0:
                                    ks = await trading_client.fetch_kill_switch_status()
                                    if ks.get("state") == "ENGAGED":
                                        ui.notify("STOPPED: Kill Switch engaged mid-flatten", type="negative")
                                        break

                                order_id = generate_close_order_id(
                                    pos["symbol"], pos["qty"], f"flatten:{reason}"
                                )
                                await trading_client.submit_order({
                                    "symbol": pos["symbol"],
                                    "qty": abs(pos["qty"]),
                                    "side": "sell" if pos["qty"] > 0 else "buy",
                                    "type": "market",
                                    "time_in_force": "day",
                                    "client_order_id": order_id,
                                    "reason": f"Flatten all: {reason}",
                                }, user_id)
                                success_count += 1
                            except Exception as e:
                                fail_count += 1
                                # Log individual failures
                                await audit_log(
                                    action="flatten_order_failed",
                                    user_id=user_id,
                                    details={
                                        "symbol": pos["symbol"],
                                        "qty": pos["qty"],
                                        "error": str(e),
                                    },
                                )

                            # Small delay to avoid rate limiting
                            await asyncio.sleep(0.1)

                        await audit_log(
                            action="flatten_all",
                            user_id=user_id,
                            details={
                                "reason": reason,
                                "positions": len(positions),
                                "success": success_count,
                                "failed": fail_count,
                            },
                        )

                        ui.notify(
                            f"Flatten complete: {success_count} success, {fail_count} failed",
                            type="positive" if fail_count == 0 else "warning",
                        )
                        dialog2.close()
                        await load_positions()

                    except Exception as e:
                        ui.notify(f"Flatten failed: {e}", type="negative")
                        await audit_log(
                            action="flatten_all_failed",
                            user_id=user_id,
                            details={"reason": reason, "error": str(e)},
                        )
                    finally:
                        action_in_progress = False

                ui.button("FLATTEN ALL", on_click=execute_flatten).classes("bg-red-600 text-white")
                ui.button("Cancel", on_click=dialog2.close)

        dialog2.open()

    # ===== Cancel All Orders =====
    # NOTE: Cancel-all BYPASSES kill switch - it's a RISK-REDUCING action
    # that should always be allowed (cancels pending orders, doesn't submit new ones)
    async def cancel_all_orders() -> None:
        nonlocal action_in_progress
        if action_in_progress:
            return

        # No kill switch check - cancel-all is risk-reducing
        with ui.dialog() as dialog, ui.card().classes("p-6 min-w-[400px]"):
            ui.label("Cancel ALL Open Orders?").classes("text-xl font-bold text-orange-600 mb-4")

            # Fetch order count
            orders_data = await trading_client.fetch_open_orders(user_id)
            order_count = len(orders_data.get("orders", []))
            ui.label(f"Open orders to cancel: {order_count}").classes("font-bold mb-4")

            if order_count == 0:
                ui.label("No open orders to cancel").classes("text-gray-500")
                ui.button("Close", on_click=dialog.close)
            else:
                reason_input = ui.textarea(
                    "Reason (required)",
                    placeholder="Why cancelling all orders? (min 10 chars)",
                    validation={"Min 10 characters": lambda v: len(v or "") >= 10},
                ).classes("w-full mb-4")

                with ui.row().classes("gap-4 justify-end"):
                    async def confirm_cancel():
                        nonlocal action_in_progress

                        # Validate reason
                        if len(reason_input.value or "") < 10:
                            ui.notify("Reason required (min 10 characters)", type="warning")
                            return

                        action_in_progress = True
                        try:
                            result = await trading_client.cancel_all_orders(user_id)
                            await audit_log(
                                action="cancel_all_orders",
                                user_id=user_id,
                                details={
                                    "reason": reason_input.value,
                                    "orders_cancelled": result.get("cancelled", 0),
                                },
                            )
                            ui.notify(
                                f"Cancelled {result.get('cancelled', 0)} orders",
                                type="positive",
                            )
                            dialog.close()
                        except Exception as e:
                            ui.notify(f"Failed: {e}", type="negative")
                            # Audit log failure
                            await audit_log(
                                action="cancel_all_orders_failed",
                                user_id=user_id,
                                details={
                                    "reason": reason_input.value,
                                    "error": str(e),
                                },
                            )
                        finally:
                            action_in_progress = False

                    ui.button("Cancel All", on_click=confirm_cancel).classes("bg-orange-600 text-white")
                    ui.button("Keep Orders", on_click=dialog.close)

        dialog.open()


def generate_close_order_id(symbol: str, qty: int, reason: str) -> str:
    """
    Generate idempotent order ID for position close.

    Includes reason_hash to allow intentional repeat closes on same day
    while preventing accidental duplicate orders from retries.
    """
    import hashlib
    from datetime import UTC, datetime

    order_date = datetime.now(UTC).date()
    reason_hash = hashlib.sha256(reason.encode()).hexdigest()[:8]
    strategy_id = f"close_{reason_hash}"

    # Match execution gateway format with pipe separators
    raw = (
        f"{symbol}|"
        f"{'sell' if qty > 0 else 'buy'}|"
        f"{abs(qty)}|"
        f"None|"  # limit_price
        f"None|"  # stop_price
        f"market|"
        f"day|"
        f"{strategy_id}|"
        f"{order_date.isoformat()}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:24]
```

**Testing:**
- [ ] Close position shows confirmation dialog
- [ ] Close position submits correct order (buy to close short, sell to close long)
- [ ] Flatten all requires two-factor confirmation
- [ ] Flatten all processes all positions
- [ ] Flatten all shows progress
- [ ] Cancel all orders shows order count
- [ ] Cancel all orders cancels correctly
- [ ] Kill switch blocks close/flatten when engaged
- [ ] Cancel-all BYPASSES kill switch (allowed even when engaged)
- [ ] Rate limiting blocks rapid position actions (5/min)
- [ ] Audit logs recorded for all actions

---

## Prerequisites Checklist

**Must verify before starting implementation:**

- [ ] **P5T4 complete:** Real-Time Dashboard with security gate passed
- [ ] **Kill switch API available:**
  - [ ] `GET /api/v1/kill_switch_status` - Current state
  - [ ] `POST /api/v1/kill_switch/engage` - Engage with reason
  - [ ] `POST /api/v1/kill_switch/disengage` - Disengage with reason
  - [ ] `GET /api/v1/kill_switch/audit` - Audit trail
- [ ] **Order API available:**
  - [ ] `POST /api/v1/orders` - Submit order
  - [ ] `DELETE /api/v1/orders/{id}` - Cancel order
  - [ ] `POST /api/v1/orders/cancel_all` - Cancel all orders
- [ ] **Position API available:**
  - [ ] `GET /api/v1/positions` - Current positions
- [ ] **Audit logging infrastructure ready**
- [ ] **Rate limiting infrastructure ready**

---

## Approach

### High-Level Plan

1. **C0: Manual Order Entry** (3-4 days)
   - Order form with validation
   - Preview dialog
   - Kill switch checks
   - Idempotent submission

2. **C1: Kill Switch Management** (2-3 days)
   - Status display with real-time updates
   - Engage with single confirmation
   - Disengage with two-factor confirmation
   - Audit trail display

3. **C2: Position Management** (2 days)
   - Close single position
   - Flatten all with two-factor
   - Cancel all orders
   - Progress indicators

---

## Component Breakdown

### C0: Manual Order Entry

**Files to Create:**
```
apps/web_console_ng/pages/
├── manual_order.py             # Order entry page
apps/web_console_ng/components/
├── order_form.py               # Reusable order form
├── order_confirmation.py       # Confirmation dialog
apps/web_console_ng/core/
├── order_utils.py              # Order ID generation
tests/apps/web_console_ng/
└── test_manual_order.py
```

---

### C1: Kill Switch Management

**Files to Create:**
```
apps/web_console_ng/pages/
├── kill_switch.py              # Kill switch page
apps/web_console_ng/components/
├── kill_switch_panel.py        # Status panel component
├── two_factor_dialog.py        # Reusable two-factor confirmation
apps/web_console_ng/core/
├── rate_limiter.py             # Rate limiting utility
tests/apps/web_console_ng/
└── test_kill_switch.py
```

---

### C2: Position Management

**Files to Create:**
```
apps/web_console_ng/pages/
├── position_management.py      # Position management page
apps/web_console_ng/components/
├── position_actions.py         # Action buttons component
├── bulk_operation_progress.py  # Progress indicator
tests/apps/web_console_ng/
└── test_position_management.py
```

---

## Testing Strategy

### Unit Tests (CI - Automated)
- `test_manual_order.py`: Form validation, order ID generation, kill switch check
- `test_kill_switch.py`: Status display, engage/disengage flow, rate limiting
- `test_position_management.py`: Close position, flatten all, cancel all
- `test_order_utils.py`: Idempotent order ID generation
- `test_rate_limiter.py`: Rate limiting behavior

### Integration Tests (CI - Docker)
- `test_order_submission_integration.py`: Full order flow with mocked backend
- `test_kill_switch_integration.py`: Kill switch state changes

### E2E Tests (CI - Playwright)
- `test_manual_order_e2e.py`: Full order entry flow
- `test_kill_switch_e2e.py`: Engage/disengage with confirmations
- `test_flatten_all_e2e.py`: Two-factor flatten confirmation

---

## Dependencies

### External
- `nicegui>=2.0`: UI framework
- `httpx>=0.25`: Async HTTP client

### Internal
- `apps/web_console_ng/core/client.py`: Async trading client (P5T1)
- `apps/web_console_ng/auth/`: Auth middleware (P5T2)
- `apps/web_console_ng/core/realtime.py`: Real-time updates (P5T4)
- `apps/web_console_ng/core/audit.py`: Audit logging

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Kill switch check race condition | Medium | Critical | Double-check: before preview AND at confirm |
| Duplicate order submission | Low | High | Deterministic client_order_id, same pattern as existing |
| Rate limit bypass | Low | Medium | Server-side rate limiting in addition to client |
| Two-factor dialog bypass | Low | High | Server validates both steps, not just client |
| Bulk operation timeout | Medium | Medium | Progress indicator, async processing |

---

## Implementation Notes

**Address during development:**

1. **Kill Switch Double-Check Pattern:**
   - Check BEFORE showing preview dialog
   - Check AGAIN at confirmation time
   - This prevents TOCTOU (time-of-check-time-of-use) race conditions

2. **Idempotent Order ID:** ✅ ADDRESSED IN DOCUMENT (Rev 2)
   - MUST match `apps/execution_gateway/order_id_generator.py` format
   - Uses pipe (`|`) separators: `{symbol}|{side}|{qty}|{limit_price}|{stop_price}|{order_type}|{time_in_force}|{strategy_id}|{date}`
   - For manual orders: `strategy_id = f"manual_{reason_hash}"`
   - For position closes: `strategy_id = f"close_{reason_hash}"`
   - NO nonces or random components - deterministic hash

3. **Two-Factor Confirmation:**
   - Used for: kill switch disengage, flatten all positions
   - First dialog: "Are you sure?" + reason
   - Second dialog: "Type CONFIRM/FLATTEN to proceed"
   - Both must pass before action executes

4. **Rate Limiting:**
   - Kill switch actions: 1 per minute per user
   - Order submissions: 10 per minute per user
   - Position closes: 5 per minute per user
   - Implemented both client and server side

5. **Audit Logging:**
   - ALL trading actions must be logged
   - Include: user_id, action, timestamp, details
   - Store in database for compliance

6. **Permission Checks:**
   - `trade:submit_order` - Required for order entry
   - `trade:manage_positions` - Required for close/cancel
   - `admin:flatten_all` - Required for flatten all (ADMIN ONLY)
   - `admin:kill_switch_disengage` - Required for disengaging kill switch

7. **Error Handling:**
   - Never leave user without feedback
   - Show specific error messages
   - Log errors for debugging
   - Allow retry after failure

8. **Kill Switch API Schema:** ✅ ADDRESSED IN DOCUMENT (Rev 2)
   - Engage: `KillSwitchEngageRequest(operator, reason, details)`
   - Disengage: `KillSwitchDisengageRequest(operator, notes)`
   - Maps `user_id` → `operator`, `reason` → `notes` for disengage

9. **Time-In-Force Field:** ✅ ADDRESSED IN DOCUMENT (Rev 2)
   - Added to manual order form (day, gtc, ioc, fok)
   - Included in order ID generation
   - Included in order request

10. **Position Close TOCTOU Prevention:** ✅ ADDRESSED IN DOCUMENT (Rev 2)
    - Pre-dialog kill switch check
    - FRESH kill switch check before submit_order()
    - Reason required (min 10 chars) for all close operations

11. **Flatten All Improvements:** ✅ ADDRESSED IN DOCUMENT (Rev 2)
    - ADMIN permission required (`admin:flatten_all`)
    - Re-fetch positions at execution time (prevents stale data)
    - Kill switch check before execution
    - Kill switch check every 5 orders during batch
    - Individual failure audit logging

12. **Failure Audit Logging:** ✅ ADDRESSED IN DOCUMENT (Rev 2)
    - All actions log both success and failure
    - Includes error details in failure logs
    - Actions: position_close_failed, flatten_order_failed, flatten_all_failed, cancel_all_orders_failed

13. **Rate Limiting Implementation:** ✅ ADDRESSED IN DOCUMENT (Rev 3)
    - Manual orders: 10/min per user (order_rate_limiter)
    - Position close/flatten: 5/min per user (position_rate_limiter)
    - Kill switch actions: 1/min per user (already in Rev 2)
    - Added rate limit checks in T5.1 and T5.3 code snippets
    - Added rate limiting to acceptance criteria and tests

14. **Cancel-All Kill Switch Policy:** ✅ ADDRESSED IN DOCUMENT (Rev 3)
    - Cancel-all orders BYPASSES kill switch
    - Rationale: Risk-reducing action (cancels pending, doesn't submit new)
    - Clearly documented in deliverables, code, and tests

15. **Removed Conflicting Order ID Helper:** ✅ ADDRESSED IN DOCUMENT (Rev 3)
    - Removed old `generate_order_id()` at end of T5.1 snippet
    - Only `generate_manual_order_id()` (matching execution gateway format) remains
    - Added comment referencing the correct helper

---

## Definition of Done

- [ ] All acceptance criteria met
- [ ] Unit tests pass with >90% coverage
- [ ] Integration tests pass
- [ ] E2E tests pass (Playwright)
- [ ] Kill switch double-check verified
- [ ] Idempotent order ID matches existing pattern
- [ ] Two-factor confirmation working for destructive actions
- [ ] Rate limiting functional
- [ ] Audit logging complete
- [ ] Permission checks enforced
- [ ] No regressions in P5T1-P5T4 tests
- [ ] Code reviewed and approved
- [ ] Merged to feature branch

---

**Last Updated:** 2025-12-31 (Rev 3)
**Status:** PLANNING
