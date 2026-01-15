"""Service layer for scheduled reports in the web console."""

from __future__ import annotations

import html
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from psycopg.rows import dict_row

from libs.core.common.db import acquire_connection
from libs.platform.web_console_auth.permissions import Permission, has_permission


class TradingClientProtocol(Protocol):
    """Protocol for trading client dependency injection."""

    async def startup(self) -> None: ...

    async def fetch_positions(
        self,
        user_id: str,
        role: str | None = None,
        strategies: list[str] | None = None,
    ) -> dict[str, Any]: ...

    async def fetch_recent_fills(
        self,
        user_id: str,
        role: str | None = None,
        strategies: list[str] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]: ...


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReportSchedule:
    """Scheduled report configuration."""

    id: str
    user_id: str
    name: str
    report_type: str
    cron: str | None
    params: dict[str, Any]
    recipients: list[str]
    strategies: list[str]
    enabled: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ReportRun:
    """Run history metadata for a scheduled report."""

    id: str
    schedule_id: str
    run_key: str
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    format: str | None = None


class ScheduledReportsService:
    """CRUD service for report schedules and archives."""

    def __init__(
        self,
        db_pool: Any,
        user: dict[str, Any],
        trading_client_factory: Callable[[], TradingClientProtocol] | None = None,
    ) -> None:
        self._db_pool = db_pool
        self._user = user
        self._trading_client_factory = trading_client_factory

    async def list_schedules(
        self, user_id: str | None = None, *, all_users: bool = False
    ) -> list[ReportSchedule]:
        """List report schedules.

        Args:
            user_id: Filter by specific user. Defaults to current user if not provided.
            all_users: If True, list schedules for all users (requires MANAGE_REPORTS).
        """
        self._require_permission(Permission.VIEW_REPORTS)

        current_user_id = self._user.get("user_id")

        # Cross-user access requires elevated permission (MANAGE_REPORTS)
        if all_users or (user_id and user_id != current_user_id):
            self._require_permission(Permission.MANAGE_REPORTS)

        # Require user_id in context for user-scoped queries
        if not all_users and not current_user_id:
            logger.warning(
                "report_list_denied_no_user",
                extra={"all_users": all_users, "user_id": user_id},
            )
            raise PermissionError("User context required for listing schedules")

        # Explicit all_users flag enables listing all schedules
        target_user_id = None if all_users else (user_id or current_user_id)

        async with acquire_connection(self._db_pool) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                if target_user_id:
                    await cur.execute(
                        """
                        SELECT id, user_id, name, template_type, schedule_config,
                               recipients, strategies, enabled, last_run_at,
                               next_run_at, created_at, updated_at
                        FROM report_schedules
                        WHERE user_id = %s
                        ORDER BY created_at DESC
                        """,
                        (target_user_id,),
                    )
                else:
                    await cur.execute(
                        """
                        SELECT id, user_id, name, template_type, schedule_config,
                               recipients, strategies, enabled, last_run_at,
                               next_run_at, created_at, updated_at
                        FROM report_schedules
                        ORDER BY created_at DESC
                        """
                    )
                rows = await cur.fetchall()

        return [self._row_to_schedule(row) for row in rows]

    async def create_schedule(
        self,
        name: str,
        report_type: str,
        cron: str,
        params: dict[str, Any],
        user_id: str,
    ) -> ReportSchedule:
        """Create a new report schedule."""
        self._require_permission(Permission.MANAGE_REPORTS)

        schedule_config = {"cron": cron, "params": params}

        async with acquire_connection(self._db_pool) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    INSERT INTO report_schedules (
                        user_id, name, template_type, schedule_config,
                        recipients, strategies, enabled, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    RETURNING id, user_id, name, template_type, schedule_config,
                              recipients, strategies, enabled, last_run_at,
                              next_run_at, created_at, updated_at
                    """,
                    (
                        user_id,
                        name,
                        report_type,
                        json.dumps(schedule_config),
                        json.dumps([]),
                        json.dumps([]),
                        True,
                    ),
                )
                row = await cur.fetchone()
            await conn.commit()

        if not row:
            raise RuntimeError("Schedule creation failed")
        return self._row_to_schedule(row)

    async def update_schedule(self, schedule_id: str, updates: dict[str, Any]) -> ReportSchedule:
        """Update an existing schedule.

        Config merge precedence (intentional):
          1. If schedule_config is provided, it replaces the current config
          2. If cron or params are provided, they override the corresponding keys
          This allows both full config replacement and partial key updates.
        """
        self._require_permission(Permission.MANAGE_REPORTS)

        allowed = {
            "name",
            "report_type",
            "cron",
            "params",
            "enabled",
            "recipients",
            "strategies",
            "schedule_config",
        }
        update_keys = [key for key in updates.keys() if key in allowed]
        if not update_keys:
            raise ValueError("No valid fields provided for update")

        async with acquire_connection(self._db_pool) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT id, user_id, name, template_type, schedule_config,
                           recipients, strategies, enabled, last_run_at,
                           next_run_at, created_at, updated_at
                    FROM report_schedules
                    WHERE id = %s
                    """,
                    (schedule_id,),
                )
                row = await cur.fetchone()
                if not row:
                    raise ValueError(f"Schedule {schedule_id} not found")

                current_config = self._normalize_config(row.get("schedule_config"))
                if "schedule_config" in updates:
                    current_config = self._normalize_config(updates["schedule_config"])
                if "cron" in updates:
                    current_config["cron"] = updates["cron"]
                if "params" in updates:
                    current_config["params"] = updates["params"]

                set_clauses: list[str] = []
                values: list[Any] = []

                if "name" in updates:
                    set_clauses.append("name = %s")
                    values.append(updates["name"])
                if "report_type" in updates:
                    set_clauses.append("template_type = %s")
                    values.append(updates["report_type"])
                if "enabled" in updates:
                    set_clauses.append("enabled = %s")
                    values.append(bool(updates["enabled"]))
                if "recipients" in updates:
                    set_clauses.append("recipients = %s")
                    values.append(json.dumps(updates["recipients"]))
                if "strategies" in updates:
                    set_clauses.append("strategies = %s")
                    values.append(json.dumps(updates["strategies"]))

                set_clauses.append("schedule_config = %s")
                values.append(json.dumps(current_config))

                set_clauses.append("updated_at = NOW()")

                query = (
                    "UPDATE report_schedules SET "
                    + ", ".join(set_clauses)
                    + " WHERE id = %s "
                    + "RETURNING id, user_id, name, template_type, schedule_config, "
                    + "recipients, strategies, enabled, last_run_at, next_run_at, "
                    + "created_at, updated_at"
                )
                values.append(schedule_id)
                await cur.execute(query, tuple(values))
                updated = await cur.fetchone()
            await conn.commit()

        if not updated:
            raise RuntimeError(f"Schedule {schedule_id} update failed")
        return self._row_to_schedule(updated)

    async def delete_schedule(self, schedule_id: str) -> bool:
        """Delete a report schedule."""
        self._require_permission(Permission.MANAGE_REPORTS)

        async with acquire_connection(self._db_pool) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "DELETE FROM report_schedules WHERE id = %s",
                    (schedule_id,),
                )
                deleted = cur.rowcount or 0
            await conn.commit()

        return deleted > 0

    async def get_run_history(self, schedule_id: str, limit: int = 25) -> list[ReportRun]:
        """Fetch run history for a schedule.

        Enforces ownership check: users can only view run history for their own
        schedules unless they have MANAGE_REPORTS permission.
        """
        self._require_permission(Permission.VIEW_REPORTS)

        current_user_id = self._user.get("user_id")

        # Check if user has admin access for cross-user queries
        has_manage = has_permission(self._user, Permission.MANAGE_REPORTS)

        async with acquire_connection(self._db_pool) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # Scope ownership query to prevent existence oracle attack
                # Non-admins only see their own schedules; admins see all
                if has_manage:
                    await cur.execute(
                        "SELECT user_id FROM report_schedules WHERE id = %s",
                        (schedule_id,),
                    )
                else:
                    await cur.execute(
                        "SELECT user_id FROM report_schedules WHERE id = %s AND user_id = %s",
                        (schedule_id, current_user_id),
                    )
                schedule_row = await cur.fetchone()

                if not schedule_row:
                    return []  # Schedule not found or not owned by user

                await cur.execute(
                    """
                    SELECT r.id, r.schedule_id, r.run_key, r.status, r.started_at,
                           r.completed_at, r.error_message,
                           a.file_format
                    FROM report_schedule_runs r
                    LEFT JOIN LATERAL (
                        SELECT file_format
                        FROM report_archives
                        WHERE idempotency_key = r.run_key
                        ORDER BY generated_at DESC
                        LIMIT 1
                    ) a ON TRUE
                    WHERE r.schedule_id = %s
                    ORDER BY r.started_at DESC NULLS LAST, r.completed_at DESC NULLS LAST
                    LIMIT %s
                    """,
                    (schedule_id, limit),
                )
                rows = await cur.fetchall()

        return [
            ReportRun(
                id=str(row["id"]),
                schedule_id=str(row["schedule_id"]),
                run_key=row["run_key"],
                status=row["status"],
                started_at=row.get("started_at"),
                completed_at=row.get("completed_at"),
                error_message=row.get("error_message"),
                format=row.get("file_format"),
            )
            for row in rows
        ]

    async def _fetch_trading_data(
        self,
        current_user_id: str | None,
        role: str | None,
        strategies: list[str] | None,
    ) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        """Fetch positions and fills from trading client.

        Returns:
            Tuple of (positions_payload, fills_payload, data_errors)
        """
        data_errors: list[str] = []
        positions_payload: dict[str, Any] = {}
        fills_payload: dict[str, Any] = {}

        if self._trading_client_factory is None:
            data_errors.append("Trading client not configured")
            return positions_payload, fills_payload, data_errors

        client = None
        try:
            client = self._trading_client_factory()
            await client.startup()
            positions_payload = await client.fetch_positions(
                current_user_id or "",
                role=role,
                strategies=strategies,
            )
            fills_payload = await client.fetch_recent_fills(
                current_user_id or "",
                role=role,
                strategies=strategies,
                limit=100,
            )
        except (OSError, ConnectionError, KeyError, ValueError, TypeError) as exc:
            data_errors.append(f"Failed to fetch trading data: {exc}")
        # NOTE: Do NOT shutdown the client here - it's a shared singleton managed
        # at the application level (NiceGUI lifespan). Shutting it down would break
        # other concurrent requests (dashboard, orders, etc.).

        return positions_payload, fills_payload, data_errors

    def _generate_html_content(
        self,
        schedule_name: str,
        report_type: str,
        params: dict[str, Any],
        positions_payload: dict[str, Any],
        fills_payload: dict[str, Any],
        data_errors: list[str],
        now: datetime,
    ) -> str:
        """Generate HTML report content.

        SECURITY: All user-controlled strings are HTML-escaped to prevent XSS.
        """

        def _fmt_money(value: Any) -> str:
            try:
                return f"${float(value):,.2f}"
            except (TypeError, ValueError):
                return "-" if value is None else html.escape(str(value))

        def _fmt_value(value: Any) -> str:
            return "-" if value is None else html.escape(str(value))

        # Normalize payloads to dicts to avoid repeated isinstance checks
        pos_data = positions_payload if isinstance(positions_payload, dict) else {}
        fills_data = fills_payload if isinstance(fills_payload, dict) else {}

        positions = pos_data.get("positions", [])
        total_realized = pos_data.get("total_realized_pl")
        total_unrealized = pos_data.get("total_unrealized_pl")
        total_positions = pos_data.get("total_positions", 0)

        fills = fills_data.get("events", [])

        positions_rows = "\n".join(
            f"<tr><td>{_fmt_value(p.get('symbol'))}</td>"
            f"<td>{_fmt_value(p.get('qty'))}</td>"
            f"<td>{_fmt_money(p.get('avg_entry_price'))}</td>"
            f"<td>{_fmt_money(p.get('current_price'))}</td>"
            f"<td>{_fmt_money(p.get('unrealized_pl'))}</td>"
            f"<td>{_fmt_money(p.get('realized_pl'))}</td></tr>"
            for p in positions
        ) or "<tr><td colspan=\"6\">No positions</td></tr>"

        fills_rows = "\n".join(
            f"<tr><td>{_fmt_value(f.get('timestamp'))}</td>"
            f"<td>{_fmt_value(f.get('symbol'))}</td>"
            f"<td>{_fmt_value(f.get('side'))}</td>"
            f"<td>{_fmt_value(f.get('qty'))}</td>"
            f"<td>{_fmt_money(f.get('price'))}</td>"
            f"<td>{_fmt_money(f.get('realized_pl'))}</td>"
            f"<td>{_fmt_value(f.get('status'))}</td></tr>"
            for f in fills
        ) or "<tr><td colspan=\"7\">No recent fills</td></tr>"

        error_block = ""
        if data_errors:
            escaped_errors = [html.escape(str(e)[:200]) for e in data_errors]
            error_block = (
                "<div style=\"color:#b91c1c; margin-bottom:12px;\">"
                "<strong>Data warnings:</strong><ul>"
                + "".join(f"<li>{e}</li>" for e in escaped_errors)
                + "</ul></div>"
            )

        safe_schedule_name = html.escape(str(schedule_name))
        safe_report_type = html.escape(str(report_type))

        return f"""<!doctype html>
<html>
<head><meta charset="utf-8"><title>{safe_schedule_name}</title></head>
<body>
  <h1>{safe_schedule_name}</h1>
  <p>Report Type: {safe_report_type}</p>
  <p>Generated At (UTC): {now.strftime("%Y-%m-%d %H:%M:%S")}</p>
  {error_block}
  <h2>Summary</h2>
  <ul>
    <li>Total Positions: {_fmt_value(total_positions)}</li>
    <li>Total Realized P&amp;L: {_fmt_money(total_realized)}</li>
    <li>Total Unrealized P&amp;L: {_fmt_money(total_unrealized)}</li>
  </ul>
  <h2>Positions</h2>
  <table border="1" cellpadding="6" cellspacing="0">
    <thead>
      <tr>
        <th>Symbol</th><th>Qty</th><th>Avg Entry</th><th>Current</th>
        <th>Unrealized P&amp;L</th><th>Realized P&amp;L</th>
      </tr>
    </thead>
    <tbody>
      {positions_rows}
    </tbody>
  </table>
  <h2>Recent Fills</h2>
  <table border="1" cellpadding="6" cellspacing="0">
    <thead>
      <tr>
        <th>Time</th><th>Symbol</th><th>Side</th><th>Qty</th>
        <th>Price</th><th>Realized P&amp;L</th><th>Status</th>
      </tr>
    </thead>
    <tbody>
      {fills_rows}
    </tbody>
  </table>
  <h2>Parameters</h2>
  <pre>{html.escape(json.dumps(params, indent=2))}</pre>
</body>
</html>
"""

    async def run_now(self, schedule_id: str) -> ReportRun:
        """Generate a report immediately and store the archive metadata."""
        self._require_permission(Permission.VIEW_REPORTS)

        current_user_id = self._user.get("user_id")
        role = self._user.get("role")
        strategies = (
            self._user.get("strategies")
            if isinstance(self._user.get("strategies"), list)
            else None
        )
        has_manage = has_permission(self._user, Permission.MANAGE_REPORTS)

        # Fetch trading data using helper method
        positions_payload, fills_payload, data_errors = await self._fetch_trading_data(
            current_user_id, role, strategies
        )

        async with acquire_connection(self._db_pool) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # Dynamic query based on permission level
                query = """
                    SELECT id, user_id, name, template_type, schedule_config
                    FROM report_schedules
                    WHERE id = %s
                """
                params: list[Any] = [schedule_id]
                if not has_manage:
                    query += " AND user_id = %s"
                    params.append(current_user_id)

                await cur.execute(query, tuple(params))
                schedule_row = await cur.fetchone()

                if not schedule_row:
                    raise ValueError("Schedule not found or access denied")

                run_key = f"manual-{uuid4().hex}"
                now = datetime.now(UTC)

                await cur.execute(
                    """
                    INSERT INTO report_schedule_runs (
                        schedule_id, run_key, status, started_at, completed_at
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (schedule_id, run_key, "completed", now, now),
                )

                report_output_dir = Path(
                    os.getenv("REPORT_OUTPUT_DIR", "artifacts/reports")
                ).resolve()
                report_output_dir.mkdir(parents=True, exist_ok=True)

                file_name = f"report_{run_key}.html"
                file_path = report_output_dir / file_name

                schedule_name = schedule_row.get("name") or "Scheduled Report"
                report_type = schedule_row.get("template_type") or "custom"
                config = self._normalize_config(schedule_row.get("schedule_config"))
                report_params_val = config.get("params")
                report_params: dict[str, Any] = (
                    report_params_val if isinstance(report_params_val, dict) else {}
                )

                # Generate HTML content using helper method
                content = self._generate_html_content(
                    schedule_name=schedule_name,
                    report_type=report_type,
                    params=report_params,
                    positions_payload=positions_payload,
                    fills_payload=fills_payload,
                    data_errors=data_errors,
                    now=now,
                )
                file_path.write_text(content)
                size_bytes = file_path.stat().st_size

                await cur.execute(
                    """
                    INSERT INTO report_archives (
                        schedule_id, user_id, idempotency_key, generated_at,
                        file_path, file_format, file_size_bytes
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        schedule_id,
                        schedule_row.get("user_id") or current_user_id,
                        run_key,
                        now,
                        str(file_path),
                        "html",
                        size_bytes,
                    ),
                )

                await cur.execute(
                    """
                    UPDATE report_schedules
                    SET last_run_at = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (now, schedule_id),
                )
            await conn.commit()

        return ReportRun(
            id=run_key,
            schedule_id=schedule_id,
            run_key=run_key,
            status="completed",
            started_at=now,
            completed_at=now,
            error_message=None,
        )

    async def download_archive(self, run_id: str) -> Path | None:
        """Return path to archived report for streaming by the API layer.

        Returns Path rather than bytes to enable efficient streaming of large files.
        """
        self._require_permission(Permission.VIEW_REPORTS)

        user_id = self._user.get("user_id")
        if not user_id:
            # Require authenticated user context to prevent cross-user access
            logger.warning(
                "report_archive_denied_no_user",
                extra={"run_id": run_id},
            )
            return None

        run_key = run_id

        async with acquire_connection(self._db_pool) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # Scope run_key lookup to user via join to prevent metadata leakage
                await cur.execute(
                    """
                    SELECT r.run_key
                    FROM report_schedule_runs r
                    JOIN report_schedules s ON r.schedule_id = s.id
                    WHERE r.id = %s AND s.user_id = %s
                    """,
                    (run_id, user_id),
                )
                run_row = await cur.fetchone()
                if run_row and run_row.get("run_key"):
                    run_key = run_row["run_key"]

                # Always filter by user_id to enforce user scoping
                await cur.execute(
                    """
                    SELECT file_path
                    FROM report_archives
                    WHERE idempotency_key = %s AND user_id = %s
                    ORDER BY generated_at DESC
                    LIMIT 1
                    """,
                    (run_key, user_id),
                )
                archive = await cur.fetchone()

        if not archive:
            return None

        file_path_raw = archive.get("file_path")
        if not file_path_raw:
            return None

        report_output_dir = Path(os.getenv("REPORT_OUTPUT_DIR", "artifacts/reports")).resolve()
        try:
            file_path = Path(file_path_raw).expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            logger.warning(
                "report_archive_invalid_path",
                extra={"path": file_path_raw, "error": str(exc)},
            )
            return None

        if not file_path.is_relative_to(report_output_dir):
            logger.warning(
                "report_archive_outside_dir",
                extra={"path": str(file_path), "allowed_dir": str(report_output_dir)},
            )
            return None

        if not file_path.exists():
            logger.warning(
                "report_archive_missing_file",
                extra={"path": str(file_path)},
            )
            return None

        # Return Path for streaming instead of loading bytes into memory
        return file_path

    def _require_permission(self, permission: Permission) -> None:
        if not has_permission(self._user, permission):
            logger.warning(
                "report_permission_denied",
                extra={
                    "user_id": self._user.get("user_id"),
                    "permission": permission.value,
                },
            )
            raise PermissionError(f"Permission {permission.value} required")

    @staticmethod
    def _normalize_config(raw: Any) -> dict[str, Any]:
        if raw is None:
            return {}
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str):
            try:
                result = json.loads(raw)
                return dict(result) if isinstance(result, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    def _row_to_schedule(self, row: dict[str, Any]) -> ReportSchedule:
        config = self._normalize_config(row.get("schedule_config"))
        cron_value = config.get("cron") or config.get("cron_expression")
        params = config.get("params") or {}

        recipients = self._parse_json_list(row.get("recipients"))
        strategies = self._parse_json_list(row.get("strategies"))

        return ReportSchedule(
            id=str(row["id"]),
            user_id=row["user_id"],
            name=row["name"],
            report_type=row["template_type"],
            cron=cron_value,
            params=params,
            recipients=recipients,
            strategies=strategies,
            enabled=bool(row["enabled"]),
            last_run_at=row.get("last_run_at"),
            next_run_at=row.get("next_run_at"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _parse_json_list(value: Any) -> list[Any]:
        """Parse a JSON string or list into a list."""
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, list) else []
            except json.JSONDecodeError:
                return []
        # For other iterables (not string)
        if hasattr(value, "__iter__"):
            return list(value)
        return []


__all__ = [
    "ScheduledReportsService",
    "ReportSchedule",
    "ReportRun",
]
