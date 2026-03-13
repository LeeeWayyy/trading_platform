"""Alert Configuration page for NiceGUI web console (P5T7 + P6T17.3).

Provides alert rule management, alert history, and notification channels display.

Features:
    - Tab layout (Alert Rules, Alert History, Channels)
    - Alert rules list with expandable JSON details
    - Rule create/edit/delete (with permissions)
    - Inline rule editing with update form
    - Alert history table with filtering + pagination
    - Bulk acknowledge for pending alerts
    - Notification channels display with PII masking
    - PagerDuty channel support

PARITY: Mirrors the legacy web_console alerts functionality
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

import psycopg
from nicegui import app, ui

from apps.web_console_ng import config
from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.core.database import get_db_pool
from apps.web_console_ng.ui.layout import main_layout
from libs.platform.alerts.models import ChannelConfig, ChannelType
from libs.platform.alerts.pii import mask_recipient
from libs.platform.web_console_auth.permissions import Permission, has_permission

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from libs.platform.alerts.models import AlertEvent, AlertRule
    from libs.web_console_services.alert_service import AlertConfigService

logger = logging.getLogger(__name__)

# Condition types for alert rules
# Condition types supported by the alerting system
CONDITION_TYPES = [
    "drawdown",
    "position_limit",
    "latency",
]

# Comparison operators
COMPARISONS = [">=", "<=", ">", "<", "=="]

# Minimum acknowledgment note length
MIN_ACK_NOTE_LENGTH = 15


def _get_alert_service(db_pool: AsyncConnectionPool) -> AlertConfigService:
    """Get AlertConfigService with async pool (global cache)."""
    if not hasattr(app.storage, "_alert_service"):
        from libs.platform.web_console_auth.audit_log import AuditLogger
        from libs.web_console_services.alert_service import AlertConfigService

        audit_logger = AuditLogger(db_pool)
        app.storage._alert_service = AlertConfigService(db_pool, audit_logger)  # type: ignore[attr-defined]  # noqa: B010

    service: AlertConfigService = getattr(app.storage, "_alert_service")  # noqa: B009
    return service


@ui.page("/alerts")
@requires_auth
@main_layout
async def alerts_page() -> None:
    """Alert Configuration page."""
    user = get_current_user()

    # Feature flag check
    if not config.FEATURE_ALERTS:
        ui.label("Alert Configuration feature is disabled.").classes("text-lg")
        ui.label("Set FEATURE_ALERTS=true to enable.").classes("text-gray-500")
        return

    # Permission check
    if not has_permission(user, Permission.VIEW_ALERTS):
        ui.label("Permission denied: VIEW_ALERTS required").classes("text-red-500 text-lg")
        return

    # Get async db pool
    async_pool = get_db_pool()
    if async_pool is None:
        ui.label("Database not configured. Contact administrator.").classes("text-red-500")
        return

    alert_service = _get_alert_service(async_pool)

    # Page title
    ui.label("Alert Configuration").classes("text-2xl font-bold mb-4")

    # Tabs
    with ui.tabs().classes("w-full") as tabs:
        tab_rules = ui.tab("Alert Rules")
        tab_history = ui.tab("Alert History")
        tab_channels = ui.tab("Channels")

    with ui.tab_panels(tabs, value=tab_rules).classes("w-full"):
        with ui.tab_panel(tab_rules):
            await _render_alert_rules(user, alert_service)

        with ui.tab_panel(tab_history):
            await _render_alert_history(user, alert_service)

        with ui.tab_panel(tab_channels):
            await _render_channels(user, alert_service)


async def _render_alert_rules(user: dict[str, Any], alert_service: AlertConfigService) -> None:
    """Render alert rules section."""
    rules_data: list[AlertRule] = []

    async def fetch_rules() -> None:
        nonlocal rules_data
        try:
            rules_data = await alert_service.get_rules()
        except psycopg.OperationalError as e:
            logger.warning(
                "rules_fetch_db_error",
                extra={"error": str(e), "operation": "fetch_rules"},
            )
            rules_data = []
        except ValueError as e:
            logger.warning(
                "rules_fetch_validation_error",
                extra={"error": str(e), "operation": "fetch_rules"},
            )
            rules_data = []

    await fetch_rules()

    can_delete = has_permission(user, Permission.DELETE_ALERT_RULE)
    can_update = has_permission(user, Permission.UPDATE_ALERT_RULE)
    can_create = has_permission(user, Permission.CREATE_ALERT_RULE)

    @ui.refreshable
    def rules_list() -> None:
        ui.label("Existing Rules").classes("text-xl font-bold mb-2")

        if not rules_data:
            ui.label("No alert rules configured.").classes("text-gray-500")
            return

        for rule in rules_data:
            with ui.expansion(f"{rule.name} ({rule.condition_type})").classes("w-full"):
                # Rule details
                ui.json_editor(
                    {
                        "content": {
                            "json": {
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
                        }
                    },
                    on_change=lambda e: None,
                ).classes("w-full")

                with ui.row().classes("gap-2 mt-2"):
                    if can_delete:

                        async def delete_rule(rule_id: str = str(rule.id)) -> None:
                            try:
                                await alert_service.delete_rule(rule_id, user)
                                ui.notify("Rule deleted", type="positive")
                                await fetch_rules()
                                rules_list.refresh()
                            except PermissionError as e:
                                logger.exception(
                                    "rule_delete_permission_denied",
                                    extra={
                                        "rule_id": rule_id,
                                        "error": str(e),
                                        "operation": "delete_rule",
                                    },
                                )
                                ui.notify(f"Permission denied: {e}", type="negative")
                            except psycopg.OperationalError as e:
                                logger.exception(
                                    "rule_delete_db_error",
                                    extra={
                                        "rule_id": rule_id,
                                        "error": str(e),
                                        "operation": "delete_rule",
                                    },
                                )
                                ui.notify("Database error. Please try again.", type="negative")
                            except ValueError as e:
                                logger.exception(
                                    "rule_delete_validation_error",
                                    extra={
                                        "rule_id": rule_id,
                                        "error": str(e),
                                        "operation": "delete_rule",
                                    },
                                )
                                ui.notify(f"Invalid rule ID: {e}", type="negative")

                        ui.button("Delete", on_click=delete_rule, color="red").props("flat")

                    if can_update:

                        async def edit_rule(
                            rule_to_edit: AlertRule = rule,
                        ) -> None:
                            await _show_edit_dialog(
                                alert_service, rule_to_edit, user, fetch_rules, rules_list
                            )

                        ui.button("Edit", on_click=edit_rule, color="primary").props("flat")

    rules_list()

    ui.separator().classes("my-4")

    # Create rule form
    if can_create:
        ui.label("Create New Rule").classes("text-xl font-bold mb-2")

        with ui.card().classes("w-full p-4"):
            name_input = ui.input(
                label="Rule Name", placeholder="e.g., High Drawdown Alert"
            ).classes("w-full")

            with ui.row().classes("gap-4"):
                condition_select = ui.select(
                    label="Condition Type",
                    options=CONDITION_TYPES,
                    value=CONDITION_TYPES[0],
                ).classes("w-48")

                comparison_select = ui.select(
                    label="Comparison",
                    options=COMPARISONS,
                    value=">=",
                ).classes("w-24")

                threshold_input = ui.number(
                    label="Threshold Value",
                    value=0.05,
                    format="%.4f",
                ).classes("w-32")

            enabled_checkbox = ui.checkbox("Enabled", value=True)

            ui.label("Notification Channels").classes("font-medium mt-4")

            # Simple channel configuration
            with ui.row().classes("gap-4"):
                email_input = ui.input(
                    label="Email (optional)", placeholder="alert@company.com"
                ).classes("w-64")
                slack_input = ui.input(
                    label="Slack Webhook (optional)", placeholder="https://hooks.slack.com/..."
                ).classes("w-64")
                pagerduty_input = ui.input(
                    label="PagerDuty Routing Key (optional)",
                    placeholder="routing-key...",
                ).classes("w-64")

            async def create_rule() -> None:
                from libs.web_console_services.alert_service import AlertRuleCreate

                name = name_input.value
                if not name or len(name.strip()) < 3:
                    ui.notify("Rule name must be at least 3 characters", type="negative")
                    return

                channels = []
                if email_input.value and email_input.value.strip():
                    channels.append(
                        ChannelConfig(
                            type=ChannelType.EMAIL,
                            recipient=email_input.value.strip(),
                            enabled=True,
                        )
                    )
                if slack_input.value and slack_input.value.strip():
                    channels.append(
                        ChannelConfig(
                            type=ChannelType.SLACK,
                            recipient=slack_input.value.strip(),
                            enabled=True,
                        )
                    )
                if pagerduty_input.value and pagerduty_input.value.strip():
                    channels.append(
                        ChannelConfig(
                            type=ChannelType.PAGERDUTY,
                            recipient=pagerduty_input.value.strip(),
                            enabled=True,
                        )
                    )

                if not channels:
                    ui.notify("At least one notification channel is required", type="negative")
                    return

                rule_create = AlertRuleCreate(
                    name=name.strip(),
                    condition_type=condition_select.value,
                    threshold_value=Decimal(str(threshold_input.value)),
                    comparison=comparison_select.value,
                    channels=channels,
                    enabled=enabled_checkbox.value,
                )

                try:
                    await alert_service.create_rule(rule_create, user)
                    ui.notify("Rule created successfully", type="positive")
                    # Clear form
                    name_input.value = ""
                    email_input.value = ""
                    slack_input.value = ""
                    pagerduty_input.value = ""
                    await fetch_rules()
                    rules_list.refresh()
                except PermissionError as e:
                    ui.notify(f"Permission denied: {e}", type="negative")
                except psycopg.OperationalError as e:
                    logger.exception(
                        "rule_create_db_error",
                        extra={"error": str(e), "operation": "create_rule"},
                    )
                    ui.notify("Database error. Please try again.", type="negative")
                except ValueError as e:
                    logger.exception(
                        "rule_create_validation_error",
                        extra={"error": str(e), "operation": "create_rule"},
                    )
                    ui.notify(f"Invalid input: {e}", type="negative")

            ui.button("Create Rule", on_click=create_rule, color="primary").classes("mt-4")
    else:
        ui.label("You do not have permission to create rules.").classes("text-gray-500")


async def _render_alert_history(user: dict[str, Any], alert_service: AlertConfigService) -> None:
    """Render alert history section with filtering, pagination, and bulk acknowledge."""
    events_data: list[AlertEvent] = []
    total_count = 0
    current_page = 0
    page_size = 20
    current_filter: str | None = None

    async def fetch_events() -> None:
        nonlocal events_data, total_count, current_page
        try:
            total_count = await alert_service.get_alert_events_count(status_filter=current_filter)
            # Clamp page to valid range after count changes (e.g. after ack/filter)
            max_page = max(0, (total_count + page_size - 1) // page_size - 1)
            current_page = min(current_page, max_page)
            events_data = await alert_service.get_alert_events(
                limit=page_size,
                offset=current_page * page_size,
                status_filter=current_filter,
            )
        except psycopg.OperationalError as e:
            logger.warning(
                "events_fetch_db_error",
                extra={"error": str(e), "operation": "fetch_events"},
            )
            events_data = []
            total_count = 0
        except ValueError as e:
            logger.warning(
                "events_fetch_validation_error",
                extra={"error": str(e), "operation": "fetch_events"},
            )
            events_data = []
            total_count = 0

    await fetch_events()

    can_acknowledge = has_permission(user, Permission.ACKNOWLEDGE_ALERT)

    @ui.refreshable
    def history_display() -> None:
        nonlocal current_page, current_filter

        ui.label("Alert History").classes("text-xl font-bold mb-2")

        # Filter controls
        with ui.row().classes("items-center gap-4 mb-4"):
            filter_options = {"all": "All", "pending": "Pending", "acknowledged": "Acknowledged"}

            async def on_filter_change(e: Any) -> None:
                nonlocal current_filter, current_page
                current_filter = None if e.value == "all" else e.value
                current_page = 0
                await fetch_events()
                history_display.refresh()

            ui.select(
                label="Status Filter",
                options=filter_options,
                value="all" if current_filter is None else current_filter,
                on_change=on_filter_change,
            ).classes("w-48")

            total_pages = max(1, (total_count + page_size - 1) // page_size)
            ui.label(f"Page {current_page + 1} of {total_pages} ({total_count} total)").classes(
                "text-sm text-gray-500"
            )

        if not events_data:
            ui.label("No alert events recorded.").classes("text-gray-500")
            return

        # Events table
        columns: list[dict[str, Any]] = [
            {"name": "timestamp", "label": "Timestamp", "field": "timestamp", "sortable": True},
            {"name": "rule_name", "label": "Rule", "field": "rule_name"},
            {"name": "severity", "label": "Severity", "field": "severity"},
            {"name": "status", "label": "Status", "field": "status"},
            {"name": "message", "label": "Message", "field": "message"},
        ]

        rows: list[dict[str, Any]] = []
        for event in events_data:
            status = "Acknowledged" if event.acknowledged_at else "Pending"
            severity = getattr(event, "severity", "medium") or "medium"
            message = getattr(event, "message", "") or ""
            rows.append(
                {
                    "timestamp": event.triggered_at.isoformat() if event.triggered_at else "-",
                    "rule_name": event.rule_name or "-",
                    "severity": severity,
                    "status": status,
                    "message": message[:50] + "..." if len(message) > 50 else message,
                }
            )

        ui.table(columns=columns, rows=rows).classes("w-full")

        # Pagination controls
        total_pages = max(1, (total_count + page_size - 1) // page_size)
        if total_pages > 1:
            with ui.row().classes("items-center gap-2 mt-4"):

                async def prev_page() -> None:
                    nonlocal current_page
                    if current_page > 0:
                        current_page -= 1
                        await fetch_events()
                        history_display.refresh()

                async def next_page() -> None:
                    nonlocal current_page
                    if current_page < total_pages - 1:
                        current_page += 1
                        await fetch_events()
                        history_display.refresh()

                ui.button("Previous", on_click=prev_page).props(
                    f"outline {'disable' if current_page == 0 else ''}"
                )
                ui.button("Next", on_click=next_page).props(
                    f"outline {'disable' if current_page >= total_pages - 1 else ''}"
                )

        # Acknowledgment section
        if can_acknowledge:
            ui.separator().classes("my-4")
            pending_events = [e for e in events_data if not e.acknowledged_at]

            if not pending_events:
                ui.label("No pending alerts on this page.").classes("text-gray-500")
                return

            # Bulk acknowledge
            ui.label("Acknowledge Alerts").classes("font-bold mb-2")

            bulk_note_input = ui.textarea(
                label=f"Acknowledgment Note (min {MIN_ACK_NOTE_LENGTH} characters)",
                placeholder="Describe the action taken...",
            ).classes("w-full max-w-md")

            def _validate_ack_note() -> str | None:
                """Validate acknowledgment note, returning stripped note or None."""
                note = bulk_note_input.value
                if not note or len(note.strip()) < MIN_ACK_NOTE_LENGTH:
                    ui.notify(
                        f"Note must be at least {MIN_ACK_NOTE_LENGTH} characters",
                        type="negative",
                    )
                    return None
                result: str = note.strip()
                return result

            with ui.row().classes("gap-2 mt-2"):

                async def acknowledge_single() -> None:
                    """Acknowledge selected single alert."""
                    if not event_select.value:
                        ui.notify("Select an alert to acknowledge", type="negative")
                        return
                    note = _validate_ack_note()
                    if note is None:
                        return
                    try:
                        await alert_service.acknowledge_alert(
                            event_select.value, note, user
                        )
                        ui.notify("Alert acknowledged", type="positive")
                        bulk_note_input.value = ""
                        await fetch_events()
                        history_display.refresh()
                    except PermissionError as e:
                        ui.notify(f"Permission denied: {e}", type="negative")
                    except psycopg.OperationalError as e:
                        logger.exception(
                            "alert_acknowledge_db_error",
                            extra={
                                "event_id": event_select.value,
                                "error": str(e),
                                "operation": "acknowledge_alert",
                            },
                        )
                        ui.notify("Database error. Please try again.", type="negative")
                    except ValueError as e:
                        ui.notify(f"Invalid input: {e}", type="negative")

                async def acknowledge_all_pending() -> None:
                    """Bulk acknowledge all pending alerts on current page."""
                    note = _validate_ack_note()
                    if note is None:
                        return
                    alert_ids = [str(e.id) for e in pending_events]
                    try:
                        count = await alert_service.bulk_acknowledge_alerts(
                            alert_ids, note, user
                        )
                        ui.notify(f"{count} alert(s) acknowledged", type="positive")
                        bulk_note_input.value = ""
                        await fetch_events()
                        history_display.refresh()
                    except PermissionError as e:
                        ui.notify(f"Permission denied: {e}", type="negative")
                    except psycopg.OperationalError as e:
                        logger.exception(
                            "bulk_acknowledge_db_error",
                            extra={"error": str(e), "operation": "bulk_acknowledge"},
                        )
                        ui.notify("Database error. Please try again.", type="negative")
                    except ValueError as e:
                        ui.notify(f"Invalid input: {e}", type="negative")

                event_options = {
                    str(e.id): (
                        f"{e.rule_name} @ "
                        f"{e.triggered_at.isoformat() if e.triggered_at else 'Unknown'}"
                    )
                    for e in pending_events
                }

                event_select = ui.select(
                    label="Select Alert",
                    options=event_options,
                    value=list(event_options.keys())[0] if event_options else None,
                ).classes("w-full max-w-sm")

                ui.button("Acknowledge Selected", on_click=acknowledge_single, color="primary")
                ui.button(
                    f"Acknowledge All ({len(pending_events)})",
                    on_click=acknowledge_all_pending,
                    color="orange",
                ).props("outline")

    history_display()


async def _show_edit_dialog(
    alert_service: AlertConfigService,
    rule: AlertRule,
    user: dict[str, Any],
    fetch_fn: Any,
    refresh_fn: Any,
) -> None:
    """Show inline edit dialog for an alert rule."""
    from libs.web_console_services.alert_service import AlertRuleUpdate

    with ui.dialog() as edit_dialog, ui.card().classes("p-6 w-[600px]"):
        ui.label(f"Edit Rule: {rule.name}").classes("text-xl font-bold mb-4")

        name_input = ui.input(label="Rule Name", value=rule.name).classes("w-full")

        with ui.row().classes("gap-4"):
            condition_select = ui.select(
                label="Condition Type",
                options=CONDITION_TYPES,
                value=rule.condition_type,
            ).classes("w-48")
            comparison_select = ui.select(
                label="Comparison",
                options=COMPARISONS,
                value=rule.comparison,
            ).classes("w-24")
            threshold_input = ui.input(
                label="Threshold",
                value=str(rule.threshold_value),
            ).classes("w-32")

        enabled_checkbox = ui.checkbox("Enabled", value=rule.enabled)

        # Channel editing
        ui.label("Channels").classes("font-medium mt-4")

        # Pre-populate channel inputs with masked placeholders (never expose raw secrets)
        existing_channels = {c.type.value: c.recipient for c in rule.channels}
        enabled_channels = {c.type.value: c.enabled for c in rule.channels}

        # Track which existing channels should be removed
        remove_flags: dict[str, Any] = {}
        channel_inputs: dict[str, Any] = {}

        channel_defs: list[tuple[str, str]] = [
            ("email", "Email"),
            ("slack", "Slack Webhook (leave blank to keep current)"),
            ("pagerduty", "PagerDuty Routing Key (leave blank to keep current)"),
            ("sms", "SMS Phone (leave blank to keep current)"),
        ]
        for ch_key, ch_label in channel_defs:
            placeholder = (
                mask_recipient(existing_channels[ch_key], ch_key)
                if ch_key in existing_channels
                else ""
            )
            channel_inputs[ch_key] = ui.input(
                label=ch_label, placeholder=placeholder
            ).classes("w-full")
            if ch_key in existing_channels:
                remove_flags[ch_key] = ui.checkbox(f"Remove {ch_key} channel")

        async def save_changes() -> None:
            # Validate name length
            name_val = name_input.value.strip() if name_input.value else ""
            if name_val and len(name_val) < 3:
                ui.notify("Rule name must be at least 3 characters", type="negative")
                return

            # Safe decimal conversion
            try:
                threshold = Decimal(str(threshold_input.value))
            except (ValueError, InvalidOperation, ArithmeticError):
                ui.notify("Invalid threshold value", type="negative")
                return

            # Build channels: use new value if provided (non-whitespace), else keep existing
            channels: list[ChannelConfig] = []
            type_map: dict[str, ChannelType] = {
                "email": ChannelType.EMAIL,
                "slack": ChannelType.SLACK,
                "pagerduty": ChannelType.PAGERDUTY,
                "sms": ChannelType.SMS,
            }
            for key, ch_type in type_map.items():
                edit_field = channel_inputs[key]
                # Skip channels marked for removal
                if key in remove_flags and remove_flags[key].value:
                    continue
                if edit_field.value and edit_field.value.strip():
                    channels.append(
                        ChannelConfig(
                            type=ch_type,
                            recipient=edit_field.value.strip(),
                            enabled=enabled_channels.get(key, True),
                        )
                    )
                elif key in existing_channels:
                    channels.append(
                        ChannelConfig(
                            type=ch_type,
                            recipient=existing_channels[key],
                            enabled=enabled_channels.get(key, True),
                        )
                    )

            update = AlertRuleUpdate(
                name=name_val or None,
                condition_type=condition_select.value,
                threshold_value=threshold,
                comparison=comparison_select.value,
                channels=channels if channels else None,
                enabled=enabled_checkbox.value,
            )

            try:
                await alert_service.update_rule(str(rule.id), update, user)
                ui.notify("Rule updated", type="positive")
                edit_dialog.close()
                await fetch_fn()
                refresh_fn.refresh()
            except PermissionError as e:
                ui.notify(f"Permission denied: {e}", type="negative")
            except psycopg.OperationalError as e:
                logger.exception(
                    "rule_update_db_error",
                    extra={"rule_id": str(rule.id), "error": str(e)},
                )
                ui.notify("Database error. Please try again.", type="negative")
            except ValueError as e:
                ui.notify(f"Invalid input: {e}", type="negative")

        with ui.row().classes("gap-2 mt-4"):
            ui.button("Save", on_click=save_changes, color="primary")
            ui.button("Cancel", on_click=edit_dialog.close)

    edit_dialog.open()


async def _render_channels(user: dict[str, Any], alert_service: AlertConfigService) -> None:
    """Render notification channels section."""
    rules_data: list[AlertRule] = []

    try:
        rules_data = await alert_service.get_rules()
    except psycopg.OperationalError as e:
        logger.warning(
            "channels_fetch_db_error",
            extra={"error": str(e), "operation": "get_rules_for_channels"},
        )
    except ValueError as e:
        logger.warning(
            "channels_fetch_validation_error",
            extra={"error": str(e), "operation": "get_rules_for_channels"},
        )

    ui.label("Notification Channels").classes("text-xl font-bold mb-2")
    ui.label("Channels configured for each alert rule.").classes("text-gray-500 text-sm mb-4")

    if not rules_data:
        ui.label("No rules configured.").classes("text-gray-500")
        return

    for rule in rules_data:
        with ui.card().classes("w-full p-4 mb-2"):
            ui.label(rule.name).classes("font-bold text-lg")

            if not rule.channels:
                ui.label("No channels configured.").classes("text-gray-500 text-sm")
                continue

            for channel in rule.channels:
                status_icon = "check_circle" if channel.enabled else "cancel"
                status_color = "text-green-600" if channel.enabled else "text-red-600"
                masked_recipient = mask_recipient(channel.recipient, channel.type.value)

                with ui.row().classes("items-center gap-2"):
                    ui.icon(status_icon).classes(f"{status_color}")
                    ui.label(f"{channel.type.value.upper()}:").classes("font-medium")
                    ui.label(masked_recipient).classes("text-gray-600")
                    ui.label("enabled" if channel.enabled else "disabled").classes(
                        f"text-sm {status_color}"
                    )


__all__ = ["alerts_page"]
