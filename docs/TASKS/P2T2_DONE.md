---
id: P2T2
title: "Secrets Management"
phase: P2
task: T2
priority: P0
owner: "@development-team"
state: DONE
created: 2025-11-06
updated: 2025-11-21
completed: 2025-11-12
dependencies: []
estimated_effort: "7.5 days"
actual_effort: "~7 days"
related_adrs: ["0017-secrets-management"]
related_docs: ["docs/RUNBOOKS/secrets-migration.md", "docs/RUNBOOKS/secret-rotation.md"]
features: ["secrets_management", "vault_backend", "aws_backend", "secret_rotation", "credential_caching"]
---

# P2T2: Secrets Management

**Phase:** P2 (Advanced Features, 91-120 days)
**Status:** DONE (Completed)
**Priority:** P0 (Critical for production)
**Owner:** @development-team
**Created:** 2025-11-06
**Completed:** 2025-11-12
**Estimated Effort:** 7.5 days
**Actual Effort:** ~7 days

---

## Naming Convention

**This task:** `P2T2_SECRETS_MANAGEMENT.md` (current) → `P2T2_PROGRESS.md` → `P2T2_DONE.md`

**Work Packages (consolidated from 13 micro-components):**
- Package 0: `P2T2-F0_PROGRESS.md` - Core Secrets Library (interface, backends, factory) - 18 hours
- Package 1: `P2T2-F1_PROGRESS.md` - Service Integration (all 5 services) - 14 hours
- Package 2: `P2T2-F2_PROGRESS.md` - Operational Tooling & Migration (rotation, audit, migration) - 12 hours
- Package 3: `P2T2-F3_PROGRESS.md` - Documentation & ADR (ADR-0017, runbooks, guides) - 7 hours

---

## Objective

**Implement secure, production-ready secrets management to eliminate hardcoded credentials and enable secure live trading.**

Currently, all sensitive credentials (Alpaca API keys, database passwords, Redis URLs, webhook secrets) are stored in `.env` files. While gitignored, this approach is insufficient for production:

1. **Security Risk:** Plain-text credentials in filesystem
2. **No Rotation:** Manual process, high risk of credential staleness
3. **No Audit Trail:** Cannot track who accessed which secrets when
4. **No Separation:** Secrets mixed with configuration values
5. **CI/CD Challenge:** Credential distribution to production requires manual `.env` file management

**Success looks like:**
- All secrets (API keys, tokens, passwords) stored in secure backend (Vault or AWS Secrets Manager)
- All services retrieve secrets programmatically on startup via `SecretManager` interface
- Clear separation: secrets via `SecretManager`, configuration via Pydantic `Settings` (env vars)
- Automated secret rotation mechanism (90-day cycle)
- Audit logging for secret access
- Zero plain-text credentials in codebase or filesystem (except local dev placeholders)
- Smooth migration path from `.env` to secrets backend

---

## Acceptance Criteria

### Core Requirements
- [ ] **AC1:** `SecretManager` abstract interface defined in `libs/secrets/manager.py` with `get_secret()` and `list_secrets()` methods
- [ ] **AC2:** `VaultSecretManager` backend implemented using `hvac` library with connection pooling and retry logic
- [ ] **AC3:** `AWSSecretsManager` backend implemented using `boto3` with IAM authentication
- [ ] **AC4:** All services (signal_service, execution_gateway, orchestrator, market_data_service, risk_manager) fetch secrets via `SecretManager` instead of `.env`
- [ ] **AC5:** Dual-mode config pattern enforced: secrets (API keys, passwords) → `SecretManager`, config (ports, feature flags) → Pydantic `Settings`
- [ ] **AC6:** `.env` file removed from codebase, `.env.template` with placeholders + real config provided for local dev

### Secret Rotation
- [ ] **AC7:** Secret rotation script (`scripts/rotate_secrets.py`) implemented with Alpaca API key rotation workflow
- [ ] **AC8:** Rotation script implements zero-downtime workflow:
  1. Generate new secret in backend
  2. Update Alpaca API with new key (keep old key active)
  3. Update backend primary/secondary keys
  4. Trigger graceful service restart (services fetch new secret on startup)
  5. Verify new secret works (health checks pass)
  6. Deprecate old secret in backend (mark as rotation_date)
  7. Schedule old secret deletion (30 days)
- [ ] **AC9:** Rotation audit log written to database (`secret_rotation_log` table)

### Audit & Security
- [ ] **AC10:** All secret accesses logged with timestamp, service, secret name (NOT secret value)
- [ ] **AC11:** Failed secret retrievals raise `SecretNotFoundError` and log warnings (never fail silently)
- [ ] **AC12:** Secret values NEVER appear in logs, metrics, or tracebacks (redaction enforced)

### Testing & Documentation
- [ ] **AC13:** Unit tests for `VaultSecretManager` and `AWSSecretsManager` with mocked backends (>90% coverage)
- [ ] **AC14:** Integration tests for config loading with secrets (use test secret namespace)
- [ ] **AC15:** E2E test for service startup with secrets retrieved from backend
- [ ] **AC16:** ADR created BEFORE implementation: `docs/ADRs/0017-secrets-management.md` documenting architecture choice (Vault vs AWS vs Doppler) to guide design decisions
- [ ] **AC17:** Migration runbook created: `docs/RUNBOOKS/secrets-migration.md` with step-by-step operator guide
- [ ] **AC18:** Secret rotation runbook created: `docs/RUNBOOKS/secret-rotation.md` with 90-day rotation procedure

