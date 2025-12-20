# P4T5 C3/C4 Implementation Plan: Alert Delivery Service & Configuration UI

**Branch:** `feature/P4T5-C3C4-alert-service`
**Created:** 2025-12-20
**Components:** C3 (T7.5 Alert Delivery Service), C4 (T7.3 Alert Configuration UI)

---

## Pre-Implementation Analysis Summary

### Existing Infrastructure Discovered

| Component | Status | Location |
|-----------|--------|----------|
| RQ Worker Infrastructure | EXISTS | `libs/backtest/worker.py`, `job_queue.py` |
| Rate Limiter (Redis) | EXISTS | `libs/web_console_auth/rate_limiter.py` |
| Audit Log Table | EXISTS | `db/migrations/0004_add_audit_log.sql`, `0005_update_audit_log_schema.sql` |
| Web Console Patterns | EXISTS | `apps/web_console/pages/health.py`, `circuit_breaker.py` |
| Auth Decorator | EXISTS | `apps/web_console/auth/operations_auth.py` |
| JSON Logging | EXISTS | `libs/common/logging/formatter.py` |
| Pydantic Events | EXISTS | `libs/redis_client/events.py` |
| Migration 0010 | EXISTS | `db/migrations/0010_relax_positions_avg_entry_price.sql` |

### Key Design Decisions

1. **Worker Infrastructure**: Reuse RQ infrastructure with dedicated `alerts` queue (NOT Celery)
2. **Database**: Use migration 0011 for alert tables (0010 exists)
3. **Rate Limiting**: Extend existing Redis rate limiter pattern
4. **Audit Logging**: Reuse existing `audit_log` table with new action types
5. **Web Console**: Follow established page/service/component patterns

---

## C3: T7.5 Alert Delivery Service

### Acceptance Criteria (from P4T5_TASK.md)

- [ ] Email delivery via SMTP/SendGrid with retry
- [ ] Slack webhook integration
- [ ] SMS delivery via Twilio
- [ ] Delivery retry with exponential backoff (1s, 2s, 4s; max 3 attempts)
- [ ] Delivery status tracking (pending, delivered, failed, poison)
- [ ] Idempotency: Dedup key = `{alert_id}:{channel}:{recipient}:{hour_bucket}`
- [ ] Rate limits (per-channel): Email 100/min, Slack 50/min, SMS 10/min
- [ ] Rate limits (per-recipient): Max 5 alerts/hour per email, 3/hour per phone
- [ ] Rate limits (global burst): Max 500 deliveries/min total
- [ ] Poison queue: Failed after 3 attempts → move to poison queue
- [ ] Prometheus metrics: `alert_delivery_attempts_total`, `alert_delivery_latency_seconds`, etc.

### Component Breakdown

#### C3.1: Database Schema & Models (0.5 day)
**Files to create:**
- `db/migrations/0011_create_alert_tables.sql`
- `libs/alerts/__init__.py`
- `libs/alerts/models.py`
- `libs/alerts/pii.py` (CENTRALIZED PII masking - all components import from here)

**Schema (idempotent):**
```sql
-- Enable pgcrypto if not already enabled
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Alert rules table
CREATE TABLE IF NOT EXISTS alert_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    condition_type VARCHAR(50) NOT NULL,  -- drawdown, position_limit, latency
    threshold_value DECIMAL(10, 4) NOT NULL,
    comparison VARCHAR(10) NOT NULL,  -- gt, lt, eq, gte, lte
    channels JSONB NOT NULL DEFAULT '[]',
    enabled BOOLEAN DEFAULT true,
    created_by VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Alert events table
CREATE TABLE IF NOT EXISTS alert_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_id UUID NOT NULL REFERENCES alert_rules(id),
    triggered_at TIMESTAMPTZ NOT NULL,
    trigger_value DECIMAL(10, 4),
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by UUID,
    acknowledgment_note TEXT,
    routed_channels JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Alert deliveries table (with idempotency and status constraint)
CREATE TABLE IF NOT EXISTS alert_deliveries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_id UUID NOT NULL REFERENCES alert_events(id),
    channel VARCHAR(20) NOT NULL CHECK (channel IN ('email', 'slack', 'sms')),
    recipient TEXT NOT NULL,
    dedup_key VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'delivered', 'failed', 'poison')),
    attempts INTEGER DEFAULT 0 CHECK (attempts >= 0 AND attempts <= 3),
    last_attempt_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ,
    poison_at TIMESTAMPTZ,  -- Timestamp when moved to poison queue
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(dedup_key)
);

-- Index for poison queue monitoring
CREATE INDEX IF NOT EXISTS idx_alert_deliveries_poison
    ON alert_deliveries(poison_at)
    WHERE status = 'poison';
```

**Pydantic Models:**
```python
# libs/alerts/models.py
class ChannelType(str, Enum):
    EMAIL = "email"
    SLACK = "slack"
    SMS = "sms"

class DeliveryStatus(str, Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    POISON = "poison"

class ChannelConfig(BaseModel):
    type: ChannelType
    recipient: str  # masked in logs/UI
    enabled: bool = True

class AlertRule(BaseModel):
    id: UUID
    name: str
    condition_type: str
    threshold_value: Decimal
    comparison: str
    channels: list[ChannelConfig]
    enabled: bool
    created_by: str
    created_at: datetime
    updated_at: datetime

class AlertDelivery(BaseModel):
    id: UUID
    alert_id: UUID
    channel: ChannelType
    recipient: str
    dedup_key: str
    status: DeliveryStatus
    attempts: int
    last_attempt_at: datetime | None
    delivered_at: datetime | None
    error_message: str | None
```

#### C3.2: Deduplication & Rate Limiting (0.5 day)
**Files to create:**
- `libs/alerts/dedup.py`

**Files to modify (extend existing rate limiter):**
- `libs/web_console_auth/rate_limiter.py` - Add alert-specific rate limit methods

**NOTE:** Per review feedback, we extend the existing rate limiter instead of creating a new one to maintain pattern parity.

**Rate Limiter Pattern Distinction (per Gemini review):**
The new Lua-based implementation uses a **Fixed Window** (INCR+EXPIRE) strategy, which differs from the existing **Sliding Window** (ZSET) implementation in `RateLimiter`. This is intentional for alert rate limiting to provide simpler semantics. Document this distinction clearly in `libs/web_console_auth/rate_limiter.py` with comments explaining both patterns.

**Dedup Key Pattern:**

**Note on Recipient Hashing (Security Enhancement):**
The task doc specifies dedup key as `{alert_id}:{channel}:{recipient}:{hour_bucket}`. However, storing raw recipient (email/phone) in the database dedup_key column would expose PII. This plan intentionally uses `recipient_hash` instead of raw recipient for security. This is documented as a deliberate security enhancement per PII handling requirements.

```python
def compute_dedup_key(
    alert_id: str,
    channel: str,
    recipient: str,
    triggered_at: datetime,
    hash_secret: str,
) -> str:
    """Compute idempotent dedup key with hashed recipient.

    Security: Uses recipient_hash instead of raw recipient to prevent PII
    exposure in database. Per task doc PII requirement: 'never store raw PII'.

    Hour bucket derived from original trigger timestamp (not current time).
    """
    hour_bucket = triggered_at.replace(minute=0, second=0, microsecond=0).isoformat()
    recipient_hash = hmac.new(
        hash_secret.encode(),
        recipient.encode(),
        hashlib.sha256
    ).hexdigest()[:16]
    return f"{alert_id}:{channel}:{recipient_hash}:{hour_bucket}"
```

**Rate Limit Keys (Redis INCR + EXPIRE pattern per task spec):**
- `ratelimit:{channel}:{minute}` - Per-channel rate (Email 100/min, Slack 50/min, SMS 10/min)
- `ratelimit:recipient:{hash}:{hour}` - Per-recipient rate (5/hr email, 3/hr phone)
- `ratelimit:global:{minute}` - Global burst limit (500/min)

**Rate Limit Thresholds (EXPLICIT from task doc):**
```python
RATE_LIMITS = {
    "channel": {
        "email": 100,   # per minute
        "slack": 50,    # per minute
        "sms": 10,      # per minute
    },
    "recipient": {
        "email": 5,     # per hour
        "sms": 3,       # per hour (phone)
    },
    "global": 500,      # per minute total
}
```

