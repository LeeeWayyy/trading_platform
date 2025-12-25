# Dataset Explorer Operations Runbook

## Common Issues & Solutions
- Query rejected: ensure it is a single SELECT and uses only allowed tables.
- Cross-dataset error: limit queries to the selected dataset tables.
- Export rate limit exceeded: wait for the hourly window to reset.
- Preview shows no rows: confirm dataset has data and user has dataset access.

## Troubleshooting Steps
1. Verify permissions: VIEW_DATA_SYNC for browsing, QUERY_DATA for queries, EXPORT_DATA for exports.
2. Check query text for disallowed functions (read_parquet, read_csv, read_json).
3. Confirm table names match dataset allowlist for the selected dataset.
4. Reduce query scope (limit columns, add filters) if timeouts occur.
5. Validate export status and check if the file has expired.

## Escalation Procedures
- Escalate to data engineering if:
  - Valid queries consistently time out.
  - Dataset tables are missing or outdated.
- Escalate to security/compliance if:
  - Users report unexpected dataset visibility.

## Related Commands/Tools
- `streamlit run apps/web_console/app.py --server.port 8501`
- `make status` (platform health)
- `tail -n 200 logs/*.log` (query/export errors)