### CI/CD & Deployment
- [ ] **AC19:** CI pipeline updated to use test secrets from secrets backend (not `.env`)
- [ ] **AC20:** Local dev guide updated in `/docs/GETTING_STARTED/LOCAL_SETUP.md` with `.env.template` usage
- [ ] **AC21:** All tests pass with `make ci-local` (no regressions)

### Trading Safety Features
- [ ] **AC22:** Credential cache with 1-hour TTL implemented to mitigate secrets backend downtime (prevents trading halt)

---

## Current State Analysis

### How Credentials Are Currently Managed

All credentials are stored in a single `.env` file at repository root:

```bash
# .env (current state)
ALPACA_API_KEY_ID=
ALPACA_API_SECRET_KEY=
ALPACA_BASE_URL=https://paper-api.alpaca.markets
DATABASE_URL=postgresql+psycopg://trader:trader@localhost:5432/trader
REDIS_URL=redis://localhost:6379/0
WEBHOOK_SECRET=
```

**Loading mechanism:**
- Pydantic `Settings` classes load values via `model_config = SettingsConfigDict(env_file=".env")`
- Example: `apps/signal_service/config.py`:
  ```python
  class Settings(BaseSettings):
      database_url: str = "postgresql://postgres:postgres@localhost:5432/trading_platform"
      model_config = SettingsConfigDict(env_file=".env")
  ```

### Why This Is Insufficient for Production

| Risk | Impact | Severity |
|------|--------|----------|
| **Plain-text credentials in filesystem** | Disk access = credential access (no encryption at rest) | Critical |
| **No access audit trail** | Cannot detect unauthorized secret access or correlate issues to secret retrieval failures | High |
| **Manual rotation** | Stale credentials risk, no enforcement of rotation policy | High |
| **Secrets mixed with config** | Difficult to enforce separation of concerns (secrets vs config) | Medium |
| **CI/CD complexity** | Manual `.env` file distribution to production servers | Medium |
| **Accidental commits** | `.gitignore` protects but human error risk remains (leaked keys in commit history) | Medium |

### Security Risks

1. **Compromised developer laptop:** Attacker gains plain-text access to production API keys
2. **Insider threat:** Any engineer with filesystem access can read all secrets
3. **Stale credentials:** No enforcement or reminder mechanism for 90-day rotation
4. **No principle of least privilege:** All services see all secrets in `.env` (over-provisioning)
5. **Backup exposure:** `.env` file in backups = long-term credential exposure

---

## P2 Requirements

### 1. SecretManager Interface Design

**Abstract interface for pluggable backends:**

```python
# libs/secrets/manager.py
from abc import ABC, abstractmethod
from typing import Dict, Optional

class SecretManager(ABC):
    """Abstract secret manager interface for pluggable backends."""

    @abstractmethod
    def get_secret(self, name: str) -> str:
        """
        Retrieve secret by name.

        Args:
            name: Secret identifier (e.g., "alpaca/api_key_id")

        Returns:
            Secret value as string

        Raises:
            SecretNotFoundError: If secret does not exist
            SecretAccessError: If backend is unreachable or auth fails
        """
        pass

    @abstractmethod
    def list_secrets(self, prefix: Optional[str] = None) -> list[str]:
        """
        List available secret names (not values).

        Args:
            prefix: Optional filter (e.g., "alpaca/" returns only Alpaca secrets)

        Returns:
            List of secret names
        """
        pass

    @abstractmethod
    def set_secret(self, name: str, value: str) -> None:
        """
        Create or update secret (for rotation).

        Args:
            name: Secret identifier
            value: Secret value to store

        Raises:
            SecretWriteError: If write fails
        """
        pass
```

### 2. Pluggable Backends

**Vault Backend (Recommended for self-hosted):**
```python
# libs/secrets/vault_backend.py
import hvac
from tenacity import retry, stop_after_attempt, wait_exponential

class VaultSecretManager(SecretManager):
    """HashiCorp Vault implementation."""

    def __init__(self, vault_addr: str, token: str, mount_point: str = "secret"):
        self.vault_addr = vault_addr
        self.token = token
        self.mount_point = mount_point
        self.client = hvac.Client(url=vault_addr, token=token)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
    def get_secret(self, name: str) -> str:
        # Fetch from Vault KV v2
        pass
```

**AWS Secrets Manager Backend (Recommended for AWS deployments):**
```python
# libs/secrets/aws_backend.py
import boto3
from tenacity import retry, stop_after_attempt, wait_exponential

class AWSSecretsManager(SecretManager):
    """AWS Secrets Manager implementation."""

    def __init__(self, region: str = "us-east-1"):
        self.region = region
        self.client = boto3.client("secretsmanager", region_name=region)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
    def get_secret(self, name: str) -> str:
        # Fetch from AWS Secrets Manager
        pass
```

### 3. Dual-Mode Config Pattern

**Principle:** Separate secrets (sensitive credentials) from configuration (non-sensitive settings).

**Secrets (via SecretManager):**
- Alpaca API key ID
- Alpaca API secret key
- Database password
- Redis password (if auth enabled)
- Webhook secret