**Rate Limiter TTL/Window and Atomicity (per review feedback):**
```python
# Explicit TTL values
RATE_LIMIT_TTL = {
    "channel": 60,      # 60 seconds for per-minute limits
    "recipient": 3600,  # 3600 seconds (1 hour) for per-hour limits
    "global": 60,       # 60 seconds for global burst
}

# Atomic INCR + EXPIRE pattern (works across distributed workers)
async def check_rate_limit(redis: Redis, key: str, limit: int, ttl: int) -> bool:
    """Check and increment rate limit atomically.

    Uses Lua script for atomicity across distributed workers per task spec:
    'INCR + EXPIRE pattern; works across distributed workers via atomic Redis operations'
    """
    lua_script = """
    local current = redis.call('INCR', KEYS[1])
    if current == 1 then
        redis.call('EXPIRE', KEYS[1], ARGV[1])
    end
    return current
    """
    current = await redis.eval(lua_script, 1, key, ttl)
    return current <= limit

# Per-recipient hashing rules (email vs phone differentiation)
def get_recipient_hash(recipient: str, channel_type: str, hash_secret: str) -> str:
    """Hash recipient for rate limit key.

    Different channels may have same recipient format but different limits:
    - email: 5/hour
    - sms (phone): 3/hour
    """
    # Include channel type in hash to differentiate email vs phone limits
    data = f"{channel_type}:{recipient}"
    return hmac.new(
        hash_secret.encode(),
        data.encode(),
        hashlib.sha256
    ).hexdigest()[:16]
```

**Recipient Hash Secret Governance (per task doc requirement):**
```python
# ALERT_RECIPIENT_HASH_SECRET governance per T7.5 acceptance criteria:
# - "secret stored in secrets manager, rotated quarterly"
# - "hash = first 16 chars of hex digest"

from libs.secrets.manager import SecretsManager

def get_recipient_hash_secret() -> str:
    """Get recipient hash secret from secrets manager.

    Per task doc:
    - Secret stored in secrets manager (NOT env var for production)
    - Rotated quarterly
    - NEVER logged
    """
    secrets = SecretsManager()
    return secrets.get("ALERT_RECIPIENT_HASH_SECRET")

# Validation test for secret governance
def test_recipient_hash_secret_in_secrets_manager():
    """Verify ALERT_RECIPIENT_HASH_SECRET is stored in secrets manager."""
    secrets = SecretsManager()
    secret = secrets.get("ALERT_RECIPIENT_HASH_SECRET")
    assert secret is not None, "Secret must be provisioned in secrets manager"
    assert len(secret) >= 32, "Secret must be at least 32 bytes for security"

def test_recipient_hash_secret_not_in_env_production():
    """Verify secret is NOT in production env files."""
    import os
    from pathlib import Path

    # Check .env.prod doesn't contain the secret directly
    prod_env = Path(".env.prod")
    if prod_env.exists():
        content = prod_env.read_text()
        assert "ALERT_RECIPIENT_HASH_SECRET=" not in content or \
               "${" in content, \
               "Secret must reference secrets manager, not be hardcoded"
```

**Secret Rotation Policy (quarterly per task doc):**
- Secret name: `ALERT_RECIPIENT_HASH_SECRET`
- Storage: Secrets manager (AWS Secrets Manager, Vault, etc.)
- Rotation: Quarterly (every 90 days)
- Rotation process:
  1. Generate new 32-byte random secret
  2. Update secrets manager
  3. Restart alert workers to pick up new secret
  4. Old hashes remain valid (rate limit keys expire naturally via TTL)

#### C3.3: Channel Handlers (1 day)
**Files to create:**
- `libs/alerts/channels/__init__.py`
- `libs/alerts/channels/base.py`
- `libs/alerts/channels/email.py`
- `libs/alerts/channels/slack.py`
- `libs/alerts/channels/sms.py`

**Files to modify (ADD DEPENDENCIES FOR PHASE 1):**
- `pyproject.toml` - Add aiosmtplib, httpx (SendGrid/Slack), twilio dependencies

**Dependencies to add (ASYNC-FRIENDLY per Codex review):**
```toml
# pyproject.toml additions - async-compatible libraries
aiosmtplib = "^3.0.0"    # Async SMTP client (NOT smtplib)
httpx = "^0.27.0"        # Async HTTP for SendGrid/Slack (NOT requests)
twilio = "^8.10.0"       # Twilio SDK (use run_in_executor for sync calls)
```

**DeliveryResult Model (defined in libs/alerts/models.py):**
```python
class DeliveryResult(BaseModel):
    """Result of a channel delivery attempt."""
    success: bool
    message_id: str | None = None  # Provider message ID
    error: str | None = None       # Error message if failed
    retryable: bool = True         # Whether failure should retry
    metadata: dict[str, str] = Field(default_factory=dict)
```

**Rate Limiting Strategy (DELIVERY SERVICE enforces, not handlers):**
Rate limiting is enforced by the DeliveryService (C3.4), NOT inside channel handlers.
Handlers are pure I/O - they only send and return DeliveryResult.
This separation keeps handlers testable and avoids Redis coupling in channel code.

**PII Logging Policy (MANDATORY):**
All channel handlers MUST:
1. Import `from libs.alerts.pii import mask_recipient`
2. Use `mask_recipient(recipient, channel_type)` in ALL log statements
3. NEVER log raw recipient, webhook URL, or phone number
4. Log structure: `logger.info("send_attempt", extra={"recipient": mask_recipient(...)})`

**Secrets Handling (via existing secrets manager):**
```python
# Channel handlers MUST source credentials from secrets manager
from libs.secrets import create_secret_manager

secrets = create_secret_manager()

# Email credentials
SMTP_HOST = secrets.get_secret("SMTP_HOST")
SMTP_PORT = secrets.get_secret("SMTP_PORT")
SMTP_USER = secrets.get_secret("SMTP_USER")
SMTP_PASSWORD = secrets.get_secret("SMTP_PASSWORD")
SENDGRID_API_KEY = secrets.get_secret("SENDGRID_API_KEY")

# Slack
SLACK_WEBHOOK_URL = secrets.get_secret("SLACK_WEBHOOK_URL")

# Twilio
TWILIO_ACCOUNT_SID = secrets.get_secret("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = secrets.get_secret("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = secrets.get_secret("TWILIO_FROM_NUMBER")

# NEVER log secrets - only log that secret was loaded/missing
```

**Base Channel Pattern:**
```python
# libs/alerts/channels/base.py
from abc import ABC, abstractmethod
from typing import Any
from libs.alerts.models import DeliveryResult

class BaseChannel(ABC):
    """Abstract base for delivery channels.

    Handlers are pure I/O - rate limiting enforced by DeliveryService.
    """

    channel_type: str  # "email", "slack", "sms"

    @abstractmethod
    async def send(
        self,
        recipient: str,
        subject: str,
        body: str,
        metadata: dict[str, Any],
    ) -> DeliveryResult:
        """Send notification. Returns DeliveryResult with success/error.

        Must log with masked recipient using libs.alerts.pii.mask_recipient.
        """
        pass
```

**Email Handler (SMTP + SendGrid fallback):**
```python
# libs/alerts/channels/email.py
import asyncio
import logging
import aiosmtplib
import httpx
from libs.alerts.models import DeliveryResult
from libs.alerts.pii import mask_recipient
from libs.alerts.channels.base import BaseChannel

logger = logging.getLogger(__name__)

class EmailChannel(BaseChannel):
    """Email channel with SMTP primary, SendGrid fallback."""
    channel_type = "email"
    TIMEOUT = 10  # seconds for both SMTP and HTTP

    async def _send_smtp(self, recipient: str, subject: str, body: str) -> DeliveryResult:
        """Send via SMTP with aiosmtplib (async, 10s timeout)."""
        try:
            async with aiosmtplib.SMTP(
                hostname=self.smtp_host,
                port=self.smtp_port,
                timeout=self.TIMEOUT
            ) as smtp:
                await smtp.login(self.smtp_user, self.smtp_password)
                message = self._build_message(recipient, subject, body)
                await smtp.send_message(message)
                return DeliveryResult(success=True, message_id=message["Message-ID"])
        except aiosmtplib.SMTPAuthenticationError as e:
            return DeliveryResult(success=False, error=str(e), retryable=False)
        except (aiosmtplib.SMTPConnectError, asyncio.TimeoutError) as e:
            return DeliveryResult(success=False, error=str(e), retryable=True)
        except aiosmtplib.SMTPResponseException as e:
            # 4xx = temp failure (retryable), 5xx = permanent (not retryable)
            retryable = 400 <= e.code < 500
            return DeliveryResult(success=False, error=str(e), retryable=retryable)

    async def _send_sendgrid(self, recipient: str, subject: str, body: str) -> DeliveryResult:
        """Send via SendGrid API (async httpx, 10s timeout).

        Endpoint: POST https://api.sendgrid.com/v3/mail/send
        Headers: Authorization: Bearer {SENDGRID_API_KEY}, Content-Type: application/json
        Payload: {"personalizations": [{"to": [{"email": recipient}]}],
                  "from": {"email": from_email}, "subject": subject,
                  "content": [{"type": "text/plain", "value": body}]}
        Response: 202 Accepted (success), x-message-id header for tracking
        """
        async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
            response = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {self.sendgrid_api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "personalizations": [{"to": [{"email": recipient}]}],
                    "from": {"email": self.from_email},
                    "subject": subject,
                    "content": [{"type": "text/plain", "value": body}]
                }
            )

        if response.status_code == 202:
            msg_id = response.headers.get("x-message-id")
            return DeliveryResult(success=True, message_id=msg_id)

        # Error mapping: 401/403 = auth (not retryable), 429/5xx = retryable
        retryable = response.status_code == 429 or response.status_code >= 500
        return DeliveryResult(
            success=False,
            error=f"SendGrid HTTP {response.status_code}",
            retryable=retryable,
            metadata={"retry_after": response.headers.get("retry-after", "")}
        )
```

