---
id: P6T19
title: "Simplify Auth — Remove Multi-User RBAC, Single Admin Account"
phase: P6
task: T19
priority: P1
owner: "@development-team"
state: TASK
created: 2026-03-26
dependencies: [P6T16]
related_adrs: [ADR-XXXX-single-admin-auth-simplification]
related_docs: [docs/TASKS/P6T16_TASK.md]
components: [T19.1, T19.2, T19.3, T19.4]
estimated_effort: "5-7 days"
---

# P6T19: Simplify Auth — Remove Multi-User RBAC, Single Admin Account

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** TASK
**Priority:** P1 (Simplification / Tech Debt)
**Owner:** @development-team
**Created:** 2026-03-26
**Track:** Track 19 of N
**Dependency:** P6T16 (Admin Pages — being replaced)
**Estimated Effort:** 5-7 days

> **ADR Required:** This is a cross-service auth architecture change. Create ADR before implementation.

---

## Objective

Remove the multi-user management system and replace it with a single admin account that has full permissions. The current RBAC system (4 roles, 52 permissions, 4 dataset tiers, strategy-level access control) is overengineered for a single-operator trading platform.

**Success looks like:**
- Single admin account with full access to all features and datasets
- Login still required (console remains password-protected)
- No RBAC role checks, no role cache, no permission lookups, no dataset filtering
- User management UI pages and components removed
- Database tables `user_roles` and `user_strategy_access` removed (with `api_keys` FK handled)
- Audit logging retained (simplified — single user, all actions logged)
- All 26+ protected pages work without permission checks
- `get_authorized_strategies()` returns all strategies (no per-user filtering)

**Cross-Service Impact (intentional):**
The shared permission library (`libs/platform/web_console_auth/permissions.py`) is imported by the web console, execution gateway, and market-data service. (Signal service also configures `api_auth(require_permission=Permission.GENERATE_SIGNALS)` at `apps/signal_service/main.py:1047`, but it is S2S-only with no JWT authenticator configured at `apps/signal_service/main.py:1043,1054` — `has_permission()` is only called in the JWT branch of `libs/core/common/api_auth_dependency.py:780`, so it is not affected in practice.) Making `has_permission()` always return True **intentionally** bypasses user-facing RBAC enforcement across these services. Two different mechanisms call `has_permission()`: (1) the `@require_permission` decorator on execution gateway routes (e.g., `@require_permission(Permission.MANAGE_STRATEGIES)` at `apps/execution_gateway/routes/admin.py:187`, `@require_permission(Permission.VIEW_PNL)` at `apps/execution_gateway/routes/positions.py:173`), and (2) the `api_auth(require_permission=...)` config parameter on market-data and signal service routes (e.g., `require_permission=Permission.VIEW_MARKET_DATA` at `apps/market_data_service/routes/market_data.py:31`, `require_permission=Permission.GENERATE_SIGNALS` at `apps/signal_service/main.py:1047`). Both mechanisms ultimately call `has_permission()` for user-auth paths, so both are affected. This is the desired behavior for a single-admin platform — user-facing RBAC checks are removed. The execution gateway's `api_auth` mechanism (S2S tokens, JWT Bearer) and `populate_user_from_headers` middleware are **not changed** — authentication is still enforced. Note: the S2S internal-token path also has `_service_has_permission()` in `api_auth_dependency.py` which enforces a per-service permission allowlist — this is a separate mechanism from user RBAC and is intentionally **kept unchanged** (it controls which services can call which endpoints, not which users can).

**Identity Restriction (MUST be atomic with permission bypass — T19.1):**
Since T19.1 makes all permission checks return True, the identity allowlist MUST be added in the same atomic change. Otherwise any authenticated user gets full admin between T19.1 and T19.2 deployment. Identity restriction covers **all** auth provider paths:

1. **OAuth2 — `apps/web_console_ng/auth/providers/oauth2.py`** — the web console OAuth2 handler (`OAuth2AuthHandler.handle_callback()` at line ~223). Currently accepts any `sub` from the IdP — must add identity allowlist check.
2. **OAuth2 — `libs/platform/web_console_auth/oauth2_flow.py`** — the auth_service flow (`OAuth2FlowHandler._load_rbac_data()` at line ~286). Currently denies unprovisioned users via `user_roles` lookup — must replace with identity allowlist.
3. **mTLS — `apps/web_console_ng/auth/providers/mtls.py`** — accepts any proxy-validated client cert and extracts CN from the DN via `_parse_dn()` (line ~180). Currently has no subject allowlist at the provider level (the shared `mtls_fallback.py` has `admin_cn_allowlist` but this provider does not use it). Must add CN allowlist check, or delegate to `get_admin_cn_allowlist()` from `libs/platform/web_console_auth/mtls_fallback.py`.
4. **dev/basic providers** — dev provider (`apps/web_console_ng/auth/providers/dev.py`) is hard-blocked in production via config (`AUTH_TYPE='dev'` raises `ValueError` when `DEBUG=False` at `apps/web_console_ng/config.py:235`). Basic provider (`apps/web_console_ng/auth/providers/basic.py`) is blocked at runtime first by `DEBUG=False` check at `basic.py:65`, then by `ALLOW_DEV_BASIC_AUTH` check at `basic.py:74`. Neither needs identity restriction, but both must populate `strategies` (see T19.1 strategy requirement).

