# P4T5 Component 5: Admin Dashboard - Implementation Plan

**Component:** C5 - Admin Dashboard (T7.4)
**Branch:** `feature/P4T5-C5-admin-dashboard`
**Created:** 2025-12-20
**Updated:** 2025-12-20 (Rev 2 - Addressing plan review feedback)
**Status:** PLANNING

---

## 1. Objective

Implement the Admin Dashboard for the trading platform web console, providing:
- API key management with secure lifecycle (generate, hash, rotate, revoke)
- System configuration editor (trading hours, limits, defaults)
- Audit log viewer with filtering and PII masking
- Integration with existing user management functionality
- **NEW:** Last Active IP tracking for user sessions (T7.4 requirement)

**Success Criteria:**
- API keys use 256-bit entropy with SHA-256 salted hashing
- One-time key display on creation (never shown again)
- PII is masked in UI and logs (`***@domain.com`, `***1234`)
- All admin actions are audited with explicit event types
- RBAC: Admin role required for all operations (MANAGE_API_KEYS permission)
- Revocation propagates via Redis cache (5-min TTL) with DB fallback
- Last Active IP displayed in user management table
- Config changes persisted to PostgreSQL with audit trail

---

## 2. Pre-Implementation Analysis Results

### 2.1 Existing Components (Reuse)

| Component | Location | Status |
|-----------|----------|--------|
| User management | `apps/web_console/pages/admin_users.py` | Exists - role/strategy CRUD |
| Role editor | `apps/web_console/components/user_role_editor.py` | Exists - role change with confirmation |
| Strategy assignment | `apps/web_console/components/strategy_assignment.py` | Exists - grant/revoke strategies |
| Bulk operations | `apps/web_console/components/bulk_operations.py` | Exists - bulk role/strategy changes |
| CSRF protection | `apps/web_console/components/csrf_protection.py` | Exists - token generation/verification |
| Permissions/RBAC | `libs/web_console_auth/permissions.py` | Exists - Role/Permission enums |
| Audit logging | `apps/web_console/auth/audit_log.py` | Exists - AuditLogger class |
| Session management | `apps/web_console/auth/session_manager.py` | Exists - @require_auth decorator |
| DB pool | `apps/web_console/utils/db_pool.py` | Exists - AsyncConnectionAdapter |

### 2.2 New Components (To Create)

| Component | Location | Purpose |
|-----------|----------|---------|
| API Keys Library | `libs/admin/api_keys.py` | Key generation, hashing, validation |
| DB Migration | `db/migrations/0011_create_api_keys.sql` | API keys table schema |
| Log Sanitizer | `libs/common/log_sanitizer.py` | PII masking utility |
| API Key Manager | `apps/web_console/components/api_key_manager.py` | Key lifecycle UI |
| Config Editor | `apps/web_console/components/config_editor.py` | System settings UI |
| Audit Log Viewer | `apps/web_console/components/audit_log_viewer.py` | Query/filter audit logs |
| Admin Page | `apps/web_console/pages/admin.py` | Main admin dashboard |
| Tests | `tests/libs/admin/`, `tests/apps/web_console/` | Comprehensive test coverage |

### 2.3 Database Schema

**Current State:**
- `user_roles` table exists (0006) - **MISSING: last_active_ip column**
- `user_strategy_access` table exists (0006)
- `audit_log` table exists (0004, 0005)
- `system_config` table - **DOES NOT EXIST** (need to create)
- Latest migration: 0010

**Migration Coordination Note:**
- T7.5 (Alert Delivery Service - C3) may also need migrations
- If C3 ships first, use 0012 for C5 migrations
- Current plan: 0011 for C5 (API keys + user_roles update + system_config)

**New Schema (0011):**
```sql
-- Enable pgcrypto if not already enabled
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 1. Add last_active_ip to user_roles (T7.4 requirement)
ALTER TABLE user_roles
    ADD COLUMN IF NOT EXISTS last_active_ip INET,
    ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_user_roles_last_active ON user_roles(last_active_at DESC);

-- 2. API Keys table
CREATE TABLE IF NOT EXISTS api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    key_hash VARCHAR(255) NOT NULL,  -- SHA-256 salted hash
    key_salt VARCHAR(64) NOT NULL,   -- 16 bytes hex
    key_prefix VARCHAR(20) NOT NULL,
    scopes JSONB NOT NULL DEFAULT '[]',
    expires_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    -- Constraints
    CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES user_roles(user_id) ON DELETE CASCADE,
    CONSTRAINT uq_key_prefix UNIQUE (key_prefix),
    CONSTRAINT chk_key_prefix_format CHECK (key_prefix ~ '^tp_live_[a-zA-Z0-9]{8}$')
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys(key_prefix);
CREATE INDEX IF NOT EXISTS idx_api_keys_active ON api_keys(user_id) WHERE revoked_at IS NULL;

-- 3. System Configuration table (canonical store for admin-editable config)
CREATE TABLE IF NOT EXISTS system_config (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    config_key VARCHAR(100) NOT NULL UNIQUE,
    config_value JSONB NOT NULL,
    config_type VARCHAR(50) NOT NULL,  -- trading_hours, position_limits, system_defaults
    updated_by VARCHAR(255) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_system_config_key ON system_config(config_key);
CREATE INDEX IF NOT EXISTS idx_system_config_type ON system_config(config_type);
```

---

## 3. Component Breakdown (6-Step Pattern)

### C5.1: API Keys Library (`libs/admin/api_keys.py`)

**Purpose:** Core security module for API key lifecycle

