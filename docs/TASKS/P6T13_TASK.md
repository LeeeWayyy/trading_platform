---
id: P6T13
title: "Professional Trading Terminal - Data Infrastructure"
phase: P6
task: T13
priority: P1
owner: "@development-team"
state: PLANNING
created: 2026-01-13
dependencies: [P5, P6T12]
related_adrs: [ADR-0031-nicegui-migration, ADR-PENDING-keyed-lifecycle-callbacks]
related_docs: [P6_PLANNING.md]
features: [T13.1-T13.4]
---

# P6T13: Data Infrastructure

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** PLANNING
**Priority:** P1 (Research Infrastructure)
**Owner:** @development-team
**Created:** 2026-01-13
**Track:** Track 13 of 18
**Dependency:** P5 complete, P6T12 (HealthMonitor, DuckDB patterns)

---

## Objective

Build data infrastructure UI: Point-in-Time inspection, coverage visualization, data services integration, and quality monitoring.

**Success looks like:**
- Point-in-Time data inspector for backtest validation
- Data coverage heatmap showing gaps
- Real data services wired (not demo mode)
- Data quality dashboard with trend charts

**Out of Scope:**
- No order submission, execution, or state-transition logic is modified in P6T13
- Trading-execution controls (circuit breaker checks, client_order_id generation, position limits, order state machines) are unaffected — this task is purely data infrastructure UI
- Service backend implementation (services currently return mock data; upgrading them to real DB queries is a separate task)

---

## Implementation Order

```
T13.3 Wire Data Services   (no dependency, modifies existing page, highest reuse)
T13.4 Quality Dashboard    (depends on T13.3, extends T13.3 quality tab)
T13.1 PIT Inspector        (depends on T13.4 for validation.py helper, new page + backend module)
T13.2 Coverage Heatmap     (depends on T13.1 filesystem patterns, extends inspector page)
```

T13.3 is implemented first as it replaces demo data with real service calls across the entire data management page, and T13.4 extends the quality tab wired in T13.3. T13.1 depends on T13.4 because it imports `is_valid_date_partition` from `validation.py` (created in T13.4). T13.2 depends on T13.1's filesystem patterns. Both T13.1 and T13.2 are new pages that build on shared data access patterns (T13.1 uses DuckDB for SQL queries, T13.2 uses Polars for lightweight date extraction).

---

## Tasks (4 total)

### T13.3: Wire Data Management Services - HIGH PRIORITY

**Goal:** Replace all mock/placeholder data in `data_management.py` with real service calls via `DataSyncService`, `DataExplorerService`, and `DataQualityService`.

**Current State Analysis:**
The existing `apps/web_console_ng/pages/data_management.py` (529 lines) has:
- Three main tabs: Data Sync, Data Explorer, Data Quality
- Permission checks are production-ready (`requires_auth`, `has_permission`)
- All data is **hardcoded mock** (e.g., `statuses = [{...}, ...]`)
- A "Demo Mode" amber banner at the top
- Services exist with full RBAC, rate limiting, and API contracts but return mock data internally
- DB tables exist (migrations 0012-0017) but services don't query them yet

**What this task does:**
1. Remove the demo mode banner
2. Replace every hardcoded mock data list with service method calls
3. Add auto-refresh timers for sync status and quality alerts
4. Add error handling for service unavailability
5. Wire manual sync trigger to `DataSyncService.trigger_sync()`
6. Wire query editor to `DataExplorerService.execute_query()`
7. Wire export button to `DataExplorerService.export_data()`
8. Wire alert acknowledgment to `DataQualityService.acknowledge_alert()`

**Approach:**

#### Service Instantiation
Instantiate services at page-load time (inside the `@ui.page` function), not at module level:
```python
from libs.web_console_services.data_sync_service import DataSyncService
from libs.web_console_services.data_explorer_service import DataExplorerService
from libs.web_console_services.data_quality_service import DataQualityService

sync_service = DataSyncService()
explorer_service = DataExplorerService()
quality_service = DataQualityService()
```

Services use the default `get_rate_limiter()` singleton internally. No DB pool or Redis client needed at instantiation time (services manage their own connections internally). **Note:** The services currently return mock data internally (see their `NOTE:` docstrings). T13.3 wires the page to services, establishing the correct call chain. When services are upgraded to use real DB queries (separate task), the page automatically benefits.

#### Data Sync Tab Wiring

**Tab-level visibility (per-capability gating):** The Data Sync tab must be visible if the user has ANY of `VIEW_DATA_SYNC`, `TRIGGER_DATA_SYNC`, or `MANAGE_SYNC_SCHEDULE`. Within the tab, each section is gated by its own required permission:
  - **Status display:** requires `VIEW_DATA_SYNC`
  - **Manual trigger:** requires `TRIGGER_DATA_SYNC`
  - **Schedule viewing:** requires `VIEW_DATA_SYNC` (since `get_sync_schedule()` requires `VIEW_DATA_SYNC` at the service level)
  - **Schedule editing:** requires BOTH `VIEW_DATA_SYNC` (to load schedules) AND `MANAGE_SYNC_SCHEDULE` (to edit them)
This matches the per-capability approach used for the Explorer tab (see below) and ensures users with only `TRIGGER_DATA_SYNC` can still see and use the manual sync trigger.

**Sync Status** (`_render_sync_status`):
- **Permission:** Requires `VIEW_DATA_SYNC`. If user lacks this permission, show "Sync status requires data-sync view permission" placeholder instead of the status table.
- Replace hardcoded `statuses` list with: `statuses = await sync_service.get_sync_status(user)`
- Map `SyncStatusDTO` fields to table columns: `dataset`, `last_sync` (format as relative time e.g. "5m ago"), `row_count`, `validation_status`
- Add `ui.timer(30.0, refresh_sync_status)` for auto-refresh (30s interval)
- **Overlap guard:** Use a boolean flag per refresh callback to prevent concurrent refresh calls from stacking under latency. **Scope:** The flag is defined inside the `@ui.page` function body, making it **per-client** in NiceGUI (each page load creates a new function scope). It is NOT a module-level global, so one client's refresh cannot block another client's:
  ```python
  _sync_refreshing = False
  async def refresh_sync_status() -> None:
      nonlocal _sync_refreshing
      if _sync_refreshing:
          return  # Skip overlapping tick
      _sync_refreshing = True
      try:
          statuses = await sync_service.get_sync_status(user)
          # ... update UI
      finally:
          _sync_refreshing = False
  ```
  Apply the same pattern to all timer callbacks (alerts, quality scores).
- Timer cleanup via `ClientLifecycleManager`

**Manual Sync Trigger** (`trigger_sync` callback):
- Replace `ui.notify(...)` placeholder with:
  ```python
  try:
      job = await sync_service.trigger_sync(user, dataset_select.value, reason_input.value)
      ui.notify(f"Sync job {job.id} queued for {job.dataset}", type="positive")
  except SyncRateLimitExceeded:
      ui.notify("Rate limit: 1 sync per minute", type="warning")
  except PermissionError as e:
      ui.notify(str(e), type="negative")
  ```
- Populate dataset dropdown from `sync_service.get_sync_status(user)` (use dataset names from status response, not hardcoded list). **Permission note:** `get_sync_status()` requires `VIEW_DATA_SYNC`. If a user has `TRIGGER_DATA_SYNC` but not `VIEW_DATA_SYNC`, fall back to displaying a manual text input for dataset name instead of a dropdown. This is reachable because the Data Sync tab is visible if the user has ANY sync-related permission (see tab-level visibility above), so a user with only `TRIGGER_DATA_SYNC` sees the tab and the manual trigger section, but the status table and dropdown are replaced by a text input.

**Sync Logs** (`_render_sync_logs`):
- Replace hardcoded `logs` with: `logs = await sync_service.get_sync_logs(user, dataset=dataset_filter, level=level_filter)`
- Add dataset and level filter dropdowns above the table
- Map `SyncLogEntry` fields: `created_at` (ensure UTC before relative time formatting), `dataset`, `level`, `message`
- Color-code level column: INFO=gray, WARN=amber, ERROR=red

**Sync Schedule** (`_render_sync_schedule`):
- **Permission:** Requires `VIEW_DATA_SYNC` to load the schedule list (since `get_sync_schedule()` requires `VIEW_DATA_SYNC` at the service level). If user has `MANAGE_SYNC_SCHEDULE` but lacks `VIEW_DATA_SYNC`, show "Schedule viewing requires data-sync view permission" placeholder. Users need BOTH `VIEW_DATA_SYNC` (to read schedules) and `MANAGE_SYNC_SCHEDULE` (to edit them) for full schedule management.
- Replace hardcoded `schedules` with: `schedules = await sync_service.get_sync_schedule(user)`
- Map `SyncScheduleDTO` fields: `dataset`, `cron_expression`, `enabled`, `last_scheduled_run`, `next_scheduled_run`
- If user has `MANAGE_SYNC_SCHEDULE` permission, add inline edit capability:
  - Toggle enabled/disabled via `ui.switch`
  - Edit cron expression via `ui.input` with save button
  - Call `sync_service.update_sync_schedule(user, dataset, SyncScheduleUpdateDTO(...))` on save
  - **Optimistic lock (conditional):** Current `DataSyncService` always returns `version=1` and does not enforce version conflicts. Add a generic `except` handler for potential version mismatch errors, but mark this as a future capability. Do not implement complex retry UX until the backend service supports real optimistic locking. For now, a simple save-and-notify is sufficient.

#### Data Explorer Tab Wiring

**Dataset Browser** (`_render_data_explorer_section`):
- **Permission note (per-capability gating):** Gate each explorer capability independently by its own permission, NOT with a combined gate:
  - **Dataset browsing:** `VIEW_DATA_SYNC` — controls dataset list visibility. Users without this see "Dataset listing requires data-sync view permission."
  - **Query execution:** `QUERY_DATA` — controls the query editor and Run button. Users without this see the query editor disabled with "Query permission required."
  - **Export:** `EXPORT_DATA` — controls the export button (see Export section below).
  - **Tab visibility:** Show the explorer tab if the user has ANY of `VIEW_DATA_SYNC` or `QUERY_DATA`. `EXPORT_DATA` alone is NOT sufficient to see the tab — export operates on query results, so without `QUERY_DATA` there is nothing to export, and without `VIEW_DATA_SYNC` there is no dataset list to browse. Users with only `EXPORT_DATA` see the tab hidden entirely (consistent with "no actionable capability").
- Replace hardcoded `datasets` list with: `datasets = await explorer_service.list_datasets(user)`
- Populate select dropdown from `DatasetInfoDTO.name` values
- Show dataset metadata below dropdown: description, row_count, date_range, symbol_count (from `DatasetInfoDTO` fields — these are returned by `list_datasets` which only requires `VIEW_DATA_SYNC`)
- On dataset selection change, load schema preview **only if user has `QUERY_DATA`**: `preview = await explorer_service.get_dataset_preview(user, selected_dataset, limit=5)` and display `preview.columns` as the schema. **Important:** `get_dataset_preview` requires `QUERY_DATA` permission at the service level. If user lacks `QUERY_DATA`, show only the metadata from `DatasetInfoDTO` (column names are not available without query rights). Do NOT call `get_dataset_preview` without the permission check — it will throw `PermissionError`.

**Query Editor** (`run_query` callback):
- Replace placeholder with:
  ```python
  try:
      result = await explorer_service.execute_query(user, selected_dataset, query_textarea.value)
      # Update results table with result.columns, result.rows
  except ValueError as e:
      ui.notify(f"Query error: {e}", type="negative")
  except ExplorerRateLimitExceeded:
      ui.notify(f"Rate limit: {MAX_QUERIES_PER_MINUTE} queries/minute", type="warning")
  ```
- Dynamically build table columns from `QueryResultDTO.columns`
- Show `result.total_count` and `result.has_more` indicator
- Add loading spinner during query execution

**Export** (`export_data` callback):
- **Permission:** Export requires `EXPORT_DATA` permission (distinct from `QUERY_DATA`). Gate the export button visibility on `has_permission(user, Permission.EXPORT_DATA)`. If user lacks it, hide the export button or show it disabled with tooltip "Export permission required."
- Replace placeholder with actual `explorer_service.export_data()` call
- Add format selector (CSV/Parquet radio buttons)
- Show export job status: queued -> processing -> ready
- Handle rate limit (5 exports/hour)

#### Data Quality Tab Wiring

