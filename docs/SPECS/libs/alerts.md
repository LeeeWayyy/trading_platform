# alerts

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `AlertRule` | fields | model | Alert rule definition (threshold, channels). |
| `AlertEvent` | fields | model | Triggered alert event record. |
| `AlertDelivery` | fields | model | Delivery attempt record. |
| `ChannelConfig` | type, recipient, enabled | model | Delivery channel configuration. |
| `ChannelType` | - | enum | Supported channels (email, slack, sms). |
| `DeliveryStatus` | - | enum | Delivery lifecycle state. |
| `DeliveryResult` | success, error, metadata | model | Channel handler result. |
| `mask_email` | email | str | Mask email PII. |
| `mask_phone` | phone | str | Mask phone PII. |
| `mask_webhook` | url | str | Mask webhook PII. |
| `mask_recipient` | recipient, channel | str | Mask PII by channel. |

## Behavioral Contracts
### AlertManager.trigger_alert(...)
**Purpose:** Create alert event, enqueue delivery jobs, and enforce queue limits.

**Preconditions:**
- Alert rule exists and is enabled.
- Recipient hash secret available in secrets manager.

**Postconditions:**
- `alert_events` and `alert_deliveries` rows created.
- RQ jobs enqueued for each delivery (when queue accepts).

**Behavior:**
1. Check queue depth via `QueueDepthManager.is_accepting()`.
2. Load rule and enabled channels.
3. Insert alert event and delivery rows with dedup keys.
4. Enqueue RQ jobs using `execute_delivery_job`.

**Raises:**
- `QueueFullError` when queue is at capacity.
- `ValueError` for missing/disabled rules or no channels.

### DeliveryExecutor.execute(...)
**Purpose:** Execute a delivery with retry and poison-queue handling.

**Preconditions:**
- Delivery row exists and is in PENDING/IN_PROGRESS.

**Postconditions:**
- Delivery marked DELIVERED, FAILED, or POISON.
- Retries scheduled per backoff rules.

**Behavior:**
1. Claim delivery (optimistic locking).
2. Apply rate limit checks.
3. Attempt channel delivery; update status and metrics.
4. Retry on transient errors; move to poison queue after max attempts.

**Raises:**
- Channel-specific errors are captured in `DeliveryResult` and persisted.

### Invariants
- Dedup keys prevent duplicate deliveries for same rule/recipient/hour.
- Raw PII is not stored in DB; masked recipients only.

### State Machine (if stateful)
```
[PENDING] --> [IN_PROGRESS] --> [DELIVERED]
      |             |
      |             +--> [FAILED] --> [RETRY] --> [IN_PROGRESS]
      +--------------------------> [POISON]
```
- **States:** pending, in_progress, delivered, failed, poison.
- **Transitions:** Retry policy and max attempts enforce moves.

## Data Flow
```
Alert rule -> AlertEvent -> AlertDelivery -> RQ job -> Channel handler
                               |
                               v
                       Metrics + DB updates
```
- **Input format:** Rule ID + trigger value/time.
- **Output format:** DB rows + queued jobs.
- **Side effects:** DB inserts/updates, Redis queue depth, metrics.

## Usage Examples
### Example 1: Trigger an alert
```python
from libs.alerts.alert_manager import AlertManager

event = await alert_manager.trigger_alert(rule_id, trigger_value, triggered_at)
```

### Example 2: Mask recipient before logging
```python
from libs.alerts.pii import mask_recipient

safe_recipient = mask_recipient(recipient, channel_type)
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Queue full | depth >= MAX_QUEUE_DEPTH | `QueueFullError` raised |
| Duplicate alert | same rule/recipient/hour | Dedup prevents duplicate delivery row |
| Provider throttling | transient errors | Retries with backoff; may re-enqueue |

## Dependencies
- **Internal:** `libs.secrets`, `libs.alerts.channels`, `libs.alerts.metrics`
- **External:** Postgres, Redis, RQ, email/SMS/Slack providers

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ALERT_RECIPIENT_HASH_SECRET` | Yes | - | Secret for recipient hashing (via secrets manager). |
| `QueueDepthManager.MAX_QUEUE_DEPTH` | No | 10000 | Max deliveries before throttling. |
| `DeliveryExecutor.RETRY_DELAYS` | No | [5, 30] | Retry backoff seconds. |

## Error Handling
- Delivery exceptions stored in `alert_deliveries.error_message`.
- Redis errors logged; DB operations are authoritative.

## Observability (Services only)
### Health Check
- **Endpoint:** N/A
- **Checks:** N/A

### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| `alert_queue_depth` | gauge | - | Current queue depth. |
| `alert_delivery_attempts_total` | counter | channel | Delivery attempts. |
| `alert_delivery_latency_seconds` | histogram | channel | Delivery latency. |
| `alert_dropped_total` | counter | channel, reason | Dropped alerts. |

## Security
- Recipient hashes computed with HMAC; raw PII masked in storage.
- Secrets retrieved from secrets manager, not env.

## Testing
- **Test Files:** `tests/libs/alerts/`
- **Run Tests:** `pytest tests/libs/alerts -v`
- **Coverage:** N/A

## Related Specs
- `secrets.md`
- `redis_client.md`
- `web_console_auth.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `libs/alerts/__init__.py`, `libs/alerts/alert_manager.py`, `libs/alerts/delivery_service.py`, `libs/alerts/models.py`, `libs/alerts/poison_queue.py`, `libs/alerts/pii.py`, `libs/alerts/dedup.py`
- **ADRs:** N/A
