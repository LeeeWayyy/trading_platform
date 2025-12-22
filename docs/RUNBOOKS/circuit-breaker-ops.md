# Circuit Breaker Operations Runbook

## Overview

This runbook covers operational procedures for the trading platform's circuit breaker mechanism. The circuit breaker is a critical safety feature that halts trading when triggered, either automatically (by risk thresholds) or manually (by operators).

## Quick Reference

| Command | Description |
|---------|-------------|
| `make circuit-trip` | Trip via CLI |
| `make kill-switch` | Full kill switch (trip + flatten) |
| Web Console → Circuit Breaker | UI-based operations |

## States

| State | Description | Trading Allowed |
|-------|-------------|-----------------|
| `OPEN` | Normal operation | Yes |
| `TRIPPED` | Halted | No (risk-reducing only) |
| `QUIET_PERIOD` | Cooling down after reset | Limited |

---

## CLI Operations

### Trip via CLI

```bash
# Trip with reason
make circuit-trip

# Direct command
PYTHONPATH=. python -c "
from libs.redis_client import RedisClient
from libs.risk_management.breaker import CircuitBreaker

redis = RedisClient(host='localhost', port=6379)
cb = CircuitBreaker(redis)
cb.trip('MANUAL', details={'operator': 'your-name', 'reason': 'describe reason'})
print('Circuit breaker TRIPPED')
"
```

### Check Status via CLI

```bash
# Redis CLI
redis-cli GET circuit_breaker:state | python -m json.tool

# Python
PYTHONPATH=. python -c "
from libs.redis_client import RedisClient
from libs.risk_management.breaker import CircuitBreaker

redis = RedisClient(host='localhost', port=6379)
cb = CircuitBreaker(redis)
status = cb.get_status()
print(f'State: {status[\"state\"]}')
print(f'Tripped at: {status.get(\"tripped_at\", \"N/A\")}')
print(f'Reason: {status.get(\"trip_reason\", \"N/A\")}')
"
```

### Reset via CLI

```bash
PYTHONPATH=. python -c "
from libs.redis_client import RedisClient
from libs.risk_management.breaker import CircuitBreaker

redis = RedisClient(host='localhost', port=6379)
cb = CircuitBreaker(redis)
cb.reset(reset_by='your-name')
print('Circuit breaker RESET to OPEN')
"
```

---

## UI Operations

### Trip via Web Console

1. Navigate to **Web Console → Circuit Breaker**
2. Verify current state is OPEN
3. Click **"Trip Circuit Breaker"**
4. Enter reason (minimum 20 characters)
   - Example: "Unusual market volatility detected, halting for review"
5. Check acknowledgment box
6. Click **"Confirm Trip"**
7. Verify state changes to TRIPPED

### Reset via Web Console

**Prerequisites:**
- Current state must be TRIPPED
- Conditions must be normalized (displayed on page)
- You must have `RESET_CIRCUIT` permission

**Steps:**
1. Navigate to **Web Console → Circuit Breaker**
2. Review condition indicators (all should be green/yellow)
3. Click **"Reset Circuit Breaker"**
4. Enter reason (minimum 20 characters)
   - Example: "Market conditions normalized, system health verified, resuming trading"
5. Check acknowledgment box
6. Click **"Confirm Reset"**
7. Verify state changes to OPEN

**Rate Limit:** Only 1 reset per minute (global). If rate limited, wait and retry.

---

## Status Verification

### Via Dashboard

Check the Circuit Breaker page for:
- Current state (OPEN/TRIPPED/QUIET_PERIOD)
- Trip reason (if tripped)
- Last trip/reset timestamps
- Recent history

### Via Grafana

Dashboard: **Track7 SLO**

Key panels:
- `cb_staleness_seconds` gauge (0 = Verified, 999999 = FAILED)
- Circuit breaker state history
- Trip/reset event annotations

**Note:** The CB staleness metric uses binary semantics:
- `0` = CB state successfully verified in this scrape
- `999999` = Verification failed (Redis unavailable or CB state missing)

### Via Redis

```bash
# Current state
redis-cli GET circuit_breaker:state

# Recent history (last 10)
redis-cli ZREVRANGE circuit_breaker:history 0 9 WITHSCORES
```

---

## Recovery Checklist

After a circuit breaker trip, follow this checklist before resetting:

