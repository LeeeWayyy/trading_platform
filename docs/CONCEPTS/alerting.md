# Alert Configuration

## Overview

The Alert Configuration UI allows operators to define, manage, and test alert rules. Rules specify conditions that trigger notifications, threshold values, and delivery channels. The system supports multiple condition types and notification channels with proper PII masking.

## Alert Rules

### Condition Types

| Type | Description | Example Threshold |
|------|-------------|-------------------|
| `drawdown` | Portfolio drawdown percentage | 5% |
| `position_size` | Single position concentration | $100,000 |
| `latency` | Service response latency | 500ms |
| `staleness` | Data freshness | 30 minutes |
| `error_rate` | Error percentage | 1% |

### Comparison Operators

| Operator | Description |
|----------|-------------|
| `gt` | Greater than |
| `lt` | Less than |
| `gte` | Greater than or equal |
| `lte` | Less than or equal |
| `eq` | Equal to |

### Rule Configuration

```json
{
    "name": "High Drawdown Alert",
    "condition_type": "drawdown",
    "threshold_value": 5.0,
    "comparison": "gt",
    "channels": ["email", "slack"],
    "recipients": ["ops-team@example.com"],
    "enabled": true,
    "created_by": "admin@example.com"
}
```

## Notification Channels

### Email
- **Provider:** SendGrid or SMTP
- **Configuration:** SMTP host, port, credentials
- **Format:** HTML with plain-text fallback

### Slack
- **Provider:** Slack Webhooks
- **Configuration:** Webhook URL, channel
- **Format:** Block Kit for rich formatting

### SMS
- **Provider:** Twilio
- **Configuration:** Account SID, Auth Token, From number
- **Format:** Plain text (160 char limit)

## Channel Setup Workflow

1. Navigate to **Alerts** page
2. Click **"Manage Channels"**
3. Select channel type (Email/Slack/SMS)
4. Enter credentials (masked after save)
5. Click **"Test Connection"**
6. Save configuration

Credentials are stored encrypted and masked in the UI after saving.

## Alert History

The history tab shows:
- All triggered alerts
- Delivery status per channel
- Acknowledgment status
- Timestamp and threshold values

### Filtering Options
- Date range
- Condition type
- Status (delivered, pending, failed)
- Acknowledged (yes/no)

## Alert Acknowledgment

Operators can acknowledge alerts to:
- Indicate awareness
- Stop escalation timers
- Clear from active alerts view

Acknowledgment is logged in the audit trail.

## Test Notification Workflow

1. Navigate to **Alerts** > **Rules** tab
2. Select a rule
3. Click **"Send Test"**
4. Receive test notification on configured channels
5. Verify formatting and delivery

Test notifications are logged but not counted in metrics.

## PII Masking

The UI masks sensitive information:

| Field | Display | Actual |
|-------|---------|--------|
| Email | `op***@example.com` | `operator@example.com` |
| Phone | `+1***789` | `+15551234789` |
| Webhook URL | `https://hooks.slack.com/***` | Full URL |

Masking occurs at the presentation layer; full values are stored encrypted.

## RBAC Requirements

| Action | Required Permission |
|--------|---------------------|
| View alerts | `VIEW_ALERTS` |
| Create/edit rules | `MANAGE_ALERTS` |
| Delete rules | `MANAGE_ALERTS` |
| Manage channels | `MANAGE_SYSTEM_CONFIG` |

## Database Schema

### alert_rules Table
```sql
CREATE TABLE alert_rules (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    condition_type VARCHAR(50) NOT NULL,
    threshold_value DECIMAL(18,4),
    comparison VARCHAR(10) NOT NULL,
    channels JSONB DEFAULT '[]',
    recipients JSONB DEFAULT '[]',
    enabled BOOLEAN DEFAULT true,
    created_by VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

### alert_events Table
```sql
CREATE TABLE alert_events (
    id UUID PRIMARY KEY,
    rule_id UUID REFERENCES alert_rules(id),
    trigger_value DECIMAL(18,4),
    triggered_at TIMESTAMP DEFAULT NOW(),
    acknowledged_at TIMESTAMP,
    acknowledged_by VARCHAR(255)
);
```

## Prometheus Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `alert_rules_total` | Gauge | Total configured rules |
| `alert_rules_enabled` | Gauge | Enabled rules count |
| `alert_events_total` | Counter | Triggered alert events |
| `alert_ack_latency_seconds` | Histogram | Time to acknowledgment |

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `FEATURE_ALERTS` | `true` | Enable alerts feature |
| `ALERT_CHANNELS_ENCRYPTED` | `true` | Encrypt channel credentials |
| `ALERT_TEST_RATE_LIMIT` | `10` | Max test notifications/hour |

## Related Documentation

- [Alert Delivery Service](./alert-delivery.md)
- [ADR-0029: Alerting System](../ADRs/ADR-0029-alerting-system.md)
- [Operations Runbook](../RUNBOOKS/ops.md#alert-operations)
