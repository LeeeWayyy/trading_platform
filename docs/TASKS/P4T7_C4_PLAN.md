# P4T7 C4: Scheduled Reports - Component Plan

**Component:** C4 - T9.4 Scheduled Reports
**Parent Task:** P4T7 Web Console Research & Reporting
**Status:** PLANNING
**Estimated Effort:** 3-4 days
**Dependencies:** C0 (Prep & Validation)

---

## Overview

Implement T9.4 Scheduled Reports that enables users to configure, schedule, and receive automated trading reports via email.

## Acceptance Criteria (from P4T7_TASK.md)

- [ ] Report template configuration UI (select metrics, date range, strategies)
- [ ] Schedule management: daily (EOD), weekly (Monday 6am), monthly (1st of month)
- [ ] PDF generation using WeasyPrint with platform branding
- [ ] HTML generation with embedded charts (Plotly static exports)
- [ ] Email distribution with attachment or inline HTML
- [ ] Report archive: persist generated reports with 90-day retention
- [ ] Report preview before scheduling
- [ ] Immediate "Run Now" option for testing
- [ ] RBAC: MANAGE_REPORTS permission for create/edit, VIEW_REPORTS for viewing archive
- [ ] Delivery confirmation tracking with retry on failure

---

## Architecture

### System Design

```
┌─────────────────────────────────────────────────────────────────┐
│                   Web Console UI                                 │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │  Template       │  │  Schedule       │  │  Archive        │ │
│  │  Editor         │  │  Manager        │  │  Viewer         │ │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘ │
└───────────┼─────────────────────┼─────────────────────┼─────────┘
            │                     │                     │
            ▼                     ▼                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Report Service                                 │
│  - create_template(config) → template_id                        │
│  - create_schedule(template_id, schedule) → schedule_id         │
│  - preview_report(template_id) → bytes (PDF)                    │
│  - generate_report(schedule_id) → archive_id                    │
│  - get_archive(archive_id) → bytes                              │
└───────────────────────────────────────────────────────────────────┘
            │                     │                     │
            ▼                     ▼                     ▼
┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
│ ReportGenerator │   │ APScheduler     │   │ DeliveryService │
│ (PDF/HTML)      │   │ (Scheduler)     │   │ (from T7.5)     │
└─────────────────┘   └─────────────────┘   └─────────────────┘
            │                                       │
            ▼                                       ▼
┌─────────────────┐                       ┌─────────────────┐
│ PostgreSQL      │                       │ S3 / Local      │
│ (schedules,     │                       │ (PDF storage)   │
│  archives)      │                       └─────────────────┘
└─────────────────┘
```

### File Structure

```
apps/web_console/
├── pages/
│   └── reports.py               # Report configuration page
├── components/
│   ├── report_template_editor.py  # Template configuration
│   ├── schedule_manager.py      # Schedule CRUD
│   └── report_preview.py        # Preview component
├── services/
│   └── report_service.py        # Report orchestration

libs/reporting/
├── __init__.py
├── report_generator.py          # Core report generation
├── pdf_generator.py             # PDF generation (WeasyPrint)
├── html_generator.py            # HTML generation (Jinja2)
├── scheduler.py                 # APScheduler with DB-persisted schedules
├── templates/
│   ├── base.html                # Base Jinja2 template
│   ├── daily_summary.html       # Daily summary template
│   ├── weekly_performance.html  # Weekly performance template
│   └── monthly_pnl.html         # Monthly P&L template
└── static/
    ├── styles.css               # Report CSS
    └── logo.png                 # Platform branding

db/migrations/
└── 0018_create_report_tables.sql

tests/libs/reporting/
├── test_report_generator.py
├── test_pdf_generator.py
├── test_scheduler.py
└── golden_reports/              # Golden PDF fixtures

docs/
├── CONCEPTS/reporting.md
└── ADRs/ADR-0030-reporting-architecture.md
```

---

## Implementation Details

### 1. Database Schema