**Slack Handler:**
```python
# libs/alerts/channels/slack.py
import logging
import httpx
from libs.alerts.models import DeliveryResult
from libs.alerts.pii import mask_recipient
from libs.alerts.channels.base import BaseChannel

logger = logging.getLogger(__name__)

class SlackChannel(BaseChannel):
    """Slack channel via webhook.

    Async I/O: Uses httpx for non-blocking HTTP POST.

    Error Mapping (status-based):
    - 200: success
    - 429: rate limited (retryable=True, capture Retry-After header)
    - 5xx: server error (retryable=True)
    - 4xx (except 429): client error (retryable=False, config issue)
    """
    channel_type = "slack"
    TIMEOUT = 10  # seconds

    async def send(self, recipient, subject, body, metadata) -> DeliveryResult:
        masked = mask_recipient(recipient, self.channel_type)
        logger.info("slack_send_attempt", extra={"recipient": masked})

        try:
            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                response = await client.post(
                    recipient,  # webhook URL
                    json={"text": f"*{subject}*\n{body}"}
                )
        except httpx.TimeoutException as e:
            logger.error("slack_timeout", extra={"recipient": masked})
            return DeliveryResult(success=False, error="timeout", retryable=True)
        except httpx.RequestError as e:
            logger.error("slack_connection_error", extra={"recipient": masked, "error": str(e)})
            return DeliveryResult(success=False, error=str(e), retryable=True)

        if response.status_code == 200:
            logger.info("slack_sent", extra={"recipient": masked})
            return DeliveryResult(success=True, message_id=None)

        # Build metadata with retry_after if present
        result_metadata: dict[str, str] = {}
        if response.status_code == 429:
            retry_after = response.headers.get("retry-after", "")
            result_metadata["retry_after"] = retry_after
            logger.warning("slack_rate_limited", extra={
                "recipient": masked, "retry_after": retry_after
            })

        # Error body from Slack (may contain structured error)
        error_body = response.text[:200]  # Truncate for safety

        # Status-based retryable mapping
        retryable = response.status_code == 429 or response.status_code >= 500

        logger.error("slack_send_failed", extra={
            "recipient": masked,
            "status": response.status_code,
            "retryable": retryable
        })
        return DeliveryResult(
            success=False,
            error=f"HTTP {response.status_code}: {error_body}",
            retryable=retryable,
            metadata=result_metadata
        )
```

**SMS Handler (Twilio):**
```python
# libs/alerts/channels/sms.py
import asyncio
import logging
from functools import partial
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from libs.alerts.models import DeliveryResult
from libs.alerts.pii import mask_recipient
from libs.alerts.channels.base import BaseChannel

logger = logging.getLogger(__name__)

class SMSChannel(BaseChannel):
    """SMS channel via Twilio.

    Async I/O: Twilio SDK is sync, wrap in run_in_executor.

    Error Mapping (status-based via TwilioRestException):
    - 429: rate limited (retryable=True)
    - 5xx: server error (retryable=True)
    - 401/403: auth error (retryable=False, config issue)
    - 400: validation error (retryable=False, bad input)
    - Other 4xx: client error (retryable=False)
    """
    channel_type = "sms"

    def __init__(self, account_sid: str, auth_token: str, from_number: str):
        self.client = Client(account_sid, auth_token)
        self.from_number = from_number

    async def send(self, recipient, subject, body, metadata) -> DeliveryResult:
        masked = mask_recipient(recipient, self.channel_type)
        logger.info("sms_send_attempt", extra={"recipient": masked})

        loop = asyncio.get_event_loop()
        try:
            message = await loop.run_in_executor(
                None,
                partial(
                    self.client.messages.create,
                    to=recipient,
                    from_=self.from_number,
                    body=f"{subject}: {body}"
                )
            )
            logger.info("sms_sent", extra={
                "recipient": masked,
                "sid": message.sid
            })
            return DeliveryResult(success=True, message_id=message.sid)

        except TwilioRestException as e:
            # Status-based retryable mapping
            retryable = e.status == 429 or e.status >= 500
            logger.error("sms_send_failed", extra={
                "recipient": masked,
                "status": e.status,
                "code": e.code,
                "retryable": retryable
            })
            return DeliveryResult(
                success=False,
                error=f"Twilio {e.status}: {e.msg}",
                retryable=retryable,
                metadata={"twilio_code": str(e.code)}
            )

        except Exception as e:
            # Connection/timeout errors are retryable
            logger.error("sms_connection_error", extra={
                "recipient": masked, "error": str(e)
            })
            return DeliveryResult(
                success=False,
                error=str(e),
                retryable=True
            )
```

**C3.3 Channel Handler Tests (PII Guard):**
```python
# tests/libs/alerts/test_channel_pii.py
"""Verify no raw PII leaks from channel handlers."""
import io
import logging

def test_email_channel_never_logs_raw_recipient():
    """Verify email handler uses mask_recipient in all log statements."""
    # Capture logs, send via mocked email channel, assert no raw email
    pass

def test_slack_channel_never_logs_raw_webhook():
    """Verify slack handler uses mask_recipient for webhook URLs."""
    pass

def test_sms_channel_never_logs_raw_phone():
    """Verify sms handler uses mask_recipient for phone numbers."""
    pass

def test_all_channels_log_only_masked_recipients():
    """Integration: send through all channels, verify logs contain only masked values."""
    recipients = ["user@domain.com", "https://hooks.slack.com/xxx", "+1234567890"]
    # Mock each channel, capture logs, assert masked patterns only
    for recipient in recipients:
        assert recipient not in captured_logs
        assert "***" in captured_logs  # Masked prefix present
```

#### C3.4: Delivery Service & Retry Logic (1 day)
**Files to create:**
- `libs/alerts/delivery_service.py`
- `libs/alerts/poison_queue.py`

**Delivery Service Pattern:**
```python
# libs/alerts/delivery_service.py
class AlertDeliveryService:
    """Multi-channel delivery with idempotency and retry."""

    RETRY_DELAYS = [1, 2, 4]  # seconds, exponential backoff
    MAX_ATTEMPTS = 3
    MAX_QUEUE_DEPTH = 10000
    QUEUE_RESUME_THRESHOLD = 8000
    RETRY_AFTER_SECONDS = 60  # Per task spec

    async def deliver(
        self,
        alert_id: str,
        channel: ChannelType,
        recipient: str,
        subject: str,
        body: str,
        triggered_at: datetime,
    ) -> DeliveryResult:
        """Deliver with idempotency, rate limiting, and retry."""
        # 1. Check queue depth
        #    - If > MAX_QUEUE_DEPTH: reject with HTTP 503 + Retry-After: 60 header
        #    - Increment alert_queue_full_total metric
        # 2. Compute dedup key
        # 3. Check if already delivered (idempotency)
        # 4. Check rate limits (per-channel, per-recipient, global)
        # 5. Attempt delivery with retry (max 3 attempts)
        # 6. Update delivery status
        # 7. Move to poison queue after MAX_ATTEMPTS failures
        #    - Increment alert_poison_queue_size metric
        pass

    async def get_queue_depth(self) -> int:
        """Get current pending delivery count."""
        pass

    async def is_accepting_deliveries(self) -> bool:
        """Return True if queue depth < MAX_QUEUE_DEPTH.

        Resume accepting when backlog < QUEUE_RESUME_THRESHOLD (8000).
        """
        pass

    async def process_retry_queue(self) -> int:
        """Process pending retries. Returns count processed."""
        pass

class QueueFullError(Exception):
    """Raised when delivery queue is full."""

    def __init__(self, retry_after: int = 60):
        self.retry_after = retry_after
        super().__init__(f"Queue full. Retry after {retry_after}s")
```