Without identity restriction, any IdP-authenticated subject (OAuth2) or any valid cert CN (mTLS) would receive an admin session. **Recommended approach: option (b)** — per-provider env vars matching existing codebase patterns. OAuth2 uses `OAUTH2_ALLOWED_SUBS` (comma-separated `sub` values from IdP), mTLS uses the existing `MTLS_ADMIN_CN_ALLOWLIST` env var already defined in `.env.example:142` and implemented in `libs/platform/web_console_auth/mtls_fallback.py:621`. This avoids namespace confusion between OAuth2 `sub` identifiers and mTLS CN strings. Add `OAUTH2_ALLOWED_SUBS` to `apps/web_console_ng/config.py` alongside existing OAuth2 config vars (lines 276-285). Alternatives considered: (a) single `ALLOWED_USER_IDS` — namespace collision risk between provider identifier formats, (c) new `allowed_users` table — not viable since T19.3 drops RBAC tables. All production auth paths must check their respective allowlist before creating sessions.

**Out of Scope:**
- Changing the auth provider *mechanisms* (dev/basic/mTLS/OAuth2 login flows stay as-is)
- Changing session management (Redis sessions stay)
- Removing audit logging
- Changing the execution gateway `api_auth` *authentication* mechanism (S2S tokens, JWT Bearer)

---

## Pre-Implementation Analysis

> Run `/analyze` before implementation.

**Existing Infrastructure:**

| Component | Status | Action |
|-----------|--------|--------|
| `libs/platform/web_console_auth/permissions.py` | EXISTS (479 lines) — Role enum, 52 permissions, RBAC maps | SIMPLIFY: `has_permission()`, `has_dataset_permission()`, `is_admin()` always True; `get_authorized_strategies()` returns all strategies; keep `ROLE_PERMISSIONS` and `ROLE_DATASET_PERMISSIONS` as empty stubs for re-export compatibility |
| `libs/web_console_services/user_management.py` | EXISTS (620 lines) — Multi-user CRUD | DELETE |
| `apps/web_console_ng/pages/admin_users.py` | EXISTS (419 lines) — User management page | DELETE |
| `apps/web_console_ng/components/user_table.py` | EXISTS — AG Grid user list | DELETE |
| `apps/web_console_ng/components/user_activity.py` | EXISTS — User activity log | DELETE |
| `apps/web_console_ng/components/role_selector.py` | EXISTS — Role change dialog | DELETE |
| `apps/web_console_ng/components/strategy_grants.py` | EXISTS — Strategy grant/revoke UI | DELETE |
| `scripts/ops/manage_roles.py` | EXISTS (170 lines) — CLI role management | DELETE |
| `apps/web_console_ng/auth/middleware.py` | EXISTS (669 lines) — `_override_role_from_db()` inline DB lookup + Redis role cache | SIMPLIFY: Remove `_override_role_from_db()`, Redis `ng_role_cache` logic; hardcode admin role. NOTE: middleware itself has no `session_version` checks or `verify_db_role` calls |
| `apps/web_console_ng/auth/db_role.py` | EXISTS (54 lines) — `verify_db_role()` for mutation callbacks | DELETE (also remove imports from `admin.py` and `tax_lots.py`) |
| `apps/web_console_ng/pages/admin.py` | EXISTS (1296 lines) — Admin dashboard (tabs: API Keys, Config, Recon, Audit) | MODIFY: Remove `verify_db_role` import and all `verify_db_role()` calls |
| `apps/web_console_ng/pages/tax_lots.py` | EXISTS — Tax lot management | MODIFY: Remove `verify_db_role` import and calls |
| `apps/web_console_ng/ui/layout.py` | EXISTS — Main navigation layout | MODIFY: Remove "Users" nav link to `/admin/users` |
| `apps/web_console_ng/auth/redirects.py` | EXISTS — Redirect allowlist | MODIFY: Remove `/admin/users` from `ALLOWED_REDIRECT_PATHS` |
| `apps/web_console_ng/pages/__init__.py` | EXISTS — Page registry | MODIFY: Remove `admin_users` import |
| `apps/web_console_ng/auth/permissions.py` | EXISTS — Re-exports `ROLE_PERMISSIONS`, `ROLE_DATASET_PERMISSIONS` from shared lib | KEEP: Re-exports continue to work (stubs) |
| `libs/platform/web_console_auth/__init__.py` | EXISTS — Package exports (re-exports `ROLE_PERMISSIONS` but NOT `ROLE_DATASET_PERMISSIONS`) | KEEP: Ensure re-exports still work |
| `libs/platform/web_console_auth/audit_log.py` | EXISTS — Audit logging | KEEP |
| `libs/platform/web_console_auth/gateway_auth.py` | EXISTS — S2S auth with `user_roles` queries | SIMPLIFY: Remove `user_roles`/`user_strategy_access` DB queries, return admin role and all strategies |
| `libs/platform/web_console_auth/oauth2_flow.py` | EXISTS — OAuth2 flow with `_load_rbac_data()` | SIMPLIFY: Replace `_load_rbac_data()` DB queries with identity allowlist check, always use admin role |
| `libs/platform/web_console_auth/session_invalidation.py` | EXISTS — Session version helpers | SIMPLIFY: Remove `user_roles` references |
| `libs/platform/web_console_auth/session_validation.py` | EXISTS — Session version validation | SIMPLIFY: Remove `user_roles` references |
| `libs/platform/web_console_auth/step_up_callback.py` | EXISTS — Uses `validate_session_version()` | SIMPLIFY: Remove session_version dependency |
| `db/migrations/0006_create_rbac_tables.sql` | EXISTS — user_roles, strategies, user_strategy_access | NEW MIGRATION to drop tables |
| `db/migrations/0007_strategy_session_version_triggers.sql` | EXISTS — Session version triggers | NEW MIGRATION to drop triggers |
| `db/migrations/0011_create_api_keys.sql` | EXISTS — api_keys with FK to user_roles | NEW MIGRATION to drop FK constraint |
| `db/migrations/0030_add_researcher_role.sql` | EXISTS — Researcher role | Superseded by drop |
| `scripts/README.md` | EXISTS — Scripts documentation | MODIFY: Remove manage_roles.py entry |

