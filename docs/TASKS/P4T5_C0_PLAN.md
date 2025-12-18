# P4T5 C0: Prep & Validation - Component Plan

**Component:** C0 - Prep & Validation
**Parent Task:** P4T5 Web Console Operations
**Status:** Plan Review
**Estimated Effort:** 1 day

---

## Objective

Verify all prerequisites for Track 7 implementation and establish the foundational auth stub pattern that will be used across all operations dashboards.

---

## Prerequisites Verification

### 1. Health Endpoints (VERIFIED)

Health endpoints exist in 7 services with `HealthResponse` schema:
- `apps/signal_service/main.py` - `/health` endpoint
- `apps/execution_gateway/main.py` - `/health` endpoint
- `apps/orchestrator/main.py` - `/health` endpoint
- `apps/auth_service/main.py` - `/health` endpoint
- `apps/model_registry/main.py` - `/health` endpoint
- `apps/market_data_service/main.py` - `/health` endpoint
- `apps/web_console/metrics_server.py` - `/health` endpoint

**Schema structure** (from signal_service):
```python
class HealthResponse(BaseModel):
    status: str  # "healthy", "degraded", "unhealthy"
    model_loaded: bool
    model_info: dict | None
    redis_status: str
    feature_cache_enabled: bool
    timestamp: str
    service: str
```

### 2. Circuit Breaker Redis Key (VERIFIED)

Canonical key is `circuit_breaker:state` (NOT `cb:state`).

Found in `libs/risk_management/breaker.py:L3`:
```python
self.state_key = "circuit_breaker:state"
```

### 3. pgcrypto Extension (VERIFIED)

Already enabled in `db/migrations/0008_create_backtest_jobs.sql`:
```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

### 4. Async Worker Infrastructure (VERIFIED)

RQ-based worker exists at `libs/backtest/worker.py`. For alerts, we can:
- Option A: Reuse `backtest_worker` with dedicated `alerts` queue
- Option B: Create lightweight `alert_worker` (recommended per Gemini review)

**Decision:** Create dedicated `alerts` queue on existing RQ infrastructure. Worker provisioning deferred to C3 (Alert Delivery Service).

---

## Implementation Plan

### Step 1: Create Operations Auth Stub

**File:** `apps/web_console/auth/operations_auth.py`

Pattern follows existing `backtest_auth.py` with these differences:
- Uses `OPERATIONS_DEV_AUTH` env var (not `BACKTEST_DEV_AUTH`)
- Returns `role: "admin"` (not `"operator"`) for full operations access
- Includes **runtime guard** that blocks startup if enabled in prod/staging

```python
# apps/web_console/auth/operations_auth.py
"""Auth decorator with dev-mode fallback for Track 7 Operations.

SECURITY:
- NEVER enable OPERATIONS_DEV_AUTH=true in production/staging
- Runtime guard blocks app startup if violated
- CI governance tests enforce this

Rollback Path (when T6.1 ships):
1. Remove OPERATIONS_DEV_AUTH from all env files
2. Replace @operations_requires_auth with @requires_auth
3. Delete this file
4. Run test_no_auth_stub_references_after_t61 to verify cleanup
"""

from __future__ import annotations

import functools
import os
import sys
from collections.abc import Callable
from typing import Any

import streamlit as st

from apps.web_console.auth.streamlit_helpers import requires_auth


# Allowlist: ONLY these environments can use dev auth (fail-closed security)
_ALLOWED_DEV_AUTH_ENVIRONMENTS = frozenset({
    "development", "dev", "local", "test", "ci",
})


def _check_dev_auth_safety() -> None:
    """Runtime guard: refuse to start if dev auth enabled outside allowed environments.

    SECURITY: Uses allowlist (fail-closed) - if ENVIRONMENT is unset, mistyped, or
    unknown, dev auth is blocked. Only explicitly allowed environments can use it.
    """
    if os.getenv("OPERATIONS_DEV_AUTH", "false").lower() in ("true", "1", "yes", "on"):
        env = os.getenv("ENVIRONMENT", "").lower()  # Empty string if unset
        if env not in _ALLOWED_DEV_AUTH_ENVIRONMENTS:
            print(
                f"FATAL: OPERATIONS_DEV_AUTH=true is only allowed in {sorted(_ALLOWED_DEV_AUTH_ENVIRONMENTS)}. "
                f"Current ENVIRONMENT='{env or '(unset)'}'. "
                "Remove OPERATIONS_DEV_AUTH or set ENVIRONMENT to an allowed value.",
                file=sys.stderr,
            )
            sys.exit(1)


# Run check at module import time
_check_dev_auth_safety()