### 1. Identify Root Cause
- [ ] Review trip reason and details
- [ ] Check Grafana for anomalies at trip time
- [ ] Review logs: `docker logs execution_gateway`

### 2. Verify Conditions Normalized
- [ ] Drawdown within limits
- [ ] Position sizes within limits
- [ ] No API errors in last 5 minutes
- [ ] Redis connectivity healthy
- [ ] PostgreSQL connectivity healthy

### 3. System Health Check
- [ ] All services showing green in System Health dashboard
- [ ] No queue backlogs (check queue depth metrics)
- [ ] Latencies within normal range

### 4. Pre-Reset Actions
- [ ] Notify relevant team members (Slack/PagerDuty)
- [ ] Document root cause in incident report
- [ ] Confirm no pending conflicting operations

### 5. Reset and Verify
- [ ] Reset via UI or CLI
- [ ] Verify state is OPEN
- [ ] Check audit log for reset entry
- [ ] Monitor for 5 minutes for any issues

---

## Automatic Trips

The circuit breaker may trip automatically when:

| Condition | Threshold | Recovery |
|-----------|-----------|----------|
| Drawdown | > 5% | Market recovery + manual reset |
| API errors | > 10 in 5 min | Fix API issues + manual reset |
| Data staleness | > 30 min | Data feed recovery + manual reset |

### Handling Auto-Trips

1. **Do not immediately reset** — understand why it tripped
2. Review the `trip_reason` field in state
3. Check `trip_details` for additional context
4. Follow Recovery Checklist above

---

## Audit Log Review

All circuit breaker operations are logged. To review:

### Via Web Console
1. Navigate to **Admin Dashboard → Audit Log**
2. Filter by action: `CIRCUIT_BREAKER_TRIP` or `CIRCUIT_BREAKER_RESET`
3. Review details including user, timestamp, reason

### Via Database
```sql
SELECT
    timestamp,
    action,
    user_id,
    details,
    ip_address
FROM audit_log
WHERE action IN ('CIRCUIT_BREAKER_TRIP', 'CIRCUIT_BREAKER_RESET')
ORDER BY timestamp DESC
LIMIT 20;
```

---

## Troubleshooting

### "Rate limit exceeded" on Reset

**Cause:** Global rate limit of 1 reset/minute exceeded.

**Solution:** Wait 60 seconds and retry. The rate limit is intentional to prevent rapid reset/trip cycles.

### CB Verification Failed Alert

**Symptom:** `cb_staleness_seconds` = 999999 (CBVerificationFailed alert firing)

**Causes:**
- Metrics server not running
- Redis connectivity issue
- CB state missing from Redis
- Prometheus scrape failing

**Solution:**
1. Check metrics server: `curl http://localhost:8503/metrics`
2. Check Redis: `redis-cli PING`
3. Check CB state exists: `redis-cli GET circuit_breaker:state`
4. Check Prometheus targets: `http://localhost:9090/targets`

**Note:** The metric uses binary semantics (0 = verified, 999999 = failed). Any non-zero value indicates a verification problem.

### Reset Blocked with "Not TRIPPED"

**Cause:** Attempting to reset when state is already OPEN.

**Solution:** Check current state first. If state shows OPEN, no reset needed.

### RBAC Violation on Trip/Reset

**Cause:** User lacks required permission.

**Solution:**
1. Verify user role has `TRIP_CIRCUIT` or `RESET_CIRCUIT` permission
2. Contact admin to update role if needed
3. As workaround, use CLI with appropriate credentials

---

## Emergency Procedures

### Full Kill Switch (Trip + Flatten)

When you need to completely halt and flatten:

```bash
make kill-switch
```

This will:
1. Trip the circuit breaker
2. Cancel all open orders
3. Flatten all positions

**Use only in emergencies.** This is irreversible without manual intervention.

### Manual Redis Override

**Only use if UI and CLI are unavailable:**

```bash
redis-cli SET circuit_breaker:state '{"state":"TRIPPED","tripped_at":"2025-01-15T10:00:00Z","trip_reason":"EMERGENCY_MANUAL","trip_details":{"operator":"emergency"}}'
```

**Warning:** This bypasses audit logging. Document the action manually.

---

## Related Documentation

- [Circuit Breaker UI Concepts](../CONCEPTS/circuit-breaker-ui.md)
- [Risk Management](../CONCEPTS/risk-management.md)
- [General Ops Runbook](./ops.md)
