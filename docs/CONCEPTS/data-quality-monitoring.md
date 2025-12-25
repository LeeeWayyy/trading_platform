# Data Quality Monitoring

## Overview
Data quality monitoring tracks the health, completeness, and correctness of ingested datasets. The web console surfaces validation results, anomaly alerts, and trend views while enforcing dataset-level access.

## Core Concepts
- Validation results: structured checks (row counts, schema rules, value bounds) per sync run.
- Anomaly alerts: threshold-based detections for sudden changes or gaps.
- Acknowledgments: operator confirmation that an alert has been reviewed (idempotent).
- Trends: historical metrics to spot degradation over time.
- Quarantine: isolation of suspect data pending investigation and remediation.

## Permissions and Access Control
- VIEW_DATA_QUALITY: required to view validation results and alerts.
- ACKNOWLEDGE_ALERTS: required to acknowledge alerts.
- Dataset-level access is enforced for all read paths.

## Alert Lifecycle
1. Validation detects anomaly and creates an alert.
2. Operator reviews details and acknowledges with a reason.
3. Acknowledgment is stored and visible to all authorized users.

## Related Docs
- docs/RUNBOOKS/data-quality-ops.md
- docs/CONCEPTS/data-sync-operations.md