---

## Tasks

### T19.1: Simplify Permission System + Delete User Management (Combined)

**Goal:** Make all permission/role check functions always return True, add identity restriction to all auth providers, ensure all providers populate full strategy lists, AND delete user management UI/service — all in the same atomic commit.

> **Why atomic:** Making permissions always-True without identity restriction creates a privilege-escalation window where any authenticated user (any OAuth2 `sub`, any valid mTLS cert CN) gets full admin access. Identity allowlist + strategy population + permission bypass + UI deletion must all land in one commit.
>
> **Session invalidation on deploy:** Pre-existing Redis sessions for non-allowlisted users are NOT re-checked against the identity allowlist during session validation (`session_store.py:204`, `middleware.py:107`). After T19.1 makes `has_permission()` always-true, these stale sessions would grant full admin. **Mitigation:** Flush ALL Redis session keys from BOTH session namespaces as part of T19.1 deployment: (1) web console session data: `ng_session:*` (actual session storage at `apps/web_console_ng/auth/session_store.py:110,253`) AND `ng_user_sessions:*` (reverse index at `session_store.py:111,534`), and (2) auth-service sessions: `session:*` (used by `libs/platform/web_console_auth/session_store.py:85,222` — the `RedisSessionStore.cleanup_all_sessions(prefix="session:")` method at line 426 can be used). The auth-service `OAuth2FlowHandler.refresh_tokens()` at `oauth2_flow.py:372` checks session version but not identity allowlist, so stale auth-service sessions must also be flushed. This forces all users to re-authenticate, at which point the identity allowlist blocks unauthorized users. **Operational note:** Any future change to identity allowlists (`OAUTH2_ALLOWED_SUBS` or `MTLS_ADMIN_CN_ALLOWLIST`) also requires a session purge, since refresh/validation paths (`oauth2_flow.py:433`, `middleware.py:107`, `session_store.py:204`) do not re-check the identity allowlist — they only validate the existing session. Add this to the ops runbook.
>
> **Note on direct role checks:** Multiple pages and components have inline role-string checks outside the permission helpers: `apps/web_console_ng/pages/position_management.py:111` (`if user_role == "viewer"`), `apps/web_console_ng/pages/position_management.py:588` (`if user_role != "admin"`), `apps/web_console_ng/pages/manual_order.py:63`, `apps/web_console_ng/pages/dashboard.py:1072`, `apps/web_console_ng/components/positions_grid.py:324`, `apps/web_console_ng/components/flatten_controls.py:368,535`, `apps/web_console_ng/components/orders_table.py:589`, `apps/web_console_ng/components/one_click_handler.py:177` (role gate `{"trader","admin"}`), `apps/web_console_ng/components/one_click_handler.py:357`, `apps/web_console_ng/components/cancel_all_dialog.py:87`, `apps/web_console_ng/components/hierarchical_orders.py:261`. These are NOT changed in T19.1 — they are resolved in T19.2 when middleware hardcodes `role: "admin"` in all sessions. Between T19.1 and T19.2, providers assign roles based on their own logic (OAuth2 defaults to non-admin at `apps/web_console_ng/auth/providers/oauth2.py:357`, mTLS parses CN at `apps/web_console_ng/auth/providers/mtls.py:193`), but middleware's `_override_role_from_db()` (still present in T19.1) queries `user_roles` and overrides to admin if the user has admin role in the DB. The allowed admin user must have `role='admin'` in `user_roles` for this to work correctly between T19.1 and T19.2. **Pre-requisite:** Verify the admin user exists in `user_roles` with `role='admin'` BEFORE deploying T19.1, since T19.1 deletes both provisioning mechanisms (`/admin/users` page and `manage_roles.py` script). Use `manage_roles.py` or direct SQL to ensure admin row exists before deployment.