**HTTP 503 Response (for FastAPI endpoint):**
```python
from fastapi.responses import JSONResponse

async def enqueue_delivery(request: DeliveryRequest):
    try:
        result = await service.deliver(...)
        return result
    except QueueFullError as e:
        return JSONResponse(
            status_code=503,
            content={"error": "Queue full", "retry_after": e.retry_after},
            headers={"Retry-After": str(e.retry_after)},
        )
```

**Poison Queue:**
```python
# libs/alerts/poison_queue.py
class PoisonQueue:
    """Handle failed deliveries for manual review."""

    async def add(self, delivery: AlertDelivery, error: str) -> None:
        """Add failed delivery to poison queue."""
        # Increment alert_poison_queue_size metric
        pass

    async def get_pending(self, limit: int = 100) -> list[AlertDelivery]:
        """Get pending poison queue items."""
        pass

    async def resolve(self, delivery_id: str, resolution: str) -> None:
        """Mark poison queue item as resolved."""
        pass
```

#### C3.5: Alert Manager & RQ Worker (1 day)
**Files to create:**
- `libs/alerts/alert_manager.py`
- `apps/alert_worker/__init__.py`
- `apps/alert_worker/entrypoint.py`
- `apps/alert_worker/Dockerfile`

**Files to modify (ADD WORKER CONTAINER DEFINITION):**
- `docker-compose.yml` - Add alert_worker service definition

**Docker Compose Addition:**
```yaml
# docker-compose.yml addition
alert_worker:
  build:
    context: .
    dockerfile: apps/alert_worker/Dockerfile
  environment:
    - REDIS_URL=redis://redis:6379
    - DATABASE_URL=postgresql://trader:trader@postgres:5432/trader
    - RQ_QUEUES=alerts
    - ALERT_RECIPIENT_HASH_SECRET=${ALERT_RECIPIENT_HASH_SECRET}
  depends_on:
    - redis
    - postgres
  restart: unless-stopped
```

**Alert Manager (Orchestration):**
```python
# libs/alerts/alert_manager.py
class AlertManager:
    """Orchestrate alert evaluation and delivery."""

    async def trigger_alert(
        self,
        rule_id: str,
        trigger_value: Decimal,
        triggered_at: datetime,
    ) -> AlertEvent:
        """Create alert event and queue deliveries."""
        # 1. Create alert_event record
        # 2. Get rule channels
        # 3. Queue delivery jobs to RQ
        pass

    async def acknowledge_alert(
        self,
        alert_id: str,
        user_id: str,
        note: str,
    ) -> None:
        """Acknowledge alert event."""
        pass
```

**RQ Worker Entry (reuse backtest_worker pattern):**
```python
# apps/alert_worker/entrypoint.py
# Listen to 'alerts' queue
# Job function: deliver_alert(alert_id, channel, recipient, ...)
```

#### C3.6: Prometheus Metrics & Alert Rules (0.5 day)
**Metrics to add in delivery_service.py:**
```python
# Counters
alert_delivery_attempts_total = Counter(
    "alert_delivery_attempts_total",
    "Total delivery attempts",
    ["channel", "status"]
)
alert_throttle_total = Counter(
    "alert_throttle_total",
    "Deliveries throttled by rate limit",
    ["channel", "limit_type"]
)
alert_dropped_total = Counter(
    "alert_dropped_total",
    "Deliveries dropped (queue full)",
    ["channel"]
)
alert_queue_full_total = Counter(
    "alert_queue_full_total",
    "Queue full rejections"
)

# Gauges
alert_poison_queue_size = Gauge(
    "alert_poison_queue_size",
    "Current poison queue size"
)

# Histograms
alert_delivery_latency_seconds = Histogram(
    "alert_delivery_latency_seconds",
    "Delivery latency",
    ["channel"],
    buckets=[0.1, 0.5, 1, 5, 10, 30, 60]
)
```

**Files to create (Prometheus alert rules + Alertmanager routes):**
- `infra/prometheus/alert_delivery_rules.yml`
- `infra/alertmanager/routes.yml` (update existing)

**Prometheus Alert Rules (per task spec):**
```yaml
# infra/prometheus/alert_delivery_rules.yml
groups:
  - name: alert_delivery
    rules:
      # Poison queue alerting (>10 triggers page per task doc)
      - alert: AlertPoisonQueueHigh
        expr: alert_poison_queue_size > 10
        for: 1m
        labels:
          severity: page
          team: platform
        annotations:
          summary: "Alert poison queue size exceeds threshold"
          description: "Poison queue has {{ $value }} items. Manual review required."

      # SLA: P95 delivery latency > 60s
      - alert: AlertDeliveryLatencyHigh
        expr: histogram_quantile(0.95, rate(alert_delivery_latency_seconds_bucket[5m])) > 60
        for: 5m
        labels:
          severity: warning
          team: platform
        annotations:
          summary: "Alert delivery P95 latency exceeds 60s SLA"
          description: "P95 delivery latency is {{ $value }}s"

      # Queue full alerts
      - alert: AlertQueueRejectingDeliveries
        expr: rate(alert_queue_full_total[5m]) > 0
        for: 2m
        labels:
          severity: warning
          team: platform
        annotations:
          summary: "Alert queue is rejecting deliveries (503s)"
          description: "Queue depth exceeded 10,000"
```

**Alertmanager Routes Integration (per review feedback):**
```yaml
# infra/alertmanager/routes.yml - ADD to existing file
route:
  routes:
    # Alert delivery poison queue - page on-call (per task spec)
    - match:
        alertname: AlertPoisonQueueHigh
      receiver: pagerduty-platform
      continue: false

    # Alert delivery latency/queue warnings - Slack
    - match:
        team: platform
        severity: warning
      receiver: slack-alerts-ops
      continue: false

receivers:
  - name: pagerduty-platform
    pagerduty_configs:
      - service_key: '{{ .Values.pagerduty_key }}'

  - name: slack-alerts-ops
    slack_configs:
      - api_url: '{{ .Values.slack_webhook }}'
        channel: '#alerts-ops'
```

**Test for Alert Rules Deployed (per review feedback):**
```python
# tests/infra/test_alert_rules_deployed.py
def test_prometheus_alert_rules_exist():
    """Verify alert delivery rules file exists."""
    assert Path("infra/prometheus/alert_delivery_rules.yml").exists()

def test_alertmanager_routes_include_poison_queue():
    """Verify Alertmanager routes include poison queue alerting."""
    routes_file = Path("infra/alertmanager/routes.yml")
    content = routes_file.read_text()
    assert "AlertPoisonQueueHigh" in content
    assert "pagerduty" in content.lower()
```

#### C3.7: Tests (1 day)
**Files to create:**
- `tests/libs/alerts/__init__.py`
- `tests/libs/alerts/test_models.py`
- `tests/libs/alerts/test_dedup.py`
- `tests/libs/alerts/test_rate_limiter.py`
- `tests/libs/alerts/test_channels.py`
- `tests/libs/alerts/test_delivery_service.py`
- `tests/libs/alerts/test_alert_manager.py`
- `tests/libs/alerts/test_poison_queue.py`
- `tests/libs/alerts/test_retry_logic.py`
- `tests/libs/alerts/test_sla_performance.py`

**Test Categories:**

**Unit Tests:**
- Dedup key generation (hour bucket, recipient hash)
- Rate limit logic (per-channel, per-recipient, global thresholds)
- Channel handlers (mocked providers)
- PII masking functions

**Integration Tests:**
- Redis rate limiting with actual Redis
- Postgres delivery tracking
- Queue depth and backpressure

**Retry/Poison Behavior Tests (REQUIRED per review):**
```python
# tests/libs/alerts/test_retry_logic.py
def test_retry_stops_after_3_attempts():
    """Verify retries stop after exactly 3 attempts."""
    pass

def test_failed_delivery_moves_to_poison_queue():
    """Verify failed delivery after 3 attempts goes to poison queue."""
    pass

def test_poison_queue_metric_increments():
    """Verify alert_poison_queue_size metric increments on poison."""
    pass

def test_retry_delays_exponential_backoff():
    """Verify delays are 1s, 2s, 4s per spec."""
    pass
```

**Rate Limit Threshold Tests (REQUIRED per review):**
```python
# tests/libs/alerts/test_rate_limiter.py
def test_email_channel_rate_limit_100_per_minute():
    """Verify email channel blocked after 100/min."""
    pass

def test_slack_channel_rate_limit_50_per_minute():
    """Verify Slack channel blocked after 50/min."""
    pass

def test_sms_channel_rate_limit_10_per_minute():
    """Verify SMS channel blocked after 10/min."""
    pass

def test_email_recipient_rate_limit_5_per_hour():
    """Verify same email recipient blocked after 5/hour."""
    pass

def test_phone_recipient_rate_limit_3_per_hour():
    """Verify same phone recipient blocked after 3/hour."""
    pass

def test_global_burst_rate_limit_500_per_minute():
    """Verify global rate limit blocks after 500/min total."""
    pass
```

