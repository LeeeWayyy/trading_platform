---
id: P6T16
title: "Professional Trading Terminal - Admin Pages"
phase: P6
task: T16
priority: P2
owner: "@development-team"
state: TASK
created: 2026-01-13
dependencies: [P5]
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [docs/TASKS/P6_PLANNING.md]
features: [T16.1-T16.3]
---

# P6T16: Admin Pages

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** TASK (Not Started)
**Priority:** P2 (Backend Integration)
**Owner:** @development-team
**Created:** 2026-01-13
**Track:** Track 16 of 18
**Dependency:** P5 complete

---

## Objective

Complete missing admin pages: tax lot management, user management with RBAC, and API key revoke/rotate.

**Success looks like:**
- Tax lots page with cost basis, wash sale detection, and tax-loss harvesting suggestions
- User management with RBAC (role assignment, strategy grants, session invalidation)
- API key revoke/rotate functionality with confirmation dialogs
- All admin pages properly role-gated with audit logging via `AuditLogger`

---

## Security Requirements (All T16 Tasks)

**RBAC Model (existing `libs/platform/web_console_auth/permissions.py`):**
- `Role.VIEWER`: Read-only (VIEW_POSITIONS, VIEW_PNL, VIEW_TRADES, VIEW_TAX_LOTS, VIEW_DATA_SYNC, VIEW_DATA_QUALITY, VIEW_CIRCUIT_BREAKER, VIEW_ALERTS, VIEW_REPORTS)
- `Role.RESEARCHER`: Independent research set — NOT a VIEWER superset (VIEW_ALPHA_SIGNALS, VIEW_FACTOR_ANALYTICS, LAUNCH_NOTEBOOKS, VIEW_REPORTS, VIEW_TAX_LOTS, VIEW_TAX_REPORTS, VIEW_FEATURES, VIEW_SHADOW_RESULTS, QUERY_DATA, VIEW_UNIVERSES, VIEW_STRATEGY_EXPOSURE). NOTE: lacks VIEWER perms like VIEW_PNL, VIEW_TRADES, VIEW_POSITIONS.
- `Role.OPERATOR`: Most operational permissions (MANAGE_STRATEGIES, SUBMIT_ORDER, EXPORT_DATA — but NOT MANAGE_TAX_LOTS)
- `Role.ADMIN`: All permissions (`set(Permission)`) including MANAGE_USERS, MANAGE_API_KEYS, MANAGE_TAX_LOTS

**CRITICAL — RESEARCHER DB constraint gap:** The `user_roles` table (`db/migrations/0006_create_rbac_tables.sql`) has `CHECK (role IN ('viewer', 'operator', 'admin'))` — no `'researcher'`. `Role.RESEARCHER` exists in the Python enum but will fail at DB level. **Before T16.2 implementation, add a migration to ALTER the CHECK constraint to include `'researcher'`.** Until then, the role dropdown must NOT offer RESEARCHER.

**AUTH PROVIDER ROLE GAP:** OAuth2 (`apps/web_console_ng/auth/providers/oauth2.py:361`) and mTLS (`apps/web_console_ng/auth/providers/mtls.py:199`) providers can emit role `"trader"`, which is NOT in the `Role` enum (`viewer`, `researcher`, `operator`, `admin`). Additionally, these providers only map `admin` and `trader` explicitly — any other IdP/cert role (including `operator` and `researcher`) falls back to `viewer` by default. Additionally, `basic` (`apps/web_console_ng/auth/providers/basic.py:24`) and `dev` (`apps/web_console_ng/auth/providers/dev.py:23`) providers use `config.DEV_ROLE` which can be set to any arbitrary string via environment variable — including non-canonical roles. Unknown roles are default-denied by `has_permission()`. The T16.2 role dropdown should only offer the 4 canonical `Role` enum values. NOTE: `"trader"` only appears in session context (from auth providers), NOT in the `user_roles` table (which has a CHECK constraint limiting to `viewer|operator|admin` + `researcher` after **planned** migration 0028 — see T16.2 Implementation Plan step 0 for the migration definition). If the admin UI encounters session users with non-canonical roles, display the raw role string and offer re-assignment to a canonical role.

**RBAC RUNTIME NOTE:** `has_permission()` is a pure helper function (`libs/platform/web_console_auth/permissions.py:237`) that checks the role of whatever subject is passed to it — it does NOT itself access the session. In the web console, the subject passed is typically `get_current_user()` which reads from `app.storage.user["user"]`, whose `role` field is set at login time by the auth provider. `change_user_role()` updates the `user_roles` DB table but does NOT update the active session. **CRITICAL: Auth providers (OAuth2, mTLS, basic, dev) derive the session role from their own identity source (IdP claims, certificate OU, env vars) — they do NOT read the `user_roles` table.** Furthermore, the web-console → execution-gateway call path (`AsyncTradingClient` at `apps/web_console_ng/core/client.py:124`) forwards the session-derived role as `X-User-Role` header; the execution gateway middleware (`apps/execution_gateway/middleware.py:222`) uses this header directly for `@require_permission` routes (e.g., `/api/v1/market_prices` at `apps/execution_gateway/routes/positions.py:469`). **NOTE:** Not all gateway routes use header-role auth — some routes (e.g., `/api/v1/positions` at `apps/execution_gateway/routes/positions.py:110`) use `api_auth()` which handles multiple auth methods: first internal-token S2S (`X-Internal-*` headers), then JWT/Bearer tokens. These are **two independent auth layers**: (1) the ASGI middleware `populate_user_from_headers` (`apps/execution_gateway/middleware.py:222`) runs **before** any route handler and populates `request.state.user` from `X-User-*` headers — this is what `@require_permission` checks; (2) `api_auth()` runs as a FastAPI dependency within the route handler and authenticates via `X-Internal-*` or Bearer JWT only — it does NOT consume `request.state.user`. `AsyncTradingClient` sends `X-User-*` headers (not JWT/Bearer), so on `api_auth`-gated routes, the middleware still populates `request.state.user` (enabling `@require_permission`), but `api_auth()` itself will NOT find valid credentials. In `API_AUTH_MODE=log_only` (docker-compose dev default via `${API_AUTH_MODE:-log_only}`; note: the code-level default in `libs/core/common/api_auth_dependency.py:111` is `enforce`), `api_auth` logs the auth failure but does not block, so web-console calls succeed. In `API_AUTH_MODE=enforce` (code default, staging/production), web-console calls to `api_auth`-gated routes would fail with 401 unless proper S2S token or JWT auth is added to `AsyncTradingClient`. The web-console does call `/api/v1/positions` via `AsyncTradingClient.fetch_positions()`, so this path IS relevant and may need S2S token support in production. **CONFIG PREREQUISITE:** `populate_user_from_headers` (`apps/execution_gateway/middleware.py:229`) validates `X-User-Signature` when `INTERNAL_TOKEN_REQUIRED=true` (staging/production default); `AsyncTradingClient` only adds the signature when `INTERNAL_TOKEN_SECRET` env var is set (`apps/web_console_ng/core/client.py:130`). If `INTERNAL_TOKEN_SECRET` is not configured in the web-console env, signed header validation fails and `populate_user_from_headers` returns 401 even for `@require_permission`-gated routes. Ensure `INTERNAL_TOKEN_SECRET` is set in all environments where `INTERNAL_TOKEN_REQUIRED=true`. In docker-compose dev (`docker-compose.yml:202`), `INTERNAL_TOKEN_REQUIRED` defaults to `false`, so unsigned headers are trusted — this is the current dev/test configuration. For the T16.1 tax lots page, the relevant endpoint is `/api/v1/market_prices` which uses `@require_permission` (header-role path). In both cases, `change_user_role()` DB updates do NOT affect web-console → gateway calls via `AsyncTradingClient` because the forwarded `X-User-Role` header carries the session-derived role, not the DB role. T16.2 must implement option (a): add a post-login middleware hook that overrides the provider-derived role with the DB role from `user_roles` (makes admin role changes authoritative for web-console session and forwarded `X-User-Role` header on `@require_permission`-gated gateway routes like `/api/v1/market_prices`). **LIMITATION:** This does NOT fix `api_auth()`-gated routes like `/api/v1/positions` — those authenticate via `X-Internal-*` S2S tokens or Bearer JWT, not `X-User-Role`. The DB-role override updates the session role (and thus the forwarded `X-User-Role` header), but `api_auth()` ignores that header entirely. Web-console calls to `api_auth`-gated routes currently rely on `API_AUTH_MODE=log_only`; fixing this requires adding S2S token support to `AsyncTradingClient`, which is out of scope for T16. Option (b) — scoping the UI to display-only — is NOT acceptable because T16.2 requires active role changes with safety guards (self-edit prevention, last-admin protection). A display-only fallback would contradict these requirements.

**Audit Infrastructure (existing `libs/platform/web_console_auth/audit_log.py`):**
- Use `AuditLogger.log_admin_change(*, admin_user_id, action, target_user_id, details)` for admin actions
- Use `AuditLogger.log_action(*, user_id, action, resource_type, resource_id, outcome, details)` for denied attempts
- Prometheus metrics: `audit_log_events_total` (Python variable: `audit_events_total`), `admin_action_total`
- NOTE: `AuditLogger` writes raw `json.dumps(details)` — no automatic sanitization. Sanitization is display-side only (in `apps/web_console_ng/pages/admin.py` audit viewer). Callers must avoid passing sensitive data (passwords, tokens) in `details` dict.
- Retention: 90-day cleanup via `cleanup_old_events()`

**Permission Enforcement Pattern:**
- Page-level: `@requires_auth` decorator + `has_permission()` check in page body
- Service-level: `TaxLotService._require_permission()` raises `PermissionError`
- Database-level: `WHERE user_id = %s` scopes queries (bypass with `all_users=True` flag)

---

## Existing Backend Infrastructure

### Services (Ready for Integration)

| Service | Location | Key API | Status |
|---------|----------|---------|--------|
| Tax Lot Service | `libs/web_console_services/tax_lot_service.py` | `TaxLotService.list_lots()`, `.get_lot()`, `.create_lot()`, `.update_lot()`, `.close_lot()` | Complete |
| User Management | `libs/web_console_services/user_management.py` | `list_users()`, `change_user_role()`, `grant_strategy()`, `revoke_strategy()`, `bulk_change_roles()` | Complete |
| API Key Admin | `libs/platform/admin/api_keys.py` | `generate_api_key()`, `hash_api_key()`, `validate_api_key()`, `is_key_revoked()` | Complete |
| Wash Sale Detector | `libs/platform/tax/wash_sale_detector.py` | `WashSaleDetector`, `WashSaleMatch`, `WashSaleAdjustment` | Complete |
| Tax Loss Harvesting | `libs/platform/tax/tax_loss_harvesting.py` | `TaxLossHarvester` | Complete |
| Form 8949 | `libs/platform/tax/form_8949.py` | `Form8949Exporter` | Complete |
| Tax Export | `libs/platform/tax/export.py` | `TaxReportRow` | Complete |
| Audit Logger | `libs/platform/web_console_auth/audit_log.py` | `AuditLogger.log_admin_change()`, `.log_action()`, `.log_export()` | Complete |

### Database Schema (Ready)