**Permission Changes:**
- `has_permission()` → always returns `True`
- `has_dataset_permission()` → always returns `True` (20+ call sites across universe, data explorer, data quality, SQL explorer, data sync, data management services)
- `is_admin()` → always returns `True`
- `require_permission` decorator → effectively a no-op because it calls `has_permission()` which now always returns True. The decorator's function-signature preservation logic (`permissions.py:285`) is left intact to avoid breaking FastAPI's parameter introspection. Affects execution gateway routes using `@require_permission` (e.g. `apps/execution_gateway/routes/admin.py:187`, `apps/execution_gateway/routes/positions.py:173`, `apps/execution_gateway/routes/reconciliation.py:116`). Market-data routes use `api_auth(require_permission=...)` instead of the decorator — also covered by `has_permission()` becoming always-true (called internally by `libs/core/common/api_auth_dependency.py:780`)
- `get_authorized_strategies()` → always return the user's `strategies` field without role filtering. **IMPORTANT:** Auth providers must populate `strategies` in the session with ALL strategy IDs at login time. Currently: dev/basic providers read from `WEB_CONSOLE_DEV_STRATEGIES` env var (`apps/web_console_ng/config.py:263`); OAuth2 provider (`apps/web_console_ng/auth/providers/oauth2.py:233`) does NOT populate strategies in session. Implementation must ensure all production providers set `strategies` to the full list (query `strategies` table at login). Pages like `apps/web_console_ng/pages/attribution.py:72` and `apps/web_console_ng/pages/compare.py:89` will fail with empty strategy lists.
- Keep `ROLE_PERMISSIONS` as empty dict `{}` and `ROLE_DATASET_PERMISSIONS` as empty dict `{}` — `ROLE_PERMISSIONS` is re-exported by both `apps/web_console_ng/auth/permissions.py` and `libs/platform/web_console_auth/__init__.py`; `ROLE_DATASET_PERMISSIONS` is re-exported by `apps/web_console_ng/auth/permissions.py` only (NOT by `__init__.py`)
- Keep `Permission`, `Role`, `DatasetPermission` enums as-is (used as string constants across 170+ call sites)
- Keep function signatures unchanged (avoid touching 26+ page files)

**Identity Restriction (atomic with permission bypass):**
- Add identity allowlist to `apps/web_console_ng/auth/providers/oauth2.py` `handle_callback()` — currently accepts any `sub`
- Replace `_load_rbac_data()` DB check in `libs/platform/web_console_auth/oauth2_flow.py` with identity allowlist
- Add CN allowlist to `apps/web_console_ng/auth/providers/mtls.py` — currently accepts any proxy-validated cert CN (line ~54-68). Can delegate to `get_admin_cn_allowlist()` from `mtls_fallback.py` which already reads `MTLS_ADMIN_CN_ALLOWLIST` env var (`.env.example:142`)
- dev/basic providers: no identity restriction needed (dev hard-blocked by `apps/web_console_ng/config.py:235` when `DEBUG=False`; basic blocked at runtime by `ALLOW_DEV_BASIC_AUTH` at `apps/web_console_ng/auth/providers/basic.py:74`)

**Strategy Population (all providers must set full strategy list):**
- OAuth2 provider (`apps/web_console_ng/auth/providers/oauth2.py:233`): does NOT populate `strategies` — must query `strategies` table at login time. **Fallback:** if DB is unavailable (`db_pool=None`, which auth-service supports at `apps/auth_service/dependencies.py:62`), deny login (fail-closed) rather than allowing empty strategy list. Current `libs/platform/web_console_auth/oauth2_flow.py:293` returns empty RBAC when `db_pool=None` — this must be changed to deny
- mTLS provider (`apps/web_console_ng/auth/providers/mtls.py:209`): hardcodes `strategies: []` — must query `strategies` table at login time. **Fallback:** deny login if DB unavailable (fail-closed)
- dev/basic providers (`apps/web_console_ng/auth/providers/dev.py:25`, `apps/web_console_ng/auth/providers/basic.py:26`): use `config.DEV_STRATEGIES` env var with hard-fallback to `["alpha_baseline"]` if empty. These providers are intentionally env-only and DB-independent — no DB fallback needed. Set `WEB_CONSOLE_DEV_STRATEGIES` env var to include all strategy IDs for local dev/testing
- **Web console** pages hard-fail on empty strategy lists (e.g., `apps/web_console_ng/pages/attribution.py:72`). T19.1 provider changes fix these. **Execution gateway** routes also hard-fail (`apps/execution_gateway/routes/admin.py:275`, `apps/execution_gateway/routes/positions.py:199`) but these pull strategies from DB via `gateway_auth.py` (not session payload) — fixed in T19.2 when `gateway_auth.py` queries `strategies` table instead of `user_strategy_access`

