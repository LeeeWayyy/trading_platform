# ADR 0017: Secrets Management

- Status: Accepted
- Date: 2025-11-06

## Context

### Problem Statement

Currently, all sensitive credentials (Alpaca API keys, database passwords, Redis URLs, webhook secrets) are stored in plain-text `.env` files. While gitignored, this approach creates critical security vulnerabilities that block production deployment:

| Risk | Impact | Severity |
|------|--------|----------|
| **Plain-text credentials in filesystem** | Disk access = credential access (no encryption at rest) | Critical |
| **No access audit trail** | Cannot detect unauthorized secret access or correlate issues to secret retrieval failures | High |
| **Manual rotation** | Stale credentials risk, no enforcement of rotation policy | High |
| **Secrets mixed with config** | Difficult to enforce separation of concerns (secrets vs config) | Medium |
| **CI/CD complexity** | Manual `.env` file distribution to production servers | Medium |
| **Accidental commits** | `.gitignored` protects but human error risk remains (leaked keys in commit history) | Medium |

### Security Risks

1. **Compromised developer laptop:** Attacker gains plain-text access to production API keys
2. **Insider threat:** Any engineer with filesystem access can read all secrets
3. **Stale credentials:** No enforcement or reminder mechanism for 90-day rotation
4. **No principle of least privilege:** All services see all secrets in `.env` (over-provisioning)
5. **Backup exposure:** `.env` file in backups = long-term credential exposure

### Why Now?

- **P2 Phase:** Production-readiness is the primary objective
- **Compliance:** Many regulatory frameworks (SOC2, PCI DSS) require encrypted secrets at rest
- **Trading Safety:** Live trading with plain-text API keys is unacceptable risk
- **Maturity:** Platform architecture is stable enough to introduce secrets management without disrupting active development

## Decision

### Core Architecture

We will implement a **pluggable secrets management system** with the following components:

1. **SecretManager Abstract Interface** (`libs/secrets/manager.py`)
   - Defines contract: `get_secret(name)`, `list_secrets()`, `set_secret(name, value)`
   - Enables backend swapping without service code changes
   - Type-safe with explicit exception hierarchy

2. **Backend Implementations**
   - **VaultSecretManager** (`libs/secrets/vault_backend.py`) - HashiCorp Vault via hvac library
   - **AWSSecretsManager** (`libs/secrets/aws_backend.py`) - AWS Secrets Manager via boto3
   - **EnvSecretManager** (`libs/secrets/env_backend.py`) - Local development fallback (reads `.env`)

3. **Backend Factory** (`libs/secrets/factory.py`)
   - Selects backend via `SECRET_BACKEND` environment variable
   - Supports: `vault`, `aws`, `env` (default: `env` for local dev)

4. **Credential Cache** (`libs/secrets/cache.py`)
   - In-memory cache with 1-hour TTL (Trading Safety: AC22)
   - Mitigates secrets backend downtime (prevents trading halt)
   - Cache invalidation on service restart or 401 errors

### Dual-Mode Configuration Pattern

**Principle:** Secrets via `SecretManager`, configuration via Pydantic `Settings` (env vars)

**Secrets (via SecretManager):**
- `alpaca/api_key_id` - Alpaca API key ID
- `alpaca/api_secret_key` - Alpaca API secret key
- `database/password` - PostgreSQL password
- `redis/password` - Redis password (future enhancement, not in P2T2 scope)
- `webhook/secret` - Webhook HMAC secret

**Configuration (via env vars):**
- `ALPACA_BASE_URL` - API endpoint (paper vs live)
- `DATABASE_HOST`, `DATABASE_PORT`, `DATABASE_NAME` - Connection params
- `REDIS_URL` - Redis connection string (password separate)
- `DRY_RUN`, `LOG_LEVEL`, `STRATEGY_ID` - Operational config
- `SECRET_BACKEND` - Backend selection (`vault`, `aws`, `env`)

### Backend Selection Rationale

**Why Vault for Production?**
- Open-source, self-hosted control (no cloud vendor lock-in)
- Dynamic secrets support (future enhancement: auto-generated DB credentials)
- Fine-grained access policies (per-service, per-secret permissions)
- Audit logging built-in
- Integration with Kubernetes via service accounts

**Why AWS Secrets Manager as Alternative?**
- Cloud-native for AWS deployments
- Automatic rotation integration with RDS, Redshift
- KMS encryption at rest
- Cross-region replication for disaster recovery
- Lower operational overhead (managed service)

**Why EnvSecretManager for Local Dev?**
- Zero setup friction for new developers
- Familiar `.env` workflow
- Clear migration path: local dev → staging (Vault) → production (Vault)