| Table | Migration | Purpose |
|-------|-----------|---------|
| `tax_lots` | `db/migrations/0019_create_tax_lots.sql` | Main lot tracking (user_id, symbol, quantity, cost_per_share, total_cost, remaining_quantity, acquisition_type, closed_at) |
| `tax_lot_dispositions` | Same migration | Sell/disposition tracking (quantity, cost_basis, proceeds_per_share, total_proceeds, realized_gain_loss, holding_period, wash_sale_disallowed, idempotency_key) |
| `tax_wash_sale_adjustments` | Same migration | Wash sale matching (disposition_id, replacement_lot_id, disallowed_loss, matching_shares, holding_period_adjustment_days) |
| `tax_user_settings` | Same migration | User tax preferences (cost_basis_method VARCHAR(20)) |
| `api_keys` | `db/migrations/0011_create_api_keys.sql` | Key storage (key_hash, key_salt, key_prefix, scopes JSONB, revoked_at, last_used_at, expires_at) |
| `user_roles` | `db/migrations/0006_create_rbac_tables.sql` (created); `db/migrations/0011_create_api_keys.sql` adds `last_active_ip`, `last_active_at` columns (note: there are two `0011_*` files — `0011_create_api_keys.sql` and `0011_create_alert_tables.sql`) | Role assignments (user_id, role with CHECK constraint, timestamps) |

### Permissions (Already Defined)

| Permission | Used By | Roles Granted |
|-----------|---------|---------------|
| `VIEW_TAX_LOTS` | T16.1 | VIEWER, RESEARCHER, OPERATOR, ADMIN |
| `MANAGE_TAX_LOTS` | T16.1 | ADMIN only |
| `MANAGE_TAX_SETTINGS` | T16.1 | ADMIN only |
| `VIEW_TAX_REPORTS` | T16.1 (Form 8949 export view) | RESEARCHER, ADMIN |
| `EXPORT_DATA` | T16.1 | OPERATOR, ADMIN |
| `MANAGE_USERS` | T16.2 | ADMIN only |
| `MANAGE_API_KEYS` | T16.3 | ADMIN only |
| `VIEW_AUDIT` | T16.2 | OPERATOR, ADMIN |

---

## Tasks (3 total)

### T16.1: Tax Lot Management - MEDIUM PRIORITY

**Goal:** Create NiceGUI page for tax lot viewing, wash sale detection, and tax-loss harvesting.

**Current State:**
- Backend: `libs/web_console_services/tax_lot_service.py` — `TaxLotService` class with full CRUD (list, get, create, update, close)
- Tax utilities: `libs/platform/tax/` — `WashSaleDetector` (61-day window, same-symbol matching), `TaxLossHarvester` (in `tax_loss_harvesting.py`), `Form8949Exporter` (in `form_8949.py`), `TaxReportRow` (in `export.py`)
- DB schema: `db/migrations/0019_create_tax_lots.sql` — `tax_lots`, `tax_lot_dispositions`, `tax_wash_sale_adjustments`, `tax_user_settings` tables
- Frontend: No NiceGUI page exists

**Service Integration Details:**
- `TaxLotService.__init__(db_pool: Any, user: dict[str, Any])` — pass `get_db_pool()` (async pool from `apps/web_console_ng/core/database.py`) and `get_current_user()`
- `TaxLotService.list_lots(user_id=None, *, all_users=False, limit=500)` → `list[TaxLot]` (frozen dataclass: lot_id, symbol, quantity, cost_basis, acquisition_date, strategy_id, status). NOTE: `strategy_id` is NOT persisted in the DB schema — it's a UI-only field carried in the dataclass for display purposes (populated via `strategy_override` param or defaults to `None`)
- `TaxLotService.close_lot(lot_id, *, user_id=None, all_users=False)` → `TaxLot | None` — sets `remaining_quantity = 0` and `closed_at = NOW()`
- Permission checks: `VIEW_TAX_LOTS` to view, `MANAGE_TAX_LOTS` to modify (ADMIN only), `MANAGE_TAX_SETTINGS` to change cost basis method (ADMIN only)
- `WashSaleDetector.detect_wash_sales(user_id, symbol, sale_date, loss_amount, shares_sold)` → `list[WashSaleMatch]` — 61-day window; same-symbol only; open replacement lots only. NOTE: This is a sale-event-driven API (called when a sale occurs), NOT a general "fetch wash sale warnings" endpoint. For displaying existing wash sale flags on lots, query `tax_wash_sale_adjustments` table directly.
- `TaxLossHarvester.find_opportunities(user_id, current_prices, min_loss_threshold=Decimal("100"))` → `HarvestingRecommendation` — requires `current_prices: dict[str, Decimal]` from market data service or cache. **PRICE 403 GAP:** The `/api/v1/market_prices` endpoint requires `VIEW_PNL` permission (`apps/execution_gateway/routes/positions.py:470`), but RESEARCHER lacks `VIEW_PNL` (only VIEWER, OPERATOR, ADMIN have it). Additionally, the endpoint also returns 403 when strategy scope is empty ("No strategy access" at `apps/execution_gateway/routes/positions.py:489`), which can affect non-RESEARCHER roles too (mTLS provider sets `strategies: []` explicitly; OAuth2 provider omits the `strategies` key entirely from `user_data`, which `get_authorized_strategies()` treats as `[]`). Non-admin roles viewing `/tax-lots` may get a 403 on price fetch (admins bypass the empty-strategy check via `VIEW_ALL_STRATEGIES` at `libs/platform/web_console_auth/permissions.py:182`). The page must handle this gracefully: `_fetch_current_prices` catches `httpx.HTTPStatusError` (specific 403 handling + fallthrough for 401/5xx) and `httpx.HTTPError` (base class covering network errors, timeouts, DNS failures), returns an empty dict on any failure, and the caller displays "N/A" for current prices and hides the harvesting panel. Alternatively, T16.1 could use a different price source that doesn't require `VIEW_PNL`, or a future migration could grant `VIEW_PNL` to RESEARCHER. **DEV MODE NOTE:** In `DEBUG` mode, `AsyncTradingClient` falls back to `DEV_STRATEGIES` (`apps/web_console_ng/config.py:263`) when the session `strategies` is `None`, so the empty-strategy 403 may not reproduce locally. **TESTING CAVEAT:** Setting `WEB_CONSOLE_DEV_STRATEGIES=""` alone is NOT sufficient to reproduce the empty-strategy 403 — both `dev` (`apps/web_console_ng/auth/providers/dev.py:25`) and `basic` (`apps/web_console_ng/auth/providers/basic.py:26`) providers force a non-empty fallback (`["alpha_baseline"]`) when strategies are empty, and `apps/web_console_ng/config.py:263` repopulates from `STRATEGY_ID` env var. To actually test 403 handling: (a) use a non-dev auth provider (e.g., OAuth2 with an IdP that doesn't return strategies), or (b) mock `AsyncTradingClient.fetch_market_prices` to raise `httpx.HTTPStatusError` with status 403 in unit tests.
- `tax_user_settings` table stores per-user `cost_basis_method` (FIFO, LIFO, specific_id). NOTE: DB schema has `VARCHAR(20)` without CHECK constraint — validate allowed values in application code (e.g., Pydantic Literal or explicit set check). **SERVICE GAP:** `TaxLotService` has NO existing method to read/write `tax_user_settings`. T16.1 must add `get_cost_basis_method(user_id)` and `set_cost_basis_method(user_id, method)` methods (with `MANAGE_TAX_SETTINGS` permission check) to the service layer.

**DB Pool Pattern:** The existing `apps/web_console_ng/pages/admin.py` uses `get_db_pool()` from `apps/web_console_ng/core/database.py` (async pool), NOT `get_sync_db_pool()`. New pages should follow this same pattern for consistency.

**Features:**
1. **Tax Lots Table** — AG Grid displaying open lots: symbol, quantity, cost_basis, acquisition_date, strategy_id, status, holding_period (short/long-term). NOTE: `TaxLot` dataclass has: lot_id, symbol, quantity, cost_basis, acquisition_date, strategy_id, status. DB has `cost_per_share`, `remaining_quantity` but these are NOT mapped to the dataclass — either extend `TaxLot` or compute derived columns (e.g., `cost_per_share = cost_basis / quantity`) at the UI layer. **OPEN LOTS FILTER:** `list_lots()` currently returns ALL lots (no `remaining_quantity > 0` or `closed_at IS NULL` filter). The page layer must either: (a) filter in Python after fetch (`[lot for lot in lots if lot.status == "open"]`), or (b) add an `open_only: bool = False` parameter to `list_lots()` that appends `WHERE remaining_quantity > 0 AND closed_at IS NULL` to the SQL query. Option (b) is preferred for performance — implement as part of T16.1.
2. **Wash Sale Warnings** — Visual indicators (red badge/icon) on lots with active wash sale adjustments; tooltip showing disallowed_loss and matching details. **"Active" definition:** `tax_wash_sale_adjustments` has no active/resolved flag — ALL rows in the table are considered active adjustments. An adjustment is "active" if its `replacement_lot_id` references the current lot. Query: `SELECT * FROM tax_wash_sale_adjustments WHERE replacement_lot_id = ANY(%s::uuid[])` passing displayed lot IDs as a list parameter (psycopg3 native list binding). **NOTE:** `TaxLot.lot_id` is `str` but `replacement_lot_id` is `UUID` in the DB schema — pass lot IDs as `list[str]` and cast to `uuid[]` in the SQL, or convert to `uuid.UUID` objects in Python before binding.
3. **Tax-Loss Harvesting Panel** — Sidebar/section showing `TaxLossHarvester` suggestions ranked by potential savings
4. **Lot Selection for Sales** — Cost basis method selector (FIFO, LIFO, Specific ID) persisted to `tax_user_settings`
5. **Summary Metrics** — Header cards: total unrealized gain/loss, short-term vs long-term split, wash sale impact total
6. **Export** — Two distinct operations: (a) CSV/Form 8949 **download** via `Form8949Exporter` requires `EXPORT_DATA` (OPERATOR, ADMIN); (b) Form 8949 on-screen **preview/view** requires `VIEW_TAX_REPORTS` (RESEARCHER, ADMIN). OPERATOR can download but cannot preview; RESEARCHER can preview but cannot download.
7. **All Users Toggle** — Admins with `MANAGE_TAX_LOTS` see a "Show All Users" toggle that calls `service.list_lots(all_users=True)`
8. **Wash Sale Limitation Note** — Info tooltip on wash sale panel explaining detection is scoped to open replacement lots only (per `WashSaleDetector` KNOWN LIMITATIONS)

**Implementation Plan:**

**NOTE on step ordering:** Steps that reference the same file are additive. Steps marked "(content for NEW FILE created at step N)" describe functions/helpers that are included when the file is first created at that step. Implement them together — the step numbers indicate logical ordering of content, not separate file operations.