```sql
-- db/migrations/0018_create_report_tables.sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Report templates (with multi-tenant scoping per P4T7_TASK.md)
CREATE TABLE IF NOT EXISTS report_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL,  -- Multi-tenant scoping
    name VARCHAR(255) NOT NULL,
    description TEXT,
    template_type VARCHAR(50) NOT NULL,  -- daily_summary, weekly_performance, monthly_pnl
    config JSONB NOT NULL DEFAULT '{}',
    created_by UUID NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(account_id, name)  -- Template names unique per account
);

CREATE INDEX idx_report_templates_account ON report_templates(account_id);

-- Example config JSONB structure:
-- {
--   "metrics": ["total_pnl", "sharpe_ratio", "max_drawdown"],
--   "strategies": ["alpha_baseline", "momentum"],
--   "date_range": "last_30_days",  -- or {"start": "2024-01-01", "end": "2024-01-31"}
--   "format": "pdf",  -- or "html"
--   "include_charts": true
-- }

-- Report schedules (with multi-tenant scoping)
CREATE TABLE IF NOT EXISTS report_schedules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL,  -- Multi-tenant scoping
    template_id UUID NOT NULL REFERENCES report_templates(id) ON DELETE CASCADE,
    schedule_type VARCHAR(20) NOT NULL,  -- daily, weekly, monthly
    schedule_config JSONB NOT NULL DEFAULT '{}',
    recipients JSONB NOT NULL DEFAULT '[]',
    enabled BOOLEAN DEFAULT true,
    last_run_at TIMESTAMPTZ,
    next_run_at TIMESTAMPTZ,
    created_by UUID NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Example schedule_config JSONB:
-- daily: {"hour": 18, "minute": 0}  -- 6pm
-- weekly: {"day_of_week": 1, "hour": 6}  -- Monday 6am
-- monthly: {"day_of_month": 1, "hour": 6}  -- 1st of month 6am

CREATE INDEX idx_report_schedules_account ON report_schedules(account_id);
CREATE INDEX idx_report_schedules_next_run ON report_schedules(next_run_at) WHERE enabled = true;

-- Report archives (with idempotency)
CREATE TABLE IF NOT EXISTS report_archives (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL,  -- Multi-tenant scoping
    schedule_id UUID REFERENCES report_schedules(id) ON DELETE SET NULL,
    template_id UUID NOT NULL REFERENCES report_templates(id),
    idempotency_key VARCHAR(100) NOT NULL,  -- schedule_id + generated_at to prevent duplicates
    generated_at TIMESTAMPTZ NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    file_format VARCHAR(10) NOT NULL,  -- pdf, html
    file_size_bytes INTEGER,
    delivery_status VARCHAR(20) DEFAULT 'pending',
    delivered_at TIMESTAMPTZ,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(idempotency_key)  -- Prevent duplicate archive generation
);

-- Schedule run tracking for idempotency (APScheduler)
CREATE TABLE IF NOT EXISTS report_schedule_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    schedule_id UUID NOT NULL REFERENCES report_schedules(id),
    run_key VARCHAR(100) NOT NULL,  -- schedule_id + date for idempotency
    status VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending, running, completed, failed
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    archive_id UUID REFERENCES report_archives(id),
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(run_key)  -- Prevent duplicate runs
);

CREATE INDEX idx_report_archives_account ON report_archives(account_id);
CREATE INDEX idx_report_archives_generated ON report_archives(generated_at);
```

### 2. Report Generator