**Deliverables:**
- `generate_api_key() -> tuple[str, str, str]` - Returns (full_key, prefix, salt)
- `hash_api_key(key: str, salt: str) -> str` - SHA-256 salted hash
- `validate_api_key(key: str, key_hash: str, key_salt: str) -> bool` - Verify key
- `parse_key_prefix(key: str) -> str` - Extract prefix from full key
- `ApiKeyScopes` Pydantic model for scope validation
- `REVOKED_KEY_CACHE_TTL = 300` (5 minutes)
- `is_key_revoked(prefix: str, redis_client, db_pool) -> bool` - Check revocation status
- `update_last_used(prefix: str, db_pool) -> None` - Debounced update (1-min resolution)

**Security Requirements (from task):**
- Key format: 32 random bytes, base64url encoded = 43 chars
- Key prefix: `tp_live_{first8chars}` for identification (regex: `^tp_live_[a-zA-Z0-9]{8}$`)
- SHA-256 salted hashing (16-byte random salt per key)
- Never log full key, salt, or hash - only prefix and last 4 chars

**Revocation Check Flow (Cache-First):**
```python
async def is_key_revoked(prefix: str, redis_client, db_pool) -> bool:
    """Check if API key is revoked. Cache-first with DB fallback."""
    # 1. Check Redis blacklist first (fast path)
    cache_key = f"api_key_revoked:{prefix}"
    if redis_client:
        try:
            if await redis_client.exists(cache_key):
                return True  # Key is revoked (cached)
        except RedisError:
            pass  # Fall through to DB check

    # 2. DB fallback (cold cache or Redis unavailable)
    async with acquire_connection(db_pool) as conn:
        row = await conn.fetchone(
            "SELECT revoked_at FROM api_keys WHERE key_prefix = %s",
            (prefix,),
        )
        if row and row["revoked_at"]:
            # Populate cache for next time
            if redis_client:
                try:
                    await redis_client.setex(cache_key, REVOKED_KEY_CACHE_TTL, "1")
                except RedisError:
                    pass
            return True
    return False
```

**Last Used Debounce (1-min resolution):**
```python
async def update_last_used(prefix: str, db_pool, redis_client) -> None:
    """Update last_used_at with 1-minute debounce."""
    debounce_key = f"api_key_last_used:{prefix}"
    current_minute = datetime.utcnow().replace(second=0, microsecond=0)

    # Check if already updated this minute
    if redis_client:
        try:
            if await redis_client.get(debounce_key):
                return  # Already updated this minute
            await redis_client.setex(debounce_key, 60, "1")
        except RedisError:
            pass  # Continue with update

    # Update DB
    async with acquire_connection(db_pool) as conn:
        await conn.execute(
            "UPDATE api_keys SET last_used_at = %s WHERE key_prefix = %s",
            (current_minute, prefix),
        )
```

**Dependencies:**
- `secrets` (stdlib)
- `hashlib` (stdlib)
- `pydantic`
- `redis.asyncio` (optional, for cache)

**Integration Point: API Key Validation Middleware**

The `is_key_revoked` and `update_last_used` functions are called from API authentication middleware:

**File:** `libs/web_console_auth/gateway_auth.py` (or new `api_key_auth.py`)

```python
from libs.platform.admin.api_keys import (
    validate_api_key,
    is_key_revoked,
    update_last_used,
    parse_key_prefix,
)

async def authenticate_api_key(
    api_key: str,
    db_pool,
    redis_client,
) -> AuthenticatedUser | None:
    """Authenticate request via API key header.

    Called from FastAPI dependency or Streamlit middleware when
    Authorization header contains 'Bearer tp_live_*'.
    """
    prefix = parse_key_prefix(api_key)
    if not prefix:
        return None  # Invalid format

    # 1. Check revocation (cache-first)
    if await is_key_revoked(prefix, redis_client, db_pool):
        logger.warning("api_key_revoked", key_prefix=prefix)
        return None

    # 2. Fetch key from DB and validate
    async with acquire_connection(db_pool) as conn:
        row = await conn.fetchone(
            "SELECT user_id, key_hash, key_salt, scopes, expires_at "
            "FROM api_keys WHERE key_prefix = %s AND revoked_at IS NULL",
            (prefix,),
        )
        if not row:
            return None  # Key not found

        # Check expiration
        if row["expires_at"] and row["expires_at"] < datetime.utcnow():
            return None

        # Validate hash
        if not validate_api_key(api_key, row["key_hash"], row["key_salt"]):
            return None

    # 3. Update last_used (debounced)
    await update_last_used(prefix, db_pool, redis_client)

    # 4. Return authenticated user context
    return AuthenticatedUser(
        user_id=row["user_id"],
        role=await _get_user_role(row["user_id"], db_pool),
        strategies=await _get_user_strategies(row["user_id"], db_pool),
        session_version=0,  # API keys don't use session versioning
        request_id=get_or_create_trace_id(),
    )
```

**Usage in FastAPI:**
```python
# In apps/execution_gateway/main.py or similar
from libs.platform.web_console_auth.api_key_auth import authenticate_api_key

async def get_current_api_user(
    authorization: str = Header(None),
    db_pool = Depends(get_db_pool),
    redis = Depends(get_redis),
) -> AuthenticatedUser:
    if not authorization or not authorization.startswith("Bearer tp_live_"):
        raise HTTPException(401, "Invalid API key")
    api_key = authorization.replace("Bearer ", "")
    user = await authenticate_api_key(api_key, db_pool, redis)
    if not user:
        raise HTTPException(401, "Invalid or revoked API key")
    return user
```

