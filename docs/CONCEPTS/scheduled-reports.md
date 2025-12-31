# Scheduled Reports

## Plain English Overview
Scheduled reports let users configure recurring reports (daily summaries, weekly
performance, risk snapshots) that run automatically and generate downloadable
artifacts. The web console UI stores the schedule configuration in Postgres and
surfaces run history and archives for retrieval.

## Cron Format
Schedules use a standard 5-field cron expression (minute, hour, day-of-month,
month, day-of-week). The cron expression is stored inside `schedule_config` on
`report_schedules` and is interpreted by the scheduler service. Example:

- `0 6 * * *` - every day at 06:00 UTC
- `0 7 * * 1` - every Monday at 07:00 UTC

## Report Types
`template_type` identifies which report template to render. The MVP uses simple
string identifiers, for example:

- `daily_summary`
- `weekly_performance`
- `risk_snapshot`
- `custom`

Additional report templates can be introduced without schema changes by adding
new `template_type` values and handling them in the reporting service.

## Parameters
Each schedule stores a JSON payload in `schedule_config.params`. These
parameters are passed into report generation (for example: strategy filters,
benchmark selection, or formatting options).

## Archive Storage
Generated reports are tracked in `report_archives` with file metadata, while the
report files themselves live on disk (default `artifacts/reports`) or an
external object store in future iterations. The web console only serves files
that are inside the configured report output directory to avoid path traversal.

Run history is tracked in `report_schedule_runs`, which captures status, timing,
and error details for each scheduled execution.