**Validation Results** (`_render_validation_results`):
- Replace hardcoded `results` with: `results = await quality_service.get_validation_results(user, dataset=None)`
- Add dataset filter dropdown
- Map `ValidationResultDTO` fields to table: `dataset`, `validation_type`, `status`, `expected_value`, `actual_value`, `created_at`
- **Validation status normalization (required):** The service emits raw `status` values (e.g., `"ok"`) that don't match the UI taxonomy. **T13.3 defines the mapping inline** (since T13.4's `quality_scorer.py` does not exist yet at this stage):
  ```python
  # Inline in data_management.py during T13.3 (will be replaced by import in T13.4)
  _STATUS_MAP = {"ok": "passed", "error": "failed", "fail": "failed", "warn": "warning"}
  normalized_status = _STATUS_MAP.get(result.status.lower(), result.status.lower())
  ```
  When T13.4 lands, this inline map is **replaced** by an import from the centralized scorer:
  ```python
  from libs.data.data_quality.quality_scorer import normalize_validation_status
  normalized_status = normalize_validation_status(result.status)
  ```
  This ensures T13.3 is independently deployable without importing T13.4 code.
- Color-code normalized status: passed=green, failed=red, warning=amber

**Anomaly Alerts** (`_render_anomaly_alerts`):
- Replace hardcoded `anomalies` with: `alerts = await quality_service.get_anomaly_alerts(user, severity=None, acknowledged=ack_mapped)`  (severity=None always; filter client-side)
- **Severity taxonomy (required strategy — client-side filtering):** The service's `AnomalyAlertDTO.severity` is a free-form `str` (currently emits `"warning"`). **Always fetch unfiltered** from the service (`severity=None`) and normalize + filter client-side:
  - **Raw → Canonical mapping** (for display and filtering): `{"critical": "critical", "high": "high", "warning": "medium", "medium": "medium", "low": "low", "info": "low"}`.
  - Apply this mapping to each alert's `severity` field immediately after fetching.
  - The severity filter dropdown shows canonical levels: all/critical/high/medium/low.
  - Filtering is applied in Python after normalization, never passed to the service.
  - This ensures no alerts are silently dropped due to raw/canonical mismatch.
- Add severity filter (all/critical/high/medium/low, applied client-side after normalization) and acknowledged filter (all/unacked/acked). **Acknowledged filter mapping** (UI string → service parameter `bool | None`): `"all" → None` (fetch both), `"unacked" → False` (unacknowledged only), `"acked" → True` (acknowledged only). Apply this mapping before calling the service:
  ```python
  _ACK_MAP: dict[str, bool | None] = {"all": None, "unacked": False, "acked": True}
  ack_mapped = _ACK_MAP[ack_filter_select.value]
  ```
- Map `AnomalyAlertDTO` fields to alert cards
- Add "Acknowledge" button per alert (only if user has `ACKNOWLEDGE_ALERTS` permission):
  ```python
  async def ack_alert(alert_id: str) -> None:
      reason = await ui.dialog(...)  # prompt for reason
      ack = await quality_service.acknowledge_alert(user, alert_id, reason)
      ui.notify(f"Alert acknowledged by {ack.acknowledged_by}", type="positive")
  ```
- Add `ui.timer(60.0, refresh_alerts)` for auto-refresh (60s interval)

**Quality Trends** (`_render_quality_trends`):
- Replace hardcoded metric cards with real data from `quality_service.get_quality_trends(user, dataset, days=30)`
- Add dataset selector for trend filtering
- Build Plotly line chart from `QualityTrendDTO.data_points`:
  ```python
  fig = go.Figure()
  for metric_name in unique_metrics:
      metric_points = [p for p in trend.data_points if p.metric == metric_name]
      fig.add_trace(go.Scatter(
          x=[p.date for p in metric_points],
          y=[p.value for p in metric_points],
          mode="lines+markers", name=metric_name,
      ))
  fig.update_layout(title=f"Quality Trends - {dataset}", xaxis_title="Date", yaxis_title="Score")
  ui.plotly(fig).classes("w-full")
  ```
- Keep summary metric cards (Current, 7-Day, 30-Day) computed from the trend data points

**Data Coverage** (`_render_data_coverage`):
- Replace hardcoded `coverage_data` with: `quarantine = await quality_service.get_quarantine_status(user)`
- Show quarantine entries table with dataset, reason, quarantine_path, created_at
- This tab is enhanced further in T13.4

#### Error Handling Pattern

**Rate-limit exception imports:** Only `DataSyncService` and `DataExplorerService` export `RateLimitExceeded`. `DataQualityService` does **not** have rate limiting. Import the two that exist:
```python
from libs.web_console_services.data_sync_service import RateLimitExceeded as SyncRateLimitExceeded
from libs.web_console_services.data_explorer_service import RateLimitExceeded as ExplorerRateLimitExceeded
# DataQualityService has no RateLimitExceeded — no rate-limit handling needed for quality calls
```

All service calls follow this pattern:
```python
# For sync/explorer services (have rate limits):
try:
    result = await service.method(user, ...)
except PermissionError as e:
    ui.notify(str(e), type="negative")
    return
except (SyncRateLimitExceeded, ExplorerRateLimitExceeded) as e:
    ui.notify(f"Rate limit exceeded: {e}", type="warning")
    return
except Exception:
    logger.exception("service_call_failed", extra={
        "method": "method_name",
        "service": "DataSyncService",  # or DataExplorerService
        "dataset": locals().get("dataset", "unknown"),
        "user_id": user.get("id") if isinstance(user, dict) else getattr(user, "id", None),
        "symbol": locals().get("symbol", None),  # Include when available
    })
    ui.notify("Service temporarily unavailable", type="warning")
    return

# For quality service (no rate limits):
try:
    result = await quality_service.method(user, ...)
except PermissionError as e:
    ui.notify(str(e), type="negative")
    return
except Exception:
    logger.exception("service_call_failed", extra={
        "method": "method_name",
        "service": "DataQualityService",
        "dataset": locals().get("dataset", "unknown"),
        "user_id": user.get("id") if isinstance(user, dict) else getattr(user, "id", None),
        "symbol": locals().get("symbol", None),  # Include when available
    })
    ui.notify("Service temporarily unavailable", type="warning")
    return
```
**Observability note:** The `logger.exception()` calls above produce structured JSON log entries (via `structlog`) that are consumed by the platform's monitoring pipeline. The `exception` level ensures they appear in error dashboards and can trigger alerts. If Prometheus metrics are instrumented on the page (future enhancement), also increment a `data_service_errors_total{service=..., method=...}` counter here. For T13.3, structured logging is sufficient — metric counters can be added when the service layer moves to real DB queries.

**Wiring test requirement:** Tests must verify that each service's `RateLimitExceeded` is caught correctly for sync and explorer calls, and that quality calls do not attempt to catch a non-existent rate-limit exception.

#### Timer Cleanup Pattern (Unified for T13.3 + T13.4)
Use the centralized `get_or_create_client_id()` helper from `apps/web_console_ng/utils/session.py` for session-stable client ID retrieval (same helper used by `layout.py`, `grid_performance.py`). **All timers from both T13.3 and T13.4 are registered in a single cleanup callback** — do not register separate callbacks per task.
```python
from apps.web_console_ng.utils.session import get_or_create_client_id

lifecycle = ClientLifecycleManager.get()
client_id = get_or_create_client_id()  # Centralized helper: checks storage, generates if missing, fallback to client.id

# T13.3 timers
timer_sync = ui.timer(30.0, refresh_sync_status)
timer_alerts = ui.timer(60.0, refresh_alerts)
# T13.4 timer — initialized as None during T13.3, assigned in T13.4
timer_scores = None  # Will be assigned ui.timer(60.0, refresh_quality_scores) in T13.4

# Single unified cleanup for ALL timers (staged-safe: checks None before cancel)
async def _cleanup_timers() -> None:
    timer_sync.cancel()
    timer_alerts.cancel()
    if timer_scores is not None:
        timer_scores.cancel()

# Keyed callback registration: use a page-scoped owner key to replace only THIS page's
# prior callback, without affecting callbacks registered by layout.py or other pages.
# NEVER clear client_callbacks[client_id] directly — that wipes callbacks from all modules.
_CLEANUP_OWNER_KEY = "data_management_timers"

if client_id:
    await lifecycle.register_cleanup_callback(
        client_id, _cleanup_timers, owner_key=_CLEANUP_OWNER_KEY
    )
```
**Important:** `timer_scores` is `None` until T13.4 is implemented. The cleanup function checks for `None` before calling `.cancel()`, so T13.3 can be deployed alone without `NameError`. When T13.4 is added, assign `timer_scores = ui.timer(60.0, refresh_quality_scores)` and the same cleanup covers it automatically. **Do NOT register separate cleanup callbacks for T13.3 and T13.4** — use one unified callback covering all timers. **Do NOT inline client_id generation** — use `get_or_create_client_id()` for consistency.

**Prerequisite: Keyed callback API for `ClientLifecycleManager`** (required before T13.3 implementation):
The current `register_cleanup_callback(client_id, callback)` appends without dedup. Directly clearing `client_callbacks[client_id]` is unsafe because other modules (layout.py, dashboard, manual_order) also register callbacks for the same `client_id`. **Add a keyed overload** to `ClientLifecycleManager`:
```python
async def register_cleanup_callback(
    self, client_id: str, callback: Callable[[], Any], *, owner_key: str | None = None
) -> None:
    """Register a cleanup callback. If owner_key is provided, replaces any prior callback
    with the same owner_key for this client (keyed dedup). Without owner_key, appends (legacy)."""
    async with self._lock:  # MUST be asyncio.Lock (non-reentrant) for atomicity
        callbacks = self.client_callbacks.setdefault(client_id, [])
        if owner_key is not None:
            # Atomic single-assignment: filter + append in one operation
            # Migration tolerance: handle both legacy bare Callable and new (Callable, key) tuple
            filtered = [
                item for item in callbacks
                if not isinstance(item, tuple) or item[1] != owner_key
            ]
            self.client_callbacks[client_id] = filtered + [(callback, owner_key)]
        else:
            self.client_callbacks[client_id].append((callback, owner_key))
```
This is a backward-compatible change: existing callers without `owner_key` still append as before. The `cleanup_client` method iterates the list and calls `cb()` for each `(cb, key)` tuple. Update the data type from `list[Callable]` to `list[tuple[Callable, str | None]]` and adjust `cleanup_client` accordingly. **Migration safety:** During the transition, any existing in-memory callbacks may still be bare `Callable` objects (from prior page loads before the code update). The `cleanup_client` method must handle both shapes during the migration window:
```python
async def cleanup_client(self, client_id: str) -> None:
    async with self._lock:
        callbacks = self.client_callbacks.pop(client_id, [])
    for item in callbacks:
        # Migration tolerance: handle both old (Callable) and new (tuple) shapes
        cb = item[0] if isinstance(item, tuple) else item
        try:
            result = cb()
            if hasattr(result, "__await__"):
                await result
        except Exception:
            logger.exception("cleanup_callback_failed", extra={"client_id": client_id})
```
Once all existing callers have been updated and no bare-callable entries remain in memory, the `isinstance` check can be removed in a follow-up cleanup. This must be added as a pre-step in T13.3.

**Files to modify:**
- `apps/web_console_ng/pages/data_management.py` - Replace all mock data with service calls

**Maintainability note:** The existing `data_management.py` is ~529 lines. T13.3 + T13.4 additions may push it past 800-1000 lines. If the file exceeds 800 lines after T13.4, extract tab-specific rendering into component modules (`apps/web_console_ng/components/data_management/sync_tab.py`, `quality_tab.py`, etc.) in a follow-up refactor. The page module would then orchestrate tabs and own shared state (services, user, timers).

**Acceptance Criteria:**
- [ ] Demo mode banner removed
- [ ] Sync status loaded from `DataSyncService.get_sync_status()`
- [ ] Manual sync trigger calls `DataSyncService.trigger_sync()` with rate limit handling
- [ ] Sync logs loaded from `DataSyncService.get_sync_logs()` with filters
- [ ] Sync schedule loaded from `DataSyncService.get_sync_schedule()` with inline edit
- [ ] Dataset list loaded from `DataExplorerService.list_datasets()`
- [ ] Query execution routed through `DataExplorerService.execute_query()` with rate limit handling
- [ ] Export routed through `DataExplorerService.export_data()` with format selector, gated on `EXPORT_DATA` permission
- [ ] Validation results from `DataQualityService.get_validation_results()`
- [ ] Anomaly alerts from `DataQualityService.get_anomaly_alerts()` with acknowledge button, severity values normalized via mapping
- [ ] Quality trends from `DataQualityService.get_quality_trends()` with Plotly chart
- [ ] Quarantine status from `DataQualityService.get_quarantine_status()`
- [ ] Auto-refresh timers with `ClientLifecycleManager` cleanup
- [ ] `PermissionError` handled for all services; `RateLimitExceeded` handled for sync and explorer only (quality has no rate limits)
- [ ] Explorer tab per-capability gating: `VIEW_DATA_SYNC` (datasets), `QUERY_DATA` (queries), `EXPORT_DATA` (export button) — tab visible if user has `VIEW_DATA_SYNC` or `QUERY_DATA` (EXPORT_DATA alone is insufficient since export operates on query results)
- [ ] Timer cleanup uses centralized `get_or_create_client_id()` helper (from `apps/web_console_ng/utils/session.py`)
- [ ] Timer cleanup staged-safe: `timer_scores` is `None` in T13.3 (no NameError when T13.4 not yet implemented)
- [ ] Timer callbacks use overlap guard (boolean flag or lock) to prevent concurrent stacking
- [ ] Validation status normalized client-side: `"ok"` → `"passed"`, `"error"` → `"failed"`
- [ ] Data Sync tab per-capability gating: visible if user has ANY of `VIEW_DATA_SYNC`, `TRIGGER_DATA_SYNC`, `MANAGE_SYNC_SCHEDULE`; manual trigger reachable without `VIEW_DATA_SYNC`

