---
id: P6T14
title: "Professional Trading Terminal - Data Services"
phase: P6
task: T14
priority: P2
owner: "@development-team"
state: TASK
created: 2026-01-13
dependencies: [P6T13]
related_adrs: [ADR-0031-nicegui-migration]
planned_adrs: [ADR-0035-sql-explorer-security]
related_docs: [P6_PLANNING.md]
estimated_effort: "8.5-11 days"
features: [T14.1-T14.4]
---

# P6T14: Data Services

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** TASK
**Priority:** P2 (Research Infrastructure)
**Owner:** @development-team
**Created:** 2026-01-13
**Estimated Effort:** 8.5-11 days
**Track:** Track 14 of 18
**Dependency:** P6T13 (Data Infrastructure)

---

## Objective

Build data service UIs: SQL Explorer, data source status, feature store browser, and shadow mode results.

**Success criteria (v1):**
- Safe SQL execution with validation, audit logging, and timeout enforcement (T14.1 — real Parquet data)
- Data source health monitoring with status display and manual refresh (T14.2 — mock data v1)
- Feature store browsing with metadata, lineage, statistics, and sample values (T14.3 — real Alpha158 catalog)
- Shadow mode validation results with accuracy metrics and trend visualization (T14.4 — mock data v1)

> Code snippets are non-normative guidance. Binding requirements are acceptance criteria, security invariants, and DoD items.

**Out of Scope:**
- No modifications to the signal service's `ShadowModeValidator` internals
- No changes to the ETL pipeline or data providers
- No new database migrations — T14.2 (Data Source Status) and T14.4 (Shadow Results) initially return mock data (consistent with P6T13 pattern); T14.1 (SQL Explorer) and T14.3 (Feature Browser) use real data with graceful empty-state fallback
- Trading-execution controls (circuit breaker checks, client_order_id generation, position limits) are unaffected — **feature scope** is purely data services UI
- Rich code editor JavaScript integration (Monaco/CodeMirror CDN) — use NiceGUI's `ui.textarea` with monospace styling; rich editor can be added as a follow-up enhancement

---

## Implementation Order

```
T14.2 Data Source Status    (MEDIUM, no deps, extends existing HealthMonitor/SyncService)
T14.3 Feature Store Browser (MEDIUM, no deps, needs new feature_metadata.py backend)
T14.1 SQL Explorer          (LOW, uses existing sql_validator.py, most complex security)
T14.4 Shadow Mode Results   (LOW, needs new shadow_results_service.py)
```

**Dependency model:** Feature components (T14.1–T14.4) are **logically independent** — they share no business logic, data, or state between each other and can be implemented in any order. However, they share **integration touchpoints** (`apps/web_console_ng/pages/__init__.py` for page registration, `apps/web_console_ng/ui/layout.py` for nav items) which must be merged sequentially to avoid conflicts. A single owner should handle the shared-file additions for all 4 components in a single commit, or components should be committed sequentially (not in parallel branches). The implementation order above is recommended for complexity ramp-up, not dependency-driven.

T14.2 is implemented first as it is the simplest, reusing existing `HealthMonitor` and `DataSyncService` infrastructure. T14.3 follows, introducing the feature metadata module. T14.1 is third because it has the most complex security requirements (RBAC, query validation, timeout, audit logging). T14.4 is last because it requires a new shadow results service with mock data.

---

## Service Ownership: SqlExplorerService vs DataExplorerService

**Decision:** `SqlExplorerService` (T14.1) is a **new, dedicated service** that coexists with the existing `DataExplorerService`. They serve different purposes:

| Service | Purpose | Query Target | Security Model |
|---------|---------|-------------|----------------|
| `DataExplorerService` | Dataset browsing, preview, metadata, CSV export | Mock/preview data | Standard RBAC + rate limiting via `get_rate_limiter()` singleton |
| `SqlExplorerService` | Ad-hoc SQL queries against Parquet-backed DuckDB views | Real Parquet files | Defense-in-depth (AST validation + blocklist + extension lockdown + sandbox) + dedicated `RateLimiter` with `fallback_mode="deny"` |

**Why not merge:** `DataExplorerService` handles high-level dataset operations (list, preview, export) with mock data; `SqlExplorerService` handles raw SQL execution with significantly stricter security controls. Merging would conflate security boundaries and complicate the rate-limiting model (different `fallback_mode` requirements).

**Migration path:** No deprecation needed. `DataExplorerService` continues to serve the existing `/data` pages. `SqlExplorerService` serves the new `/data/sql-explorer` page exclusively. If future consolidation is desired, `SqlExplorerService` can be composed into `DataExplorerService` as a delegate, but this is out of scope for T14.

---

## Prerequisite: Permission Registration

**Before any T14 task implementation,** add the following permissions to `libs/platform/web_console_auth/permissions.py`:

```python
# T14: Data Services
VIEW_FEATURES = "view_features"              # T14.3: Feature store browser
VIEW_SHADOW_RESULTS = "view_shadow_results"  # T14.4: Shadow validation results
```

Role assignments:
```python
Role.RESEARCHER: {
    ...,
    Permission.VIEW_FEATURES,       # T14.3: Researchers browse features for research
    Permission.VIEW_SHADOW_RESULTS, # T14.4: Researchers review model validation
}
Role.OPERATOR: {
    ...,
    Permission.VIEW_FEATURES,       # T14.3: Operators monitor feature health
    Permission.VIEW_SHADOW_RESULTS, # T14.4: Operators review model validation
}
# Role.ADMIN already has all permissions via set(Permission)
```

**Existing permissions used:**
- `QUERY_DATA` (OPERATOR, ADMIN) — for T14.1 SQL Explorer
- `EXPORT_DATA` (OPERATOR, ADMIN) — for T14.1 CSV export
- `VIEW_DATA_SYNC` (VIEWER, OPERATOR, ADMIN) — for T14.2 Data Source Status
- `TRIGGER_DATA_SYNC` (OPERATOR, ADMIN) — for T14.2 manual refresh

**Files to modify:**
- `libs/platform/web_console_auth/permissions.py` — Add `VIEW_FEATURES`, `VIEW_SHADOW_RESULTS` enums and role mappings

**Unit Tests:**
- `VIEW_FEATURES` granted to RESEARCHER, OPERATOR, ADMIN
- `VIEW_SHADOW_RESULTS` granted to RESEARCHER, OPERATOR, ADMIN
- VIEWER does NOT have `VIEW_FEATURES` or `VIEW_SHADOW_RESULTS`
- Existing permission assignments unchanged (regression check)

**Test File:** `tests/libs/platform/web_console_auth/test_permissions_t14.py`

---

## Tasks (4 total)

### T14.2: Data Source Status - MEDIUM PRIORITY

**Goal:** Monitor health of data providers with real-time status, last update timestamps, error rates, and manual refresh.

**Current State Analysis:**
- `HealthMonitor` exists in `libs/data/data_pipeline/health_monitor.py` with `DataSourceHealth` dataclass, `HealthStatus` enum (`OK`, `STALE`, `ERROR`), and category-based staleness thresholds (price: 15min, fundamental: 24hr)
- `HealthMonitorService` exists in `libs/web_console_services/health_service.py` for service-level health (Redis/Postgres connectivity), NOT data source health
- `DataSyncService.get_sync_status()` returns `SyncStatusDTO` with `last_sync`, `row_count`, `validation_status` per dataset
- `health.py` page (373 lines) shows system health but NOT data source-specific health
- Data providers (CRSP, YFinance, Fama-French, Compustat, TAQ) are known via `DATASET_TABLES` in `sql_validator.py` and the `UnifiedDataFetcher` in `libs/data/data_providers/unified_fetcher.py`
- Web console runs as a separate process from signal service — cannot directly access signal service's `HealthMonitor` singleton

**Approach:**

#### Backend: `libs/web_console_services/data_source_status_service.py`

Create a service that aggregates data source health information. Initially returns **mock data** (consistent with P6T13 pattern), upgradeable to real `HealthMonitor` queries or Prometheus scraping when the web console gains access to the signal service's health state.

1. **DTOs** (add to `libs/web_console_services/schemas/data_management.py`):
   ```python
   class DataSourceStatusDTO(BaseModel):
       name: str                          # e.g., "crsp", "yfinance"
       display_name: str                  # e.g., "CRSP Daily", "Yahoo Finance"
       provider_type: str                 # "academic", "free", "commercial"
       dataset_key: str | None            # Key for has_dataset_permission(); None = public (no entitlement)
       status: str                        # "ok", "stale", "error", "unknown"
       last_update: AwareDatetime | None  # Last successful data refresh
       age_seconds: float | None          # Seconds since last update
       row_count: int | None              # Total rows in dataset
       error_rate_pct: float | None       # Error rate over last 24h (0-100)
       error_message: str | None          # Last error message if status is "error"
       is_production_ready: bool          # Whether provider is production-grade
       tables: list[str]                  # SQL table names this source provides (display only)
   ```

2. **Service class:**
   ```python
   class DataSourceStatusService:
       def __init__(
           self,
           redis_client_factory: Callable[[], Awaitable[redis.asyncio.Redis]] | None = None,
       ) -> None:
           """Accept optional Redis client factory for distributed locking.

           **Redis client type: ASYNC (redis.asyncio.Redis)** — consistent with
           SqlExplorerService which uses ASYNC (redis.asyncio.Redis) for its RateLimiter.
           DataSourceStatusService uses async Redis natively because:
           (1) `get_sync_redis_client()` is reserved for legacy sync services only
           (per `apps/web_console_ng/core/dependencies.py` guidelines), and
           (2) all new NG console services should use async Redis from
           `core/redis_ha.py` to avoid connection pool bloat.

           Keeps libs/web_console_services/ independent of apps/web_console_ng/
           by accepting the Redis provider via dependency injection rather than
           importing redis_ha from the app layer directly.
           The factory returns Awaitable[redis.asyncio.Redis], e.g.:
               svc = DataSourceStatusService(redis_client_factory=lambda: get_redis_store().get_master())
           """
           self._redis_client_factory = redis_client_factory

       async def get_all_sources(self, user: Any) -> list[DataSourceStatusDTO]:
           """Return status for registered data sources the user is entitled to.
           NOTE: Returns mock data. Upgrade to real HealthMonitor/Prometheus
           queries when web console shares process with signal service.

           Filters sources by dataset entitlement: users only see sources
           whose datasets they are authorized to access via has_dataset_permission().
           This prevents leaking licensed-source metadata (row counts, freshness)
           to users without dataset entitlements.
           """
           if not has_permission(user, Permission.VIEW_DATA_SYNC):
               raise PermissionError("Permission 'view_data_sync' required")
           all_sources = self._get_mock_sources()
           # Filter by dataset entitlement using dataset_key (not table names).
           # Sources with dataset_key=None are public (no entitlement needed).
           return [
               s for s in all_sources
               if s.dataset_key is None  # Public sources visible to all
               or has_dataset_permission(user, s.dataset_key)
           ]

       async def refresh_source(self, user: Any, source_name: str) -> DataSourceStatusDTO:
           """Trigger manual health check for a specific data source."""
           if not has_permission(user, Permission.TRIGGER_DATA_SYNC):
               raise PermissionError("Permission 'trigger_data_sync' required")
           try:
               _validate_source_name(source_name)
               source = self._get_source_by_name(source_name)
           except (ValueError, KeyError, LookupError):
               raise PermissionError("Source not available")  # Uniform error prevents enumeration
           if source.dataset_key is not None and not has_dataset_permission(user, source.dataset_key):
               raise PermissionError("Source not available")
           # Per-source distributed refresh lock via Redis SETNX (TTL: 60s).
           # Refresh timeout: 45s (< lock TTL). Compare-and-delete via Lua for safe release.
           # Mode-gated: mock mode allows unlocked fallback; real mode requires Redis.
           # DATA_SOURCE_STATUS_MODE env var controls fallback behavior ("mock" default).
           # ... lock acquire, refresh, lock release ...
   ```

   **Permission check pattern:** Use explicit `has_permission()` checks inside method bodies (not `@require_permission` decorator which misidentifies `self` as user on bound methods).


   **Known data sources (hardcoded registry):**
   ```python
   _DATA_SOURCES = [
       {"name": "crsp", "display_name": "CRSP Daily", "provider_type": "academic",
        "is_production_ready": True, "dataset_key": "crsp",
        "tables": ["crsp_daily", "crsp_monthly"]},
       {"name": "yfinance", "display_name": "Yahoo Finance", "provider_type": "free",
        "is_production_ready": False, "dataset_key": None,  # Public: no entitlement needed
        "tables": ["yfinance"]},
       {"name": "compustat", "display_name": "Compustat", "provider_type": "academic",
        "is_production_ready": True, "dataset_key": "compustat",
        "tables": ["compustat_annual", "compustat_quarterly"]},
       {"name": "fama_french", "display_name": "Fama-French Factors", "provider_type": "academic",
        "is_production_ready": True, "dataset_key": "fama_french",
        "tables": ["ff_factors_daily", "ff_factors_monthly"]},
       {"name": "taq", "display_name": "TAQ (Trade & Quote)", "provider_type": "commercial",
        "is_production_ready": True, "dataset_key": "taq",
        "tables": ["taq_trades", "taq_quotes"]},
   ]
   ```

   **Dataset entitlement key:** Each source has a `dataset_key` field that maps to the keys recognized by `has_dataset_permission()` (`crsp`, `compustat`, `taq`, `fama_french`). Sources with `dataset_key=None` are **public** (e.g., YFinance) and visible to all users with `VIEW_DATA_SYNC` — no dataset entitlement check is performed. Licensed sources require `has_dataset_permission(user, dataset_key)` to pass. Entitlement checks MUST use `dataset_key`, NOT the table names in `tables`. The `tables` field is for display purposes only.

   **Source name validation:**
   ```python
   _KNOWN_SOURCES = frozenset(s["name"] for s in _DATA_SOURCES)

   def _validate_source_name(name: str) -> None:
       """Reject unknown source names to prevent enumeration attacks."""
       if name not in _KNOWN_SOURCES:
           raise ValueError(f"Unknown data source: {name}")
   ```

#### Frontend: `apps/web_console_ng/pages/data_source_status.py`

New NiceGUI page at route `/data/sources`.

**Page structure:**
1. **Summary row** — Metric cards showing: Total Sources, Healthy, Stale, Errored
   - Color-coded counts: Healthy=green text, Stale=amber text, Errored=red text
2. **Source table** — AG Grid with columns:
   - Source Name (pinned left, sortable)
   - Provider Type (filterable: academic, free, commercial)
   - Status (color-coded: ok=green, stale=amber, error=red, unknown=gray)
   - Last Update (relative time, e.g., "5m ago")
   - Age (seconds, color-coded by staleness threshold)
   - Row Count (formatted with commas)
   - Error Rate (percentage)
   - Production Ready (boolean icon: checkmark or dash)
   - Datasets (comma-separated list)
3. **Manual refresh button** — Per-source refresh via row action (requires `TRIGGER_DATA_SYNC`)
4. **Auto-refresh timer** — 30s interval with overlap guard

**Permission model:**
- Page visibility: `VIEW_DATA_SYNC` (VIEWER, OPERATOR, ADMIN — per current RBAC in `permissions.py`)
- Manual refresh: `TRIGGER_DATA_SYNC` (OPERATOR, ADMIN only)
- **RBAC note:** RESEARCHER role does NOT have `VIEW_DATA_SYNC` and therefore cannot access T14.2. This is intentional: researchers access data infrastructure through T14.3 (Feature Browser, `VIEW_FEATURES`), not through raw data pipeline status. If researchers need pipeline visibility in the future, add `VIEW_DATA_SYNC` to the RESEARCHER role mapping.

**Status color mapping (client-side):**
```python
_STATUS_COLORS = {
    "ok": "text-green-600",
    "stale": "text-amber-600",
    "error": "text-red-600",
    "unknown": "text-gray-400",
}
```

**Error handling:**
```python
try:
    sources = await source_service.get_all_sources(user)
except PermissionError as e:
    ui.notify(str(e), type="negative")
    return
except Exception:
    logger.exception("data_source_status_failed", extra={
        "service": "DataSourceStatusService",
        "user_id": get_user_id(user),
    })
    ui.notify("Data source status unavailable", type="warning")
    return
```

**Timer cleanup:**
```python
from apps.web_console_ng.utils.session import get_or_create_client_id

timer_sources = ui.timer(30.0, refresh_sources)
_CLEANUP_OWNER_KEY = "data_source_status_timers"

lifecycle = ClientLifecycleManager.get()
client_id = get_or_create_client_id()
if client_id:
    await lifecycle.register_cleanup_callback(
        client_id, timer_sources.cancel, owner_key=_CLEANUP_OWNER_KEY
    )
```

**Overlap guard (per-client):**
```python
_refreshing = False

async def refresh_sources() -> None:
    nonlocal _refreshing
    if _refreshing:
        return
    _refreshing = True
    try:
        sources = await source_service.get_all_sources(user)
        # Update AG Grid
        grid.options["rowData"] = [s.model_dump() for s in sources]
        grid.update()
    except Exception:
        logger.exception("refresh_sources_failed")
    finally:
        _refreshing = False
```

**Files to create:**
- `libs/web_console_services/data_source_status_service.py` — Service with mock data
- `apps/web_console_ng/pages/data_source_status.py` — NiceGUI page

**Files to modify:**
- `libs/web_console_services/schemas/data_management.py` — Add `DataSourceStatusDTO`
- `apps/web_console_ng/pages/__init__.py` — Register new page import

**Acceptance Criteria:**
- [ ] Admin/fully-entitled user sees all 5 data sources (CRSP, YFinance, Compustat, Fama-French, TAQ)
- [ ] Users with limited entitlements see only sources matching their `has_dataset_permission()` grants
- [ ] Status color-coded: ok=green, stale=amber, error=red, unknown=gray
- [ ] Last update timestamp shown as relative time (e.g., "5m ago") — test with `freezegun` or injectable clock to avoid flaky time-boundary assertions
- [ ] Error rate displayed as percentage (0-100)
- [ ] Production readiness indicated (boolean icon)
- [ ] Summary cards: total, healthy, stale, errored counts
- [ ] Manual refresh button per source (requires `TRIGGER_DATA_SYNC`)
- [ ] Source name validated before refresh (reject unknown sources)
- [ ] Auto-refresh every 30s with overlap guard and timer cleanup
- [ ] Timer cleanup uses `get_or_create_client_id()` and keyed `owner_key`
- [ ] Permission check: `VIEW_DATA_SYNC` required for page access
- [ ] Dataset entitlement filtering: users only see sources for datasets they are authorized to access
- [ ] Refresh source checks dataset entitlement before allowing manual refresh
- [ ] Error handling for service unavailability with structured logging
- [ ] "Preview Data" badge/label visibly displayed on page to indicate mock data is in use

**Unit Tests:**
- `DataSourceStatusService.get_all_sources()` returns all 5 sources for admin/fully-entitled user
- `DataSourceStatusService.refresh_source()` with valid source name succeeds
- `DataSourceStatusService.refresh_source()` with unknown source name raises `PermissionError("Source not available")` — same error as unauthorized, to prevent source-name enumeration
- Permission denied for users without `VIEW_DATA_SYNC`
- `TRIGGER_DATA_SYNC` required for `refresh_source()`
- Dataset entitlement filtering: user without TAQ access does not see TAQ source
- Dataset entitlement filtering: admin sees all sources (including licensed)
- Public sources (dataset_key=None, e.g., YFinance) visible to all users with VIEW_DATA_SYNC
- Refresh source denied for users without dataset entitlement for that source
- Mock data contains correct `DataSourceStatusDTO` field types and values
- Constructor injection: service instantiated with `redis_client_factory=mock_factory` for unit tests; `None` for no-Redis fallback
- No-Redis fallback: `refresh_source()` with `redis_client_factory=None` returns mock data without distributed lock (logs warning)
- Source name validation rejects empty string, unknown names
- Source enumeration prevention: unknown and unauthorized sources return identical `PermissionError("Source not available")`
- Refresh lock: concurrent refresh calls for same source return cached status (Redis SETNX prevents duplicate execution)
- Refresh lock lifecycle: lock released after successful refresh (compare-and-delete via Lua)
- Refresh lock lifecycle: lock released after failed refresh (try/finally ensures cleanup)
- Refresh lock lifecycle: lock release failure (Redis blip) does not override successful refresh result — logs warning (mock mode) or error (real mode), TTL provides eventual unlock
- Refresh lock acquire failure (runtime Redis blip): mock mode falls back to unlocked refresh with `redis_lock_acquire_failed_fallback` warning; real mode raises `RuntimeError`. Dedicated test case verifies both mode paths using mock that raises `redis.exceptions.ConnectionError` on `set()`
- Refresh lock timeout: refresh work bounded by `_REFRESH_TIMEOUT_SECONDS` (45s) to stay safely below lock TTL (60s)
- Refresh lock timeout: slow refresh exceeding timeout raises `asyncio.TimeoutError`, lock released in finally
- Source lookup normalization: `ValueError`, `KeyError`, and `LookupError` all produce identical `PermissionError("Source not available")`
- No-Redis fallback (configuration): `refresh_source()` with `redis_client_factory=None` skips distributed lock and logs warning. In multi-worker deployments without Redis, duplicate refresh work is possible but idempotent (mock data returns deterministic results). Acceptable trade-off for v1 mock-data implementation.
  - **Phase-specific policy:** Mock-data mode (v1) allows unlocked fallback because refresh is idempotent and side-effect-free. When real data integration is added (future), Redis availability MUST be required for refresh operations (fail-closed) to prevent duplicate API calls or data corruption. The transition MUST update `refresh_source()` to raise `RuntimeError("Redis required for real-data refresh")` when `redis_client_factory=None` and mock mode is disabled.
- No-Redis fallback (runtime): When Redis is configured (`redis_client_factory` provided) but operationally unavailable, `refresh_source()` catches `redis.exceptions.ConnectionError`/`redis.exceptions.TimeoutError`, logs warning with `redis_lock_fallback_runtime_error`, and proceeds without lock ONLY when `data_mode == "mock"`. When `data_mode == "real"`, the same exception raises `RuntimeError("Redis required for real-data refresh")`. **Mode contract:** `DataSourceStatusService.__init__()` accepts `data_mode: Literal["mock", "real"] = "mock"` parameter. The mode determines lock-failure behavior and is validated at construction time (invalid mode raises `ValueError`). Tests verify: (a) `data_mode="mock"` allows fallback, (b) `data_mode="real"` raises `RuntimeError` on Redis failure, (c) invalid mode raises `ValueError`
- Status color mapping: ok→green, stale→amber, error→red, unknown→gray

**Test File:** `tests/libs/web_console_services/test_data_source_status_service.py`

**Page-level tests** (timer/overlap are UI concerns):
- Timer cleanup registered with owner key
- Overlap guard prevents concurrent refresh
- "Preview Data" badge/label rendered on page (mock data disclosure)