**Configuration (via Pydantic Settings + env vars):**
- Service ports (8001, 8002, etc.)
- Feature flags (DRY_RUN, REDIS_ENABLED)
- Timeout values
- Log levels
- Base URLs (e.g., Alpaca paper vs live)
- Capital limits
- Strategy IDs

**Example service config:**
```python
# apps/signal_service/config.py (updated)
from libs.secrets.manager import SecretManager

class Settings(BaseSettings):
    # Configuration (non-sensitive, from env vars)
    host: str = "0.0.0.0"
    port: int = 8001
    log_level: str = "INFO"

    # Secrets loaded separately (NOT in Settings)
    # database_url: REMOVED - load via SecretManager

# Startup (apps/signal_service/main.py)
def get_database_url(secret_mgr: SecretManager, settings: Settings) -> str:
    """Load database URL from secrets + config (demonstrates dual-mode pattern)."""
    password = secret_mgr.get_secret("database/password")
    # Config values (host, port, user, db) from Settings
    # Secret value (password) from SecretManager
    return f"postgresql://{settings.db_user}:{password}@{settings.db_host}:{settings.db_port}/{settings.db_name}"
```

### 4. Secret Rotation Mechanism

**90-day rotation policy enforced via script:**

```bash
# scripts/rotate_secrets.py
# Usage: ./scripts/rotate_secrets.py alpaca_api_key

Steps:
1. Generate new API key via Alpaca API
2. Store new key in secrets backend with _new suffix
3. Graceful rollout: update services one-by-one to use new key
4. Deprecate old key after 7-day grace period
5. Log rotation event to database (audit trail)
```

**Rotation metadata table:**
```sql
-- migrations/XXX_secret_rotation_log.sql
CREATE TABLE secret_rotation_log (
    id SERIAL PRIMARY KEY,
    secret_name TEXT NOT NULL,
    rotated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    rotated_by TEXT NOT NULL,  -- operator username
    old_key_deprecated_at TIMESTAMP WITH TIME ZONE,
    notes TEXT
);
```

### 5. Migration from .env

**Step-by-step migration:**

1. **Setup secrets backend:**
   - Provision Vault server OR configure AWS Secrets Manager
   - Create service credentials for secret access

2. **Populate secrets:**
   - Migrate credentials from `.env` to backend:
     ```bash
     vault kv put secret/alpaca/api_key_id value="<YOUR_ALPACA_KEY_ID>"
     vault kv put secret/alpaca/api_secret value="<YOUR_ALPACA_SECRET>"
     vault kv put secret/database/password value="<YOUR_DB_PASSWORD>"
     ```

3. **Update service startup:**
   - Initialize `SecretManager` in `main.py`
   - Load secrets via `SecretManager.get_secret()`
   - Remove secret fields from Pydantic `Settings`

4. **Create `.env.template`:**
   - Non-sensitive config with real values (ports, feature flags)
   - Placeholder comments for secrets:
     ```bash
     # .env.template
     # Secrets are loaded from Vault/AWS Secrets Manager
     # See docs/RUNBOOKS/secrets-migration.md for setup

     # Service Configuration
     HOST=0.0.0.0
     PORT=8001
     LOG_LEVEL=INFO
     DRY_RUN=true

     # Secrets (DO NOT put real values here - use secrets backend)
     # ALPACA_API_KEY_ID -> loaded from secret/alpaca/api_key_id
     # ALPACA_API_SECRET -> loaded from secret/alpaca/api_secret
     # DATABASE_PASSWORD -> loaded from secret/database/password
     ```

5. **Remove `.env` from repository:**
   - Commit removal: `git rm .env`
   - Update `.gitignore` to ignore `.env` but track `.env.template`

---

## Implementation Approach

### Work Package Breakdown (4-Step Pattern)

**Duration:** 7.5 days (60 hours) = 51 hours base + 15% buffer (9 hours)

**CRITICAL:** Each package follows 4-step pattern (Implement → Test → Review → Commit) as a UNIT. Do NOT break packages into sub-commits.

---

#### **Package 0: Core Secrets Library** (18 hours)

**Scope:** Build complete secrets abstraction layer with all backends and supporting infrastructure.

**What gets built:**
1. **Interface & Exceptions** (`libs/secrets/manager.py`, `libs/secrets/exceptions.py`)
   - Abstract `SecretManager` class with `get_secret()`, `list_secrets()`, `set_secret()` methods
   - Custom exceptions: `SecretNotFoundError`, `SecretAccessError`, `SecretWriteError`
   - Credential cache decorator with 1-hour TTL (AC22 - trading safety requirement)

2. **Vault Backend** (`libs/secrets/vault_backend.py`)
   - `VaultSecretManager` implementation using `hvac` library
   - Connection pooling to Vault server
   - Retry logic for transient failures (tenacity: 3 attempts, exponential backoff)
   - KV v2 secrets engine support

3. **AWS Backend** (`libs/secrets/aws_backend.py`)
   - `AWSSecretsManager` implementation using `boto3`
   - IAM authentication support
   - Retry logic for AWS API throttling (tenacity: 3 attempts, exponential backoff)
   - Optional: Cross-region replication support