**Unit Tests:**
- Service instantiation and error handling wrappers
- Timer registration and cleanup
- Filter state management (dataset, severity, level selectors)
- Sync trigger with rate limit handling (`SyncRateLimitExceeded` from `data_sync_service`)
- Query execution with validation error display (`ExplorerRateLimitExceeded` from `data_explorer_service`)
- **`ClientLifecycleManager` keyed callback tests (prerequisite):**
  - Legacy registration (no `owner_key`): appends without dedup (backward compat)
  - Keyed registration: replaces prior callback with same `owner_key`
  - Mixed ownership: keyed replacement does NOT remove legacy or other-keyed callbacks
  - Cleanup: `cleanup_client()` runs ALL callbacks (legacy + keyed) and pops the list
  - Migration tolerance: `cleanup_client()` handles both bare `Callable` and `tuple[Callable, str | None]` in-memory shapes
  - Concurrency: multiple `register_cleanup_callback` calls under `_lock` are serialized
- Quality service calls do NOT catch `RateLimitExceeded` (none exists in that service)
- Timer cleanup registration uses `get_or_create_client_id()` centralized helper
- Timer cleanup staged-safe: `timer_scores is None` does not raise NameError when T13.3 deployed alone
- Timer overlap guard prevents concurrent refresh calls from stacking
- Validation status normalization: `"ok"` → `"passed"`, `"error"` → `"failed"`, unknown values pass through lowercased
- Severity normalization: fetch unfiltered, apply canonical mapping client-side, "warning" -> "medium"
- Severity filter dropdown applies after normalization (never sends canonical values to service)
- Export button visibility gated on `EXPORT_DATA` permission
- Data Sync tab visible with only `TRIGGER_DATA_SYNC` permission (manual text input fallback shown instead of dropdown)
- Unified timer cleanup covers all timers (T13.3 + T13.4) in single callback

**Test File:** `tests/apps/web_console_ng/pages/test_data_management_wiring.py`

---

### T13.4: Data Quality Dashboard - MEDIUM PRIORITY

**Goal:** Enhance the Data Quality tab (wired in T13.3) with computed quality scores, trend visualization, degradation alerts, and quarantine inspection.

**Current State Analysis:**
After T13.3, the quality tab displays raw `DataQualityService` output. T13.4 adds computed analytics on top:
- Quality scores aggregated per dataset (from validation results)
- Trend charts showing quality over time
- Threshold-based degradation alerts
- Quarantine data inspection (drill into quarantined rows)

**Approach:**

#### Backend: `libs/data/data_quality/quality_scorer.py`
Create a new scoring module in the existing `libs/data/data_quality/` package. This is a **pure computation module** (no I/O) that takes service DTOs as input and produces scores.

1. **`QualityScore` dataclass:**
   ```python
   @dataclass
   class QualityScore:
       dataset: str
       overall_score: float | None   # 0.0 - 100.0 (percentage), None if no validations
       validation_pass_rate: float | None  # % passed, None if no validations
       anomaly_count: int            # Number of unacknowledged anomalies
       quarantine_count: int         # Number of quarantine entries
       score_breakdown: dict[str, float]  # Per-metric scores
       computed_at: datetime  # UTC-aware (datetime.now(UTC))
   ```

2. **`compute_quality_scores()` function:**
   The scorer accepts **Protocol-based inputs** (not service DTOs directly) to avoid coupling core data-quality logic to web-console service types. Define protocols in `quality_scorer.py`:
   ```python
   from typing import Protocol, runtime_checkable

   @runtime_checkable
   class ValidationLike(Protocol):
       dataset: str
       status: str

   @runtime_checkable
   class AlertLike(Protocol):
       dataset: str
       severity: str
       acknowledged: bool

   @runtime_checkable
   class QuarantineLike(Protocol):
       dataset: str

   def compute_quality_scores(
       validations: list[ValidationLike],
       alerts: list[AlertLike],
       quarantine: list[QuarantineLike],
   ) -> list[QualityScore]:
   ```
   The service DTOs (`ValidationResultDTO`, `AnomalyAlertDTO`, `QuarantineEntryDTO`) already satisfy these protocols — no adapter needed at the call site. This decoupling allows reuse of the scorer from non-UI contexts (e.g., CLI health checks, batch monitoring).
   **Status normalization (built into scorer):** The scorer owns the canonical status mapping so it is self-correcting regardless of caller:
   ```python
   _STATUS_MAP = {"ok": "passed", "error": "failed", "fail": "failed", "warn": "warning"}

   def _normalize_status(raw: str) -> str:
       return _STATUS_MAP.get(raw.lower(), raw.lower())
   ```
   `compute_quality_scores()` applies `_normalize_status()` to each `ValidationResultDTO.status` internally before counting. Callers do not need to pre-normalize.

   **Scoring formula (per dataset):**
   - `validation_pass_rate = passed_count / total_validations * 100` (0-100). `passed_count` is determined by `_normalize_status(result.status) == "passed"`. Raw `"ok"` from the service counts as `"passed"` because the scorer normalizes internally.
   - `anomaly_penalty = min(unacknowledged_count * 5.0, 30.0)` (max 30 points deducted)
   - `quarantine_penalty = min(quarantine_count * 10.0, 20.0)` (max 20 points deducted)
   - `overall_score = max(0.0, validation_pass_rate - anomaly_penalty - quarantine_penalty)`
   - If `total_validations == 0` for a dataset, `overall_score = None` (displayed as "N/A - No validations")
   - Score breakdown: `{"validation": validation_pass_rate, "anomaly_penalty": anomaly_penalty, "quarantine_penalty": quarantine_penalty}`

3. **`compute_trend_summary()` function:**
   **Scope:** This function accepts a `TrendLike` object whose `data_points` may contain multiple metrics. The function filters to `metric_name` internally and computes the summary for that single metric. The UI calls this once per unique metric and displays each metric's trend separately.
   Uses **Protocol-based inputs** (consistent with `compute_quality_scores()`):
   ```python
   @runtime_checkable
   class TrendPointLike(Protocol):
       value: float
       date: datetime  # Matches QualityTrendPointDTO.date (AwareDatetime)
       metric: str     # Metric name for filtering (matches QualityTrendPointDTO.metric)

   @runtime_checkable
   class TrendLike(Protocol):
       dataset: str
       period_days: int
       data_points: list[Any]  # Each element must satisfy TrendPointLike

   def compute_trend_summary(
       trend: TrendLike,
       metric_name: str,  # Function filters data_points to this metric internally
   ) -> TrendSummary:
   ```
   ```python
   @dataclass
   class TrendSummary:
       current_score: float | None    # Latest data point value
       avg_7d: float | None           # Mean of last 7 data points
       avg_30d: float | None          # Mean of last 30 data points
       trend_direction: str           # "improving", "stable", "degrading"
       degradation_alert: bool        # True if avg_7d < avg_30d * 0.95 (5% degradation)
   ```
   **Trend direction formula:**
   - `improving`: `avg_7d > avg_30d * 1.02` (>2% improvement)
   - `degrading`: `avg_7d < avg_30d * 0.98` (>2% decline)
   - `stable`: otherwise
   - `degradation_alert`: `avg_7d < avg_30d * 0.95` (>5% decline triggers alert)
   - If fewer than 7 data points, `avg_7d = None` and `trend_direction = "insufficient_data"`
   - If fewer than 2 data points, all fields are `None` / `"insufficient_data"` / `False`

#### Frontend Enhancement: Data Quality Tab

Modify `_render_data_quality_section()` in `data_management.py` to add:

1. **Quality Score Cards (new section at top of quality tab):**
   - Call all three services, then `compute_quality_scores()`:
     ```python
     validations = await quality_service.get_validation_results(user, dataset=None)
     alerts = await quality_service.get_anomaly_alerts(user, severity=None, acknowledged=None)
     quarantine = await quality_service.get_quarantine_status(user)
     scores = compute_quality_scores(validations, alerts, quarantine)
     ```
   - Render score cards per dataset using `MetricCard` pattern:
     - Overall score with color: >= 90 green, 70-89 amber, < 70 red
     - Validation pass rate
     - Unacknowledged anomaly count
     - Quarantine entry count

2. **Enhanced Quality Trends (replace T13.3 basic trends):**
   - After loading trend data from `quality_service.get_quality_trends()`, call `compute_trend_summary()`
   - Render summary cards: Current Score, 7-Day Avg, 30-Day Avg with trend arrow icon
   - If `degradation_alert` is True, show amber warning card: "Quality degradation detected: 7-day average is significantly below 30-day average for {dataset}"
   - Plotly line chart of trend data points (same as T13.3 chart but with threshold line overlay at score 90 and 70)