**Queue Backpressure Tests (REQUIRED per review):**
```python
# tests/libs/alerts/test_delivery_service.py
def test_queue_full_returns_503_with_retry_after_header():
    """Verify 503 response includes Retry-After: 60 header."""
    pass

def test_queue_resumes_accepting_at_8000_threshold():
    """Verify queue resumes when backlog < 8000."""
    pass

def test_queue_full_increments_metric():
    """Verify alert_queue_full_total metric increments."""
    pass
```

**SLA/Performance Tests (REQUIRED per review):**
```python
# tests/libs/alerts/test_sla_performance.py
def test_delivery_p95_latency_under_60s():
    """Performance test: P95 delivery latency < 60s SLA."""
    pass

def test_delivery_latency_histogram_recorded():
    """Verify alert_delivery_latency_seconds histogram populated."""
    pass
```

**Fault Injection Tests:**
- Provider timeout: Verify retry behavior
- Redis unavailable: Verify graceful degradation
- Postgres unavailable: Verify queue continues

---

## C4: T7.3 Alert Configuration UI

### Acceptance Criteria (from P4T5_TASK.md)

- [ ] Threshold configuration form (drawdown limits, position limits, latency thresholds)
- [ ] Notification channel setup with credential masking (show last 4 chars only)
- [ ] Alert rules editor (condition → action mapping) with Pydantic validation
- [ ] Alert history table with acknowledgment tracking
- [ ] Test notification button for each channel
- [ ] PII handling: phone/email masked in UI and logs

### Component Breakdown

#### C4.1: Alert Configuration Page (1 day)
**Files to create:**
- `apps/web_console/pages/alerts.py`
- `apps/web_console/services/alert_service.py`

**Page Structure (follows health.py pattern):**
```python
# apps/web_console/pages/alerts.py
@operations_requires_auth
def render_alerts_page(user: dict[str, Any], db_pool: Any) -> None:
    """Render alert configuration page."""
    if not FEATURE_ALERTS:
        st.info("Alert configuration is disabled.")
        return

    if not has_permission(user, Permission.VIEW_ALERTS):
        st.error("Permission denied: VIEW_ALERTS required")
        st.stop()

    st.title("Alert Configuration")

    # Tab navigation
    tab1, tab2, tab3 = st.tabs(["Alert Rules", "Alert History", "Channels"])

    with tab1:
        _render_alert_rules_section(user)

    with tab2:
        _render_alert_history_section(user)

    with tab3:
        _render_channels_section(user)
```

**Service Layer (with audit event emission):**
```python
# apps/web_console/services/alert_service.py
class AlertConfigService:
    """Service for alert configuration CRUD with audit logging."""

    def __init__(self, db_pool: Any, audit_logger: AuditLogger):
        self.db_pool = db_pool
        self.audit_logger = audit_logger

    async def get_rules(self) -> list[AlertRule]:
        """Fetch all alert rules."""
        pass

    async def create_rule(self, rule: AlertRuleCreate, user: dict) -> AlertRule:
        """Create new alert rule with audit logging.

        Emits: ALERT_RULE_CREATED audit event
        """
        # 1. Validate with Pydantic
        # 2. Insert into alert_rules table
        # 3. Emit audit event
        await self.audit_logger.log_action(
            user_id=user["user_id"],
            action="ALERT_RULE_CREATED",
            resource_type="alert_rule",
            resource_id=str(rule.id),
            outcome="success",
            details={"rule_name": rule.name, "condition_type": rule.condition_type},
        )
        pass

    async def update_rule(self, rule_id: str, update: AlertRuleUpdate, user: dict) -> AlertRule:
        """Update alert rule with audit logging.

        Emits: ALERT_RULE_UPDATED audit event
        """
        await self.audit_logger.log_action(
            user_id=user["user_id"],
            action="ALERT_RULE_UPDATED",
            resource_type="alert_rule",
            resource_id=rule_id,
            outcome="success",
            details={"changes": update.dict(exclude_unset=True)},
        )
        pass

    async def delete_rule(self, rule_id: str, user: dict) -> None:
        """Delete alert rule (admin only) with audit logging.

        Emits: ALERT_RULE_DELETED audit event
        """
        await self.audit_logger.log_action(
            user_id=user["user_id"],
            action="ALERT_RULE_DELETED",
            resource_type="alert_rule",
            resource_id=rule_id,
            outcome="success",
        )
        pass

    async def acknowledge_alert(self, alert_id: str, note: str, user: dict) -> None:
        """Acknowledge alert event.

        Emits: ALERT_ACKNOWLEDGED audit event
        """
        await self.audit_logger.log_action(
            user_id=user["user_id"],
            action="ALERT_ACKNOWLEDGED",
            resource_type="alert_event",
            resource_id=alert_id,
            outcome="success",
            details={"note": note},
        )
        pass

    async def test_notification(self, channel: ChannelConfig, user: dict) -> TestResult:
        """Send test notification.

        Emits: TEST_NOTIFICATION_SENT audit event
        """
        await self.audit_logger.log_action(
            user_id=user["user_id"],
            action="TEST_NOTIFICATION_SENT",
            resource_type="notification_channel",
            resource_id=channel.type.value,
            outcome="success",
            details={"recipient_masked": mask_for_logs(channel.recipient, channel.type.value)},
        )
        pass

    # Channel CRUD operations with audit events (per review feedback)
    async def add_channel(self, rule_id: str, channel: ChannelConfig, user: dict) -> None:
        """Add notification channel to rule.

        Emits: CHANNEL_ADDED audit event
        """
        await self.audit_logger.log_action(
            user_id=user["user_id"],
            action="CHANNEL_ADDED",
            resource_type="notification_channel",
            resource_id=f"{rule_id}:{channel.type.value}",
            outcome="success",
            details={"channel_type": channel.type.value},
        )
        pass

    async def update_channel(self, rule_id: str, channel: ChannelConfig, user: dict) -> None:
        """Update notification channel configuration.

        Emits: CHANNEL_UPDATED audit event
        """
        await self.audit_logger.log_action(
            user_id=user["user_id"],
            action="CHANNEL_UPDATED",
            resource_type="notification_channel",
            resource_id=f"{rule_id}:{channel.type.value}",
            outcome="success",
            details={"channel_type": channel.type.value, "enabled": channel.enabled},
        )
        pass

    async def remove_channel(self, rule_id: str, channel_type: str, user: dict) -> None:
        """Remove notification channel from rule.

        Emits: CHANNEL_REMOVED audit event
        """
        await self.audit_logger.log_action(
            user_id=user["user_id"],
            action="CHANNEL_REMOVED",
            resource_type="notification_channel",
            resource_id=f"{rule_id}:{channel_type}",
            outcome="success",
        )
        pass
```

#### C4.2: Alert Rule Editor Component (0.5 day)
**Files to create:**
- `apps/web_console/components/alert_rule_editor.py`

**Component Pattern:**
```python
def render_alert_rule_editor(
    rule: AlertRule | None = None,
    on_save: Callable[[AlertRuleCreate | AlertRuleUpdate], None] | None = None,
) -> tuple[bool, AlertRuleCreate | AlertRuleUpdate | None]:
    """Render alert rule editor form.

    Args:
        rule: Existing rule for editing (None for new)
        on_save: Callback when save clicked

    Returns:
        (saved, rule_data)
    """
    with st.form("alert_rule_form"):
        name = st.text_input("Rule Name", value=rule.name if rule else "")

        condition_type = st.selectbox(
            "Condition Type",
            options=["drawdown", "position_limit", "latency"],
            index=0 if not rule else ["drawdown", "position_limit", "latency"].index(rule.condition_type)
        )

        threshold = st.number_input(
            "Threshold Value",
            value=float(rule.threshold_value) if rule else 0.0
        )

        comparison = st.selectbox(
            "Comparison",
            options=["gt", "lt", "gte", "lte", "eq"],
        )

        # Channel configuration (rendered separately)
        channels = _render_channel_config(rule.channels if rule else [])

        enabled = st.checkbox("Enabled", value=rule.enabled if rule else True)

        submitted = st.form_submit_button("Save Rule")

        if submitted:
            # Validate with Pydantic
            try:
                rule_data = AlertRuleCreate(
                    name=name,
                    condition_type=condition_type,
                    threshold_value=Decimal(str(threshold)),
                    comparison=comparison,
                    channels=channels,
                    enabled=enabled,
                )
                return True, rule_data
            except ValidationError as e:
                st.error(f"Validation error: {e}")
                return False, None

    return False, None
```

