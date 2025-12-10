# ADR-024: Analytics Security Architecture

**Status:** Accepted
**Date:** 2025-12-10
**Author:** Claude Code
**Related:** P4T3 (Web Console - Core Analytics), ADR-018 (Web Console mTLS Authentication)

## Context

Track 6 (P4T3) implements analytics dashboards for the trading platform's web console. These dashboards expose sensitive P&L, position, and trade data. The existing OAuth2/PKCE authentication infrastructure (from P2) validates user identity but lacks:

1. Role-based access control (RBAC)
2. Per-strategy authorization
3. Comprehensive audit logging for compliance
4. 2FA step-up authentication for destructive actions
5. Rate limiting for manual trade controls

## Decision

### 1. Role Sourcing: Database vs Auth0 Custom Claims

**Decision:** Store roles in PostgreSQL `user_roles` table, fetch on login, cache in Redis session.

**Rationale:**
- Auth0 custom claims require Management API integration (complexity)
- DB lookup is simple, auditable, and allows runtime updates
- Role cached in session for fast permission checks
- `session_version` enables immediate invalidation on role change

**Alternatives Considered:**
- Auth0 custom claims: More "pure" OIDC but requires Auth0 Management API
- JWT claims at login: Stale until token refresh (up to 15 min delay)

### 2. Session Invalidation: Immediate vs Polling

**Decision:** Use `session_version` integer in `user_roles` table, checked per-request.

**Rationale:**
- Security benefit outweighs DB hit (~1ms per request)
- Enables immediate revocation when admin changes role/strategy
- No polling delay - takes effect on next request
- Consider Redis caching of session_version with 30s TTL if latency becomes issue

**Alternatives Considered:**
- Redis pub/sub: Complex, eventual consistency
- Polling with TTL: Up to N seconds of stale access

### 3. 2FA Mechanism: Auth0 Step-Up vs Internal TOTP

**Decision:** Use Auth0 re-authentication (step-up auth) with `amr` claim validation.

**Rationale:**
- Auth0 already handles MFA enrollment/management
- No need to build separate TOTP infrastructure
- Leverages existing OAuth2 flow with `prompt=login&max_age=0`
- `amr` claim provides evidence of MFA method used

**Implementation:**
- Destructive actions (flatten_all) trigger step-up redirect
- Callback validates `amr` contains MFA method (otp, sms, webauthn)
- `auth_time` must be within 60 seconds
- `step_up_requested_at` enforces 5-minute callback timeout

**Alternatives Considered:**
- Internal TOTP: Full control but significant implementation effort
- SMS OTP: Auth0 handles this via amr claim

### 4. Rate Limiter Fallback: Fail-Open vs Fail-Closed

**Decision:** Default to fail-open (allow) on Redis failure, configurable to fail-closed.

**Rationale:**
- Trading operations should remain available during Redis outage
- Rate limiting is defense-in-depth, not primary security control
- Logging on fallback enables detection and alerting
- Configurable via `RATE_LIMITER_FALLBACK_MODE` for high-security deployments

**Alternatives Considered:**
- Always fail-closed: More secure but impacts availability
- In-memory fallback: Complex, state drift across instances

### 5. Strategy Scoping: Client-Side vs Server-Side

**Decision:** Server-side only via `StrategyScopedDataAccess` class.

**Rationale:**
- Client-side filtering can be bypassed
- Server-side enforcement with parameterized queries prevents SQL injection
- Cache keyed by user_id + strategy_set_hash prevents cross-user leakage
- Pagination enforced server-side (100 default, 1000 max)

**Implementation:**
- All dashboard queries go through `StrategyScopedDataAccess`
- Strategy filter applied to ALL data queries via `strategy_id = ANY($1)`
- Admin role uses `VIEW_ALL_STRATEGIES` permission
- Empty strategy list raises `PermissionError` (fail-closed)

## Consequences

### Positive
- Clear separation of concerns (auth vs authorization)
- Immediate privilege revocation without session deletion
- Comprehensive audit trail for compliance
- Defense-in-depth with multiple security layers

### Negative
- Per-request DB hit for session_version (~1ms overhead)
- Redis dependency for rate limiting (with fallback)
- Step-up auth adds UX friction for destructive actions

### Risks Mitigated
- Unauthorized access: RBAC + per-request validation + audit
- Strategy data leakage: Server-side scoping + regression tests
- Accidental flatten: 2FA + rate limiting + reason field
- Stale elevated access: session_version invalidation

## Implementation

See `docs/PLANS/P4T3_T6.1a_PLAN.md` for detailed implementation plan.

### Key Files
- `apps/web_console/auth/permissions.py` - RBAC roles/permissions
- `apps/web_console/auth/session_invalidation.py` - session_version management
- `apps/web_console/auth/mfa_verification.py` - 2FA step-up validation
- `apps/web_console/auth/rate_limiter.py` - Redis rate limiting
- `apps/web_console/data/strategy_scoped_queries.py` - Server-side scoping
- `db/migrations/0005_update_audit_log_schema.sql` - Audit schema
- `db/migrations/0006_create_rbac_tables.sql` - RBAC tables

## References

- [OWASP RBAC Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)
- [Auth0 Step-Up Authentication](https://auth0.com/docs/secure/multi-factor-authentication/step-up-authentication)
- [ADR-018: Web Console mTLS Authentication](./0018-web-console-mtls-authentication.md)
