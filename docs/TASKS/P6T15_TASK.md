---
id: P6T15
title: "Professional Trading Terminal - Universe & Exposure"
phase: P6
task: T15
priority: P1
owner: "@development-team"
state: TASK
created: 2026-01-13
dependencies: [P5]
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P6_PLANNING.md]
estimated_effort: "7-9 days"
features: [T15.1-T15.3]
---

# P6T15: Universe & Exposure

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** TASK (Not Started)
**Priority:** P1 (Research Infrastructure)
**Owner:** @development-team
**Created:** 2026-01-13
**Estimated Effort:** 7-9 days
**Track:** Track 15 of 18
**Dependency:** P5 complete

---

## Objective

Build universe management and strategy exposure dashboards for research and risk monitoring.

**Success criteria (v1):**
- Universe listing with built-in (SP500, R1000) and custom universe definitions (T15.1 — real CRSP data for built-in, JSON-file persistence for custom)
- Universe analytics with characteristics: market cap distribution, ADV statistics, sector breakdown (T15.2 — real CRSP-derived metrics for market cap/ADV, mock sector data v1)
- Net exposure by strategy dashboard with per-strategy Long/Short/Gross/Net breakdowns and directional bias warnings (T15.3 — real position data via `ExposureQueries`, mock fallback when positions empty)

> Code snippets are non-normative guidance. Binding requirements are acceptance criteria, security invariants, and DoD items.

**Out of Scope:**
- No new database migrations — universe definitions stored in JSON files under `data/universes/`; no Postgres tables needed for v1
- No changes to `UniverseProvider` internals (already provides PIT-safe constituents)
- No changes to the execution gateway or position tracking systems
- No real-time streaming of position updates — uses periodic polling (30s timer)
- No Compustat integration for sector classifications — v1 uses mock sector mapping; real GICS data is a future enhancement
- No beta/factor computation from scratch — v1 uses mock factor data; real factor loadings require Barra model artifacts from `libs/risk/`
- No changes to `BacktestJobConfig` schema — universe selection in backtest config is deferred to a follow-up task after T15.1 proves the universe manager API stable

---

## Implementation Order

```
T15.3 Net Exposure by Strategy  (HIGH, no deps, new ExposureQueries adapter)
T15.1 Universe Management       (HIGH, no deps, needs new universe_manager.py backend)
T15.2 Universe Analytics         (MEDIUM, depends on T15.1 universe_manager.py)
```

**Dependency model:** T15.3 is **independent** — it uses only existing position/P&L infrastructure via `ExposureQueries` (new adapter in `libs/web_console_data/`) and does not share business logic with T15.1/T15.2. T15.1 introduces the `UniverseManager` backend and the universe page. T15.2 depends on T15.1's `UniverseManager` for constituent data. All three share **integration touchpoints** (`apps/web_console_ng/pages/__init__.py` for page registration, `apps/web_console_ng/ui/layout.py` for nav items) and should be committed sequentially.

T15.3 is implemented first because it is the most operationally valuable (risk monitoring), uses well-understood existing infrastructure, and is completely independent. T15.1 follows with the universe manager — a new module but with clear requirements. T15.2 is last because it depends on T15.1 and requires the most mock data (sectors, factors).

---

## Prerequisite: Permission Registration

**Before any T15 task implementation,** add the following permissions to `libs/platform/web_console_auth/permissions.py`:

```python
class Permission(str, Enum):
    ...
    # T15: Universe & Exposure
    VIEW_UNIVERSES = "view_universes"            # T15.1/T15.2: Universe browser/analytics
    MANAGE_UNIVERSES = "manage_universes"        # T15.1: Create/edit/delete custom universes
    VIEW_STRATEGY_EXPOSURE = "view_strategy_exposure"  # T15.3: Strategy exposure dashboard
```

Role assignments:
```python
Role.RESEARCHER: {
    ...,
    Permission.VIEW_UNIVERSES,         # T15.1/T15.2: Browse universes for research
    Permission.VIEW_STRATEGY_EXPOSURE, # T15.3: View exposure for research
}
Role.OPERATOR: {
    ...,
    Permission.VIEW_UNIVERSES,
    Permission.MANAGE_UNIVERSES,       # T15.1: Operators manage custom universes
    Permission.VIEW_STRATEGY_EXPOSURE,
}
Role.VIEWER: {
    ...,
    # No T15 permissions — universe and exposure features are research/operations-facing only.
    # Viewers see live positions/P&L but not strategy-level exposure or universe research tools.
}
Role.ADMIN: set(Permission),  # Admins have all permissions
```

---

## Existing Infrastructure Audit

### What EXISTS (reuse, don't rebuild):

