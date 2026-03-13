# ADR-0038: DB Role Authority Override

**Status:** Accepted
**Date:** 2026-03-12
**Deciders:** @development-team

## Context

Auth providers (OAuth2, mTLS, basic, dev) derive the session role from their identity source (IdP claims, certificate OU, env vars) — they do NOT read the `user_roles` DB table. When an admin changes a user's role via `change_user_role()`, the DB is updated but the active session retains the old provider-derived role until the user logs out and back in.

This creates a gap: admin role changes are not authoritative for active sessions. A demoted user retains elevated permissions until their session expires or they re-authenticate.

## Decision

Override the provider-derived session role with the DB role from `user_roles` per-request in `AuthMiddleware.dispatch`.

### Implementation

1. **Middleware DB role override** (`AuthMiddleware.dispatch`): After the user-population block, query `user_roles` for the session user's DB role. If it differs from the session role, update in-memory state, NiceGUI storage, and the Redis session payload.

2. **Failure behavior — fail-open**: On DB timeout, connection error, or any exception, skip the override and keep the provider-derived session role. The middleware must NEVER block or error a request due to a role-override failure.

3. **Caching strategy**: Redis cache `ng_role_cache:{user_id}` with 60s TTL. On cache hit, use cached role. On miss, query DB and populate cache. `change_user_role()` deletes the cache key immediately for instant propagation.

4. **Scope limitation**: Only affects `@require_permission`-gated routes (which consume the session role via `X-User-Role` header). Does NOT affect `api_auth()`-gated routes which authenticate via S2S tokens or JWT.

## Consequences

### Positive
- Admin role changes take effect within 60s (cache TTL) without requiring re-login
- "Force Logout" button provides immediate effect as defense-in-depth
- No changes required to auth providers

### Negative
- Adds a per-request DB/cache lookup (mitigated by 60s Redis cache)
- Fail-open means a recently demoted user retains elevated role during DB outages (bounded by session absolute_timeout)

### Security Trade-off
Fail-open is accepted because:
- The window is bounded by `absolute_timeout` (sessions expire)
- Force Logout immediately invalidates Redis sessions (no DB dependency)
- Fail-closed would block ALL users during DB outages