**Tests:**
- `tests/libs/admin/test_api_keys.py`
- Key generation entropy validation
- Hash/verify round-trip
- Prefix uniqueness and format validation
- Scope validation
- Revocation cache hit/miss scenarios
- Last used debounce behavior
- Redis unavailable fallback
- **Integration test:** Full auth flow via middleware

---

### C5.2: Database Migration (`db/migrations/0011_create_api_keys.sql`)

**Purpose:** Create API keys table, add last_active_ip to user_roles, create system_config table

**Deliverables:**
- Idempotent migration (IF NOT EXISTS / IF EXISTS patterns)
- pgcrypto extension enabled
- `user_roles.last_active_ip` and `user_roles.last_active_at` columns
- `api_keys` table with constraints and indexes
- `system_config` table for admin-editable configuration
- CHECK constraint on key_prefix format

**Full Migration Script:**
```sql
-- Migration: 0011_create_api_keys.sql
-- Purpose: API keys, user last_active_ip, system configuration
-- Author: Claude Code (P4T5-C5)
-- Date: 2025-12-20

-- Enable pgcrypto if not already enabled
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 1. Add last_active_ip to user_roles (T7.4 requirement)
ALTER TABLE user_roles
    ADD COLUMN IF NOT EXISTS last_active_ip INET,
    ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_user_roles_last_active ON user_roles(last_active_at DESC);

-- 2. API Keys table with security constraints
CREATE TABLE IF NOT EXISTS api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    key_hash VARCHAR(255) NOT NULL,
    key_salt VARCHAR(64) NOT NULL,
    key_prefix VARCHAR(20) NOT NULL,
    scopes JSONB NOT NULL DEFAULT '[]',
    expires_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT fk_api_keys_user FOREIGN KEY (user_id)
        REFERENCES user_roles(user_id) ON DELETE CASCADE,
    CONSTRAINT uq_api_keys_prefix UNIQUE (key_prefix),
    CONSTRAINT chk_key_prefix_format CHECK (key_prefix ~ '^tp_live_[a-zA-Z0-9]{8}$')
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys(key_prefix);
CREATE INDEX IF NOT EXISTS idx_api_keys_active ON api_keys(user_id) WHERE revoked_at IS NULL;

COMMENT ON TABLE api_keys IS 'API keys for programmatic access with salted SHA-256 hashing';
COMMENT ON COLUMN api_keys.key_hash IS 'SHA-256 hash of key with per-key salt';
COMMENT ON COLUMN api_keys.key_salt IS '16-byte hex salt for this key';
COMMENT ON COLUMN api_keys.key_prefix IS 'tp_live_{first8chars} for identification';

-- 3. System Configuration table (canonical store)
CREATE TABLE IF NOT EXISTS system_config (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    config_key VARCHAR(100) NOT NULL UNIQUE,
    config_value JSONB NOT NULL,
    config_type VARCHAR(50) NOT NULL,
    updated_by VARCHAR(255) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_config_type CHECK (config_type IN ('trading_hours', 'position_limits', 'system_defaults'))
);

CREATE INDEX IF NOT EXISTS idx_system_config_key ON system_config(config_key);
CREATE INDEX IF NOT EXISTS idx_system_config_type ON system_config(config_type);

COMMENT ON TABLE system_config IS 'Admin-editable system configuration with audit trail';
```

**Tests:**
- Migration runs idempotently (can be applied multiple times)
- Constraints enforced (prefix format, unique prefix)
- Foreign key cascade works
- Indexes created

---

### C5.3: Log Sanitizer (`libs/common/log_sanitizer.py`)

**Purpose:** PII masking utility for logs and UI

**Deliverables:**
- `mask_email(email: str) -> str` - Returns `***@domain.com`
- `mask_phone(phone: str) -> str` - Returns `***1234`
- `mask_api_key(key: str) -> str` - Returns `tp_live_xxx...xxxx`
- `sanitize_dict(data: dict) -> dict` - Recursively sanitize dict values
- `sanitize_log_record(record: dict) -> dict` - Apply all masks to log record
- `SanitizingFormatter(logging.Formatter)` - Logging formatter with auto-masking

**Patterns:**
```python
EMAIL_PATTERN = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
PHONE_PATTERN = re.compile(r'\+?[\d\s\-\(\)]{10,}')
API_KEY_PATTERN = re.compile(r'tp_live_[a-zA-Z0-9_-]{35,}')
```

**Formatter Integration (Exact Insertion Points):**

The `SanitizingFormatter` must be wired at ALL logging sinks to prevent PII leaks:

**1. Console Handler (StreamHandler):**
```python
# In libs/common/logging/config.py
import logging
from libs.core.common.log_sanitizer import SanitizingFormatter

def configure_logging(service_name: str) -> None:
    """Configure logging with PII sanitization for ALL handlers."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Remove existing handlers to avoid duplicates
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    # Console handler with sanitization
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(SanitizingFormatter(
        fmt='%(asctime)s %(levelname)s %(name)s %(message)s',
        datefmt='%Y-%m-%dT%H:%M:%S%z',
    ))
    root.addHandler(console_handler)

    # File handler (if used) - MUST also use SanitizingFormatter
    if os.getenv("LOG_FILE"):
        file_handler = logging.FileHandler(os.getenv("LOG_FILE"))
        file_handler.setFormatter(SanitizingFormatter(
            fmt='%(asctime)s %(levelname)s %(name)s %(message)s',
        ))
        root.addHandler(file_handler)

    # JSON handler (for structured logging) - wrap JSONFormatter
    if os.getenv("LOG_JSON", "false").lower() == "true":
        json_handler = logging.StreamHandler()
        json_handler.setFormatter(SanitizingJSONFormatter(service_name=service_name))
        root.addHandler(json_handler)
```