**Page Test File:** `tests/apps/web_console_ng/pages/test_data_source_status.py`

---

### T14.3: Feature Store Browser - MEDIUM PRIORITY

**Goal:** Browse available features for research, with descriptions, lineage, sample values, and statistics.

**Current State Analysis:**
- Alpha158 features defined in `strategies/alpha_baseline/features.py` via Qlib's `Alpha158` handler
- `get_alpha158_features(symbols, start_date, end_date, ...)` returns `pd.DataFrame` with 158 feature columns and `(datetime, instrument)` MultiIndex
- No feature registry, catalog, or metadata module exists anywhere in the codebase
- `libs/data/data_pipeline/` has ETL infrastructure but no feature metadata
- `libs/feature_store/` does NOT exist (confirmed in MEMORY.md)

**Approach:**

#### Backend: `libs/data/feature_metadata.py`

Create a feature catalog module that provides static metadata for Alpha158 features and can compute statistics from actual data. This is a **computation module with optional data access** — metadata is static (no I/O), statistics require calling `get_alpha158_features()` which performs file I/O.

1. **`FeatureMetadata` dataclass:**
   ```python
   @dataclass(frozen=True)
   class FeatureMetadata:
       name: str                    # e.g., "KBAR0"
       category: str                # e.g., "KBAR"
       description: str             # e.g., "(Close - Open) / Open"
       formula: str                 # e.g., "(close - open) / open"
       input_columns: list[str]     # e.g., ["open", "close"]
       lookback_window: int | None  # e.g., 5 (days), None for point-in-time features
       # Implementation note: verify int type against all 158 Alpha158 feature
       # definitions in strategies/alpha_baseline/features.py. If any features
       # use fractional or interval-based windows, change to float | str | None.
       data_type: str               # "float64"
   ```

2. **`FeatureStatistics` dataclass:**
   ```python
   @dataclass
   class FeatureStatistics:
       name: str
       count: int
       mean: float | None
       std: float | None
       min_val: float | None
       q25: float | None
       median: float | None
       q75: float | None
       max_val: float | None
       null_pct: float              # Percentage of null values (0-100)
       computed_at: datetime         # UTC-aware
   ```

3. **`get_feature_catalog()` function (static taxonomy — no I/O):**
   ```python
   def get_feature_catalog() -> list[FeatureMetadata]:
       """Return static metadata for all Alpha158 features.

       Feature categories, descriptions, and formulas based on Qlib's Alpha158
       handler documentation. No I/O — returns hardcoded taxonomy metadata.

       This is the single source of truth for feature descriptions and formulas.
       Use validate_catalog_against_runtime() to detect drift from actual output.
       """
   ```

   **Feature categories (Alpha158):**

   | Category | Count | Description | Input Columns | Lookback |
   |----------|-------|-------------|---------------|----------|
   | KBAR | 5 | Price bar ratios | open, high, low, close | None |
   | KLEN | 1 | Bar length ratio | high, low, open | None |
   | KMID | 1 | Midpoint ratio | open, high, low, close | None |
   | KUP | 2 | Upper shadow ratios | open, high, close | None |
   | KLOW | 2 | Lower shadow ratios | open, low, close | None |
   | KSFT | 2 | Shadow difference ratios | open, high, low, close | None |
   | ROC | 5 | Rate of change | close | 5,10,20,30,60d |
   | MA | 5 | Moving average ratio | close | 5,10,20,30,60d |
   | STD | 5 | Rolling std deviation | close | 5,10,20,30,60d |
   | BETA | 5 | Rolling beta | close | 5,10,20,30,60d |
   | RSQR | 5 | Rolling R-squared | close | 5,10,20,30,60d |
   | RESI | 5 | Rolling residual | close | 5,10,20,30,60d |
   | MAX | 5 | Rolling max ratio | high | 5,10,20,30,60d |
   | MIN | 5 | Rolling min ratio | low | 5,10,20,30,60d |
   | QTLU | 5 | Upper quantile ratio | close | 5,10,20,30,60d |
   | QTLD | 5 | Lower quantile ratio | close | 5,10,20,30,60d |
   | RANK | 5 | Rolling rank | close | 5,10,20,30,60d |
   | RSV | 5 | Relative strength value | close, high, low | 5,10,20,30,60d |
   | IMAX | 5 | Argmax position | high | 5,10,20,30,60d |
   | IMIN | 5 | Argmin position | low | 5,10,20,30,60d |
   | IMXD | 5 | Argmax minus Argmin | high, low | 5,10,20,30,60d |
   | CORR | 5 | Correlation with volume | close, volume | 5,10,20,30,60d |
   | CORD | 5 | Change correlation with volume | close, volume | 5,10,20,30,60d |
   | CNTP | 5 | Count positive return days | close | 5,10,20,30,60d |
   | CNTN | 5 | Count negative return days | close | 5,10,20,30,60d |
   | CNTD | 5 | Count net (pos minus neg) | close | 5,10,20,30,60d |
   | SUMP | 5 | Sum positive returns | close | 5,10,20,30,60d |
   | SUMN | 5 | Sum negative returns | close | 5,10,20,30,60d |
   | SUMD | 5 | Sum net returns | close | 5,10,20,30,60d |
   | VMA | 5 | Volume moving average | volume | 5,10,20,30,60d |
   | VSTD | 5 | Volume rolling std | volume | 5,10,20,30,60d |
   | WVMA | 5 | Weighted volume MA | close, volume | 5,10,20,30,60d |
   | VSUMP | 5 | Volume-weighted sum pos | close, volume | 5,10,20,30,60d |
   | VSUMN | 5 | Volume-weighted sum neg | close, volume | 5,10,20,30,60d |
   | VSUMD | 5 | Volume-weighted sum net | close, volume | 5,10,20,30,60d |
   | **Total** | **158** | | | |

   **Note:** Per-category counts above are derived from Qlib's `Alpha158` handler source. The `validate_catalog_against_runtime()` integration test (in `tests/integration/test_feature_catalog_parity.py`) verifies these counts match actual runtime output. If counts drift, the integration test fails and the static catalog must be updated.

4. **`validate_catalog_against_runtime()` function (runtime validation — requires I/O):**
   ```python
   def validate_catalog_against_runtime(
       runtime_columns: list[str],
   ) -> tuple[list[str], list[str]]:
       """Compare static catalog against actual runtime feature columns.

       Args:
           runtime_columns: Column names from get_alpha158_features() output.

       Returns:
           Tuple of (missing_from_catalog, extra_in_catalog):
           - missing_from_catalog: columns in runtime but not in static catalog
           - extra_in_catalog: features in catalog but not in runtime output

       Use this during integration tests and optionally at page load to log
       warnings about catalog drift. Does NOT modify the static catalog.
       """
   ```

   **Separation of concerns:**
   - `get_feature_catalog()` is the **static taxonomy** — hardcoded descriptions, formulas, and categories. Always available, no I/O.
   - `validate_catalog_against_runtime()` is the **drift detector** — compares static catalog against actual `get_alpha158_features()` output. Called during integration tests and optionally logged as a warning at page load.
   - This split avoids the contradiction of "no I/O" metadata that "derives from runtime output."
   - **CI integration:** Add `validate_catalog_against_runtime()` as a CI integration test (`tests/integration/test_feature_catalog_parity.py`) to alert developers of catalog drift when Alpha158 features evolve. This runs in the integration test suite (not unit tests) since it requires Qlib initialization and data files. Mark with `@pytest.mark.integration` and include a skip condition (`pytest.importorskip("qlib")` + check for data directory) so **regular CI jobs** (which may lack Qlib data) skip gracefully instead of failing. **Dedicated parity job (separate enforcement level):** extend the existing integration pipeline (`.github/workflows/ci-tests-parallel.yml`) to include a scheduled parity job rather than creating a standalone workflow (avoids infra duplication and data-provisioning drift). The parity job (added as part of T14.3) MUST:
     1. Share data provisioning with the existing integration stage (Qlib data from cached artifact)
     2. Run `pytest tests/integration/test_feature_catalog_parity.py -m integration --no-header -rN`
     3. Use **non-skip semantics** (applies ONLY to this dedicated parity job, NOT regular integration runs): if Qlib data provisioning succeeds but the parity test is missing or skipped, the job MUST fail. The distinction is: regular CI may skip (data not provisioned), but the dedicated parity job guarantees data provisioning and therefore forbids skips. Implementation: run `pytest --co -q tests/integration/test_feature_catalog_parity.py` first to assert at least 1 test is collected (`wc -l >= 1`), then run the actual test with `--tb=short -rN --junitxml=parity-report.xml` and assert exit code 0. **Additionally**, enforce non-skip semantics using JUnit XML output (built-in to pytest, no extra plugins required): parse `parity-report.xml` and validate that `testsuite` attributes show `tests >= 1` and `skipped == 0`. This uses pytest's built-in `--junitxml` flag, avoiding dependency on third-party plugins like `pytest-json-report`. Example validation: `python3 -c "import xml.etree.ElementTree as ET; r=ET.parse('parity-report.xml').getroot(); ts=r if r.tag=='testsuite' else r.find('.//testsuite'); assert ts is not None, 'No testsuite element found in JUnit XML'; assert int(ts.get('tests',0))>=1 and int(ts.get('skipped',0))==0, f'Non-skip check failed: tests={ts.get(\"tests\")}, skipped={ts.get(\"skipped\")}'"`. Note: handles both `<testsuites><testsuite>` (multi-suite) and `<testsuite>` (single-suite) root shapes that pytest may produce. This approach is deterministic across pytest versions and plugin configurations
     4. Include a weekly cron trigger in `.github/workflows/ci-tests-parallel.yml` to catch Alpha158 drift between releases

5. **`compute_feature_statistics()` function:**
   ```python
   def compute_feature_statistics(
       features_df: pd.DataFrame,
       feature_names: list[str] | None = None,
   ) -> list[FeatureStatistics]:
       """Compute descriptive statistics for features.

       Args:
           features_df: DataFrame from get_alpha158_features() with
                        (datetime, instrument) MultiIndex and feature columns.
           feature_names: Optional subset of features to compute stats for.
                         If None, computes for all columns.

       Returns:
           List of FeatureStatistics, one per feature column.
       """
   ```

   Uses `pd.DataFrame.describe()` for efficient batch computation. Null percentage computed via `df.isnull().mean() * 100`. Statistics are computed **on-demand** per page interaction (not cached — feature data changes daily).

6. **`get_sample_values()` function:**
   ```python
   def get_sample_values(
       features_df: pd.DataFrame,
       feature_name: str,
       n_samples: int = 10,
   ) -> list[dict[str, Any]]:
       """Return sample rows for a single feature.

       Returns list of dicts with keys: date, symbol, value.
       Samples from the most recent available date to show current values.
       """
   ```

#### Frontend: `apps/web_console_ng/pages/feature_browser.py`

New NiceGUI page at route `/data/features`.

**Page structure:**
1. **Feature catalog table** (full width, upper section):
   - AG Grid with columns: Name, Category, Description, Lookback Window
   - `apply_compact_grid_options()` for consistent styling
   - Category filter dropdown (all / specific category)
   - Search input for feature name filtering (client-side AG Grid filter)
   - Sortable by name, category, window
   - Row selection triggers detail panel update

2. **Feature detail panel** (below table, shown on feature selection):
   - **Metadata section:**
     - Feature name, category, description, formula
     - Input columns listed as chips/tags
     - Lookback window (or "Point-in-time" if None)
   - **Lineage diagram** (text-based, not graphical):
     ```
     Input Columns: [open, high, low, close]
       → Qlib Alpha158 Handler (strategies/alpha_baseline/features.py)
         → Feature: KBAR0
           Formula: (close - open) / open
     ```
   - **Sample values** (last 10 rows from most recent data):
     - Table showing: date, symbol, value
     - Loaded on-demand when feature is selected via `asyncio.to_thread()`
     - Loading spinner shown during computation
   - **Statistics card:**
     - Count, Mean, Std, Min, Q25, Median, Q75, Max, Null%
     - Loaded on-demand when feature is selected
     - Color-coded Null%: < 5% green, 5-20% amber, > 20% red

3. **Data loading:**
   - Feature catalog (metadata) loads immediately (static, no I/O)
   - Feature data loaded **once per client session** via `get_alpha158_features()`, then persisted in `app.storage.client["feature_cache"]` so it survives page navigations within the same browser session. On page re-visit, the handler checks `app.storage.client` first and skips the I/O load if data is already cached. Each browser tab/client gets its own cache (NiceGUI scopes `app.storage.client` per WebSocket connection). Subsequent feature selections compute stats/samples from the cached DataFrame — no repeated I/O or Qlib re-initialization. This is NOT shared across users/processes. Note: `app.storage.client` stores pickled Python objects server-side, so large DataFrames are acceptable. **Memory monitoring:** During T14.3 rollout, monitor server memory metrics. If many concurrent users cause memory pressure, consider adding a per-session LRU eviction policy or shorter cache TTL.
   - Statistics and sample values computed on feature selection from cached DataFrame
   - Data source: `get_alpha158_features()` for recent date range
   - **Performance limits:** Load only last 30 calendar days and 5 representative symbols to avoid excessive data loading. These limits are enforced as module-level constants: `_MAX_CACHE_DAYS = 30`, `_MAX_CACHE_SYMBOLS = 5`, `_CACHE_TTL_SECONDS = 1800` (30 minutes). Symbols selected from universe if available, otherwise hardcoded sample: `["AAPL", "MSFT", "GOOGL", "AMZN", "META"]`. Note: 5 symbols is a minimum for initial implementation; for more robust statistical properties (stationarity, skew), consider allowing users to select a sector/index subset or expanding to 10-20 symbols in a follow-up.
   - **Lookback padding:** Many Alpha158 features (MA, STD, ROC, etc.) have lookback windows up to 60 days. The `get_alpha158_features()` call MUST request data starting at least `max_lookback` days (60) before the display window start date. This ensures the 30-day display window has fully populated values (no NaN-only features). Example: to display features for "last 30 days", request features from `today - 90 days` to `today`, then slice the final DataFrame to the last 30 days.
   - Use `asyncio.to_thread()` for the initial `get_alpha158_features()` call (blocking I/O)
   - **Overlap guard:** Loading flag prevents concurrent data loads on rapid feature selection changes
   - **Cache cleanup:** Register a cleanup callback via `ClientLifecycleManager` to purge `app.storage.client["feature_cache"]` on client disconnect. This prevents unbounded server-side memory growth from long-lived sessions. Pattern: `await lifecycle_manager.register_cleanup_callback(client_id, lambda: app.storage.client.pop("feature_cache", None), owner_key="feature_cache")`
   - **Lazy TTL eviction (secondary safety net):** Cache entries include a `cached_at` timestamp. On access, entries older than 30 minutes are evicted and recomputed. This handles ungraceful disconnects (process crash, network drop) where `ClientLifecycleManager` callbacks are bypassed.
   - **Memory monitoring:** Log estimated cache size (bytes) at load time via `df.memory_usage(deep=True).sum()` on the DataFrame. Emit as structured log field `cache_size_bytes` and optionally as a Prometheus gauge (`feature_cache_size_bytes{client_id}`). This provides observability for memory pressure under concurrent sessions without requiring explicit byte-size caps in v1.
   - **Implementation gate — cache load test (MUST pass before enabling caching):** Before enabling `app.storage.client` caching in production, run a manual load test: simulate N concurrent browser sessions (minimum N=10) each loading the feature cache, and verify: (a) server RSS stays below 80% of container memory limit, (b) no serialization errors in logs, (c) `ClientLifecycleManager` cleanup fires correctly on disconnect. If any check fails, fall back to on-demand computation (no caching) until the issue is resolved. **Hard per-session size cap:** `_MAX_CACHE_BYTES = 50 * 1024 * 1024` (50 MB). If `df.memory_usage(deep=True).sum() > _MAX_CACHE_BYTES`, skip caching and compute on demand with a warning log. Note: `df.memory_usage(deep=True).sum()` accurately accounts for underlying array buffers and string data, unlike `sys.getsizeof()` which only returns the object header size for DataFrames.

**Permission model:**
- Page visibility: `VIEW_FEATURES` (RESEARCHER, OPERATOR, ADMIN)

**Error handling (initial page load — populates cache):**
```python
try:
    cached_features_df = await asyncio.to_thread(
        get_alpha158_features,
        symbols=sample_symbols,
        start_date=start_date,
        end_date=end_date,
    )
except FileNotFoundError:
    ui.notify("Feature data not available — run ETL pipeline first", type="warning")
    cached_features_df = None
except Exception:
    logger.exception("feature_data_load_failed", extra={"symbols": sample_symbols})
    ui.notify("Feature data unavailable", type="warning")
    cached_features_df = None
```

**Error handling (on feature selection — uses cached data):**
```python
if cached_features_df is None:
    ui.notify("No feature data loaded", type="info")
    return
try:
    # Offload CPU-bound compute to thread to avoid blocking NiceGUI event loop
    stats = await asyncio.to_thread(compute_feature_statistics, cached_features_df, [selected_feature])
    samples = await asyncio.to_thread(get_sample_values, cached_features_df, selected_feature)
except Exception:
    logger.exception("feature_statistics_failed", extra={
        "feature": selected_feature,
    })
    ui.notify("Feature statistics unavailable", type="warning")
```

**Empty state:**
- If no feature data files exist (ETL never run): show informational message "No feature data available. Run the ETL pipeline to generate features." with no statistics or samples panel.

**Files to create:**
- `libs/data/feature_metadata.py` — Feature catalog, metadata dataclasses, statistics computation
- `apps/web_console_ng/pages/feature_browser.py` — NiceGUI page

**Files to modify:**
- `apps/web_console_ng/pages/__init__.py` — Register new page import

**Acceptance Criteria:**
- [ ] All Alpha158 features listed with name, category, description (count from static taxonomy; runtime parity validated via integration test)
- [ ] Feature catalog table in AG Grid with sorting and filtering
- [ ] Category filter dropdown filters feature list (all + 35 categories)
- [ ] Search input filters by feature name (case-insensitive)
- [ ] Feature selection shows detail panel with metadata
- [ ] Feature lineage displayed (text-based: input columns → handler → feature → formula)
- [ ] Sample values loaded on demand (last 10 rows from recent data, 5 symbols max)
- [ ] Feature statistics computed on demand (count, mean, std, min, q25, median, q75, max, null%)
- [ ] Null% color-coded: < 5% green, 5-20% amber, > 20% red
- [ ] Loading spinner during statistics/sample computation
- [ ] `asyncio.to_thread()` used for blocking `get_alpha158_features()` call
- [ ] Permission check: `VIEW_FEATURES` required for page access
- [ ] Empty state: informational message when no feature data available
- [ ] `FileNotFoundError` handled gracefully (ETL not yet run)

**Unit Tests:**
- `get_feature_catalog()` returns exactly 158 features (pinned from Alpha158 specification; update this count explicitly when Alpha158 evolves)
- All 35 categories present in static catalog (KBAR, KLEN, KMID, KUP, KLOW, KSFT, ROC, MA, STD, BETA, RSQR, RESI, MAX, MIN, QTLU, QTLD, RANK, RSV, IMAX, IMIN, IMXD, CORR, CORD, CNTP, CNTN, CNTD, SUMP, SUMN, SUMD, VMA, VSTD, WVMA, VSUMP, VSUMN, VSUMD)
- **Note:** Runtime parity validation (`validate_catalog_against_runtime()` vs actual `get_alpha158_features()` output) belongs in integration tests, not unit tests, to avoid Qlib init + data file dependencies in CI
- Each feature has non-empty `name`, `description`, `formula`, `category`
- Each feature has non-empty `input_columns` list
- Rolling features (ROC, MA, STD, etc.) have `lookback_window` set to correct value
- Point-in-time features (KBAR, KLEN, etc.) have `lookback_window = None`
- `compute_feature_statistics()` computes correct stats for known synthetic input
- `compute_feature_statistics()` handles empty DataFrame (returns zero count, None values)
- `compute_feature_statistics()` handles all-null column (null_pct=100)
- `compute_feature_statistics()` with `feature_names` subset returns only requested features
- `get_sample_values()` returns correct number of samples (capped at `n_samples`)
- `get_sample_values()` returns dicts with keys: date, symbol, value
- `get_sample_values()` with empty DataFrame returns empty list
- Category filtering returns only features in selected category
- Feature name search filters case-insensitively
- `validate_catalog_against_runtime()` returns empty lists when catalog matches given columns (tested with synthetic column list, no I/O)
- `validate_catalog_against_runtime()` detects missing features (given columns not in catalog)
- `validate_catalog_against_runtime()` detects extra features (catalog entries not in given columns)
- Feature data caching: uses `app.storage.client` to persist DataFrame across page navigations
- Feature cache cleanup: `ClientLifecycleManager` purges `feature_cache` on client disconnect
- Feature lookback padding: request date window padded by max lookback (60 days) to avoid NaN-only features
- **Note:** These unit tests use synthetic column lists. Runtime parity validation against actual `get_alpha158_features()` output is in integration tests only (see Testing Strategy section)

**Test File:** `tests/libs/data/test_feature_metadata.py`

---

### T14.1: SQL Explorer (Validated) - LOW PRIORITY

**Goal:** Safe SQL execution for power users with query validation, AG Grid results, CSV export, and audit logging.

**Current State Analysis:**
- `SQLValidator` exists in `libs/web_console_services/sql_validator.py` (222 lines) with:
  - Single SELECT-only validation via `sqlglot` parsing (DuckDB dialect)
  - **Known limitation (MUST FIX in T14.1):** Semicolon blocking uses blunt string check (`if ";" in query`) which rejects valid queries with semicolons inside string literals (e.g., `WHERE col = 'val;ue'`). This string-level check **MUST be removed** and replaced with `sqlglot`'s `len(expressions) != 1` check which handles this case correctly via AST parsing. `sqlglot.parse()` already splits on statement boundaries, making the string check both redundant and harmful (false positives). **Required test cases:** (a) multi-statement `SELECT 1; SELECT 2` rejected, (b) semicolon inside string literal `WHERE col = 'val;ue'` accepted, (c) semicolon in comment `SELECT 1 -- comment;` accepted, (d) trailing semicolon `SELECT 1;` accepted (single statement).
  - DML/DDL blocking (INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, MERGE, COPY, LOAD, ATTACH, PRAGMA, SET)
  - Schema-qualified table blocking (prevents cross-schema access)
  - Blocked function list (45+ patterns covering file I/O, remote access, system functions with wildcard support)
  - Dataset-scoped table whitelist (`DATASET_TABLES`: crsp, compustat, fama_french, taq)
  - Row limit enforcement (`enforce_row_limit()`)
- `DuckDBCatalog` exists in `libs/duckdb_catalog.py` with context manager, `register_table()`, parameterized `query()`, and read-only mode
- `get_read_only_connection()` in `libs/web_console_services/duckdb_connection.py` creates secure read-only DuckDB connections with `enable_external_access=false`
- `QUERY_DATA` permission exists (OPERATOR, ADMIN roles)
- `EXPORT_DATA` permission exists (OPERATOR, ADMIN roles)
- Existing query editor in `data_management.py` uses `ui.textarea` with monospace font
- No query audit logging exists anywhere in the codebase