| Step | File | Description |
|------|------|-------------|
| 0a | `libs/web_console_services/tax_lot_service.py` | Add `get_cost_basis_method(user_id) -> str` and `set_cost_basis_method(user_id, method) -> None` methods (with `MANAGE_TAX_SETTINGS` permission check). Add `open_only: bool = False` parameter to `list_lots()` that appends `WHERE remaining_quantity > 0 AND closed_at IS NULL` when True. |
| 1 | `apps/web_console_ng/components/tax_lot_table.py` (**NEW FILE**) | `render_tax_lot_table(lots, *, wash_sale_lot_ids=None, on_close=None, on_select=None)` — AG Grid with wash sale flag column. NOTE: Wash sale data passed IN from page layer (queried via service), not fetched by component. |
| 2 | `apps/web_console_ng/pages/tax_lots.py` (content for NEW FILE created at step 4) | Add helper to query `tax_wash_sale_adjustments` for lot IDs with active adjustments (all rows are "active" — no resolved flag in schema; query by `replacement_lot_id = ANY(%s::uuid[])` with list param for displayed lots — cast needed since `TaxLot.lot_id` is `str` but `replacement_lot_id` is `UUID`), pass to component |
| 3 | `apps/web_console_ng/components/tax_harvesting.py` (**NEW FILE**) | `render_harvesting_suggestions(suggestions)` — Ranked cards with estimated tax savings |
| 4 | `apps/web_console_ng/pages/tax_lots.py` (**NEW FILE**) | Page at `/tax-lots`, orchestrates service calls and component rendering |
| 5 | `apps/web_console_ng/pages/__init__.py` | Add `tax_lots` to the grouped import block |
| 6 | `apps/web_console_ng/ui/layout.py` | Add nav entry `("Tax Lots", "/tax-lots", "receipt_long", None)` + inline permission check |
| 6b | `apps/web_console_ng/auth/redirects.py` | Add `/tax-lots` to `ALLOWED_REDIRECT_PATHS` (see Navigation Updates section) |
| 7 | `tests/apps/web_console_ng/pages/test_tax_lots.py` (**NEW FILE**) | Page-level tests: permission checks, data rendering, wash sale display |
| 7b | `apps/web_console_ng/pages/tax_lots.py` (addition to file created at step 4) | Add Form 8949 export handler: query `tax_lot_dispositions JOIN tax_lots ON tax_lot_dispositions.lot_id = tax_lots.id` for the user (the join is required because `TaxReportRow` needs `symbol` and `acquired_at` (DB column name — mapped to `acquired_date` in `TaxReportRow`) from `tax_lots`, which are not in `tax_lot_dispositions`), map joined rows to `TaxReportRow` dataclass (from `libs/platform/tax/export.py`), pass to `Form8949Exporter` (from `libs/platform/tax/form_8949.py`), download CSV. Requires `EXPORT_DATA` permission (OPERATOR+). **TYPE CONVERSION:** DB columns `tax_lots.acquired_at` and `tax_lot_dispositions.disposed_at` are `TIMESTAMPTZ`, but `TaxReportRow.acquired_date` and `TaxReportRow.disposed_date` are `datetime.date` (see `libs/platform/tax/export.py:44-45`). The mapping must convert via `.date()` (e.g., `row["acquired_at"].date()`) — do not pass raw `datetime` objects. |
| 7c | `apps/web_console_ng/pages/tax_lots.py` (addition to file created at step 4) | Add Form 8949 on-screen **preview** panel: reuses the same `tax_lot_dispositions JOIN tax_lots` query and `TaxReportRow` mapping from step 7b, but renders the data in a `ui.table` instead of triggering a download. Requires `VIEW_TAX_REPORTS` permission (RESEARCHER, ADMIN). OPERATOR can download (step 7b) but not preview; RESEARCHER can preview but not download. Guard both actions with inline `has_permission()` checks. |
| 8 | `tests/apps/web_console_ng/components/test_tax_lot_table.py` (**NEW FILE**) | Component unit tests |
| 9 | `tests/apps/web_console_ng/test_navigation.py` | Update `test_navigation_item_structure()` to include Tax Lots nav entry |
| 9b | `tests/apps/web_console_ng/test_layout.py` | Update `test_nav_items_include_expected_routes()` (line 304) to add `/tax-lots` to `expected_paths` list — this test hard-codes expected routes and will fail without the update |

**Page Pattern (follows `apps/web_console_ng/pages/admin.py` for DB pool via `get_db_pool()` + page decorator structure):**
```python
from decimal import Decimal

from nicegui import ui, app
from apps.web_console_ng.auth.middleware import requires_auth, get_current_user
from apps.web_console_ng.components.tax_lot_table import render_tax_lot_table
from apps.web_console_ng.components.tax_harvesting import render_harvesting_suggestions
from apps.web_console_ng.core.database import get_db_pool
from apps.web_console_ng.ui.layout import main_layout
from libs.platform.web_console_auth.permissions import Permission, has_permission
from libs.platform.tax.tax_loss_harvesting import TaxLossHarvester
from libs.web_console_services.tax_lot_service import TaxLotService


@ui.page("/tax-lots")
@requires_auth
@main_layout
async def tax_lots_page() -> None:
    """Tax lot management dashboard."""
    user = get_current_user()
    if not has_permission(user, Permission.VIEW_TAX_LOTS):
        ui.label("Permission denied").classes("text-red-500")
        return

    db_pool = get_db_pool()
    if db_pool is None:
        ui.label("Database unavailable").classes("text-red-500")
        return
    service = TaxLotService(db_pool, user)
    lots = await service.list_lots()  # TODO(T16.1): add open_only=True param to list_lots()

    # Fetch harvesting data (detect_wash_sales is sale-event-driven, not a general fetch)
    # NOTE: detect_wash_sales(user_id, symbol, sale_date, loss_amount, shares_sold) is called
    # per-sale, not for general display. For the lots table, query tax_wash_sale_adjustments
    # directly to show existing wash sale flags on lots.
    harvester = TaxLossHarvester(db_pool)
    # current_prices must be fetched from market data service or cache
    # RESEARCHER lacks VIEW_PNL → 403 on /api/v1/market_prices; degrade gracefully
    current_prices: dict[str, Decimal] = await _fetch_current_prices(lots, user)
    if current_prices:
        suggestions = await harvester.find_opportunities(user["user_id"], current_prices)
    else:
        suggestions = None  # No prices → skip harvesting panel

    # Header metrics (to be implemented as component)
    # render_tax_lot_summary(lots)  # TODO: implement component

    # Main content: AG Grid + wash sale panel
    with ui.row().classes("w-full gap-4"):
        with ui.column().classes("flex-1"):
            render_tax_lot_table(lots)
        with ui.column().classes("w-80"):
            render_harvesting_suggestions(suggestions)


async def _fetch_current_prices(lots: list, user: dict) -> dict[str, Decimal]:
    """Fetch current prices for lot symbols via AsyncTradingClient.

    Uses established pattern from dashboard.py — scoped client calls with auth headers.
    See apps/web_console_ng/core/client.py:AsyncTradingClient.fetch_market_prices().

    NOTE: AsyncTradingClient.get() is a SYNC classmethod (returns singleton, not a coroutine).
    fetch_market_prices(user_id, role=None, strategies=None) -> list[dict[str, Any]]
    returns a list of dicts matching MarketPricePoint schema (e.g., [{"symbol": "AAPL", "mid": 150.0, "timestamp": "..."}, ...]).
    NOTE: The field is "mid" (from MarketPricePoint at apps/execution_gateway/schemas.py:627), NOT "price".
    Must iterate and build {symbol: Decimal(mid)} mapping; mid can be None — skip entries where mid is None.

    GRACEFUL DEGRADATION: RESEARCHER lacks VIEW_PNL, so /api/v1/market_prices
    returns 403. Catch httpx.HTTPStatusError and return empty dict — caller
    should display "N/A" for current prices and hide the harvesting panel.
    """
    import logging
    import httpx
    from apps.web_console_ng.core.client import AsyncTradingClient
    logger = logging.getLogger(__name__)
    client = AsyncTradingClient.get()  # sync classmethod — no await
    symbols_needed = {lot.symbol for lot in lots}
    try:
        raw = await client.fetch_market_prices(
            user["user_id"],
            role=user.get("role"),
            strategies=user.get("strategies"),
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            logger.info("market_prices_permission_denied", extra={"role": user.get("role")})
            return {}  # RESEARCHER or other role lacking VIEW_PNL
        # 401, 5xx, etc — degrade gracefully rather than crash the page
        logger.warning("market_prices_http_error", extra={"status": exc.response.status_code})
        return {}
    except httpx.HTTPError as exc:
        # Covers network errors, timeouts, connection refused, DNS failures, etc.
        # httpx.HTTPError is the base class for all httpx exceptions.
        logger.warning("market_prices_fetch_failed", extra={"error": str(exc)})
        return {}
    except ValueError as exc:
        # fetch_market_prices() raises ValueError if response is not a JSON array
        # (see apps/web_console_ng/core/client.py:421)
        logger.warning("market_prices_invalid_payload", extra={"error": str(exc)})
        return {}
    # raw is list[dict] matching MarketPricePoint schema — field is "mid", NOT "price"
    # mid can be None (MarketPricePoint.mid is Optional) — skip entries with no price
    return {
        str(item["symbol"]): Decimal(str(item["mid"]))
        for item in raw
        if item.get("symbol") in symbols_needed and item.get("mid") is not None
    }
```

**Acceptance Criteria:**
- [ ] Tax lots page accessible at `/tax-lots`
- [ ] AG Grid displays open lots with columns: symbol, quantity, cost_basis, acquisition_date, strategy_id, status, holding_period (derived from acquisition_date)
- [ ] Wash sale warnings rendered as red badge + tooltip with disallowed_loss amount
- [ ] Tax-loss harvesting suggestions panel with estimated savings
- [ ] Cost basis method selector (FIFO/LIFO/Specific) persisted to `tax_user_settings`
- [ ] Summary header: total unrealized gain/loss, short-term/long-term split, total wash sale impact
- [ ] Form 8949 **download** button (requires `EXPORT_DATA` — OPERATOR, ADMIN). Form 8949 on-screen **preview/view** requires `VIEW_TAX_REPORTS` (RESEARCHER, ADMIN). OPERATOR can download but not preview; RESEARCHER can preview but not download.
- [ ] **RBAC:** `VIEW_TAX_LOTS` to view page (VIEWER+); `MANAGE_TAX_LOTS` to close lots (ADMIN only); `MANAGE_TAX_SETTINGS` to change cost basis method (ADMIN only)
- [ ] **Audit:** `AuditLogger.log_admin_change(admin_user_id=..., action=..., target_user_id=..., details=...)` on lot closure and cost basis method changes
- [ ] **Audit (denied):** Permission denials on T16.1 actions must be logged via `AuditLogger.log_action(outcome="denied")` at the PAGE layer. NOTE: `TaxLotService._require_permission()` only raises `PermissionError` — it does NOT call AuditLogger. The page must catch `PermissionError` and log the denied attempt explicitly.
- [ ] **Nav:** Entry in sidebar with `receipt_long` icon + inline `has_permission` check

---

### T16.2: User Management / RBAC Admin - MEDIUM PRIORITY

**Goal:** Admin page for user role management, strategy grants, and activity monitoring.

