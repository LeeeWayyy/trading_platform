# ADR-0029: Alerting System Architecture

## Status

ACCEPTED (2025-12-21)

**Accepted:** Track 7 (P4T5) alerting system implementation is complete.
Implementation PRs: #93, #95, #96, #97, #98

## Context

Track 7 (P4T5) requires a multi-channel alert delivery system for operational notifications.
The system must support email, Slack, and SMS delivery with:

- Idempotent delivery (no duplicate alerts)
- Rate limiting (per-channel, per-recipient, global)
- Retry with exponential backoff
- Poison queue for failed deliveries

This ADR documents the architectural decisions for the alerting subsystem.

## Decision

### Architecture Overview

The alerting system consists of:

1. **Alert Events** - Stored in `alert_events` table when conditions trigger
2. **Alert Rules** - User-configured conditions stored in `alert_rules` table
3. **Delivery Service** - Async worker processes delivery queue
4. **Channel Handlers** - Email, Slack, SMS with per-channel rate limits

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ Alert Rules │────▶│Alert Events │────▶│  Delivery   │
│  (Config)   │     │  (Trigger)  │     │   Queue     │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                               │
                    ┌──────────────────────────┼──────────────────────────┐
                    │                          │                          │
                    ▼                          ▼                          ▼
             ┌─────────────┐            ┌─────────────┐            ┌─────────────┐
             │    Email    │            │    Slack    │            │     SMS     │
             │  100/min    │            │   50/min    │            │   10/min    │
             └─────────────┘            └─────────────┘            └─────────────┘
```

### Channel Handlers

| Channel | Provider | Rate Limit | Retry Strategy |
|---------|----------|------------|----------------|
| Email | SMTP/SendGrid | 100/min | 1s, 2s, 4s (max 3) |
| Slack | Webhook | 50/min | 1s, 2s, 4s (max 3) |
| SMS | Twilio | 10/min | 1s, 2s, 4s (max 3) |

### Idempotency Model

Dedup key format: `{alert_id}:{channel}:{recipient}:{hour_bucket}`

- `alert_id` - UUID of the alert event
- `channel` - email, slack, or sms
- `recipient` - HMAC-SHA256 hashed recipient identifier
- `hour_bucket` - UTC ISO 8601 truncated to hour (e.g., `2025-12-18T14:00:00Z`)

**Important:** `hour_bucket` is derived from the **original alert trigger timestamp**, not current time. This ensures retries crossing hour boundaries remain idempotent.

### Rate Limiting

Rate limits are enforced using Redis token bucket pattern:

1. **Per-channel:** `ratelimit:{channel}:{minute}` - INCR + EXPIRE
2. **Per-recipient:** `ratelimit:recipient:{hash}:{hour}` - Max 5 email/3 SMS per hour
3. **Global burst:** `ratelimit:global:{minute}` - Max 500/min total

Recipient hashing uses HMAC-SHA256 with `ALERT_RECIPIENT_HASH_SECRET` env var.
The resulting hex digest is truncated to the first 16 characters to keep Redis
keys compact while still avoiding collisions for our expected recipient volume.

### Poison Queue

Deliveries that fail after 3 attempts are moved to poison queue:
- Status changed to `poison` in `alert_deliveries` table
- Metric `alert_poison_queue_size` exposed for alerting
- Alert rule: `alert_poison_queue_size > 10` → page on-call

### Queue Depth Protection

- Max queue depth: 10,000 pending deliveries
- When exceeded: HTTP 503 + `Retry-After: 60`
- Auto-resume when backlog < 8,000
- Metric: `alert_queue_full_total`

### Data Retention

- Alert events: 90 days, partitioned by month
- Delivery records: 90 days
- Automated cleanup via scheduled job

## Alternatives Considered

### 1. Synchronous Delivery (Rejected)

**Approach:** Process alert deliveries synchronously in the request path.

**Pros:**
- Simpler architecture, no worker infrastructure needed
- Immediate feedback on delivery success

**Cons:**
- Cannot meet P95 < 60s SLA under load
- Blocking I/O impacts dashboard responsiveness
- No retry capability without complex request handling

**Decision:** Rejected - async worker required for SLA compliance.

### 2. External Alert Service (e.g., PagerDuty, OpsGenie) (Deferred)

**Approach:** Delegate all alerting to a managed service.

**Pros:**
- Mature escalation policies, on-call scheduling
- Built-in deduplication and rate limiting
- Reduced operational burden

**Cons:**
- Additional vendor dependency and cost
- Less control over delivery timing and customization
- Requires integration work

**Decision:** Deferred to future iteration. Build in-house for MVP to maintain control and reduce dependencies.

### 3. Per-Alert Dedup Key Without Hour Bucket (Considered)

**Approach:** Use only `{alert_id}:{channel}:{recipient}` for deduplication.

**Pros:**
- Simpler key structure
- No time-boundary edge cases

**Cons:**
- Would prevent legitimate re-alerting for recurring conditions
- Less flexible for future alert escalation patterns

**Decision:** Rejected - hour bucket provides appropriate dedup window for operational alerts.

## Implementation Notes

### Rollout Plan

1. **Phase 1 (C0):** Create ADR, establish auth stub, validate prerequisites
2. **Phase 2 (C3):** Implement delivery service with single channel (email)
3. **Phase 3 (C3):** Add Slack and SMS channels with rate limiting
4. **Phase 4 (C4):** Build configuration UI, connect to delivery service
5. **Phase 5 (C6):** Integration testing, documentation, runbook updates

### Rollback Plan

If critical issues discovered post-deployment:

1. **Disable delivery:** Set `ALERT_DELIVERY_ENABLED=false` to stop processing
2. **Clear queue:** Drain pending deliveries to poison queue if needed
3. **Revert code:** Standard git revert of delivery service changes
4. **Notify users:** Manual notification of alert delivery suspension

### Migration Considerations

- No existing alert tables to migrate
- `alert_events` and `alert_deliveries` are new tables
- Migrations must be idempotent (`CREATE TABLE IF NOT EXISTS`)

### Secrets Required

| Secret | Purpose | Rotation |
|--------|---------|----------|
| `ALERT_RECIPIENT_HASH_SECRET` | HMAC for rate limit keys | Quarterly |
| SMTP credentials | Email delivery | Per provider policy |
| SendGrid API key | Email delivery (alternative) | Annual |
| Slack webhook URL | Slack notifications | As needed |
| Twilio SID/Token | SMS delivery | Annual |

## Consequences

### Positive

- Idempotent delivery prevents duplicate notifications
- Rate limiting protects external providers and recipients
- Poison queue enables manual intervention for persistent failures
- Async processing meets P95 < 60s SLA

### Negative

- Adds infrastructure complexity (async worker, Redis for rate limits)
- Requires secrets provisioning for all channel providers
- HMAC secret rotation requires coordination

### Risks

- Provider outages could cause backlog buildup
- Rate limit key TTL mismatch could cause over-delivery
- Clock skew across workers could affect hour_bucket consistency

## References

- [P4T5_DONE.md](../ARCHIVE/TASKS_HISTORY/P4T5_DONE.md) - Track 7 specification
- libs/backtest/worker.py - RQ worker pattern reference
- T7.5 Alert Delivery Service acceptance criteria