**Primary Choice:** Vault for production, EnvSecretManager for local development.
**Rationale:** Educational project - demonstrates self-hosted secrets management. AWS Secrets Manager available as reference implementation.

### Secret Rotation Workflow

**7-Step Zero-Downtime Rotation** (AC8):

1. **Generate new secret** in backend (Vault/AWS)
2. **Update Alpaca API** with new key (keep old key active - grace period)
3. **Update backend** with primary/secondary keys
4. **Trigger graceful service restart** (services fetch new secret on startup via cache invalidation)
5. **Verify new secret works** (health checks pass)
6. **Deprecate old secret** in backend (mark as `rotation_date`)
7. **Schedule old secret deletion** (30 days)

**Grace Period:** 24 hours (old key valid during rollout, allows rollback)

**Rotation Schedule:** 90 days (automated reminder, manual execution)

**Audit Logging:** All rotations logged to `secret_rotation_log` database table

### Trading Safety Features

**Credential Cache with 1-Hour TTL (AC22):**
- **Problem:** Secrets backend downtime halts trading (cannot fetch credentials)
- **Solution:** In-memory cache with 1-hour TTL
- **Behavior:**
  - Service startup: If cache miss + backend unreachable → fail-fast with clear error
  - Runtime: If cache hit + backend unreachable → use cached credentials (up to 1 hour)
  - After 1 hour: If backend still unreachable → circuit breaker trips, stop trading
- **No Disk Persistence:** Credentials never written to disk (more secure, acceptable cold-start trade-off)

**Fail-Fast on Invalid Credentials:**
- Alpaca API returns 401 Unauthorized → AlpacaValidationError → circuit breaker trips
- Service health check fails → alerting triggered
- **No auto-recovery:** Manual restart required (prevents trading with stale credentials)

**Secret Redaction (AC12):**
- Logging config enforces redaction (replace patterns like `api_key=xyz` with `api_key=***`)
- SecretManager exceptions NEVER include secret values (only names)
- CI check: Scan logs for patterns (API key format, `pk-`, `sk-`) → fail if found

### Migration Path

**Phase 1: Core Library Implementation (Package 0)**
- Implement SecretManager interface + backends + factory + cache
- Unit tests with mocked Vault/AWS clients (>90% coverage)

**Phase 2: Service Integration (Package 1)**
- Update all 5 services (signal_service, execution_gateway, orchestrator, market_data_service, risk_manager)
- Remove secret fields from Pydantic Settings, construct dynamically in `main.py`
- Integration tests with test secret namespace

**Phase 3: Operational Tooling (Package 2)**
- Rotation script (`scripts/rotate_secrets.py`)
- Audit script (`scripts/audit_secrets.py`)
- Migration script (`scripts/migrate_secrets.py` - populates backend from `.env`)

**Phase 4: Documentation & Migration (Package 3)**
- ADR-0017 (this document)
- Migration runbook (`docs/RUNBOOKS/secrets-migration.md`)
- Rotation runbook (`docs/RUNBOOKS/secret-rotation.md`)
- Update local setup guide (`.env.template` usage)

**Rollback Strategy:**
- If secrets backend fails: Fall back to `EnvSecretManager` via `SECRET_BACKEND=env`
- If service startup fails: Revert to previous commit, investigate with `make ci-local`
- `.env` file kept for 30 days after migration (safety net)

## Consequences

### Positive

**Security Improvements:**
- ✅ Secrets encrypted at rest (Vault KV v2 encryption)
- ✅ Audit trail for all secret access (timestamp, service, secret name)
- ✅ Automated rotation workflow (reduces stale credential risk)
- ✅ Principle of least privilege (services only access required secrets)
- ✅ No plain-text credentials in filesystem or backups

**Operational Benefits:**
- ✅ Centralized secret management (single source of truth)
- ✅ Simplified CI/CD (inject secrets programmatically, not manual `.env` files)
- ✅ Clear separation of concerns (secrets vs configuration)
- ✅ Production-ready credential management (SOC2/PCI DSS compliant)

**Trading Safety:**
- ✅ Credential cache prevents trading halt during backend downtime (1-hour survival window)
- ✅ Fail-fast on invalid credentials (prevents trading with stale API keys)
- ✅ Zero-downtime rotation (no trading interruption during key updates)

**Developer Experience:**
- ✅ Local development unchanged (`EnvSecretManager` maintains `.env` workflow)
- ✅ Clear migration path (local → staging → production)
- ✅ `.env.template` provides setup guidance

### Negative / Trade-Offs

**Complexity:**
- ⚠️ New infrastructure dependency (Vault server or AWS Secrets Manager)
- ⚠️ Service startup now depends on secrets backend availability
- ⚠️ Additional retry/error handling logic required