#### C4.3: Threshold Configuration Component (0.5 day)
**Files to create:**
- `apps/web_console/components/threshold_config.py`

**Preset Thresholds:**
```python
PRESET_THRESHOLDS = {
    "drawdown": {
        "label": "Drawdown Alert",
        "description": "Alert when drawdown exceeds threshold",
        "default_value": -0.05,  # -5%
        "comparison": "lt",
        "unit": "%",
    },
    "position_limit": {
        "label": "Position Limit Alert",
        "description": "Alert when position exceeds limit",
        "default_value": 100000,  # $100k
        "comparison": "gt",
        "unit": "$",
    },
    "latency": {
        "label": "Latency Alert",
        "description": "Alert when P95 latency exceeds threshold",
        "default_value": 1000,  # 1000ms
        "comparison": "gt",
        "unit": "ms",
    },
}

def render_threshold_config(condition_type: str) -> tuple[Decimal, str]:
    """Render threshold configuration for condition type."""
    preset = PRESET_THRESHOLDS.get(condition_type, {})
    # Render appropriate input widget based on type
    pass
```

#### C4.4: Notification Channels Component (0.5 day)
**Files to create:**
- `apps/web_console/components/notification_channels.py`

**PII Masking (STRICTLY last 4 chars only per acceptance criteria):**

**IMPORTANT - Centralized Masking Helper:**
All PII masking MUST use the centralized helper in `libs/alerts/pii.py` to ensure consistency across:
- Delivery service logs
- Web console UI
- Audit event details

Integration test verifies no raw PII in audit_log table.

```python
# libs/alerts/pii.py - CENTRALIZED HELPER
# All components MUST import from here - DO NOT duplicate masking logic

def mask_email(email: str) -> str:
    """Mask email showing ONLY last 4 chars: user@domain.com -> ***.com

    Per task doc: "show last 4 chars only" - NO local part, NO @ symbol.
    Example: "user@domain.com" has last 4 chars = ".com"
    """
    if len(email) >= 4:
        return f"***{email[-4:]}"
    return "***"

def mask_phone(phone: str) -> str:
    """Mask phone showing ONLY last 4 chars: +1234567890 -> ***7890

    Per task doc: "show last 4 chars only".
    """
    if len(phone) >= 4:
        return f"***{phone[-4:]}"
    return "***"

def mask_webhook(url: str) -> str:
    """Mask webhook showing ONLY last 4 chars: https://hooks.slack.com/xxxx -> ***xxxx

    Per task doc: "show last 4 chars only" - NO scheme, NO host.
    """
    if len(url) >= 4:
        return f"***{url[-4:]}"
    return "***"

def mask_for_logs(value: str, mask_type: str) -> str:
    """Mask value for structured logs (STRICTLY last 4 chars only).

    All PII types masked identically to comply with acceptance criteria.
    """
    if len(value) >= 4:
        return f"***{value[-4:]}"
    return "***"
```

**PII Masking Tests (must verify STRICTLY last 4 chars only):**
```python
def test_email_mask_shows_only_last_4_chars():
    """Email masking shows STRICTLY last 4 chars only - no domain, no @ symbol."""
    # "user@domain.com" -> last 4 chars = ".com"
    assert mask_email("user@domain.com") == "***.com"
    # "test@example.org" -> last 4 chars = ".org"
    assert mask_email("test@example.org") == "***.org"
    # "a@b.com" -> last 4 chars = ".com"
    assert mask_email("a@b.com") == "***.com"

def test_phone_mask_shows_only_last_4_digits():
    """Phone masking shows STRICTLY last 4 chars only."""
    # "+1234567890" -> last 4 chars = "7890"
    assert mask_phone("+1234567890") == "***7890"
    # "555-1234" -> last 4 chars = "1234"
    assert mask_phone("555-1234") == "***1234"

def test_webhook_mask_shows_only_last_4_chars():
    """Webhook masking shows STRICTLY last 4 chars only - no scheme, no host."""
    # "https://hooks.slack.com/services/T00/B00/xxxx" -> last 4 chars = "xxxx"
    assert mask_webhook("https://hooks.slack.com/services/T00/B00/xxxx") == "***xxxx"
    # "https://example.com/webhook" -> last 4 chars = "hook"
    assert mask_webhook("https://example.com/webhook") == "***hook"

def test_all_pii_types_masked_identically():
    """All PII types use same masking logic - last 4 chars only."""
    # Verify mask_for_logs uses same logic regardless of type
    assert mask_for_logs("user@domain.com", "email") == "***.com"
    assert mask_for_logs("+1234567890", "phone") == "***7890"
    assert mask_for_logs("https://hooks.slack.com/xxxx", "webhook") == "***xxxx"
```

**Component (with MANDATORY test button per T7.3 acceptance criteria):**

**Note:** Per acceptance criteria "Test notification button for each channel", the test button is MANDATORY (not optional). RBAC enforcement: Only Operator and Admin roles can test. Viewer role denied.

```python
def render_notification_channels(
    channels: list[ChannelConfig],
    user: dict[str, Any],
    alert_service: AlertConfigService,
) -> list[ChannelConfig]:
    """Render notification channel configuration with masking and test buttons.

    Test button is MANDATORY per T7.3 acceptance criteria.
    RBAC: Operator/Admin can test, Viewer cannot.
    """
    updated_channels = []
    can_test = has_permission(user, Permission.TEST_NOTIFICATION)

    for i, channel in enumerate(channels):
        with st.expander(f"{channel.type.value.title()} Channel"):
            # Show masked recipient
            masked = _mask_recipient(channel.recipient, channel.type)
            st.text(f"Recipient: {masked}")

            # Edit recipient (input masked, actual value stored)
            new_recipient = st.text_input(
                "Update recipient",
                key=f"channel_{i}_recipient",
                type="password",  # Hide input
            )

            enabled = st.checkbox(
                "Enabled",
                value=channel.enabled,
                key=f"channel_{i}_enabled"
            )

            # Test notification button - MANDATORY per acceptance criteria
            # RBAC enforced: Operator/Admin only
            if can_test:
                if st.button(f"Test {channel.type.value}", key=f"test_{i}"):
                    try:
                        result = alert_service.test_notification(channel, user)
                        if result.success:
                            st.success(f"Test notification sent to {masked}")
                        else:
                            st.error(f"Test failed: {result.error}")
                    except Exception as e:
                        st.error(f"Test failed: {e}")
            else:
                # Show disabled button for Viewer role
                st.button(
                    f"Test {channel.type.value}",
                    key=f"test_{i}",
                    disabled=True,
                    help="Requires Operator or Admin role"
                )

            updated_channels.append(ChannelConfig(
                type=channel.type,
                recipient=new_recipient if new_recipient else channel.recipient,
                enabled=enabled,
            ))

    return updated_channels
```

#### C4.5: Alert History Component (0.5 day)
**Files to create:**
- `apps/web_console/components/alert_history.py`

**Component:**
```python
def render_alert_history(
    events: list[AlertEvent],
    can_acknowledge: bool = False,
    on_acknowledge: Callable[[str, str], None] | None = None,
) -> None:
    """Render alert history table with acknowledgment."""
    if not events:
        st.info("No alert events recorded.")
        return

    # Convert to DataFrame for display
    df = pd.DataFrame([
        {
            "Time": event.triggered_at.strftime("%Y-%m-%d %H:%M:%S"),
            "Rule": event.rule_name,
            "Value": str(event.trigger_value),
            "Channels": ", ".join(event.routed_channels),
            "Acknowledged": "Yes" if event.acknowledged_at else "No",
            "Acknowledged By": event.acknowledged_by or "-",
        }
        for event in events
    ])

    st.dataframe(df, use_container_width=True)

    # Acknowledgment section
    if can_acknowledge:
        unacked = [e for e in events if not e.acknowledged_at]
        if unacked:
            st.subheader("Pending Acknowledgments")
            for event in unacked[:5]:  # Show first 5
                with st.expander(f"Alert: {event.rule_name} at {event.triggered_at}"):
                    note = st.text_area(
                        "Acknowledgment Note",
                        key=f"ack_note_{event.id}",
                        min_chars=10,
                    )
                    if st.button("Acknowledge", key=f"ack_{event.id}"):
                        if on_acknowledge and len(note) >= 10:
                            on_acknowledge(str(event.id), note)
                            st.success("Acknowledged!")
                            st.rerun()
```