```python
# libs/reporting/report_generator.py
"""Core report generation orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from libs.reporting.html_generator import HTMLGenerator
from libs.reporting.pdf_generator import PDFGenerator

logger = logging.getLogger(__name__)


@dataclass
class ReportConfig:
    """Configuration for report generation."""
    template_type: str
    metrics: list[str]
    strategies: list[str]
    date_range: tuple[date, date]
    format: str  # "pdf" or "html"
    include_charts: bool = True


@dataclass
class GeneratedReport:
    """Result of report generation."""
    report_id: str
    file_path: Path
    file_format: str
    file_size_bytes: int
    generated_at: datetime


class ReportGenerator:
    """Orchestrates report generation."""

    def __init__(
        self,
        html_generator: HTMLGenerator,
        pdf_generator: PDFGenerator,
        output_dir: Path,
    ):
        self._html = html_generator
        self._pdf = pdf_generator
        self._output_dir = output_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)

    async def generate_report(
        self,
        config: ReportConfig,
        data: dict[str, Any],
    ) -> GeneratedReport:
        """Generate report from configuration and data.

        Args:
            config: Report configuration
            data: Data to include in report (metrics, charts, etc.)

        Returns:
            GeneratedReport with file path and metadata
        """
        report_id = str(uuid4())
        timestamp = datetime.now(UTC)

        # Generate HTML first
        html_content = await self._html.render(
            template_name=f"{config.template_type}.html",
            context={
                "config": config,
                "data": data,
                "generated_at": timestamp,
                "include_charts": config.include_charts,
            },
        )

        if config.format == "html":
            file_path = self._output_dir / f"{report_id}.html"
            file_path.write_text(html_content)
        else:
            # Convert HTML to PDF
            pdf_bytes = await self._pdf.html_to_pdf(html_content)
            file_path = self._output_dir / f"{report_id}.pdf"
            file_path.write_bytes(pdf_bytes)

        file_size = file_path.stat().st_size

        logger.info(
            f"Generated report {report_id}",
            extra={
                "report_id": report_id,
                "format": config.format,
                "file_size": file_size,
            },
        )

        return GeneratedReport(
            report_id=report_id,
            file_path=file_path,
            file_format=config.format,
            file_size_bytes=file_size,
            generated_at=timestamp,
        )

    async def preview_report(
        self,
        config: ReportConfig,
        data: dict[str, Any],
    ) -> bytes:
        """Generate preview PDF without saving.

        Args:
            config: Report configuration
            data: Data to include in report

        Returns:
            PDF bytes for preview
        """
        html_content = await self._html.render(
            template_name=f"{config.template_type}.html",
            context={
                "config": config,
                "data": data,
                "generated_at": datetime.now(UTC),
                "include_charts": config.include_charts,
                "is_preview": True,
            },
        )

        return await self._pdf.html_to_pdf(html_content)
```

### 3. PDF Generator

```python
# libs/reporting/pdf_generator.py
"""PDF generation using WeasyPrint."""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from pathlib import Path

from weasyprint import CSS, HTML

logger = logging.getLogger(__name__)

STYLES_PATH = Path(__file__).parent / "static" / "styles.css"


class PDFGenerator:
    """Generates PDFs from HTML using WeasyPrint."""

    def __init__(self, css_path: Path | None = None):
        self._css_path = css_path or STYLES_PATH

    async def html_to_pdf(self, html_content: str) -> bytes:
        """Convert HTML to PDF bytes.

        Args:
            html_content: HTML string to convert

        Returns:
            PDF bytes
        """
        # Run in thread pool since WeasyPrint is blocking
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            partial(self._generate_pdf, html_content),
        )

    def _generate_pdf(self, html_content: str) -> bytes:
        """Synchronous PDF generation."""
        html = HTML(string=html_content)
        css = CSS(filename=str(self._css_path))

        return html.write_pdf(stylesheets=[css])
```

### 4. HTML Generator

```python
# libs/reporting/html_generator.py
"""HTML report generation using Jinja2."""

from __future__ import annotations

import base64
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
from jinja2 import Environment, FileSystemLoader

TEMPLATES_PATH = Path(__file__).parent / "templates"


class HTMLGenerator:
    """Generates HTML reports from Jinja2 templates."""

    def __init__(self, templates_path: Path | None = None):
        self._env = Environment(
            loader=FileSystemLoader(templates_path or TEMPLATES_PATH),
            autoescape=True,
        )
        self._env.filters["format_number"] = self._format_number
        self._env.filters["format_pct"] = self._format_pct
        self._env.filters["format_date"] = self._format_date

    async def render(
        self,
        template_name: str,
        context: dict[str, Any],
    ) -> str:
        """Render template with context.

        Args:
            template_name: Template filename
            context: Template context variables

        Returns:
            Rendered HTML string
        """
        template = self._env.get_template(template_name)

        # Convert any Plotly figures to static images
        if context.get("include_charts"):
            context = self._embed_charts(context)

        return template.render(**context)

    def _embed_charts(self, context: dict[str, Any]) -> dict[str, Any]:
        """Convert Plotly figures to base64 images for embedding."""
        charts = context.get("charts", {})
        embedded = {}

        for name, fig in charts.items():
            if isinstance(fig, go.Figure):
                img_bytes = fig.to_image(format="png", width=800, height=400)
                b64 = base64.b64encode(img_bytes).decode()
                embedded[name] = f"data:image/png;base64,{b64}"
            else:
                embedded[name] = fig

        context["charts"] = embedded
        return context

    @staticmethod
    def _format_number(value: float) -> str:
        """Format number with commas."""
        return f"{value:,.2f}"

    @staticmethod
    def _format_pct(value: float) -> str:
        """Format as percentage."""
        return f"{value:.2%}"

    @staticmethod
    def _format_date(value: date) -> str:
        """Format date."""
        return value.strftime("%Y-%m-%d")
```