**2. AuditLogger (Separate Path):**
```python
# AuditLogger writes directly to DB via SQL, not through logging.
# Sanitization is applied at write time in AuditLogger._write():

async def _write(self, ..., details: dict | None = None, ...):
    # Sanitize details before DB insert
    sanitized_details = sanitize_dict(details) if details else None
    await conn.execute(
        "INSERT INTO audit_log (..., details) VALUES (..., %s)",
        (..., json.dumps(sanitized_details)),
    )
```

**3. Web Console Entry Point:**
```python
# In apps/web_console/app.py (called once at startup)
from libs.core.common.logging.config import configure_logging

# Call early in app initialization
configure_logging(service_name="web_console")
```

**4. FastAPI Services:**
```python
# In apps/execution_gateway/main.py, apps/signal_service/main.py, etc.
from libs.core.common.logging.config import configure_logging

@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(service_name="execution_gateway")
    yield
```

**UI Masking (for audit_log_viewer):**
```python
def mask_pii_in_details(details: dict) -> dict:
    """Mask PII in audit log details for UI display."""
    return sanitize_dict(details)
```

**Tests:**
- `tests/libs/common/test_log_sanitizer.py`
- Email masking (various formats)
- Phone masking (international, domestic)
- API key masking
- Nested dict sanitization
- Log record sanitization
- **Negative test:** Verify no raw email/phone in sanitized output

---

### C5.4: API Key Manager Component (`apps/web_console/components/api_key_manager.py`)

**Purpose:** UI for API key lifecycle management

**Deliverables:**
- `render_api_key_manager(user, db_pool, audit_logger, redis_client)` - Main render function
- Create key form (name, scopes, optional expiry)
- One-time key display modal (copy button, never shown again)
- Key list table (prefix, name, scopes, last_used, created, status)
- Revoke action with confirmation dialog
- Rotation workflow with guardrails

**UI Flow - Create Key:**
1. User clicks "Create API Key"
2. Form: name, scopes (checkboxes), expiry (optional)
3. Submit -> Generate key -> Store hash in DB
4. Show one-time modal: full key with copy button, warning message
5. Require "I have copied this key" checkbox before closing
6. After close: only prefix shown in list

**Rotation Workflow (Detailed):**
```
┌─────────────────────────────────────────────────────────────────┐
│  ROTATION WORKFLOW                                              │
├─────────────────────────────────────────────────────────────────┤
│  1. User clicks "Rotate" on existing key                        │
│     ↓                                                           │
│  2. Warning dialog: "This will create a new key. The old key    │
│     will remain valid until you explicitly revoke it."          │
│     [Cancel] [Continue]                                         │
│     ↓                                                           │
│  3. Create new key (same name + "_rotated_YYYYMMDD")            │
│     ↓                                                           │
│  4. Show new key in one-time modal (copy + checkbox required)   │
│     ↓                                                           │
│  5. After modal close, show both keys in list:                  │
│     - New key: Active                                           │
│     - Old key: "Pending Revocation" with [Revoke Now] button    │
│     ↓                                                           │
│  6. Grace period: Old key remains valid until explicit revoke   │
│     (NO automatic timeout - user controls migration timing)     │
│     ↓                                                           │
│  7. User clicks [Revoke Now] when migration complete            │
│     - Confirmation: "Are you sure? This cannot be undone."      │
│     - Revoke sets revoked_at + adds to Redis blacklist          │
└─────────────────────────────────────────────────────────────────┘
```

**Revocation Flow:**
1. User clicks "Revoke" on key
2. Confirmation dialog with reason text (min 20 chars)
3. On confirm:
   - Set `revoked_at` in DB
   - Add prefix to Redis blacklist (TTL=5min)
   - Log audit event
4. Key disappears from active list, moves to "Revoked Keys" section

**RBAC:** MANAGE_API_KEYS permission required (checked at component level)

**Tests:**
- `tests/apps/web_console/test_api_key_manager.py`
- Create flow (mocked DB)
- Revoke flow with confirmation
- Rotation workflow (create new -> old pending -> revoke old)
- One-time display checkbox enforcement
- Scope validation
- CSRF protection
- **Security test:** Revoked key replay fails auth

---

### C5.5: Config Editor Component (`apps/web_console/components/config_editor.py`)

**Purpose:** System configuration editor for trading parameters

**Deliverables:**
- `render_config_editor(user, db_pool, audit_logger)` - Main render function
- Trading hours configuration (market open/close times)
- Position limits (per-symbol max, total notional)
- System defaults (DRY_RUN, circuit breaker thresholds)
- Audit logging for all config changes

**Storage Decision: PostgreSQL (Canonical Store)**
- **Rationale:** Consistent with existing patterns (user_roles, audit_log in Postgres)
- **Table:** `system_config` (created in migration 0011)
- **Caching:** Optional Redis cache with 5-min TTL for read performance
- **Cache Keys:** `system_config:{config_key}` (e.g., `system_config:trading_hours`)
- **Invalidation:** On write, delete cache key before DB update

**Cache Invalidation Pattern:**
```python
async def save_config(...):
    # 1. Delete cache first (prevents stale reads during update)
    cache_key = f"system_config:{config_key}"
    if redis_client:
        try:
            await redis_client.delete(cache_key)
        except RedisError:
            pass  # Cache miss is acceptable

    # 2. Update DB (as shown above)
    ...

async def get_config(config_key: str, db_pool, redis_client, config_class):
    # 1. Try cache first
    cache_key = f"system_config:{config_key}"
    if redis_client:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                return config_class.model_validate_json(cached)
        except RedisError:
            pass

    # 2. Fall back to DB
    async with acquire_connection(db_pool) as conn:
        row = await conn.fetchone(...)
        if row:
            config = config_class.model_validate(row["config_value"])
            # Populate cache for next time
            if redis_client:
                try:
                    await redis_client.setex(cache_key, 300, config.model_dump_json())
                except RedisError:
                    pass
            return config

    return config_class()  # Defaults
```