| Component | Location | Status |
|-----------|----------|--------|
| `UniverseProvider` | `libs/data/data_providers/universe.py` | PIT-safe SP500/R1000 constituents via CRSP parquet |
| `CRSPLocalProvider` | `libs/data/data_providers/crsp_local_provider.py` | Daily returns with `[date, permno, cusip, ticker, ret, prc, vol, shrout]` |
| `config/universe.py` | `config/universe.py` | MVP `TRADABLE_SYMBOLS` filter (live trading only) |
| `StrategyScopedDataAccess` | `libs/web_console_data/strategy_scoped_queries.py` | Permission-aware queries — **CAUTION: `get_positions()` has broken SQL referencing non-existent `strategy_id` column on positions table; use `DatabaseClient.get_positions_for_strategies()` instead for T15.3** |
| `DatabaseClient.get_positions_for_strategies()` | `apps/execution_gateway/database.py:3106` | Best-effort symbol-to-strategy mapping (fail-closed) |
| `RiskService` | `libs/web_console_services/risk_service.py` | Factor exposures, VaR, stress tests |
| `BacktestJobConfig` | `libs/trading/backtest/job_queue.py:81` | `@dataclass` with `alpha_name, start_date, end_date, weight_method, provider, extra_params` |
| NiceGUI page patterns | `apps/web_console_ng/pages/risk.py`, `data_source_status.py` | `@ui.page` + `@requires_auth` + `@main_layout` + service layer |
| Data management DTOs | `libs/web_console_services/schemas/data_management.py` | Pydantic schema patterns |

### What DOES NOT EXIST (must build):

| Component | Purpose |
|-----------|---------|
| `UniverseManager` | Wraps `UniverseProvider` + custom universe CRUD + metadata |
| Universe DTOs | Pydantic schemas for universe metadata, stats, exposure |
| `UniverseService` | Service layer for web console (async, permission-aware) |
| `ExposureService` | Strategy exposure aggregation from positions |
| Market cap computation | `market_cap = abs(prc) * shrout` from CRSP daily data |
| Sector classification | Mock data v1 — no GICS/SIC in CRSP provider schema |
| Universe pages/components | NiceGUI pages for management, analytics, exposure |

### Key Limitations:

1. **Position-to-Strategy mapping is fail-closed:** `get_positions_for_strategies()` returns empty list when a symbol is traded by multiple strategies (line 3106-3122). T15.3 must handle this gracefully with mock fallback.
2. **No sector data in CRSP:** The CRSP daily schema is `[date, permno, cusip, ticker, ret, prc, vol, shrout]` — no GICS sector codes. T15.2 sector charts use mock data v1.
3. **Market cap derivable:** `market_cap = abs(prc) * shrout` can be computed from CRSP data (use `abs(prc)` because CRSP encodes bid/ask quotes as negative prices).
4. **ADV derivable:** 20-day average of `abs(prc) * vol` from CRSP daily data.

---

## Tasks (3 total)

### T15.3: Net Exposure by Strategy — HIGH PRIORITY

**Goal:** Show per-strategy risk exposure for multi-algo traders. This is the most operationally critical component — traders need real-time directional awareness.

**Data Source:** Real position data via `ExposureQueries.get_strategy_positions()` (new module in `libs/web_console_data/exposure_queries.py`). This adapter replicates the fail-closed symbol-to-strategy mapping SQL from `DatabaseClient.get_positions_for_strategies()` (database.py:3106) but lives in the `libs/` layer to avoid libs-to-apps circular dependency. It queries the positions table joined with orders via HAVING/ANY pattern, filtering to `qty != 0`. **Do NOT use `StrategyScopedDataAccess.get_positions()`** — it has a broken SQL query referencing a non-existent `strategy_id` column. When positions are empty (fail-closed or no live positions), display mock/placeholder data with "No live positions — showing example data" badge.

**Display:**
```
Strategy Exposure Dashboard:

Summary Cards:
+----------+ +----------+ +----------+ +----------+
| Net: +$80K| |Gross:$830K| |Long:$454K | |Short:$375K|
+----------+ +----------+ +----------+ +----------+

Strategy Grid:
Strategy         | Net ($)   | Gross ($) | Long ($) | Short ($) | Net %   | # Pos
---------------------------------------------------------------------------------
Momentum Alpha   | +$125,000 | $450,000  | $287,500 | $162,500  | +27.8%  | 12
Mean Reversion   | -$50,000  | $200,000  | $75,000  | $125,000  | -25.0%  | 8
Stat Arb         | +$5,000   | $180,000  | $92,500  | $87,500   | +2.8%   | 15
---------------------------------------------------------------------------------
TOTAL            | +$80,000  | $830,000  | $455,000 | $375,000  | +9.6%   | 35

[Stacked Bar Chart: Long vs Short by Strategy]

Warning: Net Long bias across strategies (+9.6% of gross)
```

**Formulas:**
- `long_notional = sum(qty * current_price)` for positions where `qty > 0`
- `short_notional = sum(abs(qty) * current_price)` for positions where `qty < 0`
- `gross_notional = long_notional + short_notional`
- `net_notional = long_notional - short_notional`
- `net_pct = net_notional / gross_notional * 100` (if `gross_notional > 0`, else `0.0`)
- **Directional bias warning threshold:** `abs(net_pct) > 10.0%` triggers amber warning; `abs(net_pct) > 25.0%` triggers red warning
- When `current_price` is `None`, fall back to `avg_entry_price` for notional computation (log warning)

**Architecture:**