**Operational Overhead:**
- ⚠️ Vault server requires setup, maintenance, backup (or AWS Secrets Manager costs)
- ⚠️ Secret rotation requires manual execution (automated reminders only)
- ⚠️ Migration requires coordination (populate backend, update service configs, remove `.env`)

**Performance:**
- ⚠️ Secret fetch latency: ~50-100ms for Vault HTTP call (mitigated by 1-hour cache)
- ⚠️ Service startup time increased by ~200-300ms (one-time secret fetch)

**Developer Friction:**
- ⚠️ New developers need `.env.template` setup (vs automated `.env` generation)
- ⚠️ Local Vault setup optional but recommended for staging parity

### Mitigations

**Complexity Mitigation:**
- Comprehensive unit tests (>90% coverage) validate backend behavior
- Integration tests verify service startup with secrets
- E2E tests confirm full paper_run with secrets from backend

**Operational Mitigation:**
- Detailed runbooks for migration, rotation, troubleshooting
- Vault setup documented in ADR (step-by-step)
- Rollback strategy: `SECRET_BACKEND=env` escape hatch (LOCAL DEV ONLY)
  - **PRODUCTION GUARDRAIL**: EnvSecretManager MUST NOT be used in staging/production
  - Services will fail startup if `SECRET_BACKEND=env` in non-local environments
  - Enforce via environment variable: `DEPLOYMENT_ENV` (local/staging/production)
  - Factory validation: Reject `env` backend if `DEPLOYMENT_ENV != local`

**Performance Mitigation:**
- 1-hour credential cache reduces backend calls to 1 per hour per service
- Retry logic with exponential backoff (3 attempts, max 5s wait)
- Connection pooling for Vault HTTP client (keep-alive)

**Developer Friction Mitigation:**
- `.env.template` with placeholders + real config values
- Local setup guide updated with secret workflow
- `EnvSecretManager` preserves familiar `.env` workflow

### Risks

**Risk 1: Secrets Backend Downtime**
- **Impact:** Services cannot start if backend unreachable + cache miss
- **Probability:** Low (Vault/AWS have high availability)
- **Mitigation:** 1-hour credential cache + circuit breaker + alerting
- **Contingency:** Fall back to `EnvSecretManager` via `SECRET_BACKEND=env`

**Risk 2: Invalid Credentials After Rotation**
- **Impact:** Alpaca API returns 401, trading halts
- **Probability:** Medium (rotation requires manual verification)
- **Mitigation:** Zero-downtime rotation with 24-hour grace period + health checks
- **Contingency:** Rollback to old key (still active during grace period)

**Risk 3: Secret Exposure in Logs**
- **Impact:** Credentials leaked via exception tracebacks or debug logs
- **Probability:** Low (logging redaction enforced)
- **Mitigation:** CI check scans logs for secret patterns + SecretManager never logs values
- **Contingency:** Immediate rotation of exposed secret + audit log review

**Risk 4: Migration Failure**
- **Impact:** Services fail to start after migration
- **Probability:** Low (comprehensive testing + rollback plan)
- **Mitigation:** Staged rollout (local → staging → production) + keep `.env` for 30 days
- **Contingency:** Revert to `.env` via `SECRET_BACKEND=env` + investigate with `make ci-local`

### Follow-Up Work

**Immediate (P2T2 Scope):**
- ✅ Implement Core Secrets Library (Package 0)
- ✅ Integrate into all 5 services (Package 1)
- ✅ Build operational tooling (Package 2)
- ✅ Write documentation and runbooks (Package 3)

**Near-Term (P3):**
- Dynamic database credentials (Vault auto-generates, auto-rotates DB passwords)
- Redis password management (currently auth disabled)
- Webhook secret integration (execution gateway future feature)
- Cross-region Vault replication (disaster recovery)

**Long-Term (Future):**
- Kubernetes integration (Vault agent sidecar injection)
- Automated secret rotation (triggered via cron, not manual)
- Secret versioning and rollback (restore previous secret version)
- External secret operator (sync Vault secrets to Kubernetes secrets)

### Educational Value

This implementation demonstrates several production-grade patterns:

1. **Abstract Factory Pattern:** Backend factory enables swapping implementations
2. **Dependency Injection:** Services receive SecretManager via constructor
3. **Separation of Concerns:** Secrets vs configuration cleanly separated
4. **Defense in Depth:** Multiple layers (cache, retry, circuit breaker, audit logging)
5. **Graceful Degradation:** Cache survives backend downtime (1-hour window)
6. **Fail-Fast Philosophy:** Invalid credentials immediately trip circuit breaker
7. **Zero-Downtime Operations:** Secret rotation without service interruption