4. **Secret Factory** (`libs/secrets/factory.py`)
   - `create_secret_manager()` factory function
   - Backend selection via `SECRET_BACKEND` env var: "vault", "aws", or "env"
   - `EnvSecretManager` for local dev fallback (reads from `.env`)

5. **Shared Infrastructure**
   - Credential caching with TTL (1 hour default, configurable)
   - Connection pooling for Vault/AWS clients
   - Structured audit logging (timestamp, service, secret name - NOT value)
   - Redaction enforcement for logs/tracebacks

**Testing:**
- Unit tests for `SecretManager` interface contract (>90% coverage)
- Unit tests for `VaultSecretManager` with mocked `hvac` client
- Unit tests for `AWSSecretsManager` with mocked `boto3` client
- Unit tests for factory logic and backend selection
- Unit tests for credential cache (hit/miss/expiry scenarios)
- Integration test: Cache prevents backend calls for <1h

**Dependencies:**
- Add to `pyproject.toml`: `hvac>=1.2.1`, `boto3>=1.26.0`

**4-Step Pattern:**
1. Implement all components above
2. Create comprehensive test suite
3. Request zen-mcp review (clink + gemini → codex)
4. Run `make ci-local` → Commit after BOTH reviewers approve + CI passes

---

#### **Package 1: Service Integration** (14 hours)

**Scope:** Update all 5 services to use `SecretManager` with dual-mode config pattern.

**What gets updated:**

1. **Signal Service** (`apps/signal_service/`)
   - Update `main.py`: Initialize `SecretManager` on startup
   - Load database password via `secret_mgr.get_secret("database/password")`
   - Remove `database_url` from `Settings` (construct dynamically)
   - Update tests: Use test secret namespace (`test/database/password`)

2. **Execution Gateway** (`apps/execution_gateway/`)
   - Update `main.py`: Initialize `SecretManager`
   - Load Alpaca credentials via secrets:
     - `secret_mgr.get_secret("alpaca/api_key_id")`
     - `secret_mgr.get_secret("alpaca/api_secret")`
   - Remove `alpaca_api_key_id`, `alpaca_api_secret` from `Settings`
   - Update tests: Mock `SecretManager` for Alpaca client tests

3. **Orchestrator** (`apps/orchestrator/`)
   - Update `main.py`: Initialize `SecretManager`
   - Load database password via secrets
   - Update tests: Use test secret namespace

4. **Market Data Service** (`apps/market_data_service/`)
   - Update `main.py`: Initialize `SecretManager`
   - Load Alpaca credentials if direct integration (or inherit from execution_gateway)
   - Update tests: Mock secret access

5. **Risk Manager** (`libs/risk_management/`)
   - Accept secrets via dependency injection (NO direct `SecretManager` access in library)
   - Secrets passed from service layer (orchestrator)
   - Update tests: Inject test secrets as parameters

**Dual-Mode Config Pattern:**
- **Secrets** (via `SecretManager`): API keys, passwords, tokens
- **Config** (via Pydantic `Settings`): Ports, feature flags, timeouts, log levels

**Testing:**
- Integration test per service: Startup with secrets from test backend → health check passes
- Integration test: Service startup with missing secret → fails gracefully with clear error
- Integration test: Service startup with unreachable backend + cache miss → retries, then fails
- Integration test: Service startup with unreachable backend + cache hit → succeeds (AC22)

**4-Step Pattern:**
1. Implement all service updates
2. Create integration tests for each service
3. Request zen-mcp review (clink + gemini → codex)
4. Run `make ci-local` → Commit after BOTH reviewers approve + CI passes

---

#### **Package 2: Operational Tooling & Migration** (12 hours)

**Scope:** Secret rotation automation, audit logging, and migration from `.env` to secrets backend.

**What gets built:**

1. **Secret Rotation Script** (`scripts/rotate_secrets.py`)
   - CLI interface: `./scripts/rotate_secrets.py alpaca_api_key`
   - Zero-downtime rotation workflow (AC8):
     1. Generate new secret in backend
     2. Update Alpaca API with new key (keep old key active)
     3. Update backend primary/secondary keys
     4. Trigger graceful service restart (services fetch new secret on startup)
     5. Verify new secret works (health checks pass)
     6. Deprecate old secret in backend (mark as `rotation_date`)
     7. Schedule old secret deletion (30 days)
   - Log rotation event to `secret_rotation_log` table

2. **Audit Logging Infrastructure**
   - Database migration: `secret_rotation_log` table
     ```sql
     CREATE TABLE secret_rotation_log (
       id SERIAL PRIMARY KEY,
       secret_name TEXT NOT NULL,
       rotated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
       rotated_by TEXT NOT NULL,
       old_key_deprecated_at TIMESTAMP WITH TIME ZONE,
       notes TEXT
     );
     ```
   - Database migration: `secret_access_log` table (if not in Package 0)
   - Ensure secret VALUES never logged (redaction enforced)

3. **Migration Runbook** (`docs/RUNBOOKS/secrets-migration.md`)
   - Step-by-step guide for operators:
     1. Provision secrets backend (Vault or AWS)
     2. Populate secrets from `.env`
     3. Update services to use `SecretManager`
     4. Create `.env.template`
     5. Remove `.env` from repository
   - Rollback plan (revert to `.env` if issues)