#### C4.6: Integration & Feature Flags (0.5 day)
**Files to modify:**
- `apps/web_console/config.py` - Add `FEATURE_ALERTS`
- `apps/web_console/app.py` - Add alerts page to navigation
- `libs/web_console_auth/permissions.py` - Add alert permissions

**Permissions to add:**
```python
class Permission(str, Enum):
    # ... existing permissions ...
    VIEW_ALERTS = "view_alerts"
    CREATE_ALERT_RULE = "create_alert_rule"
    UPDATE_ALERT_RULE = "update_alert_rule"
    DELETE_ALERT_RULE = "delete_alert_rule"
    TEST_NOTIFICATION = "test_notification"
    ACKNOWLEDGE_ALERT = "acknowledge_alert"
```

**RBAC Matrix (from task doc):**
| Action | Viewer | Operator | Admin |
|--------|--------|----------|-------|
| View rules | Yes | Yes | Yes |
| Create/edit rules | No | Yes | Yes |
| Delete rules | No | No | Yes |
| Test notification | No | Yes | Yes |

#### C4.7: Tests (0.5 day)
**Files to create:**
- `tests/apps/web_console/test_alerts_page.py`
- `tests/apps/web_console/test_alert_components.py`
- `tests/apps/web_console/test_alert_service.py`
- `tests/apps/web_console/test_alert_rbac.py`
- `tests/apps/web_console/test_alert_audit.py`

**Test Categories:**

**Unit Tests:**
- Component rendering
- PII masking (last 4 chars display)
- Pydantic validation

**Integration Tests:**
- CRUD operations
- Audit logging to database

**RBAC Denial Tests (REQUIRED per review - per role matrix):**
```python
# tests/apps/web_console/test_alert_rbac.py
def test_viewer_cannot_create_alert_rule():
    """Viewer role should be denied CREATE_ALERT_RULE permission."""
    pass

def test_viewer_cannot_edit_alert_rule():
    """Viewer role should be denied UPDATE_ALERT_RULE permission."""
    pass

def test_viewer_cannot_delete_alert_rule():
    """Viewer role should be denied DELETE_ALERT_RULE permission."""
    pass

def test_viewer_cannot_test_notification():
    """Viewer role should be denied TEST_NOTIFICATION permission."""
    pass

def test_viewer_sees_disabled_test_button():
    """Viewer role should see test button but it's disabled."""
    pass

def test_operator_can_test_notification_emits_audit():
    """Operator test notification should emit TEST_NOTIFICATION_SENT audit event."""
    pass

def test_admin_can_test_notification_emits_audit():
    """Admin test notification should emit TEST_NOTIFICATION_SENT audit event."""
    pass

def test_operator_cannot_delete_alert_rule():
    """Operator role should be denied DELETE_ALERT_RULE permission."""
    pass

def test_operator_can_create_alert_rule():
    """Operator role should have CREATE_ALERT_RULE permission."""
    pass

def test_operator_can_test_notification():
    """Operator role should have TEST_NOTIFICATION permission."""
    pass

def test_admin_can_delete_alert_rule():
    """Admin role should have DELETE_ALERT_RULE permission."""
    pass

def test_all_roles_can_view_rules():
    """All roles (viewer, operator, admin) should have VIEW_ALERTS permission."""
    pass
```

**Audit Event Tests (REQUIRED per review):**
```python
# tests/apps/web_console/test_alert_audit.py
def test_create_rule_emits_audit_event():
    """Creating alert rule should emit ALERT_RULE_CREATED audit event."""
    pass

def test_update_rule_emits_audit_event():
    """Updating alert rule should emit ALERT_RULE_UPDATED audit event."""
    pass

def test_delete_rule_emits_audit_event():
    """Deleting alert rule should emit ALERT_RULE_DELETED audit event."""
    pass

def test_acknowledge_alert_emits_audit_event():
    """Acknowledging alert should emit ALERT_ACKNOWLEDGED audit event."""
    pass

def test_test_notification_emits_audit_event():
    """Sending test notification should emit TEST_NOTIFICATION_SENT audit event."""
    pass

def test_channel_add_emits_audit_event():
    """Adding channel should emit CHANNEL_ADDED audit event."""
    pass

def test_channel_update_emits_audit_event():
    """Updating channel should emit CHANNEL_UPDATED audit event."""
    pass

def test_channel_remove_emits_audit_event():
    """Removing channel should emit CHANNEL_REMOVED audit event."""
    pass

def test_audit_events_include_user_id():
    """All audit events should include user_id."""
    pass

def test_audit_events_include_rule_id():
    """Rule-related audit events should include rule_id as resource_id."""
    pass

def test_audit_log_never_contains_raw_pii():
    """Integration test: Verify audit_log table never contains raw PII.

    Per centralized masking requirement - ensures all audit details use masked values.
    """
    # 1. Create alert rule with email/phone recipients
    # 2. Perform various operations (create, test notification, etc.)
    # 3. Query audit_log table
    # 4. Assert no raw email/phone patterns in details column
    emails_to_check = ["user@domain.com", "test@example.org"]
    phones_to_check = ["+1234567890", "+0987654321"]

    # Query all audit log entries for alert-related actions
    audit_entries = db.execute(
        "SELECT details FROM audit_log WHERE action LIKE 'ALERT_%' OR action LIKE 'CHANNEL_%'"
    ).fetchall()

    for entry in audit_entries:
        details = entry["details"]
        for email in emails_to_check:
            assert email not in str(details), f"Raw email {email} found in audit log"
        for phone in phones_to_check:
            assert phone not in str(details), f"Raw phone {phone} found in audit log"

# Audit Write Latency SLA Test (per task doc: <1s)
@pytest.mark.performance
def test_audit_write_latency_under_1s():
    """Audit log write latency should be < 1s per SLA.

    Per task doc: 'Audit log write latency | <1s | Time from action to audit record commit'
    """
    import time
    start = time.perf_counter()
    # Perform audit write
    audit_logger.log_action(...)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"Audit write took {elapsed:.3f}s, exceeds 1s SLA"
```

**PII Masking Tests (STRICTLY last 4 chars only):**
```python
def test_email_mask_shows_strictly_last_4_chars():
    """Email masking shows STRICTLY last 4 chars - no domain visible."""
    # "user@domain.com" -> "***.com" (last 4 chars of full string)
    assert mask_email("user@domain.com") == "***.com"

def test_phone_mask_shows_strictly_last_4_digits():
    """Phone masking shows STRICTLY last 4 chars."""
    assert mask_phone("+1234567890") == "***7890"

def test_log_sanitizer_masks_pii_strictly():
    """Verify log output shows ONLY last 4 chars - no full email/phone/domain."""
    import io
    import logging

    # Capture log output
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    logger = logging.getLogger("test_pii")
    logger.addHandler(handler)

    # Log masked values
    logger.info(f"Email: {mask_email('user@domain.com')}")
    logger.info(f"Phone: {mask_phone('+1234567890')}")

    output = log_stream.getvalue()

    # Verify NO full PII in logs
    assert "user@domain.com" not in output
    assert "+1234567890" not in output
    assert "@domain" not in output  # No domain visible

    # Verify only last 4 chars visible
    assert "***.com" in output
    assert "***7890" in output
```

---

## Implementation Order

### Phase 1: C3 Alert Delivery Service (4-5 days)

| Step | Component | Effort | Dependencies |
|------|-----------|--------|--------------|
| 1 | C3.1 Database Schema & Models | 0.5d | None |
| 2 | C3.2 Dedup & Rate Limiting | 0.5d | C3.1 |
| 3 | C3.3 Channel Handlers | 1d | C3.1, C3.2 |
| 4 | C3.4 Delivery Service & Retry | 1d | C3.2, C3.3 |
| 5 | C3.5 Alert Manager & RQ Worker | 1d | C3.4 |
| 6 | C3.6 Prometheus Metrics | 0.5d | C3.4 |
| 7 | C3.7 Tests | 1d | All above |

### Phase 2: C4 Alert Configuration UI (3-4 days)

| Step | Component | Effort | Dependencies |
|------|-----------|--------|--------------|
| 1 | C4.1 Alert Configuration Page | 1d | C3 complete |
| 2 | C4.2 Alert Rule Editor | 0.5d | C4.1 |
| 3 | C4.3 Threshold Configuration | 0.5d | C4.2 |
| 4 | C4.4 Notification Channels | 0.5d | C4.2 |
| 5 | C4.5 Alert History | 0.5d | C4.1 |
| 6 | C4.6 Integration & Flags | 0.5d | C4.1-C4.5 |
| 7 | C4.7 Tests | 0.5d | All above |

---

## 6-Step Pattern Per Component

For each sub-component (C3.1-C3.7, C4.1-C4.7):