**User Management Deletion:**
- Delete user management page (`/admin/users`)
- Delete user management components (user_table, user_activity, role_selector, strategy_grants)
- Delete user management service
- Delete CLI role management script
- Remove `/admin/users` from page registry (`apps/web_console_ng/pages/__init__.py`)
- Remove "Users" nav link from layout (`apps/web_console_ng/ui/layout.py`)
- Remove `/admin/users` from redirect allowlist (`apps/web_console_ng/auth/redirects.py`)
- Remove `manage_roles.py` entry from `scripts/README.md`

**Acceptance Criteria:**
- [ ] `has_permission()` returns True for any input
- [ ] `has_dataset_permission()` returns True for any input
- [ ] `is_admin()` returns True for any input
- [ ] `require_permission` decorator effectively bypassed (decorator mechanics preserved, `has_permission()` always returns True)
- [ ] `is_admin()` always returns True — verify admin-only PII exposure paths are acceptable for single-admin: `apps/execution_gateway/routes/orders.py:2349,2380` (order detail fields), `apps/web_console_ng/components/grid_export_toolbar.py:96` (export visibility), `apps/web_console_ng/components/order_audit_panel.py:145,214,254` (audit detail fields). Since only admin user can log in, all PII exposure is to the admin — this is the desired behavior
- [ ] `get_authorized_strategies()` returns full strategy list — NOTE: current implementation returns `[]` for unknown roles (`permissions.py:442`), and OAuth2 assigns `"trader"` (`providers/oauth2.py:357`) which is NOT in the `Role` enum, so `_extract_role()` returns None → empty list. Function must be simplified to always return `user.strategies` without role validation. Additionally, upstream providers must populate this list at login
- [ ] `ROLE_PERMISSIONS` and `ROLE_DATASET_PERMISSIONS` are still importable (empty dicts)
- [ ] OAuth2 provider rejects unknown `sub` values (identity allowlist)
- [ ] OAuth2 flow handler rejects unknown users (identity allowlist replaces `user_roles` lookup)
- [ ] mTLS provider rejects unknown cert CNs (identity allowlist)
- [ ] Production auth providers (OAuth2, mTLS) populate `strategies` with full strategy list from DB in session data; dev/basic providers use env config (`DEV_STRATEGIES`/`STRATEGY_ID`) as-is
- [ ] `/admin/users` route no longer exists
- [ ] No "Users" link in navigation
- [ ] `/admin/users` not in `ALLOWED_REDIRECT_PATHS`
- [ ] Unauthenticated requests still get 401/redirect (auth enforcement unchanged — only authorization bypassed)
- [ ] `user_management.py` service deleted
- [ ] `manage_roles.py` script deleted
- [ ] All 4 user management components deleted
- [ ] Deployment step: flush all Redis sessions from ALL namespaces (`ng_session:*` + `ng_user_sessions:*` + `session:*`) to force re-authentication through identity allowlist

**Files:**
- Modify: `libs/platform/web_console_auth/permissions.py`
- Modify: `apps/web_console_ng/auth/providers/oauth2.py` (identity allowlist + strategy population)
- Modify: `libs/platform/web_console_auth/oauth2_flow.py` (identity allowlist replaces `_load_rbac_data()`)
- Modify: `apps/web_console_ng/auth/providers/mtls.py` (CN allowlist + strategy population)
- Verify (no change needed): `apps/web_console_ng/auth/providers/dev.py` — already uses `config.DEV_STRATEGIES or ["alpha_baseline"]`
- Verify (no change needed): `apps/web_console_ng/auth/providers/basic.py` — already uses `config.DEV_STRATEGIES or ["alpha_baseline"]`
- Delete: `apps/web_console_ng/pages/admin_users.py`
- Delete: `apps/web_console_ng/components/user_table.py`
- Delete: `apps/web_console_ng/components/user_activity.py`
- Delete: `apps/web_console_ng/components/role_selector.py`
- Delete: `apps/web_console_ng/components/strategy_grants.py`
- Delete: `libs/web_console_services/user_management.py`
- Delete: `scripts/ops/manage_roles.py`
- Modify: `apps/web_console_ng/pages/__init__.py`
- Modify: `apps/web_console_ng/ui/layout.py`
- Modify: `apps/web_console_ng/auth/redirects.py`
- Modify: `scripts/README.md`
- Modify: `apps/web_console_ng/config.py` (add `OAUTH2_ALLOWED_SUBS` env var)
- Modify: `docs/RUNBOOKS/ops.md` (add session-purge guidance for allowlist changes)

**Estimated Effort:** 2 days

---

### T19.2: Simplify Auth Middleware & Remove DB Role Verification

**Goal:** Remove role cache lookups, inline DB role override, and all `verify_db_role()` call sites. Remove RBAC DB queries from gateway auth and session version modules. (Identity restriction and strategy population are now in T19.1.)