**Approach:**

#### Backend: Query Execution & Audit

**Query execution flow (6 steps):**
1. User types SQL query and selects target dataset
2. `SQLValidator.validate(query, dataset)` validates safety (whitelist + blocklist checks)
3. Sensitive table check (defense-in-depth, see below)
4. `SQLValidator.enforce_row_limit(query, max_rows)` applies row limit cap
5. Query executed via `duckdb.connect()` with Parquet-backed views in a thread with `asyncio.wait_for()` timeout
6. Results returned as Polars DataFrame → AG Grid

**Sensitive table protection (defense-in-depth):**
The `SQLValidator` already uses a whitelist approach via `DATASET_TABLES` — only tables listed in the whitelist are allowed. Sensitive tables (users, api_keys, secrets) are NOT in the whitelist and are therefore **blocked by default**. As a belt-and-suspenders measure, add an explicit blocklist check to ensure sensitive tables are never accessible even if the whitelist is misconfigured:

```python
# Exact names blocked unconditionally
_SENSITIVE_TABLES_EXACT = frozenset({
    "users", "api_keys", "secrets", "sessions", "credentials",
    "auth_tokens", "password_hashes",
})
# Prefixes blocked via case-insensitive startswith (catches auth_users,
# user_roles, secret_keys, etc.)
_SENSITIVE_TABLE_PREFIXES = ("user", "auth", "secret", "credential", "password", "session", "api_key")

def _check_sensitive_tables(tables: list[str]) -> None:
    """Raise SensitiveTableAccessError if any sensitive table is referenced.
    Defense-in-depth: whitelist already blocks these, but explicit check
    ensures safety even if DATASET_TABLES is misconfigured.
    Uses both exact match and case-insensitive prefix matching to catch
    variants like auth_users, user_roles, secret_keys.
    Raises SensitiveTableAccessError directly — no return-value contract —
    so callers and exception handlers don't need string-matching heuristics."""
    blocked: set[str] = set()
    for t in tables:
        t_lower = t.lower()
        if t_lower in _SENSITIVE_TABLES_EXACT:
            blocked.add(t_lower)
        elif any(t_lower.startswith(p) for p in _SENSITIVE_TABLE_PREFIXES):
            blocked.add(t_lower)
    if blocked:
        raise SensitiveTableAccessError(
            f"Access to restricted tables denied: {', '.join(sorted(blocked))}"
        )
```

**Query audit logging:**
All executed queries are logged via structured logging with full context. No new DB table is needed initially — structured JSON logs are sufficient for audit compliance and can be queried via log aggregation tools (ELK, CloudWatch, etc.).

**Error message redaction:** Parser/validation errors from `sqlglot` can include fragments of the original SQL, which may contain sensitive literals. Error messages are classified into canonical codes before logging:

```python
_ERROR_CODES = {
    "validation_error": "Query failed validation",
    "authorization_denied": "Dataset access denied",
    "security_blocked": "Restricted table access denied",
    "rate_limited": "Query rate limit exceeded",
    "timeout": "Query execution timed out",
    "concurrency_limit": "Too many concurrent queries",
    "error": "Query execution failed",
}

def _safe_error_message(status: str, raw_error: str | None) -> str:
    """Return a safe, canonical error message for logging/UI display.
    Never includes raw SQL text — parser errors that may contain query
    fragments are replaced with the canonical code message.
    Catch-all "Internal execution error" prevents leaking DuckDB/Polars
    internals (schema details, file paths) to audit trail or UI."""
    return _ERROR_CODES.get(status, "Internal execution error")
```

```python
def _fingerprint_query(sql: str) -> str:
    """Generate a normalized query fingerprint with literals replaced.

    Replaces string literals with '?' and numeric literals with '?'
    to prevent sensitive data (PII, secrets) from appearing in logs.
    Uses sqlglot for safe AST-based normalization.

    Example: "SELECT * FROM t WHERE name = 'John'" -> "SELECT * FROM t WHERE name = ?"
    """
    try:
        parsed = sqlglot.parse_one(sql, read="duckdb")
        for literal in parsed.find_all(exp.Literal):
            literal.replace(exp.Placeholder())
        return parsed.sql(dialect="duckdb")
    except Exception:
        # Safe fallback: never emit any fragment of the original SQL.
        # Regex-based partial sanitization was considered but rejected because
        # secrets in comments, unusual literal forms, or dialect-specific
        # constructs can survive basic regex stripping.
        return "<unparseable query>"


def _log_query(
    user: Any,
    dataset: str,
    original_query: str,
    executed_query: str | None,
    row_count: int,
    execution_ms: int,
    status: str,
    error_message: str | None,
) -> None:
    """Log query execution for audit trail.

    By default, logs ONLY the fingerprinted (redacted) query — raw SQL
    is never written to standard logs. Raw query text is gated behind
    an explicit _AUDIT_LOG_RAW_SQL flag that must be enabled for a
    separate restricted audit sink only.

    Security: Literals (strings, numbers) are replaced with '?' in the
    fingerprint to prevent PII/secret exposure in logs.
    """
    fingerprint = _fingerprint_query(original_query) if original_query else None
    import uuid as _uuid
    query_id = str(_uuid.uuid4())  # Stable correlation ID for this request
    log_extra: dict[str, Any] = {
        "query_id": query_id,  # Correlates all log entries for this request
        "user_id": get_user_id(user),
        "user_role": user.get("role") if isinstance(user, dict) else getattr(user, "role", None),
        "dataset": dataset,
        "query_fingerprint": fingerprint,  # Redacted: literals replaced with ?
        "query_modified": original_query != executed_query if executed_query else False,
        "row_count": row_count,
        "execution_ms": execution_ms,
        "status": status,  # "success", "validation_error", "authorization_denied", "security_blocked", "rate_limited", "concurrency_limit", "timeout", "error"
        "error_message": _safe_error_message(status, error_message),  # Canonical message, never raw SQL
        "timestamp": datetime.now(UTC).isoformat(),
    }
    # Raw SQL logged to DEDICATED audit logger (separate from standard logger)
    # to ensure raw queries never leak into standard log pipeline
    if _AUDIT_LOG_RAW_SQL:
        max_query_len = 2000
        raw_extra = {
            **log_extra,
            "original_query": original_query[:max_query_len] if original_query else None,
            "executed_query": executed_query[:max_query_len] if executed_query else None,
        }
        _audit_logger.info("sql_query_executed_raw", extra=raw_extra)
    logger.info("sql_query_executed", extra=log_extra)
```

**Raw SQL audit flag:**
```python
import os

_AUDIT_LOG_RAW_SQL = os.getenv("SQL_EXPLORER_AUDIT_RAW_SQL", "false").lower() == "true"

# Production guardrail: raw SQL audit logging is forbidden in production
# by default. It can only be enabled with an explicit emergency override.
_app_env = os.getenv("APP_ENV", "").lower()
_AUDIT_RAW_SQL_EMERGENCY_OVERRIDE = os.getenv(
    "SQL_EXPLORER_AUDIT_RAW_SQL_EMERGENCY_OVERRIDE", "false"
).lower() == "true"
if _AUDIT_LOG_RAW_SQL and _app_env == "production" and not _AUDIT_RAW_SQL_EMERGENCY_OVERRIDE:
    raise RuntimeError(
        "SQL_EXPLORER_AUDIT_RAW_SQL=true is forbidden when APP_ENV=production. "
        "Raw SQL may contain PII or sensitive strategy logic. "
        "If this is a genuine security investigation, set "
        "SQL_EXPLORER_AUDIT_RAW_SQL_EMERGENCY_OVERRIDE=true alongside it "
        "(requires deployment-lead approval documented in deployment log)."
    )

# Dedicated audit logger — separate from standard application logger.
# Must be configured with its own handler/sink (e.g., encrypted file, SIEM)
# to prevent raw SQL from leaking into standard log pipeline.
_audit_logger = logging.getLogger("sql_explorer.audit")

if _AUDIT_LOG_RAW_SQL:
    logger.warning(
        "sql_explorer_raw_sql_audit_enabled",
        extra={"warning": "Raw SQL logging is enabled. Ensure logs are directed "
               "to a restricted, encrypted audit sink only. Raw queries may "
               "contain PII or sensitive literals.",
               "emergency_override": _AUDIT_RAW_SQL_EMERGENCY_OVERRIDE},
    )
```

**Security note:** `_AUDIT_LOG_RAW_SQL` defaults to `false` and MUST remain `false` in all standard deployments. Raw queries may contain PII, proprietary strategy logic, or sensitive literals. Only enable when ALL of the following conditions are met: (1) logs are directed to a separate restricted audit sink, (2) sink access is limited to authorized security personnel only, (3) sink storage is encrypted at rest, (4) **log rotation/retention policy is configured** — if the sink is file-based, configure aggressive rotation (e.g., 7-day retention, 100MB max per file) in `logging.yaml` or equivalent, as raw SQL logs can grow rapidly under active use. A startup warning is logged when this flag is enabled. Standard application logs never contain raw SQL text — fingerprinted queries are sufficient for most audit and debugging purposes.

**Timeout enforcement with connection interrupt:**
DuckDB does not have a native query timeout. Use `asyncio.wait_for()` with `asyncio.to_thread()`, and **interrupt the connection** on timeout to prevent runaway worker threads:

```python
_DEFAULT_TIMEOUT_SECONDS = 30
_MAX_TIMEOUT_SECONDS = 120
_DEFAULT_MAX_ROWS = 10_000
_MAX_ROWS_LIMIT = 50_000
_MAX_CONCURRENT_QUERIES = 3  # Cap concurrent executions per service instance

# Server-side rate limiting — matches DataExplorerService limits to prevent
# high-frequency abuse that concurrency caps alone cannot catch.
_QUERY_RATE_LIMIT = 10   # max queries per minute per user
_EXPORT_RATE_LIMIT = 5   # max exports per hour per user
_rate_limiter: RateLimiter | None = None  # initialized lazily at page load

_active_queries = 0
_active_queries_lock = asyncio.Lock()

async def _execute_query_with_timeout(
    conn: duckdb.DuckDBPyConnection,
    sql: str,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> pl.DataFrame:
    """Execute SQL query with timeout and connection interrupt.

    On timeout, calls conn.interrupt() to cancel the running DuckDB query,
    then closes the connection. This prevents runaway worker threads from
    consuming resources after the user-facing request has timed out.

    Raises asyncio.TimeoutError if query exceeds timeout.
    Raises ConcurrencyLimitError if concurrent query limit is exceeded.
    Timeout clamped to _MAX_TIMEOUT_SECONDS regardless of user input.
    """
    global _active_queries
    acquired_slot = False
    async with _active_queries_lock:
        if _active_queries >= _MAX_CONCURRENT_QUERIES:
            raise ConcurrencyLimitError("Too many concurrent queries. Please try again later.")
        _active_queries += 1
        acquired_slot = True

    clamped_timeout = min(timeout_seconds, _MAX_TIMEOUT_SECONDS)
    try:
        def _run() -> pl.DataFrame:
            result = conn.execute(sql)
            df = result.pl()
            # Server-side cell count cap — fail-fast before returning to UI.
            # A query with 100 columns × 20,000 rows = 2M cells would pass
            # the row limit but crash the browser. Check total cells here.
            _MAX_CELLS = 1_000_000
            cell_count = len(df) * len(df.columns)
            if cell_count > _MAX_CELLS:
                raise ValueError(
                    f"Result too large: {cell_count:,} cells exceeds limit of "
                    f"{_MAX_CELLS:,}. Add filters or reduce columns."
                )
            return df
        return await asyncio.wait_for(
            asyncio.to_thread(_run),
            timeout=clamped_timeout,
        )
    except asyncio.TimeoutError:
        # Interrupt the DuckDB query to stop the worker thread
        try:
            conn.interrupt()
        except Exception:
            logger.warning("duckdb_interrupt_failed", extra={"timeout": clamped_timeout})
        raise
    finally:
        if acquired_slot:
            async with _active_queries_lock:
                _active_queries -= 1
```

**Concurrency cap:** `_MAX_CONCURRENT_QUERIES = 3` limits simultaneous DuckDB queries **per process** to prevent resource contention under heavy load. Excess requests raise `ConcurrencyLimitError` (a `RuntimeError` subclass) for deterministic audit classification as `"concurrency_limit"`. In a multi-worker deployment (e.g., Uvicorn `--workers 4`), the aggregate limit is `workers * 3`. Per-process limiting is sufficient for initial deployment; if a strict global cap is needed in the future, move the counter to Redis.

**Memory sizing:** Total reserved memory = `workers * _MAX_CONCURRENT_QUERIES * SQL_EXPLORER_MAX_MEMORY_MB`. Default: `4 * 3 * 512MB = 6GB`. Operators can tune via `SQL_EXPLORER_MAX_MEMORY_MB` env var. On memory-constrained hosts, reduce to 256MB or lower the concurrency cap.

**DuckDB timeout/interrupt validation:** The `conn.interrupt()` + `asyncio.to_thread()` pattern crosses thread boundaries. During T14.1 implementation, add a validation test that confirms: (1) `conn.interrupt()` actually stops a long-running query in a separate thread, (2) the worker thread terminates cleanly after interrupt, (3) no thread leaks under repeated timeout scenarios. If interrupt proves unreliable under load, fall back to per-query subprocess isolation (create short-lived `duckdb.connect()` in subprocess, kill on timeout).

**DuckDB connection strategy:**

**Relationship to `DuckDBCatalog`:** The existing `libs/duckdb_catalog.py` provides a general-purpose analytics interface (`DuckDBCatalog`) for internal use. The SQL Explorer intentionally does NOT use `DuckDBCatalog` because it requires security hardening that `DuckDBCatalog` was not designed for: (1) extension lockdown (`enable_extension_autoloading/loading=false`), (2) resource limits (`max_memory`, `threads`), (3) per-dataset view scoping, and (4) table name identifier validation against injection. Using `DuckDBCatalog` directly would require adding these security features to a general-purpose class that other callers don't need. Instead, SQL Explorer creates purpose-built connections. If `DuckDBCatalog` is later extended with a secure factory method, SQL Explorer should adopt it. See ADR mentioned below.

**Constraints that eliminate simpler approaches:**
- `duckdb.connect(read_only=True)` fails for in-memory databases (`CatalogException`)
- `SET enable_external_access = false` breaks queries against Parquet-backed views because DuckDB views are **lazy** — `read_parquet()` is executed at query time, not at view creation time. Lockdown after view creation therefore blocks all data reads.
- DuckDB does not support prepared parameters (`?`) in DDL statements like `CREATE VIEW ... read_parquet(?)` (Binder Error).

**Chosen approach: Validated Parquet-backed views (no runtime lockdown)**

**ADR Required:** This security architecture decision (validator-based containment without DuckDB runtime lockdown) is a non-trivial security boundary change. Create `ADR-0035-sql-explorer-security` documenting: (1) why `enable_external_access=false` is incompatible with lazy views, (2) why validator + extension lockdown provides equivalent protection for the operator-only audience, (3) conditions under which process-level sandboxing becomes mandatory. Reference this ADR from the task metadata.

Security is enforced by **three independent layers** (defense-in-depth), without runtime `enable_external_access` lockdown:
- **Layer 1 (Parse-time):** `SQLValidator.validate()` blocks all DML/DDL, non-whitelisted tables, and all 45+ blocked functions (`read_parquet`, `read_csv`, `glob`, `parquet_scan`, `sqlite_scan`, `http_*`, `s3_*`, etc.) via pattern matching. This prevents users from calling filesystem/network functions in queries.
- **Layer 2 (Pre-execution):** `_check_sensitive_tables()` rejects system tables (users, api_keys, secrets) even if the whitelist is misconfigured.
- **Layer 3 (Runtime containment):** DuckDB connections are configured with `SET enable_extension_autoloading = false` and `SET enable_extension_loading = false` immediately after `duckdb.connect()` to prevent loading extensions that could bypass the validator. Additionally, `_create_query_connection()` only registers views for the specific dataset being queried, limiting exposure scope per query.

**Mandatory deployment constraint (v1) — canonical filesystem + network policy:** The web console process hosting SQL Explorer MUST run in an environment with:
- **No outbound internet access** (egress-deny for external networks). **Important caveat:** the web console container requires internal network access to Redis and Postgres for rate limiting (fail-closed), health checks, distributed refresh locks, and session storage. Therefore `network_mode: "none"` is NOT appropriate for the web_console container — it would break these required dependencies. Instead, in production, web_console must be attached **exclusively** to `internal: true` Compose networks (which block external egress but allow internal inter-container communication). Ingress traffic reaches web_console via a reverse proxy that bridges the external and internal networks. This is the only reliable Compose-native egress control without host-level firewall rules.
- **Read-only root filesystem** with explicit exceptions: `data/` is **read-only** (SQL Explorer only reads Parquet files), `/tmp` is **writable** (DuckDB scratch space, probe files), Python library paths are read-only
- All other directories (`libs/`, `apps/`, `config/`, etc.) are **read-only**

This provides hard isolation at the OS/container level, ensuring that even if the validator or extension lockdown is bypassed, the blast radius is contained (DuckDB cannot exfiltrate data to external hosts). This is NOT optional — it is required for initial release. For production Compose: use an `internal: true` network that includes only the web_console, Redis, and Postgres services (no internet-facing containers on the same network). For K8s: `NetworkPolicy` with egress default-deny and explicit allow rules for Redis/Postgres endpoints only + `readOnlyRootFilesystem: true`. The specific configuration is an operational choice documented in the runbook.

**Environment classification (HARD GATE — fail-closed for unknown environments):** `SqlExplorerService.__init__()` validates `APP_ENV` against a known-good set: `_KNOWN_ENVS = {"production", "staging", "development", "test", "local"}`. If `APP_ENV` is unset, empty, or not in the allowed set, the service **refuses to start** and raises `RuntimeError(f"SQL_EXPLORER requires explicit APP_ENV in {_KNOWN_ENVS}, got: '{_app_env}'")`. This prevents misconfigured or missing environment classification from silently disabling production guardrails. Unit test: verify startup fails with `APP_ENV=""`, `APP_ENV="prod"` (typo), and `APP_ENV` unset.

**Production startup attestation (HARD GATE — fail-closed):** In production (`APP_ENV=production`), `SqlExplorerService.__init__()` checks for a deployment attestation stamp before enabling the service. The deploy pipeline (after successful `validate_deployment_manifest.py` execution) sets the env var `SQL_EXPLORER_DEPLOY_ATTESTED=true`. If this attestation is missing in production, the service **refuses to start** and raises `RuntimeError("SQL Explorer requires deploy-attested sandbox in production")`. This ensures that even if the CI/CD pipeline is miswired, production cannot run SQL Explorer without validated sandbox controls. In non-production environments, the attestation check is skipped (the env var is optional).

**Runtime advisory probe:** `SqlExplorerService.__init__()` additionally performs a startup probe to check sandbox constraints. If the probe detects issues, it **logs a warning** and emits a Prometheus metric (`sql_explorer_sandbox_probe_failed`) but **does not disable SQL Explorer** — the three-layer defense provides sufficient protection independently:
```python
_SQL_EXPLORER_SANDBOX_SKIP = os.getenv("SQL_EXPLORER_SANDBOX_SKIP", "").lower() == "true"  # Dev-only

def _verify_sandbox() -> tuple[bool, list[str]]:
    """Advisory check of deployment sandbox constraints. Returns (safe, failures).

    Checks two constraints independently (advisory telemetry, not a gate):
    1. Network egress blocked (outbound socket to well-known endpoint)
    2. Filesystem write protection (attempt temp file write outside data/)
    Returns (True, []) if both constraints appear met, (False, [reasons]) otherwise.
    Callers log failures as warnings but do NOT disable the service.
    """
    if _SQL_EXPLORER_SANDBOX_SKIP:
        logger.warning("sql_explorer_sandbox_skip_dev_mode")
        return True, []  # Dev/local: skip probes
    failures: list[str] = []
    # 1. Network isolation check (targets are configurable via env vars)
    _PROBE_HOST = os.getenv("SQL_EXPLORER_PROBE_HOST", "1.1.1.1")
    _PROBE_PORT = int(os.getenv("SQL_EXPLORER_PROBE_PORT", "53"))
    import socket
    try:
        sock = socket.create_connection((_PROBE_HOST, _PROBE_PORT), timeout=3)
        sock.close()
        failures.append("network_egress_allowed")
    except (OSError, socket.timeout):
        pass  # Good: blocked
    # 2. Filesystem write protection check
    # Validate against EXPLICITLY FORBIDDEN paths from deployment attestation,
    # not generic temp directories. /var/tmp writability is common in hardened
    # deployments and is NOT a sandbox failure — data directories are the concern.
    import tempfile
    # Resolve forbidden paths against _PROJECT_ROOT to avoid cwd-sensitivity.
    # If _PROJECT_ROOT is /nonexistent (production fail-closed), probes target
    # paths that don't exist — which correctly reports "write blocked".
    _FORBIDDEN_WRITE_PATHS = [
        (_PROJECT_ROOT / p.strip()).resolve() if not _Path(p.strip()).is_absolute() else _Path(p.strip()).resolve()
        for p in os.getenv(
            "SQL_EXPLORER_FORBIDDEN_WRITE_PATHS",
            "data/,libs/,apps/,config/"
        ).split(",") if p.strip()
    ]
    for forbidden_dir in _FORBIDDEN_WRITE_PATHS:
        probe_path = forbidden_dir / ".sql_explorer_probe"
        try:
            probe_path.write_text("probe")
            probe_path.unlink()
            failures.append(f"filesystem_write_allowed:{forbidden_dir}")
        except OSError:
            pass  # Good: write blocked for this path
    return len(failures) == 0, failures
```
This probe runs at service initialization. If `_verify_sandbox()` returns `False`, the service **logs a warning** with the failure reasons and emits a Prometheus metric (`sql_explorer_sandbox_probe_failed`), but **does not disable SQL Explorer**. **Security rationale for advisory-only:** The probe is intentionally non-blocking because production enforcement is handled by two upstream hard gates that have already passed before this code executes: (1) **environment classification gate** (rejects unknown `APP_ENV`, see above), and (2) **production startup attestation** (`SQL_EXPLORER_DEPLOY_ATTESTED=true`, which is set only after `validate_deployment_manifest.py` confirms sandbox controls). In production, if this probe point is reached, both hard gates have already verified the deployment posture. The probe provides supplementary telemetry for ops teams, not primary enforcement. In non-production environments, sandbox is not required. The `SQL_EXPLORER_SANDBOX_SKIP=true` env var skips probes entirely in local development.

