# Ops Runbook

## Daily
- `make status` — check positions, open orders, P&L
- Review Grafana alerts (data freshness, API errors, DD)
- Check System Health dashboard for service status

## Incidents
- **Trip breaker:** `make circuit-trip`
- **Flatten:** `make kill-switch`
- **Data stale:** run replay test; switch to DRY_RUN if needed

## Recovery
- Restart services; ensure reconciler completes; verify breaker state is OPEN.

---

## Alert Operations

### Alert Routing Configuration
- Alert rules are configured via Web Console → Alerts
- Channels: Email (SMTP/SendGrid), Slack (webhook), SMS (Twilio)
- Rate limits enforced automatically (see channel-specific limits)

### Alert Troubleshooting
1. **Check poison queue:**
   ```sql
   SELECT id, channel, recipient, error_message, retry_count
   FROM alert_deliveries
   WHERE status = 'poison'
   ORDER BY failed_at DESC;
   ```

2. **Review delivery failures in Grafana:**
   - Dashboard: Track7 SLO
   - Panel: `alert_delivery_latency_seconds` histogram
   - Alert: `AlertDeliveryLatencyHigh` (P95 > 60s)

3. **Retry failed delivery:**
   ```sql
   UPDATE alert_deliveries
   SET status = 'pending', retry_count = 0
   WHERE id = :delivery_id AND status = 'poison';
   ```

4. **Common failure causes:**
   - Invalid recipient (email/phone format)
   - Webhook URL unreachable (Slack)
   - Rate limit exceeded at provider
   - Network timeout

---

## System Health Monitor Operations

### Accessing System Health Dashboard
1. Navigate to Web Console → System Health
2. Dashboard shows all microservices, Redis, and Postgres status
3. Auto-refreshes every 10 seconds

### Interpreting Status Indicators
| Icon | Status | Action Required |
|------|--------|-----------------|
| Green | Healthy | None |
| Yellow | Degraded | Monitor closely, investigate latency |
| Red | Unhealthy | Immediate investigation |
| Gray (stale) | Cached | Dashboard using old data, check connectivity |

### Troubleshooting Service Issues
1. Check service status in dashboard for error messages
2. Review Prometheus metrics for latency trends:
   - `execution_gateway_request_duration_seconds`
   - `signal_service_latency_seconds`
3. Check service logs: `docker logs <service_name>`
4. Verify Redis/Postgres connectivity in dashboard
5. If staleness indicator shows, dashboard is using cached data

### Graceful Degradation
- If health fetch fails, dashboard shows last known status with staleness warning
- Refresh interval: 10 seconds (configurable via `AUTO_REFRESH_INTERVAL`)
- Queue depth metrics require Redis connectivity

---

## Circuit Breaker Operations (UI)

### Trip Circuit Breaker via UI
1. Navigate to Web Console → Circuit Breaker
2. Click **"Trip Circuit Breaker"**
3. Enter reason (minimum 20 characters)
4. Check acknowledgment box
5. Confirm action

**Impact:** All new trading orders blocked immediately.

### Reset Circuit Breaker via UI
1. Navigate to Web Console → Circuit Breaker
2. Verify all conditions are normalized (displayed on page)
3. Click **"Reset Circuit Breaker"**
4. Enter reason (minimum 20 characters)
5. Check acknowledgment box
6. Confirm action
7. **Rate limit:** 1 reset per minute (global)

### CLI Commands (Alternative)
- **Trip:** `make circuit-trip`
- **Status:** Check Redis key `circuit_breaker:state`
- **History:** Query `circuit_breaker:history` sorted set

### Verification Checklist
After reset, verify:
- [ ] CB state shows OPEN in dashboard
- [ ] No pending orders blocked
- [ ] Audit log shows reset entry
- [ ] Grafana shows `cb_staleness_seconds` near 0

---

## Admin Dashboard Operations

### API Key Management
1. Navigate to Web Console → Admin Dashboard → API Keys
2. **Create:** Click "New Key", enter name and scopes
3. **Rotate:** Click "Rotate" on existing key (24h grace period)
4. **Revoke:** Click "Revoke" (immediate, irreversible)

### Configuration Changes
1. Navigate to Web Console → Admin Dashboard → Configuration
2. Select setting to modify
3. Enter new value
4. Review change preview
5. Click "Apply Changes"
6. Changes propagate via Redis pub/sub (~5s)

### Audit Log Review
1. Navigate to Web Console → Admin Dashboard → Audit Log
2. Use filters: date range, action type, user
3. Export to CSV if needed (max 10,000 rows)

---

## SLA Monitoring

### Track 7 SLA Targets
| Metric | Target | Alert Threshold |
|--------|--------|-----------------|
| CB staleness | ≤ 5s | > 10s (page) |
| Alert delivery P95 | < 60s | > 60s (Slack) |
| Audit write P95 | < 1s | > 1s (Slack) |
| Poison queue size | 0 | > 10 (page) |

### Alert Escalation
1. **Warning alerts** → #alerts-ops Slack channel
2. **Critical alerts** → PagerDuty (platform-team)

### SLA Breach Response
1. Acknowledge alert in PagerDuty/Slack
2. Check Grafana dashboard for context
3. Review logs for root cause
4. Apply fix or escalate to engineering
5. Document in incident report
