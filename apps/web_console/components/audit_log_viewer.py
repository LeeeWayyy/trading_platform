"""Streamlit component for viewing audit logs with filtering and export."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from io import StringIO
from typing import Any

import streamlit as st

from libs.common.async_utils import run_async
from libs.common.log_sanitizer import sanitize_dict
from libs.web_console_auth.db import acquire_connection
from libs.web_console_auth.gateway_auth import AuthenticatedUser
from libs.web_console_auth.permissions import Permission, has_permission

PAGE_SIZE = 50
MAX_EXPORT_RECORDS = 10000  # Limit CSV export to prevent memory issues
_PAGE_STATE_KEY = "audit_log_page"
_EXPORT_CACHE_KEY = "audit_log_export_cache"
_EXPORT_FILTERS_KEY = "audit_log_export_filters"

# Timeout constants
_FETCH_TIMEOUT_SECONDS = 10.0
_EXPORT_TIMEOUT_SECONDS = 30.0

_AUDIT_QUERY = """
SELECT
    id, timestamp, user_id, action, event_type,
    resource_type, resource_id, outcome, details
FROM audit_log
WHERE (%s::text IS NULL OR user_id = %s::text)
  AND (%s::text IS NULL OR action = %s::text)
  AND (%s::text IS NULL OR event_type = %s::text)
  AND (%s::text IS NULL OR outcome = %s::text)
  AND (%s::timestamptz IS NULL OR timestamp >= %s::timestamptz)
  AND (%s::timestamptz IS NULL OR timestamp <= %s::timestamptz)
ORDER BY timestamp DESC
LIMIT %s::int OFFSET %s::int
"""

_COUNT_QUERY = """
SELECT COUNT(*) FROM audit_log
WHERE (%s::text IS NULL OR user_id = %s::text)
  AND (%s::text IS NULL OR action = %s::text)
  AND (%s::text IS NULL OR event_type = %s::text)
  AND (%s::text IS NULL OR outcome = %s::text)
  AND (%s::timestamptz IS NULL OR timestamp >= %s::timestamptz)
  AND (%s::timestamptz IS NULL OR timestamp <= %s::timestamptz)