**Extensibility Note:**
- `config_type` CHECK constraint allows: `trading_hours`, `position_limits`, `system_defaults`
- To add new types: Create migration to ALTER CHECK constraint
- Migration pattern: `ALTER TABLE system_config DROP CONSTRAINT chk_config_type, ADD CONSTRAINT chk_config_type CHECK (config_type IN (...))`

**Config Categories with Pydantic Validation:**
```python
from datetime import time
from decimal import Decimal
from pydantic import BaseModel, Field, field_validator

class TradingHoursConfig(BaseModel):
    """Trading hours configuration with validation."""
    market_open: time = time(9, 30)
    market_close: time = time(16, 0)
    pre_market_enabled: bool = False
    after_hours_enabled: bool = False

    @field_validator('market_close')
    @classmethod
    def close_after_open(cls, v: time, info) -> time:
        if 'market_open' in info.data and v <= info.data['market_open']:
            raise ValueError('market_close must be after market_open')
        return v

class PositionLimitsConfig(BaseModel):
    """Position limits with range validation."""
    max_position_per_symbol: int = Field(default=1000, ge=1, le=100000)
    max_notional_total: Decimal = Field(default=Decimal("100000"), ge=Decimal("1000"), le=Decimal("10000000"))
    max_open_orders: int = Field(default=10, ge=1, le=1000)

class SystemDefaultsConfig(BaseModel):
    """System defaults with safety constraints."""
    dry_run: bool = True
    circuit_breaker_enabled: bool = True
    drawdown_threshold: Decimal = Field(default=Decimal("0.05"), ge=Decimal("0.01"), le=Decimal("0.50"))

    @field_validator('circuit_breaker_enabled')
    @classmethod
    def warn_on_disable(cls, v: bool) -> bool:
        # Note: Disabling circuit breaker requires additional confirmation in UI
        return v
```

**Config CRUD Operations:**
```python
async def get_config(config_key: str, db_pool, config_class: type[BaseModel]) -> BaseModel:
    """Load config from DB, return default if not found."""
    async with acquire_connection(db_pool) as conn:
        row = await conn.fetchone(
            "SELECT config_value FROM system_config WHERE config_key = %s",
            (config_key,),
        )
        if row:
            return config_class.model_validate(row["config_value"])
        return config_class()  # Return defaults

async def save_config(
    config_key: str,
    config: BaseModel,
    config_type: str,
    user_id: str,
    db_pool,
    redis_client,  # REQUIRED: For cache invalidation
    audit_logger,
) -> None:
    """Save config to DB with cache invalidation and audit logging."""
    cache_key = f"system_config:{config_key}"

    # 1. Delete cache FIRST (prevents stale reads during update)
    if redis_client:
        try:
            await redis_client.delete(cache_key)
        except RedisError:
            pass  # Cache miss is acceptable

    # 2. Fetch old value for audit trail, then update DB
    async with acquire_connection(db_pool) as conn:
        old_row = await conn.fetchone(
            "SELECT config_value FROM system_config WHERE config_key = %s",
            (config_key,),
        )
        old_value = old_row["config_value"] if old_row else None

        await conn.execute(
            """
            INSERT INTO system_config (config_key, config_value, config_type, updated_by, updated_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (config_key) DO UPDATE SET
                config_value = EXCLUDED.config_value,
                updated_by = EXCLUDED.updated_by,
                updated_at = NOW()
            """,
            (config_key, config.model_dump_json(), config_type, user_id),
        )

    # 3. Audit log with old_value and new_value
    await audit_logger.log_action(
        user_id=user_id,
        action="config_update",
        event_type="admin",  # Explicit event_type
        resource_type="system_config",
        resource_id=config_key,
        outcome="success",
        details={
            "config_type": config_type,
            "old_value": old_value,  # None if first creation
            "new_value": config.model_dump(),
        },
    )
```

**Tests:**
- `tests/apps/web_console/test_config_editor.py`
- Pydantic validation (valid and invalid inputs)
- market_close > market_open constraint
- Position limits range validation
- DB persistence (save/load round-trip)
- Audit logging verification
- RBAC enforcement
- **Parity test:** Config values match production loaders

---

### C5.6: Audit Log Viewer Component (`apps/web_console/components/audit_log_viewer.py`)

**Purpose:** Query and display audit logs with filtering

**Deliverables:**
- `render_audit_log_viewer(user, db_pool)` - Main render function
- Filters: user_id, action, event_type, outcome, date range
- Pagination (50 records per page)
- PII masking in display
- Export to CSV (optional)

**Query Pattern:**
```sql
SELECT
    id, timestamp, user_id, action, event_type,
    resource_type, resource_id, outcome, details
FROM audit_log
WHERE ($1 IS NULL OR user_id = $1)
  AND ($2 IS NULL OR action = $2)
  AND ($3 IS NULL OR timestamp >= $3)
  AND ($4 IS NULL OR timestamp <= $4)
ORDER BY timestamp DESC
LIMIT $5 OFFSET $6
```

**PII Masking:**
- Email in details -> `***@domain.com`
- Phone in details -> `***1234`
- IP address shown (not PII for audit purposes)