```
exposure_page.py (NiceGUI)
    +-- @ui.page("/risk/exposure")
    +-- @requires_auth, @main_layout
    +-- Permission: VIEW_STRATEGY_EXPOSURE
    +-- Uses: ExposureService

ExposureService (libs/web_console_services/exposure_service.py)
    +-- get_strategy_exposure(user, db_pool) -> list[StrategyExposureDTO]
    +-- get_total_exposure(user, db_pool) -> TotalExposureDTO
    +-- Uses: ExposureQueries (libs/web_console_data/exposure_queries.py)

ExposureQueries (libs/web_console_data/exposure_queries.py) — NEW
    +-- get_strategy_positions(strategies, db_pool) -> ExposureQueryResult
        ExposureQueryResult = NamedTuple with:
          - positions: list[dict[str, Any]]  (not Position from apps/ — avoids layering violation)
          - excluded_symbol_count: int  (symbols traded by multiple strategies, excluded by fail-closed logic)
    +-- Implements the same fail-closed symbol-to-strategy mapping SQL as
        DatabaseClient.get_positions_for_strategies() (database.py:3106),
        but lives in libs/ to avoid libs->apps circular dependency.
        Queries positions table joined with orders table via HAVING/ANY
        pattern. Filters to qty != 0 (non-zero positions only).
        Additionally runs a companion COUNT query for ambiguous symbols
        (symbols with COUNT(DISTINCT strategy_id) > 1) to populate
        excluded_symbol_count for partial-data detection.
    NOTE: Do NOT use StrategyScopedDataAccess.get_positions() — it has a
    broken SQL query referencing a non-existent `strategy_id` column on
    the positions table. Strategy list comes from get_authorized_strategies(user).

DTOs (libs/web_console_services/schemas/exposure.py)
    +-- StrategyExposureDTO: strategy, long_notional, short_notional, gross_notional, net_notional, net_pct, position_count
    +-- TotalExposureDTO: long_total, short_total, gross_total, net_total, net_pct, strategy_count, bias_warning, is_placeholder (bool), is_partial (bool), data_quality_warning (str | None)
```

**Component: `render_exposure_chart`**
```
apps/web_console_ng/components/strategy_exposure.py
    +-- render_exposure_chart(exposures: list[StrategyExposureDTO]) -> None
    |   +-- Stacked bar chart: go.Figure with go.Bar traces for long (green) and short (red)
    +-- render_exposure_summary_cards(total: TotalExposureDTO) -> None
        +-- 4 metric cards: Net, Gross, Long, Short with color coding
```

**Mock Data Strategy:**
- When `ExposureQueries.get_strategy_positions()` returns empty list AND no excluded/unmapped positions exist:
  - Generate 3 mock strategies: "Momentum Alpha", "Mean Reversion", "Stat Arb"
  - Each with 2-3 mock positions with realistic notionals ($50K-$500K range)
  - Display badge text: "No live positions — showing example data" with distinctive amber styling (`bg-amber-100 text-amber-700`, same as data_source_status.py:124-126) to be visually distinct from real data states
  - Set `is_placeholder = True` flag in `TotalExposureDTO`
- When positions exist but cannot be attributed (fail-closed excludes all, or only unmapped remain):
  - Return empty exposures with `is_placeholder = False` and `is_partial = True`
  - Render explicit "Exposure Unavailable" card instead of misleading $0 totals
  - Data quality warning explains why (excluded symbols, unmapped positions)
- When real non-zero positions exist: compute from actual data — no mock mixing
- **Note:** `ExposureQueries.get_strategy_positions()` filters to `qty != 0`, so all-flat portfolios are indistinguishable from empty results. This is acceptable for v1 — a fully flat portfolio shows mock data with the badge, which is operationally safe (no active risk to monitor).
- **Partial data warning:** `is_partial = True` when `excluded_symbol_count > 0`, `unmapped_position_count > 0`, or `missing_price_count > 0`. Data quality warning lists all contributing factors.