### Compatibility with Existing Systems

**No Breaking Changes:**
- Pydantic Settings remain for configuration (only secret fields removed)
- Service APIs unchanged (internal implementation only)
- FastAPI startup flow compatible (add SecretManager init before DB/Redis clients)
- Testing unchanged (`EnvSecretManager` for unit tests, test namespace for integration)

**Backward Compatibility:**
- `EnvSecretManager` preserves `.env` workflow for local development
- Migration script supports gradual rollout (service-by-service)
- Rollback to `.env` via `SECRET_BACKEND=env` if issues arise

### Success Metrics

**Acceptance Criteria:**
- All 22 ACs in P2T2_TASK.md satisfied
- `make ci-local` passes (no regressions)
- All services start successfully with secrets from backend
- Paper run completes end-to-end with secrets from backend
- Secret rotation completes without trading interruption
- Zero secret values appear in logs (CI check passes)

**Operational Metrics:**
- Secret fetch latency p95 < 200ms (with cache: p95 < 1ms)
- Service startup time increase < 500ms (acceptable)
- Rotation completion time < 10 minutes (manual execution)
- Audit log 100% coverage (all secret accesses logged)

## Alternatives Considered

### Alternative 1: Doppler Secrets Management

**Pros:**
- Managed service (no operational overhead)
- Generous free tier
- Excellent developer experience (CLI, dashboard)
- Automatic sync to environments

**Cons:**
- Cloud dependency (vs self-hosted Vault)
- Less educational value (opaque managed service)
- Limited dynamic secrets support
- Vendor lock-in

**Decision:** Rejected - prefer self-hosted Vault for educational transparency and operational control.

### Alternative 2: AWS Systems Manager Parameter Store

**Pros:**
- Native AWS integration
- Free for standard parameters
- Simple API
- No additional infrastructure

**Cons:**
- Throughput limits (10,000 requests/sec/region)
- No built-in rotation workflow
- Limited access control granularity
- AWS-only (no multi-cloud support)

**Decision:** Rejected - prefer Vault for richer feature set and multi-cloud portability.

### Alternative 3: Keep .env Files with Ansible Vault Encryption

**Pros:**
- Simple deployment (encrypted `.env` files)
- No runtime dependency (decrypt on deploy)
- Familiar workflow

**Cons:**
- Still file-based (no centralized management)
- No audit logging
- Manual rotation (no automation)
- No dynamic secrets
- Decrypted secrets still in-memory (vulnerable to dumps)

**Decision:** Rejected - insufficient for production-grade secret management.

### Alternative 4: Kubernetes Secrets + External Secrets Operator

**Pros:**
- Native Kubernetes integration
- Declarative configuration
- Auto-sync from external backends

**Cons:**
- Requires Kubernetes (overkill for current deployment)
- Base64 encoding ≠ encryption (without envelope encryption)
- Adds deployment complexity

**Decision:** Rejected - premature for current deployment model (Docker Compose). Revisit in P4 (Kubernetes migration).

## Implementation Notes

**Package 0: Core Secrets Library (18 hours)**
- `libs/secrets/manager.py` - Abstract interface
- `libs/secrets/vault_backend.py` - Vault implementation
- `libs/secrets/aws_backend.py` - AWS implementation
- `libs/secrets/env_backend.py` - Local dev fallback
- `libs/secrets/factory.py` - Backend factory
- `libs/secrets/cache.py` - 1-hour TTL credential cache
- `libs/secrets/exceptions.py` - Exception hierarchy
- Unit tests (>90% coverage)

**Package 1: Service Integration (14 hours)**
- Update all 5 services (`main.py`, `config.py`, `conftest.py`)
- Remove secret fields from Pydantic Settings
- Construct connection strings dynamically
- Integration tests with test secret namespace

**Package 2: Operational Tooling (12 hours)**
- `scripts/rotate_secrets.py` - Zero-downtime rotation
- `scripts/audit_secrets.py` - Access audit reporting
- `scripts/migrate_secrets.py` - Populate backend from `.env`
- Database migrations for audit tables

**Package 3: Documentation & ADR (7 hours)**
- ADR-0017 (this document)
- Migration runbook
- Rotation runbook
- Update local setup guide

**Total Effort:** 51 hours (7.5 days with 15% buffer = 60 hours)

## References

- HashiCorp Vault Documentation: https://developer.hashicorp.com/vault
- AWS Secrets Manager Documentation: https://docs.aws.amazon.com/secretsmanager/
- OWASP Secrets Management Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html
- ADR-0009: Redis Integration (credential caching patterns)
- ADR-0011: Risk Management System (circuit breaker integration)
- P2T2_TASK.md: Detailed acceptance criteria and implementation plan