"""

_ACTION_CHOICES = [
    "All",
    "config_saved",
    "api_key_created",
    "api_key_revoked",
    "role_changed",
    "login",
    "logout",
]
_EVENT_TYPES = ["All", "admin", "auth", "action"]
_OUTCOMES = ["All", "success", "failure"]


@dataclass
class AuditFilters:
    """Filters applied to audit log queries."""

    user_id: str | None
    action: str | None
    event_type: str | None
    outcome: str | None
    start_at: datetime | None
    end_at: datetime | None

    def cache_key(self) -> str:
        """Generate a cache key from filter values."""
        return (
            f"{self.user_id}|{self.action}|{self.event_type}|"
            f"{self.outcome}|{self.start_at}|{self.end_at}"
        )


def render_audit_log_viewer(user: AuthenticatedUser, db_pool: Any) -> None:
    """Render the audit log viewer with filters, pagination, and export."""

    if not has_permission(user, Permission.VIEW_AUDIT):
        st.error("Permission denied: VIEW_AUDIT required")
        return

    st.title("Audit Log")
    st.caption("Query audit events with masking applied to sensitive fields.")

    filters, reset_page = _render_filters()

    page = st.session_state.get(_PAGE_STATE_KEY, 0)
    if reset_page:
        page = 0
    offset = _offset_for_page(page)

    logs, total = run_async(
        _fetch_audit_logs(db_pool=db_pool, filters=filters, limit=PAGE_SIZE, offset=offset),
        timeout=_FETCH_TIMEOUT_SECONDS,
    )

    st.caption(f"Showing {len(logs)} of {total} records (page {page + 1})")

    _render_table(logs)

    new_page = _render_pagination(total=total, current_page=page)
    if new_page != page:
        st.session_state[_PAGE_STATE_KEY] = new_page

    if total > 0:
        # Lazy export: only fetch when filters change (cache by filter key)
        current_filter_key = filters.cache_key()
        cached_filter_key = st.session_state.get(_EXPORT_FILTERS_KEY)

        if cached_filter_key != current_filter_key:
            # Filters changed - fetch fresh export data
            all_logs, _ = run_async(
                _fetch_audit_logs(
                    db_pool=db_pool, filters=filters, limit=MAX_EXPORT_RECORDS, offset=0
                ),
                timeout=_EXPORT_TIMEOUT_SECONDS,
            )
            csv_data = _build_csv(all_logs)
            st.session_state[_EXPORT_CACHE_KEY] = (csv_data, len(all_logs))
            st.session_state[_EXPORT_FILTERS_KEY] = current_filter_key
        else:
            # Use cached export data
            csv_data, _ = st.session_state.get(_EXPORT_CACHE_KEY, (b"", 0))

        _, export_count = st.session_state.get(_EXPORT_CACHE_KEY, (b"", 0))
        label = f"Download CSV ({export_count} records)"
        if export_count >= MAX_EXPORT_RECORDS:
            label = f"Download CSV ({export_count}+ records, limited)"
        st.download_button(
            label,
            data=csv_data,
            file_name="audit_logs.csv",
            mime="text/csv",
        )


def _render_filters() -> tuple[AuditFilters, bool]:
    """Render filter controls and return selected filters."""

    with st.form("audit_log_filters"):
        user_id = st.text_input("User ID", help="Exact user id match (optional)")
        action = st.selectbox("Action", _ACTION_CHOICES, index=0)
        event_type = st.selectbox("Event Type", _EVENT_TYPES, index=0)
        outcome = st.selectbox("Outcome", _OUTCOMES, index=0)

        use_date_filter = st.checkbox("Filter by date range", value=False)
        start_date: date | None = None
        end_date: date | None = None
        if use_date_filter:
            start_date = st.date_input("Start date")
            end_date = st.date_input("End date")

        submitted = st.form_submit_button("Apply Filters", type="primary")

    filters = AuditFilters(
        user_id=user_id.strip() or None,
        action=_normalize_select(action),
        event_type=_normalize_select(event_type),
        outcome=_normalize_select(outcome),
        start_at=_to_datetime_start(start_date),
        end_at=_to_datetime_end(end_date),
    )

    return filters, submitted


def _render_table(logs: list[dict[str, Any]]) -> None:
    """Display the audit log table with expandable details."""

    if not logs:
        st.info("No audit events found for the selected filters.")
        return

    table_rows = [
        {
            "Timestamp": _format_ts(log["timestamp"]),
            "User ID": log["user_id"],
            "Action": log["action"],
            "Event Type": log["event_type"],
            "Resource Type": log["resource_type"],
            "Resource ID": log["resource_id"],
            "Outcome": log["outcome"],
        }
        for log in logs
    ]
    st.dataframe(table_rows, use_container_width=True)

    for log in logs:
        with st.expander(f"Details • {log['action']} • {log['timestamp']}"):
            st.json(log.get("details") or {})


def _render_pagination(total: int, current_page: int) -> int:
    """Render pagination controls and return the updated page index."""

    max_page = 0 if total == 0 else (total - 1) // PAGE_SIZE
    prev_disabled = current_page <= 0
    next_disabled = current_page >= max_page

    prev, next_ = st.columns(2)
    new_page = current_page
    if prev.button("Previous", disabled=prev_disabled, key="audit_prev"):
        new_page = max(current_page - 1, 0)
    if next_.button("Next", disabled=next_disabled, key="audit_next"):
        new_page = min(current_page + 1, max_page)

    return new_page


async def _fetch_audit_logs(
    *,
    db_pool: Any,
    filters: AuditFilters,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    """Execute filtered audit log query and return rows plus total count."""

    params = (
        filters.user_id,
        filters.user_id,
        filters.action,
        filters.action,
        filters.event_type,
        filters.event_type,
        filters.outcome,
        filters.outcome,
        filters.start_at,
        filters.start_at,
        filters.end_at,
        filters.end_at,
        limit,
        offset,
    )
    count_params = params[:-2]

    async with acquire_connection(db_pool) as conn:
        cursor = await conn.execute(_AUDIT_QUERY, params)
        rows = await cursor.fetchall()

        count_cursor = await conn.execute(_COUNT_QUERY, count_params)
        count_row = await count_cursor.fetchone()

    total = _extract_count(count_row)
    parsed = [_parse_row(row) for row in rows or []]
    return parsed, total


def _parse_row(row: Any) -> dict[str, Any]:
    """Convert raw DB row to a sanitized dictionary."""

    def _get(idx: int, key: str) -> Any:
        if isinstance(row, dict):
            return row.get(key)
        return row[idx]

    details_raw = _get(8, "details")
    return {
        "id": _get(0, "id"),
        "timestamp": _get(1, "timestamp"),
        "user_id": _get(2, "user_id"),
        "action": _get(3, "action"),
        "event_type": _get(4, "event_type"),
        "resource_type": _get(5, "resource_type"),
        "resource_id": _get(6, "resource_id"),
        "outcome": _get(7, "outcome"),
        "details": _sanitize_details(details_raw),
    }


def _sanitize_details(details: Any) -> dict[str, Any] | None:
    """Sanitize details payload while preserving structure."""

    if details is None:
        return None

    parsed: Any = details
    if isinstance(details, str):
        try:
            parsed = json.loads(details)
        except json.JSONDecodeError:
            parsed = {"raw": details}

    if isinstance(parsed, dict):
        return sanitize_dict(parsed)

    return {"data": sanitize_dict({"data": parsed}).get("data")}


def _extract_count(row: Any) -> int:
    """Extract integer count from DB row format."""

    if row is None:
        return 0
    if isinstance(row, dict):
        return int(row.get("count") or row.get("count_1") or 0)
    if isinstance(row, list | tuple) and row:
        return int(row[0])
    return int(row)


def _build_csv(logs: list[dict[str, Any]]) -> bytes:
    """Build CSV bytes from sanitized log rows."""

    fieldnames = [
        "timestamp",
        "user_id",
        "action",
        "event_type",
        "resource_type",
        "resource_id",
        "outcome",
        "details",
    ]
    output_rows: list[dict[str, str]] = []
    for log in logs:
        details = log.get("details") or {}
        output_rows.append(
            {
                "timestamp": _format_ts(log.get("timestamp")),
                "user_id": str(log.get("user_id") or ""),
                "action": str(log.get("action") or ""),
                "event_type": str(log.get("event_type") or ""),
                "resource_type": str(log.get("resource_type") or ""),
                "resource_id": str(log.get("resource_id") or ""),
                "outcome": str(log.get("outcome") or ""),
                "details": json.dumps(details, default=str),
            }
        )

    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(output_rows)
    return buffer.getvalue().encode()


def _format_ts(value: Any) -> str:
    """Format timestamp values for display/export."""

    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _normalize_select(value: str | None) -> str | None:
    """Return None when selectbox value represents 'All'."""

    if value is None:
        return None
    if value.lower() == "all":
        return None
    return value


def _to_datetime_start(selected: date | None) -> datetime | None:
    """Convert start date to UTC datetime at midnight."""

    if selected is None:
        return None
    return datetime.combine(selected, time.min, tzinfo=UTC)


def _to_datetime_end(selected: date | None) -> datetime | None:
    """Convert end date to UTC datetime at end of day."""

    if selected is None:
        return None
    return datetime.combine(selected, time.max, tzinfo=UTC)


def _offset_for_page(page: int) -> int:
    """Compute offset for the given page index."""

    return max(page, 0) * PAGE_SIZE


__all__ = ["render_audit_log_viewer"]