4. **.env.template Creation**
   - Non-sensitive config with real values (ports, feature flags)
   - Placeholder comments for secrets:
     ```bash
     # .env.template
     # Secrets loaded from Vault/AWS Secrets Manager
     # Service Configuration
     HOST=0.0.0.0
     PORT=8001
     LOG_LEVEL=INFO
     DRY_RUN=true
     # Secrets (use secrets backend):
     # ALPACA_API_KEY_ID -> secret/alpaca/api_key_id
     # ALPACA_API_SECRET -> secret/alpaca/api_secret
     ```

5. **Migration Execution**
   - Remove `.env` from repository: `git rm .env`
   - Update `.gitignore`: Track `.env.template`, ignore `.env`
   - Update CI pipeline: Use test secrets from backend (not `.env`)

**Testing:**
- Unit test: `rotate_secrets.py` with mocked Alpaca API → new key stored
- Unit test: Rotation logs to `secret_rotation_log` table
- Integration test: Full rotation workflow in staging
- E2E test: `make paper-run` with secrets from backend → completes successfully

**4-Step Pattern:**
1. Implement rotation script, audit tables, runbook, migration
2. Create test suite
3. Request zen-mcp review (clink + gemini → codex)
4. Run `make ci-local` → Commit after BOTH reviewers approve + CI passes

---

#### **Package 3: Documentation & ADR** (7 hours)

**CRITICAL:** Create ADR-0017 BEFORE starting Package 0 implementation to guide design decisions.

**Scope:** Architecture documentation and operational runbooks.

**What gets created:**

1. **ADR-0017** (`docs/ADRs/0017-secrets-management.md`) - **CREATE FIRST**
   - **Context:** Why secrets management needed for production
   - **Decision:** Vault vs AWS Secrets Manager (document choice + rationale)
   - **Alternatives Considered:** Doppler, AWS Parameter Store, GCP Secret Manager
   - **Consequences:**
     - Operational complexity (backend maintenance)
     - Dependency on external service
     - Improved security posture
     - Credential cache mitigates downtime risk (AC22)

2. **Rotation Runbook** (`docs/RUNBOOKS/secret-rotation.md`)
   - 90-day rotation policy
   - Alpaca API key rotation procedure (step-by-step)
   - Zero-downtime deployment process (AC8 workflow)
   - Rollback procedure if rotation fails

3. **Getting Started Guide Updates** (`/docs/GETTING_STARTED/LOCAL_SETUP.md`)
   - Section: "Setting Up Secrets for Local Development"
   - `.env.template` usage instructions
   - `EnvSecretManager` fallback for local dev
   - Backend setup for staging/production

4. **CI/CD Documentation**
   - Update CI docs with secrets backend injection (not `.env`)
   - GitHub Actions integration (Vault Action or AWS Secrets Manager Action)

5. **Project Status Update** (`/docs/GETTING_STARTED/PROJECT_STATUS.md`)
   - Mark P2T2 as complete when done
   - Update "Production Readiness" section

**4-Step Pattern:**
1. Create ADR-0017 (BEFORE Package 0 starts)
2. Create all runbooks and documentation
3. Request zen-mcp review (clink + gemini → codex)
4. Run `make ci-local` → Commit after BOTH reviewers approve + CI passes

---

## Effort Estimation

**Base Implementation:** 51 hours (4 packages: 18h + 14h + 12h + 7h)
**Buffer (15%):** +9 hours (rounded from 7.65h)
**Total:** 60 hours = **7.5 days** (8h/day)

**Package-level breakdown:**
- **Package 0 (Core Secrets Library):** 18 hours (30%)
  - Complex: Interface design, multiple backends, caching, retry logic
- **Package 1 (Service Integration):** 14 hours (23%)
  - Repetitive: 5 services with similar patterns
- **Package 2 (Operational Tooling):** 12 hours (20%)
  - Moderate: Rotation script, audit tables, migration runbook
- **Package 3 (Documentation & ADR):** 7 hours (12%)
  - Straightforward: Documentation writing
- **Buffer:** 9 hours (15%)

**Time allocation per package:**
- **Implement:** 50% (25.5h total)
- **Testing:** 25% (12.75h total)
- **Review + fixes:** 19% (9.7h total)
- **Commit workflow:** 6% (3h total)

**Risk factors:**
- Backend provisioning (Vault/AWS setup): May add 1-2 days if infrastructure team delays
- Service integration complexity: Some services may have non-standard config patterns
- Migration testing: E2E test with real secrets backend may reveal integration issues
- Credential cache implementation (AC22): First-time pattern for this codebase

**Assumptions:**
- Secrets backend (Vault or AWS) is already provisioned OR can be provisioned in parallel
- Existing CI/CD pipeline supports injecting secrets from backend (not `.env` files)
- No live trading in progress during migration (paper trading only)
- ADR-0017 created BEFORE Package 0 starts (not counted in Package 0 time)

---

## Files to Create

### Documentation (CREATE FIRST - Package 3 start)
- `docs/ADRs/0017-secrets-management.md` - Architecture decision record (CREATE BEFORE Package 0)
- `docs/RUNBOOKS/secrets-migration.md` - Operator migration guide
- `docs/RUNBOOKS/secret-rotation.md` - 90-day rotation procedure
- `.env.template` - Template for local dev (placeholders + real config)