**Acceptance Criteria:**
- [ ] Exposure page at `/risk/exposure` with permission gate (`VIEW_STRATEGY_EXPOSURE`)
- [ ] Summary cards for Net/Gross/Long/Short notional with color coding
- [ ] AG Grid table with per-strategy exposure breakdown (Net, Gross, Long, Short, Net%, # Positions)
- [ ] Total row across all strategies
- [ ] Stacked bar chart (Long green, Short red) per strategy using `go.Figure()`
- [ ] Directional bias warning: amber at >10%, red at >25% of gross
- [ ] Auto-refresh via `ui.timer(30.0, ...)` with `ClientLifecycleManager` cleanup
- [ ] Mock fallback with "No live positions — showing example data" badge when no live positions
- [ ] All-flat portfolio (qty=0 filtered out by ExposureQueries) falls through to mock data with badge — v1 limitation documented above
- [ ] TOTAL row net/gross/long/short equals arithmetic sum of per-strategy values
- [ ] When `ExposureQueries` raises `PermissionError`, page displays access-denied message rather than crashing
- [ ] `current_price` fallback to `avg_entry_price` with log warning
- [ ] Unit tests for `ExposureService` (>85% coverage): empty positions, single strategy, multi-strategy, bias thresholds

**Files:**
- Create: `libs/web_console_data/exposure_queries.py`
- Create: `libs/web_console_services/exposure_service.py`
- Create: `libs/web_console_services/schemas/exposure.py`
- Create: `apps/web_console_ng/pages/exposure.py`
- Create: `apps/web_console_ng/components/strategy_exposure.py`
- Create: `tests/libs/web_console_data/test_exposure_queries.py`
- Create: `tests/libs/web_console_services/test_exposure_service.py`
- Modify: `libs/platform/web_console_auth/permissions.py` (add `VIEW_STRATEGY_EXPOSURE`)
- Modify: `apps/web_console_ng/pages/__init__.py` (register page)
- Modify: `apps/web_console_ng/ui/layout.py` (add nav item — flat insertion into existing Risk section menu items, consistent with current nav structure; no nested sub-menus needed for v1)

**Estimated Effort:** 2-3 days

---

### T15.1: Universe Management / Selector — HIGH PRIORITY

**Goal:** Define and manage tradable universes dynamically. Users can browse built-in universes (SP500, R1000) backed by real CRSP data, and create custom universes with filter-based or manual symbol list definitions.

**Data Source:** Real CRSP constituent data via `UniverseProvider` for built-in universes. Custom universe definitions stored as JSON files in `data/universes/`. Market cap and ADV derived from CRSP daily data (`abs(prc) * shrout` and 20-day mean of `abs(prc) * vol`).

**Display:**
```
Universe Manager:
+-------------------------------------------------------------+
| [Category: All v] [Search: ___________]  [+ Create New]     |
+-------------------------------------------------------------+
| Name              | Type    | Symbols | Last Updated | Base  |
|-------------------|---------|---------|--------------|-------|
| SP500             | Built-in| 503     | 2026-01-10   | CRSP  |
| R1000             | Built-in| 1000    | 2026-01-10   | CRSP  |
| Liquid Large Cap  | Custom  | 287     | 2026-01-08   | SP500 |
| Tech Focus        | Custom  | 45      | 2026-01-05   | List  |
+-------------------------------------------------------------+

Universe Detail (SP500):
+-- Members: 503 symbols as of 2026-01-10
+-- Constituents Table: [ticker, market_cap, adv_20d]
+-- Source: CRSP index_constituents.parquet
+-- Last Rebalance: 2026-01-10

Universe Builder (Custom):
+-- Name: [_______________]
+-- Base Universe: [SP500 v] [R1000 v] [Manual List]
+-- Filters:
|   +-- Market Cap > $[___] B
|   +-- ADV 20d > $[___] M
|   +-- Exclude Symbols: [TSLA, ...]
+-- Preview: 287 symbols matching
+-- [Save Universe] [Cancel]
```

**Formulas (CRSP-derived metrics):**
- `market_cap = abs(prc) * shrout` — CRSP encodes bid/ask as negative `prc`; `shrout` is shares outstanding in thousands, so result is in $thousands
- `adv_20d = mean(abs(prc) * vol, last 20 trading days)` — `vol` is daily volume in shares. The 20-day lookback uses calendar days from the CRSP parquet file and gracefully handles fewer than 20 rows by averaging whatever is available. No error if a constituent has limited history.
- **`as_of_date` defaults:** When not supplied by the page, `as_of_date` defaults to the most recent date available in the CRSP parquet file (`index_constituents.parquet` max date). If `as_of_date` falls on a weekend/holiday with no CRSP data, use the most recent prior trading day.
- Market cap filter: `market_cap > threshold` (threshold in $thousands to match CRSP units). **UI input accepts human-readable units** ($B, $M) — the universe builder component converts to raw CRSP $thousands before calling `UniverseManager.apply_filters()`. Example: user enters "$5B" → component sends `5_000_000` ($thousands).
- ADV filter: `adv_20d > threshold` (threshold in $ notional). **UI input accepts $M** — component converts to raw $ notional. Example: user enters "$10M" → component sends `10_000_000`.

**Unit Convention:** All DTO fields store raw CRSP-derived values. `market_cap` is in **$thousands** (matching CRSP `shrout` units), `adv_20d` is in **$ notional**. Display conversion to human-readable units ($B, $M, $T) happens **only in the component render layer** (e.g., `market_cap / 1_000_000` for $B display). Filter thresholds use the same raw units as the DTO fields.

**Architecture:**

```
universes.py (NiceGUI page)
    +-- @ui.page("/research/universes")
    +-- @requires_auth, @main_layout
    +-- Permission: VIEW_UNIVERSES (browse), MANAGE_UNIVERSES (create/edit/delete)
    +-- Uses: UniverseService

UniverseService (libs/web_console_services/universe_service.py)
    +-- get_universe_list(user) -> list[UniverseListItemDTO]
    +-- get_universe_detail(user, universe_id, as_of_date) -> UniverseDetailDTO
    +-- preview_filter(user, base_universe, filters, as_of_date) -> int  # count only
    +-- create_custom_universe(user, definition) -> str  # returns universe_id
    +-- delete_custom_universe(user, universe_id) -> None
    +-- Uses: UniverseManager

UniverseManager (libs/data/universe_manager.py) — SYNC-ONLY
    +-- list_universes() -> list[UniverseMetadata]
    +-- get_constituents(universe_id, as_of_date) -> pl.DataFrame[permno, ticker, market_cap, adv_20d]
    +-- get_enriched_constituents(universe_id, as_of_date) -> pl.DataFrame  # with CRSP metrics
    +-- apply_filters(base_df, filters) -> pl.DataFrame
    +-- save_custom(name, definition) -> str
    +-- delete_custom(universe_id) -> None
    +-- Uses: UniverseProvider, CRSPLocalProvider
    NOTE: UniverseManager is synchronous (Polars I/O + CPU computation).
    UniverseService MUST wrap all calls with `asyncio.to_thread()` to avoid
    blocking the NiceGUI async event loop. Same pattern as feature_browser.py:176.
    PERFORMANCE: get_enriched_constituents() must use Polars lazy scan
    (`pl.scan_parquet()`) with predicate pushdown to avoid loading all years
    of CRSP data. Filter to `as_of_date` minus 30 calendar days (covers 20
    trading days with buffer) before collecting. Same pattern as
    UniverseProvider.get_constituents() (universe.py:99).

DTOs (libs/web_console_services/schemas/universe.py)
    +-- UniverseListItemDTO: id, name, type (built_in|custom), symbol_count (int | None — None when CRSP unavailable), last_updated, base
    +-- UniverseDetailDTO: id, name, type, constituents (list), statistics, filters_applied, unresolved_tickers (list[str] — tickers from manual list with no CRSP PERMNO match)
    +-- UniverseFilterDTO: field (market_cap|adv_20d), operator (gt|lt|gte|lte), value (float)
    +-- CustomUniverseDefinitionDTO: name, base_universe_id, filters (list[UniverseFilterDTO]), exclude_symbols (list[str])
    +-- UniverseConstituentDTO: permno, ticker, market_cap (float, unit: $thousands — raw CRSP value), adv_20d (float, unit: $ notional)
```

**Component: `render_universe_builder`**
```
apps/web_console_ng/components/universe_builder.py
    +-- render_universe_builder(on_save: Callable, on_cancel: Callable) -> None
    |   +-- Name input, base universe selector, filter rows
    |   +-- Real-time preview count via service.preview_filter()
    |   +-- Save/Cancel buttons
    +-- render_universe_filters(filters: list[UniverseFilterDTO]) -> None
        +-- Dynamic filter row builder with add/remove
```

**Custom Universe Persistence:**
- Storage: `data/universes/{universe_id}.json`
- Schema:
```json
{
  "id": "liquid_large_cap",
  "name": "Liquid Large Cap",
  "created_by": "operator-1",
  "created_at": "2026-01-08T10:00:00Z",
  "base_universe_id": "SP500",
  "filters": [
    {"field": "market_cap", "operator": "gt", "value": 5000000},
    {"field": "adv_20d", "operator": "gt", "value": 10000000}
  ],
  "exclude_symbols": ["TSLA"],
  "manual_symbols": null
}
```
- Manual list universes: `"manual_symbols": ["AAPL", "MSFT", ...]` with `"base_universe_id": null`
- **Ticker-to-PERMNO resolution for manual lists:** `UniverseManager.get_enriched_constituents()` resolves tickers to PERMNOs via CRSP daily data (most recent date with matching ticker). Unresolved tickers (no CRSP match) are **skipped with warning** — returned in a `unresolved_tickers: list[str]` field on `UniverseDetailDTO`. Ambiguous tickers (multiple PERMNOs) use the most recent active PERMNO. Tests must cover: valid ticker, missing ticker, ambiguous ticker, and mixed-valid lists.
- Universe IDs: slugified name (e.g., "Liquid Large Cap" -> "liquid_large_cap")
- File-based persistence avoids database migration; migration to Postgres in future if needed
- **Atomic write pattern:** `save_custom()` acquires the advisory lock on `data/universes/.lock`, then within that lock scope: (1) checks if `{universe_id}.json` already exists (raises `ConflictError` — a `ValueError` subclass defined in `universe_manager.py` — if duplicate), (2) writes to `{universe_id}.json.tmp`, (3) calls `os.rename()` atomically to the final path. The existence check, temp write, and rename all happen inside the same lock scope to prevent TOCTOU races.
- **File-system lock:** All write operations (save + delete) acquire an advisory lock on `data/universes/.lock` via `fcntl.flock()` to serialize concurrent modifications. Planned ADR: `ADR-NNNN-file-based-universe-persistence` will document the trade-offs of file-based vs. Postgres persistence and the migration path for v2.

**Security Invariants:**
- Universe file paths validated: no traversal beyond `data/universes/`
- Universe IDs alphanumeric + underscores only (regex: `^[a-z0-9_]{1,64}$`)
- Custom universe limit per user: 20 (prevent storage abuse). Limit check and file creation happen within a file-system advisory lock (`data/universes/.lock`) to prevent race conditions between concurrent creates.
- `created_by` field derived from authenticated user session (`get_user_id(user)`) — never from user-supplied input. Sanitized: alphanumeric + hyphens only, max 64 chars.
- Read operations require `VIEW_UNIVERSES`; write operations require `MANAGE_UNIVERSES`
- **Dataset-level CRSP access:** `UniverseService` read paths (detail, analytics) must call `has_dataset_permission(user, "crsp")` before accessing CRSP data. If denied, return a user-facing "CRSP data access denied" message rather than silently failing. This protects against future dataset licensing restrictions.

**Acceptance Criteria:**
- [ ] Universe list page at `/research/universes` with permission gate (`VIEW_UNIVERSES`)
- [ ] Built-in universes (SP500, R1000) listed with real constituent counts from CRSP
- [ ] Custom universes listed from `data/universes/*.json` files
- [ ] Universe detail view showing constituent table with `[ticker, market_cap, adv_20d]` via AG Grid
- [ ] Universe builder with name, base universe selector, filter rows (market_cap, adv_20d), exclude list
- [ ] Real-time preview count when filters change (debounced 500ms, backed by cached enriched constituents per `universe_id + as_of_date` with TTL matching `_CACHE_TTL_SECONDS = 1800`)
- [ ] Save custom universe to JSON file (requires `MANAGE_UNIVERSES`)
- [ ] Delete custom universe (requires `MANAGE_UNIVERSES`)
- [ ] Path traversal protection on universe file operations
- [ ] Creating a universe with a duplicate ID raises `ConflictError`
- [ ] When CRSP data is unavailable (`CRSPUnavailableError`), universe list shows built-in universes with `symbol_count=None` and "CRSP data unavailable" message; detail view shows error state rather than crashing
- [ ] Universe detail page does not block other UI interactions during CRSP data load (async via `asyncio.to_thread()`)
- [ ] ADV for a constituent with fewer than 20 days of history uses all available days without error
- [ ] Manual symbol list exceeding 5000 symbols rejected with descriptive error
- [ ] Dataset-level CRSP permission checked via `has_dataset_permission(user, "crsp")` before CRSP data access; denied users see "CRSP data access denied" message
- [ ] Unit tests for `UniverseManager` (>85% coverage): list, get constituents, apply filters, save/delete custom, path validation, duplicate ID conflict, CRSP unavailable fallback
- [ ] Unit tests for `UniverseService` (>85% coverage): permission checks, CRUD operations

**Files:**
- Create: `libs/data/universe_manager.py`
- Create: `libs/web_console_services/universe_service.py`
- Create: `libs/web_console_services/schemas/universe.py`
- Create: `apps/web_console_ng/pages/universes.py`
- Create: `apps/web_console_ng/components/universe_builder.py`
- Create: `tests/libs/data/test_universe_manager.py`
- Create: `tests/libs/web_console_services/test_universe_service.py`
- Modify: `libs/platform/web_console_auth/permissions.py` (add `VIEW_UNIVERSES`, `MANAGE_UNIVERSES`)
- Modify: `apps/web_console_ng/pages/__init__.py` (register page)
- Modify: `apps/web_console_ng/ui/layout.py` (add nav item — flat insertion into existing Research section menu items, consistent with current nav structure)

**Estimated Effort:** 3-4 days

---

### T15.2: Universe Analytics — MEDIUM PRIORITY

**Goal:** Analyze characteristics of trading universes. Provides distribution charts and summary statistics for any selected universe.

**Data Source:** Real CRSP-derived metrics for market cap and ADV distributions (computed by `UniverseManager.get_enriched_constituents()`). Sector distribution and factor exposures use mock data v1 — no GICS sector codes exist in the CRSP provider schema, and factor loadings require Barra model artifacts not available per-universe.

**Display:**
```
Universe Analytics: SP500 (503 symbols)

Summary Cards:
+-----------+ +-----------+ +-----------+ +-----------+
|Symbols:503| |Avg MCap:  | |Median ADV:| |Total MCap:|
|           | |$85.2B     | |$45.3M     | |$42.8T     |
+-----------+ +-----------+ +-----------+ +-----------+

[Market Cap Distribution Histogram]    [ADV Distribution Histogram]
[Sector Distribution Pie Chart*]       [Factor Exposure Bar Chart*]

* Mock data — real sector/factor data requires Compustat/Barra integration

Comparison Mode:
Universe A: [SP500 v]    vs    Universe B: [R1000 v]
+-------------+----------+----------+
| Metric      | SP500    | R1000    |
|-------------|----------|----------|
| # Symbols   | 503      | 1000     |
| Avg MCap    | $85.2B   | $42.1B   |
| Median ADV  | $45.3M   | $22.7M   |
| Total MCap  | $42.8T   | $42.1T   |
| Overlap     | 503/503  | 503/1000 |
+-------------+----------+----------+
```

**Formulas:**
- `avg_market_cap = mean(market_cap)` across all constituents
- `median_adv = median(adv_20d)` across all constituents
- `total_market_cap = sum(market_cap)` across all constituents
- `overlap_count = len(set(A_permnos) & set(B_permnos))`
- Market cap histogram: 10 bins, log-scale x-axis (market caps span orders of magnitude). **Service layer filters out zero and null values** before computing distribution arrays (log(0) = -inf breaks Plotly). `UniverseAnalyticsDTO.market_cap_distribution` contains only pre-filtered positive values. Service logs count of dropped constituents.
- ADV histogram: 10 bins, log-scale x-axis. **Service layer filters out zero and null values** before computing distribution arrays. `UniverseAnalyticsDTO.adv_distribution` contains only pre-filtered positive values. Service logs count of dropped constituents.
- Sector pie chart: mock data with 11 GICS sectors, proportional allocation

**Mock Sector Data (v1):**
```python
_MOCK_SECTOR_WEIGHTS = {
    "Information Technology": 0.28,
    "Health Care": 0.13,
    "Financials": 0.13,
    "Consumer Discretionary": 0.10,
    "Communication Services": 0.09,
    "Industrials": 0.08,
    "Consumer Staples": 0.06,
    "Energy": 0.04,
    "Utilities": 0.03,
    "Real Estate": 0.03,
    "Materials": 0.03,
}
```

**Architecture:**

```
universes.py (NiceGUI page — extends T15.1 page with analytics tab)
    +-- Analytics panel shown when universe selected
    +-- Uses: UniverseService.get_universe_analytics()

UniverseService (extend from T15.1)
    +-- get_universe_analytics(user, universe_id, as_of_date) -> UniverseAnalyticsDTO
    +-- compare_universes(user, universe_a, universe_b, as_of_date) -> UniverseComparisonDTO
    +-- Uses: UniverseManager.get_enriched_constituents()

DTOs (extend libs/web_console_services/schemas/universe.py)
    +-- UniverseAnalyticsDTO: universe_id, symbol_count, avg_market_cap, median_adv, total_market_cap, market_cap_distribution (list[float] — pre-filtered positive values), adv_distribution (list[float] — pre-filtered positive values), sector_distribution (dict[str, float]), is_sector_mock (bool), factor_exposure (dict[str, float] — mock factor loadings e.g. {"Market": 1.0, "Size": -0.3, "Value": 0.1, "Momentum": 0.4, "Volatility": -0.2}), is_factor_mock (bool)
    +-- UniverseComparisonDTO: universe_a_stats, universe_b_stats, overlap_count, overlap_pct
```

**Component: `render_universe_analytics`**
```
apps/web_console_ng/components/universe_analytics.py
    +-- render_universe_analytics(analytics: UniverseAnalyticsDTO) -> None
    |   +-- Summary cards: symbol count, avg market cap, median ADV, total market cap
    |   +-- Market cap histogram: go.Histogram with log-scale x-axis
    |   +-- ADV histogram: go.Histogram with log-scale x-axis
    |   +-- Sector pie chart: go.Pie with mock data (labeled as mock)
    |   +-- Factor exposure bar chart: go.Bar with mock data (labeled as mock)
    +-- render_universe_comparison(comparison: UniverseComparisonDTO) -> None
        +-- Side-by-side statistics table with overlap metrics
```

**Acceptance Criteria:**
- [ ] Analytics panel visible when a universe is selected on the universe page
- [ ] Summary cards: symbol count, avg market cap ($B), median ADV ($M), total market cap ($T)
- [ ] Market cap distribution histogram (log-scale x-axis, 10 bins)
- [ ] ADV distribution histogram (log-scale x-axis, 10 bins)
- [ ] Sector distribution pie chart with mock weights (labeled "Mock Data")
- [ ] Universe comparison: select two universes, show side-by-side stats + overlap
- [ ] Overlap metric: count and percentage of shared constituents
- [ ] "Mock Data" badge on sector/factor charts (same pattern as `data_source_status.py:124-126`)
- [ ] Unit tests for analytics calculations in service layer (>85% coverage): summary stats, comparison overlap, zero-value filtering
- [ ] Component render tests: renders without error, histogram handles zero values, comparison table

**Files:**
- Create: `apps/web_console_ng/components/universe_analytics.py`
- Create: `tests/apps/web_console_ng/components/test_universe_analytics.py`
- Modify: `libs/web_console_services/universe_service.py` (add analytics/comparison methods)
- Modify: `libs/web_console_services/schemas/universe.py` (add analytics/comparison DTOs)
- Modify: `apps/web_console_ng/pages/universes.py` (add analytics tab/panel)

**Estimated Effort:** 2-3 days

---

## Dependencies

```
T15.3 Net Exposure ---------------------------> Dashboard (standalone)
                                                 Can be deployed independently

T15.1 Universe Manager ---> T15.2 Universe Analytics
     |                         |
     |                         +-- Depends on UniverseManager.get_enriched_constituents()
     |
     +-- Future: Backtest Config universe selection (out of scope for T15)
```

**Cross-task shared files (commit sequentially):**
- `apps/web_console_ng/pages/__init__.py` — page imports
- `apps/web_console_ng/ui/layout.py` — nav menu items
- `libs/platform/web_console_auth/permissions.py` — new permissions

---

## Security Model

### Permission Gates
| Action | Permission Required | Roles |
|--------|-------------------|-------|
| View universe list | `VIEW_UNIVERSES` | Researcher, Operator, Admin |
| View universe detail/analytics | `VIEW_UNIVERSES` | Researcher, Operator, Admin |
| Create/edit/delete custom universe | `MANAGE_UNIVERSES` | Operator, Admin |
| View strategy exposure | `VIEW_STRATEGY_EXPOSURE` | Researcher, Operator, Admin |

### File System Safety (T15.1)
- Universe JSON files stored under `data/universes/` only
- Universe ID validation: `^[a-z0-9_]{1,64}$` (no path separators, no special chars)
- Path resolution via `Path(data_dir / "universes" / f"{universe_id}.json").resolve()` must be under `data/universes/`
- Maximum 20 custom universes per user (prevent storage abuse)
- Maximum 5000 symbols in manual list (prevent memory abuse)

### Exposure Data Access (T15.3)
- Strategy list from `get_authorized_strategies(user)` → passed to `ExposureQueries.get_strategy_positions()`
- Users only see strategies they are authorized for via `get_authorized_strategies()`
- No raw position data exposure — only aggregated notional values per strategy
- **Known pre-existing issue:** `get_authorized_strategies()` in `permissions.py` has redundant logic (returns `strategies_list` regardless of `VIEW_ALL_STRATEGIES` check). Not a security hole (relies on provided user object's strategies), but should be cleaned up as a separate fix outside T15 scope.

---

## Testing Strategy

### Unit Tests

**T15.3 Exposure Service Tests (`tests/libs/web_console_services/test_exposure_service.py`):**
- Empty positions -> returns mock exposure list with `is_placeholder=True` (service owns fallback generation)
- Single strategy, all long -> correct long/gross/net, net_pct = 100%
- Single strategy, all short -> correct short/gross/net, net_pct = -100%
- Multi-strategy -> correct per-strategy breakdown and total
- Missing `current_price` -> falls back to `avg_entry_price`
- Zero gross notional -> net_pct = 0.0 (no division by zero)
- Bias warning thresholds: 10% amber, 25% red, <10% none
- Permission denied for viewer role
- Mock fallback when positions empty
- TOTAL row equals arithmetic sum of per-strategy values
- PermissionError from ExposureQueries handled gracefully
- All-flat portfolio (qty=0) falls through to mock data (v1 limitation — ExposureQueries filters qty != 0)
- `is_partial=True` when `excluded_symbol_count > 0` (multi-strategy ambiguous symbols)
- `data_quality_warning` populated with excluded count when partial

**T15.1 Universe Manager Tests (`tests/libs/data/test_universe_manager.py`):**
- List universes returns built-in + custom
- Get constituents for SP500 returns non-empty DataFrame
- Apply market_cap filter reduces count
- Apply adv_20d filter reduces count
- Apply exclude_symbols filter removes specified symbols
- Save custom universe creates JSON file
- Delete custom universe removes JSON file
- Path traversal rejected (e.g., `../../etc/passwd`)
- Invalid universe ID format rejected
- Custom universe limit enforced (>20 -> error)
- Manual list universe with explicit symbols
- Manual list with unresolved ticker → skipped, returned in `unresolved_tickers`
- Manual list with ambiguous ticker → most recent active PERMNO used
- Manual list with all invalid tickers → empty constituents + all in `unresolved_tickers`
- Manual list exceeding 5000 symbols → rejected with ValueError

**T15.2 Analytics Calculation Tests (`tests/libs/web_console_services/test_universe_service.py` — extend from T15.1):**
- Summary statistics computed correctly (avg, median, total)
- Comparison overlap: identical universes -> 100% overlap
- Comparison overlap: disjoint universes -> 0% overlap
- Mock sector weights sum to 1.0
- Mock factor exposure dict has expected keys and `is_factor_mock=True`
- Zero/null market_cap values filtered before histogram computation (service layer)

**T15.2 Component Render Tests (`tests/apps/web_console_ng/components/test_universe_analytics.py`):**
- Renders without error when valid `UniverseAnalyticsDTO` provided
- Histogram with zero-value constituent does not raise error
- Comparison table renders with overlap metrics

### Integration Tests
- Universe service + manager integration (service calls manager correctly)
- Exposure service + ExposureQueries integration (verify ExposureQueries adapter produces same results as DatabaseClient.get_positions_for_strategies() for identical inputs)

### E2E Tests
- Universe list page loads and displays built-in universes
- Exposure dashboard shows summary cards and grid
- Universe builder: create, preview, save, verify in list

---

## Definition of Done

- [ ] All 3 tasks implemented and functional
- [ ] T15.3: Exposure dashboard at `/risk/exposure` with real/mock data
- [ ] T15.1: Universe management at `/research/universes` with CRUD for custom universes
- [ ] T15.2: Universe analytics with distributions and comparison
- [ ] Permissions registered and enforced (`VIEW_UNIVERSES`, `MANAGE_UNIVERSES`, `VIEW_STRATEGY_EXPOSURE`)
- [ ] Pages registered in `apps/web_console_ng/pages/__init__.py`
- [ ] Nav items added to `apps/web_console_ng/ui/layout.py`
- [ ] Mock data clearly labeled: T15.3 uses "No live positions — showing example data" badge; T15.2 uses "Mock Data" badge on sector/factor charts
- [ ] Unit tests > 85% coverage for all new modules
- [ ] All existing tests pass (`make test`)
- [ ] `make ci-local` passes
- [ ] Code reviewed and approved via shared-context iteration (Gemini + Codex)
- [ ] No security vulnerabilities (path traversal, permission bypass, division by zero)

---

**Last Updated:** 2026-02-26
**Status:** TASK
