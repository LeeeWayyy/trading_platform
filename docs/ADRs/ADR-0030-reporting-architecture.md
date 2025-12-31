# ADR-0030: Reporting Architecture

## Status
Proposed

## Context
The trading platform requires a flexible reporting system to generate and distribute periodic reports (daily summaries, performance reviews, tax lots) to researchers and stakeholders. These reports need to be:
- Scheduled (daily, weekly, monthly)
- Formatted as PDF (primary) or HTML
- Distributed via email
- Archived for audit/compliance

We already have an alerting system (`libs/alerts`) that handles channel delivery (email, slack) with retries and rate limiting.

## Decision
We will implement a reporting service that leverages the existing alerting infrastructure for delivery while adding dedicated components for generation and scheduling.

### 1. Report Generation
- **HTML Generation**: Use `Jinja2` templates for structural flexibility.
- **Charts**: Use `Plotly` to generate static images (PNG) embedded in HTML.
- **PDF Generation**: Use `WeasyPrint` to convert the rendered HTML+Images into high-fidelity PDFs.
- **Data Source**: A `ReportService` will orchestrate data fetching from existing services (Performance, Risk, Tax).

### 2. Scheduling
- Use `Celery Beat` (or `APScheduler` if lightweight preferred, but Celery is consistent with worker architecture) to trigger report generation jobs.
- Schedule configuration stored in `report_schedules` table (PostgreSQL).

### 3. Distribution
- Reuse `libs/alerts/delivery_service.py` (`DeliveryExecutor`).
- **Extension**: Update `DeliveryExecutor` and `EmailChannel` to support file attachments (Done in C0).
- Reports are generated as temporary files (or S3 blobs), attached to the email, and then archived.

### 4. Archival
- Store generated reports (PDFs) in an object store (S3-compatible) or local filesystem for prototype.
- Track metadata in `report_archives` table (path, generation time, recipient list).

## Consequences

### Positive
- Reuses existing robust delivery infrastructure (retries, rate limits).
- Decouples generation (compute intensive) from delivery (I/O intensive).
- PDF via HTML/CSS (WeasyPrint) allows easier styling than programmatic PDF builders (ReportLab).

### Negative
- `WeasyPrint` introduces OS-level dependencies (pango, cairo) which complicates Docker builds.
- Email attachment size limits must be managed.

## Compliance
- **Retention**: Reports must be retained for 90 days (configurable).
- **Security**: Reports containing PII or sensitive PnL must be access-controlled in archive.