### Core Implementation (Package 0)
- `libs/secrets/__init__.py` - Package initialization
- `libs/secrets/manager.py` - Abstract `SecretManager` interface
- `libs/secrets/vault_backend.py` - Vault implementation
- `libs/secrets/aws_backend.py` - AWS Secrets Manager implementation
- `libs/secrets/env_backend.py` - Local dev fallback (reads `.env`)
- `libs/secrets/factory.py` - Backend factory function
- `libs/secrets/exceptions.py` - Custom exceptions

### Scripts & Migrations (Package 2)
- `scripts/rotate_secrets.py` - Secret rotation CLI
- `db/migrations/XXX_secret_rotation_log.sql` - Rotation audit table
- `db/migrations/XXX_secret_access_log.sql` - Access audit table

### Testing (Packages 0, 1, 2)
- `tests/libs/secrets/test_manager.py` - Interface contract tests
- `tests/libs/secrets/test_vault_backend.py` - Vault backend tests (mocked)
- `tests/libs/secrets/test_aws_backend.py` - AWS backend tests (mocked)
- `tests/libs/secrets/test_factory.py` - Factory logic tests
- `tests/integration/test_secrets_config_loading.py` - E2E config loading

---

## Files to Modify

### Service Startup (Package 1)
- `apps/signal_service/main.py` - Initialize `SecretManager`, load DB password
- `apps/signal_service/config.py` - Remove `database_url` from `Settings`
- `apps/execution_gateway/main.py` - Initialize `SecretManager`, load Alpaca credentials
- `apps/execution_gateway/config.py` - Remove `alpaca_api_key_id`, `alpaca_api_secret` from `Settings`
- `apps/orchestrator/main.py` - Initialize `SecretManager`, load DB password
- `apps/orchestrator/config.py` - Remove `database_url` from `Settings`
- `apps/market_data_service/main.py` - Initialize `SecretManager`, load credentials if needed
- `apps/market_data_service/config.py` - Remove credentials from `Settings`

### Testing Infrastructure (Packages 1, 2)
- `tests/conftest.py` - Add fixtures for test `SecretManager` with test namespace
- `.github/workflows/ci.yml` - Update CI to use test secrets from backend (not `.env`)

### Configuration (Packages 0, 2)
- `.gitignore` - Remove `.env`, ensure `.env.template` is tracked
- `pyproject.toml` - Add dependencies: `hvac>=1.2.1`, `boto3>=1.26.0`

### Documentation (Package 3)
- `/docs/GETTING_STARTED/LOCAL_SETUP.md` - Document `.env.template` usage
- `/docs/GETTING_STARTED/PROJECT_STATUS.md` - Mark P2T2 as complete when done

---

## Dependencies and Integration

### External Dependencies

**Python Libraries:**
- `hvac` (HashiCorp Vault client): Version 1.2.1+
- `boto3` (AWS SDK): Version 1.26.0+
- `tenacity` (retry library): Already in use

**Infrastructure:**
- **Vault:** Self-hosted Vault server (recommended for on-premise deployments)
  - Requires: Vault 1.12+ with KV v2 secrets engine enabled
  - Authentication: Token-based or AppRole
- **AWS Secrets Manager:** AWS account with IAM role for secret access
  - Requires: `secretsmanager:GetSecretValue`, `secretsmanager:PutSecretValue` permissions
  - Authentication: IAM role (EC2 instance profile) or access keys

**CI/CD:**
- CI pipeline must support secrets injection from backend (not `.env` files)
- GitHub Actions: Use HashiCorp Vault Action or AWS Secrets Manager Action

### Integration Points

**With Existing Services:**
1. **All services:** Replace Pydantic `Settings` secret fields with `SecretManager` calls
2. **Database connections:** Load password dynamically in `main.py`, construct connection URL
3. **Alpaca client:** Pass credentials from secrets to `AlpacaClient` initialization
4. **Redis client:** Load password (if auth enabled) via secrets

**Service Startup Flow (updated):**
```python
# apps/signal_service/main.py (example)
from libs.secrets.factory import create_secret_manager

# 1. Initialize SecretManager
secret_mgr = create_secret_manager()

# 2. Load secrets
db_password = secret_mgr.get_secret("database/password")
db_url = f"postgresql://trader:{db_password}@localhost:5432/trader"

# 3. Load config (non-secrets)
from apps.signal_service.config import settings

# 4. Initialize database, clients, etc.
engine = create_engine(db_url)
```

**Migration Strategy:**

**Phase 1: Parallel Operation (week 1)**
- Secrets in both `.env` AND secrets backend
- Services load from `.env` (existing behavior)
- Test secret retrieval from backend in staging

**Phase 2: Backend Cutover (week 2)**
- Update services to load from secrets backend
- `.env` retained as fallback for local dev
- Deploy to staging, run E2E tests

**Phase 3: Production Rollout (week 3)**
- Deploy to production one service at a time
- Monitor for secret access errors
- Remove `.env` after successful rollout

**Rollback Plan:**
- Revert service code to load from `.env`
- Secrets remain in backend (no data loss)
- Downtime: <5 minutes per service

---

## Testing Strategy

### Unit Tests

**SecretManager Interface Contract:**
- Test `get_secret()` with valid secret name → returns value
- Test `get_secret()` with non-existent secret → raises `SecretNotFoundError`
- Test `list_secrets()` with prefix filter → returns filtered list
- Test `set_secret()` creates new secret → subsequent `get_secret()` returns value