**Tests:**
- `tests/apps/web_console/test_audit_log_viewer.py`
- Query construction
- Pagination
- PII masking
- RBAC (VIEW_AUDIT permission)

---

### C5.7: Admin Page (`apps/web_console/pages/admin.py`)

**Purpose:** Main admin dashboard integrating all components

**Deliverables:**
- `main()` - Page entry point with @require_auth
- Tab structure: Users | API Keys | Config | Audit Log
- RBAC enforcement (Admin role for all tabs)
- Navigation integration

**Structure:**
```python
@require_auth
def main():
    user = get_current_user()
    if user.get("role") != "admin":
        st.error("Admin access required")
        st.stop()

    st.set_page_config(page_title="Admin Dashboard", page_icon="⚙️")
    st.title("Admin Dashboard")

    tab1, tab2, tab3, tab4 = st.tabs(["Users", "API Keys", "Config", "Audit Log"])

    with tab1:
        render_admin_users(user, db_pool, audit_logger)
    with tab2:
        render_api_key_manager(user, db_pool, audit_logger, redis_client)
    with tab3:
        render_config_editor(user, db_pool, audit_logger)
    with tab4:
        render_audit_log_viewer(user, db_pool)
```

**Tests:**
- `tests/apps/web_console/test_admin_page.py`
- Tab rendering
- RBAC enforcement
- Component integration

---

### C5.8: Tests

**Unit Tests:**
- `tests/libs/admin/test_api_keys.py` - Key generation, hashing, validation
- `tests/libs/common/test_log_sanitizer.py` - PII masking

**Integration Tests:**
- `tests/libs/admin/test_api_keys_integration.py` - DB operations
- `tests/apps/web_console/test_api_key_manager.py` - Component with mocked DB
- `tests/apps/web_console/test_config_editor.py` - Config persistence
- `tests/apps/web_console/test_audit_log_viewer.py` - Query and display

**E2E Tests:**
- `tests/e2e/test_admin_api_key_lifecycle.py` - Create, use, revoke flow
- `tests/e2e/test_admin_config_workflow.py` - Config change audit trail

**Security Tests:**
- API key replay after revocation (must fail)
- Log redaction verification (no PII in logs)
- RBAC denial tests (non-admin cannot access)

---

## 4. Implementation Order

Based on dependencies and the 6-step pattern:

```
Phase 1: Core Libraries (delegate to Codex)
├── C5.1: API Keys Library
├── C5.2: Database Migration
└── C5.3: Log Sanitizer

Phase 2: UI Components (delegate to Codex)
├── C5.4: API Key Manager Component
├── C5.5: Config Editor Component
└── C5.6: Audit Log Viewer Component

Phase 3: Integration (delegate to Codex)
├── C5.7: Admin Page
└── C5.8: Tests

Phase 4: Documentation & Finalization
├── Documentation (platform-administration.md)
└── CI validation
```

---

## 5. Files to Create

| File | Type | Description |
|------|------|-------------|
| `libs/admin/__init__.py` | Library | Package init |
| `libs/admin/api_keys.py` | Library | API key generation/hashing |
| `libs/common/log_sanitizer.py` | Library | PII masking utility |
| `db/migrations/0011_create_api_keys.sql` | Migration | API keys schema |
| `apps/web_console/components/api_key_manager.py` | Component | API key UI |
| `apps/web_console/components/config_editor.py` | Component | Config UI |
| `apps/web_console/components/audit_log_viewer.py` | Component | Audit log UI |
| `apps/web_console/pages/admin.py` | Page | Main admin page |
| `tests/libs/admin/__init__.py` | Test | Package init |
| `tests/libs/admin/test_api_keys.py` | Test | API key tests |
| `tests/libs/common/test_log_sanitizer.py` | Test | Sanitizer tests |
| `tests/apps/web_console/test_api_key_manager.py` | Test | Component tests |
| `tests/apps/web_console/test_config_editor.py` | Test | Component tests |
| `tests/apps/web_console/test_audit_log_viewer.py` | Test | Component tests |
| `tests/apps/web_console/test_admin_page.py` | Test | Page tests |
| `docs/CONCEPTS/platform-administration.md` | Docs | Admin documentation |

---

## 6. Files to Modify

| File | Modification |
|------|--------------|
| `apps/web_console/app.py` | Add admin page to navigation |
| `apps/web_console/config.py` | Add admin feature flags + wire SanitizingFormatter |
| `libs/web_console_auth/permissions.py` | **Add MANAGE_API_KEYS permission** (required) |
| `apps/web_console/services/user_management.py` | Update UserInfo to include last_active_ip |
| `pyproject.toml` | No new deps needed (stdlib for crypto) |

### 6.1 Permission Update (Required)

**File:** `libs/web_console_auth/permissions.py`

```python
# Add to Permission enum:
class Permission(str, Enum):
    # ... existing permissions ...
    MANAGE_API_KEYS = "manage_api_keys"  # NEW: API key CRUD operations

# Add to ROLE_PERMISSIONS:
ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.VIEWER: {...},  # No change
    Role.OPERATOR: {...},  # No change
    Role.ADMIN: set(Permission),  # Automatically includes new permission
}
```

### 6.2 UserInfo Update (Required)

**File:** `apps/web_console/services/user_management.py`

```python
@dataclass
class UserInfo:
    user_id: str
    role: str
    session_version: int
    updated_at: str
    updated_by: str | None
    strategy_count: int
    last_active_ip: str | None  # NEW
    last_active_at: str | None  # NEW
```

### 6.3 Last Active IP Capture (Required)

**Where IP is captured:** In session validation middleware on each authenticated request.