### 5. Report Service

```python
# apps/web_console/services/report_service.py
"""Service layer for report management."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from libs.alerts.delivery_service import AlertDeliveryService
from libs.reporting.report_generator import GeneratedReport, ReportConfig, ReportGenerator

logger = logging.getLogger(__name__)


@dataclass
class ReportTemplate:
    """Report template configuration."""
    id: UUID
    name: str
    description: str | None
    template_type: str
    config: dict[str, Any]
    created_by: UUID
    created_at: datetime


@dataclass
class ReportSchedule:
    """Report schedule configuration."""
    id: UUID
    template_id: UUID
    schedule_type: str  # daily, weekly, monthly
    schedule_config: dict[str, Any]
    recipients: list[str]
    enabled: bool
    last_run_at: datetime | None
    next_run_at: datetime | None


class ReportService:
    """Manages report templates, schedules, and generation."""

    def __init__(
        self,
        db_pool,
        report_generator: ReportGenerator,
        delivery_service: AlertDeliveryService,
    ):
        self._db = db_pool
        self._generator = report_generator
        self._delivery = delivery_service

    async def create_template(
        self,
        name: str,
        template_type: str,
        config: dict[str, Any],
        created_by: UUID,
        description: str | None = None,
    ) -> ReportTemplate:
        """Create a new report template."""
        async with self._db.connection() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO report_templates (name, description, template_type, config, created_by)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id, created_at
                """,
                name,
                description,
                template_type,
                config,
                created_by,
            )

            return ReportTemplate(
                id=row["id"],
                name=name,
                description=description,
                template_type=template_type,
                config=config,
                created_by=created_by,
                created_at=row["created_at"],
            )

    async def create_schedule(
        self,
        template_id: UUID,
        schedule_type: str,
        schedule_config: dict[str, Any],
        recipients: list[str],
        created_by: UUID,
    ) -> ReportSchedule:
        """Create a report schedule."""
        next_run = self._calculate_next_run(schedule_type, schedule_config)

        async with self._db.connection() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO report_schedules
                (template_id, schedule_type, schedule_config, recipients, next_run_at, created_by)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                template_id,
                schedule_type,
                schedule_config,
                recipients,
                next_run,
                created_by,
            )

            return ReportSchedule(
                id=row["id"],
                template_id=template_id,
                schedule_type=schedule_type,
                schedule_config=schedule_config,
                recipients=recipients,
                enabled=True,
                last_run_at=None,
                next_run_at=next_run,
            )

    async def run_schedule(self, schedule_id: UUID) -> GeneratedReport:
        """Execute a scheduled report with idempotency protection.

        Uses report_schedule_runs table to prevent duplicate executions.
        """
        today = datetime.now(UTC).date()
        run_key = f"{schedule_id}:{today.isoformat()}"

        async with self._db.connection() as conn:
            # Idempotency check: prevent duplicate runs
            existing = await conn.fetchrow(
                "SELECT id, status FROM report_schedule_runs WHERE run_key = $1",
                run_key,
            )
            if existing:
                if existing["status"] == "completed":
                    raise ValueError(f"Schedule {schedule_id} already ran today")
                elif existing["status"] == "running":
                    raise ValueError(f"Schedule {schedule_id} is already running")
                # If failed, allow retry by continuing

            # Record run start
            run_id = await conn.fetchval(
                """
                INSERT INTO report_schedule_runs (schedule_id, run_key, status, started_at)
                VALUES ($1, $2, 'running', NOW())
                ON CONFLICT (run_key) DO UPDATE SET status = 'running', started_at = NOW()
                RETURNING id
                """,
                schedule_id,
                run_key,
            )

        # Execute report generation (outside transaction for proper error handling)
        try:
            report = await self._execute_schedule(schedule_id)

            # Mark as completed
            async with self._db.connection() as conn:
                await conn.execute(
                    """
                    UPDATE report_schedule_runs
                    SET status = 'completed', completed_at = NOW(), archive_id = $2
                    WHERE id = $1
                    """,
                    run_id,
                    UUID(report.report_id),
                )

            return report

        except Exception as e:
            # Mark as failed
            async with self._db.connection() as conn:
                await conn.execute(
                    """
                    UPDATE report_schedule_runs
                    SET status = 'failed', completed_at = NOW(), error_message = $2
                    WHERE id = $1
                    """,
                    run_id,
                    str(e),
                )
            raise

    async def _execute_schedule(self, schedule_id: UUID) -> GeneratedReport:
        """Internal: Execute schedule without idempotency (called by run_schedule)."""
        async with self._db.connection() as conn:
            schedule = await conn.fetchrow(
                """
                SELECT s.*, t.template_type, t.config
                FROM report_schedules s
                JOIN report_templates t ON s.template_id = t.id
                WHERE s.id = $1
                """,
                schedule_id,
            )

            if not schedule:
                raise ValueError(f"Schedule {schedule_id} not found")

            # Generate report
            config = ReportConfig(
                template_type=schedule["template_type"],
                metrics=schedule["config"].get("metrics", []),
                strategies=schedule["config"].get("strategies", []),
                date_range=self._resolve_date_range(schedule["config"].get("date_range")),
                format=schedule["config"].get("format", "pdf"),
                include_charts=schedule["config"].get("include_charts", True),
            )

            data = await self._fetch_report_data(config)
            report = await self._generator.generate_report(config, data)

            # Archive
            await conn.execute(
                """
                INSERT INTO report_archives
                (schedule_id, template_id, generated_at, file_path, file_format, file_size_bytes)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                schedule_id,
                schedule["template_id"],
                report.generated_at,
                str(report.file_path),
                report.file_format,
                report.file_size_bytes,
            )

            # Deliver via email
            # NOTE: AlertDeliveryService (from T7.5) may need enhancement for attachments.
            # Options for email attachments:
            # 1. Extend AlertDeliveryService.send_email() to accept attachments parameter
            # 2. Use dedicated email service (e.g., SendGrid, SES) with attachment support
            # 3. Include download link in email body instead of attachment
            # For MVP, include report download link; full attachment support is stretch.
            report_url = f"/reports/archive/{report.report_id}"
            for recipient in schedule["recipients"]:
                await self._delivery.send_email(
                    to=recipient,
                    subject=f"Trading Report: {schedule['template_type']}",
                    body=f"Your scheduled report is ready.\n\nDownload: {report_url}",
                    # attachments=[report.file_path],  # TODO: Implement attachment support
                )

            # Update schedule
            next_run = self._calculate_next_run(
                schedule["schedule_type"],
                schedule["schedule_config"],
            )
            await conn.execute(
                """
                UPDATE report_schedules
                SET last_run_at = $1, next_run_at = $2
                WHERE id = $3
                """,
                datetime.now(UTC),
                next_run,
                schedule_id,
            )

            return report

    async def preview_report(self, template_id: UUID) -> bytes:
        """Generate preview PDF for template."""
        async with self._db.connection() as conn:
            template = await conn.fetchrow(
                "SELECT * FROM report_templates WHERE id = $1",
                template_id,
            )

            if not template:
                raise ValueError(f"Template {template_id} not found")

            config = ReportConfig(
                template_type=template["template_type"],
                metrics=template["config"].get("metrics", []),
                strategies=template["config"].get("strategies", []),
                date_range=self._resolve_date_range(template["config"].get("date_range")),
                format="pdf",
                include_charts=template["config"].get("include_charts", True),
            )

            data = await self._fetch_report_data(config)
            return await self._generator.preview_report(config, data)

    def _calculate_next_run(
        self,
        schedule_type: str,
        config: dict[str, Any],
    ) -> datetime:
        """Calculate next run time based on schedule."""
        now = datetime.now(UTC)

        if schedule_type == "daily":
            hour = config.get("hour", 18)
            next_run = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)

        elif schedule_type == "weekly":
            day_of_week = config.get("day_of_week", 1)  # Monday
            hour = config.get("hour", 6)
            days_ahead = day_of_week - now.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            next_run = (now + timedelta(days=days_ahead)).replace(
                hour=hour, minute=0, second=0, microsecond=0
            )

        elif schedule_type == "monthly":
            day_of_month = config.get("day_of_month", 1)
            hour = config.get("hour", 6)
            if now.day >= day_of_month:
                # Next month
                if now.month == 12:
                    next_run = now.replace(year=now.year + 1, month=1, day=day_of_month)
                else:
                    next_run = now.replace(month=now.month + 1, day=day_of_month)
            else:
                next_run = now.replace(day=day_of_month)
            next_run = next_run.replace(hour=hour, minute=0, second=0, microsecond=0)

        else:
            raise ValueError(f"Unknown schedule type: {schedule_type}")

        return next_run

    def _resolve_date_range(self, config: str | dict) -> tuple[date, date]:
        """Resolve date range from config."""
        today = date.today()

        if isinstance(config, dict):
            return (
                date.fromisoformat(config["start"]),
                date.fromisoformat(config["end"]),
            )

        ranges = {
            "last_7_days": (today - timedelta(days=7), today),
            "last_30_days": (today - timedelta(days=30), today),
            "last_90_days": (today - timedelta(days=90), today),
            "mtd": (today.replace(day=1), today),
            "ytd": (date(today.year, 1, 1), today),
        }

        return ranges.get(config, (today - timedelta(days=30), today))

    async def _fetch_report_data(self, config: ReportConfig) -> dict[str, Any]:
        """Fetch data for report generation."""
        # TODO: Implement data fetching from performance, risk, etc.
        return {
            "metrics": {},
            "charts": {},
            "tables": {},
        }
```