**Vault Backend (mocked hvac client):**
- Test connection to Vault with valid token → success
- Test connection to Vault with invalid token → raises `SecretAccessError`
- Test secret retrieval with retry on transient failure → succeeds after retry
- Test secret retrieval exhausts retries → raises `SecretAccessError`

**AWS Backend (mocked boto3 client):**
- Test secret retrieval from AWS Secrets Manager → returns value
- Test secret retrieval with throttling (429 error) → retries and succeeds
- Test secret retrieval with IAM permission error → raises `SecretAccessError`

**Factory Logic:**
- Test `create_secret_manager()` with `SECRET_BACKEND=vault` → returns `VaultSecretManager`
- Test `create_secret_manager()` with `SECRET_BACKEND=aws` → returns `AWSSecretsManager`
- Test `create_secret_manager()` with `SECRET_BACKEND=env` → returns `EnvSecretManager`
- Test `create_secret_manager()` with invalid backend → raises `ValueError`

### Integration Tests

**Config Loading with Secrets:**
- Test service startup loads database password from secrets → connects successfully
- Test service startup with missing secret → logs error and fails gracefully
- Test service startup with unreachable backend → retries, then fails with clear error

**Secret Rotation:**
- Test `rotate_secrets.py alpaca_api_key` creates new key → stored in backend
- Test rotation logs event to `secret_rotation_log` table
- Test rotation with invalid secret name → raises error

### E2E Tests

**Service Startup with Secrets Backend:**
- Test signal_service starts with secrets from Vault → health check passes
- Test execution_gateway starts with Alpaca credentials from secrets → connects to Alpaca API
- Test orchestrator starts with DB password from secrets → connects to database

**Paper Trading Run with Secrets:**
- Test full `make paper-run` workflow with secrets loaded from backend → completes successfully
- Test order submission with Alpaca credentials from secrets → orders created

### Manual Testing Checklist

**Secrets Backend Setup:**
- [ ] Provision Vault server or configure AWS Secrets Manager
- [ ] Create service credentials (token or IAM role)
- [ ] Populate secrets: Alpaca API key, DB password, Redis password

**Service Migration:**
- [ ] Update signal_service to use `SecretManager` → test startup
- [ ] Update execution_gateway to use `SecretManager` → test Alpaca connection
- [ ] Update orchestrator to use `SecretManager` → test DB connection

**Secret Rotation:**
- [ ] Rotate Alpaca API key using `scripts/rotate_secrets.py` → verify new key works
- [ ] Check rotation logged in `secret_rotation_log` table
- [ ] Verify old key deprecated after grace period

**Audit Logging:**
- [ ] Check `secret_access_log` table has entries for secret retrievals
- [ ] Verify secret VALUES not logged (only names)

---

## Documentation Requirements

### Must Create

**ADR (Architecture Decision Record):**
- [ ] `docs/ADRs/0017-secrets-management.md`
  - **Context:** Why secrets management is needed for production
  - **Decision:** Chose Vault/AWS Secrets Manager (document which and why)
  - **Alternatives Considered:** Doppler, AWS Parameter Store, GCP Secret Manager
  - **Consequences:** Operational complexity, dependency on external service, improved security posture

**Runbooks:**
- [ ] `docs/RUNBOOKS/secrets-migration.md`
  - Step-by-step guide for operators to migrate from `.env` to secrets backend
  - Includes: backend setup, secret population, service updates, rollback plan
- [ ] `docs/RUNBOOKS/secret-rotation.md`
  - 90-day rotation procedure for Alpaca API keys
  - Includes: generation, storage, rollout, deprecation, audit

### Must Update

**Getting Started Guides:**
- [ ] `/docs/GETTING_STARTED/LOCAL_SETUP.md`
  - Add section: "Setting Up Secrets for Local Development"
  - Document `.env.template` usage
  - Explain fallback to `EnvSecretManager` for local dev

**Project Status:**
- [ ] `/docs/GETTING_STARTED/PROJECT_STATUS.md`
  - Mark P2T2 as complete when done
  - Update "Production Readiness" section to reflect secrets management implementation

**CI/CD Documentation:**
- [ ] Add documentation for CI secrets injection from backend (not `.env` files)

---

## Risks & Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| **Secrets backend downtime during trading hours** | Trading halted (cannot retrieve credentials) | Medium | **AC22 (FORMAL REQUIREMENT):** Credential cache with 1-hour TTL implemented in Package 0; services can continue if backend unreachable for <1h |
| **Misconfigured secret names cause service startup failure** | Service crashes, no trading | Medium | Validate secret names in config; fail-fast with clear error messages; include secret name in exception |
| **Secret rotation causes brief downtime** | Trading interrupted for 1-2 minutes during rotation | Low | **AC8 (FORMAL REQUIREMENT):** Zero-downtime rotation workflow in Package 2: create new secret → update broker → graceful service restart → deprecate old secret |
| **Infrastructure team delays Vault/AWS provisioning** | Implementation blocked for 1-2 days | Medium | Coordinate with infra team early; use `EnvSecretManager` fallback for local dev; provision staging backend first |
| **Audit logs fill disk space** | Database disk full, system crash | Low | Set log retention policy: 90 days max; archive old logs to S3; monitor disk usage |
| **Accidental secret exposure in logs** | Credentials leaked in log files | High | **AC12 (FORMAL REQUIREMENT):** Enforce redaction in logging library; test for secret leakage in logs; add CI check to scan logs for patterns (API keys, passwords) |
| **Migration breaks existing services** | Services crash on startup after migration | Medium | Deploy to staging first; test E2E paper run; rollout to production one service at a time; keep `.env` as rollback option |