**Important caveat — probe limitations:** Runtime probes are advisory, not deterministic. Network egress checks (connecting to configurable probe host) and filesystem write checks (against explicitly forbidden paths, not generic temp dirs) are environment-dependent and may produce false positives (e.g., DNS resolution blocked but other egress allowed) or false negatives (e.g., forbidden path writable via symlink). The probes serve as a safety net, not a guarantee. `/var/tmp` and `/tmp` writability are NOT treated as sandbox failures — these are standard writable temp directories even in hardened deployments.

**Deployment attestation (required alongside probes):** In production, the deployment manifest (Docker Compose, Kubernetes spec, or equivalent) MUST include explicit sandbox controls:
- Internal-only networks with no external egress (explicit allowlist for Redis/Postgres only)
- Read-only filesystem mounts outside `data/` and `/tmp`
The deployment runbook MUST document these controls and include a verification checklist. Runtime probes supplement (not replace) deployment attestation.

**Machine-enforced deployment validation (HARD GATE — replaces runtime strict mode):** Add a deployment validation script (`scripts/validate_deployment_manifest.py`) that parses the production deployment manifest and fails the deploy pipeline if required sandbox controls are missing. This is the **deterministic** enforcement mechanism for production sandbox controls. Runtime probes are advisory telemetry only — they do not block startup. The validation runs as part of the deployment pipeline, not `ci-local`.

**Concrete manifest contract — target files and required keys:**

| Manifest Type | Target File(s) | Target Service | Required Controls |
|---------------|----------------|----------------|-------------------|
| **Docker Compose** | `docker-compose.yml`, `docker-compose.staging.yml`, or path from `--manifest` | `web_console` (configurable via `--service`, defaults to `web_console`) | **Production (`--env production`):** web_console must be attached **exclusively** to `internal: true` networks (no non-internal networks allowed — this is the only reliable way to block outbound internet egress in Compose without host-level firewall rules). Ingress to web_console must be handled by a reverse proxy on a separate network (the proxy bridges the internal and external networks; web_console itself never touches a non-internal network). **Staging/dev (`--env staging`/`dev`):** At least one `internal: true` network required; non-internal networks produce warnings but do not fail. `network_mode: "none"` is rejected in all environments (requires Redis/Postgres). `services.<service>.read_only: true` (**mandatory**); writable tmpfs mount for `/tmp` (DuckDB scratch) |
| **Kubernetes** | `deploy/k8s/` manifests or path from `--manifest` | Container running web console | `securityContext.readOnlyRootFilesystem: true`; `NetworkPolicy` with `policyTypes: ["Egress"]` that (a) selects web-console pods via `podSelector` matching the target workload's labels, (b) has default-deny egress with explicit allow rules for Redis/Postgres endpoints only (port + namespace/label selectors), (c) optionally allows DNS (port 53) for service discovery; `emptyDir` or `tmpfs` mount at `/tmp` |