**Changes:**
- Remove `_override_role_from_db()` method from `AuthMiddleware` (inline DB query to `user_roles` + Redis `ng_role_cache` cache — NOTE: middleware does NOT call `verify_db_role()` or check `session_version` itself)
- Hardcode `role: "admin"` in session data after successful authentication — this also covers direct role string checks outside the permission helpers (e.g., `position_management.py:588` has `if user_role != "admin":` which becomes always-False with hardcoded admin)
- Delete `db_role.py` module (defines `verify_db_role()`)
- Remove `verify_db_role` imports and calls from `admin.py` (7 call sites: lines 208, 751, 875, 926, 966, 1120, 1163)
- Remove `verify_db_role` imports and calls from `tax_lots.py` (3 call sites: lines 61, 498, 559)
- Simplify `gateway_auth.py`: remove `user_roles` DB queries, remove `user_strategy_access` queries, return admin role and all strategies (query `strategies` table)
- Simplify `oauth2_flow.py` refresh path: at line 434, `refresh_tokens()` calls `validate_session_version()` (imported from `session_invalidation` at line 35), which queries `user_roles`. After T19.2 makes `validate_session_version()` always return True, this call becomes a harmless no-op — but the import and call site should be cleaned up to avoid confusion when `user_roles` is dropped in T19.3
- Simplify `session_invalidation.py`: remove `user_roles` UPDATE queries for session_version increment; `validate_session_version()` should always return True
- Simplify `session_validation.py`: remove `user_roles` queries from BOTH `validate_session_version()` (always return True) AND `invalidate_user_sessions()` (line ~45 updates `user_roles.session_version` — must be removed to prevent runtime break after T19.3 drops the table)
- Simplify `step_up_callback.py`: remove dependency on `validate_session_version()` (which queries `user_roles`)
- Keep session management (cookies, Redis sessions, encryption) intact

**Acceptance Criteria:**
- [ ] Middleware no longer has `_override_role_from_db()` method
- [ ] Middleware no longer queries Redis for `ng_role_cache`
- [ ] Authenticated user always gets `role: "admin"` in session
- [ ] Session store still works (create, validate, rotate, expire)
- [ ] `db_role.py` deleted
- [ ] `admin.py` has no `verify_db_role` imports or calls
- [ ] `tax_lots.py` has no `verify_db_role` imports or calls
- [ ] `gateway_auth.py` no longer queries `user_roles` or `user_strategy_access`; `get_user_strategies()` queries `strategies` table instead
- [ ] `session_invalidation.py` no longer references `user_roles`
- [ ] `session_validation.py`'s `validate_session_version()` always returns True
- [ ] `step_up_callback.py` works without `user_roles` table

**Files:**
- Modify: `apps/web_console_ng/auth/middleware.py`
- Modify: `apps/web_console_ng/pages/admin.py`
- Modify: `apps/web_console_ng/pages/tax_lots.py`
- Modify: `libs/platform/web_console_auth/gateway_auth.py`
- Modify: `libs/platform/web_console_auth/oauth2_flow.py` (clean up `validate_session_version()` call in refresh path at line 434 + import at line 35)
- Modify: `libs/platform/web_console_auth/session_invalidation.py`
- Modify: `libs/platform/web_console_auth/session_validation.py`
- Modify: `libs/platform/web_console_auth/step_up_callback.py`
- Delete: `apps/web_console_ng/auth/db_role.py`

**Estimated Effort:** 1 day

---

### T19.3: Database Migration — Drop RBAC Tables

**Goal:** Create a migration to drop the user management database tables, triggers, and FK constraints.

> **API Key FK Note:** Between T19.2 and T19.3 deployment, the `api_keys.user_id` FK still references `user_roles(user_id)`. Creating a new API key for a user not in `user_roles` will fail with FK violation (`admin.py:642`). Ensure the allowed admin user exists in `user_roles` before T19.3 drops the FK. Deploy T19.3 promptly after T19.2 to minimize this window.

**Migration order (critical — must be followed exactly):**
1. Drop triggers on `user_strategy_access` FIRST (they reference the table and will error if table is dropped first)
2. Drop function `bump_session_version_on_strategy_change()`
3. Drop FK constraint on `api_keys.user_id` referencing `user_roles(user_id)` — the `api_keys` table itself is KEPT but the FK is removed since `user_roles` is being dropped
4. Drop table `user_strategy_access` (has FK to both `user_roles` and `strategies`)
5. Drop table `user_roles`
6. Keep `strategies` table (used by `StrategyService`, `signal_service`)
7. Keep `audit_log` table (still needed for action logging)

**Acceptance Criteria:**
- [ ] Triggers dropped BEFORE tables — use `DO $$ BEGIN ... END $$` block that checks `pg_tables` for table existence before `DROP TRIGGER`, since `DROP TRIGGER IF EXISTS ... ON <table>` still errors if the table doesn't exist in PostgreSQL
- [ ] Function `bump_session_version_on_strategy_change()` dropped (`DROP FUNCTION IF EXISTS` — safe)
- [ ] `api_keys` FK to `user_roles` dropped (table preserved) — use `ALTER TABLE IF EXISTS api_keys DROP CONSTRAINT IF EXISTS fk_api_keys_user` (constraint name from `db/migrations/0011_create_api_keys.sql:29`)
- [ ] `user_strategy_access` table dropped (`DROP TABLE IF EXISTS` — safe)
- [ ] `user_roles` table dropped (`DROP TABLE IF EXISTS` — safe)
- [ ] `strategies` table preserved
- [ ] `audit_log` table preserved
- [ ] Migration is re-runnable: `IF EXISTS` for functions/tables/constraints, `DO $$` guard for triggers