3. **Quarantine Inspector (new sub-tab in quality section):**
   - Replace basic quarantine table with an interactive inspector
   - Show quarantine entries grouped by dataset
   - For each entry, show: dataset, quarantine_path, reason, created_at
   - **Drill-down:** When user clicks a quarantine entry, **always validate the path first**, then load **only the entry's dataset-scoped files** (never all files in the date directory). **Never call `register_table` with a raw unvalidated path or an unscoped glob:**
     ```python
     from libs.data.data_quality.validation import validate_quarantine_path
     # Step 1: Validate BEFORE any DuckDB access (returns resolved path to minimize TOCTOU)
     safe_path = validate_quarantine_path(quarantine_entry.quarantine_path, data_dir=data_dir)
     # Step 1b: TOCTOU re-validation at point of use (belt-and-suspenders)
     quarantine_root = data_dir / "quarantine"
     if not safe_path.resolve().is_relative_to(quarantine_root.resolve()):
         raise ValueError("Path validation failed at access time")
     # Step 2: Sanitize dataset name before using in path construction
     import re
     _DATASET_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")  # 64-char cap aligns with typical dataset naming
     if not _DATASET_PATTERN.match(quarantine_entry.dataset):
         raise ValueError(f"Invalid dataset name: {quarantine_entry.dataset!r}")
     # Step 3: Scope to entry's dataset — NEVER use unscoped "*.parquet" glob
     # ETL layout: {date}/{symbol}.parquet — try entry.dataset as symbol filename first
     entry_file = safe_path / f"{quarantine_entry.dataset}.parquet"
     with DuckDBCatalog() as catalog:
         if entry_file.exists():
             # Dataset maps to a single symbol file — load only that file
             catalog.register_table("quarantine", str(entry_file))
         else:
             # Dataset doesn't map 1:1 to a symbol file — load all but filter via SQL
             catalog.register_table("quarantine", str(safe_path / "*.parquet"))
         # Always filter by dataset context to prevent cross-dataset data leak
         if entry_file.exists():
             preview = catalog.query("SELECT * FROM quarantine LIMIT 100")
         else:
             # Schema introspection: verify 'symbol' column exists before filtering
             schema = catalog.query("SELECT * FROM quarantine LIMIT 0")
             if "symbol" in schema.columns:
                 preview = catalog.query(
                     "SELECT * FROM quarantine WHERE symbol = ? LIMIT 100",
                     params=[quarantine_entry.dataset],
                 )
             else:
                 # No 'symbol' column — cannot filter; return empty to prevent data leak
                 preview = None  # Render "No matching data for this entry" in UI
     ```
     **Cross-dataset isolation:** The canonical ETL layout stores all symbols' quarantine files in a shared date directory (`quarantine/{date}/{symbol}.parquet`). An unscoped `*.parquet` glob would expose every symbol's quarantined data for that date — including symbols belonging to other datasets that the user's quarantine entry does not reference. The scoping above ensures only the clicked entry's data is loaded. When the entry's `dataset` field doesn't correspond to a symbol filename AND the Parquet files lack a `symbol` column for SQL filtering, the drill-down returns an empty preview with a "No matching data" message rather than leaking cross-dataset rows. The schema introspection (`LIMIT 0` query) is a zero-row read that validates column existence before attempting the filter.
     **Known limitation (T13 scope):** The service mock's `dataset` field (e.g., `"crsp"`) does not always map 1:1 to ETL symbol filenames (e.g., `AAPL.parquet`). Until the quality service is upgraded to real DB queries (out of scope for T13), some drill-down attempts will return "No matching data" for entries where the dataset-to-symbol mapping is ambiguous. The UI must display a clear informational message: "Preview unavailable — dataset does not map to a specific data file. Full drill-down available when the quality service is DB-backed." This is acceptable for T13 since the primary value is the score/trend/alert UI, not deep drill-down.
     Display the preview in a data table showing the quarantined rows with their `reason` column highlighted
   - **Quarantine path contract:**
     - **Canonical on-disk layout (ETL source of truth):** The ETL pipeline (`libs/data/data_pipeline/etl.py:_save_results`) writes quarantine files as `data/quarantine/{date}/{symbol}.parquet` (e.g., `data/quarantine/2024-10-16/AAPL.parquet`). This is the canonical layout used by both the coverage scanner (T13.2) and the drill-down preview.
     - **Service mock path:** The `QuarantineEntryDTO.quarantine_path` currently returns `data/quarantine/{dataset}/{date}` (e.g., `data/quarantine/crsp/2024-10-16`), which differs from the ETL layout. The validator must normalize the service path to resolve against the canonical ETL layout.
     - **Normalization:** Since `quarantine_root = data_dir / "quarantine"`, directly joining would create a double-nested path. Strip the `data/quarantine/` prefix, then resolve to the canonical ETL date directory:
     ```python
     # Normalize: strip 'data/quarantine/' prefix
     raw = quarantine_path
     prefix = "data/quarantine/"
     if raw.startswith(prefix):
         raw = raw[len(prefix):]  # e.g., "crsp/2024-10-16" or "2024-10-16"

     # Resolve to canonical ETL layout: quarantine_root/{date}/{symbol}.parquet
     # Accepted input formats (strict allowlist):
     #   1. ETL format:          "2024-10-16"
     #   2. Service mock format: "data/quarantine/crsp/2024-10-16" (prefix stripped above)
     #   3. Already-stripped:    "crsp/2024-10-16"
     # Reject unknown shapes: paths with >3 segments, absolute paths, or no YYYY-MM-DD component
     parts = Path(raw).parts
     if len(parts) > 3:  # At most: prefix/dataset/date or just date
         raise ValueError(f"Unexpected quarantine path format (too many segments): {quarantine_path}")
     date_part = None
     for part in reversed(parts):
         if is_valid_date_partition(part):
             date_part = part
             break
     if date_part is None:
         raise ValueError(f"No valid date component in quarantine path: {quarantine_path}")
     candidate = quarantine_root / date_part  # Always resolve to canonical {date} directory
     ```
     The validated result is an absolute resolved **directory** path suitable for globbing with `*.parquet`. The validator enforces a **directory-only contract**: the result must be a directory (or not-yet-existing path under quarantine_root), never a file. If the resolved path is a file, raise `ValueError`. The consumer always does `safe_path / "*.parquet"`, so file inputs would produce invalid globs.
     Tests must cover: ETL format (`2024-10-16`), service mock format (`data/quarantine/crsp/2024-10-16`), and already-stripped (`crsp/2024-10-16`). All three must resolve to the same canonical directory `quarantine_root/2024-10-16/`.
     - **Production alignment (recommended):** The service mock's `quarantine_path` (`{dataset}/{date}`) does not match the ETL's canonical layout (`{date}/{symbol}.parquet`). The normalization extracts the date component to resolve deterministically. Long-term the service should be updated to emit paths matching the ETL layout. This is out of scope for T13 (service is mock-backed) but should be addressed when the service is DB-backed.
   - **Security:** Validate `quarantine_path` before registering with DuckDB to prevent path traversal and symlink escape. **This validation must be implemented in a dedicated module** `libs/data/data_quality/validation.py` (separate from the pure-computation scorer). The UI calls the helper, which encapsulates the three-phase check:
     ```python
     # In libs/data/data_quality/validation.py:
     # is_valid_date_partition is defined in THIS module (see Date validation helper above)

     def validate_quarantine_path(quarantine_path: str, data_dir: Path) -> Path:
         """Validate and return resolved quarantine directory path. Raises ValueError on violation.
         Returns a directory path (never a file) suitable for globbing with '*.parquet'."""
         quarantine_root = data_dir / "quarantine"  # Derived from configured data_dir, not hardcoded
         # FAIL-CLOSED: Reject absolute paths and traversal markers BEFORE any normalization.
         # Malicious inputs like "../../2024-10-16" are rejected outright rather than silently
         # normalized to a valid date directory — this ensures auditability of rejected attempts.
         raw = quarantine_path
         if Path(raw).is_absolute():
             raise ValueError(f"Absolute path rejected: {quarantine_path}")
         if ".." in Path(raw).parts:
             raise ValueError(f"Path traversal rejected: {quarantine_path}")
         # Normalize: strip known 'data/quarantine/' prefix, extract date component
         known_prefix = "data/quarantine/"
         if raw.startswith(known_prefix):
             raw = raw[len(known_prefix):]
         # Re-check after prefix stripping (defense in depth)
         if ".." in Path(raw).parts:
             raise ValueError(f"Path traversal after normalization: {quarantine_path}")
         # Extract canonical date directory from path (strict allowlist of accepted formats)
         parts = Path(raw).parts
         if len(parts) > 3:  # Reject unknown shapes with too many segments
             raise ValueError(f"Unexpected quarantine path format: {quarantine_path}")
         date_part = None
         for part in reversed(parts):
             if is_valid_date_partition(part):
                 date_part = part
                 break
         if date_part is None:
             raise ValueError(f"No valid date component in quarantine path: {quarantine_path}")
         candidate = quarantine_root / date_part  # Always canonical: quarantine_root/{date}/
         # Phase 1: Lexical containment (catch construction errors)
         try:
             relative = candidate.relative_to(quarantine_root)
         except ValueError:
             raise ValueError(f"Path {quarantine_path} not under quarantine root")
         if ".." in relative.parts:
             raise ValueError(f"Path traversal detected in {quarantine_path}")
         # Phase 2: Check each component for symlinks BEFORE resolve
         check = quarantine_root
         for part in relative.parts:
             check = check / part
             if check.is_symlink():
                 raise ValueError(f"Symlink detected at {check}")
         # Phase 3: Post-resolve containment (both root and candidate resolved independently)
         resolved_root = quarantine_root.resolve()
         resolved_path = candidate.resolve()
         if not resolved_path.is_relative_to(resolved_root):
             raise ValueError(f"Resolved path {resolved_path} escapes quarantine")
         # Phase 4: Enforce directory-only contract
         # Consumer always does `safe_path / "*.parquet"`, so file inputs would be invalid
         if resolved_path.exists() and resolved_path.is_file():
             raise ValueError(f"Expected directory, got file: {resolved_path}")
         return resolved_path  # Return RESOLVED path for immediate use (minimizes TOCTOU window)
     ```
     **Contract:** The validator always returns a **directory** path (the canonical `quarantine_root/{date}/` directory). The consumer globs with `safe_path / "*.parquet"`. File inputs (e.g., `2024-10-16/AAPL.parquet`) would produce invalid globs and are rejected. The date extraction strategy ensures both service mock paths (`crsp/2024-10-16`) and ETL paths (`2024-10-16`) resolve to the same canonical directory.
     Tests must cover: absolute path rejected (fail-closed), `../` traversal rejected before normalization (fail-closed), traversal after prefix stripping rejected (defense in depth), symlink in intermediate components (before resolve), symlink at leaf, post-resolve containment escape rejected, TOCTOU re-validation at point of use, file-path input (rejected), service mock format (`crsp/2024-10-16`), ETL format (`2024-10-16`), and all three resolving to same canonical dir. **Known limitation:** Hardlink attacks are not detected by the validator (OS-specific detection is complex). Mitigated by filesystem permissions (`0700` on quarantine directory). Document as operational requirement in the ADR.

4. **Auto-refresh:** Quality scores refresh every 60s via `ui.timer`, aligned with alert refresh from T13.3. The `timer_scores` timer is included in the unified cleanup callback defined in T13.3's Timer Cleanup Pattern section. **Do not register a separate cleanup** — add `timer_scores` to the existing single unified callback.

**Files to create:**
- `libs/data/data_quality/quality_scorer.py` - Pure scoring/trend computation (no I/O, no filesystem logic)

**Files to modify:**
- `apps/web_console_ng/pages/data_management.py` - Enhance quality tab with scores, trends, quarantine inspector
- `libs/data/data_quality/validation.py` - Add `validate_quarantine_path()` helper to existing validation module

**Acceptance Criteria:**
- [ ] Quality score cards per dataset with overall score, validation rate, anomaly count, quarantine count
- [ ] Scoring formula: `max(0, validation_pass_rate - anomaly_penalty - quarantine_penalty)`
- [ ] Trend summary with current, 7-day, 30-day averages and trend direction
- [ ] Degradation alert when 7-day avg drops >5% below 30-day avg
- [ ] Plotly trend chart with threshold lines at 90 (good) and 70 (critical)
- [ ] Quarantine inspector with drill-down via DuckDB Parquet reading, scoped to entry's dataset (no cross-dataset data leak). Entries where dataset-to-symbol mapping is ambiguous show "Preview unavailable" with informational message (expected while services are mock-backed)
- [ ] Path traversal prevention on quarantine_path before DuckDB registration (including symlink-safe checks)
- [ ] Auto-refresh every 60s with unified timer cleanup (T13.3 + T13.4 timers in single callback)
- [ ] Handles empty data gracefully (no validations, no trends, no quarantine)

**Unit Tests:**
- `compute_quality_scores()` with various validation/alert/quarantine combinations
- Scorer's internal `_normalize_status()`: `"ok"` → `"passed"`, `"error"` → `"failed"`, unknown pass-through
- Scoring uses normalized status: raw `"ok"` treated as `"passed"` in `validation_pass_rate` calculation (no external pre-normalization needed)
- Edge cases: zero validations (`overall_score` is `None`, rendered as "N/A"), all passed (score = 100), all failed (score = 0)
- Anomaly penalty cap at 30, quarantine penalty cap at 20
- `compute_trend_summary()` with known data points
- Trend direction: improving, stable, degrading thresholds
- Degradation alert: exactly at 5% boundary, above, below
- Insufficient data handling (< 2 points, < 7 points)
- **Quarantine path validation** (in `validation.py`): `../` escape rejected (fail-closed before normalization), absolute path rejected (fail-closed), traversal after prefix stripping rejected (defense in depth), symlink in intermediate component rejected (before resolve), symlink at leaf rejected, post-resolve containment verified, normal valid path accepted
- **Quarantine drill-down cross-dataset isolation:** verify that drill-down loads only the entry's dataset file (or filters via SQL), not all `*.parquet` files in the date directory
- Unified timer cleanup covers all T13.3 + T13.4 timers

**Test Files:**
- `tests/libs/data/data_quality/test_quality_scorer.py` — scoring, trend, normalization tests
- `tests/libs/data/data_quality/test_validation.py` — quarantine path validation tests (traversal, symlink, relative/absolute)

---

### T13.1: Point-in-Time Data Inspector - HIGH PRIORITY

**Goal:** Build a PIT lookup tool that shows what market data was available as of a specific "knowledge date", enabling researchers to validate that backtests don't suffer from look-ahead bias.

**Technical Context:**
- Market data is stored in `data/adjusted/YYYY-MM-DD/{SYMBOL}.parquet` (one file per symbol per run date)
- The directory date partition represents when the data was **processed** (pipeline run date), which serves as a proxy for when the data became "known"
- `DuckDBCatalog` provides efficient SQL queries over these Parquet files with predicate pushdown
- The orchestrator already supports `as_of_date` for signal generation, but there is no UI to inspect what data was available at a given point in time
- No existing PIT lookup or knowledge date filtering exists in the codebase

**Approach:**

#### Backend: `libs/data/data_quality/pit_inspector.py`
Create a new module in `libs/data/data_quality/` (existing quality package) for PIT inspection logic. This is a **logic-focused module with data access via `DuckDBCatalog`** (not pure computation — it performs filesystem discovery and DuckDB I/O). Imports `is_valid_date_partition` from `libs.data.data_quality.validation` (created in T13.4, which ships before T13.1).

