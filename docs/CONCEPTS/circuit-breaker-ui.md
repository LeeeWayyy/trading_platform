# Circuit Breaker Dashboard

## Overview

The Circuit Breaker Dashboard provides a web-based interface for monitoring and controlling the trading platform's circuit breaker mechanism. This critical safety feature allows operators to manually trip or reset the circuit breaker, view current status, and review trip/reset history.

## Architecture

```
+-------------------+     +------------------+     +-------+
| Streamlit UI      | --> | CB Service       | --> | Redis |
| (circuit_breaker  |     | (cb_service.py)  |     | State |
|  .py)             |     |                  |     |       |
+-------------------+     +------------------+     +-------+
        |                         |
        v                         v
+-------------------+     +------------------+
| Session State     |     | PostgreSQL       |
| (ack state)       |     | (audit_log)      |
+-------------------+     +------------------+
```

## Status Indicators

The circuit breaker has three possible states:

| State | Icon | Description |
|-------|------|-------------|
| **OPEN** | Green | Normal operation - trading allowed |
| **TRIPPED** | Red | Trading halted - requires manual reset |
| **QUIET_PERIOD** | Yellow | Cooling down after reset (optional) |

## RBAC Requirements

Access to the Circuit Breaker Dashboard is controlled by permissions:

| Action | Required Permission | Roles |
|--------|---------------------|-------|
| View status | `VIEW_CIRCUIT_BREAKER` | Viewer, Operator, Admin |
| Trip breaker | `TRIP_CIRCUIT` | Operator, Admin |
| Reset breaker | `RESET_CIRCUIT` | Operator, Admin |

The page is also gated by the `FEATURE_CIRCUIT_BREAKER` feature flag.

## Manual Trip Workflow

1. Navigate to **Circuit Breaker** page
2. Click **"Trip Circuit Breaker"** button
3. Enter a reason (minimum 20 characters)
4. Check the acknowledgment checkbox confirming you understand the impact
5. Click **"Confirm Trip"**

**Impact:** When tripped, all new trading orders are blocked. Only risk-reducing orders may be allowed.

## Manual Reset Workflow

1. Navigate to **Circuit Breaker** page
2. Verify all conditions are normalized (displayed on page)
3. Click **"Reset Circuit Breaker"** button
4. Enter a reason (minimum 20 characters)
5. Check the acknowledgment checkbox
6. Click **"Confirm Reset"**

**Rate Limit:** Only 1 reset per minute is allowed (global, not per-user).

## Step-Up Confirmation

Both trip and reset operations require:

1. **Reason text** - Minimum 20 characters explaining the action
2. **Acknowledgment checkbox** - Must be checked to enable confirm button
3. **Server-side validation** - Prevents bypassing client-side checks

This ensures operators consciously confirm critical actions.

## Rate Limiting

Reset operations are globally rate-limited to prevent accidental rapid resets:

- **Limit:** 1 reset per minute
- **Scope:** Global (all users share the same limit)
- **Implementation:** Redis-based with atomic INCR and TTL

If rate limit is exceeded, the UI displays an error and the operation is rejected.

## Audit Logging

All circuit breaker operations are logged to PostgreSQL:

```sql
INSERT INTO audit_log (
    timestamp, action, resource_type, resource_id,
    user_id, user_name, details, ip_address, outcome
) VALUES (
    NOW(), 'CIRCUIT_BREAKER_TRIP', 'circuit_breaker', 'global',
    :user_id, :user_name, :details_json, :ip, 'success'
)
```

Details include:
- Trip reason
- Tripped by user ID
- Reset reason (for reset actions)
- Reset by user ID

## Redis Key Schema

Circuit breaker state is stored in Redis:

| Key | Type | Description |
|-----|------|-------------|
| `circuit_breaker:state` | JSON | Current state and metadata |
| `circuit_breaker:history` | ZSET | Trip/reset history with timestamps |
| `circuit_breaker:reset_rate_limit` | String | Rate limit counter with TTL |

**State JSON structure:**
```json
{
    "state": "OPEN",
    "tripped_at": null,
    "trip_reason": null,
    "trip_details": null,
    "reset_at": "2025-01-15T10:30:00Z",
    "reset_by": "operator@example.com",
    "trip_count_today": 0
}
```

## Prometheus Metrics

The following metrics are exposed for monitoring:

| Metric | Type | Description |
|--------|------|-------------|
| `cb_status_checks_total` | Counter | Total CB status checks |
| `cb_trip_total` | Counter | Total trips (manual + auto) |
| `cb_reset_total` | Counter | Total resets |
| `cb_staleness_seconds` | Gauge | Time since last state verification |

## SLA Alerts

| Condition | Threshold | Action |
|-----------|-----------|--------|
| CB staleness high | > 10 seconds | Page on-call (PagerDuty) |

## Related Documentation

- [Circuit Breaker Operations Runbook](../RUNBOOKS/circuit-breaker-ops.md)
- [Risk Management Concepts](./risk-management.md)
- [ADR-0029: Alerting System](../ADRs/ADR-0029-alerting-system.md)