**Files:**
- Create: `db/migrations/0031_drop_user_rbac_tables.sql`

**Estimated Effort:** 0.5 days

---

### T19.4: Update Tests & Clean Up Imports

**Goal:** Remove or update tests that reference deleted modules, and verify no broken imports remain.

**Changes:**
- Delete tests for `user_management.py` service: `tests/libs/web_console_services/test_user_management.py`, `tests/apps/web_console/services/test_user_management.py`
- Delete tests for `admin_users.py` page: `tests/apps/web_console_ng/pages/test_admin_users.py`
- Delete tests for components: `tests/apps/web_console_ng/components/test_role_selector.py`, `tests/apps/web_console_ng/components/test_user_table.py`
- Update admin page tests: remove `verify_db_role` stubs (`test_admin_api_keys.py`, `test_admin_coverage.py`, `test_admin.py`)
- Update middleware tests: remove role override tests (`test_middleware_role_override.py`)
- Update shared permission tests: `tests/libs/platform/web_console_auth/test_permissions_unit.py` (currently asserts deny paths — must assert always-True), `tests/libs/platform/web_console_auth/test_p4t7_permissions.py` (asserts ROLE_PERMISSIONS contents — must update for empty dict)
- Update execution gateway tests: `tests/apps/execution_gateway/api/test_dependencies.py` (ensure_permission denial tests must be updated)
- Update gateway_auth tests: `tests/libs/platform/web_console_auth/test_gateway_auth.py` (session-version/role tests)
- Update OAuth2 flow tests: `tests/libs/platform/web_console_auth/test_oauth2_flow.py` (RBAC-loading tests)
- Update step-up tests: `tests/libs/platform/web_console_auth/test_step_up_callback.py` (session-version tests)
- Update web console permission tests: `tests/apps/web_console_ng/auth/test_permissions.py`
- Update session tests: `test_session_store_t16.py`, `test_session_validation.py`, `test_session_invalidation.py`
- Update layout/navigation tests: remove `/admin/users` references (`test_layout.py:322`, `test_navigation.py:343`)
- Update redirect tests: `test_redirects.py` (allowlist change)
- Note: No dedicated `manage_roles.py` tests exist (confirmed via grep)
- Grep for stale imports across codebase

**Acceptance Criteria:**
- [ ] No test references deleted modules
- [ ] Permission tests verify new always-True behavior (including `has_dataset_permission`)
- [ ] Shared permission tests updated (deny-path assertions removed)
- [ ] Execution gateway tests updated (permission denial tests adjusted)
- [ ] Gateway auth tests updated (no session-version/user_roles assertions)
- [ ] OAuth2 flow tests updated (no RBAC-loading assertions)
- [ ] Step-up callback tests updated
- [ ] Admin page tests no longer stub `verify_db_role`
- [ ] Layout/navigation tests updated (no `/admin/users`)
- [ ] Redirect tests updated (no `/admin/users` in allowlist)
- [ ] `make test` passes
- [ ] `make lint` passes
- [ ] No stale imports found via grep