1. **`PITLookupResult` dataclass:**
   ```python
   @dataclass
   class PITLookupResult:
       ticker: str
       knowledge_date: date           # The "as of" date
       data_available: list[PITDataPoint]  # Data known on or before knowledge_date
       data_future: list[PITDataPoint]     # Data from after knowledge_date (look-ahead)
       has_look_ahead_risk: bool      # True if data_future is non-empty
       latest_available_date: date | None   # Most recent data point available
       days_stale: int | None         # Days between knowledge_date and latest_available_date
       total_rows_available: int      # Total data points known (after dedup)
       future_partition_count: int    # Number of future run-date partitions (not row count)
   ```

2. **`PITDataPoint` dataclass:**
   ```python
   @dataclass
   class PITDataPoint:
       date: date          # The data's market date
       run_date: date      # When this data was processed (partition date)
       open: float
       high: float
       low: float
       close: float
       volume: int
       source: str         # "adjusted" or "quarantine"
   ```

3. **`PITInspector` class:**
   ```python
   class PITInspector:
       def __init__(self, data_dir: Path = Path("data")) -> None:
           self._data_dir = data_dir
           self._adjusted_dir = data_dir / "adjusted"

       def lookup(
           self,
           ticker: str,
           knowledge_date: date,
           lookback_days: int = 365,  # Calendar days (not trading days)
       ) -> PITLookupResult:
   ```
   **Authorization model:** `PITInspector` is a backend module that reads the filesystem. It does NOT enforce RBAC internally — authorization is enforced at the **page level** before calling `lookup()`. The page (`data_inspector_page`) is decorated with `@requires_auth` and gates the ticker input to the user's authorized symbol universe via `get_available_tickers()` filtered by the user's dataset permissions. The UI must NOT allow arbitrary ticker input — use a dropdown populated from the authorized ticker list. Tests must verify that unauthorized tickers cannot be passed to `lookup()`.
   ```python
   ```

   **Date validation helper** (shared by T13.1, T13.2, and `validation.py`):
   Defined as a public function in `libs/data/data_quality/validation.py` (created in T13.4, which ships before T13.1). This avoids a circular dependency: T13.4's `validate_quarantine_path()` uses it, and T13.1/T13.2 import it from the same module:
   ```python
   # In libs/data/data_quality/validation.py (created in T13.4):
   def is_valid_date_partition(s: str) -> bool:
       """Check if string is a valid YYYY-MM-DD date. Used for partition directory name validation."""
       try:
           date.fromisoformat(s)
           return True
       except ValueError:
           return False
   ```
   T13.1 (`pit_inspector.py`) and T13.2 (`coverage_analyzer.py`) import it:
   ```python
   from libs.data.data_quality.validation import is_valid_date_partition
   ```
   **Rationale:** Placing it in `validation.py` (T13.4) rather than `pit_inspector.py` (T13.1) respects the implementation order (T13.3 → T13.4 → T13.1 → T13.2). If it lived in `pit_inspector.py`, T13.4 would have a broken import until T13.1 was implemented.

   **SQL identifier sanitization helper** (shared by T13.1 and T13.2):
   ```python
   import re
   _SAFE_IDENT = re.compile(r"^[A-Za-z0-9_]+$")

   def _safe_table_name(prefix: str, *parts: str) -> str:
       """Build a DuckDB table name from components, sanitizing each part.
       Raises ValueError if any part contains non-identifier characters after normalization."""
       sanitized = [p.replace("-", "_").replace(".", "_") for p in parts]
       for s in sanitized:
           if not _SAFE_IDENT.match(s):
               raise ValueError(f"Unsafe identifier component: {s!r}")
       return f"{prefix}_{'_'.join(sanitized)}"
   ```
   All dynamic table names in T13.1 and T13.2 must use `_safe_table_name()` instead of raw f-strings. This prevents SQL identifier injection from malformed filenames.

   **Lookup algorithm:**
   1. **Validate inputs:** `ticker` must match `SYMBOL_PATTERN` (alphanumeric, 1-10 chars). `knowledge_date` must be <= today. `lookback_days` is in **calendar days** (not trading days) and must be >= 1 and <= 3650 (10 years). The `timedelta(days=lookback_days)` subtraction is correct for calendar-day lookback.
   2. **Discover available run dates:** Scan `data/adjusted/` directory for date-partition subdirectories. Each subdirectory name is a run date (YYYY-MM-DD). Filter to run dates <= `knowledge_date`.
   3. **Query data available as-of knowledge_date:** Register each date partition separately (not all at once), because the current `DuckDBCatalog.register_table()` creates views via `read_parquet(...)` which does **not** expose the `filename` virtual column. Instead, register one partition at a time and tag with run_date in Python:
      ```python
      # Partition discovery happens OUTSIDE the with block (pure filesystem)
      # Both partition sets use ALL run_date <= knowledge_date. Market-date filtering
      # is done in SQL (CAST(date AS DATE) >= earliest AND <= knowledge_date), NOT by
      # excluding partitions by run_date. This is critical: older run-date partitions
      # may contain valid market dates within the lookback window (e.g., reprocessed
      # snapshots), and excluding them by run_date would cause false staleness/missing
      # data conclusions.
      earliest_market_date = (knowledge_date - timedelta(days=lookback_days)).isoformat()
      available_partitions: list[tuple[str, date]] = []  # (path, run_date) — for data extraction
      contam_check_partitions: list[tuple[str, date]] = []  # (path, run_date) — for anomaly scan
      if not self._adjusted_dir.exists():
          return PITLookupResult(
              ticker=ticker, knowledge_date=knowledge_date,
              data_available=[], data_future=[],
              has_look_ahead_risk=False, latest_available_date=None,
              days_stale=None, total_rows_available=0,
              future_partition_count=0,
          )
      for d in sorted(self._adjusted_dir.iterdir()):
          if d.is_dir() and is_valid_date_partition(d.name) and d.name <= knowledge_date.isoformat():
              parquet_path = d / f"{ticker}.parquet"
              if parquet_path.exists():
                  run_dt = date.fromisoformat(d.name)
                  # BOTH sets include ALL run_date <= knowledge_date.
                  # Market-date bounds are enforced in SQL, not here.
                  contam_check_partitions.append((str(parquet_path), run_dt))
                  available_partitions.append((str(parquet_path), run_dt))

      # ALL DuckDB operations inside a single with block
      with DuckDBCatalog() as catalog:
          # Query each partition and tag rows with run_date in Python
          # Collect raw rows keyed by market date for deduplication
          raw_rows: dict[date, PITDataPoint] = {}  # market_date -> best PITDataPoint
          for path, run_date in available_partitions:
              table_name = _safe_table_name("avail", run_date.isoformat())
              catalog.register_table(table_name, path)
              rows = catalog.query(
                  f"SELECT *, CAST(date AS DATE) AS market_date FROM {table_name} WHERE CAST(date AS DATE) >= ? AND CAST(date AS DATE) <= ? ORDER BY market_date DESC",
                  params=[str(knowledge_date - timedelta(days=lookback_days)),
                          str(knowledge_date)]
              )
              for row in rows.iter_rows(named=True):
                  market_date = row["market_date"]
                  point = PITDataPoint(
                      date=market_date, run_date=run_date,
                      open=row["open"], high=row["high"], low=row["low"],
                      close=row["close"], volume=row["volume"], source="adjusted",
                  )
                  # Dedup: keep row from LATEST eligible run_date per market date
                  # (reprocessed snapshots should supersede older ones)
                  if market_date not in raw_rows or run_date > raw_rows[market_date].run_date:
                      raw_rows[market_date] = point
          all_available_rows = sorted(raw_rows.values(), key=lambda p: p.date, reverse=True)

          # --- Step 4b: Anomaly detection for future-dated data in ALL historical partitions ---
          # The main query (step 3) filters `date <= knowledge_date`, so future-dated rows
          # in historical partitions are silently excluded. Scan ALL partitions with
          # run_date <= knowledge_date (not just lookback-filtered ones) to detect contamination
          # in older partitions that could indicate data corruption or pipeline bugs.
          has_contaminated_historical = False
          for path, run_date in contam_check_partitions:
              table_name = _safe_table_name("contam", run_date.isoformat())
              catalog.register_table(table_name, path)  # Register for anomaly check
              anomaly_count = catalog.query(
                  f"SELECT COUNT(*) AS cnt FROM {table_name} WHERE CAST(date AS DATE) > ?",
                  params=[str(knowledge_date)]
              )
              if anomaly_count.row(0)[0] > 0:
                  has_contaminated_historical = True
                  break  # One contaminated partition is enough to flag

          # --- Step 4c: Sample future partitions (INSIDE same catalog session) ---
          all_future_rows: list[PITDataPoint] = []
          for path, run_date in future_partitions[:5]:
              table_name = _safe_table_name("future", run_date.isoformat())
              catalog.register_table(table_name, path)
              rows = catalog.query(
                  f"SELECT *, CAST(date AS DATE) AS market_date FROM {table_name} WHERE CAST(date AS DATE) > ? ORDER BY market_date ASC LIMIT 20",
                  params=[str(knowledge_date)]
              )
              for row in rows.iter_rows(named=True):
                  all_future_rows.append(PITDataPoint(
                      date=row["market_date"], run_date=run_date,
                      open=row["open"], high=row["high"], low=row["low"],
                      close=row["close"], volume=row["volume"], source="adjusted",
                  ))
      # End of `with DuckDBCatalog() as catalog:` — steps 3 + 4b + 4c all inside
      ```
      **Critical:** The `CAST(date AS DATE) <= knowledge_date` upper bound in SQL prevents any future-dated market data within valid partitions from contaminating the "available" set (a pipeline anomaly could write future market dates into a past run-date partition). The explicit `CAST(date AS DATE)` normalization ensures consistent Python `date` object keying regardless of whether Parquet stores dates as `DATE` or `TIMESTAMP`.

      **`run_date` derivation:** Known at partition discovery time from the directory name. Each partition is registered as a separate DuckDB table, so `run_date` is a Python-side tag — no reliance on DuckDB's `filename` virtual column (which the current `DuckDBCatalog` view abstraction does not expose).
   4. **Query future data (look-ahead):** Same per-partition approach for run dates > knowledge_date. **Important:** Decouple look-ahead risk detection from UI preview sampling. First, check ALL future partitions for existence (cheap file-system check **outside** `with`), then sample inside the same catalog session (step 4b in the code block above):
      ```python
      # Step 4a: Full scan for look-ahead risk flag (filesystem only, OUTSIDE DuckDB)
      # This runs BEFORE the `with DuckDBCatalog()` block above
      future_partitions: list[tuple[str, date]] = []
      # Note: self._adjusted_dir.exists() already checked above (early return if absent).
      # The iterdir() below is safe because execution only reaches here if dir exists.
      for d in sorted(self._adjusted_dir.iterdir()):
          if d.is_dir() and is_valid_date_partition(d.name) and d.name > knowledge_date.isoformat():
              parquet_path = d / f"{ticker}.parquet"
              if parquet_path.exists():
                  future_partitions.append((str(parquet_path), date.fromisoformat(d.name)))

      future_partition_count = len(future_partitions)  # Partition count, not row count
      # Step 4b anomaly detection + 4c sampling run INSIDE the `with` block (see code above)
      # has_look_ahead_risk is computed AFTER the `with` block completes (see step 5 below)
      ```
      **Scope rule (authoritative):** The execution order is:
      1. Filesystem discovery for available partitions (outside `with`)
      2. Filesystem discovery for future partitions — step 4a (outside `with`)
      3. **Single** `with DuckDBCatalog() as catalog:` block containing: step 3 queries + step 4b anomaly detection + step 4c future sampling
      All `catalog.register_table()` and `catalog.query()` calls are inside this one `with` block. Filesystem scans are outside.
   5. **Detect look-ahead risk (AFTER `with` block):** Compute `has_look_ahead_risk` **after** the `with DuckDBCatalog()` block completes, because `has_contaminated_historical` is set inside step 4b:
      ```python
      # This runs AFTER the `with` block (steps 3 + 4b + 4c)
      has_look_ahead_risk = len(future_partitions) > 0 or has_contaminated_historical
      ```
      `has_look_ahead_risk` is True if **either** (a) future partitions exist (step 4a filesystem check), **or** (b) future-dated market data is found inside historical partitions (step 4b anomaly query). The anomaly query reuses already-registered tables from step 3 to detect `date > knowledge_date` rows that the main query's `date <= knowledge_date` filter excludes. This catches pipeline bugs that write future market dates into past run-date partitions — a subtle contamination the filesystem-only check would miss. **Important:** Do NOT compute `has_look_ahead_risk` before the `with` block — `has_contaminated_historical` would be unbound.
   6. **Compute staleness:** `days_stale` is computed in **trading days** (not calendar days) between `knowledge_date` and `latest_available_date`, using `ExchangeCalendarAdapter("XNYS")` from `libs/data/data_quality/types.py` (same adapter as T13.2). Count days where `adapter.is_trading_day(d)` is True between the two dates. If `latest_available_date` is None, `days_stale` is None.
   7. **DuckDB resource management:** All snippets above use `with DuckDBCatalog() as catalog:` (context manager protocol). `DuckDBCatalog` implements `__enter__`/`__exit__`, guaranteeing `close()` even on exceptions. **Every `DuckDBCatalog` usage in T13.1 and T13.4 must use the `with` statement.** (T13.2 uses Polars direct reads instead of DuckDB — see T13.2 key design decisions.) Failure to close DuckDB leaks connections.

   **Performance:** DuckDB predicate pushdown on Parquet files means only relevant rows are read from each partition. Typical lookup takes ~50-200ms for a single ticker with 1-2 years of history. **Degradation at large lookbacks:** At `lookback_days=3650` (10 years), partition count can reach ~2500+; per-partition table registration may push latency to 1-2s. If this is observed during implementation, consider: (a) batch-registering partitions via glob-based `read_parquet`, (b) capping processed partitions at ~1000 with a UI notice, or (c) background async lookup with progress indicator. The current max lookback of 3650 is acceptable for the initial implementation but should be benchmarked during T13.1 testing.

   **Thread safety:** Each call creates its own `DuckDBCatalog` instance (in-memory DuckDB connection). Safe for concurrent use from multiple UI clients.

