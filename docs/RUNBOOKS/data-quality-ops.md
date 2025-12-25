# Data Quality Operations Runbook

## Common Issues & Solutions
- Alert flood: confirm thresholds and recent dataset changes; consider temporary suppression if verified benign.
- False positives: acknowledge with reason and track the rule for tuning.
- Missing alerts: verify validation jobs are running and data sync is healthy.
- Unable to acknowledge: confirm ACKNOWLEDGE_ALERTS permission and dataset access.

## Troubleshooting Steps
1. Verify VIEW_DATA_QUALITY permission for the user.
2. Review Validation Results for the dataset to identify the triggering run.
3. Inspect anomaly alert details (metric, deviation, expected vs actual).
4. Check recent sync logs for ingestion failures or partial loads.
5. If anomaly persists, compare against historical trends for baseline drift.

## Escalation Procedures
- Escalate to data engineering if:
  - Anomalies persist across multiple syncs.
  - Validation failures block downstream jobs.
- Escalate to model research if:
  - Data quality degradation materially affects signals.
- Escalate to compliance if:
  - Dataset scope or licensing constraints appear violated.

## Related Commands/Tools
- `streamlit run apps/web_console/app.py --server.port 8501`
- `make status` (verify platform health)
- `tail -n 200 logs/*.log` (scan validation/anomaly logs)