### 6. Report Page

```python
# apps/web_console/pages/reports.py
"""Scheduled Reports page."""

from __future__ import annotations

import os

import streamlit as st

from apps.web_console.auth import get_current_user
from apps.web_console.auth.permissions import Permission, has_permission
from apps.web_console.auth.streamlit_helpers import requires_auth
from apps.web_console.services.report_service import ReportService

FEATURE_REPORTS = os.getenv("FEATURE_REPORTS", "false").lower() in {
    "1", "true", "yes", "on",
}


@requires_auth
def main() -> None:
    st.set_page_config(page_title="Scheduled Reports", page_icon="📊", layout="wide")
    st.title("Scheduled Reports")

    if not FEATURE_REPORTS:
        st.info("Feature not available.")
        return

    user = get_current_user()
    can_view = has_permission(user, Permission.VIEW_REPORTS)
    can_manage = has_permission(user, Permission.MANAGE_REPORTS)

    if not can_view:
        st.error("Permission denied: VIEW_REPORTS required.")
        st.stop()

    tab1, tab2, tab3 = st.tabs(["Templates", "Schedules", "Archive"])

    with tab1:
        _render_templates_tab(can_manage)

    with tab2:
        _render_schedules_tab(can_manage)

    with tab3:
        _render_archive_tab()


def _render_templates_tab(can_manage: bool) -> None:
    """Render templates management tab."""
    st.subheader("Report Templates")

    if can_manage:
        with st.expander("Create New Template"):
            name = st.text_input("Template Name")
            template_type = st.selectbox(
                "Type",
                ["daily_summary", "weekly_performance", "monthly_pnl"],
            )

            # Metrics selection
            st.write("Select Metrics")
            col1, col2 = st.columns(2)
            with col1:
                total_pnl = st.checkbox("Total P&L", value=True)
                sharpe = st.checkbox("Sharpe Ratio", value=True)
            with col2:
                max_dd = st.checkbox("Max Drawdown", value=True)
                win_rate = st.checkbox("Win Rate")

            if st.button("Create Template"):
                st.success(f"Template '{name}' created!")

    # List existing templates
    st.divider()
    st.write("Existing Templates")
    st.info("Templates would be listed here.")


def _render_schedules_tab(can_manage: bool) -> None:
    """Render schedules management tab."""
    st.subheader("Report Schedules")

    if can_manage:
        with st.expander("Create New Schedule"):
            template = st.selectbox("Template", ["daily_summary", "weekly_performance"])

            schedule_type = st.radio("Frequency", ["daily", "weekly", "monthly"])

            if schedule_type == "daily":
                hour = st.slider("Time (UTC)", 0, 23, 18)
            elif schedule_type == "weekly":
                day = st.selectbox("Day", ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"])
                hour = st.slider("Time (UTC)", 0, 23, 6)
            else:
                day = st.number_input("Day of Month", 1, 28, 1)
                hour = st.slider("Time (UTC)", 0, 23, 6)

            recipients = st.text_area("Recipients (one per line)")

            if st.button("Create Schedule"):
                st.success("Schedule created!")

    # List schedules
    st.divider()
    st.write("Active Schedules")
    st.info("Schedules would be listed here.")


def _render_archive_tab() -> None:
    """Render archive viewer tab."""
    st.subheader("Report Archive")

    # Date filter
    col1, col2 = st.columns(2)
    with col1:
        start = st.date_input("From")
    with col2:
        end = st.date_input("To")

    st.info("Archived reports would be listed here with download links.")


if __name__ == "__main__":
    main()
```

