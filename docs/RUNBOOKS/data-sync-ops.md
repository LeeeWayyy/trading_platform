# Data Sync Operations Runbook

## Common Issues & Solutions
- Manual sync fails with rate limit error: wait 60 seconds and retry; confirm no repeated submissions.
- Missing datasets in UI: verify dataset-level permissions for the user role.
- Stale status timestamps: check sync scheduler health and upstream data availability.
- Validation status shows errors: review validation logs and quarantine entries.

## Troubleshooting Steps
1. Confirm user permissions: VIEW_DATA_SYNC and dataset-level access.
2. Open Data Sync Dashboard and review Sync Logs for errors.
3. Validate scheduler configuration (cron expression) in Schedule Config tab.
4. Check service logs for ingestion errors or upstream timeouts.
5. If validation failed, inspect quarantine location and compare to prior successful runs.

## Escalation Procedures
- Escalate to data engineering if:
  - Upstream provider is down or delivers corrupted files.
  - Schema changes are detected (breaking downstream consumers).
  - Sync failures persist across two scheduled windows.
- Escalate to security/compliance if dataset access appears incorrect.

## Related Commands/Tools
- `streamlit run apps/web_console/app.py --server.port 8501`
- `make up` (ensure infrastructure is running)
- `make status` (confirm system health)
- `tail -n 200 logs/*.log` (review recent sync errors)
