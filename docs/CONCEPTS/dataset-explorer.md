# Dataset Explorer

## Overview
The Dataset Explorer provides governed access to licensed datasets for inspection, ad-hoc querying, and exports. Queries are read-only, scoped to a single dataset, and validated before execution to prevent cross-dataset access or unsafe functions.

## Key Capabilities
- Dataset catalog: browse available datasets filtered by permissions.
- Schema viewer: inspect column names and types.
- Data preview: view the first N rows (bounded limit).
- Query editor: run validated, read-only SQL.
- Exports: generate CSV or Parquet exports with row limits and expiration.
- Coverage timeline: visualize available date ranges.

## Permissions and Access Control
- VIEW_DATA_SYNC: required to browse datasets.
- QUERY_DATA: required to execute SQL queries.
- EXPORT_DATA: required to export results.
- Dataset-level access is enforced for every query and export.

## Query Validation Rules
- Single SELECT statement only.
- Multi-statement queries are rejected.
- Cross-dataset table references are blocked.
- Dangerous functions (read_parquet, read_csv, etc.) are denied.

## Rate Limits and Guardrails
- Queries: 10 per minute per user.
- Exports: 5 per hour per user.
- Preview limit: max 1000 rows.
- Export limit: max 100,000 rows (enforced server-side).

## Related Docs
- docs/RUNBOOKS/dataset-explorer-ops.md
- docs/CONCEPTS/data-sync-operations.md