**File:** `apps/web_console/auth/session_manager.py` (modify `require_auth` decorator)

```python
# In require_auth decorator, after successful session validation:
async def _update_last_active(
    user_id: str,
    ip_address: str,
    db_pool,
    redis_client,  # REQUIRED: Pass redis_client for debounce
) -> None:
    """Update last_active_ip and last_active_at on each authenticated request.

    Note: This runs on every authenticated request but is debounced
    via Redis to prevent DB thrashing (same pattern as API key last_used).
    """
    debounce_key = f"user_last_active:{user_id}"
    current_minute = datetime.utcnow().replace(second=0, microsecond=0)

    # Check Redis debounce (1-minute resolution)
    if redis_client:
        try:
            if await redis_client.get(debounce_key):
                return  # Already updated this minute
            await redis_client.setex(debounce_key, 60, "1")
        except RedisError:
            pass  # Continue with update

    # Update DB
    async with acquire_connection(db_pool) as conn:
        await conn.execute(
            """UPDATE user_roles
               SET last_active_ip = %s, last_active_at = %s
               WHERE user_id = %s""",
            (ip_address, current_minute, user_id),
        )

# Called in require_auth decorator:
@functools.wraps(func)
def wrapper(*args, **kwargs):
    # ... existing session validation ...
    user = get_current_user()
    ip_address = _get_client_ip(st.request)  # Extract from request headers

    # Update last active (async, fire-and-forget with timeout)
    # Note: db_pool and redis_client obtained from cached resources
    asyncio.create_task(_update_last_active(
        user["user_id"],
        ip_address,
        db_pool,
        redis_client,  # Pass redis_client for debounce
    ))

    return func(*args, **kwargs)
```

**IP Extraction Helper:**
```python
def _get_client_ip(request) -> str:
    """Extract client IP from request, handling proxies."""
    # Check X-Forwarded-For first (for reverse proxy)
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    # Fall back to direct connection IP
    return request.remote_addr or "unknown"
```

---

## 7. Explicit Audit Events (Required by T7.4)

**All admin actions must be logged with the following structure:**

```python
await audit_logger.log_action(
    user_id=admin_user_id,
    action=action_name,
    event_type=event_type,  # REQUIRED: See mapping below
    resource_type=resource_type,
    resource_id=resource_id,
    outcome="success" | "denied" | "failed",
    details={...},
)
```

### 7.0 Event Type Taxonomy (Aligns with Migration 0005)

**event_type values (from audit_log schema):**

| event_type | Description | Used For |
|------------|-------------|----------|
| `admin` | Administrative actions | User CRUD, role changes, API key ops, config changes |
| `auth` | Authentication events | Login, logout, session validation (existing) |
| `action` | User actions | Manual orders, kill switch (existing) |

**Mapping: action → event_type:**

| action | event_type |
|--------|------------|
| `user_create` | `admin` |
| `user_update` | `admin` |
| `user_disable` | `admin` |
| `role_change` | `admin` |
| `strategy_grant` | `admin` |
| `strategy_revoke` | `admin` |
| `api_key_create` | `admin` |
| `api_key_revoke` | `admin` |
| `api_key_rotate` | `admin` |
| `config_update` | `admin` |
| `admin_page_access_denied` | `admin` |
| `api_key_action_denied` | `admin` |
| `config_edit_denied` | `admin` |

### 7.1 User Management Events

| Action | resource_type | resource_id | details |
|--------|--------------|-------------|---------|
| `user_create` | user | {user_id} | `{role, strategies}` |
| `user_update` | user | {user_id} | `{field, old_value, new_value}` |
| `user_disable` | user | {user_id} | `{reason}` |
| `role_change` | user | {user_id} | `{old_role, new_role, reason}` |
| `strategy_grant` | user_strategy | {user_id}:{strategy_id} | `{}` |
| `strategy_revoke` | user_strategy | {user_id}:{strategy_id} | `{reason}` |

### 7.2 API Key Events

| Action | resource_type | resource_id | details |
|--------|--------------|-------------|---------|
| `api_key_create` | api_key | {key_prefix} | `{name, scopes, expires_at}` |
| `api_key_revoke` | api_key | {key_prefix} | `{reason}` |
| `api_key_rotate` | api_key | {old_prefix}:{new_prefix} | `{reason}` |

### 7.3 Config Change Events

| Action | resource_type | resource_id | details |
|--------|--------------|-------------|---------|
| `config_update` | system_config | {config_key} | `{config_type, old_value, new_value}` |

### 7.4 Access Denial Events

| Action | resource_type | resource_id | details |
|--------|--------------|-------------|---------|
| `admin_page_access_denied` | page | admin | `{role, required_permission}` |
| `api_key_action_denied` | api_key | {key_prefix} | `{action, role}` |
| `config_edit_denied` | system_config | {config_key} | `{role}` |

### 7.5 Test Requirements for Audit Events

```python
# tests/apps/web_console/test_admin_audit_events.py

@pytest.mark.parametrize("action,resource_type", [
    ("user_create", "user"),
    ("user_disable", "user"),
    ("role_change", "user"),
    ("api_key_create", "api_key"),
    ("api_key_revoke", "api_key"),
    ("config_update", "system_config"),
])
async def test_audit_event_logged(action, resource_type, mock_audit_logger):
    """Verify each admin action creates an audit log entry."""
    # ... test implementation ...
    assert mock_audit_logger.log_action.called
    call_args = mock_audit_logger.log_action.call_args
    assert call_args.kwargs["action"] == action
    assert call_args.kwargs["resource_type"] == resource_type
    assert call_args.kwargs["outcome"] in ("success", "denied", "failed")
```