1. **Plan**: Document specific implementation details
2. **Plan Review**: Request fresh zen-mcp review (no continuation_id)
3. **Implement**: Write code following established patterns
4. **Test**: Create unit/integration tests (TDD)
5. **Code Review**: Request fresh zen-mcp review (no continuation_id)
6. **Commit**: After review approval + CI passes

---

## Review Strategy

### Fresh, Unbiased Reviews (CRITICAL)

**DO:**
- Request completely new review for each component
- Never reuse `continuation_id`
- Provide full context in each review request
- Request independent reviews from both Gemini and Codex

**DON'T:**
- Resume previous review sessions
- Bias reviewer with "I fixed X, please approve"
- Skip either Gemini or Codex review

### Review Request Template

```
Review the implementation of [Component Name] for P4T5 Alert Delivery Service.

Files to review:
- [file1.py]
- [file2.py]
- [test_file.py]

Acceptance criteria:
- [Criterion 1]
- [Criterion 2]

Please provide comprehensive independent review covering:
1. Trading safety implications
2. Security (PII handling, injection prevention)
3. Error handling and retry logic
4. Test coverage
5. Pattern parity with existing code
```

---

## Delegation Strategy

### Component-by-Component Delegation to Codex

For implementation, delegate to Codex in small chunks:

1. **C3.1**: "Implement database migration 0011 and Pydantic models for alerts"
2. **C3.2**: "Implement dedup key generation and rate limiter for alerts"
3. **C3.3**: "Implement email, Slack, SMS channel handlers"
4. etc.

**Do NOT delegate entire C3 or C4 at once.**

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| External service failures | Mock SMTP/Slack/Twilio in tests; integration tests use sandbox |
| Rate limit bypass | Unit tests for rate limit logic; integration tests verify Redis |
| PII leak | Log sanitizer tests; code review focus on masking |
| Queue overflow | MAX_QUEUE_DEPTH with 503 response; metric + alert |
| Poison queue growth | Metric alert at >10 items; manual review process |

---

## Documentation to Create

- [ ] `docs/CONCEPTS/alert-delivery.md` (C3)
- [ ] `docs/CONCEPTS/alerting.md` (C4)
- [ ] `docs/ADRs/ADR-0029-alerting-system.md` (architecture decisions)

---

## Success Criteria

### C3 Complete When:
- [ ] All channel handlers work with mock providers
- [ ] Deduplication prevents duplicate deliveries
- [ ] Rate limiting enforced (per-channel, per-recipient, global)
- [ ] Retry with exponential backoff (1s, 2s, 4s)
- [ ] Poison queue captures failed deliveries
- [ ] Prometheus metrics exported
- [ ] All tests pass
- [ ] Code review approved (fresh, unbiased)

### C4 Complete When:
- [ ] Alert rules CRUD functional
- [ ] PII masked in UI and logs (show last 4 chars)
- [ ] Test notification works
- [ ] Alert history displays with acknowledgment
- [ ] RBAC enforced (viewer/operator/admin)
- [ ] RBAC denial tests pass
- [ ] Audit events emitted for all CRUD operations
- [ ] Integrated into web console navigation
- [ ] All tests pass
- [ ] Code review approved (fresh, unbiased)

---

## Revision History

### Revision 8 (2025-12-20)

**Review feedback addressed from Codex (C3.3 plan review, 2nd round):**
1. ✅ **Email timeout mapping**: Added concrete _send_smtp and _send_sendgrid implementations with 10s timeout and typed exception handling
2. ✅ **SendGrid spec**: Documented endpoint, headers, JSON payload, and x-message-id extraction
3. ✅ **Slack Retry-After**: Added capture of retry-after header to metadata and status-based error mapping
4. ✅ **SMS retryable status-based**: Changed to use TwilioRestException.status for retryable mapping (429/5xx = retryable)
5. ✅ **PII tests**: Added C3.3 Channel Handler Tests section with PII guard tests for all channels

### Revision 7 (2025-12-20)

**Review feedback addressed from Codex (C3.3 plan review):**
1. ✅ **DeliveryResult undefined**: Added DeliveryResult model to libs/alerts/models.py with success, message_id, error, retryable, metadata fields
2. ✅ **Rate limit integration gaps**: Clarified that DeliveryService (C3.4) enforces rate limits, handlers are pure I/O
3. ✅ **PII masking uncertain**: Added mandatory PII Logging Policy requiring mask_recipient in ALL log statements
4. ✅ **SMTP→SendGrid fallback unspecified**: Documented fallback policy including 10s timeout, error mapping to retryable
5. ✅ **Sync vs async I/O choice**: Specified async-friendly clients: aiosmtplib, httpx, Twilio via run_in_executor

### Revision 6 (2025-12-20)

**Review feedback addressed from Codex (5th review):**
1. ✅ **Dedup key spec mismatch**: Documented recipient hashing as intentional security enhancement (raw recipient in dedup_key would expose PII in database)
2. ✅ **Test notification button mandatory**: Made test button MANDATORY per T7.3 acceptance criteria, added RBAC enforcement (Operator/Admin only), disabled button shown for Viewer role
3. ✅ **Test notification RBAC tests**: Added test_viewer_sees_disabled_test_button, test_operator_can_test_notification_emits_audit, test_admin_can_test_notification_emits_audit

**Review feedback addressed from Gemini (5th review - non-blocking recommendations):**
1. ✅ **Rate limiter pattern distinction**: Added note documenting Fixed Window (INCR+EXPIRE) vs existing Sliding Window (ZSET) patterns
2. ✅ **Centralized PII masking**: Added `libs/alerts/pii.py` as centralized helper, added integration test `test_audit_log_never_contains_raw_pii`

### Revision 5 (2025-12-20)

**Review feedback addressed from Codex (4th review):**
1. ✅ **Recipient hash secret governance**: Added secrets manager storage requirement + quarterly rotation policy + validation tests
2. ✅ **PII masking example text**: Fixed docstring example to show "***.com" (not "***main")

### Revision 4 (2025-12-20)

**Review feedback addressed from Gemini + Codex (3rd review):**
1. ✅ **PII test expectations**: Fixed ALL test assertions to match implementation
   - `mask_email("user@domain.com")` → `"***.com"` (not `"***user@domain.com"`)
   - `mask_webhook("...xxxx")` → `"***xxxx"` (not `"***/xxx"`)
2. ✅ **Test/implementation consistency**: All PII masking tests now verify STRICTLY last 4 chars only

### Revision 3 (2025-12-20)

**Review feedback addressed from Codex (2nd review):**
1. ✅ **PII masking spec mismatch**: Fixed to show STRICTLY last 4 chars only (no domain, no @ symbol)
2. ✅ **Audit coverage for channel CRUD**: Added CHANNEL_ADDED, CHANNEL_UPDATED, CHANNEL_REMOVED audit events
3. ✅ **Audit write latency SLA**: Added test_audit_write_latency_under_1s performance test
4. ✅ **Rate limiter TTL/window**: Added explicit 60s/3600s TTL values and Lua script for atomicity
5. ✅ **Alertmanager routes integration**: Added routes.yml config and test for alert rules deployed
6. ✅ **Status tracking constraints**: Added CHECK constraints for status, channel, attempts + poison_at timestamp

### Revision 2 (2025-12-20)

**Review feedback addressed from Gemini:**
1. ✅ **Migration numbering**: Confirmed all references use 0011 (0010 exists)
2. ✅ **docker-compose.yml**: Added alert_worker service definition to C3.5
3. ✅ **Dependencies timing**: Moved pyproject.toml updates to Phase 1 (C3.3)

**Review feedback addressed from Codex:**
1. ✅ **Rate limit thresholds**: Added explicit thresholds (100/50/10 per channel, 5/3 per recipient, 500 global)
2. ✅ **Rate limiter parity**: Changed to extend existing `libs/web_console_auth/rate_limiter.py`
3. ✅ **Queue backpressure**: Added `Retry-After: 60` header, tests for 8000 resume threshold
4. ✅ **Poison queue alerting**: Added Prometheus alert rule (>10 triggers page)
5. ✅ **Secrets handling**: Added secrets manager integration for channel credentials
6. ✅ **SLA/perf tests**: Added P95 <60s performance test and Prometheus alert rule
7. ✅ **PII masking**: Updated to show last 4 chars per acceptance criteria
8. ✅ **RBAC denial tests**: Added comprehensive role matrix tests
9. ✅ **Audit events**: Added audit event emission for all CRUD operations
10. ✅ **Retry/poison tests**: Added explicit tests for 3-attempt limit and poison queue behavior

### Revision 1 (2025-12-20)
- Initial plan creation