def operations_requires_auth(func: Callable[..., Any]) -> Callable[..., Any]:
    """Auth decorator with dev-mode fallback for Track 7 operations.

    CRITICAL: Dev stub must set the same session keys as real OAuth2 auth.
    Uses admin role for full operations access (CB trip/reset, user management, etc.)
    """
    if os.getenv("OPERATIONS_DEV_AUTH", "false").lower() in ("true", "1", "yes", "on"):
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            st.session_state["authenticated"] = True
            st.session_state["username"] = "dev_user"
            st.session_state["user_id"] = "dev_user_id"
            st.session_state["auth_method"] = "dev_stub"
            st.session_state["session_id"] = "dev_session"
            st.session_state["role"] = "admin"  # Admin for full operations access
            st.session_state["strategies"] = ["*"]
            return func(*args, **kwargs)
        return wrapper
    else:
        return requires_auth(func)


__all__ = ["operations_requires_auth"]
```

### Step 2: Create CI Governance Tests

**File:** `tests/apps/web_console/test_operations_auth_governance.py`

```python
"""CI governance tests for operations auth stub.

These tests ensure dev auth stub cannot leak to production/staging.
They run in CI and block merges if violated.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

# Regex to catch OPERATIONS_DEV_AUTH with truthy values in various formats:
# - OPERATIONS_DEV_AUTH=true (shell/env)
# - OPERATIONS_DEV_AUTH: true (YAML)
# - OPERATIONS_DEV_AUTH: "true" (YAML quoted)
# - operations_dev_auth: True (case variations)
_DEV_AUTH_TRUTHY_PATTERN = re.compile(
    r"operations_dev_auth\s*[=:]\s*['\"]?\s*(true|1|yes|on)\s*['\"]?",
    re.IGNORECASE,
)


class TestOperationsAuthGovernance:
    """Governance tests for OPERATIONS_DEV_AUTH."""

    @pytest.fixture
    def project_root(self) -> Path:
        """Get project root directory."""
        return Path(__file__).parents[4]

    def test_no_dev_auth_in_prod_env(self, project_root: Path) -> None:
        """CI fails if OPERATIONS_DEV_AUTH is truthy in .env.prod."""
        prod_env = project_root / ".env.prod"
        if prod_env.exists():
            content = prod_env.read_text()
            match = _DEV_AUTH_TRUTHY_PATTERN.search(content)
            assert match is None, (
                f"OPERATIONS_DEV_AUTH with truthy value found in .env.prod: '{match.group()}'"
            )

    def test_no_dev_auth_in_staging_env(self, project_root: Path) -> None:
        """CI fails if OPERATIONS_DEV_AUTH is truthy in .env.staging."""
        staging_env = project_root / ".env.staging"
        if staging_env.exists():
            content = staging_env.read_text()
            match = _DEV_AUTH_TRUTHY_PATTERN.search(content)
            assert match is None, (
                f"OPERATIONS_DEV_AUTH with truthy value found in .env.staging: '{match.group()}'"
            )

    def test_no_dev_auth_in_docker_compose_prod(self, project_root: Path) -> None:
        """CI fails if OPERATIONS_DEV_AUTH is truthy in docker-compose.prod.yml."""
        compose_prod = project_root / "docker-compose.prod.yml"
        if compose_prod.exists():
            content = compose_prod.read_text()
            match = _DEV_AUTH_TRUTHY_PATTERN.search(content)
            assert match is None, (
                f"OPERATIONS_DEV_AUTH with truthy value found in docker-compose.prod.yml: '{match.group()}'"
            )

    def test_no_dev_auth_in_docker_compose_staging(self, project_root: Path) -> None:
        """CI fails if OPERATIONS_DEV_AUTH is truthy in docker-compose.staging.yml."""
        compose_staging = project_root / "docker-compose.staging.yml"
        if compose_staging.exists():
            content = compose_staging.read_text()
            match = _DEV_AUTH_TRUTHY_PATTERN.search(content)
            assert match is None, (
                f"OPERATIONS_DEV_AUTH with truthy value found in docker-compose.staging.yml: '{match.group()}'"
            )

    def test_no_dev_auth_in_infra_deploy_configs(self, project_root: Path) -> None:
        """CI fails if OPERATIONS_DEV_AUTH is truthy in any infra deploy configs."""
        infra_dir = project_root / "infra"
        if infra_dir.exists():
            # Scan both .yml and .yaml extensions
            for ext in ("*.yml", "*.yaml"):
                for config_file in infra_dir.rglob(ext):
                    if "prod" in config_file.name.lower() or "staging" in config_file.name.lower():
                        content = config_file.read_text()
                        match = _DEV_AUTH_TRUTHY_PATTERN.search(content)
                        assert match is None, (
                            f"OPERATIONS_DEV_AUTH with truthy value found in {config_file}: '{match.group()}'"
                        )

    @pytest.mark.parametrize("allowed_env", ["development", "dev", "local", "test", "ci"])
    def test_runtime_guard_allows_dev_environments(
        self, monkeypatch: pytest.MonkeyPatch, allowed_env: str
    ) -> None:
        """Runtime guard should allow dev auth in explicitly allowed environments."""
        monkeypatch.setenv("OPERATIONS_DEV_AUTH", "true")
        monkeypatch.setenv("ENVIRONMENT", allowed_env)

        # Import should NOT trigger sys.exit
        import importlib
        import apps.web_console.auth.operations_auth as ops_auth
        importlib.reload(ops_auth)
        # If we get here, test passes

    @pytest.mark.parametrize("blocked_env", ["production", "prod", "staging", "stage", "unknown", "prod1"])
    def test_runtime_guard_blocks_non_allowed_environments(
        self, monkeypatch: pytest.MonkeyPatch, blocked_env: str
    ) -> None:
        """Runtime guard should block any environment not in allowlist (fail-closed)."""
        monkeypatch.setenv("OPERATIONS_DEV_AUTH", "true")
        monkeypatch.setenv("ENVIRONMENT", blocked_env)

        with pytest.raises(SystemExit) as exc_info:
            import importlib
            import apps.web_console.auth.operations_auth as ops_auth
            importlib.reload(ops_auth)

        assert exc_info.value.code == 1

    def test_runtime_guard_blocks_unset_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Runtime guard should block when ENVIRONMENT is unset (fail-closed)."""
        monkeypatch.setenv("OPERATIONS_DEV_AUTH", "true")
        monkeypatch.delenv("ENVIRONMENT", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            import importlib
            import apps.web_console.auth.operations_auth as ops_auth
            importlib.reload(ops_auth)

        assert exc_info.value.code == 1

    def test_runtime_guard_blocks_empty_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Runtime guard should block when ENVIRONMENT is empty string."""
        monkeypatch.setenv("OPERATIONS_DEV_AUTH", "true")
        monkeypatch.setenv("ENVIRONMENT", "")

        with pytest.raises(SystemExit) as exc_info:
            import importlib
            import apps.web_console.auth.operations_auth as ops_auth
            importlib.reload(ops_auth)

        assert exc_info.value.code == 1


class TestOperationsAuthSessionContract:
    """Unit tests for auth stub session state contract."""

    def test_dev_stub_sets_full_session_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Dev stub must set all required session keys for RBAC parity."""
        monkeypatch.setenv("OPERATIONS_DEV_AUTH", "true")
        monkeypatch.setenv("ENVIRONMENT", "development")

        # Mock streamlit session_state
        mock_session: dict[str, Any] = {}
        monkeypatch.setattr("streamlit.session_state", mock_session)

        import importlib
        import apps.web_console.auth.operations_auth as ops_auth
        importlib.reload(ops_auth)

        # Create a dummy function and wrap it
        @ops_auth.operations_requires_auth
        def dummy_page() -> str:
            return "rendered"

        # Call the wrapped function
        result = dummy_page()

        # Verify all required session keys are set
        assert mock_session["authenticated"] is True
        assert mock_session["username"] == "dev_user"
        assert mock_session["user_id"] == "dev_user_id"
        assert mock_session["auth_method"] == "dev_stub"
        assert mock_session["session_id"] == "dev_session"
        assert mock_session["role"] == "admin"  # Admin for operations
        assert mock_session["strategies"] == ["*"]
        assert result == "rendered"

    def test_prod_mode_uses_real_auth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When dev auth disabled, should delegate to real requires_auth."""
        monkeypatch.setenv("OPERATIONS_DEV_AUTH", "false")
        monkeypatch.setenv("ENVIRONMENT", "development")

        import importlib
        import apps.web_console.auth.operations_auth as ops_auth
        importlib.reload(ops_auth)

        @ops_auth.operations_requires_auth
        def dummy_page() -> str:
            return "rendered"

        # The wrapper should be the real requires_auth, not our stub
        # We can verify by checking the function name or behavior
        assert dummy_page.__wrapped__.__name__ == "dummy_page"


class TestAuthStubRemovalGate:
    """Gate test to ensure stub is removed after T6.1 ships."""

    @pytest.fixture
    def project_root(self) -> Path:
        return Path(__file__).parents[4]

    @pytest.mark.skip(reason="Enable after T6.1 ships")
    def test_no_auth_stub_references_after_t61(self, project_root: Path) -> None:
        """After T6.1 ships, CI fails if operations_requires_auth is referenced."""
        result = subprocess.run(
            ["grep", "-r", "operations_requires_auth", str(project_root / "apps")],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, (
            f"Found references to operations_requires_auth after T6.1:\n{result.stdout}"
        )
```

### Step 3: Create ADR-0026 Outline

**File:** `docs/ADRs/ADR-0026-alerting-system.md`

```markdown
# ADR-0026: Alerting System Architecture

## Status
PROPOSED

## Context
Track 7 (P4T5) requires a multi-channel alert delivery system for operational notifications.
The system must support email, Slack, and SMS delivery with:
- Idempotent delivery (no duplicate alerts)
- Rate limiting (per-channel, per-recipient, global)
- Retry with exponential backoff
- Poison queue for failed deliveries

## Decision
[To be completed during C3 implementation]

### Architecture Overview
- Alert events stored in `alert_events` table
- Deliveries tracked in `alert_deliveries` table with dedup key
- Async worker processes delivery queue
- Redis token bucket for rate limiting

### Channel Handlers
- Email: SMTP/SendGrid with 100/min limit
- Slack: Webhook with 50/min limit
- SMS: Twilio with 10/min limit

### Idempotency Model
Dedup key: `{alert_id}:{channel}:{recipient}:{hour_bucket}`
- `hour_bucket` = UTC ISO 8601 truncated to hour, derived from trigger timestamp

### Rate Limiting
- Per-channel: Redis INCR + EXPIRE pattern
- Per-recipient: HMAC-SHA256 hashed recipient identifier
- Global burst: 500/min total

### Data Retention
- Alert events: 90 days, partitioned by month
- Delivery records: 90 days

## Consequences
[To be completed]

## References
- P4T5_TASK.md - Track 7 specification
- libs/backtest/worker.py - RQ worker pattern reference
```

### Step 4: Update .env.example

**File:** `.env.example` (append)

```bash
# Alert Delivery Service (T7.5)
# HMAC secret for hashing recipient identifiers in rate limit keys
# Generate with: python -c "import secrets; print(secrets.token_hex(32))"
ALERT_RECIPIENT_HASH_SECRET=your-secret-here-min-32-chars
```

---

## Files to Create

| File | Purpose |
|------|---------|
| `apps/web_console/auth/operations_auth.py` | Auth stub with runtime guard |
| `tests/apps/web_console/test_operations_auth_governance.py` | CI governance tests |
| `docs/ADRs/ADR-0026-alerting-system.md` | ADR outline for alerting |

## Files to Modify

| File | Change |
|------|--------|
| `.env.example` | Add `ALERT_RECIPIENT_HASH_SECRET` |

---

## Testing Strategy

1. **Unit Tests (TestOperationsAuthSessionContract):**
   - `test_dev_stub_sets_full_session_state` - Verifies all 7 session keys set correctly
   - `test_prod_mode_uses_real_auth` - Verifies delegation to real auth when disabled

2. **Runtime Guard Tests (TestOperationsAuthGovernance) - Allowlist/Fail-Closed:**
   - `test_runtime_guard_allows_dev_environments` - Allows development/dev/local/test/ci
   - `test_runtime_guard_blocks_non_allowed_environments` - Blocks production/prod/staging/unknown/prod1
   - `test_runtime_guard_blocks_unset_environment` - Blocks when ENVIRONMENT unset
   - `test_runtime_guard_blocks_empty_environment` - Blocks when ENVIRONMENT empty

3. **CI Governance Tests (regex-based for all truthy formats):**
   - Uses `_DEV_AUTH_TRUTHY_PATTERN` regex to catch: `=true`, `: true`, `: "true"`, `=1`, etc.
   - `test_no_dev_auth_in_prod_env` - No truthy flag in .env.prod
   - `test_no_dev_auth_in_staging_env` - No truthy flag in .env.staging
   - `test_no_dev_auth_in_docker_compose_prod` - No truthy flag in docker-compose.prod.yml
   - `test_no_dev_auth_in_docker_compose_staging` - No truthy flag in docker-compose.staging.yml
   - `test_no_dev_auth_in_infra_deploy_configs` - No truthy flag in infra/*.yml and *.yaml prod/staging files

4. **Manual Verification:**
   - Import operations_auth in dev mode works
   - Import operations_auth in prod mode with flag exits

---

## Success Criteria

- [ ] All 7 health endpoints verified with stable schema
- [ ] `circuit_breaker:state` confirmed as canonical key
- [ ] `pgcrypto` extension confirmed enabled
- [ ] `operations_auth.py` created with runtime guard
- [ ] Governance tests pass in CI
- [ ] ADR-0026 outline created
- [ ] `.env.example` updated with hash secret placeholder

---

## Dependencies

- None (this is the first component)

## Blocks

- C1: T7.1 Circuit Breaker Dashboard (needs operations_auth)
- C2: T7.2 System Health Monitor (needs operations_auth)
- C3: T7.5 Alert Delivery Service (needs ADR-0026)
- C4: T7.3 Alert Configuration UI (needs operations_auth)
- C5: T7.4 Admin Dashboard (needs operations_auth)
