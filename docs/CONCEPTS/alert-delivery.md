# Alert Delivery Service

## Overview

The Alert Delivery Service handles the reliable delivery of alerts through multiple channels (Email, Slack, SMS). It provides idempotent delivery, rate limiting, retry with exponential backoff, and poison queue management.

## Architecture

```
+----------------+     +-------------------+     +------------------+
| Alert Events   | --> | Delivery Service  | --> | Channel Handlers |
| (PostgreSQL)   |     | (alert_delivery   |     | (Email/Slack/SMS)|
|                |     |  _worker.py)      |     |                  |
+----------------+     +-------------------+     +------------------+
        ^                      |
        |                      v
+----------------+     +-------------------+
| alert_rules    |     | alert_deliveries  |
| (PostgreSQL)   |     | (PostgreSQL)      |
+----------------+     +-------------------+
```

## Channel Handlers

### Email (SMTP/SendGrid)
- **Primary:** SendGrid API for reliable delivery
- **Fallback:** Direct SMTP
- **Template:** HTML with plain-text fallback
- **Headers:** Includes `X-Dedup-Key` for idempotency

### Slack
- **Method:** Webhook POST to Slack API
- **Format:** Block Kit for rich formatting
- **Attachments:** Alert details, thresholds, timestamps

### SMS (Twilio)
- **Provider:** Twilio API
- **Format:** Plain text, 160 char limit
- **Priority:** High priority alerts only

## Idempotency Model

Each delivery has a unique dedup key:

```
dedup_key = hash(alert_id + channel + recipient + date)[:24]
```

Before processing, the service checks for existing delivery with same dedup key:
- If found with `status=delivered`: Skip (already sent)
- If found with `status=pending`: Skip (in flight)
- If found with `status=failed`: Check retry count, proceed if under limit

This prevents duplicate notifications during retries or worker restarts.

## Rate Limiting

Rate limits are enforced at multiple levels:

### Per-Channel Limits
| Channel | Rate Limit |
|---------|------------|
| Email | 100/minute |
| Slack | 30/minute |
| SMS | 10/minute |

### Per-Recipient Limits
- **Default:** 10 alerts/hour per recipient
- **Override:** Configurable per rule

### Global Limits
- **Total deliveries:** 1000/minute
- **Burst:** 50 in 10 seconds

Rate limit state is stored in Redis with automatic TTL expiry.

## Retry with Exponential Backoff

Failed deliveries are retried with exponential backoff:

```
retry_delay = min(base_delay * (2 ^ attempt), max_delay)
```

| Attempt | Delay | Cumulative |
|---------|-------|------------|
| 1 | 5s | 5s |
| 2 | 10s | 15s |
| 3 | 20s | 35s |
| 4 | 40s | 75s |
| 5 | 60s | 135s |

**Maximum retries:** 5

After exhausting retries, the delivery is moved to the poison queue.

## Poison Queue

Deliveries that fail all retries are marked as "poison":

```sql
UPDATE alert_deliveries
SET status = 'poison',
    failed_at = NOW(),
    error_message = :error
WHERE id = :delivery_id
```

### Manual Review Process
1. Query poison queue: `SELECT * FROM alert_deliveries WHERE status = 'poison'`
2. Investigate root cause (network, credentials, recipient invalid)
3. Fix issue
4. Retry: `UPDATE alert_deliveries SET status = 'pending', retry_count = 0 WHERE id = :id`

### Automatic Alerting
When poison queue size exceeds threshold (default: 10), a critical alert is triggered.

## Database Schema

### alert_deliveries Table
```sql
CREATE TABLE alert_deliveries (
    id UUID PRIMARY KEY,
    alert_id UUID REFERENCES alert_events(id),
    channel VARCHAR(20) NOT NULL,  -- email, slack, sms
    recipient TEXT NOT NULL,        -- masked for PII
    dedup_key VARCHAR(64) UNIQUE,
    status VARCHAR(20) DEFAULT 'pending',
    retry_count INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    delivered_at TIMESTAMP,
    failed_at TIMESTAMP,
    error_message TEXT
);
```

### Status Transitions
```
pending -> processing -> delivered
              |
              v
           failed -> pending (retry) -> poison
```

## Prometheus Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `alert_delivery_total` | Counter | channel, status | Delivery attempts |
| `alert_delivery_latency_seconds` | Histogram | channel | End-to-end delivery time |
| `alert_queue_depth` | Gauge | - | Pending deliveries |
| `alert_poison_queue_size` | Gauge | - | Poison queue size |
| `alert_rate_limit_hits_total` | Counter | level | Rate limit rejections |

## SLA Targets

| Metric | Target | Alert Threshold |
|--------|--------|-----------------|
| Delivery latency P95 | < 60s | > 60s |
| Queue drain time | < 5min | > 10min |
| Poison queue size | 0 | > 10 |

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `ALERT_DELIVERY_WORKERS` | `2` | Number of worker processes |
| `ALERT_RETRY_BASE_DELAY` | `5` | Base retry delay (seconds) |
| `ALERT_RETRY_MAX_DELAY` | `60` | Maximum retry delay (seconds) |
| `ALERT_MAX_RETRIES` | `5` | Maximum retry attempts |
| `ALERT_POISON_THRESHOLD` | `10` | Poison queue alert threshold |

## Related Documentation

- [Alerting Configuration](./alerting.md)
- [ADR-0029: Alerting System](../ADRs/ADR-0029-alerting-system.md)
- [Operations Runbook](../RUNBOOKS/ops.md#alert-troubleshooting)