**Files:**
- Delete: `tests/libs/web_console_services/test_user_management.py`
- Delete: `tests/apps/web_console/services/test_user_management.py`
- Delete: `tests/apps/web_console_ng/pages/test_admin_users.py`
- Delete: `tests/apps/web_console_ng/components/test_role_selector.py`
- Delete: `tests/apps/web_console_ng/components/test_user_table.py`
- Modify: `tests/apps/web_console_ng/pages/test_admin_api_keys.py`
- Modify: `tests/apps/web_console_ng/pages/test_admin_coverage.py`
- Modify: `tests/apps/web_console_ng/pages/test_admin.py`
- Modify: `tests/apps/web_console_ng/auth/test_middleware_role_override.py`
- Modify: `tests/apps/web_console_ng/auth/test_permissions.py`
- Modify: `tests/apps/web_console_ng/auth/test_session_store_t16.py`
- Modify: `tests/libs/platform/web_console_auth/test_session_validation.py`
- Modify: `tests/libs/platform/web_console_auth/test_session_invalidation.py`
- Modify: `tests/libs/platform/web_console_auth/test_permissions_unit.py`
- Modify: `tests/libs/platform/web_console_auth/test_p4t7_permissions.py`
- Modify: `tests/apps/execution_gateway/api/test_dependencies.py`
- Modify: `tests/libs/platform/web_console_auth/test_gateway_auth.py`
- Modify: `tests/libs/platform/web_console_auth/test_oauth2_flow.py`
- Modify: `tests/libs/platform/web_console_auth/test_step_up_callback.py`
- Modify: `tests/apps/web_console_ng/test_layout.py`
- Modify: `tests/apps/web_console_ng/test_navigation.py`
- Modify: `tests/apps/web_console_ng/auth/test_redirects.py`
- Modify: `tests/libs/platform/web_console_auth/test_permissions.py` (deny-path assertions)
- Modify: `tests/apps/web_console_ng/pages/test_models.py` (permission denial tests)
- Modify: `tests/apps/web_console_ng/pages/test_strategies.py` (permission denial tests)
- Modify: `tests/libs/web_console_services/test_strategy_service.py` (admin-only deny tests at lines ~78, ~151)
- Modify: `tests/libs/web_console_services/test_model_registry_browser_service.py` (admin-only deny test at line ~176)
- Modify: `tests/libs/web_console_services/test_data_sync_service.py` (RBAC deny tests at lines ~70, ~91, ~120)
- Modify: `tests/libs/web_console_services/test_universe_service.py` (RBAC deny tests at line ~98)
- Modify: `tests/apps/web_console_ng/auth/providers/test_oauth2.py` (identity allowlist + strategy population changes)
- Modify: `tests/apps/web_console_ng/auth/providers/test_mtls.py` (CN allowlist + strategy population changes)
- Verify (no change expected): `tests/apps/web_console_ng/auth/providers/test_basic.py` — dev/basic providers unchanged in T19.1
- Verify (no change expected): `tests/apps/web_console_ng/auth/providers/test_dev.py` — dev/basic providers unchanged in T19.1
- Modify: `tests/libs/core/common/test_api_auth_dependency.py` (ensure_permission tests)
- Modify: `tests/apps/execution_gateway/api/test_manual_controls.py` (permission tests)
- Modify: `tests/apps/web_console/services/test_data_sync_service.py` (RBAC denial tests)
- Modify: `tests/apps/web_console/services/test_data_sync_rate_limit.py` (ROLE_DATASET_PERMISSIONS refs)
- Modify: `tests/apps/web_console/services/test_alert_acknowledgment.py` (ROLE_DATASET_PERMISSIONS refs)
- Modify: `tests/apps/web_console/services/test_data_explorer_service.py` (ROLE_DATASET_PERMISSIONS refs)
- Modify: `tests/libs/web_console_services/test_data_source_status_service.py` (has_dataset_permission mocks)
- Modify: `tests/libs/web_console_services/test_data_quality_service.py` (has_dataset_permission mocks)
- Modify: `tests/apps/web_console/services/test_notebook_launcher_service.py` (permission deny tests)
- Modify: `tests/apps/web_console/services/test_scheduled_reports_service.py` (permission deny tests)
- Modify: `tests/apps/web_console/services/test_tax_lot_service.py` (permission deny tests)
- Modify: `tests/libs/web_console_services/test_alert_service.py` (permission deny tests)
- Modify: `tests/libs/web_console_services/test_sql_explorer_service.py` (permission deny tests)
- Note: 100+ `PermissionError` expectations across test suite must be updated or removed. **Implementation must grep** for `PermissionError`, `is_admin`, `has_permission`, `has_dataset_permission`, and `require_permission` assertions across all tests to catch any unlisted files

**Estimated Effort:** 2.5 days

---

## Dependencies

```
T19.1 (permissions + identity restriction + strategy population + UI deletion, atomic) → T19.2 (middleware + DB role cleanup)
T19.1 + T19.2 → T19.3 (DB migration, after all code stops referencing tables)
T19.1 + T19.2 + T19.3 → T19.4 (tests updated last)
```

T19.1 is the critical atomic commit: permission bypass + identity allowlist + strategy population + UI deletion all land together to prevent privilege escalation. T19.2 cleans up middleware and all remaining DB-referencing code. T19.3 (DB migration) goes after all code changes. T19.4 (tests) is last.

## Testing Strategy

**Unit Tests:**
- `has_permission()` returns True for all role/permission combos
- `has_dataset_permission()` returns True for any input
- `is_admin()` returns True for any input
- `require_permission` decorator passes through without checking
- `get_authorized_strategies()` returns all strategies
- Middleware attaches `role: "admin"` to session
- `validate_session_version()` always returns True

**Integration Tests:**
- All pages load without permission errors
- Dataset-filtered pages show all datasets
- Admin dashboard works (API Keys, Config, Recon, Audit tabs)
- Login → session → page access flow works end-to-end
- Execution gateway routes accept any authenticated request

## Library Change Requests

- None — all changes are within existing module boundaries

## Definition of Done

- [ ] ADR created for this architectural change (supersedes ADR-0039)
- [ ] Existing ADRs referencing RBAC updated or marked superseded (e.g., `docs/ADRs/ADR-0039-db-role-authority-override.md`, `docs/ADRs/ADR-024-analytics-security.md`)
- [ ] `libs/web_console_services/__init__.py` docstring updated (references RBAC)
- [ ] All acceptance criteria met
- [ ] `/review` approved by Gemini + Codex
- [ ] All tests pass, coverage >= ratchet
- [ ] `make ci-local` passes
- [ ] PR created with zen trailers
- [ ] No broken imports or references to deleted modules
