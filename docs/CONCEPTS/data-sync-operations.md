# Data Sync Operations

## Overview
Data sync operations keep licensed datasets current and auditable. The web console provides read-only visibility into sync health plus controlled mechanisms to trigger or schedule syncs. All views and actions are constrained by dataset-level permissions to ensure licensing compliance.

## Core Concepts
- Sync status: last successful run, row count, validation status, and schema version per dataset.
- Sync logs: recent runs with status, errors, and metadata for troubleshooting.
- Schedules: cron-based definitions for automated runs, versioned for auditability.
- Manual triggers: operator-initiated incremental syncs with audit reason capture.

## Permissions and Access Control
- VIEW_DATA_SYNC: required to view status, logs, and schedules.
- TRIGGER_DATA_SYNC: required to manually trigger a sync.
- MANAGE_SYNC_SCHEDULE: required to edit schedules.
- Dataset-level access (e.g., dataset:crsp) is enforced on all reads and writes.

## Operational Flow
1. Scheduler enqueues a dataset sync at configured times.
2. Sync job runs incremental ingestion and validation.
3. Status + logs are updated for visibility.
4. Operators can trigger a manual sync when needed (rate-limited).

## Rate Limits and Guardrails
- Manual trigger is rate-limited (1 per minute per user) to prevent accidental overload.
- Syncs run in read-only mode for external sources; writes are scoped to internal storage.

## Failure Modes
- Validation failure: dataset may be quarantined pending investigation.
- Upstream outage: manual trigger may fail and surface error logs.
- Schema mismatch: surfaced in validation status for review.

## Related Docs
- docs/RUNBOOKS/data-sync-ops.md
- docs/CONCEPTS/data-quality-monitoring.md