---

## Related

**ADRs:**
- `docs/ADRs/0017-secrets-management.md` (to be created)

**Documentation:**
- `docs/RUNBOOKS/secrets-migration.md` (to be created)
- `docs/RUNBOOKS/secret-rotation.md` (to be created)

**Tasks:**
- Depends on: None (can start immediately)
- Blocks: Live trading deployment (cannot go live without secrets management)

---

## Notes

### Backend Selection Criteria

**Vault (Recommended for self-hosted):**
- ✅ Open-source, self-hosted (no vendor lock-in)
- ✅ Fine-grained access control (AppRole, policies)
- ✅ Built-in audit logging
- ✅ Dynamic secrets (can generate DB credentials on-the-fly)
- ❌ Requires infrastructure setup and maintenance
- ❌ High availability requires Raft/Consul

**AWS Secrets Manager (Recommended for AWS deployments):**
- ✅ Fully managed (no ops overhead)
- ✅ IAM integration (leverage existing AWS permissions)
- ✅ Automatic rotation for RDS/Redshift
- ✅ Cross-region replication
- ❌ Vendor lock-in (AWS only)
- ❌ Cost: $0.40/secret/month + $0.05/10k API calls

**Doppler (SaaS Alternative):**
- ✅ User-friendly UI, developer-focused
- ✅ Environment branching (dev/staging/prod)
- ❌ SaaS only (no self-hosted option)
- ❌ Cost: $7-50/user/month

**Recommendation:** Start with AWS Secrets Manager if using AWS; otherwise Vault for self-hosted.

### Secret Naming Convention

Use hierarchical naming with `/` separator:

```
alpaca/api_key_id
alpaca/api_secret
database/password
database/admin_password
redis/password
webhook/secret
mlflow/tracking_uri
mlflow/username
mlflow/password
```

**Benefits:**
- Clear ownership (service or resource prefix)
- Easy filtering via `list_secrets(prefix="alpaca/")`
- Supports multi-environment: `prod/alpaca/api_key`, `staging/alpaca/api_key`

### Local Development Fallback

For local dev, support `.env` fallback via `EnvSecretManager`:

```python
# libs/secrets/env_backend.py
class EnvSecretManager(SecretManager):
    """Fallback for local dev: reads from .env file."""

    def __init__(self, env_file: str = ".env"):
        self.env = dotenv_values(env_file)

    def get_secret(self, name: str) -> str:
        # Map secret name to env var
        # "alpaca/api_key_id" -> "ALPACA_API_KEY_ID"
        env_var = name.upper().replace("/", "_")
        if env_var not in self.env:
            raise SecretNotFoundError(f"Secret not found: {name}")
        return self.env[env_var]
```

**Usage:**
```bash
export SECRET_BACKEND=env  # Use .env fallback
python apps/signal_service/main.py
```

---

## Task Creation Review Checklist

**RECOMMENDED:** Before starting work, request task creation review to validate scope and requirements.

See [`./AI/Workflows/03-reviews.md`](.././AI/Workflows/03-reviews.md) for workflow details.

**Review validates:**
- [x] Objective is clear and measurable
- [x] Success criteria are testable
- [x] Functional requirements are comprehensive
- [x] Trading safety requirements specified (secret values never logged)
- [x] Non-functional requirements documented (retry logic, audit logging)
- [x] Component breakdown follows 4-step pattern (13 components)
- [x] Time estimates are reasonable (6.5 days with 15% buffer)
- [x] Dependencies and blockers identified (infrastructure provisioning)
- [x] ADR requirement clear (ADR-0017 for secrets architecture)
- [x] Test strategy comprehensive (unit, integration, E2E)

**When to use task review:**
- ✅ Complex tasks (>4 hours estimated) - **P2T2 is 6.5 days**
- ✅ Tasks with architectural changes - **Major change: secrets management architecture**
- ✅ Tasks with unclear requirements - **Requirements are clear but validation is valuable**
- ✅ New feature development - **New capability: secrets management**

**How to request:**
```bash
# Phase 1: Gemini validation
"Review docs/TASKS/P2T2_SECRETS_MANAGEMENT.md using clink + gemini planner"

# Phase 2: Codex synthesis
"Use clink + codex planner with continuation_id to synthesize readiness assessment"
```

---

## State Transition Instructions

**When starting this task:**

```bash
# 1. Rename file
git mv docs/TASKS/P2T2_SECRETS_MANAGEMENT.md docs/TASKS/P2T2_PROGRESS.md

# 2. Update front matter in P2T2_PROGRESS.md:
#    state: PROGRESS
#    started: YYYY-MM-DD

# 3. Commit
git add docs/TASKS/P2T2_PROGRESS.md
git commit -m "Start P2T2: Secrets Management"
```

**Or use automation:**
```bash
./scripts/tasks.py start P2T2
```