4. **`get_available_tickers()` helper:**
   ```python
   def get_available_tickers(self) -> list[str]:
       """Scan adjusted data directory for all available ticker symbols."""
       if not self._adjusted_dir.exists():
           return []  # Graceful: no data directory yet
       tickers: set[str] = set()
       for date_dir in self._adjusted_dir.iterdir():
           if date_dir.is_dir():
               for parquet_file in date_dir.glob("*.parquet"):
                   tickers.add(parquet_file.stem)
       return sorted(tickers)
   ```

5. **`get_date_range()` helper:**
   ```python
   def get_date_range(self) -> tuple[date | None, date | None]:
       """Get min and max run dates from adjusted data directory."""
       if not self._adjusted_dir.exists():
           return (None, None)  # Graceful: no data directory yet
       dates = [
           date.fromisoformat(d.name)
           for d in self._adjusted_dir.iterdir()
           if d.is_dir() and is_valid_date_partition(d.name)
       ]
       return (min(dates), max(dates)) if dates else (None, None)
   ```
   **All directory scans must guard with `if not dir.exists(): return ...`** to satisfy the graceful empty-data handling requirement. This applies to PIT inspector, coverage analyzer, and any helper that iterates filesystem paths.

#### Frontend: Page + Component

**Page:** `apps/web_console_ng/pages/data_inspector.py`

```python
@ui.page("/data/inspector")
@requires_auth
@main_layout
async def data_inspector_page() -> None:
```

- Permission: `VIEW_DATA_QUALITY` (reuse existing permission for data inspection)
- Page layout: Two-column layout
  - Left: Lookup form (ticker input, date picker, lookback slider)
  - Right: Results display

**Component:** `apps/web_console_ng/components/pit_lookup.py`

1. **`render_pit_lookup_form(inspector: PITInspector, on_submit: Callable)` function:**
   - Ticker input: `ui.select` dropdown populated from `inspector.get_available_tickers()` (NOT `ui.input` — dropdown enforces authorized-only selection). Server-side validation before `lookup()`: `if ticker not in authorized_tickers: raise ValueError("Unauthorized ticker")`. This prevents manual typing of unauthorized symbols.
   - Knowledge date: `ui.date` picker. Default to today. Constrained to `inspector.get_date_range()`.
   - Lookback days: `ui.slider(min=1, max=3650, value=365)` with label showing "{N} calendar days (~{N/365:.1f} years)". Min=1 matches the backend contract (`lookback_days >= 1`).
   - Submit button: "Inspect Point-in-Time"
   - Loading state during lookup (disable button, show spinner)

2. **`render_pit_results(result: PITLookupResult)` function:**
   - **Summary Card:**
     - Ticker and knowledge date header
     - Status badge: green "No look-ahead bias" or red "Look-ahead risk detected!"
     - Latest data available: `result.latest_available_date` with staleness (`result.days_stale` days stale)
     - Data points available: `result.total_rows_available`
   - **Available Data Table:**
     - `ui.table` showing `data_available` (most recent first): date, open, high, low, close, volume, run_date
     - Max 50 rows shown, with "Show all" expansion
   - **Future Data Warning (if `has_look_ahead_risk`):**
     - Red warning card: "The following data points were NOT yet available on {knowledge_date} but exist in the dataset. Using them would introduce look-ahead bias."
     - Table of `data_future` entries: date, close, run_date
   - **Timeline Visualization:**
     - Simple Plotly scatter chart: x=date, y=close for available data (blue) and future data (red dashed)
     - Vertical line at `knowledge_date` labeled "Knowledge Cutoff"
     ```python
     fig = go.Figure()
     fig.add_trace(go.Scatter(x=avail_dates, y=avail_close, mode="lines+markers",
                              name="Available (known)", line=dict(color="blue")))
     if result.data_future:
         fig.add_trace(go.Scatter(x=future_dates, y=future_close, mode="lines+markers",
                                  name="Future (look-ahead)", line=dict(color="red", dash="dash")))
     fig.add_vline(x=str(result.knowledge_date), line_dash="dash", line_color="gray",
                   annotation_text="Knowledge Cutoff")
     ```

#### Error Handling:
- Empty data directory: Show info message "No adjusted data found. Run the ETL pipeline first."
- Ticker not found: Show "No data available for {ticker}. Available tickers: {first 20...}"
- Knowledge date before any data: Show "No data available before {knowledge_date}. Earliest data: {min_date}"

**Files to create:**
- `libs/data/data_quality/pit_inspector.py` - PITInspector class + dataclasses
- `apps/web_console_ng/pages/data_inspector.py` - Page at `/data/inspector`
- `apps/web_console_ng/components/pit_lookup.py` - Lookup form + results rendering

**Files to modify:**
- `apps/web_console_ng/pages/__init__.py` - Add `data_inspector` import

**Acceptance Criteria:**
- [ ] PIT lookup form with authorized ticker `ui.select` dropdown and date picker
- [ ] Lookup queries Parquet files via DuckDB, filtering by run date partition
- [ ] Results show data available as-of knowledge date, deduplicated by market date (latest eligible run_date wins)
- [ ] Future data (look-ahead) shown separately with red warning; UI clarifies "Showing sample of {len(future_rows)} rows from {future_partition_count} future partition(s)" since only 5 partitions are sampled
- [ ] `has_look_ahead_risk` flag computed from: (a) ALL future partitions (full scan), AND (b) anomaly detection for future-dated market data in historical partitions
- [ ] Timeline chart with knowledge cutoff vertical line
- [ ] Staleness indicator in trading days (XNYS calendar, not calendar days)
- [ ] `run_date` derived from partition directory name at discovery time (no DuckDB `filename` column dependency)
- [ ] Per-partition DuckDB registration (separate table per run date) for reliable run_date tagging
- [ ] Explicit `date <= knowledge_date` upper bound in SQL prevents future market dates in valid partitions
- [ ] Input validation: SYMBOL_PATTERN, date range, lookback bounds
- [ ] Handles empty data directory gracefully
- [ ] DuckDB resources managed via `with DuckDBCatalog() as catalog:` context manager

**Unit Tests:**
- `PITInspector.lookup()` with known fixture data: data before and after knowledge date
- **Deduplication:** overlapping partitions (reprocessed snapshots) produce one row per market date with latest eligible run_date
- Look-ahead detection: data from future run dates flagged
- **Look-ahead full-scope:** risk flag is True when: (a) future partitions exist beyond knowledge_date, OR (b) future-dated market data is found embedded in historical partitions (anomaly detection)
- No data: empty data directory returns empty result
- Staleness calculation: correct trading-day days_stale computation (not calendar days)
- `run_date` extraction: verify partition directory name parsed correctly for each PITDataPoint
- Input validation: invalid ticker, future knowledge date, out-of-range lookback
- `_safe_table_name()`: valid identifiers pass, non-identifier chars rejected (ValueError)
- `get_available_tickers()` and `get_date_range()` with fixture directories
- Edge case: knowledge date exactly matches a run date (data available, not future)

**Test File:** `tests/libs/data/data_quality/test_pit_inspector.py`

---

### T13.2: Data Coverage Heatmap - MEDIUM PRIORITY

**Goal:** Visualize data completeness across the ticker universe with a symbol x date heatmap, enabling quick identification of data gaps.

**Technical Context:**
- Data stored as `data/adjusted/YYYY-MM-DD/{SYMBOL}.parquet`
- Polars `read_parquet(columns=["date"])` can efficiently extract date columns from Parquet files
- Need a trading calendar to distinguish "missing data" from "market closed"
- No existing coverage matrix computation in the codebase

**Approach:**

#### Backend: `libs/data/data_quality/coverage_analyzer.py`
Create in the existing `libs/data/data_quality/` package. Logic-focused module with data access via Polars (direct Parquet reads for efficient per-file date extraction).

1. **`CoverageStatus` enum:**
   ```python
   class CoverageStatus(str, Enum):
       COMPLETE = "complete"       # Data present for all expected trading days
       MISSING = "missing"         # No data for expected trading day
       SUSPICIOUS = "suspicious"   # Data present but quarantined or has quality issues
       NO_EXPECTATION = "no_expectation"  # Market closed, no data expected
   ```

2. **`CoverageMatrix` dataclass:**
   ```python
   @dataclass
   class CoverageMatrix:
       symbols: list[str]              # Row labels (sorted)
       dates: list[date]               # Column labels (sorted)
       matrix: list[list[CoverageStatus]]  # [symbol_idx][date_idx]
       summary: CoverageSummary
       truncated: bool                 # True if symbol list was capped at 200
       total_symbol_count: int         # Total symbols before truncation
       effective_resolution: Literal["daily", "weekly", "monthly"]  # Actual resolution used (may differ from requested if auto-coerced)
       notices: list[str] = field(default_factory=list)  # UI-facing messages (e.g., "Resolution auto-upgraded from daily to weekly: range exceeds 180 days")
       skipped_file_count: int = 0     # Files that could not be read (corrupt/malformed)

   @dataclass
   class CoverageSummary:
       total_expected: int         # Total symbol-date cells where data is expected
       total_present: int          # Cells with data
       total_missing: int          # Cells missing data
       total_suspicious: int       # Cells with quality issues
       coverage_pct: float         # total_present / total_expected * 100 (0.0 if total_expected == 0)
       gaps: list[CoverageGap]     # List of specific gaps

   @dataclass
   class CoverageGap:
       symbol: str
       start_date: date
       end_date: date
       gap_days: int              # Number of missing trading days
   ```