---

## Testing Strategy

### Unit Tests

```python
# tests/libs/reporting/test_report_generator.py

import pytest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from libs.reporting.report_generator import ReportConfig, ReportGenerator


@pytest.fixture
def mock_html_generator():
    gen = MagicMock()
    gen.render = AsyncMock(return_value="<html>Test</html>")
    return gen


@pytest.fixture
def mock_pdf_generator():
    gen = MagicMock()
    gen.html_to_pdf = AsyncMock(return_value=b"%PDF-1.4 test")
    return gen


@pytest.mark.asyncio
async def test_generate_pdf_report(mock_html_generator, mock_pdf_generator, tmp_path):
    generator = ReportGenerator(mock_html_generator, mock_pdf_generator, tmp_path)

    config = ReportConfig(
        template_type="daily_summary",
        metrics=["total_pnl"],
        strategies=["alpha"],
        date_range=(date(2024, 1, 1), date(2024, 1, 31)),
        format="pdf",
    )

    report = await generator.generate_report(config, {"data": "test"})

    assert report.file_format == "pdf"
    assert report.file_path.suffix == ".pdf"
    assert report.file_size_bytes > 0
```

### Golden Tests

```python
# tests/libs/reporting/test_pdf_golden.py

import pytest
from pathlib import Path

GOLDEN_DIR = Path(__file__).parent / "golden_reports"


@pytest.mark.slow
def test_pdf_output_matches_golden():
    """Verify PDF output matches golden fixture."""
    # Generate PDF
    # Compare with golden file
    # Allow small differences (timestamps, etc.)
    pass
```

---

## Deliverables

1. **ReportGenerator:** Core report generation orchestration
2. **PDFGenerator:** WeasyPrint PDF generation
3. **HTMLGenerator:** Jinja2 HTML rendering
4. **ReportService:** Template, schedule, and archive management
5. **APScheduler Integration:** Scheduler for periodic reports (DB-persisted)
6. **Reports Page:** Streamlit UI
7. **Database Migration:** 0018_create_report_tables.sql
8. **Tests:** Unit and golden tests
9. **Documentation:** `docs/CONCEPTS/reporting.md`, `docs/ADRs/ADR-0030-reporting-architecture.md`

---

## Verification Checklist

- [ ] PDF generation works with WeasyPrint
- [ ] HTML generation with embedded charts
- [ ] Schedule creation and execution
- [ ] Email delivery with attachments
- [ ] Archive retrieval and download
- [ ] Preview functionality
- [ ] 90-day retention enforced
- [ ] RBAC enforcement tested
- [ ] All tests pass