---

## 8. Acceptance Criteria Mapping

| Requirement | Component | Implementation |
|-------------|-----------|----------------|
| User management table | Existing `admin_users.py` | Already implemented |
| **Last Active IP column** | C5.2, C5.7 | **NEW:** Migration adds column, UI displays it |
| Role/permission UI | Existing `user_role_editor.py` | Already implemented |
| API key format (32 bytes, base64url) | C5.1 | `generate_api_key()` |
| Key prefix `tp_live_{first8}` | C5.1 | `parse_key_prefix()` with CHECK constraint |
| One-time key display | C5.4 | Modal with copy button + checkbox |
| SHA-256 salted hashing | C5.1 | `hash_api_key()` |
| Scoped permissions (Pydantic) | C5.1 | `ApiKeyScopes` model |
| Rotation workflow | C5.4 | Detailed UX: create new -> grace period -> explicit revoke |
| Revocation propagation (Redis) | C5.1, C5.4 | Cache-first with DB fallback |
| Last used timestamp (debounced) | C5.1 | `update_last_used()` with 1-min resolution |
| Audit/log redaction | C5.3 | `SanitizingFormatter` globally wired |
| System config editor | C5.5 | `render_config_editor()` with PostgreSQL storage |
| Config validation | C5.5 | Pydantic models with constraints |
| Audit log viewer | C5.6 | `render_audit_log_viewer()` with PII masking |
| RBAC: Admin required | C5.7 | Page-level + MANAGE_API_KEYS permission |
| Audit events | All | Explicit events per Section 7 |

---

## 9. Security Considerations

### 9.1 API Key Security
- **Entropy:** 32 bytes = 256 bits (cryptographically secure)
- **One-time display:** Key shown only at creation, never retrievable
- **Storage:** Only hash + salt stored, never plaintext
- **Revocation:** Cache-first (Redis 5-min TTL) with DB fallback
- **Logging:** Only prefix + last 4 chars logged
- **Prefix format:** Enforced via DB CHECK constraint

### 9.2 PII Protection
- **Email:** Masked as `***@domain.com`
- **Phone:** Masked as `***1234`
- **Log sanitizer:** Applied at formatter level (globally wired)
- **Audit viewer:** PII masked on render

### 9.3 RBAC
- **Admin role:** Required for all admin operations
- **MANAGE_API_KEYS:** New permission for granular control
- **Defense in depth:** Permission checks at page AND component level
- **Audit trail:** All actions logged with explicit event types
- **Denial tests:** Viewer/Operator cannot access admin functions

---

## 10. Delegation Strategy

Each sub-component will be delegated to Codex separately:
1. **C5.1 + C5.2:** API Keys Library + Migration (together - related)
2. **C5.3:** Log Sanitizer (standalone)
3. **C5.4:** API Key Manager Component
4. **C5.5:** Config Editor Component
5. **C5.6:** Audit Log Viewer Component
6. **C5.7:** Admin Page Integration
7. **C5.8:** Tests (may be split by component)

**Review Flow:**
- Each component gets fresh, unbiased Gemini + Codex review
- Never reuse continuation IDs across reviews
- CI must pass before commit

---

## 11. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Key prefix collision | Low | UNIQUE constraint + retry (max 3) |
| Redis unavailable for revocation cache | Medium | Fall back to DB check |
| Config change without audit | High | Mandatory audit in all save paths |
| PII leak in logs | High | Formatter-level sanitization |

---

## 12. Review Checklist

- [ ] All acceptance criteria from T7.4 addressed
- [ ] **Last Active IP** added to user_roles and displayed in UI
- [ ] API key security requirements met (256-bit, salted hash, CHECK constraint)
- [ ] **MANAGE_API_KEYS permission** added to permissions.py
- [ ] PII masking applied consistently (formatter + UI)
- [ ] RBAC enforced at all levels with denial tests
- [ ] **Explicit audit events** per Section 7 with tests
- [ ] **Config storage** uses PostgreSQL with Pydantic validation
- [ ] **Revocation workflow** has cache-first with DB fallback
- [ ] **Last used debounce** implemented with 1-min resolution
- [ ] **Rotation UX** includes grace period and explicit revoke
- [ ] Tests cover security scenarios (revocation replay, PII in logs, RBAC denial)
- [ ] No new dependencies added (stdlib crypto)
- [ ] Migration is idempotent with proper constraints

---

**Plan Status:** Revision 3 - Addressing all review concerns
**Changes in Rev 2:**
1. Added Last Active IP to migration and UI
2. Specified PostgreSQL as config storage
3. Enumerated explicit audit events (Section 7)
4. Detailed revocation cache-first flow with DB fallback
5. Detailed last_used_at debounce implementation
6. Added MANAGE_API_KEYS permission explicitly
7. Added CHECK constraint for key_prefix format
8. Wired SanitizingFormatter globally
9. Detailed rotation UX with grace period
10. Added denial tests for RBAC

**Changes in Rev 3 (Addressing Codex Concerns):**
11. Added explicit IP capture flow in session_manager.py (Section 6.3)
12. Added old_value to config audit payload
13. Defined event_type taxonomy and action→event_type mapping (Section 7.0)
14. Added API key authentication middleware integration point (C5.1)
15. Detailed exact sanitizer insertion points for all handlers (C5.3)
16. Added cache invalidation pattern with Redis keys (C5.5)
17. Added config_type extensibility migration pattern (C5.5)

**Gemini:** APPROVED (Rev 2)
**Codex:** Concerns addressed in Rev 3

**Next Step:** Request fresh zen-mcp plan review (unbiased, no continuation-id)