3. **`CoverageAnalyzer` class:**
   ```python
   class CoverageAnalyzer:
       def __init__(self, data_dir: Path = Path("data")) -> None:
           self._adjusted_dir = data_dir / "adjusted"
           self._quarantine_dir = data_dir / "quarantine"
   ```

   **Authorization model:** Like `PITInspector`, `CoverageAnalyzer` does NOT enforce RBAC internally. Authorization is enforced at the **page level**: the coverage page gates the `symbols` parameter to the user's authorized symbol universe. When `symbols=None`, the analyzer discovers all symbols from the filesystem — the UI must pre-filter this to authorized symbols before rendering. The page uses the same `@requires_auth` + permission check pattern as the PIT inspector page. Tests must verify unauthorized symbols are excluded from results.

   **`analyze()` method:**
   ```python
   def analyze(
       self,
       symbols: list[str] | None = None,
       start_date: date | None = None,
       end_date: date | None = None,
       resolution: Literal["daily", "weekly", "monthly"] = "monthly",
   ) -> CoverageMatrix:
   ```

   **Algorithm:**
   1. **Build date axis (calendar days, classified):** Generate **all calendar days** between `effective_start` and `effective_end`. For each day, use `ExchangeCalendarAdapter("XNYS")` to classify as trading day or non-trading day. The matrix date axis includes all calendar days; non-trading days receive `NO_EXPECTATION` status (they appear in the heatmap as gray cells so the user sees weekends/holidays in context). Only trading days contribute to `total_expected` in the summary. This uses the existing `ExchangeCalendarAdapter("XNYS")` from `libs/data/data_quality/types.py` (which wraps the `exchange_calendars` library already in the project's dependencies). This provides accurate NYSE session dates (not US federal holidays, which differ — e.g., Columbus Day is a federal holiday but NYSE is open). Call `adapter.is_trading_day(d)` to classify each date in the range. If no date range specified, use the full range from available data partitions. **Do not introduce `pandas_market_calendars` — use the existing `exchange_calendars` adapter for consistency.**
   2. **Discover available symbols:** If `symbols` is None, scan all Parquet files to get unique ticker names. Sort **lexicographically ascending** (deterministic ordering), then cap at 200 symbols to prevent excessive heatmap rendering. **If truncated, the UI must show a visible info banner:** "Showing first 200 of {total_count} symbols (alphabetical). Use the symbol filter to narrow results." The returned `CoverageMatrix` includes a `truncated: bool` field and `total_symbol_count: int` for the UI to render this indicator.
   3. **Query data presence:** Use Polars direct reads per file to reliably derive symbol from the filename, since not all Parquet files are guaranteed to contain a `symbol` column. Polars is used instead of DuckDB here to avoid O(symbols * date_dirs) view registration overhead (see Key design decisions):
      ```python
      # Materialize concrete date bounds before querying (nullable -> effective)
      if start_date is None or end_date is None:
          data_min, data_max = self._discover_date_range()  # scan partition dirs
          if data_min is None or data_max is None:
              # No data partitions found — return empty matrix immediately
              return CoverageMatrix(
                  symbols=[], dates=[], matrix=[], summary=CoverageSummary(
                      total_expected=0, total_present=0, total_missing=0,
                      total_suspicious=0, coverage_pct=0.0, gaps=[],
                  ), truncated=False, total_symbol_count=0,
              )
          effective_start = start_date or data_min
          effective_end = end_date or data_max
      else:
          effective_start, effective_end = start_date, end_date

      # Build target symbol set (enforce 200-symbol cap for BOTH auto-discovered and user-provided)
      if symbols is None:
          all_symbols = sorted({pq.stem for dd in self._adjusted_dir.iterdir()
                                if dd.is_dir() for pq in dd.glob("*.parquet")}) if self._adjusted_dir.exists() else []
      else:
          all_symbols = sorted(symbols)  # Normalize user input for deterministic ordering
      total_symbol_count = len(all_symbols)
      target_symbols = set(all_symbols[:200])  # Cap ALWAYS applied (prevents DoS via large input)
      truncated = total_symbol_count > 200

      # Step 3: Scan adjusted data using Polars direct reads (NOT DuckDB per-file views).
      # Rationale: Per-file DuckDB view registration would create O(symbols * date_dirs) views
      # (e.g., 200 * 252 = 50,400 views for 1 year), causing significant latency.
      # Polars read_parquet(columns=["date"]) is lightweight — reads only the date column
      # without full schema inference or view creation overhead.
      # Per-file fault tolerance: wrap each read in try/except to degrade gracefully
      # on corrupt/malformed files instead of failing the entire analysis.
      presence_set: set[tuple[str, date]] = set()  # (symbol, market_date)
      skipped_files: list[str] = []  # Track skipped files for UI warning
      if not self._adjusted_dir.exists():
          return CoverageMatrix(
              symbols=[], dates=[], matrix=[], summary=CoverageSummary(...),
              truncated=False, total_symbol_count=0,
              effective_resolution="daily", notices=["No adjusted data directory found"],
          )
      for date_dir in sorted(self._adjusted_dir.iterdir()):
          if not date_dir.is_dir() or not is_valid_date_partition(date_dir.name):
              continue
          for pq_file in date_dir.glob("*.parquet"):
              symbol = pq_file.stem  # Symbol derived from filename, not column
              if symbol not in target_symbols:
                  continue  # Skip non-target symbols (enforces 200 cap)
              try:
                  df = pl.read_parquet(pq_file, columns=["date"])
              except Exception:
                  logger.warning("coverage_scan_skip_file",
                      extra={"file": str(pq_file), "symbol": symbol,
                             "partition": date_dir.name})
                  skipped_files.append(str(pq_file))
                  continue  # Degrade gracefully: skip corrupt file
              for market_date in df["date"].unique().to_list():
                  # Normalize: Parquet may store as datetime, convert to date
                  if hasattr(market_date, "date"):
                      market_date = market_date.date()
                  if effective_start <= market_date <= effective_end:
                      presence_set.add((symbol, market_date))

      # Step 4: Scan quarantine data with same Polars approach (same target_symbols filter)
      quarantine_set: set[tuple[str, date]] = set()
      if self._quarantine_dir.exists():
          for date_dir in sorted(self._quarantine_dir.iterdir()):
              if not date_dir.is_dir() or not is_valid_date_partition(date_dir.name):
                  continue
              for pq_file in date_dir.glob("*.parquet"):
                  symbol = pq_file.stem
                  if symbol not in target_symbols:
                      continue  # Skip non-target symbols
                  try:
                      df = pl.read_parquet(pq_file, columns=["date"])
                  except Exception:
                      logger.warning("coverage_scan_skip_quarantine_file",
                          extra={"file": str(pq_file), "symbol": symbol,
                                 "partition": date_dir.name})
                      skipped_files.append(str(pq_file))
                      continue
                  for market_date in df["date"].unique().to_list():
                      if hasattr(market_date, "date"):
                          market_date = market_date.date()
                      if effective_start <= market_date <= effective_end:
                          quarantine_set.add((symbol, market_date))
      # If files were skipped, include count in CoverageMatrix for UI warning banner:
      # "N files could not be read and were excluded from analysis"
      ```
      **Key design decisions:**
      - **No run_date filtering on partitions:** Reprocessed partitions outside the market-date window may still contain valid market dates within range. All partitions are scanned; rows are filtered by market date in Python (coverage) or SQL (PIT).
      - **Nullable bounds materialized:** `start_date`/`end_date` are nullable in the API. Before querying, materialize concrete `effective_start`/`effective_end` from `_discover_date_range()` (scans partition directory names). This ensures SQL params are never `None`.
      - **Polars direct reads (not DuckDB) for coverage scanning:** Per-file DuckDB view registration would create O(symbols * date_dirs) views (e.g., 200 * 252 = 50,400 views for 1 year), causing significant latency and memory overhead. Instead, use `pl.read_parquet(file, columns=["date"])` to extract only the date column per file — lightweight, no view creation, no SQL overhead. DuckDB is still used for T13.1 PIT Inspector (where SQL filtering and dedup add value) and T13.4 quarantine drill-down preview (SQL LIMIT).
      - **Per-file symbol derivation:** The ETL pipeline stores one Parquet file per symbol (`{SYMBOL}.parquet`), and files are not guaranteed to have a `symbol` column. Deriving symbol from `pq_file.stem` is reliable and consistent with T13.1's per-partition approach.
      - **Date-type normalization:** All DuckDB SQL queries (T13.1 PIT, T13.4 drill-down) must use `CAST(date AS DATE) AS market_date` to normalize the `date` column. All Polars reads (T13.2 coverage) must normalize via `hasattr(d, "date")` check to handle both `DATE` and `TIMESTAMP` Parquet storage. This prevents set membership failures and comparison drift when keying against Python `date` objects.
   5. **Build matrix:** For each (symbol, date) pair in the expected grid, check against `presence_set` and `quarantine_set` built in step 3:
      - If `date` is not a trading day: `NO_EXPECTATION`
      - If `(symbol, date) in quarantine_set`: `SUSPICIOUS`
      - If `(symbol, date) in presence_set`: `COMPLETE`
      - Else: `MISSING`
   6. **Aggregate by resolution:** Reduce daily statuses to coarser time buckets:
      - **Daily** (`resolution="daily"`): No aggregation; one cell per **calendar day** (including weekends/holidays as `NO_EXPECTATION` gray cells, matching the calendar-day matrix axis from step 1). **Hard cap:** Daily mode is limited to 180 calendar days maximum. If the requested range exceeds 180 days, auto-upgrade to weekly resolution. Set `effective_resolution="weekly"` and append to `notices`: `"Date range exceeds 180 days; switched to weekly view. Use a shorter range for daily granularity."` The UI renders `notices` as info banners above the heatmap. This prevents browser/Plotly overload from large `z` + `text` matrices.
      - **Weekly** (`resolution="weekly"`): Group by ISO week (Monday-Sunday). Each week's label is the Monday date. Precedence rules (same as monthly): `MISSING` > `SUSPICIOUS` > `COMPLETE` > `NO_EXPECTATION`. If any day in the week is `MISSING`, the week is `MISSING`. If all trading days are `COMPLETE`, the week is `COMPLETE`. If any day is `SUSPICIOUS` (but none missing), the week is `SUSPICIOUS`. Weeks with no trading days are `NO_EXPECTATION`.
      - **Monthly** (`resolution="monthly"`): Group by calendar month. Same precedence rules as weekly. The month label is the first day of the month.
      - This keeps the heatmap compact for long date ranges
   7. **Compute summary:** Count totals, compute `coverage_pct`, identify contiguous gaps. **Edge case:** If `total_expected == 0` (no trading days in range for any symbol), set `coverage_pct = 0.0` to avoid division by zero. This is explicitly tested.
   8. **Identify gaps:** Scan each symbol's timeline for consecutive `MISSING` **trading days**, produce `CoverageGap` entries. **Gap semantics:** Gaps are contiguous runs of missing *trading days* only. Non-trading days (`NO_EXPECTATION`) between two missing trading days do NOT break a gap — they are skipped when determining contiguity. For example, if Friday and Monday are both `MISSING`, Saturday/Sunday (`NO_EXPECTATION`) in between means this is ONE gap of 2 trading days, not two gaps of 1. `gap_days` counts only missing trading days (excludes weekends/holidays).

   **Performance:** Polars `read_parquet(columns=["date"])` reads only the date column per file (minimal I/O). ~200ms for 200 symbols x 252 trading days (with the 200-symbol cap enforced). Benchmark assumes warm filesystem cache on SSD; cold-cache first-run may take 2-3x longer.

4. **`export_coverage_report()` method:**
   ```python
   def export_coverage_report(
       self, matrix: CoverageMatrix, format: Literal["csv", "json"] = "csv"
   ) -> str:
       """Export coverage matrix as CSV or JSON string."""
   ```
   - CSV: symbol, date, status columns (one row per cell)
   - JSON: structured summary + gaps list

#### Frontend: Page + Component

**Page:** `apps/web_console_ng/pages/data_coverage.py`

```python
@ui.page("/data/coverage")
@requires_auth
@main_layout
async def data_coverage_page() -> None:
```

- Permission: `VIEW_DATA_QUALITY`
- Links from data inspector page and data management page

**Component:** `apps/web_console_ng/components/coverage_heatmap.py`

1. **`render_coverage_controls(analyzer: CoverageAnalyzer, on_analyze: Callable)` function:**
   - Symbol filter: `ui.select` multi-select dropdown populated from `analyzer.get_available_tickers()` (authorized universe only), or "All" checkbox. Server-side validation: user-entered symbols are intersected with `authorized_tickers` before calling `analyze()`. Unauthorized symbols are silently excluded (not forwarded to the analyzer). If intersection is empty, show "No authorized symbols selected" warning.
   - Date range: `ui.date` pickers for start/end (default: last 12 months)
   - Resolution: `ui.toggle(["Daily", "Weekly", "Monthly"], value="Monthly")`
   - Analyze button + loading spinner

2. **`render_coverage_heatmap(matrix: CoverageMatrix)` function:**
   - **Summary Cards Row:**
     - Coverage percentage with color (>= 95% green, 80-94% amber, <80% red)
     - Total expected cells
     - Missing count
     - Suspicious count
   - **Plotly Heatmap:** Use normalized 0..1 numeric mapping for all statuses (not negative values) with explicit discrete color boundaries:
     ```python
     # Map CoverageStatus to normalized 0..1 range
     status_to_value = {
         CoverageStatus.MISSING: 0.0,
         CoverageStatus.SUSPICIOUS: 0.33,
         CoverageStatus.COMPLETE: 0.67,
         CoverageStatus.NO_EXPECTATION: 1.0,
     }
     z = [[status_to_value[cell] for cell in row] for row in matrix.matrix]

     fig = go.Figure(data=go.Heatmap(
         z=z,
         x=[d.isoformat() for d in matrix.dates],
         y=matrix.symbols,
         colorscale=[
             [0.0, "#ef4444"],     # Missing: red
             [0.165, "#ef4444"],   # Missing boundary
             [0.165, "#f59e0b"],   # Suspicious: amber
             [0.5, "#f59e0b"],     # Suspicious boundary
             [0.5, "#22c55e"],     # Complete: green
             [0.835, "#22c55e"],   # Complete boundary
             [0.835, "#e5e7eb"],   # No expectation: light gray
             [1.0, "#e5e7eb"],     # No expectation boundary
         ],
         zmin=0.0, zmax=1.0,
         hovertemplate="Symbol: %{y}<br>Date: %{x}<br>Status: %{text}<extra></extra>",
         text=[[cell.value for cell in row] for row in matrix.matrix],
         colorbar=dict(
             tickvals=[0.0, 0.33, 0.67, 1.0],
             ticktext=["Missing", "Suspicious", "Complete", "No Expectation"],
         ),
     ))
     fig.update_layout(
         title=f"Data Coverage ({matrix.summary.coverage_pct:.1f}%)",
         xaxis_title="Date",
         yaxis_title="Symbol",
         height=max(400, len(matrix.symbols) * 20 + 100),
     )
     ui.plotly(fig).classes("w-full")
     ```
   - **Click-to-investigate:** Use Plotly `plotly_click` event handler. On click, show detail card for that (symbol, date) cell:
     - Status, whether quarantined (with reason if SUSPICIOUS)
     - Link to PIT inspector for that symbol/date combination: `/data/inspector?ticker={symbol}&date={date}`
   - **Gaps Table:**
     - List `matrix.summary.gaps` as a `ui.table` with columns: symbol, start_date, end_date, gap_days
     - Sorted by gap_days descending (largest gaps first)
     - Click gap row to navigate to PIT inspector

3. **`render_coverage_export(matrix: CoverageMatrix, analyzer: CoverageAnalyzer)` function:**
   - Export button with format selector (CSV/JSON)
   - Generate export via `analyzer.export_coverage_report()`
   - Download via `ui.download()` or copy to clipboard

#### Navigation Integration:
- Add "Coverage" link in the data management page sidebar or as a new tab
- Add "Coverage" link in data inspector page header
- Bidirectional navigation between inspector and coverage via URL parameters

**Files to create:**
- `libs/data/data_quality/coverage_analyzer.py` - CoverageAnalyzer + CoverageMatrix
- `apps/web_console_ng/pages/data_coverage.py` - Page at `/data/coverage`
- `apps/web_console_ng/components/coverage_heatmap.py` - Heatmap rendering

**Files to modify:**
- `apps/web_console_ng/pages/__init__.py` - Add `data_coverage` import
- `apps/web_console_ng/pages/data_inspector.py` - Add link to coverage page

**Acceptance Criteria:**
- [ ] Coverage analyzer queries adjusted and quarantine Parquet data via Polars direct reads
- [ ] NYSE/XNYS exchange session calendar used to distinguish missing data from market-closed dates
- [ ] Symbol x Date matrix with status: complete, missing, suspicious, no_expectation
- [ ] Resolution aggregation: daily, weekly, monthly
- [ ] Plotly heatmap with color coding (green=complete, amber=suspicious, red=missing, gray=no expectation)
- [ ] Click heatmap cell to see details and link to PIT inspector
- [ ] Gaps table sorted by largest gap first
- [ ] Summary cards: coverage %, total expected, missing, suspicious
- [ ] Export coverage report as CSV or JSON
- [ ] Symbol cap at 200 with UI truncation banner showing total count
- [ ] Handles empty data directory gracefully
- [ ] Per-file fault tolerance: corrupt/malformed Parquet files logged and skipped, not fatal
- [ ] `skipped_file_count` in `CoverageMatrix` reported to UI as warning banner when > 0

**Unit Tests:**
- `CoverageAnalyzer.analyze()` with fixture Parquet data
- Symbol derivation: symbol extracted from `pq_file.stem` (filename), not from Parquet column
- Trading calendar: weekends excluded, NYSE holidays excluded (not just federal holidays)
- Status assignment: complete, missing, suspicious, no_expectation
- Resolution aggregation: daily (no rollup), weekly (ISO week, Monday-Sunday), monthly rollup logic
- Weekly boundary: week with no trading days → `NO_EXPECTATION`
- Gap detection: contiguous missing dates identified
- `export_coverage_report()` output format validation
- Empty data: no Parquet files returns empty matrix
- Zero expected cells: `total_expected == 0` yields `coverage_pct == 0.0` (no division by zero)
- Symbol cap: > 200 symbols truncated with warning

**Test File:** `tests/libs/data/data_quality/test_coverage_analyzer.py`

---

## Dependencies

```
T13.3 Wire Services      (independent, implemented first)
T13.4 Quality Dashboard   (depends on T13.3, extends quality tab wired in T13.3)
T13.1 PIT Inspector       (depends on T13.4 for validation.py helper, new page + backend)
T13.2 Coverage Heatmap    (depends on T13.1 filesystem patterns, links to inspector page)
```

**Internal dependency:**
```
T13.3 Wire Services --> T13.4 Quality Dashboard (extends quality tab)
T13.4 Quality Dashboard --> T13.1 PIT Inspector (validation.py: is_valid_date_partition)
T13.1 PIT Inspector --> T13.2 Coverage Heatmap  (filesystem patterns, cross-page links)
```

**External dependencies:**
- P5 complete (NiceGUI migration)
- P6T12.4 HealthMonitor (already implemented - health_monitor.py exists)
- `DuckDBCatalog` (libs/duckdb_catalog.py - already exists)
- Data services (libs/web_console_services/ - already exist with mock implementations)
- DB migrations 0012-0017 (already applied)

---

## File Summary

### New Files (7)
| File | Task | Purpose |
|------|------|---------|
| `libs/data/data_quality/quality_scorer.py` | T13.4 | Pure quality score computation + trend analysis (no I/O) |
| `libs/data/data_quality/pit_inspector.py` | T13.1 | PIT lookup via DuckDB on Parquet partitions |
| `libs/data/data_quality/coverage_analyzer.py` | T13.2 | Coverage matrix computation via Polars |
| `apps/web_console_ng/pages/data_inspector.py` | T13.1 | PIT inspector page at `/data/inspector` |
| `apps/web_console_ng/pages/data_coverage.py` | T13.2 | Coverage heatmap page at `/data/coverage` |
| `apps/web_console_ng/components/pit_lookup.py` | T13.1 | PIT lookup form + results rendering |
| `apps/web_console_ng/components/coverage_heatmap.py` | T13.2 | Heatmap + controls + export rendering |

### Modified Files (4)
| File | Task | Change |
|------|------|--------|
| `apps/web_console_ng/pages/data_management.py` | T13.3, T13.4 | Replace mock data with service calls, add quality scores/trends/quarantine inspector |
| `apps/web_console_ng/pages/__init__.py` | T13.1, T13.2 | Add `data_inspector` and `data_coverage` imports |
| `apps/web_console_ng/core/client_lifecycle.py` | T13.3 (prerequisite) | Add keyed callback API (`owner_key` parameter) for safe per-page dedup (see ADR) |
| `libs/data/data_quality/validation.py` | T13.4 | Add `validate_quarantine_path()` helper to existing validation module |

### New Test Files (5)
| File | Task |
|------|------|
| `tests/apps/web_console_ng/pages/test_data_management_wiring.py` | T13.3 |
| `tests/libs/data/data_quality/test_quality_scorer.py` | T13.4 |
| `tests/libs/data/data_quality/test_validation.py` | T13.4 |
| `tests/libs/data/data_quality/test_pit_inspector.py` | T13.1 |
| `tests/libs/data/data_quality/test_coverage_analyzer.py` | T13.2 |

---

## Backend Integration Points

| Feature | Backend Location | Data Source | Frontend Action |
|---------|-----------------|-------------|-----------------|
| PIT Lookup | `libs/data/data_quality/pit_inspector.py` | `data/adjusted/` Parquet via DuckDB | Query by ticker + date, display results |
| Data Coverage | `libs/data/data_quality/coverage_analyzer.py` | `data/adjusted/` + `data/quarantine/` via Polars | Heatmap visualization |
| Sync Status | `libs/web_console_services/data_sync_service.py` | Service layer (currently mock-backed; DB tables 0012-0013 exist for future) | Wire to data_management.py |
| Data Explorer | `libs/web_console_services/data_explorer_service.py` | Service layer (currently mock-backed; DuckDB catalog for future) | Wire query editor |
| Data Quality | `libs/web_console_services/data_quality_service.py` | Service layer (currently mock-backed; DB tables 0015-0017 exist for future) | Wire to quality tab |
| Quality Scoring | `libs/data/data_quality/quality_scorer.py` | Computed from service DTOs | Score cards + trend charts |

---

## Testing Strategy

### Unit Tests
- T13.3: Service call wiring, error handling, timer lifecycle
- T13.4: Quality score computation, trend summary, degradation alerting
- T13.1: PIT lookup algorithm, look-ahead detection, input validation, staleness
- T13.2: Coverage matrix building, trading calendar, gap detection, resolution aggregation

### Integration Tests
- T13.3: Service -> page data flow (service returns mock data, page renders correctly)
- T13.1: PIT inspector with fixture Parquet directory structure
- T13.2: Coverage analyzer with fixture Parquet files (adjusted + quarantine)

### E2E Tests
- T13.3: Data management page loads without demo banner, all tabs render
- T13.1: PIT inspector form submission, results display, timeline chart, **look-ahead risk red warning badge verified** with mock future data in test environment
- T13.2: Coverage heatmap renders, click-to-investigate works

---

## Patterns & Conventions

All implementations follow established codebase patterns:
- **Components:** Render functions (`def render_X(...) -> None`) with `__all__` exports
- **Error handling:** None/empty guard -> input validation -> try/except with `logger.warning`
- **Charts:** Plotly `go.Figure()` + `ui.plotly(fig).classes("w-full")`
- **Pydantic models:** For configuration only; `@dataclass` for result types
- **Type hints:** Full typing, `TYPE_CHECKING` for heavy imports
- **Logging:** `logger = logging.getLogger(__name__)` with structured extras; PIT Inspector logs must include `symbol` (ticker) for audit traceability
- **Timers:** `ui.timer` + `ClientLifecycleManager` cleanup
- **Permissions:** `has_permission(user, Permission.X)` checks at page and operation level
- **DuckDB:** `DuckDBCatalog()` per-request for thread safety, always use `with` statement (T13.1 PIT, T13.4 drill-down). T13.2 coverage uses Polars direct reads instead.
- **Trading calendar:** `ExchangeCalendarAdapter("XNYS")` from `libs/data/data_quality/types.py` (not `pandas_market_calendars`)
- **Client ID:** Use centralized `get_or_create_client_id()` from `apps/web_console_ng/utils/session.py` (checks storage, generates if missing, falls back to `client.id`)
- **Data access:** All user-facing data filtered by RBAC (dataset permissions)
- **Security:** Path traversal prevention for file-system access, parameterized SQL queries

---

## Definition of Done

- [ ] All 4 tasks implemented
- [ ] Data management page uses real services (no mock data, no demo banner)
- [ ] Quality dashboard with computed scores, trend charts, degradation alerts
- [ ] PIT inspector at `/data/inspector` with look-ahead bias detection
- [ ] Coverage heatmap at `/data/coverage` with gap identification
- [ ] All service errors handled gracefully (PermissionError, RateLimitExceeded, unavailable)
- [ ] Auto-refresh timers with cleanup
- [ ] Unit tests > 85% coverage per module
- [ ] E2E tests pass
- [ ] Code reviewed and approved
- [ ] No new mypy errors (`mypy --strict`)

---

**Pre-Implementation Checklist:**
- [ ] Transition workflow_gate state from P6T12 PR-review to P6T13 planning before coding
- [ ] Verify `exchange_calendars` is in project dependencies. Currently present in `requirements.txt` (`exchange-calendars==4.5.3`). If also needed in `pyproject.toml` under `[tool.poetry.dependencies]`, add `exchange-calendars = "^4.0"`. Canonical dependency management: Poetry (`pyproject.toml`) is the source of truth; `requirements.txt` is generated via `poetry export`. Verify both are aligned before implementation
- [ ] Plan approved by all reviewers
- [ ] **ADR:** One ADR required for the **keyed callback API** added to `ClientLifecycleManager`. This is a cross-cutting change to a shared infrastructure component used by multiple pages (layout.py, dashboard, manual_order, data_management). The ADR must document: (1) motivation (dedup without wipeout), (2) backward-compatible API design (`owner_key` optional parameter), (3) internal storage type change from `list[Callable]` to `list[tuple[Callable, str | None]]`, (4) migration expectations for existing callers (none — legacy callers without `owner_key` continue to append as before), and (5) rollout/testing plan. File: `docs/ADRs/ADR-XXXX-keyed-lifecycle-callbacks.md` (assign concrete ID before implementation starts; update frontmatter `related_adrs` field from `ADR-PENDING-keyed-lifecycle-callbacks` to the assigned ID). All other T13 changes (UI pages, pure-logic modules) follow established patterns and do not require ADRs.

**Last Updated:** 2026-02-12
**Status:** PLANNING