**Current State:**
- Backend: `libs/web_console_services/user_management.py` — async functions: `list_users()`, `change_user_role()`, `grant_strategy()`, `revoke_strategy()`, `list_strategies()`, `get_user_strategies()`, bulk ops
- Audit: Integrated with `AuditLogger` — logs both SUCCESS and DENIED attempts
- Session invalidation: `change_user_role()` increments `session_version` in DB. **MIDDLEWARE GAP (multi-layer):** (1) NiceGUI `session_store.py:validate_session` does NOT check DB `session_version`. (2) Auth providers (OAuth2, mTLS, basic, dev) do NOT include `session_version` in the `user_data` dict they store in `app.storage.user` — so even if middleware were updated to compare versions, there's no session-side value to compare against. To make `session_version` checking work, one would need to: (a) update all auth providers to fetch and include `session_version` from `user_roles` table in `user_data` at login time, AND (b) add a `session_version` comparison check to `validate_session` that queries `user_roles` and compares against the stored value. **CHOSEN APPROACH:** T16.2 does NOT implement session_version checking. Instead, steps 0c/0d below implement a **middleware DB role override** (query `user_roles` per-request to override provider-derived role) combined with a **Force Logout** button (step 0c). This avoids modifying all auth providers and is simpler to implement. The session_version approach is documented here for context but is NOT part of the implementation plan. **SESSION STORE LIMITATION:** `session_store.py` only supports `invalidate_session(session_id)` and has no user→session index (`ng_session:{session_id}` keys are not indexed by user_id). To force-logout a user by user_id, T16.2 must either: (i) add a `user_id→session_ids` reverse index (e.g., Redis SET key `ng_user_sessions:{user_id}` → set of session_ids, SADD at login in `create_session`; must be 1:N because the session store allows multiple concurrent sessions per user — each `create_session` call creates a new `ng_session:{session_id}` key), or (ii) SCAN all `ng_session:*` keys, decrypt, check `user_id`, and invalidate matching sessions (expensive but requires no schema changes).
- Frontend: No NiceGUI page exists

**Service Integration Details:**
- `list_users(db_pool)` → `list[UserInfo]` (dataclass: user_id, role, session_version, updated_at: str, updated_by, strategy_count). NOTE: `updated_at` is a `str` — AG Grid component must handle string-to-date parsing for sorting/formatting
- `change_user_role(db_pool, user_id, new_role, admin_user_id, audit_logger, reason)` → `tuple[bool, str]` — validates role via `Role` enum, increments `session_version`, audit logs. NOTE: `reason` is required. **PROVISIONING GAP:** Returns `(False, "User not found: {user_id}")` if no `user_roles` row exists for the target user (see `libs/web_console_services/user_management.py:141`). Auth providers do NOT INSERT into `user_roles` at login — the table is populated by explicit admin actions or ops tooling (`scripts/ops/manage_roles.py`). The T16.2 admin UI must handle this: before calling `change_user_role()`, check if the user has a row; if not, INSERT one (e.g., `INSERT INTO user_roles (user_id, role, updated_by) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO NOTHING`). Similarly, `grant_strategy()` depends on FK to `user_roles(user_id)`, so strategy grants also fail for un-provisioned users. Step 0b below adds this provisioning logic to the service layer.
- `grant_strategy(db_pool, user_id, strategy_id, admin_user_id, audit_logger)` → `tuple[bool, str]`
- `revoke_strategy(db_pool, user_id, strategy_id, admin_user_id, audit_logger)` → `tuple[bool, str]`
- `list_strategies(db_pool)` → `list[StrategyInfo]` (dataclass: strategy_id, name, description: str | None)
- `get_user_strategies(db_pool, user_id)` → `list[str]` (list of strategy ID strings, NOT StrategyInfo)
- Bulk ops: `bulk_change_roles(db_pool, user_ids, new_role, admin_user_id, audit_logger, reason)` → `dict[str, tuple[bool, str]]`, `bulk_grant_strategy(db_pool, user_ids, strategy_id, admin_user_id, audit_logger)` → `dict[str, tuple[bool, str]]`, `bulk_revoke_strategy(...)` → `dict[str, tuple[bool, str]]`
- All functions take `db_pool` as first argument (not class-based; module-level async functions)

**Features:**
1. **User List Table** — AG Grid: user_id, role (badge), strategy_count, updated_at, updated_by, status indicators. **IMPORTANT: `list_users()` only reads from `user_roles` table** (see `libs/web_console_services/user_management.py:72`). Users who logged in via auth providers but were never admin-provisioned will NOT appear in this list (auth providers do not INSERT into `user_roles`). To manage unprovisioned users, the page must also provide a **manual user_id lookup** text field: the admin enters a user_id, the page calls `ensure_user_provisioned()` (step 0b) to bootstrap their `user_roles` row, then refreshes the user list. This is the expected workflow — an admin learns a user_id from the audit log, team communication, or the auth provider's user directory, then provisions them via the admin UI.
2. **Role Assignment** — Inline dropdown or dialog to change role (VIEWER, RESEARCHER, OPERATOR, ADMIN). After migration 0028 adds RESEARCHER to the DB CHECK constraint, the dropdown offers all four roles. Before migration 0028 is applied, the dropdown must NOT offer RESEARCHER (see CRITICAL note above).
3. **Strategy Grants** — Expand row or dialog showing assigned strategies; add/remove strategy checkboxes
4. **Bulk Operations** — Multi-select rows + bulk role change or strategy grant/revoke
5. **Activity Log** — Per-user audit log filtered by `audit_log.user_id = selected_user OR audit_log.resource_id = selected_user OR audit_log.resource_id LIKE '{selected_user}:%'` (since `log_admin_change` stores actor in `user_id` and target in `resource_id`; successful strategy grant/revoke events use `log_admin_change(target_user_id=user_id)` so `resource_id=target_user_id`; **only** denied/failed strategy events use `resource_id=f"{user_id}:{strategy_id}"` via `log_action`). The LIKE clause catches both the composite denied/failed format and any future composite patterns. **SQL safety:** The LIKE pattern must escape SQL wildcard characters AND the escape character itself in `selected_user` before appending `:%`. Escape order matters — escape backslash first, then wildcards: `escaped = selected_user.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')`. Then parameterize as `%s` with the escaped value + `:%` suffix. The SQL LIKE clause must include an explicit `ESCAPE` directive: `resource_id LIKE %s ESCAPE '\\'`. All three clauses use parameterized queries (`%s` placeholders), never f-string interpolation in SQL.
6. **Safety Guards** — Cannot edit own role; cannot remove last ADMIN; confirmation dialog for role downgrades
7. **Force Logout Button** — Per-user action button (ADMIN only) that calls `invalidate_redis_sessions_for_user(user_id)` (step 0c) to immediately terminate all active sessions for a target user. Use case: after demoting a user's role, admin force-logouts their sessions so the old elevated role cannot be used. Requires confirmation dialog ("Force logout user X? They will be signed out of all sessions."). Audit-logged via `AuditLogger.log_admin_change(action="force_logout", target_user_id=...)`.

**Implementation Plan:**

**ADR REQUIRED:** Steps 0c and 0d below change the session role-authority model (from provider-only to DB-override) and add middleware-level DB dependencies. Per repo policy (`CLAUDE.md`: "Every architectural change requires an ADR"), an ADR must be written before implementing steps 0c/0d. Suggested: `docs/ADRs/ADR-0038-db-role-authority-override.md` (**NEW FILE — write before implementing steps 0c/0d**) covering: (1) decision to override provider-derived roles with DB roles, (2) failure behavior (fail-open), (3) caching strategy, (4) scope limitation (only affects `@require_permission` routes, not `api_auth` routes).