**Validation script checks (all must pass):**
1. **Manifest discovery:** Script accepts `--manifest PATH`, `--service NAME` (default `web_console`), and `--env ENV` (default `production`, valid values: `production`, `staging`, `dev`) arguments. The `--env` flag controls enforcement tiers: **production (default)** = hard fail if any non-`internal: true` network is attached, hard fail on missing `read_only` and `/tmp`; **staging/dev** = hard fail if no `internal: true` network attached, warning for non-internal networks alongside internal (still hard fail on missing `read_only` and `/tmp` mount). If `--manifest` not provided, searches `docker-compose.yml`, `docker-compose.staging.yml` in order. Fails (exit 2) if no manifest found.
2. **Network isolation (egress focus):** The goal is to prevent web_console from reaching external hosts while preserving internal Redis/Postgres connectivity. For Compose, the validator enforces: (a) In `--env production` (default): web_console must be attached **exclusively** to `internal: true` networks — any non-internal network attachment is a **hard failure** (exit 1). This is the only reliable Compose-native mechanism to block outbound internet egress without host-level firewall rules. Ingress is handled by a reverse proxy on a separate bridged network (the proxy reaches web_console via the shared internal network; web_console never touches the non-internal network directly). (b) In `--env staging`/`dev`: at least one `internal: true` network is required (hard failure if none attached); additional non-internal networks produce warnings but do not fail (developers may need bridged networks for debugging). (c) `network_mode: "none"` is rejected in all environments because web_console requires Redis and Postgres access. "No published ports" alone is NOT sufficient as it only blocks ingress, not egress. For K8s: `NetworkPolicy` with `policyTypes: ["Egress"]` and default-deny plus explicit allow rules for Redis/Postgres endpoints only (applies to all environments).
3. **Filesystem restrictions:** For Compose: `read_only: true` on service is **mandatory** (`:ro` volume mounts alone are insufficient — they don't affect the writable image layer). Additionally validate that non-data/non-tmp volume mounts use `:ro` as defense-in-depth. For K8s: `readOnlyRootFilesystem: true` in security context.
4. **Writable temp mount:** For Compose: explicit `tmpfs` mount at `/tmp` (DuckDB scratch space). For K8s: `emptyDir` or `tmpfs` mount at `/tmp`. Without this, DuckDB queries will fail at runtime despite passing security validation.
5. **Exit code:** 0 = pass, 1 = validation failure (with human-readable error listing missing controls), 2 = manifest not found.

**Mandatory test coverage for validator:** Unit tests in `tests/scripts/test_validate_deployment_manifest.py` MUST cover:
- Compose (production): all attached networks `internal: true` + `read_only: true` + tmpfs `/tmp` → exit 0
- Compose (production): one `internal: true` + one non-internal network → exit 1 (non-internal disallowed in production)
- Compose (production): no `internal: true` network attached → exit 1
- Compose (staging): one `internal: true` + one non-internal network → exit 0 with warning (staging allows non-internal)
- Compose (staging): no `internal: true` network attached → exit 1 (at least one internal network required in all environments)
- Compose: `network_mode: "none"` → exit 1 with error "network_mode: none blocks required Redis/Postgres access; use internal networks instead"
- Compose: service has no explicit networks (uses default bridge) → exit 1 (default bridge is not internal)
- Compose: missing `read_only` → exit 1 (any env)
- Compose: missing tmpfs `/tmp` mount → exit 1
- K8s: NetworkPolicy with `podSelector` matching web-console labels + `Egress` policyType + `readOnlyRootFilesystem` + emptyDir `/tmp` → exit 0
- K8s: NetworkPolicy exists but `podSelector` does not match web-console workload labels → exit 1 (selector linkage failure)
- K8s: NetworkPolicy missing `Egress` in `policyTypes` → exit 1 (egress not controlled)
- K8s: missing NetworkPolicy entirely → exit 1
- K8s: missing emptyDir `/tmp` → exit 1
- No manifest found at any search path → exit 2
- `--manifest` pointing to non-existent file → exit 2
- `--service` targeting non-existent service → exit 1 with descriptive error
Tests use fixture YAML files in `tests/scripts/fixtures/` (not production manifests).

**Integration point — enforceable CI/CD checks (two layers):**

1. **PR CI gate (required status check on PRs to `master`):** Add a job `validate-deployment-manifest` in `.github/workflows/ci-tests-parallel.yml` triggered on `pull_request`. This job validates **all production-relevant manifests** (not just one), using a matrix or loop over the manifest list defined in a canonical config (e.g., `PRODUCTION_MANIFESTS=docker-compose.yml,docker-compose.prod.yml` env var or a `deploy/manifests.txt` file). Each manifest is validated with `python3 scripts/validate_deployment_manifest.py --manifest <manifest> --service web_console --env production`. This ensures that environment-specific overlays (`docker-compose.prod.yml`), generated manifests, and any other production-deployed files are all validated — not just the default `docker-compose.yml`. The job MUST be a **required status check** in GitHub branch protection for `master` (this repo uses `master` as the default branch, NOT `main`). This addresses the problem that deploy workflows (push/`workflow_dispatch`-triggered) do not run on PRs and therefore cannot serve as required PR status checks.
2. **CD pipeline gate (deploy-time validation):** Additionally run the same validation in the deployment workflow (e.g., `.github/workflows/deploy-staging.yml` or equivalent CD config) as a pre-deploy step. This provides a second gate at deploy time, catching any manifest changes introduced between merge and deploy (e.g., via hotfix or manual edit).
3. **Pre-deploy hook (defense-in-depth):** Additionally invoke the script in the deployment entrypoint/Makefile target (e.g., `make deploy` calls `validate_deployment_manifest.py` before `docker compose up`) so that manual deployments are also gated.
4. **Override:** Requires deployment-lead approval documented in the deployment log. No `--skip-validation` flag exists — the only bypass is removing the required status check, which requires admin access and produces an audit trail in GitHub.

**Note on `ci-local`:** The `make ci-local` target does NOT run the manifest validator itself (manifests are deployment artifacts, not test artifacts). However, `ci-local` DOES run the validator's unit tests (in `tests/scripts/test_validate_deployment_manifest.py`) to ensure the validation logic is correct.

**Deployment-profile probe behavior:**

| Environment | `SQL_EXPLORER_SANDBOX_SKIP` | Probe Behavior | Outcome on Failure |
|-------------|----------------------------|----------------|-------------------|
| **Dev/local** | `true` | Skipped entirely | N/A (warning logged) |
| **Staging** | `false` | Runs both probes | Warning logged + metric emitted. Service continues. Ops team should investigate. |
| **Production** | `false` | Runs both probes | **ERROR** logged + `sql_explorer_sandbox_probe_critical` metric emitted (distinct from staging's warning-level metric). Service continues ONLY because startup attestation (`SQL_EXPLORER_DEPLOY_ATTESTED=true`) has already verified sandbox controls at deploy time. If attestation is missing, service refuses to start (fail-closed). Probe failures in production with valid attestation indicate runtime drift and MUST trigger ops investigation within SLA. |

> **Note:** Runtime probes are advisory in ALL environments. There is no `SQL_EXPLORER_SANDBOX_STRICT` env var — the hard security gate is the **machine-enforced deployment manifest validation** (`scripts/validate_deployment_manifest.py`) which deterministically checks sandbox controls at deploy time.

**Release gate policy — revised (two tiers):**
1. **Runtime probes (ALL environments):** Probes are telemetry only — they never disable SQL Explorer. The three-layer defense (validator + blocklist + extension lockdown) provides sufficient protection independently. Probe results are advisory: logged as warnings + Prometheus metric for ops visibility.
2. **Deploy-time manifest validation (production HARD GATE):** The deployment validation script (`scripts/validate_deployment_manifest.py`) is the deterministic enforcement mechanism. It parses all production-relevant deployment manifests and fails the deploy pipeline if sandbox controls (network isolation, filesystem restrictions) are missing. This replaces the non-deterministic `SQL_EXPLORER_SANDBOX_STRICT` runtime startup block, which was vulnerable to both false positives (blocking healthy deployments) and false negatives (passing insecure ones). Override requires deployment-lead approval documented in the deployment log.
3. **Startup attestation (production FAIL-CLOSED):** In production (`APP_ENV=production`), `SqlExplorerService` requires `SQL_EXPLORER_DEPLOY_ATTESTED=true` (set by the deploy pipeline after successful manifest validation). If missing, the service refuses to start. This provides a fail-closed guarantee even if CI/CD wiring is incorrect — SQL Explorer cannot run unattested in production.

If probes fail in staging, the ops runbook (`docs/RUNBOOKS/ops.md`) defines the escalation path: verify deployment manifest controls, adjust probe targets if environment-specific, and document resolution in the deployment log.

**Future enhancement:** For additional depth, run DuckDB query execution in a subprocess with `seccomp`/`pledge` or a lightweight container. This provides per-query isolation beyond the per-process constraint above.

**Security analysis — why three layers are sufficient:**
The `SQLValidator` uses `sqlglot` AST parsing (not regex) to detect blocked functions. The AST walk MUST be exhaustive across ALL expression node types — not just `exp.Func` but every node that could invoke external behavior.

**RELEASE-BLOCKING GATE (T14.1):** Exhaustive AST traversal coverage MUST be proven by tests before T14.1 can ship. Specifically, the test suite MUST include at least one test case for EACH expression type listed below AND for each query pattern (direct, nested, aliased, CTE, subquery). If any expression type or pattern is untested, T14.1 MUST NOT pass code review. This is the primary security boundary since runtime `enable_external_access` lockdown is not used (see ADR-0035).

**Expression types validated (mandatory coverage):**
- `exp.Func` (all subclasses): Direct function calls (`read_parquet`, `http_get`, `read_csv`, etc.)
- `exp.Anonymous`: Unrecognized function names not mapped to `sqlglot` builtins
- `exp.Command`: DuckDB-specific commands (COPY, ATTACH, INSTALL, LOAD, EXPORT)
- `exp.Set`: SET statements that could change runtime behavior (`enable_external_access`, etc.)
- `exp.Pragma`: DuckDB pragma statements
- `exp.Create`/`exp.Drop`/`exp.Insert`/`exp.Update`/`exp.Delete`: DDL/DML blocking

**Coverage by query pattern:**
- Direct calls: `SELECT read_parquet('/etc/passwd')` → blocked
- Nested calls: `SELECT * FROM (SELECT read_parquet(...))` → blocked (AST walk finds all `exp.Func` nodes recursively)
- Aliased calls: `SELECT read_parquet(...) AS x` → blocked
- CTE usage: `WITH t AS (SELECT read_parquet(...))` → blocked
- Subquery expressions: `WHERE col IN (SELECT read_parquet(...))` → blocked
- Anonymous functions: Catches both recognized and unrecognized function names
- Lateral joins: `FROM t, LATERAL (SELECT read_parquet(...))` → blocked

**Implementation requirement:** The validator MUST use `sqlglot`'s `walk()` or `find_all()` to traverse the **complete** AST recursively, not just top-level expressions. Unit tests MUST include at least one test per expression type above to verify exhaustive coverage.

The only known bypass vectors (DuckDB pragmas, COPY, ATTACH, SET) are all blocked as disallowed statement types. The validator is the same one used by the existing `DataExplorerService`, providing battle-tested coverage.

```python
def _create_query_connection(
    dataset: str,
    available_tables: set[str],
) -> duckdb.DuckDBPyConnection:
    """Create a DuckDB connection with dataset tables registered as Parquet-backed views.

    Only registers views for tables in `available_tables` (validated by
    `_validate_table_paths()` at page load). This ensures users cannot query
    tables that lack backing Parquet files.

    Security: Does NOT use enable_external_access=false because DuckDB views
    are lazy — read_parquet() executes at query time, not at view creation time.
    Security is enforced by SQLValidator (parse-time function/table blocking)
    and _check_sensitive_tables() (pre-execution system table blocking).

    Path safety: View paths are derived from _resolve_table_paths() which
    reads from known provider configurations. Paths are validated with
    _validate_path_safe() to reject traversal attacks (../ sequences).
    DuckDB DDL does not support prepared parameters, so paths must be
    interpolated — but they come from server-side configuration, never
    from user input.
    """
    conn = duckdb.connect()  # In-memory, writable for view creation only
    # Layer 3: Disable extension autoloading to prevent bypass via extensions
    conn.execute("SET enable_extension_autoloading = false")
    conn.execute("SET enable_extension_loading = false")
    # Verify no restricted extensions were pre-loaded (e.g., via ~/.duckdbrc
    # or environment variables). Log and reject if any are unexpectedly active.
    _loaded = conn.execute(
        "SELECT extension_name FROM duckdb_extensions() WHERE loaded = true"
    ).fetchall()
    # Baseline built-in extensions that DuckDB loads by default.
    # Only risky extensions (httpfs, postgres_scanner, sqlite_scanner, etc.)
    # are denied — these enable network/filesystem access that bypasses validation.
    # NOTE: This allowlist is version-sensitive. Pin DuckDB version in
    # requirements.txt (e.g., duckdb==1.1.x) to prevent surprise extension
    # changes on upgrades. If DuckDB adds new built-in extensions in a future
    # version, they will be flagged as unknown — update the allowlist after
    # security review of the new extension's capabilities.
    _ALLOWED_EXTENSIONS = frozenset({
        "core_functions", "icu", "json", "parquet",  # DuckDB built-ins
    })
    # For operational flexibility, unknown extensions that are NOT in the
    # denied set trigger a warning (not a hard failure) if
    # SQL_EXPLORER_STRICT_EXTENSIONS=false. Default is strict (hard failure).
    _STRICT_EXTENSIONS = os.getenv(
        "SQL_EXPLORER_STRICT_EXTENSIONS", "true"
    ).lower() == "true"
    # Production guardrail: strict mode is mandatory in production.
    # Prevents accidental security weakening via env var misconfiguration.
    _app_env = os.getenv("APP_ENV", "").lower()
    if not _STRICT_EXTENSIONS and _app_env == "production":
        raise RuntimeError(
            "SQL_EXPLORER_STRICT_EXTENSIONS=false is forbidden when APP_ENV=production. "
            "Unknown extensions MUST be reviewed and added to _ALLOWED_EXTENSIONS, "
            "or removed from the DuckDB installation."
        )
    _DENIED_EXTENSIONS = frozenset({
        "httpfs", "postgres_scanner", "sqlite_scanner", "mysql_scanner",
        "azure", "aws", "motherduck",
    })
    for (ext_name,) in _loaded:
        if ext_name in _DENIED_EXTENSIONS:
            logger.error("duckdb_denied_extension", extra={"extension": ext_name})
            conn.close()
            raise RuntimeError(f"Denied DuckDB extension loaded: {ext_name}")
        if ext_name not in _ALLOWED_EXTENSIONS:
            if _STRICT_EXTENSIONS:
                logger.error("duckdb_unknown_extension_strict", extra={"extension": ext_name})
                conn.close()
                raise RuntimeError(
                    f"Unknown DuckDB extension loaded: {ext_name}. "
                    "Update _ALLOWED_EXTENSIONS after security review, "
                    "or set SQL_EXPLORER_STRICT_EXTENSIONS=false."
                )
            logger.warning("duckdb_unknown_extension_advisory", extra={"extension": ext_name})
    # Resource bounds: prevent single query from OOM-ing the web console process
    # Memory budget per connection — configurable via env to prevent overcommit.
    # Sizing formula: total_reserved = workers * _MAX_CONCURRENT_QUERIES * max_memory_mb
    # Default 512MB: 4 workers * 3 queries * 512MB = 6GB (safe on 16GB host)
    # Previous 2GB default: 4 * 3 * 2GB = 24GB (OOM risk on typical hosts)
    _DEFAULT_MAX_MEMORY_MB = 512
    try:
        _max_memory_mb = max(64, int(os.getenv("SQL_EXPLORER_MAX_MEMORY_MB", str(_DEFAULT_MAX_MEMORY_MB))))
    except (ValueError, TypeError):
        logger.warning("sql_explorer_invalid_max_memory", extra={
            "raw_value": os.getenv("SQL_EXPLORER_MAX_MEMORY_MB"),
            "fallback": _DEFAULT_MAX_MEMORY_MB,
        })
        _max_memory_mb = _DEFAULT_MAX_MEMORY_MB
    conn.execute(f"SET max_memory = '{_max_memory_mb}MB'")
    conn.execute("SET threads = 1")
    table_paths = _resolve_table_paths()
    for table_name in DATASET_TABLES[dataset]:
        if table_name not in available_tables:
            continue  # Skip tables without valid Parquet files
        # Validate table_name is a safe SQL identifier (defense-in-depth:
        # DATASET_TABLES is hardcoded, but validate in case it becomes dynamic)
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", table_name):
            raise ValueError(f"Invalid table identifier: {table_name}")
        parquet_path = table_paths.get(table_name)
        if parquet_path:
            _validate_path_safe(parquet_path)
            # DDL does not support prepared params — paths are server-controlled.
            # Double-quote identifier to handle reserved words (e.g., "group").
            conn.execute(
                f'CREATE OR REPLACE VIEW "{table_name}" AS '
                f"SELECT * FROM read_parquet('{parquet_path}')"
            )
    return conn


from pathlib import Path as _Path

# Project root anchored via environment variable (mandatory in production).
# Startup validation ensures the data directory exists before accepting queries.
_PROJECT_ROOT_ENV = os.getenv("PROJECT_ROOT")
if _PROJECT_ROOT_ENV:
    _PROJECT_ROOT = _Path(_PROJECT_ROOT_ENV).resolve()
else:
    # Development fallback: walk up from this file to find repo root marker.
    # Check pyproject.toml first (survives stripped Docker images where .git
    # may be excluded via .dockerignore), then .git as secondary marker.
    _candidate = _Path(__file__).resolve().parent
    _found_marker = False
    while _candidate != _candidate.parent:
        if (_candidate / "pyproject.toml").exists() or (_candidate / ".git").exists():
            _found_marker = True
            break
        _candidate = _candidate.parent
    if _found_marker:
        _PROJECT_ROOT = _candidate
    else:
        # Fail-open only in dev mode. In production (PROJECT_ROOT not set and
        # no source tree marker found), fail closed to prevent unpredictable
        # dataset exposure via CWD-relative paths.
        _is_dev = os.getenv("SQL_EXPLORER_DEV_MODE", "").lower() == "true"
        _app_env = os.getenv("APP_ENV", "").lower()
        if _is_dev and _app_env == "production":
            raise RuntimeError(
                "SQL_EXPLORER_DEV_MODE=true is forbidden when APP_ENV=production."
            )
        if _is_dev:
            _PROJECT_ROOT = _Path.cwd()
            logger.warning(
                "sql_explorer_project_root_fallback_cwd_dev",
                extra={"cwd": str(_PROJECT_ROOT)},
            )
        else:
            logger.error(
                "sql_explorer_project_root_not_found",
                extra={"hint": "Set PROJECT_ROOT env var or SQL_EXPLORER_DEV_MODE=true for local dev"},
            )
            _PROJECT_ROOT = _Path("/nonexistent")  # Fail closed: no valid data root

_ALLOWED_DATA_ROOTS: list[_Path] = [
    (_PROJECT_ROOT / "data").resolve(),  # Project data directory (absolute)
]

# Module-level: log a warning if data root is missing (do NOT raise —
# raising at import time crashes the entire web console, not just this page).
_DATA_ROOT_AVAILABLE = (_PROJECT_ROOT / "data").is_dir()
if not _DATA_ROOT_AVAILABLE:
    logger.warning(
        "sql_explorer_data_root_missing",
        extra={"expected": str(_PROJECT_ROOT / "data")},
    )

def _validate_path_safe(path: str) -> None:
    """Validate that a Parquet path is safe for DDL interpolation.

    Paths come from server config (not user input), but are validated as
    defense-in-depth since DuckDB DDL does not support prepared parameters.

    Validation strategy:
    1. Reject paths containing single/double quotes or control chars
       (prevents SQL injection in DDL string interpolation).
    2. For non-glob paths, resolve to absolute and verify it falls under
       an allowed data root (prevents directory traversal).
    3. For glob paths (containing *), validate the base directory portion.

    Note: Providers may use .resolve() producing absolute paths. This is
    allowed as long as the resolved path is under an allowed root.
    """
    if not path:
        raise ValueError("Empty path rejected")
    # Block SQL injection characters (quotes, control chars, backslashes)
    if "'" in path or '"' in path or "\\" in path or any(ord(c) < 32 for c in path):
        raise ValueError(f"Unsafe characters in path: {path}")
    # Block traversal segments ANYWHERE in the full path (before and after globs)
    # This prevents patterns like "data/valid/../../../etc/passwd" or "data/*.parquet/../secret"
    for segment in path.replace("\\", "/").split("/"):
        if segment == "..":
            raise ValueError(f"Path traversal rejected: {path}")
    # For glob paths, validate the directory portion under allowed roots
    base_path = path.split("*")[0].rstrip("/") if "*" in path else path
    if not base_path:
        raise ValueError(f"Unsafe path rejected: {path}")
    resolved = _Path(base_path).resolve()
    if not any(resolved.is_relative_to(root) for root in _ALLOWED_DATA_ROOTS):
        raise ValueError(
            f"Path not under allowed data root: {path} "
            f"(resolved to {resolved})"
        )
```

**DuckDB table registration:**
Table-to-Parquet mappings should be resolved dynamically from data provider metadata rather than hardcoded paths, since provider storage layouts may change. Build the mapping at page load time by consulting the provider implementations:

```python
def _resolve_table_paths() -> dict[str, str]:
    """Resolve Parquet paths for ALL dataset tables from provider metadata.

    Returns dict mapping table_name -> parquet_glob_path for all tables
    across all datasets. Paths are derived from data provider storage
    layouts to avoid hardcoding that drifts from actual file locations.

    Note: Returns paths for ALL datasets (not per-dataset). Callers
    filter by dataset via DATASET_TABLES[dataset] to get relevant tables.
    """
    # See primary implementation above for concrete path mappings.
    ...
```

The primary approach is **provider-derived resolution** that reads storage paths from the actual provider implementations at startup. This avoids path drift between the SQL Explorer and data providers:

```python
def _resolve_table_paths() -> dict[str, str]:
    """Resolve Parquet paths for dataset tables from provider implementations.

    Reads storage_path from each provider class to build the mapping.
    Falls back to convention-based paths for tables with no provider reference.
    Logs warnings for unresolvable tables.
    """
    data_root = str((_PROJECT_ROOT / "data").resolve())
    paths: dict[str, str] = {}
    # Provider-derived paths anchored to absolute _PROJECT_ROOT/data.
    # Uses absolute paths to avoid CWD dependency.
    # CRSPLocalProvider.storage_path = "data/wrds/crsp/daily/"
    paths["crsp_daily"] = f"{data_root}/wrds/crsp/daily/*.parquet"
    # CompustatLocalProvider: annual="data/wrds/compustat_annual/", quarterly="data/wrds/compustat_quarterly/"
    paths["compustat_annual"] = f"{data_root}/wrds/compustat_annual/*.parquet"
    paths["compustat_quarterly"] = f"{data_root}/wrds/compustat_quarterly/*.parquet"
    # FamaFrenchLocalProvider.storage_path = "data/fama_french/factors/"
    paths["ff_factors_daily"] = f"{data_root}/fama_french/factors/factors_*_daily.parquet"
    paths["ff_factors_monthly"] = f"{data_root}/fama_french/factors/factors_*_monthly.parquet"
    # TAQStorageManager.storage_path = "data/taq/"
    paths["taq_trades"] = f"{data_root}/taq/aggregates/1min_bars/*.parquet"
    paths["taq_quotes"] = f"{data_root}/taq/aggregates/spread_stats/*.parquet"
    return paths
```

**Known table-name alignment issue:** `DATASET_TABLES` in `sql_validator.py` defines table names (`taq_trades`, `taq_quotes`, `crsp_monthly`) that don't all have direct 1:1 mapping to provider storage schemas:
- `taq_trades` → maps to `data/taq/aggregates/1min_bars/` (actual TAQ schema uses `taq_1min_bars`, `taq_daily_rv`, `taq_spread_stats`, `taq_ticks`)
- `taq_quotes` → maps to `data/taq/aggregates/spread_stats/`
- `crsp_monthly` → no separate monthly storage found in `CRSPLocalProvider`
- `ff_factors_daily/monthly` → actual files are `factors_3_daily`, `factors_5_daily`, etc.

**Implementation decision during T14.1:** Either (a) update `DATASET_TABLES` in `sql_validator.py` to match actual provider schemas and add a migration note, or (b) create logical-to-physical table mapping that translates the existing validator table names to actual Parquet paths. Option (b) is safer as it avoids breaking existing validator tests. The mapping above uses option (b).

```python
def _validate_table_paths(
    table_paths: dict[str, str] | None = None,
) -> tuple[dict[str, set[str]], list[str]]:
    """Validate that resolved Parquet paths exist on disk.

    Args:
        table_paths: Table-to-path mapping from _resolve_table_paths().
                     If None, calls _resolve_table_paths() internally.

    Returns:
        Tuple of (available_tables_by_dataset, warnings):
        - available_tables_by_dataset: dict mapping dataset name to set of
          table names with valid Parquet files on disk. Datasets with NO
          valid tables are excluded from the dict entirely.
        - warnings: list of warning messages for tables with no matching files

    Per-table granularity ensures users cannot query a whitelisted table
    that has no backing Parquet files. _create_query_connection() only
    registers views for tables in the available set, and queries against
    unregistered tables produce a clear "table not found" error.
    """
    if table_paths is None:
        table_paths = _resolve_table_paths()
    available_tables_by_dataset: dict[str, set[str]] = {}
    warnings: list[str] = []
    for dataset, tables in DATASET_TABLES.items():
        available_tables: set[str] = set()
        for table in tables:
            path = table_paths.get(table)
            if path and _glob_has_match(path):
                available_tables.add(table)
            else:
                warnings.append(f"No Parquet files found for {table}: {path}")
        if available_tables:
            available_tables_by_dataset[dataset] = available_tables
        else:
            warnings.append(f"Dataset '{dataset}' has no available data — excluded from UI")
    return available_tables_by_dataset, warnings
```

```python
import glob as _glob_module

def _glob_has_match(pattern: str) -> bool:
    """Check if a glob pattern matches at least one file.
    Handles both relative and absolute paths correctly.
    pathlib's Path.glob() does not support absolute patterns,
    so we use the stdlib glob module which handles both."""
    return bool(_glob_module.glob(pattern))
```

**Usage:** At page load, first check `_DATA_ROOT_AVAILABLE`. If `False`, display `ui.notify("SQL Explorer unavailable: data directory not found. Set PROJECT_ROOT env var.", type="warning")` and render the page with all controls disabled (no dataset selector, no query editor, no execute button). This graceful degradation avoids crashing the entire web console — the data root check is page-scoped, NOT module-scoped. If `_DATA_ROOT_AVAILABLE` is `True`, run path discovery asynchronously to avoid blocking the NiceGUI event loop:

```python
# Page load: run synchronous glob/filesystem checks off the event loop.
# Uses a module-level TTL cache (120s) to avoid re-scanning filesystem on
# every page load. Thread-safe via asyncio.Lock. Operators can invalidate
# by restarting the service or waiting for TTL expiry.
_path_cache: tuple[dict[str, set[str]], list[str]] | None = None
_path_cache_ts: float = 0.0
_PATH_CACHE_TTL = 120  # seconds
_path_cache_lock = asyncio.Lock()  # Prevent duplicate filesystem scans on concurrent first loads

async def _get_validated_paths() -> tuple[dict[str, set[str]], list[str]]:
    global _path_cache, _path_cache_ts
    # Fast path: cache hit without lock
    now = time.monotonic()
    if _path_cache is not None and (now - _path_cache_ts) < _PATH_CACHE_TTL:
        return _path_cache
    # Slow path: acquire lock, re-check, then scan
    async with _path_cache_lock:
        now = time.monotonic()
        if _path_cache is not None and (now - _path_cache_ts) < _PATH_CACHE_TTL:
            return _path_cache
        result = await asyncio.to_thread(_validate_table_paths)
        _path_cache = result
        _path_cache_ts = now
        return result

# At page load:
available_tables_by_dataset, warnings = await _get_validated_paths()
for w in warnings:
    logger.warning("sql_explorer_path_warning", extra={"detail": w})
```

Only datasets in `available_tables_by_dataset` appear in the dataset selector. When creating a query connection, only register views for tables in `available_tables_by_dataset[dataset]` — this ensures users cannot query tables lacking backing Parquet files. Warnings are logged via `logger.warning()` for operator visibility. The data source status page (T14.2) can also surface these warnings.

**Implementation note:** The `_resolve_table_paths()` function above reflects actual provider storage layouts as of this writing. During T14.1 implementation, confirm paths by running `_validate_table_paths()` at startup and checking for warnings. If providers change storage layouts, update `_resolve_table_paths()` accordingly.

**Future consolidation:** Ideally, `_resolve_table_paths()` should read `storage_path` attributes directly from provider classes (e.g., `CRSPLocalProvider.storage_path`) rather than duplicating path literals. This ensures a single source of truth between the SQL Explorer and ETL pipeline. For initial implementation, hardcoded paths anchored to `_PROJECT_ROOT` are acceptable since provider APIs are not yet standardized for path introspection.

**ADR-0035 mapping decision:** The mapping strategy (Option b: keep `DATASET_TABLES` unchanged in `sql_validator.py`, handle logical-to-physical resolution in `SqlExplorerService._resolve_table_paths()`) MUST be documented as a formal decision in `ADR-0035-sql-explorer-security`. The ADR should record: (a) the decision to keep logical and physical table names separate, (b) the rationale (backward compatibility with existing validator tests), (c) the startup validation contract (`_validate_table_paths()` logs warnings for missing Parquet files and excludes datasets with no valid tables from the UI), and (d) the future consolidation path (provider path introspection).

#### Backend: `libs/web_console_services/sql_explorer_service.py`

All security-critical SQL execution logic lives in the service layer, NOT in the page module. This ensures policy enforcement is centralized, testable, and reusable.

```python
class SensitiveTableAccessError(ValueError):
    """Raised when a query references a sensitive/restricted table.
    Subclass of ValueError for backward compatibility with callers
    that catch ValueError, but enables deterministic audit classification
    as 'security_blocked' without brittle string matching."""


class ConcurrencyLimitError(RuntimeError):
    """Raised when concurrent query cap is reached.
    Subclass of RuntimeError for backward compatibility, but enables
    deterministic audit classification as 'concurrency_limit'."""


class RateLimitExceededError(RuntimeError):
    """Raised when rate limit is exceeded (query or export).
    Subclass of RuntimeError for backward compatibility, but enables
    deterministic audit classification as 'rate_limited' without catching
    unrelated RuntimeError from libraries/runtime."""


@dataclass(frozen=True)
class QueryResult:
    """Typed result from execute_query() — avoids loose attribute access."""
    df: pl.DataFrame
    execution_ms: int
    fingerprint: str  # Redacted query for history/status display


class SqlExplorerService:
    """Centralizes SQL validation, connection management, execution, and audit logging.

    The page module is a thin UI orchestrator that delegates all execution
    to this service. Security controls (validation, sensitive table checks,
    extension verification, rate limiting, timeout, audit) are enforced here.
    """

    def __init__(self, rate_limiter: RateLimiter | None = None) -> None:
        # Guard: rate_limiter=None is only acceptable in dev mode.
        # In production, omitting the limiter would silently disable
        # query/export throttling, enabling resource abuse.
        if rate_limiter is None:
            _is_dev = os.getenv("SQL_EXPLORER_DEV_MODE", "").lower() == "true"
            _app_env = os.getenv("APP_ENV", "").lower()
            # Production guardrail: reject dev mode in production to prevent
            # accidental rate-limit bypass through environment variable drift.
            if _is_dev and _app_env == "production":
                raise RuntimeError(
                    "SQL_EXPLORER_DEV_MODE=true is forbidden when APP_ENV=production. "
                    "This prevents accidental rate-limit bypass in production. "
                    "Remove SQL_EXPLORER_DEV_MODE or set APP_ENV to a non-production value."
                )
            if not _is_dev:
                raise ValueError(
                    "SqlExplorerService requires a RateLimiter in production. "
                    "Set SQL_EXPLORER_DEV_MODE=true to skip rate limiting in dev."
                )
            logger.warning("sql_explorer_rate_limiter_disabled_dev_mode")
        elif rate_limiter.fallback_mode != "deny":
            raise ValueError(
                f"SqlExplorerService requires RateLimiter with fallback_mode='deny' "
                f"(got '{rate_limiter.fallback_mode}'). Use a dedicated instance, "
                f"not the get_rate_limiter() singleton."
            )
        self._rate_limiter = rate_limiter
        self._validator = SQLValidator()

        # Sandbox probe — advisory in ALL environments.
        # Hard gate is deploy-time manifest validation, not runtime probes.
        safe, failures = _verify_sandbox()
        if not safe:
            logger.warning(
                "sql_explorer_sandbox_probe_failed",
                extra={"failures": failures},
            )

    # --- Redis outage policy ---
    # The RateLimiter MUST be created with fallback_mode="deny" (fail-closed).
    # When Redis is unavailable, queries and exports are BLOCKED rather than
    # allowed without rate limiting. This prevents unbounded resource
    # consumption during Redis outages.
    #
    # ⚠️ IMPORTANT: Do NOT use the `get_rate_limiter()` singleton for SQL
    # Explorer. The singleton returns the first-created instance, which may
    # have been initialized with a different `fallback_mode` by another page.
    # Instead, construct a DEDICATED `RateLimiter` instance with explicit
    # `redis_client` and `fallback_mode="deny"`, then inject it into
    # `SqlExplorerService`.
    #
    # Page-level instantiation pattern (MUST use async Redis client):
    #   from apps.web_console_ng.core.redis_ha import get_redis_store
    #   store = get_redis_store()
    #   redis_client = await store.get_master()  # returns redis.asyncio.Redis
    #   rate_limiter = RateLimiter(redis_client=redis_client, fallback_mode="deny")
    #   sql_explorer_service = SqlExplorerService(rate_limiter=rate_limiter)
    #
    # IMPORTANT: RateLimiter expects redis.asyncio.Redis (async), NOT the sync
    # client from get_sync_redis_client(). Using sync Redis would cause mypy
    # failures and runtime async/sync mismatches.
    #
    # The service __init__ asserts effective mode at startup:
    #   assert self._rate_limiter.fallback_mode == "deny"
    #
    # In dev mode (SQL_EXPLORER_DEV_MODE=true), rate_limiter=None is accepted
    # and rate limiting is skipped entirely with a warning log.

    async def execute_query(
        self,
        user: Any,
        dataset: str,
        query: str,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
        max_rows: int = _DEFAULT_MAX_ROWS,
        available_tables: set[str] | None = None,
    ) -> QueryResult:
        """Validate, execute, and audit a SQL query.

        Returns a QueryResult with df, execution_ms, and fingerprint.
        The page uses these typed fields for status display and history —
        no loose attribute access on the service instance.

        Enforces: authorization, rate limiting, SQL validation, sensitive table
        check, row limit, timeout, connection security, and audit logging.
        Raises PermissionError, ValueError, RuntimeError, asyncio.TimeoutError.

        Args:
            available_tables: Set of table names with valid Parquet backing
                for this dataset. Passed to _create_query_connection() to
                register only available views. If None, uses all tables
                from DATASET_TABLES[dataset].
        """
        start = time.monotonic()
        limited_sql: str | None = None
        try:
            # 1. Authorization
            if not can_query_dataset(user, dataset):
                raise PermissionError(f"Not authorized for dataset '{dataset}'")
            # 2. Rate limiting
            if self._rate_limiter:
                allowed, _ = await self._rate_limiter.check_rate_limit(
                    user_id=get_user_id(user),
                    action="sql_query", max_requests=_QUERY_RATE_LIMIT, window_seconds=60,
                )
                if not allowed:
                    raise RateLimitExceededError("Rate limit exceeded")
            # 3. SQL validation
            valid, error = self._validator.validate(query, dataset)
            if not valid:
                raise ValueError(error)
            # 4. Sensitive table check — raises SensitiveTableAccessError directly
            #    for deterministic audit status classification without string matching
            tables = self._validator.extract_tables(query)
            _check_sensitive_tables(tables)  # Raises SensitiveTableAccessError if blocked
            # 5. Row limit enforcement
            limited_sql = self._validator.enforce_row_limit(query, min(max_rows, _MAX_ROWS_LIMIT))
            # 6. Resolve available tables — default to all dataset tables if None
            resolved_tables = available_tables if available_tables is not None else set(DATASET_TABLES[dataset])
            # 7. Create secure connection + execute with timeout
            conn = _create_query_connection(dataset, available_tables=resolved_tables)
            try:
                result = await _execute_query_with_timeout(conn, limited_sql, timeout_seconds)
            finally:
                conn.close()  # Always close to prevent connection/FD leaks
            execution_ms = int((time.monotonic() - start) * 1000)
            # 7. Audit logging — success path
            fingerprint = _fingerprint_query(query)
            _log_query(user, dataset, query, limited_sql, len(result), execution_ms, "success", None)
            return QueryResult(df=result, execution_ms=execution_ms, fingerprint=fingerprint)
        except PermissionError:
            execution_ms = int((time.monotonic() - start) * 1000)
            _log_query(user, dataset, query, limited_sql, 0, execution_ms, "authorization_denied", None)
            raise
        except SensitiveTableAccessError as e:
            # Dedicated exception type — no string matching needed for classification
            execution_ms = int((time.monotonic() - start) * 1000)
            _log_query(user, dataset, query, limited_sql, 0, execution_ms, "security_blocked", _safe_error_message("security_blocked", str(e)))
            raise
        except ValueError as e:
            execution_ms = int((time.monotonic() - start) * 1000)
            _log_query(user, dataset, query, limited_sql, 0, execution_ms, "validation_error", _safe_error_message("validation_error", str(e)))
            raise
        except ConcurrencyLimitError as e:
            # Dedicated exception type for concurrency cap — no string matching
            execution_ms = int((time.monotonic() - start) * 1000)
            _log_query(user, dataset, query, limited_sql, 0, execution_ms, "concurrency_limit", _safe_error_message("concurrency_limit", str(e)))
            raise
        except RateLimitExceededError as e:
            # Dedicated exception type for rate limiting — prevents generic
            # RuntimeError from library/runtime faults being mislabeled
            execution_ms = int((time.monotonic() - start) * 1000)
            _log_query(user, dataset, query, limited_sql, 0, execution_ms, "rate_limited", _safe_error_message("rate_limited", str(e)))
            raise
        except asyncio.TimeoutError:
            execution_ms = int((time.monotonic() - start) * 1000)
            _log_query(user, dataset, query, limited_sql, 0, execution_ms, "timeout", None)
            raise
        except Exception as e:
            execution_ms = int((time.monotonic() - start) * 1000)
            _log_query(user, dataset, query, limited_sql, 0, execution_ms, "error", _safe_error_message("error", str(e)))
            raise

    async def export_csv(self, user: Any, dataset: str, df: pl.DataFrame) -> bytes:
        """Export query results as CSV bytes.

        Enforces: EXPORT_DATA permission, dataset authorization, rate limiting,
        and audit logging for ALL terminal states (success, denied, rate-limited,
        error). The page MUST NOT duplicate any of these checks.
        """
        def _log_export(status: str, row_count: int = 0) -> None:
            logger.info("sql_export_csv", extra={
                "user_id": get_user_id(user),
                "dataset": dataset,
                "row_count": row_count,
                "status": status,
            })

        try:
            if not has_permission(user, Permission.EXPORT_DATA):
                _log_export("authorization_denied")
                raise PermissionError("Export permission required")
            if not can_query_dataset(user, dataset):
                _log_export("authorization_denied")
                raise PermissionError(f"Not authorized for dataset '{dataset}'")
            # Rate limiting (export-specific: 5/hour per user)
            if self._rate_limiter:
                allowed, _ = await self._rate_limiter.check_rate_limit(
                    user_id=get_user_id(user),
                    action="sql_export", max_requests=_EXPORT_RATE_LIMIT, window_seconds=3600,
                )
                if not allowed:
                    _log_export("rate_limited")
                    raise RateLimitExceededError("Export rate limit exceeded")
            csv_bytes = df.write_csv().encode("utf-8")
            _log_export("success", len(df))
            return csv_bytes
        except (PermissionError, RateLimitExceededError):
            raise  # Already logged above
        except Exception:
            _log_export("error")
            raise
```

The `_create_query_connection(dataset: str, available_tables: set[str])`, `_execute_query_with_timeout()`, `_check_sensitive_tables()`, `_fingerprint_query()`, `_log_query()`, `can_query_dataset()`, `SensitiveTableAccessError`, `ConcurrencyLimitError`, and `QueryResult` also live in this service module.

#### Frontend: `apps/web_console_ng/pages/sql_explorer.py`

New NiceGUI page at route `/data/sql-explorer`.

**Page structure:**
1. **Dataset selector** — Dropdown populated from the intersection of `available_tables_by_dataset` keys (from `_validate_table_paths()`) and datasets the user is authorized for (via `can_query_dataset()`). Only datasets that are both authorized AND have at least one available Parquet-backed table appear.
   - Shows available tables for selected dataset below the dropdown as info text
   - Permission: `QUERY_DATA` required

2. **SQL editor** — `ui.textarea` with monospace font styling
   - Placeholder: `"SELECT * FROM crsp_daily WHERE symbol = 'AAPL' LIMIT 100"`
   - CSS classes: `"w-full font-mono"` with minimum 8 rows height
   - **Enhancement note:** Can be upgraded to CodeMirror via `ui.html()` or NiceGUI's `ui.code()` component in a follow-up if NiceGUI version supports it. Syntax highlighting is a nice-to-have, not a blocker.

3. **Query controls row:**
   - **Run button** — Executes query (requires `QUERY_DATA`), disabled while query running
   - **Timeout selector** — Dropdown: 10s, 30s (default), 60s, 120s
   - **Max rows input** — Number input: default 10,000, min 1, max 50,000
   - **Export CSV button** — Downloads results as CSV file (requires `EXPORT_DATA`)

4. **Results section:**
   - **Status bar** — Shows: query status, row count, execution time in ms
   - **AG Grid** — Displays query results with:
     - Dynamic columns generated from query result DataFrame columns
     - Sortable, filterable columns
     - `apply_compact_grid_options()` for consistent styling
   - **Empty state** — "Run a query to see results" when no query has been executed
   - **Error state** — Shows validation error message or timeout notification

5. **Query history sidebar** (collapsible right panel):
   - Shows recent queries from current session (in-memory list, not persisted across sessions)
   - History entries store and display **fingerprinted SQL only** (literals replaced with `?`)
   - **No raw SQL stored in history** — replay populates editor with fingerprinted query (user re-fills literals)
   - "Clear History" button to purge all entries
   - Security rationale: storing raw SQL in session state creates sensitive-data exposure via UI snapshots, debugging dumps, or memory. Fingerprint-only history eliminates this risk at the cost of requiring users to re-enter literals for replay.
   - **Future enhancement:** If power users find fingerprint-only replay frustrating, a follow-up could allow raw SQL in session-scoped history IF: (a) the session is HTTPS-secured, (b) raw SQL never appears in server logs (already handled by `SQL_EXPLORER_AUDIT_RAW_SQL=false` default), and (c) the history is strictly in-memory (no persistence). This would require a separate opt-in flag (`SQL_EXPLORER_RAW_HISTORY=true`) and security review. Out of scope for T14 v1.
   - Shows per entry: timestamp (relative), dataset, status (icon), row count
   - Limited to 20 most recent queries

**Permission model:**
- Page visibility: `QUERY_DATA` (OPERATOR, ADMIN)
- Query execution: `QUERY_DATA` AND `can_query_dataset(user, dataset)` — enforces per-dataset licensing via `_DATASET_PERMISSION_MAP` (default-deny for unmapped datasets)
- CSV export: `EXPORT_DATA` AND `can_query_dataset(user, dataset)`
- **Dataset-level control (DEFAULT-DENY):** The dataset selector MUST be filtered to only show datasets the user is authorized for. Before query execution and CSV export, call `can_query_dataset(user, dataset)` to enforce licensed access. Every dataset in `DATASET_TABLES` MUST have a corresponding entry in `_DATASET_PERMISSION_MAP`. Unmapped datasets are denied by default to prevent accidental exposure of licensed data when new datasets are added to `DATASET_TABLES`.
- **Public vs licensed datasets:** SQL Explorer only exposes datasets registered in `DATASET_TABLES` (crsp, compustat, fama_french, taq). YFinance is a "public" data source in T14.2 (no `DatasetPermission` mapping) but is NOT in `DATASET_TABLES` and therefore NOT queryable via SQL Explorer. All four current `DATASET_TABLES` entries have corresponding `_DATASET_PERMISSION_MAP` entries. Adding a new dataset requires both a `DATASET_TABLES` entry AND a `_DATASET_PERMISSION_MAP` entry.
- **Authorization helper:** Use the dedicated `can_query_dataset()` helper (default-deny for unmapped datasets) instead of `has_dataset_permission()` directly, because `has_dataset_permission()` returns `False` for unknown mappings and `can_query_dataset()` provides explicit logging for unmapped-but-present datasets.
- **Permission note:** SQL Explorer access is controlled entirely by the `QUERY_DATA` permission (currently granted to OPERATOR and ADMIN roles). If researchers need SQL access in the future, add `QUERY_DATA` to the RESEARCHER role mapping — no new roles are required.

**Dataset authorization helper:**
```python
# Mapping from DATASET_TABLES keys to DatasetPermission enum values.
# DEFAULT-DENY: every dataset in DATASET_TABLES MUST have an entry here.
# If a new dataset is added to DATASET_TABLES without a mapping, it is
# denied by default to prevent accidental licensing/security leakage.
_DATASET_PERMISSION_MAP: dict[str, DatasetPermission] = {
    "crsp": DatasetPermission.CRSP_ACCESS,
    "compustat": DatasetPermission.COMPUSTAT_ACCESS,
    "taq": DatasetPermission.TAQ_ACCESS,
    "fama_french": DatasetPermission.FAMA_FRENCH_ACCESS,
}

def can_query_dataset(user: Any, dataset: str) -> bool:
    """Check if user can query a given dataset in SQL Explorer.

    DEFAULT-DENY: every queryable dataset MUST have an entry in
    _DATASET_PERMISSION_MAP. Unmapped datasets are denied even if they
    appear in DATASET_TABLES, preventing accidental exposure of future
    licensed datasets that are added to DATASET_TABLES without a
    corresponding permission mapping.

    Always requires QUERY_DATA as a baseline.
    """
    if not has_permission(user, Permission.QUERY_DATA):
        return False
    if dataset not in DATASET_TABLES:
        return False
    # DEFAULT-DENY: unmapped datasets are blocked
    if dataset not in _DATASET_PERMISSION_MAP:
        logger.warning("sql_explorer_unmapped_dataset_denied", extra={"dataset": dataset})
        return False
    return has_dataset_permission(user, _DATASET_PERMISSION_MAP[dataset])
```

**Query execution callback:**
```python
_query_running = False
_last_result_dataset: str | None = None  # Dataset that produced current grid results

async def run_query() -> None:
    nonlocal _query_running
    if _query_running:
        ui.notify("Query already running", type="info")
        return
    _query_running = True
    status_label.text = "Running..."

    query = query_textarea.value.strip()
    dataset = dataset_select.value
    timeout = int(timeout_select.value)
    max_rows = min(int(max_rows_input.value), _MAX_ROWS_LIMIT)
    limited_query: str | None = None  # Initialized before try to avoid UnboundLocalError

    # Page is a THIN ORCHESTRATOR: all validation, rate limiting, execution,
    # and audit logging is handled by SqlExplorerService. The page only
    # renders UI responses. This prevents double-enforcement of security controls.
    try:
        qr = await sql_explorer_service.execute_query(
            user=user,
            dataset=dataset,
            query=query,
            timeout_seconds=timeout,
            max_rows=max_rows,
            available_tables=page_available_tables[dataset],
        )

        # Cell count safety cap — prevent browser OOM on wide results.
        # NOTE: This is a UI-level check. The backend SqlExplorerService ALSO
        # enforces a cell count cap (rows * columns <= _MAX_CELLS) to fail-fast
        # before data loading — see _execute_query() server-side check below.
        _MAX_CELLS = 1_000_000
        cell_count = len(qr.df) * len(qr.df.columns)
        if cell_count > _MAX_CELLS:
            ui.notify(f"Result too large ({cell_count:,} cells). Add more filters.", type="warning")
            status_label.text = f"Result too large ({cell_count:,} cells)"
            return

        # Display results in AG Grid
        grid.options["columnDefs"] = [
            {"field": col, "headerName": col, "sortable": True, "filter": True}
            for col in qr.df.columns
        ]
        grid.options["rowData"] = qr.df.to_dicts()
        grid.update()

        nonlocal _last_result_dataset
        _last_result_dataset = dataset
        status_label.text = f"{len(qr.df)} rows in {qr.execution_ms}ms"
        _add_to_history(qr.fingerprint, dataset, "success", len(qr.df))

    except PermissionError as e:
        ui.notify(str(e), type="negative")
        status_label.text = "Authorization denied"
    except ValueError as e:
        ui.notify(f"Validation failed: {e}", type="negative")
        status_label.text = "Validation failed"
    except RuntimeError as e:
        ui.notify(str(e), type="warning")
        status_label.text = str(e)
    except asyncio.TimeoutError:
        ui.notify(f"Query timed out after {timeout}s", type="negative")
        status_label.text = f"Timeout after {timeout}s"
        _add_to_history("<timed out>", dataset, "timeout", 0)
    except Exception:
        logger.exception("sql_query_error", extra={"dataset": dataset})
        ui.notify("Query execution failed", type="negative")
        status_label.text = "Error"
    finally:
        _query_running = False
```

**CSV export callback:**
```python
async def export_csv() -> None:
    # Use _last_result_dataset (set at query time) to ensure export
    # authorization matches the dataset that actually produced the grid data,
    # not the currently selected dropdown value (which user may have changed).
    export_dataset = _last_result_dataset or dataset_select.value
    row_data = grid.options.get("rowData", [])
    if not row_data:
        ui.notify("No data to export", type="info")
        return
    df = pl.DataFrame(row_data)
    # Page is a THIN ORCHESTRATOR: all authorization, rate limiting, and
    # audit logging is handled by SqlExplorerService.export_csv(). The page
    # only renders UI responses. This prevents double-enforcement.
    try:
        csv_bytes = await sql_explorer_service.export_csv(
            user=user, dataset=export_dataset, df=df,
        )
    except PermissionError as e:
        ui.notify(str(e), type="negative")
        return
    except RuntimeError as e:
        ui.notify(str(e), type="warning")
        return
    # Nanosecond timestamp prevents filename collision within same second
    ui.download(csv_bytes, filename=f"query_results_{time.time_ns()}.csv")
```

**Files to create:**
- `libs/web_console_services/sql_explorer_service.py` — `SqlExplorerService` with all security-critical SQL execution logic (validation, connection, timeout, audit)
- `apps/web_console_ng/pages/sql_explorer.py` — Thin NiceGUI page orchestrating UI (editor, AG Grid, buttons) delegating all execution to `SqlExplorerService`

**Files to modify:**
- `apps/web_console_ng/pages/__init__.py` — Register new page import

**Acceptance Criteria:**
- [ ] SQL editor with monospace font and placeholder text
- [ ] Dataset selector populated from authorized + available datasets only (intersection of `can_query_dataset()` grants and `available_tables_by_dataset` keys)
- [ ] Available tables shown for selected dataset as info text
- [ ] `SQLValidator.validate()` called before every query execution
- [ ] DDL statements blocked (CREATE, DROP, ALTER, TRUNCATE)
- [ ] DML statements blocked (INSERT, UPDATE, DELETE, MERGE)
- [ ] Only SELECT queries allowed (including CTEs and set operations)
- [ ] Schema-qualified tables blocked (e.g., `other_schema.table`)
- [ ] Blocked functions rejected (file I/O, remote access, system functions)
- [ ] Sensitive table access blocked via defense-in-depth check (users, api_keys, secrets)
- [ ] Row limit enforced (default 10,000, configurable up to 50,000) via `enforce_row_limit()`
- [ ] Timeout enforced (default 30s, max 120s) via `asyncio.wait_for()` with `conn.interrupt()` on timeout
- [ ] Concurrent query cap (default 3) prevents resource exhaustion under heavy load
- [ ] Server-side rate limiting: 10 queries/min per user, 5 exports/hour per user (matches `DataExplorerService` limits via `RateLimiter`)
- [ ] Timeout selector offers: 10s, 30s, 60s, 120s options
- [ ] Results displayed in AG Grid with dynamic columns, sorting, and filtering
- [ ] CSV export button (requires `EXPORT_DATA` permission)
- [ ] Export generates valid CSV file with download prompt
- [ ] All queries logged with user, timestamp, dataset, query fingerprint (redacted), status, execution time, row count — including denied, failed, and timed-out queries (not just success)
- [ ] Raw SQL never in standard logs — gated behind `SQL_EXPLORER_AUDIT_RAW_SQL` env var for restricted sinks only
- [ ] Permission check: `QUERY_DATA` required for page access and query execution
- [ ] All security-critical logic (validation, execution, audit, timeout, connection) centralized in `SqlExplorerService` (service layer, not page module)
- [ ] Deployment constraint documented: web console host requires no outbound network + read-only FS scope to `data/` directory
- [ ] Dataset authorization: `can_query_dataset(user, dataset)` enforced before query execution and CSV export (default-deny: all datasets require explicit `_DATASET_PERMISSION_MAP` entry)
- [ ] Dataset selector filtered to only show datasets the user is authorized for
- [ ] Overlap guard: prevents concurrent query execution (button disabled while running)
- [ ] Query timeout shows user-friendly error message ("Query timed out after Xs")
- [ ] Session-scoped query history (in-memory, up to 20 entries, click to replay fingerprinted SQL only)
- [ ] DuckDB connections use validated Parquet-backed views (no runtime lockdown — security via SQLValidator + sensitive table check)
- [ ] Empty state: "Run a query to see results" when no results

**Unit Tests (categorization: S = security-critical mandatory, F = functional, R = regression/robustness):**

*Security-critical (S) — MUST pass before merge, blocks release:*
- [S] SQL validation: valid SELECT passes validation
- [S] SQL validation: DDL (CREATE, DROP) rejected
- [S] SQL validation: DML (INSERT, UPDATE, DELETE) rejected
- [S] SQL validation: multi-statement queries rejected (via AST statement count, not string search)
- [S] SQL validation: queries with semicolons inside string literals are accepted (e.g., `WHERE col = 'val;ue'`)
- [S] SQL validation: unknown dataset rejected
- [S] SQL validation: tables not in dataset whitelist rejected
- [S] SQL validation: schema-qualified tables rejected
- [S] SQL validation: blocked functions (read_parquet, etc.) rejected — direct call
- [S] SQL validation: blocked functions in nested subquery rejected (`SELECT * FROM (SELECT read_parquet(...))`)
- [S] SQL validation: blocked functions in CTE rejected (`WITH t AS (SELECT read_parquet(...))`)
- [S] SQL validation: blocked functions in WHERE subquery rejected (`WHERE col IN (SELECT read_parquet(...))`)
- [S] SQL validation: blocked functions in lateral join rejected
- [S] SQL validation: anonymous function names rejected (`exp.Anonymous` node)
- [S] SQL validation: COPY/ATTACH/INSTALL/LOAD/EXPORT commands rejected (`exp.Command`)
- [S] SQL validation: SET statements rejected (`exp.Set`)
- [S] SQL validation: PRAGMA statements rejected (`exp.Pragma`)
- [S] SQL validation: trailing semicolon accepted (`SELECT 1;` → single statement)
- [S] SQL validation: semicolons in comments accepted (`SELECT 1 -- comment;`)
- [S] Sensitive table blocking: "SELECT * FROM users" raises `SensitiveTableAccessError`
- [S] Sensitive table blocking: whitelist-allowed table passes `_check_sensitive_tables()` without exception
- [S] Sensitive table prefix: `auth_users`, `user_roles`, `secret_keys` blocked by prefix matching
- [S] Path validation: `_validate_path_safe()` rejects `..` traversal in pre-glob prefix (e.g., `data/../etc/passwd`)
- [S] Path validation: `_validate_path_safe()` rejects `..` traversal in post-glob suffix (e.g., `data/*.parquet/../secret`)
- [S] Path validation: `_validate_path_safe()` rejects backslashes (`\\`), quotes, and control chars
- [S] Path validation: `_validate_path_safe()` accepts absolute paths that resolve under allowed data root
- [S] Error redaction: `_safe_error_message()` returns canonical code, never raw parser error containing SQL
- [S] Query fingerprinting: `_fingerprint_query()` handles unparseable SQL gracefully (returns `"<unparseable query>"`, never raw SQL)
- [S] DuckDB security: extension autoloading disabled (`enable_extension_autoloading=false`, `enable_extension_loading=false`) on connection creation
- [S] DuckDB security: extension verification rejects connection if restricted extension pre-loaded (e.g., httpfs via ~/.duckdbrc)
- [S] Permission checks: page requires `QUERY_DATA`, export requires `EXPORT_DATA`
- [S] Dataset authorization: user without dataset entitlement cannot execute queries against that dataset
- [S] Dataset authorization: user without dataset entitlement cannot export CSV for that dataset
- [S] CSV export: `SqlExplorerService.export_csv()` enforces `EXPORT_DATA` permission (raises PermissionError)
- [S] CSV export: `SqlExplorerService.export_csv()` enforces dataset authorization via `can_query_dataset()` (raises PermissionError)
- [S] Rate limiting: Redis outage with `fallback_mode="deny"` blocks queries (fail-closed)
- [S] Rate limiting: `rate_limiter=None` without `SQL_EXPLORER_DEV_MODE=true` raises `ValueError` at construction
- [S] Rate limiting: `SQL_EXPLORER_DEV_MODE=true` + `APP_ENV=production` raises `RuntimeError` (production guardrail prevents accidental dev-mode in production)
- [S] Rate limiting: `RateLimiter(fallback_mode="allow")` raises `ValueError` at construction (must be `"deny"`)
- [S] `can_query_dataset()`: returns False for unmapped dataset (default-deny with warning log)
- [S] `can_query_dataset()`: returns False when user lacks QUERY_DATA (even if dataset is mapped)

*Functional (F) — core behavior, must pass:*
- [F] Row limit enforcement: query without LIMIT gets default limit added
- [F] Row limit enforcement: query with LIMIT > max gets clamped to max
- [F] Row limit enforcement: query with LIMIT <= max passes through unchanged
- [F] Timeout enforcement: mock slow query raises `asyncio.TimeoutError`
- [F] Timeout interrupt: `conn.interrupt()` called on timeout to cancel running DuckDB query
- [F] Timeout clamping: user-requested timeout > 120s clamped to 120s
- [F] Concurrent query cap: 4th simultaneous query raises `ConcurrencyLimitError` with user-friendly message
- [F] Concurrent query cap: counter correctly decremented after query completion (success, timeout, error) — guarded by `acquired_slot` flag
- [F] Concurrent query cap: counter NOT decremented when `ConcurrencyLimitError` raised (slot never acquired)
- [F] Query fingerprinting: `_fingerprint_query()` replaces string literals with `?`
- [F] Query fingerprinting: `_fingerprint_query()` replaces numeric literals with `?`
- [F] CSV export: `SqlExplorerService.export_csv()` generates valid CSV bytes
- [F] CSV export: `SqlExplorerService.export_csv()` enforces rate limiting (5/hour, raises RuntimeError)
- [F] CSV export: `SqlExplorerService.export_csv()` logs audit entry on success
- [F] CSV export: page delegates entirely to `SqlExplorerService.export_csv()` — no auth/rate-limit logic in page
- [F] CSV export: empty result set returns "No data to export" notification (page-level check before service call)
- [F] CSV export: filename uses `time.time_ns()` for nanosecond-resolution collision avoidance
- [F] Dataset selector: only shows datasets the user is authorized for AND that have available Parquet-backed tables
- [F] Overlap guard: second `run_query()` call while first running returns early with notification
- [F] DuckDB table registration: `_create_query_connection()` registers correct Parquet-backed views for each dataset table
- [F] DuckDB correctness: views are queryable after creation (SELECT against registered view returns data)
- [F] DuckDB resource: `SQL_EXPLORER_MAX_MEMORY_MB` env var configures per-connection memory budget (default 512MB)
- [F] DuckDB resource: invalid `SQL_EXPLORER_MAX_MEMORY_MB` value falls back to 512MB with warning log
- [F] DuckDB resource: non-positive `SQL_EXPLORER_MAX_MEMORY_MB` value clamped to 64MB minimum
- [F] Path validation: `_validate_table_paths()` returns per-table availability (dict[str, set[str]]) and warnings for missing paths
- [F] Path validation: dataset with no valid Parquet files excluded from available_tables_by_dataset dict
- [F] Path validation: dataset with partial table coverage only includes tables with valid Parquet files
- [F] DuckDB registration: `_create_query_connection()` only registers views for tables in available set
- [F] `can_query_dataset()`: returns True for licensed dataset (crsp) when user has entitlement
- [F] `can_query_dataset()`: returns False for licensed dataset (crsp) when user lacks entitlement
- [F] `can_query_dataset()`: returns False for dataset not in DATASET_TABLES
- [F] Graceful degradation: page renders with disabled controls when `_DATA_ROOT_AVAILABLE=False` (no RuntimeError crash)
- [F] Graceful degradation: page renders normally when `_DATA_ROOT_AVAILABLE=True`
- [F] Rate limiting: 11th query within 60s returns rate-limit warning (mock RateLimiter)
- [F] Rate limiting: 6th export within 3600s returns rate-limit warning (mock RateLimiter)
- [F] Rate limiting: query and export succeed when under rate limit
- [F] Rate limiting: `rate_limiter=None` accepted only when `SQL_EXPLORER_DEV_MODE=true` (logs warning)
- [F] Rate limiting: page constructs dedicated `RateLimiter(redis_client=..., fallback_mode="deny")` instance, not `get_rate_limiter()` singleton
- [F] `execute_query`: returns `QueryResult(df, execution_ms, fingerprint)` — page accesses typed fields, no loose service attributes
- [F] `execute_query`: `available_tables` parameter passed through to `_create_query_connection()`
- [F] `execute_query`: `available_tables=None` resolved to `set(DATASET_TABLES[dataset])` before passing to `_create_query_connection()`
- [F] Cell count cap: result exceeding 1M cells (rows × columns) rejected with user-friendly warning


*Audit logging (F) — complete status coverage:*
- [F] Query audit logging: verify `_log_query()` produces `query_fingerprint` but NOT `original_query`/`executed_query` by default
- [F] Query audit logging: with `_AUDIT_LOG_RAW_SQL=true`, `original_query` and `executed_query` fields are included
- [F] Query text truncation: raw queries > 2000 chars truncated when `_AUDIT_LOG_RAW_SQL=true`
- [F] Audit logging: authorization denied → status `"authorization_denied"`
- [F] Audit logging: validation error → status `"validation_error"` with redacted error
- [F] Audit logging: sensitive table block → status `"security_blocked"` (distinct from `"validation_error"`)
- [F] Audit logging: rate-limited → status `"rate_limited"`
- [F] Audit logging: concurrency cap → status `"concurrency_limit"` (distinct from `"rate_limited"`)
- [F] Audit logging: timed-out → status `"timeout"`
- [F] Audit logging: unexpected error → status `"error"` with redacted error
- [F] Audit logging: all 8 status values (7 `_ERROR_CODES` keys + `"success"`) have corresponding test cases
- [F] Audit log: `query_id` (UUID) present in all log entries for cross-entry correlation
- [F] Audit log: `query_fingerprint` present in all log entries; `original_query` absent by default
- [F] Audit log: `query_modified` flag correctly set based on original vs executed query comparison
- [F] Audit log: startup warning logged when `SQL_EXPLORER_AUDIT_RAW_SQL=true`
- [F] Exception types: `SensitiveTableAccessError(ValueError)` caught before `ValueError` for deterministic classification
- [F] Exception types: `ConcurrencyLimitError(RuntimeError)` caught before `RuntimeError` for deterministic classification

*Regression/robustness (R) — defense-in-depth, advisory:*
- [R] Sandbox verification: `_verify_sandbox()` returns `(True, [])` when both probes pass (simulated via mock)
- [R] Sandbox verification: `_verify_sandbox()` returns `(False, ["network_egress_allowed"])` when network egress succeeds
- [R] Sandbox verification: `_verify_sandbox()` returns `(False, ["filesystem_write_allowed:{forbidden_dir}"])` when filesystem write to a forbidden path succeeds (token format matches code at `failures.append(f"filesystem_write_allowed:{forbidden_dir}")`)
- [R] Sandbox verification: service logs warning with failure reasons when `_verify_sandbox()` returns `False` (advisory — all environments)
- [R] Sandbox verification: probe failure does NOT raise or block startup (advisory; hard gate is deploy-time manifest validation)
- [R] Sandbox verification: `SQL_EXPLORER_SANDBOX_SKIP=true` skips all probes and logs warning (dev mode)
- [R] PROJECT_ROOT: fail-closed in production when no marker found and `SQL_EXPLORER_DEV_MODE` not set
- [R] PROJECT_ROOT: CWD fallback allowed when `SQL_EXPLORER_DEV_MODE=true`
- [R] PROJECT_ROOT: explicit `PROJECT_ROOT` env var always used if set
- [R] Query history: entries store fingerprinted SQL only (no raw SQL), limited to 20 entries
- [R] Query history: `_add_to_history()` receives fingerprinted query from `_fingerprint_query()`, never raw SQL
- [R] Table name validation: `_create_query_connection()` rejects non-identifier table names via regex
- [R] DDL identifier quoting: table names double-quoted in CREATE VIEW to handle reserved words
- [R] Export authorization: uses `_last_result_dataset` not `dataset_select.value` to prevent stale-data export bypass
- [R] Rate limiter signature: uses `window_seconds=` and `get_user_id(user)` matching `RateLimiter` API
- [R] Status label: set to terminal text on each early return (rate limited, authorization denied, validation failed, blocked)
- [R] Path cache: second page load within TTL returns cached results without filesystem scan

**Service Test File:** `tests/libs/web_console_services/test_sql_explorer_service.py` (validation, execution, audit, timeout, security)
**Page Test File:** `tests/apps/web_console_ng/pages/test_sql_explorer.py` (UI orchestration, AG Grid, buttons)

---

### T14.4: Shadow Mode Results - LOW PRIORITY

**Goal:** Visualize shadow validation results with accuracy metrics, prediction comparison, and trend visualization.

**Current State Analysis:**
- `ShadowModeValidator` exists in `apps/signal_service/shadow_validator.py` with:
  - `ShadowValidationResult` frozen dataclass: `passed`, `correlation`, `mean_abs_diff_ratio`, `sign_change_rate`, `sample_count`, `old_range`, `new_range`, `message`
  - Validation logic: passes when `correlation >= threshold AND divergence <= threshold`
  - Default thresholds: correlation >= 0.5, divergence <= 0.5
- Shadow validation runs during model hot-swap in signal service (`apps/signal_service/main.py` lines 1091-1133)
- Results are **only recorded to Prometheus metrics** (gauges: `signal_service_shadow_validation_correlation`, `_mean_abs_diff_ratio`, `_sign_change_rate`; counter: `_total` with status label: passed/rejected/failed/skipped)
- Results are NOT persisted to database — ephemeral in-process state
- No UI exists for viewing shadow validation results
- Historical results unavailable without Prometheus range query

**Approach:**

#### Backend: `libs/web_console_services/shadow_results_service.py`

Create a service that provides shadow validation results. Initially returns **mock data** (consistent with P6T13 pattern). Future upgrade paths:
1. Query Prometheus via HTTP API (`/api/v1/query_range`) for historical metrics
2. Add DB persistence in signal service to store `ShadowValidationResult` on each validation run

1. **DTOs** (add to `libs/web_console_services/schemas/data_management.py`):
   ```python
   class ShadowResultDTO(BaseModel):
       id: str
       model_version: str             # Model version identifier
       strategy: str                  # Strategy name (e.g., "alpha_baseline")
       validation_time: AwareDatetime # When validation ran
       passed: bool                   # Overall pass/fail
       correlation: float             # Prediction correlation (0.0-1.0)
       mean_abs_diff_ratio: float     # Divergence metric (0.0+)
       sign_change_rate: float        # Rate of prediction sign flips (0.0-1.0)
       sample_count: int              # Number of feature samples used
       old_range: float               # Old model prediction range (max - min)
       new_range: float               # New model prediction range (max - min)
       message: str                   # Human-readable result description
       correlation_threshold: float   # Threshold used (default 0.5)
       divergence_threshold: float    # Threshold used (default 0.5)

   class ShadowTrendPointDTO(BaseModel):
       date: AwareDatetime
       correlation: float
       mean_abs_diff_ratio: float
       sign_change_rate: float
       passed: bool

   class ShadowTrendDTO(BaseModel):
       strategy: str
       period_days: int
       data_points: list[ShadowTrendPointDTO]
       total_validations: int
       pass_rate: float               # Percentage passed (0.0-100.0)
       avg_correlation: float | None  # Average correlation, None if no validations
       avg_divergence: float | None   # Average divergence, None if no validations
   ```

2. **Service class:**
   ```python
   _MAX_RESULTS_LIMIT = 200
   _MAX_TREND_DAYS = 365

   class ShadowResultsService:
       async def get_recent_results(
           self,
           user: Any,
           strategy: str | None = None,
           limit: int = 50,
       ) -> list[ShadowResultDTO]:
           """Return recent shadow validation results.
           NOTE: Returns mock data. Upgrade to Prometheus query or DB persistence
           when signal service stores results.
           Limit clamped to [1, _MAX_RESULTS_LIMIT] to prevent unbounded queries."""
           if not has_permission(user, Permission.VIEW_SHADOW_RESULTS):
               raise PermissionError("Permission 'view_shadow_results' required")
           clamped_limit = max(1, min(limit, _MAX_RESULTS_LIMIT))
           # Mock data: generate exactly clamped_limit results across the last
           # 30 days. For limit values > mock horizon, repeat recent dates.
           # This ensures "respects limit" tests pass for any valid value.

       async def get_trend(
           self,
           user: Any,
           strategy: str | None = None,
           days: int = 30,
       ) -> ShadowTrendDTO:
           """Return shadow validation trend data for charting.
           NOTE: Returns mock data with realistic trend patterns.
           Days clamped to [1, _MAX_TREND_DAYS] to prevent unbounded queries."""
           if not has_permission(user, Permission.VIEW_SHADOW_RESULTS):
               raise PermissionError("Permission 'view_shadow_results' required")
           clamped_days = max(1, min(days, _MAX_TREND_DAYS))
           # Mock data: generate data points for clamped_days with intentional
           # gaps (~10% of days skipped randomly) to simulate realistic
           # production behavior where validations don't run every day.
           # This enables gap-indicator acceptance tests without fixtures.
   ```

   **Permission check pattern:** Uses explicit `has_permission()` checks inside method bodies (same pattern as T14.2 and existing services). Avoids `@require_permission` decorator which misidentifies `self` as the user on bound instance methods.


   **Mock data generation:**
   - Generate 20-50 mock `ShadowResultDTO` entries with realistic correlation values (0.4-0.95), divergence values (0.1-0.6), and sign change rates (0.05-0.25)
   - ~80% of entries pass validation (matches expected production behavior)
   - Validation times spread over last 30 days
   - Trend data points generated daily from mock results
   - **Deterministic seeding:** Mock data generation accepts an optional `_rng_seed: int | None` parameter (default `None` for production, fixed seed for tests). Unit tests MUST use a fixed seed to ensure deterministic assertions (exact counts, not probabilistic ranges). Example: `_rng_seed=42` produces exactly 40 results with exactly 32 passed (80%).

   **Pass rate calculation:**
   Summary card metrics MUST be derived from the same `results` list used to populate the results table, NOT from independent trend data points. This ensures internal consistency between the card and table views.
   ```python
   pass_rate = (sum(1 for r in results if r.passed) / len(results)) * 100 if results else 0.0
   avg_correlation = sum(r.correlation for r in results) / len(results) if results else None
   avg_divergence = sum(r.mean_abs_diff_ratio for r in results) / len(results) if results else None
   ```

#### Frontend: `apps/web_console_ng/pages/shadow_results.py`

New NiceGUI page at route `/data/shadow`.

**Page structure:**
1. **Summary cards row:**
   - Total Validations (count)
   - Pass Rate (percentage, color: >= 90% green, 70-89% amber, < 70% red)
   - Average Correlation (value, 2 decimal places)
   - Average Divergence (value, 2 decimal places)

2. **Trend chart** (Plotly):
   ```python
   fig = go.Figure()
   fig.add_trace(go.Scatter(
       x=[p.date for p in trend.data_points],
       y=[p.correlation for p in trend.data_points],
       mode="lines+markers", name="Correlation",
       line=dict(color="blue"),
   ))
   # Divergence on secondary Y-axis (right) to prevent scale compression
   # when divergence >> correlation (e.g., divergence=5.0 vs correlation=0.8)
   fig.add_trace(go.Scatter(
       x=[p.date for p in trend.data_points],
       y=[p.mean_abs_diff_ratio for p in trend.data_points],
       mode="lines+markers", name="Divergence",
       line=dict(color="orange"),
       yaxis="y2",
   ))
   # Threshold lines (on primary Y-axis for correlation)
   fig.add_hline(y=0.5, line_dash="dash", line_color="red",
                  annotation_text="Threshold (0.5)")
   # Add shaded regions for days with no validation data to distinguish
   # "no runs" from "flatline" — prevents operators from misinterpreting gaps
   # as system failures. Use vrect with semi-transparent gray fill.
   if trend.data_points:
       dates = sorted(p.date for p in trend.data_points)
       for i in range(1, len(dates)):
           gap_days = (dates[i] - dates[i - 1]).days
           if gap_days > 1:
               fig.add_vrect(
                   x0=dates[i - 1], x1=dates[i],
                   fillcolor="gray", opacity=0.15, line_width=0,
                   annotation_text="No data" if gap_days > 3 else None,
               )
   fig.update_layout(
       title="Shadow Validation Trends",
       xaxis_title="Date",
       yaxis_title="Correlation",
       yaxis=dict(range=[0, 1.05]),
       yaxis2=dict(title="Divergence", overlaying="y", side="right"),
   )
   ui.plotly(fig).classes("w-full")
   ```

3. **Results table** (AG Grid):
   - Columns: Time (relative), Model Version, Status (passed/failed with color), Correlation, Divergence, Sign Change Rate, Sample Count, Message
   - Status cell style: passed=green background, failed=red background
   - Sortable by all columns, filterable by status
   - `apply_compact_grid_options()` for consistent styling

4. **Result detail panel** (expandable on row click):
   - Full validation metrics with labels
   - Threshold comparison: correlation vs threshold, divergence vs threshold
   - Pass/fail reasoning from `message` field
   - Prediction range comparison: old_range vs new_range (table format)

5. **Auto-refresh timer** — 60s interval with overlap guard and timer cleanup:
   ```python
   timer_shadow = ui.timer(60.0, refresh_shadow_results)
   _CLEANUP_OWNER_KEY = "shadow_results_timers"
   lifecycle = ClientLifecycleManager.get()
   client_id = get_or_create_client_id()
   if client_id:
       await lifecycle.register_cleanup_callback(
           client_id, timer_shadow.cancel, owner_key=_CLEANUP_OWNER_KEY
       )
   ```

**Permission model:**
- Page visibility: `VIEW_SHADOW_RESULTS` (RESEARCHER, OPERATOR, ADMIN)

**Error handling:**
```python
try:
    results = await shadow_service.get_recent_results(user)
    trend = await shadow_service.get_trend(user)
except PermissionError as e:
    ui.notify(str(e), type="negative")
    return
except Exception:
    logger.exception("shadow_results_failed", extra={
        "service": "ShadowResultsService",
        "user_id": get_user_id(user),
    })
    ui.notify("Shadow results unavailable", type="warning")
    return
```

**Empty state:**
- If no shadow validations exist: show informational message "No shadow validations recorded. Shadow validation runs automatically during model hot-swap when `SHADOW_VALIDATION_ENABLED=true`."

**Files to create:**
- `libs/web_console_services/shadow_results_service.py` — Service with mock data
- `apps/web_console_ng/pages/shadow_results.py` — NiceGUI page

**Files to modify:**
- `libs/web_console_services/schemas/data_management.py` — Add `ShadowResultDTO`, `ShadowTrendPointDTO`, `ShadowTrendDTO`
- `apps/web_console_ng/pages/__init__.py` — Register new page import

**Acceptance Criteria:**
- [ ] Summary cards: total validations, pass rate, avg correlation, avg divergence
- [ ] Pass rate color-coded: >= 90% green, 70-89% amber, < 70% red
- [ ] Plotly trend chart with correlation (blue) and divergence (orange) lines
- [ ] Threshold line at 0.5 shown as dashed red line
- [ ] Y-axis auto-scaled: floor at 1.05, expands dynamically for divergence values >1.0
- [ ] Results table in AG Grid with status color coding (passed=green, failed=red)
- [ ] AG Grid uses `apply_compact_grid_options()` for consistent styling
- [ ] Row click shows detail panel with full metrics and threshold comparison
- [ ] Auto-refresh every 60s with overlap guard and timer cleanup
- [ ] Timer cleanup uses `get_or_create_client_id()` and keyed `owner_key`
- [ ] Permission check: `VIEW_SHADOW_RESULTS` required for page access
- [ ] Error handling for service unavailability with structured logging
- [ ] Empty state: informational message when no shadow validations exist
- [ ] Mock data generates realistic values (~80% pass rate, realistic metric ranges)
- [ ] "Preview Data" badge/label visibly displayed on page to indicate mock data is in use

**Unit Tests:**
- `ShadowResultsService.get_recent_results()` returns valid mock data
- `ShadowResultsService.get_recent_results()` respects `limit` parameter
- `ShadowResultsService.get_recent_results()` clamps `limit` to [1, _MAX_RESULTS_LIMIT=200]
- `ShadowResultsService.get_trend()` returns valid trend with data points
- `ShadowResultsService.get_trend()` respects `days` parameter
- `ShadowResultsService.get_trend()` clamps `days` to [1, _MAX_TREND_DAYS=365]
- Permission denied for users without `VIEW_SHADOW_RESULTS`
- Mock data: correlation values in [0.0, 1.0] range
- Mock data: divergence values non-negative
- Mock data: sign_change_rate in [0.0, 1.0] range
- Mock data: deterministic pass rate with seeded RNG (exact count, not probabilistic)
- Trend pass rate calculation: correct percentage from data points
- Trend pass rate: handles zero results (pass_rate=0, avg_correlation=None)
- Summary metrics: correct averages from result set

**Test File:** `tests/libs/web_console_services/test_shadow_results_service.py`

**Page-level tests** (timer/overlap are UI concerns):
- Timer cleanup registered with owner key
- Overlap guard prevents concurrent refresh
- Trend chart gap indicator: shaded gray regions appear for multi-day gaps in validation data (>1 day gap between data points)
- "Preview Data" badge/label rendered on page (mock data disclosure)

**Page Test File:** `tests/apps/web_console_ng/pages/test_shadow_results.py`

---

## Dependencies

```
P6T13.3 Data Services   ──> T14.2 (uses DataSyncService patterns, HealthMonitor infrastructure)
P6T13.4 Quality Dashboard ──> T14.3 (uses validation.py helpers if needed)
P6T13 complete           ──> T14.1-T14.4 (all pages follow P6T13 NiceGUI patterns)

T14 internal:
  Prerequisite (permissions) ──> T14.2, T14.3, T14.1, T14.4 (all tasks need RBAC)
  T14.2 (no internal deps)
  T14.3 (no internal deps)
  T14.1 (no internal deps)
  T14.4 (no internal deps)
```

All four T14 tasks are independent of each other — implementation order is by priority and complexity, not by dependency.

---

## Backend Integration Points

| Feature | Backend Location | Frontend Action |
|---------|-----------------|-----------------|
| SQL Validator | `libs/web_console_services/sql_validator.py` | Validate queries before execution (T14.1) |
| SQL Explorer Service | `libs/web_console_services/sql_explorer_service.py` (`SqlExplorerService`) | Centralized SQL execution, validation, audit, timeout, connection security (T14.1) |
| Health Monitor | `libs/data/data_pipeline/health_monitor.py` | Data source health patterns (T14.2) |
| Data Sync Service | `libs/web_console_services/data_sync_service.py` | Sync status for sources (T14.2) |
| Alpha158 Features | `strategies/alpha_baseline/features.py` | Feature browsing and statistics (T14.3) |
| Shadow Validator | `apps/signal_service/shadow_validator.py` | Result dataclass reference (T14.4) |
| Prometheus Metrics | `apps/signal_service/main.py:1055-1063` | Shadow validation gauges (T14.4 future) |
| Permissions | `libs/platform/web_console_auth/permissions.py` | RBAC enforcement (all tasks) |
| Session Utils | `apps/web_console_ng/utils/session.py` | `get_or_create_client_id()` (T14.2, T14.4) |
| Lifecycle Manager | `apps/web_console_ng/core/client_lifecycle.py` | Timer cleanup callbacks (T14.2, T14.4) |

---

## File Summary

### New Files (8)

| File | Task | Purpose |
|------|------|---------|
| `libs/web_console_services/data_source_status_service.py` | T14.2 | Data source health aggregation service (mock data) |
| `apps/web_console_ng/pages/data_source_status.py` | T14.2 | Data source status NiceGUI page |
| `libs/data/feature_metadata.py` | T14.3 | Feature catalog, metadata dataclasses, statistics computation |
| `apps/web_console_ng/pages/feature_browser.py` | T14.3 | Feature store browser NiceGUI page |
| `libs/web_console_services/sql_explorer_service.py` | T14.1 | SQL execution service with validation, audit, timeout, security |
| `apps/web_console_ng/pages/sql_explorer.py` | T14.1 | SQL Explorer NiceGUI page (thin UI orchestrator) |
| `libs/web_console_services/shadow_results_service.py` | T14.4 | Shadow validation results service (mock data) |
| `apps/web_console_ng/pages/shadow_results.py` | T14.4 | Shadow mode results NiceGUI page |

### Modified Files (5 feature + additional DoD artifacts)

| File | Task | Change |
|------|------|--------|
| `libs/platform/web_console_auth/permissions.py` | All | Add `VIEW_FEATURES`, `VIEW_SHADOW_RESULTS` enums and role mappings |
| `libs/web_console_services/schemas/data_management.py` | T14.2, T14.4 | Add `DataSourceStatusDTO`, `ShadowResultDTO`, `ShadowTrendPointDTO`, `ShadowTrendDTO` |
| `libs/web_console_services/sql_validator.py` | T14.1 | Replace blunt semicolon check with AST-based multi-statement detection (`len(expressions) != 1`). **MUST be bundled atomically with corresponding test updates** in `tests/libs/web_console_services/test_sql_validator.py` (existing tests assert old error path). `DATASET_TABLES` mapping unchanged — logical-to-physical table resolution handled by new `_resolve_table_paths()` in `SqlExplorerService` (Option b). |
| `apps/web_console_ng/pages/__init__.py` | All | Register 4 new page imports |
| `apps/web_console_ng/ui/layout.py` | All | Refactor nav loop to honor `required_permission` generically (replacing hardcoded `/admin` check), migrate Admin entry to use permission tuple, add 4 T14 nav items with permission-gated visibility |

### Additional DoD Artifacts (modified as part of completion)

| File | Task | Change |
|------|------|--------|
| `.github/workflows/ci-tests-parallel.yml` | T14.1/T14.3 | Add feature catalog parity job, trading-safety path assertion |
| `docs/GETTING_STARTED/PROJECT_STATUS.md` | Completion | Mark T14 tasks complete |
| `docs/GETTING_STARTED/REPO_MAP.md` | Completion | Add new file paths |
| `docs/RUNBOOKS/ops.md` | T14.1 | SQL Explorer deployment attestation checklist |
| `docs/STANDARDS/CODING_STANDARDS.md` | T14 | Add T14 logging schema exception cross-reference |

**Navigation integration:** Add 4 flat nav items to the existing `nav_items` list in `apps/web_console_ng/ui/layout.py` (in the `nav_items` variable definition, follows existing flat-list pattern — minimal targeted refactor of the nav permission loop only, NO structural layout redesign):

| Nav Label | Route | Icon | Required Permission |
|-----------|-------|------|-------------------|
| Data Sources | `/data/sources` | `storage` | `VIEW_DATA_SYNC` |
| Features | `/data/features` | `category` | `VIEW_FEATURES` |
| SQL Explorer | `/data/sql-explorer` | `terminal` | `QUERY_DATA` |
| Shadow Results | `/data/shadow` | `compare` | `VIEW_SHADOW_RESULTS` |

**Nav visibility enforcement:** The current `layout.py` nav-item loop ignores the `_required_role` tuple field; permission gating is hardcoded only for the `/admin` entry. To support per-item permission gating for T14 nav items, the loop **must be refactored** to honor the 4th tuple element as a permission requirement:

The 4th tuple element supports three forms:
- `None` — visible to all authenticated users (no permission check)
- `Permission.X` — single permission required (AND semantics)
- `(Permission.X, Permission.Y, ...)` — **any-of** semantics (user needs at least ONE)

```python
for label, path, icon, required_permission in nav_items:
    if required_permission is not None:
        if isinstance(required_permission, tuple):
            # Any-of (OR) semantics: user needs at least ONE of these permissions
            if not any(has_permission(user, p) for p in required_permission):
                continue
        else:
            # Single permission (AND) semantics
            if not has_permission(user, required_permission):
                continue
    # ... render nav item ...
```

This is a **prerequisite refactor** to avoid leaking nav items to unauthorized users. The existing `/admin` hardcoded check should be migrated to use `required_permission` for consistency. Existing nav items with `None` as the 4th element remain visible to all authenticated users (unchanged behavior).

**Admin regression protection (MANDATORY):** During the nav refactor, the `/admin` entry MUST be updated from its current hardcoded check to use the generic `required_permission` mechanism with **any-of (OR) semantics** to preserve current behavior. The Admin tuple should change from `("Admin", "/admin", "settings", None)` to `("Admin", "/admin", "settings", (Permission.MANAGE_API_KEYS, Permission.MANAGE_SYSTEM_CONFIG, Permission.VIEW_AUDIT))`. This preserves the current OR-based authorization where users with ANY of these three permissions can see the Admin nav item. **Regression test required:** Verify that after the refactor: (a) users with ONLY `MANAGE_API_KEYS` CAN see Admin, (b) users with ONLY `VIEW_AUDIT` CAN see Admin, (c) users with ONLY `MANAGE_SYSTEM_CONFIG` CAN see Admin, (d) users with NONE of these three permissions CANNOT see Admin. This test must live in the **UI test module** (`tests/apps/web_console_ng/ui/test_layout.py` or `tests/apps/web_console_ng/test_navigation.py`) because it validates nav rendering/visibility behavior, not permission enum logic. Keep permission-unit tests (enum membership, role assignments) in the auth test module separately. This test must pass before any T14 nav items are added.

**Route-level authorization (MANDATORY — independent of nav visibility):** Nav visibility is a UX convenience, NOT a security boundary. Every T14 page MUST enforce its own page-level permission check at the route handler level (e.g., `has_permission(user, Permission.QUERY_DATA)` at the top of the SQL Explorer page function), following the **existing in-page deny pattern** used by other pages (e.g., `execution_quality.py:214-218`):

```python
if not has_permission(user, Permission.QUERY_DATA):
    ui.notify("Permission denied: QUERY_DATA required", type="negative")
    with ui.card().classes("w-full p-6"):
        ui.label("Permission denied: QUERY_DATA required.").classes("text-red-500 text-center")
    return
```

This returns HTTP 200 with a visible "Permission denied" card — consistent with the existing NiceGUI page pattern (not HTTP 403/redirect). **Test required:** Each page test file must include a test case for unauthorized direct route access verifying the permission-denied card is rendered.

**Nav visibility tests:** Add assertions in each page's test file verifying that the nav item appears for authorized users and is absent for unauthorized users (e.g., VIEWER should not see "SQL Explorer" nav item since they lack `QUERY_DATA`).

### New Test Files (9)

| File | Task |
|------|------|
| `tests/libs/platform/web_console_auth/test_permissions_t14.py` | All |
| `tests/libs/web_console_services/test_data_source_status_service.py` | T14.2 |
| `tests/apps/web_console_ng/pages/test_data_source_status.py` | T14.2 |
| `tests/libs/data/test_feature_metadata.py` | T14.3 |
| `tests/integration/test_feature_catalog_parity.py` | T14.3 |
| `tests/libs/web_console_services/test_sql_explorer_service.py` | T14.1 |
| `tests/apps/web_console_ng/pages/test_sql_explorer.py` | T14.1 |
| `tests/libs/web_console_services/test_shadow_results_service.py` | T14.4 |
| `tests/apps/web_console_ng/pages/test_shadow_results.py` | T14.4 |

### ADR Files (1)

| File | Task | Purpose |
|------|------|---------|
| `docs/ADRs/ADR-0035-sql-explorer-security.md` | T14.1 | Documents validator-based containment without DuckDB runtime lockdown. Uses `ADR-0035` naming consistent with existing ADRs. Add explicit entry to ADR index if one exists. |

---

## Risks & Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| SQL injection via DuckDB DDL path interpolation | **HIGH** — data exfiltration or filesystem access | Low | Three-layer defense: SQLValidator AST parsing (Layer 1), sensitive table blocklist (Layer 2), extension lockdown + path validation (Layer 3). Paths from server config only, never user input. `_validate_path_safe()` rejects traversal, quotes, control chars. |
| DuckDB resource exhaustion (OOM/CPU) from complex queries | **HIGH** — web console process crash | Medium | Per-connection memory cap (`SQL_EXPLORER_MAX_MEMORY_MB`, default 512MB), single thread per query, concurrent query cap (3), query timeout (default 30s, max 120s), `conn.interrupt()` on timeout, cell count cap (1M cells). |
| Rate-limit bypass during Redis outage | **MEDIUM** — unbounded query/export abuse | Low | Dedicated `RateLimiter(redis_client=..., fallback_mode="deny")` instance (not `get_rate_limiter()` singleton). `SqlExplorerService.__init__()` asserts `fallback_mode == "deny"` at startup. Redis outage blocks queries rather than allowing unlimited access. |
| Feature catalog drift from Alpha158 runtime | **MEDIUM** — stale metadata shown to researchers | Medium | `validate_catalog_against_runtime()` integration test in CI detects drift. Logged as warning at page load. Static catalog is single source of truth; drift triggers update. |
| Large DataFrames in `app.storage.client` causing memory pressure | **MEDIUM** — server OOM under many concurrent sessions | Low | Cache limited to 30 days × 5 symbols. `ClientLifecycleManager` cleanup on disconnect. **Secondary safety net:** cache entries carry a `cached_at` timestamp; entries older than 30 minutes are evicted on next access (lazy TTL). This handles ungraceful disconnects where `ClientLifecycleManager` callbacks are bypassed. Memory monitoring during rollout. LRU eviction policy as follow-up if needed. |
| Mock data confusion — users mistake mock for real data | **LOW** — incorrect operational decisions | Medium | T14.2 and T14.4 pages display visible "Preview Data" badge. Documented upgrade paths to real data. |
| Parquet file path changes in data providers | **LOW** — SQL Explorer tables become unavailable | Medium | `_validate_table_paths()` at page load detects missing files and logs warnings. Datasets with no valid tables excluded from UI. Graceful degradation, not crash. |
| Sandbox verification false positive (network blocked by coincidence) | **LOW** — misleading advisory warning in logs | Low | `SQL_EXPLORER_SANDBOX_SKIP=true` for dev/local. Probe is advisory (logs warning + Prometheus metric), does not disable SQL Explorer. Filesystem probe validates explicitly forbidden paths (configurable via `SQL_EXPLORER_FORBIDDEN_WRITE_PATHS`), NOT generic temp dirs like `/var/tmp`. Deployment attestation (runbook + manifest controls) is the primary enforcement. |
| Trading safety regression from shared dependencies | **LOW** — unintended side effects on order routing | Very Low | T14 feature components (T14.1–T14.4) are purely data services UI — no changes to order routing, circuit breaker, client_order_id, or risk-limit modules. Verified by `git diff` scope check in Definition of Done. **CI enforcement:** PR check asserts no files modified under trading-critical paths (see canonical list below). |

---

## Patterns & Conventions

- **Service layer separation:** Security-critical logic in `libs/web_console_services/` services, NiceGUI pages are thin UI orchestrators
- **Permission checks:** Explicit `has_permission()` calls inside method bodies (not `@require_permission` decorator) for bound instance methods
- **Mock data pattern:** T14.2 and T14.4 return mock data consistent with P6T13 pattern, with documented upgrade paths
- **Timer cleanup:** `ui.timer` + `ClientLifecycleManager.register_cleanup_callback()` with unique `owner_key` per page
- **Overlap guards:** Per-client `_refreshing` flag prevents concurrent timer-triggered refreshes
- **AG Grid styling:** `apply_compact_grid_options()` for consistent table appearance across all pages
- **Charts:** `go.Figure()` + `ui.plotly(fig).classes("w-full")` for Plotly charts (T14.4)
- **Render functions:** `def render_X(...) -> None` with `__all__` exports for reusable components
- **Error handling:** `try/except` with `ui.notify()` for user-facing errors, `logger.exception()` for structured logging
- **Structured logging (T14-specific schema — documented exception from global standards):** JSON logs with domain-appropriate context fields. T14 does not produce orders, so `strategy_id`, `client_order_id`, and `symbol` are NOT included. This exception is documented in ADR-0035 (see Documentation Requirements) and MUST be cross-referenced in the central logging standards (`docs/STANDARDS/CODING_STANDARDS.md` logging section) to prevent inconsistent audit queries. The cross-reference should note: "UI-only services (T14 Data Services) use domain-specific context fields instead of trading fields. See ADR-0035 for rationale." Instead, T14 uses:
  - **All services:** `user_id`, `route`, `status`, `error_message`
  - **T14.1 (SQL Explorer):** `query_id` (UUID), `dataset`, `query_fingerprint`, `execution_ms`, `row_count`
  - **T14.2 (Data Source Status):** `source_name`, `provider_type`, `refresh_status`
  - **T14.3 (Feature Browser):** `feature_name`, `category`, `symbols_count`
  - **T14.4 (Shadow Results):** `strategy_name`, `metric_name`, `window_days`
- **Dataclass vs Pydantic:** DTOs use Pydantic `BaseModel` for serialization; internal metadata uses `@dataclass(frozen=True)` for immutability
- **Async I/O:** `asyncio.to_thread()` for blocking operations (DuckDB queries, Qlib feature loading, filesystem scans)
- **Session caching:** `app.storage.client` for per-session data persistence across page navigations
- **Defense-in-depth:** Multiple independent security layers (validator + blocklist + extension lockdown + path validation + sandbox)
- **Rate limiting:** Dedicated `RateLimiter(redis_client=..., fallback_mode="deny")` instance (not `get_rate_limiter()` singleton). `redis_client` MUST be `redis.asyncio.Redis` from `get_redis_store().get_master()`, NOT the sync client from `get_sync_redis_client()`. Service `__init__` asserts `fallback_mode == "deny"` at startup. Fail-closed on Redis outage
- **Redis client type consistency:** Both T14.1 `RateLimiter` and T14.2 refresh lock use **async** Redis (`redis.asyncio.Redis` via `get_redis_store()`). All new NG console services use async Redis exclusively — `get_sync_redis_client()` is reserved for legacy sync services only (per `apps/web_console_ng/core/dependencies.py` guidelines)
- **Audit logging:** Fingerprinted queries (literals replaced with `?`), raw SQL gated behind opt-in env var
- **UTC timestamps:** All `AwareDatetime` fields use UTC timezone-aware datetimes
- **Known duplication:** Dataset/source name constants appear in multiple places (`sql_validator.py` DATASET_TABLES, `data_source_status_service.py` source registry, `_resolve_table_paths()` mapping). This is accepted for v1 because each has distinct purpose and lifecycle. A shared dataset registry module is a follow-up consolidation target if drift becomes an issue

---

## Documentation Requirements

### Must Create
- [ ] `docs/ADRs/ADR-0035-sql-explorer-security.md` — Validator-based containment without DuckDB runtime lockdown, lazy view constraint, logical-to-physical table mapping strategy (Option b), T14-specific structured logging schema exemption (no `strategy_id`/`client_order_id`/`symbol`), conditions for process sandboxing (T14.1, MANDATORY before implementation)

### Must Update
- [ ] `docs/GETTING_STARTED/PROJECT_STATUS.md` — Mark T14 tasks as complete when done
- [ ] `docs/GETTING_STARTED/REPO_MAP.md` — Add new file paths if directory structure changes
- [ ] `docs/RUNBOOKS/ops.md` — Add SQL Explorer deployment attestation checklist (sandbox controls, network isolation, filesystem restrictions)
- [ ] `docs/STANDARDS/CODING_STANDARDS.md` **(PRE-MERGE GATE — must be included in T14 PR)** — Add cross-reference in logging section: "UI-only services (T14 Data Services) use domain-specific context fields (`query_id`, `source_name`, etc.) instead of trading fields (`strategy_id`, `client_order_id`, `symbol`). See ADR-0035 for rationale." This is a pre-merge gate because T14 services intentionally diverge from the standard logging schema; without the cross-reference, future reviewers would flag the divergence as a bug. Verification: `rg "UI-only services" docs/STANDARDS/CODING_STANDARDS.md` returns 1 match.

### No Documentation Needed
- No new concept docs required (shadow validation, SQL execution, feature metadata are implementation-level, not trading concepts)
- No API spec changes (all pages are NiceGUI server-rendered, no new REST endpoints)
- No database migration docs (no schema changes — mock data pattern)

---

## Estimation Breakdown

| Component | Effort | Notes |
|-----------|--------|-------|
| T14.2: Data Source Status | 1.5-2 days | Service (mock) + page + tests |
| T14.3: Feature Store Browser | 2-3 days | Feature catalog (158 features) + statistics + page + parity CI workflow + tests |
| T14.1: SQL Explorer | 3-4 days | Most complex: security layers, DuckDB connections, audit, ADR-0035 |
| T14.4: Shadow Mode Results | 1.5-2 days | Service (mock) + page + trend chart + tests |
| **Total** | **8.5-11 days** | |

---

## Testing Strategy

### Test Determinism Rules (MANDATORY)

Environment-sensitive tests MUST use deterministic harnesses to prevent flaky CI:

| Dependency | Unit Test Harness | Integration Test Harness | Skip/Fail Policy |
|------------|-------------------|--------------------------|-------------------|
| **Redis** | `fakeredis` or mock; no real Redis | Real Redis (CI has Redis service) | Unit: never skip; Integration: skip with `@pytest.mark.skipif` if Redis unavailable |
| **DuckDB** | In-memory `duckdb.connect()` with test data | Same as unit (DuckDB is embedded) | Never skip; DuckDB is a pip dependency |
| **Qlib** | Mock/fixture data; no Qlib initialization | `pytest.importorskip("qlib")` + data dir check | Unit: never skip (use fixtures); Integration: skip if Qlib not provisioned, but non-skip semantics in CI parity job |
| **Network/Sandbox probes** | Mock `socket.create_connection` and `Path.write_text` | Same as unit | Never skip; probes are tested via mocks |
| **Filesystem** | `tmp_path` fixture | `tmp_path` fixture | Never skip |

### Unit Tests
- SQL validation rules and edge cases (T14.1)
- Sensitive table blocking defense-in-depth (T14.1)
- Row limit and timeout enforcement (T14.1)
- Query audit log structure (T14.1)
- Feature metadata completeness (exactly 158 features, 35 categories, validated against static spec — runtime parity is integration-only) (T14.3)
- Feature statistics computation and edge cases (T14.3)
- Data source status service mock data (T14.2)
- Shadow results service mock data and pass rate calculation (T14.4)
- Permission enforcement for all four pages
- Timer cleanup registration with owner keys (T14.2, T14.4)

### Integration Tests
- SQL execution flow: validate → limit → execute → display (T14.1)
- Feature catalog runtime parity: `validate_catalog_against_runtime()` with actual `get_alpha158_features()` output (T14.3)
- Feature statistics from actual Alpha158 data if available (T14.3)
- Page routing and authentication for all four routes (all tasks)

### E2E Tests
- SQL Explorer workflow: select dataset → type query → execute → view results → export CSV (T14.1)
- Data source status: view sources → check status colors → manual refresh (T14.2)
- Feature store browsing: list features → filter by category → select → view details + stats (T14.3)
- Shadow results: view summary cards → view trend chart → view results table → click detail (T14.4)

---

## Definition of Done

**Feature tranche:**
- [ ] T14.2: Data source status page functional at `/data/sources`
- [ ] T14.3: Feature store browser page functional at `/data/features`
- [ ] T14.1: SQL Explorer page functional at `/data/sql-explorer` with RBAC, validation, timeout, audit logging
- [ ] T14.4: Shadow mode results page functional at `/data/shadow`
- [ ] All 4 pages registered in `apps/web_console_ng/pages/__init__.py`
- [ ] T14.2 and T14.4 services return mock data (consistent with P6T13 pattern), with documented upgrade paths
- [ ] T14.2 and T14.4 pages display a visible "Preview Data" badge/label to indicate mock data is in use, preventing user confusion during the transition to live integration
- [ ] T14.1 and T14.3 use real data-backed behavior (SQL execution against Parquet, feature computation from Alpha158) with graceful empty-state when data is absent
- [ ] Auto-refresh timers use overlap guards and `ClientLifecycleManager` cleanup with owner keys
- [ ] Structured logging for all error paths and audit trails
- [ ] Permission checks enforced on every page and every sensitive action
- [ ] Unit tests pass `make ci-local` (BLOCKING — enforces global 50% coverage floor and all lint/type checks)
- [ ] Unit test coverage quality target (NON-BLOCKING, tracked): 85% per-file coverage for new T14 files. Measured via: `pytest --cov=libs/web_console_services/sql_explorer_service.py --cov=libs/web_console_services/data_source_status_service.py --cov=libs/web_console_services/shadow_results_service.py --cov=libs/data/feature_metadata.py --cov=apps/web_console_ng/pages/sql_explorer.py --cov=apps/web_console_ng/pages/data_source_status.py --cov=apps/web_console_ng/pages/feature_browser.py --cov=apps/web_console_ng/pages/shadow_results.py --cov-report=term-missing`. This is a quality target reviewed during code review, NOT a CI gate. If coverage falls below 85% for a specific file, the reviewer should request additional tests but may approve with documented justification (e.g., NiceGUI page files with untestable UI wiring). Per-file scoping prevents dilution from unrelated files in the same directories.
- [ ] E2E tests pass
- [ ] Trading safety regression: no changes to order routing, circuit breaker, client_order_id, or risk-limit modules. **Concrete CI enforcement:** Add a trading-critical path guard job in `.github/workflows/ci-tests-parallel.yml` that runs on **all PRs** (not branch-name-scoped, which is bypassable via naming). **Canonical trading-critical path list** (single source of truth — regex and CODEOWNERS MUST use this exact list): `apps/execution_gateway/`, `apps/signal_service/`, `libs/risk/`, `libs/risk_management/`, `libs/trading/`. **Primary authorization:** These paths MUST be listed in `CODEOWNERS` with designated reviewers (e.g., `@trading-safety-team`). Branch protection rules require CODEOWNERS approval for these paths. **Supplemental label gate:** The CI job additionally checks for a `trading-paths-approved` GitHub label on the PR — this is a supplemental acknowledgment that the CODEOWNERS review specifically covered trading safety, not just a general approval. Without the label, the CI job fails as a reminder to explicitly approve trading-critical changes. For push events (direct push to `master`), the job always runs as an advisory warning. Branch protection rules on `master` (require PR, require reviews) are the durable control. Pseudocode:
  ```yaml
  - name: Determine base SHA
    id: base
    run: |
      set -euo pipefail
      SKIP_CHECK="false"
      if [ "${{ github.event_name }}" = "pull_request" ]; then
        BASE_SHA="${{ github.event.pull_request.base.sha }}"
      elif [ "${{ github.event_name }}" = "push" ]; then
        BASE_SHA="${{ github.event.before }}"
      else
        echo "FAIL: Unsupported event type: ${{ github.event_name }}" && exit 1
      fi
      # Validate base SHA availability
      if [ -z "$BASE_SHA" ] || [ "$BASE_SHA" = "0000000000000000000000000000000000000000" ]; then
        if [ "${{ github.event_name }}" = "pull_request" ]; then
          # PR mode: hard fail — cannot skip enforcement
          echo "FAIL: Base SHA unavailable or zero. Cannot determine changed files." && exit 1
        else
          # Push mode: advisory only — skip check gracefully
          echo "ADVISORY: Base SHA unavailable or zero (new branch?). Skipping push advisory check."
          SKIP_CHECK="true"
        fi
      fi
      echo "sha=$BASE_SHA" >> "$GITHUB_OUTPUT"
      echo "skip=$SKIP_CHECK" >> "$GITHUB_OUTPUT"
  - name: Check trading-critical paths
    if: github.event_name == 'pull_request'
    run: |
      set -euo pipefail
      changed_files="$(git diff --name-only "${{ steps.base.outputs.sha }}...${{ github.sha }}")" || {
        echo "FAIL: git diff command failed" && exit 1
      }
      if echo "$changed_files" | grep -qE '^(apps/execution_gateway/|apps/signal_service/|libs/risk/|libs/risk_management/|libs/trading/)'; then
        echo "WARNING: PR modifies trading-critical paths:"
        echo "$changed_files" | grep -E '^(apps/execution_gateway/|apps/signal_service/|libs/risk/|libs/risk_management/|libs/trading/)'
        if [ "${{ contains(github.event.pull_request.labels.*.name, 'trading-paths-approved') }}" != "true" ]; then
          echo "FAIL: Add 'trading-paths-approved' label to acknowledge trading-critical changes"
          exit 1
        fi
        echo "PASS: trading-paths-approved label present"
      else
        echo "PASS: No trading-critical paths modified"
      fi
  - name: Check trading-critical paths (push advisory)
    if: github.event_name == 'push' && steps.base.outputs.skip != 'true'
    run: |
      set -euo pipefail
      changed_files="$(git diff --name-only "${{ steps.base.outputs.sha }}...${{ github.sha }}")" || {
        echo "ADVISORY: git diff command failed (non-blocking)" && exit 0
      }
      if echo "$changed_files" | grep -qE '^(apps/execution_gateway/|apps/signal_service/|libs/risk/|libs/risk_management/|libs/trading/)'; then
        echo "ADVISORY: push event modifies trading-critical paths (review recommended):"
        echo "$changed_files" | grep -E '^(apps/execution_gateway/|apps/signal_service/|libs/risk/|libs/risk_management/|libs/trading/)'
      else
        echo "PASS: No trading-critical paths modified"
      fi
  ```
  Ensure the job has `fetch-depth: 0`. The guard runs on both `pull_request` and `push` events with **different enforcement levels:** `pull_request` is the **hard gate** (required status check, blocks merge on failure); `push` is **advisory-only** (logs warnings but does not block deployment). The PR hard gate is the primary enforcement mechanism — branch protection rules on `master` requiring PR-based merges ensure that all production code passes through the hard gate. The push advisory exists as a defense-in-depth signal for hotfix paths that bypass PRs (e.g., direct push by admins with branch protection override), but it cannot prevent bypass on its own. **Base SHA handling:** PR events fail-fast if base SHA is unavailable (hard error). Push events skip gracefully with advisory log if base SHA is unavailable (new branch). This dual policy is reflected in the pseudocode above.
- [ ] Navigation: `layout.py` loop refactored to honor `required_permission` tuple field generically with both single-permission (AND) and tuple-of-permissions (any-of/OR) semantics (replacing hardcoded `/admin` check); `/admin` entry uses `(Permission.MANAGE_API_KEYS, Permission.MANAGE_SYSTEM_CONFIG, Permission.VIEW_AUDIT)` tuple for OR-based authorization; regression tests in **UI test module** (`tests/apps/web_console_ng/ui/test_layout.py` or `tests/apps/web_console_ng/test_navigation.py`) verify these 4 specific admin nav visibility cases: (a) user with ONLY `MANAGE_API_KEYS` CAN see Admin, (b) user with ONLY `VIEW_AUDIT` CAN see Admin, (c) user with ONLY `MANAGE_SYSTEM_CONFIG` CAN see Admin, (d) user with NONE of these 3 permissions CANNOT see Admin. These 4 cases cover the OR-semantics boundary conditions. Nav visibility tests belong in UI test module, not permission-unit tests; all 4 data pages added to sidebar with permission-gated visibility
- [ ] Documentation: `docs/GETTING_STARTED/PROJECT_STATUS.md` updated with T14 completion status
- [ ] Documentation: `docs/GETTING_STARTED/REPO_MAP.md` updated with new file paths
- [ ] Documentation: `docs/RUNBOOKS/ops.md` updated with SQL Explorer deployment attestation checklist
- [ ] Documentation (PRE-MERGE GATE): `docs/STANDARDS/CODING_STANDARDS.md` updated with T14 logging schema exception cross-reference (verification: `rg "UI-only services" docs/STANDARDS/CODING_STANDARDS.md` returns 1 match)
- [ ] CI enforcement: Feature catalog parity test (`test_feature_catalog_parity.py`) integrated into existing `.github/workflows/ci-tests-parallel.yml` with scheduled weekly cron trigger and non-skip semantics
- [ ] CI enforcement: PR manifest validation job validates ALL production-relevant manifests listed in `config/deploy_manifests.yml` (canonical list — single source of truth consumed by both CI and `validate_deployment_manifest.py`), not just default `docker-compose.yml`
- [ ] Security: `SQL_EXPLORER_STRICT_EXTENSIONS=false` rejected at startup when `APP_ENV=production` (production guardrail test included)
- [ ] Security: `SQL_EXPLORER_DEPLOY_ATTESTED=true` required at startup when `APP_ENV=production` (fail-closed attestation; deploy pipeline sets this after successful manifest validation)
- [ ] Security: `SQL_EXPLORER_AUDIT_RAW_SQL=true` rejected at startup when `APP_ENV=production` unless `SQL_EXPLORER_AUDIT_RAW_SQL_EMERGENCY_OVERRIDE=true` is also set (production guardrail test included)
- [ ] Code reviewed and approved
- [ ] **Implementation-stage zen review (MANDATORY):** After code and tests are staged, run a fresh shared-context zen-mcp review (Gemini + Codex, `role='codereviewer'`) against the implementation diff. This planning-stage review validates design only; trading safety, concurrency, and security checks MUST be re-verified against actual code paths. Do not merge without implementation-stage approval.

---

**Last Updated:** 2026-02-14
**Status:** TASK