| Step | File | Description |
|------|------|-------------|
| 0 | `db/migrations/0028_add_researcher_role.sql` (**NEW FILE — to be created during T16.2 implementation**) | **PREREQUISITE:** `ALTER TABLE user_roles DROP CONSTRAINT IF EXISTS valid_role; ALTER TABLE user_roles ADD CONSTRAINT valid_role CHECK (role IN ('viewer', 'researcher', 'operator', 'admin'));` — idempotent; required before offering RESEARCHER in the role dropdown. Next available migration number; verify no conflicts at implementation time. **OPS TOOLING PARITY:** Also update `scripts/ops/manage_roles.py:97` to add `"researcher"` to the CLI `choices` list (currently `["viewer", "operator", "admin"]`) so that ops tooling can assign the RESEARCHER role via CLI. Without this update, migration 0028 enables RESEARCHER in the DB and admin UI, but the ops CLI still rejects it. |
| 0b | `libs/web_console_services/user_management.py` | Add server-side safety guards to `change_user_role()`: (1) self-edit check (`if user_id == admin_user_id: return False, "Cannot change own role"`), (2) last-admin guard — within the existing transaction, use row-level locking: `SELECT user_id FROM user_roles WHERE role = 'admin' FOR UPDATE`, then count the returned rows in Python; block if count == 1 and the target is the sole admin. This follows the service's existing pattern of locking concrete rows with `FOR UPDATE` (see `libs/web_console_services/user_management.py:137`) rather than locking an aggregate query. **(3) CACHE INVALIDATION:** After a successful role change, delete the Redis role cache key `ng_role_cache:{user_id}` (if using the optional caching from step 0d) so the middleware picks up the new role on the next request instead of waiting for TTL expiry. Use `DELETE ng_role_cache:{user_id}` — best-effort, log and ignore Redis errors. Also add an `ensure_user_provisioned(db_pool, user_id, default_role, admin_user_id, audit_logger)` function that does `INSERT INTO user_roles (user_id, role, updated_by) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO NOTHING` — this bootstraps the `user_roles` row for users who logged in via auth providers (which do NOT provision `user_roles`). The admin UI must call this before `change_user_role()` or `grant_strategy()` for users without existing rows. The `default_role` parameter accepts the target user's provider-derived session role when available (e.g., from `app.storage.user["user"]["role"]` if the target is the current user, or from a session lookup). **However, in the manual user_id lookup flow (Feature 1), the admin enters a bare user_id and the target user's provider role is NOT available from any existing page/service data source.** In this case, pass `default_role="viewer"` — the safest canonical default. The admin can then immediately call `change_user_role()` to assign the intended role. The `default_role` **must be validated** against the DB CHECK constraint before INSERT: if the provider role is non-canonical (e.g., `"trader"` from OAuth2/mTLS, or arbitrary env values from basic/dev), normalize to `"viewer"` (the safest default). Only INSERT roles present in the CHECK constraint (`viewer|operator|admin`, plus `researcher` after migration 0028). |
| 0c | `apps/web_console_ng/auth/session_store.py` | **ROLE AUTHORITY FIX:** Add `invalidate_redis_sessions_for_user(user_id)` method. NOTE: This is distinct from the existing `invalidate_user_sessions()` in `libs/platform/web_console_auth/session_invalidation.py` which only increments `session_version` in the DB `user_roles` table — it does NOT touch Redis sessions. The new method targets Redis directly. **Preferred approach — reverse index:** Add a 1:N reverse index using a Redis SET `ng_user_sessions:{user_id}` (SADD session_id at login in `create_session`; must be SET not plain key since the store supports multiple concurrent sessions per user). `invalidate_redis_sessions_for_user` iterates the SET members and DELETEs each `ng_session:{session_id}` key, then DELETEs the SET itself. **Lifecycle cleanup (REQUIRED):** (1) `create_session` must SADD session_id to the SET, (2) `rotate_session` must SREM the old session_id and SADD the new session_id in the same SET, (3) `invalidate_session` must SREM the session_id from the SET, (4) SET TTL should match the **remaining TTL** of the newest session in the set (not `absolute_timeout`) — rotated sessions use `SETEX` with remaining TTL (see `session_store.py:355-397`), so using full `absolute_timeout` on the reverse-index SET would leave stale index members after rotation. On `create_session` (new session), use `absolute_timeout` as the initial TTL; on `rotate_session`, re-set the SET TTL to the remaining TTL of the new session key (query via `TTL ng_session:{new_session_id}`). This is O(K) where K = sessions per user (typically <10), not O(N) across all sessions. **Fallback approach — SCAN+decrypt (NOT recommended for production):** SCAN `ng_session:*` keys, decrypt each, check `user_id`, delete matches. This is O(N) across ALL sessions and operationally expensive at scale due to encrypted payloads (Fernet). Only use if reverse index cannot be implemented. This enables T16.2 "Force Logout" button. |
| 0d | `apps/web_console_ng/auth/middleware.py` | **ROLE AUTHORITY FIX:** Add a DB role override in `AuthMiddleware.dispatch`. **MIDDLEWARE ORDERING CONSTRAINT:** `SessionMiddleware` (added at `apps/web_console_ng/main.py:65-66`) runs BEFORE `AuthMiddleware` (added at `apps/web_console_ng/main.py:64`) due to ASGI middleware stack reversal. When `SessionMiddleware` has already populated `request.state.user`, `AuthMiddleware.dispatch` skips `_validate_session_and_get_user()` entirely (line 203: `if not user:`). Therefore, the DB role override must NOT be placed inside `_validate_session_and_get_user()` — it would never execute for users authenticated via `SessionMiddleware`. **CORRECT INSERTION POINT:** Place the DB role override in `AuthMiddleware.dispatch` AFTER the user-population block (lines 199-236) and BEFORE the `if not user:` redirect check (line 237). At this point, `user` is available regardless of which middleware/path populated it. The override logic: query `user_roles` for the session user's current DB role; if it differs from `user["role"]` (which is also `app.storage.user["user"]["role"]`), update BOTH the in-memory session state (`app.storage.user["user"]["role"]`, `request.state.user["role"]`, and local `user["role"]`) AND the Redis-backed session payload. For Redis update, add a new `update_session_role(session_id, role)` method to `SessionStore` — where `session_id` is extracted from `app.storage.user.get("session_id")` (stored by `AuthMiddleware` at line 214-215 on prior or current requests) using the existing `extract_session_id(signed_cookie)` helper (`apps/web_console_ng/auth/session_store.py:592`) which safely parses the cookie format `{session_id}.{key_id}:{signature}` via `rsplit(".", 1)` (handles session IDs that may contain dots). **STORAGE FALLBACK:** If `app.storage.user` is unavailable (NiceGUI storage write failures are handled best-effort at `apps/web_console_ng/auth/middleware.py:218`), fall back to extracting `session_id` directly from the request cookie via the same `extract_session_id()` helper. **COOKIE NAME:** The cookie key is NOT `ng_session` — it is `"nicegui_session"` (HTTP) or `"__Host-nicegui_session"` (HTTPS), as determined by `CookieConfig.get_cookie_name()` in `apps/web_console_ng/auth/cookie_config.py:31`. Use the same `CookieConfig` to get the correct name: `cookie_name = CookieConfig.from_env().get_cookie_name()`, then `request.cookies.get(cookie_name)`. If neither source yields a session_id, skip the Redis session update — the in-memory override still applies for the current request, and the next request will re-trigger the DB lookup. The method reads `ng_session:{session_id}` from Redis, decrypts, updates the `user.role` field, re-encrypts, and re-persists with the same TTL via `SETEX`. Also refresh `X-User-Role` for subsequent gateway calls. Without updating the Redis session, the in-memory override would be lost on the next request when session data is re-loaded from Redis. **No `_validate_session_and_get_user()` refactoring needed** — session_id is obtained from `app.storage.user` instead of the helper's return value. **Missing-row handling:** Auth providers do NOT provision `user_roles` rows — a user may have no DB role entry. If the `user_roles` query returns no row, skip the override and keep the provider-derived session role unchanged (the provider role is still the authority for users without explicit DB role assignments). Only override when a DB row exists AND its role differs from the session role. This makes `change_user_role()` DB updates authoritative for web-console sessions while gracefully handling users who have never had an admin-assigned role. **FAILURE BEHAVIOR (per-request DB dependency):** The DB role lookup adds a per-request DB query. To mitigate failures and latency: (1) **Timeout:** Use a short query timeout (e.g., `statement_timeout = '500ms'` or `asyncio.wait_for(..., timeout=0.5)`) — role override is not critical enough to block page loads. (2) **Fail-open:** On DB timeout, connection error, or any `psycopg.Error`, log a warning and skip the override — keep the provider-derived session role unchanged. The middleware must NEVER block or error the request due to a role-override DB failure. (3) **Optional caching:** To reduce per-request DB load, cache the DB role in Redis with a short TTL (e.g., `ng_role_cache:{user_id}` → role string, TTL 60s). On cache hit, use the cached role; on cache miss, query DB and populate cache. Cache is best-effort (fail-open to DB query, which itself is fail-open to no override). This bounds the DB queries to at most 1 per user per TTL window. **CACHE INVALIDATION:** `change_user_role()` (step 0b) must delete `ng_role_cache:{user_id}` after a successful role change so the middleware picks up the new role immediately instead of serving the stale cached role until TTL expiry. **SECURITY TRADE-OFF:** Fail-open means that if the DB is unavailable AND the Redis role cache has expired, a recently demoted user retains their elevated provider-derived session role until the DB recovers. This is an accepted trade-off because: (a) the window is bounded by session `absolute_timeout` (sessions eventually expire), (b) the admin can use the "Force Logout" button (step 0c / Feature 7) to immediately invalidate the demoted user's Redis sessions — this doesn't require DB access, (c) fail-closed would block ALL authenticated users during DB outages, which is worse for availability. After demoting a user, admins SHOULD also force-logout their sessions as a defense-in-depth measure. **PAGE-LOAD CACHING LIMITATION:** The middleware override updates the session role per-request, but existing pages (e.g., `apps/web_console_ng/pages/dashboard.py:237`, `apps/web_console_ng/pages/manual_order.py:52`, `apps/web_console_ng/pages/position_management.py:103`) capture `user_role = user.get("role")` once at page load and reuse it in async action callbacks. Mid-session role changes via the override will NOT be reflected in already-loaded pages until the user navigates away and reloads. Force Logout (Feature 7) is the recommended way to ensure immediate effect — it terminates the session, forcing a fresh page load with the updated role. |
| 0e | `apps/web_console_ng/auth/redirects.py` | Add `/admin/users` to `ALLOWED_REDIRECT_PATHS` (T16.1 step 6b already adds `/tax-lots`). |
| 0f | `apps/web_console_ng/pages/admin_users.py` (Feature 7 — content for NEW FILE created at step 5) | Wire "Force Logout" button per user row: on click, show confirmation dialog → call `invalidate_redis_sessions_for_user(user_id)` from step 0c → audit log via `AuditLogger.log_admin_change(action="force_logout", target_user_id=...)` → notify success. Button should be visually distinct (e.g., orange with `logout` icon) and disabled for the admin's own row (cannot force-logout self — use normal logout instead). |
| 1 | `apps/web_console_ng/components/user_table.py` (**NEW FILE**) | `render_user_table(users, *, on_role_change, on_view_strategies, on_view_activity, on_force_logout)` — AG Grid with role badges and action buttons including Force Logout |
| 2 | `apps/web_console_ng/components/role_selector.py` (**NEW FILE**) | `render_role_change_dialog(user_id, current_role, *, on_confirm)` — Dropdown + confirmation for role changes |
| 3 | `apps/web_console_ng/components/strategy_grants.py` (**NEW FILE**) | `render_strategy_grants_dialog(user_id, assigned, available, *, on_grant, on_revoke)` — Checkbox list of strategies |
| 4 | `apps/web_console_ng/components/user_activity.py` (**NEW FILE**) | `render_user_activity_log(user_id, events)` — Filtered audit log viewer per user. Page layer queries `WHERE user_id = %s OR resource_id = %s OR resource_id LIKE %s ESCAPE '\\'` (with escaped `f"{user_id}:%"` for LIKE — escape `\` first, then `%` and `_` in `user_id`: `user_id.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')`) to capture: (a) actions performed BY the user, (b) actions targeting the user exactly (includes successful strategy grant/revoke via `log_admin_change(target_user_id=user_id)`), (c) denied/failed strategy events where `resource_id` is `f"{user_id}:{strategy_id}"` (via `log_action`). |
| 5 | `apps/web_console_ng/pages/admin_users.py` (**NEW FILE**) | Page at `/admin/users`, orchestrates user management |
| 6 | `apps/web_console_ng/pages/__init__.py` | Add `admin_users` to the grouped import block |
| 7 | `apps/web_console_ng/ui/layout.py` | Add nav entry `("Users", "/admin/users", "people", None)` + inline `has_permission(user, Permission.MANAGE_USERS)` check |
| 8 | `tests/apps/web_console_ng/pages/test_admin_users.py` (**NEW FILE**) | Page-level tests |
| 9 | `tests/apps/web_console_ng/components/test_user_table.py` (**NEW FILE**) | Component unit tests |
| 9b | `tests/apps/web_console_ng/components/test_role_selector.py` (**NEW FILE**) | Role dropdown, self-edit guard, last-admin guard tests |
| 10 | `tests/apps/web_console_ng/test_navigation.py` | Update `test_navigation_item_structure()` to include Users nav entry |
| 10b | `tests/apps/web_console_ng/test_layout.py` | Update `test_nav_items_include_expected_routes()` (line 304) to add `/admin/users` to `expected_paths` list — this test hard-codes expected routes and will fail without the update |
| 11 | `tests/libs/web_console_services/test_user_management.py` | Extend existing service integration tests with T16.2-specific scenarios (self-edit guard, last-admin guard, RESEARCHER role) |

**Page Pattern:**
```python
@ui.page("/admin/users")
@requires_auth
@main_layout
async def admin_users_page() -> None:
    """User management admin page."""
    user = get_current_user()
    if not has_permission(user, Permission.MANAGE_USERS):
        ui.label("Admin access required").classes("text-red-500")
        return

    db_pool = get_db_pool()
    if db_pool is None:
        ui.label("Database unavailable").classes("text-red-500")
        return
    audit = AuditLogger(db_pool)
    users = await list_users(db_pool)

    async def on_role_change(target_user_id: str, new_role: str, reason: str) -> None:
        current = get_current_user()
        # Guard: cannot edit own role
        if target_user_id == current["user_id"]:
            await audit.log_action(
                user_id=current["user_id"],
                action="role_change_denied",
                resource_type="user",
                resource_id=target_user_id,
                outcome="denied",
                details={"reason": "self_edit", "attempted_role": new_role},
            )
            ui.notify("Cannot change your own role", type="warning")
            return
        # Guard: cannot remove last admin
        admin_count = sum(1 for u in users if u.role == "admin")
        target = next((u for u in users if u.user_id == target_user_id), None)
        if target is None:
            ui.notify("User not found — list may be stale", type="warning")
            return
        if target.role == "admin" and admin_count <= 1 and new_role != "admin":
            await audit.log_action(
                user_id=current["user_id"],
                action="role_change_denied",
                resource_type="user",
                resource_id=target_user_id,
                outcome="denied",
                details={"reason": "last_admin", "attempted_role": new_role},
            )
            ui.notify("Cannot remove the last admin", type="negative")
            return

        success, msg = await change_user_role(
            db_pool, target_user_id, new_role, current["user_id"], audit, reason
        )
        if success:
            ui.notify(f"Role updated to {new_role}", type="positive")
        else:
            ui.notify(msg, type="negative")
        # Refresh user list...

    render_user_table(
        users,
        on_role_change=on_role_change,
        on_view_strategies=on_view_strategies,  # TODO: implement callback
        on_view_activity=on_view_activity,  # TODO: implement callback
    )
```

**Acceptance Criteria:**
- [ ] Users page accessible at `/admin/users`
- [ ] AG Grid displays: user_id, role (color-coded badge), strategy_count, updated_at, updated_by. NOTE: Only users with `user_roles` rows appear in the list (see Feature 1 note).
- [ ] Manual user_id lookup field for provisioning users not yet in `user_roles` (admin enters user_id → calls `ensure_user_provisioned()` → refreshes list)
- [ ] Role change via dropdown/dialog with confirmation and reason field
- [ ] Strategy grant/revoke dialog with available strategies checkbox list
- [ ] Bulk role change and strategy grant/revoke for multi-selected users
- [ ] Per-user activity log panel (filtered audit events)
- [ ] Force Logout button per user row (confirmation dialog → `invalidate_redis_sessions_for_user()` → audit log with `action="force_logout"` → success notification). Disabled for admin's own row.
- [ ] **RBAC:** `MANAGE_USERS` required to access page (ADMIN only)
- [ ] **Safety:** Cannot change own role (prevents self-elevation). NOTE: `change_user_role()` currently has NO server-side self-edit guard — **must add a server-side check** (`if user_id == admin_user_id: return False, "Cannot change own role"`) during T16.2 implementation to prevent bypass via direct service calls.
- [ ] **Safety:** Cannot downgrade last ADMIN user. NOTE: `change_user_role()` service has no server-side last-admin guard — **must add a server-side check** (row-level lock: `SELECT user_id FROM user_roles WHERE role = 'admin' FOR UPDATE`, then count rows in Python) during T16.2 implementation to prevent bypass.
- [ ] **Safety:** Confirmation dialog for role downgrades (e.g., "Demote user X from OPERATOR to VIEWER?")
- [ ] **Session — Middleware DB Role Override (step 0d):** `AuthMiddleware.dispatch` overrides provider-derived session role with DB role from `user_roles` per-request (placed AFTER user-population block, BEFORE `if not user:` redirect — see step 0d for details). Updates in-memory session, Redis session payload, and `X-User-Role` header for gateway calls. Fail-open on DB/Redis errors. **KNOWN LIMITATION:** Does NOT affect `api_auth()`-gated gateway routes (see LIMITATION note in Security Requirements).
- [ ] **Session — Force Logout (step 0c / Feature 7):** "Force Logout" button per user row calls `invalidate_redis_sessions_for_user(user_id)` to immediately terminate all active sessions. Audit-logged. Used as defense-in-depth after role demotion.
- [ ] **Session — Background context:** Role changes still increment `session_version` in DB (existing `change_user_role()` behavior). Session_version is NOT checked by middleware — the middleware DB role override (step 0d) makes this unnecessary for the web-console. Auth providers derive role from IdP/cert/env, NOT `user_roles` table, but step 0d overrides this per-request.
- [ ] **Audit:** `AuditLogger.log_admin_change(admin_user_id=..., action=..., target_user_id=..., details=...)` on all role changes, strategy grants/revokes. NOTE: Existing action-name discrepancy — `libs/web_console_services/user_management.py` writes `action="role_change"` but admin UI filter (`apps/web_console_ng/pages/admin.py:125`) lists `"role_changed"`. T16.2 activity log must filter on the actual stored value (`role_change`), not the admin UI filter label.
- [ ] **Audit:** Failed attempts logged via `AuditLogger.log_action(...)` with `outcome="denied"` for permission/validation failures, `outcome="failed"` for DB/infrastructure errors (matches existing `libs/web_console_services/user_management.py` conventions). NOTE: Existing admin UI audit filter (`apps/web_console_ng/pages/admin.py:130`) uses `OUTCOMES = ["All", "success", "failure"]` — the filter value `"failure"` will NOT match `"failed"` records. T16.2 activity log should filter on the actual stored values (`"denied"`, `"failed"`) rather than the admin UI's vocabulary.
- [ ] **Nav:** Entry in sidebar with `people` icon + inline `has_permission` check

---

### T16.3: API Key Revoke/Rotate - LOW PRIORITY

**Goal:** Add revoke and rotate functionality to the existing API Keys tab in `/admin`.

**Current State:**
- Page: `apps/web_console_ng/pages/admin.py` — API Keys tab has create + list; revoke/rotate is TODO (see docstring line 4)
- Backend: `libs/platform/admin/api_keys.py` — `generate_api_key()`, `hash_api_key()`, `is_key_revoked()`, `update_last_used()`
- DB: `api_keys` table has `revoked_at` column (NULL = active), `last_used_at` column
- Existing key display: shows `key_prefix` only (full key shown once at creation in dialog)
- Existing admin.py uses `get_db_pool()` from `apps/web_console_ng/core/database.py` and `db_pool.connection()` for DB access

**Service Integration Details:**
- Revoke: `UPDATE api_keys SET revoked_at = NOW() WHERE id = %s AND user_id = %s AND revoked_at IS NULL` (user-scoped — matches existing `_list_api_keys()` which filters `WHERE user_id = %s`; `revoked_at IS NULL` prevents re-revoking already-revoked keys). NOTE: Admin revoking other users' keys is out of scope for T16.3; the current implementation is strictly self-service within the admin's own keys.
- **FK DEPENDENCY:** `api_keys.user_id` has `FOREIGN KEY REFERENCES user_roles(user_id) ON DELETE CASCADE` (`0011_create_api_keys.sql:29`). Auth providers do NOT provision `user_roles` rows at login, so `INSERT INTO api_keys` will fail with FK violation for unprovisioned users. **Mitigation:** T16.3 is restricted to `MANAGE_API_KEYS` (ADMIN only), and admins are typically provisioned via ops tooling (`scripts/ops/manage_roles.py`). The existing `_create_api_key` in `apps/web_console_ng/pages/admin.py:337` already has this FK dependency and works because admins are provisioned. For belt-and-suspenders safety, T16.3 rotate (which inserts a new row) AND the existing create path (`apps/web_console_ng/pages/admin.py:351`, which currently only catches `ValueError`/`RuntimeError` at line 265) should both catch `ForeignKeyViolation` and display a user-friendly error ("User not provisioned — contact admin"). If T16.2 is implemented first, `ensure_user_provisioned()` from step 0b can be called before key creation/rotation as a more robust fix.
- Rotate: Revoke existing key + call `generate_api_key()` + `hash_api_key()` for `key_hash`/`key_salt` storage + insert new row (same pattern as `_create_api_key` in `apps/web_console_ng/pages/admin.py:337`) + show new full key once
- Revocation cache key: `f"api_key_revoked:{key_prefix}"` — on revoke, set cache immediately: `redis.setex(f"api_key_revoked:{key_prefix}", 300, "1")` (avoids DB roundtrip on next check)
- Last used: `last_used_at` updated via `update_last_used()` with 1-minute debounce

**Features:**
1. **Revoke Button** — Per-row action button in API keys `ui.table` (NOT AG Grid — current implementation uses `ui.table`); opens confirmation dialog
2. **Revoke Confirmation** — Type "REVOKE" to confirm (destructive action pattern)
3. **Rotate Action** — Per-row action; revokes old key + creates new key in single transaction; shows new key once
4. **Last Used Display** — `last_used_at` column in `ui.table` formatted as relative time ("2h ago", "never")
5. **Status Badge** — Active (green) / Revoked (red) / Expired (yellow) based on `revoked_at` and `expires_at`
6. **Revoke History** — Show revoked keys (greyed out) with revocation timestamp

**Implementation Plan:**

| Step | File | Description |
|------|------|-------------|
| 1 | `apps/web_console_ng/pages/admin.py` | Add `_revoke_api_key(key_id, key_prefix, db_pool)` function with confirmation dialog (see illustrative code below for full signature) |
| 2 | Same file | Add `_rotate_api_key(key_id, key_prefix, db_pool)` function (revoke + create in transaction) |
| 3 | Same file | `_list_api_keys()` already selects `id`, `last_used_at`, `revoked_at`, `expires_at` — update `ui.table` column rendering to display them (status badge, relative time). **ROW ID WIRING:** The query fetches `id` (`apps/web_console_ng/pages/admin.py:371`) but the `ui.table` rows dict construction (`apps/web_console_ng/pages/admin.py:322`) currently omits it. T16.3 must include `id` (as string) in row data so revoke/rotate action buttons can reference the key's DB primary key. **SORT ORDER:** Current query sorts by `created_at DESC` (`apps/web_console_ng/pages/admin.py:372`). Update to sort active keys first (revoked at bottom): `ORDER BY (revoked_at IS NOT NULL), created_at DESC` — this groups active keys above revoked keys while preserving chronological order within each group. |
| 4 | Same file | Add status badge rendering (Active/Revoked/Expired) to key list |
| 5 | Same file | Add revoke/rotate action buttons per row |
| 6 | Same file | Cache revoked state in Redis on revoke: `redis.setex(f"api_key_revoked:{key_prefix}", 300, "1")` |
| 6b | Same file | Fix audit filter vocabulary: add `"api_key_rotated"` to `ACTION_CHOICES` (at `apps/web_console_ng/pages/admin.py:125`); update `OUTCOMES` list (at `apps/web_console_ng/pages/admin.py:130`) from `["All", "success", "failure"]` to `["All", "success", "failure", "denied", "failed"]` — keep `"failure"` for forward compatibility and safety (`audit_log.outcome` is `VARCHAR(20)` with no CHECK constraint — any service can write arbitrary outcome strings; while current `libs/web_console_services/cb_service.py` only writes `outcome="success"` and `apps/web_console_ng/auth/session_store.py` auth failures go to `auth_audit_log` NOT `audit_log`, future services or manual DB entries may use `"failure"` — removing it from filters would make those records unfilterable), and add `"denied"` and `"failed"` to match values from `libs/web_console_services/user_management.py` and T16 audit calls. Without this fix, rotated key events won't appear in the audit filter dropdown, and denied/failed outcomes will be unfilterable. |
| 7 | `tests/apps/web_console_ng/pages/test_admin_api_keys.py` (**NEW FILE**) | Tests for revoke, rotate, permission checks, confirmation flow |

**Revoke Implementation (illustrative — lives in `admin.py` alongside existing API key code):**
```python
import logging
from typing import Any

from nicegui import ui
from redis.exceptions import RedisError

from apps.web_console_ng.auth.middleware import get_current_user
from apps.web_console_ng.core.redis_ha import get_redis_store
from libs.platform.web_console_auth.audit_log import AuditLogger
from libs.platform.web_console_auth.permissions import Permission, has_permission

logger = logging.getLogger(__name__)


async def _revoke_api_key(key_id: str, key_prefix: str, db_pool: Any) -> None:
    """Revoke an API key with confirmation.

    NOTE: key_id is str from the ui.table row data. The DB column api_keys.id is UUID;
    psycopg3 accepts str for UUID parameters, so no explicit cast is needed.
    """
    user = get_current_user()
    audit = AuditLogger(db_pool)
    # Permission check: MANAGE_API_KEYS required
    if not has_permission(user, Permission.MANAGE_API_KEYS):
        await audit.log_action(
            user_id=user.get("user_id", "unknown"),
            action="api_key_revoke_denied",
            resource_type="api_key",
            resource_id=key_id,
            outcome="denied",
            details={"key_prefix": key_prefix, "role": user.get("role")},
        )
        ui.notify("Permission denied", type="negative")
        return
    # Confirmation dialog: user must type "REVOKE"
    with ui.dialog() as dialog, ui.card():
        ui.label(f"Revoke API key {key_prefix}?").classes("text-lg font-bold")
        ui.label("This action cannot be undone. Type REVOKE to confirm.")
        confirm_input = ui.input("Type REVOKE").props("outlined")

        async def on_confirm():
            if confirm_input.value != "REVOKE":
                ui.notify("Type REVOKE to confirm", type="warning")
                return
            async with db_pool.connection() as conn:
                # User-scoped revoke: include user_id to prevent revoking other users' keys
                cursor = await conn.execute(
                    "UPDATE api_keys SET revoked_at = NOW() WHERE id = %s AND user_id = %s AND revoked_at IS NULL RETURNING key_prefix",
                    (key_id, user["user_id"]),
                )
                row = await cursor.fetchone()
                if not row:
                    ui.notify("Key not found or already revoked", type="warning")
                    dialog.close()
                    return
                db_key_prefix = row[0]  # Use DB-returned prefix, not UI input
            # Cache revoked state in Redis (avoids DB roundtrip on next check)
            # Use async redis from apps/web_console_ng/core/redis_ha.py (NOT sync redis from dependencies.py)
            # Fail-open: Redis cache is best-effort; DB is authoritative for revocation
            try:
                store = get_redis_store()  # from apps.web_console_ng.core.redis_ha
                # Both HARedisStore and SimpleRedisStore expose async get_master()
                redis_client = await store.get_master()
                await redis_client.setex(f"api_key_revoked:{db_key_prefix}", 300, "1")
            except (RedisError, OSError, ConnectionError):
                # Catch RedisError and broader connection failures (HA plumbing may raise OSError/ConnectionError)
                logger.warning("redis_revoke_cache_failed", extra={"key_prefix": db_key_prefix})
            # Audit log
            audit = AuditLogger(db_pool)
            # target_user_id = key owner (from the api_keys row's user_id column)
            # For self-revoke they're the same as admin; for admin revoking
            # another user's key, pass the key owner's user_id
            await audit.log_admin_change(
                admin_user_id=user["user_id"],
                action="api_key_revoked",
                target_user_id=user["user_id"],  # self-revoke; adjust if admin-revoke added
                details={"key_id": key_id, "key_prefix": db_key_prefix},  # Use DB-returned prefix, not UI input
            )
            dialog.close()
            ui.notify("API key revoked", type="positive")
            await _refresh_keys()  # Re-query and update ui.table rows

        ui.button("Revoke", on_click=on_confirm, color="red")
        ui.button("Cancel", on_click=dialog.close)
    dialog.open()


async def _refresh_keys() -> None:
    """Re-query API keys and update the ui.table. Implementation detail of admin.py."""
    ...  # Re-fetches from DB and updates table rows
```

**Acceptance Criteria:**
- [ ] Revoke button per row in API keys `ui.table` (red, with `block` icon). NOTE: Current implementation uses `ui.table`, not AG Grid.
- [ ] Revoke confirmation: dialog requires typing "REVOKE" to confirm
- [ ] Rotate button per row (revoke + create new key in single DB transaction)
- [ ] Rotate shows new full key in dialog (same pattern as key creation)
- [ ] `last_used_at` column displayed as relative time ("2h ago", "3d ago", "never")
- [ ] Status badges: Active (green), Revoked (red with timestamp), Expired (yellow)
- [ ] Revoked keys shown greyed out at bottom of list (not hidden)
- [ ] Redis revocation cache set on revoke via async `get_redis_store()` from `apps/web_console_ng/core/redis_ha.py` — use `await store.get_master()` uniformly (both `HARedisStore` and `SimpleRedisStore` expose the same async `get_master()` API)
- [ ] **RBAC:** `/admin` page requires any of `MANAGE_API_KEYS`, `MANAGE_SYSTEM_CONFIG`, or `VIEW_AUDIT` (see `ADMIN_PERMISSIONS` set at `apps/web_console_ng/pages/admin.py:41`). The API Keys tab itself has a per-tab `MANAGE_API_KEYS` check (`apps/web_console_ng/pages/admin.py:184`). Users with `VIEW_AUDIT` or `MANAGE_SYSTEM_CONFIG` can access `/admin` but see "Permission denied" on the API Keys tab content.
- [ ] **Security:** Full key never re-displayed after creation (only `key_prefix` shown)
- [ ] **Security:** Revoke is irreversible (no "unrevoke" action)
- [ ] **Audit (success):** `AuditLogger.log_admin_change(admin_user_id=..., action=..., target_user_id=..., details=...)` with action `api_key_revoked` or `api_key_rotated`, including `key_id` and `key_prefix` (never full key). NOTE: `api_key_rotated` is a new action not currently in `admin.py` `ACTION_CHOICES` filter list — T16.3 must add it to `ACTION_CHOICES` so rotated events appear in the audit log filter dropdown.
- [ ] **Audit (denied/failed):** Permission denials (user lacks `MANAGE_API_KEYS`) and DB/infrastructure errors during revoke/rotate must be logged via `AuditLogger.log_action(outcome="denied")` or `AuditLogger.log_action(outcome="failed")` respectively — consistent with the denied/failed audit standard used in T16.1 and T16.2.

---

## Cross-Cutting Implementation Patterns

### Page-Level Authorization (Established Pattern)

```python
# Pattern from apps/web_console_ng/pages/admin.py, universes.py — use for all T16 pages
# NOTE: get_current_user() fallback returns {"role": "viewer", "username": "Guest"}
# which lacks "user_id". Pages behind @requires_auth will always have a real user,
# but defensive code should use user.get("user_id") or guard after @requires_auth.
from nicegui import ui, app
from apps.web_console_ng.auth.middleware import requires_auth, get_current_user
from apps.web_console_ng.core.database import get_db_pool
from apps.web_console_ng.ui.layout import main_layout
from libs.platform.web_console_auth.permissions import Permission, has_permission


@ui.page("/admin/users")
@requires_auth
@main_layout
async def admin_users_page() -> None:
    user = get_current_user()
    if not has_permission(user, Permission.MANAGE_USERS):
        ui.label("Permission denied").classes("text-red-500")
        return
    # ... page content
```

### Action-Level Authorization with Audit

```python
# ILLUSTRATIVE PSEUDO-CODE — not runnable as-is. Shows pattern only.
# Pattern for page/component layer — auth is enforced HERE, not in service layer.
# user_management.py explicitly delegates auth to callers (see its SECURITY NOTE).
# Service functions assume the caller has verified permissions and provide audit trail.
from libs.platform.web_console_auth.audit_log import AuditLogger


async def on_destructive_action(target_id: str) -> None:
    user = get_current_user()
    audit = AuditLogger(db_pool)

    if not has_permission(user, Permission.MANAGE_USERS):
        await audit.log_action(
            user_id=user["user_id"],
            action="role_change_denied",
            resource_type="user",
            resource_id=target_id,
            outcome="denied",
            details={"target_id": target_id},
        )
        ui.notify("Permission denied", type="negative")
        return

    # Perform action...
    await audit.log_admin_change(
        admin_user_id=user["user_id"],
        action="role_change",
        target_user_id=target_id,
        details={"before": old_role, "after": new_role},
    )
```

### Component Pattern (Established from P6T15)

```python
# Render functions with callbacks — may maintain internal UI state (e.g., timers, async tasks)
def render_feature_table(
    data: list[SomeDataclass],
    *,
    on_action: Callable[[str], Awaitable[None]] | None = None,
) -> Any:
    """Render feature as AG Grid."""
    rows = [{"col": item.field, ...} for item in data]
    grid_options = {"columnDefs": [...], "rowData": rows}
    grid = ui.aggrid(grid_options).classes("w-full")
    # Wire callbacks...
    return grid
```

### Service Initialization Pattern

```python
# ILLUSTRATIVE PSEUDO-CODE — shows calling pattern only (top-level await not valid outside async def).
# Class-based service (TaxLotService pattern)
db_pool = get_db_pool()  # async pool from apps/web_console_ng/core/database.py
service = TaxLotService(db_pool, user=get_current_user())

# Module-level functions (user_management pattern)
db_pool = get_db_pool()
users = await list_users(db_pool)
```

### Navigation Item Pattern

```python
# ILLUSTRATIVE PSEUDO-CODE — `...` is shorthand for remaining permissions.
# Nav items use tuple format: (label, path, icon, None)
# Permission checks are inline conditionals, NOT embedded in the tuple.
# See layout.py lines 157-203 for examples.

nav_items = [
    ...
    ("Tax Lots", "/tax-lots", "receipt_long", None),
    ("Users", "/admin/users", "people", None),
    ("Admin", "/admin", "settings", None),
]

for label, path, icon, _required_role in nav_items:
    # Inline permission check per path
    if path == "/tax-lots" and not has_permission(user, Permission.VIEW_TAX_LOTS):
        continue
    if path == "/admin/users" and not has_permission(user, Permission.MANAGE_USERS):
        continue
    if path == "/admin" and not any(
        has_permission(user, p) for p in (Permission.MANAGE_API_KEYS, ...)
    ):
        continue
```

### Page Registration Pattern

```python
# apps/web_console_ng/pages/__init__.py uses a grouped import block (NOT individual import-as statements)
from apps.web_console_ng.pages import (
    admin,  # noqa: F401
    admin_users,  # noqa: F401 - P6T16
    ...
    tax_lots,  # noqa: F401 - P6T16
    universes,  # noqa: F401 - P6T15
)
```

---

## Navigation Updates

Add entries to `apps/web_console_ng/ui/layout.py` sidebar:

| Page | Route | Icon | Inline Permission Guard |
|------|-------|------|------------------------|
| Tax Lots | `/tax-lots` | `receipt_long` | `has_permission(user, Permission.VIEW_TAX_LOTS)` |
| User Management | `/admin/users` | `people` | `has_permission(user, Permission.MANAGE_USERS)` |

T16.3 modifies the existing `/admin` page (no new nav entry needed).

**REDIRECT ALLOWLIST:** `apps/web_console_ng/auth/redirects.py:ALLOWED_REDIRECT_PATHS` must be updated to include `/tax-lots` and `/admin/users`. Without this, deep-link post-login redirects to these pages will collapse to `/`. Add these paths when implementing T16.1 and T16.2 respectively. **NOTE: Broader inconsistency** — the existing allowlist (`{"/", "/manual", "/kill-switch", "/risk", "/backtest", "/admin", "/mfa-verify"}`) has two issues: (1) **stale entries** — `/manual` and `/kill-switch` have no matching `@ui.page` routes (the actual routes are `/manual-order` and `/circuit-breaker`), so these allowlist entries are dead; (2) **missing routes** — many active routes are absent — e.g., `/alerts`, `/execution-quality`, `/research/universes`, `/circuit-breaker`, `/position-management`, `/manual-order`, etc. (note: the dashboard route is `/`, which IS in the allowlist; the universes route is `/research/universes`, not `/universes`). Deep-link post-login redirects already collapse to `/` for most pages. T16 should add the two new paths (`/tax-lots`, `/admin/users`); fixing the broader allowlist gap across all routes is out of scope but should be filed as a separate issue to avoid growing the inconsistency further.

---

## Backend Integration Points

| Feature | Backend Service | Service Location | Key Functions | DB Tables |
|---------|----------------|-----------------|---------------|-----------|
| Tax Lots | `TaxLotService` | `libs/web_console_services/tax_lot_service.py` | `list_lots()`, `close_lot()`, `update_lot()` | `tax_lots`, `tax_lot_dispositions`, `tax_wash_sale_adjustments`, `tax_user_settings` |
| Wash Sales | `WashSaleDetector` | `libs/platform/tax/wash_sale_detector.py` | `detect_wash_sales(user_id, symbol, sale_date, loss_amount, shares_sold)` → `list[WashSaleMatch]` | `tax_wash_sale_adjustments` |
| Harvesting | `TaxLossHarvester` | `libs/platform/tax/tax_loss_harvesting.py` | `find_opportunities(user_id, current_prices, min_loss_threshold=Decimal("100"))` → `HarvestingRecommendation` | N/A (computed, requires current_prices from market data) |
| Form 8949 | `Form8949Exporter` | `libs/platform/tax/form_8949.py` | Export dispositions to Form 8949 format | `tax_lot_dispositions` |
| Tax Export | `TaxReportRow` | `libs/platform/tax/export.py` | Tax report data model | N/A |
| Users | Module functions | `libs/web_console_services/user_management.py` | `list_users()`, `change_user_role(db_pool, user_id, new_role, admin_user_id, audit_logger, reason)`, `grant_strategy()`, `revoke_strategy()` | `user_roles`, `user_strategy_access` |
| API Keys | Inline SQL + `api_keys.py` | `libs/platform/admin/api_keys.py` | `generate_api_key()`, `is_key_revoked()` | `api_keys` |
| Audit | `AuditLogger` | `libs/platform/web_console_auth/audit_log.py` | `log_admin_change(*, admin_user_id, action, target_user_id, details)`, `log_action(*, user_id, action, resource_type, resource_id, outcome, details)` | `audit_log` |

---

## Spec Updates Required

After implementation, update these spec files:

| Spec | Update Needed |
|------|--------------|
| `docs/SPECS/services/web_console_ng.md` | Add T16.1 tax lots page, T16.2 admin users page, T16.3 revoke/rotate |
| `docs/SPECS/libs/web_console_services.md` | Document TaxLotService and user_management integration points |
| `docs/SPECS/libs/platform.md` | Note T16 usage of tax, admin, audit_log modules |
| `docs/ARCHITECTURE/system_map.config.json` | Add new page routes if tracked |
| `docs/SPECS/DATA_MODELS.md` | Update `user_roles` CHECK constraint documentation (line 1756) to include `'researcher'` after migration 0028 is applied |

---

## Testing Strategy

### Unit Tests

| Test File | Coverage |
|-----------|----------|
| `tests/apps/web_console_ng/components/test_tax_lot_table.py` (**NEW FILE**) | AG Grid rendering, wash sale badge, column formatting |
| `tests/apps/web_console_ng/components/test_user_table.py` (**NEW FILE**) | User list rendering, role badge colors, action button visibility |
| `tests/apps/web_console_ng/components/test_role_selector.py` (**NEW FILE**) | Role dropdown, self-edit guard, last-admin guard |
| `tests/apps/web_console_ng/pages/test_admin_api_keys.py` (**NEW FILE**) | Revoke flow, rotate flow, confirmation dialog, Redis cache invalidation. NOTE: Coordinate with existing `test_admin.py` and `test_admin_coverage.py` to avoid duplicate coverage. |

### Integration Tests

| Test File | Coverage |
|-----------|----------|
| `tests/libs/web_console_services/test_tax_lot_service.py` | CRUD operations, permission enforcement, user scoping |
| `tests/libs/web_console_services/test_user_management.py` | Role changes, session invalidation, strategy grants, bulk ops, audit logging, **`ensure_user_provisioned()` coverage:** (a) new user inserted with default_role, (b) existing user not overwritten (ON CONFLICT DO NOTHING), (c) non-canonical provider roles normalized to "viewer", (d) audit logging on provisioning |
| `tests/libs/platform/admin/test_api_keys.py` | Key generation, hashing, validation, revocation check |

### Auth/Session Tests (steps 0c/0d)

| Test File | Coverage |
|-----------|----------|
| `tests/apps/web_console_ng/auth/test_session_store.py` | `invalidate_redis_sessions_for_user(user_id)` (new method from step 0c) — verifies all Redis sessions for a user are deleted (including multiple concurrent sessions). **Reverse-index lifecycle tests (if using preferred approach from step 0c):** (a) `create_session` SADD session_id to `ng_user_sessions:{user_id}` SET, (b) `rotate_session` SREM old + SADD new session_id atomically, (c) `invalidate_session` SREM session_id from SET, (d) SET TTL matches `absolute_timeout`, (e) stale entries auto-expire. These tests prevent stale-session index drift. NOTE: This is distinct from `invalidate_user_sessions()` in `session_invalidation.py` which only bumps DB `session_version`. |
| `tests/apps/web_console_ng/auth/test_middleware_role_override.py` (**NEW FILE**) | DB role override hook — verifies middleware fetches current DB role and updates session when it differs from provider-derived role; tests role propagation to `X-User-Role` header for subsequent gateway calls |

### Page-Level Tests

| Test File | Coverage |
|-----------|----------|
| `tests/apps/web_console_ng/pages/test_tax_lots.py` (**NEW FILE**) | Permission gating (VIEWER can view, only ADMIN can modify), navigation entry |
| `tests/apps/web_console_ng/pages/test_admin_users.py` (**NEW FILE**) | ADMIN-only access, self-edit prevention, last-admin guard, audit trail |

### RBAC Scenario Tests

| Scenario | Expected |
|----------|----------|
| VIEWER visits `/tax-lots` | Can see lots table (read-only), no close/modify buttons |
| RESEARCHER visits `/tax-lots` | Can see lots + Form 8949 preview (has VIEW_TAX_REPORTS), no download button (lacks EXPORT_DATA), no close/modify buttons |
| OPERATOR visits `/tax-lots` | Can see lots + Form 8949 download (has EXPORT_DATA), no preview (lacks VIEW_TAX_REPORTS), cannot close lots (lacks MANAGE_TAX_LOTS) |
| ADMIN visits `/tax-lots` | Full access including close lots, cost basis method changes, export |
| OPERATOR visits `/admin/users` | Permission denied |
| ADMIN visits `/admin/users` | Full access |
| ADMIN tries to change own role | UI shows warning, action blocked |
| ADMIN tries to downgrade last ADMIN | UI shows error, action blocked |
| OPERATOR visits `/admin` API keys tab | OPERATOR can reach `/admin` via `VIEW_AUDIT` in `ADMIN_PERMISSIONS`, but API Keys tab content shows "Permission denied" (per-tab `MANAGE_API_KEYS` check at `admin.py:184`). VIEWER/RESEARCHER can access `/admin` via direct URL (route exists) but see "Permission denied" UI (page-level `ADMIN_PERMISSIONS` check at `admin.py:144` denies access). The sidebar nav link to `/admin` is hidden for these roles by the inline permission check at `apps/web_console_ng/ui/layout.py:182`. |
| ADMIN clicks revoke on `/admin` API keys | Revoke not yet implemented (TODO in `apps/web_console_ng/pages/admin.py:4`). Key list is user-scoped (`WHERE user_id = %s`). T16.3 scope: self-service revoke only (admin revokes their own keys). Cross-user revoke is explicitly out of scope for T16.3. |

---

## Implementation Order

**Recommended sequence (dependencies flow downward):**

```
Migration 0028: Add RESEARCHER to DB CHECK constraint (prerequisite for T16.2 role dropdown)
  |
T16.3: API Key Revoke/Rotate (lowest risk — modify existing page)
  |
T16.1: Tax Lot Management (new page, backend ready)
  |
T16.2: User Management (new page, most complex — safety guards, bulk ops)
```

**Rationale:**
- Migration 0028 must be applied before T16.2 so RESEARCHER can be assigned via admin UI. NOTE: T16.1 does NOT depend on migration 0028 — T16.1 page auth uses session-derived roles (provider/session/header path), not the DB `user_roles` CHECK constraint. T16.3 and T16.1 can technically proceed in parallel with migration 0028.
- T16.3 is contained within a single existing file — smallest scope, good warm-up
- T16.1 follows established page+component patterns from P6T15
- T16.2 has the most safety-critical logic (self-edit prevention, last-admin guard, session invalidation)

---

## Definition of Done

- [ ] All 3 tasks implemented and functioning
- [ ] Tax lots page at `/tax-lots` with AG Grid, wash sale warnings, harvesting suggestions
- [ ] User management page at `/admin/users` with role assignment, strategy grants, activity log
- [ ] API key revoke/rotate on `/admin` with confirmation dialogs and Redis cache invalidation
- [ ] **All pages role-gated** using `has_permission()` with correct Permission enums
- [ ] **All destructive actions** logged via `AuditLogger.log_admin_change()`
- [ ] **All denied attempts** logged via `AuditLogger.log_action()` with `outcome="denied"` (permission) or `outcome="failed"` (DB error). NOTE: These are the actual stored values per `user_management.py` conventions — distinct from admin UI filter label `"failure"`.
- [ ] **Safety guards:** self-edit prevention, last-admin protection, confirmation dialogs
- [ ] Navigation entries added to layout sidebar with correct icons and inline permission guards
- [ ] Pages registered in `apps/web_console_ng/pages/__init__.py` grouped import block
- [ ] Unit tests > 85% coverage for components and service integration
- [ ] RBAC scenario tests cover all role x page x action combinations
- [ ] Spec files updated (web_console_ng.md, web_console_services.md)
- [ ] `make ci-local` passes
- [ ] Code reviewed and approved via `/review`

---

**Last Updated:** 2026-03-10
**Status:** TASK
