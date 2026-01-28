---
id: P6T5
title: "Professional Trading Terminal - Grid Enhancements"
phase: P6
task: T5
priority: P1
owner: "@development-team"
state: PLANNING
created: 2026-01-13
updated: 2026-01-27
dependencies: [P6T1]
related_adrs: [ADR-0031-nicegui-migration, ADR-0015-twap-order-slicer]
pending_adrs: [ADR-0032-grid-enhancements-architecture]
related_docs: [P6_PLANNING.md]
features: [T5.1-T5.4]
---

# P6T5: Grid Enhancements

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** PLANNING
**Priority:** P1 (Enhanced UX)
**Owner:** @development-team
**Created:** 2026-01-13
**Updated:** 2026-01-27
**Track:** Track 5 of 18
**Dependency:** P6T1 (Core Infrastructure - throttling required)

---

## Objective

Enhance AG Grid components with hierarchical orders, tabbed panels, DOM/Level 2 view, and position sparklines.

**Success looks like:**
- Hierarchical order blotter showing parent/child relationships
- Tabbed positions/orders panel
- DOM ladder for market depth
- Position sparklines showing trend

---

## Implementation Priority Order

Based on dependency analysis and incremental value delivery:

1. **T5.2: Tabbed Positions/Orders Panel** (MEDIUM complexity, HIGH foundational value)
   - Foundation for other components
   - Uses existing GridStateManager for persistence
   - **Backend work required:** New `/api/v1/fills` endpoint + extend `/api/v1/orders` with pagination/filters

2. **T5.1: Hierarchical Order Blotter** (MEDIUM complexity)
   - Extends existing orders_table.py
   - Leverages existing TWAP parent/child data structure
   - Requires AG Grid Tree Data feature

3. **T5.4: Position Sparklines** (MEDIUM complexity)
   - Extends positions_grid.py
   - Requires P&L history tracking (new data source)
   - Uses lightweight-charts or inline SVG

4. **T5.3: DOM/Level 2 Order Book** (HIGH complexity)
   - Uses Alpaca Pro Level 2 data (available)
   - WebSocket streaming for real-time updates
   - New infrastructure for depth data handling

---

## Detailed Implementation Plan

### T5.1: Hierarchical Order Blotter

**Goal:** View parent/child orders in expandable hierarchy.

**Technical Design:**

AG Grid supports hierarchical data via Tree Data feature. The existing database schema already has `parent_order_id`, `slice_num`, and `total_slices` columns (see `db/migrations/0001_extend_orders_for_slicing.sql`).

**Data Structure (from existing schemas.py):**
```python
# Parent order: parent_order_id=NULL, total_slices=N
# Child slices: parent_order_id=<parent_id>, slice_num=0..N-1
```

**Key Components:**

1. **HierarchicalOrdersTransformer** (new module)
   - Transform flat orders list into tree structure
   - Group child orders under parent
   - Compute aggregate metrics (filled_qty/total_qty for parent)
   - Handle orphan children gracefully (parent not in snapshot)

2. **AG Grid Tree Data Configuration**
   ```javascript
   {
     treeData: true,
     getDataPath: params => params.data.hierarchy_path,  // e.g., ['parent_id'] or ['parent_id', 'child_id']
     groupDefaultExpanded: 1,  // Expand first level by default
     autoGroupColumnDef: {
       headerName: 'Symbol',
       cellRendererParams: { suppressCount: true }
     }
   }
   ```

3. **Progress Column** for parent orders
   - Shows "400/1000 filled" format
   - Color-coded progress bar (optional)
   - Updates in real-time as children fill

4. **Cancel Parent Logic**
   - Cancel all pending children when parent is cancelled
   - Backend already supports this via parent_order_id lookup
   - UI shows confirmation dialog listing affected children
   - **Edge Cases:**
     - Partially filled children: Cancel remaining qty, show partial fill status
     - Already filled children: Skip (cannot cancel), show in dialog as "filled"
     - Already cancelled children: Skip, no action needed
     - Mixed state: Dialog shows breakdown (e.g., "2 pending, 1 partial, 3 filled")

5. **Expansion State Persistence**
   - Store expanded parent IDs in workspace preferences (per-user scoped)
   - Key: `hierarchical_orders:{user_id}:expanded_ids: string[]`
   - Restore on page load/reconnect
   - Pruning: Remove IDs from storage when parent order disappears from snapshot (filled/cancelled)
   - Max stored: 100 IDs (prevent unbounded growth for active traders)

**Files:**
- Create: `apps/web_console_ng/components/hierarchical_orders.py`
  - `transform_to_hierarchy(orders: list[dict]) -> list[dict]`
  - `compute_parent_aggregates(parent: dict, children: list[dict]) -> dict`
- Modify: `apps/web_console_ng/components/orders_table.py`
  - Add `create_hierarchical_orders_table()` function
  - Optional: Feature flag to switch between flat/hierarchical view

**Acceptance Criteria:**
- [ ] TWAP parent orders show expandable children
- [ ] Aggregate progress for parent orders (e.g., "400/1000 filled")
- [ ] Cancel parent cancels all pending children (filled/cancelled children skipped)
- [ ] Cancel dialog shows breakdown of child states before confirmation
- [ ] Tree-style expandable rows with smooth animation
- [ ] Updates throttled via P6T1 infrastructure
- [ ] Orphan children (parent not in snapshot) shown as flat rows with visual indicator (dashed border/icon)
- [ ] Expansion state persisted via workspace preferences
- [ ] Expansion state restored on page load/reconnect

**Testing:**
- Unit: Hierarchy transformation, aggregate calculation, expansion state serialization
- Integration: Parent-child cancel cascade (including partial fill edge cases)
- E2E: Expand/collapse, real-time updates, expansion persistence across refresh

---

### T5.2: Tabbed Positions/Orders Panel

**Goal:** Consolidate bottom panel with tabs.

**Technical Design:**

NiceGUI provides `ui.tabs()` component. Tab state will persist via GridStateManager (already supports arbitrary key-value persistence).

**Tab Structure:**
```
[Positions] [Working] [Fills] [History]
   (3)        (5)       (12)
```
- Badge counts show pending items
- Cross-tab symbol filter syncs across all tabs

**Key Components:**

1. **TabbedPanel Component**
   - Manages tab state (active tab, filter state)
   - Lazy loads tab content (only render when selected)
   - Persists active tab via workspace preferences

2. **Symbol Filter Bar**
   - Dropdown or autocomplete for symbol selection
   - "All" option to clear filter
   - Filter persists across tab switches
   - Syncs with watchlist selection (if applicable)

3. **Tab Content & Data Contracts:**
   - **Positions:** Existing `positions_grid.py` (unchanged)
     - Data source: `GET /api/v1/positions` (existing endpoint)
   - **Working:** Filter orders_table to `status IN ('new', 'pending_new', 'partially_filled')`
     - Data source: `GET /api/v1/orders?status=open` (existing endpoint)
   - **Fills:** Execution-level fill events (NOT order status)
     - Data source: `GET /api/v1/fills?from_date={today_utc}&limit=100&cursor={fill_time,pk}` (NEW endpoint)
     - Row granularity: One row per fill event (partial fills = multiple rows)
     - Columns: symbol, side, qty, price, fill_time, order_id
     - Backend: **TBD by ADR-0032** - use existing `trades` table OR create new fills table
     - Sorting: **TBD by ADR-0032** - must be stable and deterministic
     - Cursor pagination: prevents gaps under concurrent inserts
   - **History:** All orders with date range filter
     - Data source: `GET /api/v1/orders?from_date=X&to_date=Y&limit=100&cursor={created_at,id}`
     - Backend: Add cursor-based pagination and date range to orders endpoint
     - Sorting: `created_at DESC, id DESC` (stable - id is auto-increment PK)
     - Date boundaries: Half-open interval `[from_date 00:00:00 UTC, to_date+1 00:00:00 UTC)`
     - Cursor pagination: `cursor=2026-01-27T14:30:00Z,12345` (last seen created_at,id)
     - Default: Last 30 days, max 90 days, paginated (100 per page)
     - UI: Date picker + "Load more" button (passes cursor from last response)

4. **Badge Count Logic:**
   - Positions: Count of open positions (from existing snapshot)
   - Working: Count of open orders (from existing snapshot)
   - Fills: Count of fill events today (UTC day boundary, from fills endpoint total_count)
   - History: No badge (static)
   - Update cadence: Real-time for Positions/Working (via existing subscriptions), on-tab-switch for Fills

**Backend Changes Required:**
- NEW: `GET /api/v1/fills` endpoint with `from_date`, `to_date`, `limit`, `cursor`, `symbol` params
- Extend `GET /api/v1/orders` with: `from_date`, `to_date`, `limit`, `cursor`
- Cursor-based pagination: Return `next_cursor` in response
- Both endpoints return `total_count` for badge display (cached count, refreshed every 60s)
- Date filtering uses half-open intervals: `[from, to+1day)` for consistent UTC handling

**Cursor Format Specification:**
- Encoding: `{ISO8601_timestamp},{row_identifier}` (e.g., `2026-01-27T14:30:00.123Z,12345` or `2026-01-27T14:30:00.123Z,abc123`)
- Timestamp: RFC3339 with optional fractional seconds, always UTC (Z suffix). Normalize to microseconds on parse.
- Row identifier: **TBD by ADR-0032** (options: surrogate bigint PK, or text PK like `client_order_id`/`trade_id`)
- **Ordering Predicate (DESC):** **TBD by ADR-0032** - must ensure stable, deterministic ordering
  - Example with bigint: `WHERE (created_at, id) < (cursor_ts, cursor_id)`
  - Example with text PK: `WHERE (created_at, client_order_id) < (cursor_ts, cursor_id)` (requires deterministic text ordering)
- **Initial/Null Cursor:** Omit `cursor` param or pass empty string; returns first page from most recent
- Validation: For malformed cursors, return first page with `X-Cursor-Reset: true` (UX-friendly recovery)
- **Invalid Cursor (Server):** Return first page with header `X-Cursor-Reset: true` when:
  - Cursor format invalid (malformed timestamp or non-integer ID)
  - Cursor timestamp is outside current `from_date`/`to_date` filter range
- **Client Cursor Rules:**
  - MUST drop stored cursor when `X-Cursor-Reset: true` header is present
  - MUST clear cursor on ANY filter/date/symbol change (before server responds)
  - Should notify user: "Showing latest results" when reset occurs

**total_count Caching:**
- Cache key: `count:{endpoint}:{user_id}:{from_date}:{to_date}:{symbol}` (filter-scoped)
- Refresh: Every 60 seconds or on cache miss
- Response: Include `"total_count_approximate": true` if from cache older than 5 seconds
- Badge use: Acceptable for badge display; exact count not critical for UX

**Auth/Tenant Scoping:**
- All fills/history queries MUST be scoped to authenticated user's account
- Endpoint does NOT accept arbitrary `user_id` or `account_id` params
- Account derived from auth token (JWT claim or session)

**Pre-Implementation Requirements:**
- [ ] **ADR-0032** must be created and approved before implementation begins
  - Scope: New `/api/v1/fills` endpoint, Level2WebSocket service, Redis pub/sub channels
  - Content: Data contracts, service ownership, tenancy model, scaling considerations
  - **Data Model Decisions Required:**
    - Cursor pagination PK: Add surrogate `bigint id` columns OR use `(timestamp, text_pk)` with deterministic tie-breaker
    - Fills data source: Use existing `trades` table OR create new fills table
    - Index requirements for pagination: Define composite indexes for cursor predicates
    - Tenancy model: Single-account shared deployment OR per-user account scoping (affects column requirements)
  - Owner: Tech lead to author before T5.2/T5.3 implementation starts
- [ ] **Spike/POC** for Alpaca L2 WebSocket to verify snapshot vs delta semantics before T5.3 implementation

**Files:**
- Create: `apps/web_console_ng/components/tabbed_panel.py`
  - `create_tabbed_panel(positions_grid, orders_grid, fills_grid, history_grid)`
  - `TabbedPanelState` class for state management
- Create: `apps/web_console_ng/components/symbol_filter.py`
  - Reusable filter component
- Modify: `apps/web_console_ng/core/workspace_persistence.py`
  - Add `tabbed_panel_state` to valid workspace keys

**Acceptance Criteria:**
- [ ] Tabbed panel (Positions | Working | Fills | History)
- [ ] Remember selected tab across sessions (via P6T1.4)
- [ ] Badge counts for pending orders (real-time for Working, on-switch for Fills)
- [ ] Cross-tab filtering by symbol
- [ ] Lazy loading for non-active tabs
- [ ] Fills tab shows execution-level fills (partial fills = separate rows)
- [ ] History tab pagination with stable ordering (no duplicates/gaps on page)
- [ ] Date range filters use half-open UTC intervals `[from 00:00, to+1 00:00)`

**Testing:**
- Unit: Tab state persistence, badge count calculation
- Integration: Tab switch preserves filter, workspace restore
- E2E: Tab interaction, filter application

---

### T5.3: DOM/Level 2 Order Book

**Goal:** Display market depth for informed execution decisions.

**Data Source:** Alpaca Pro (confirmed available)

**Technical Design:**

DOM (Depth of Market) ladder displays bid/ask depth at multiple price levels using Alpaca Pro's Level 2 market data via WebSocket streaming.

**Alpaca Pro Level 2 Integration:**

1. **WebSocket Connection & Entitlements**
   - Endpoint: `wss://stream.data.alpaca.markets/v2/sip` (SIP feed - requires Alpaca Pro)
   - Authentication: API key/secret in connection params
   - **Entitlement Check:** Attempt SIP WebSocket connection; on auth error (code 401/403), show "Pro required"
     - Note: Alpaca doesn't expose entitlements via REST; detect via connection attempt
     - IEX feed does NOT provide Level 2 orderbook data (only quotes)
   - **Fallback:** If SIP connection rejected, show "Level 2 requires Alpaca Pro subscription" and hide DOM ladder
   - Subscribe: `{"action": "subscribe", "quotes": ["AAPL"], "orderbooks": ["AAPL"]}`
   - Max symbols: 30 concurrent subscriptions **per WebSocket connection** (Alpaca limit)

2. **Message Types & Semantics (per Alpaca docs):**
   - `o` (orderbook): **Full snapshot** on each update (not delta) - **VERIFY during implementation**
   - `q` (quote): Top-of-book quote (bid/ask)
   - No sequence numbers needed since each `o` message is a complete snapshot
   - Updates arrive ~100ms intervals during market hours
   - **Implementation Note:** If Alpaca emits deltas instead of snapshots, implement stateful merge with sequence validation and reset on gaps. Verify actual behavior during spike/prototype phase.

3. **Data Structure (Alpaca orderbook message):**
   ```json
   {
     "T": "o",
     "S": "AAPL",
     "t": "2024-01-15T14:30:00.123Z",
     "b": [{"p": 185.50, "s": 100}, {"p": 185.49, "s": 250}, ...],  // bids (best first)
     "a": [{"p": 185.51, "s": 150}, {"p": 185.52, "s": 300}, ...]   // asks (best first)
   }
   ```
   - Bids sorted descending (highest first), Asks sorted ascending (lowest first)
   - Size is aggregate at that price level

4. **Per-User Channel Isolation**
   - Redis pub/sub channel: `l2:{user_id}:{symbol}` (user-scoped to prevent cross-account)
   - Each user session subscribes to their own channel
   - WebSocket service tags messages with user_id before broadcast

5. **DOM Ladder Component**
   - Vertical ladder layout (price in center, bid qty left, ask qty right)
   - 10 levels each side (configurable)
   - Current price highlighted in center
   - Color gradient for size (larger = more intense)
   - Large order highlighting: >2x rolling average size

6. **Price Click Integration**
   - Click bid price → pre-fill order form with SELL at that price
   - Click ask price → pre-fill order form with BUY at that price
   - Emit event for order_entry_context.py to consume

**Key Components:**

1. **Level2WebSocketService** (new)
   - Manages Alpaca WebSocket connection (ONE connection per app instance)
   - **Tenancy Model:** **TBD by ADR-0032**
     - Option A: Single Alpaca account per deployment (all users share credentials)
     - Option B: Per-user Alpaca accounts (requires connection pool)
     - Per-user Redis channels provide UI session isolation
     - Final decision affects WebSocket architecture and data scoping
   - Handles reconnection with exponential backoff
   - Broadcasts updates via Redis pub/sub (for multi-browser-session support)
   - **Subscription Management:**
     - Global ref-counting: track `{symbol: count}` of active browser sessions
     - Subscribe when first session for a symbol connects
     - Unsubscribe when last session for a symbol disconnects
     - Global cap: max 30 symbols across ALL sessions (Alpaca per-connection limit)
     - Rejection: If at cap, new subscription returns error "max symbols reached"

2. **DOMDataProcessor**
   - Aggregates orderbook updates
   - Computes rolling average for large order detection
   - Throttles UI updates to 30fps (via P6T1 throttling)

3. **DOMLadder Component**
   - AG Grid or custom HTML table for ladder display
   - CSS animations for level changes
   - Click handlers for price selection

**Files:**
- Create: `apps/web_console_ng/components/dom_ladder.py`
  - `create_dom_ladder(symbol: str)` - Creates ladder UI
  - `DOMLadderState` - Manages subscription and data
- Create: `apps/web_console_ng/components/depth_visualizer.py`
  - Size bar rendering, large order highlighting
- Create: `apps/web_console_ng/core/level2_websocket.py`
  - `Level2WebSocketService` - Alpaca WS connection
  - `Level2DataProcessor` - Data aggregation
- Create: `apps/web_console_ng/static/js/dom_ladder.js`
  - Client-side rendering and interactions

**Acceptance Criteria:**
- [ ] DOM ladder displays bid/ask depth (10 levels each side)
- [ ] Click price to pre-fill order form (integrates with P6T4 order entry)
- [ ] Large orders highlighted (>2x rolling average size)
- [ ] Real-time updates throttled to 30fps via P6T1 infrastructure
- [ ] Graceful fallback when Level 2 data unavailable (show "Connecting..." or error)
- [ ] Entitlement check on load: show "Alpaca Pro required" if no SIP access
- [ ] Reconnection handling (auto-reconnect on disconnect with exponential backoff)
- [ ] Symbol switching (unsubscribe old, subscribe new)
- [ ] Max 30 symbol subscriptions enforced (Alpaca limit)
- [ ] Per-user channel isolation (no cross-account data leakage)

**Testing:**
- Unit: Data aggregation, large order detection, throttling
- Integration: WebSocket connection, Redis pub/sub broadcast
- E2E: Ladder rendering, price click → order form prefill

---

### T5.4: Position Sparklines

**Goal:** Mini trend charts in position table rows.

**Technical Design:**

Sparklines will show P&L history over the last hour (60 data points, 1-minute intervals). Two implementation options:

**Option A: Inline SVG (Recommended)**
- Lightweight, no external library
- Pure Python generation via `sparkline_renderer.py`
- CSS for coloring (green up, red down)
- ~300 bytes per sparkline

**Option B: Lightweight Charts Cell Renderer**
- Use existing TradingView library
- More feature-rich but heavier
- May have performance issues with many rows

**Data Storage:**

P&L history needs to be tracked. Selected approach: **Redis Sorted Set** (ZSET)

**Storage Design:**
- Key: `pnl_history:{user_id}:{symbol}` (scoped per user to prevent cross-account bleed)
- Structure: `ZADD pnl_history:user123:AAPL {unix_timestamp} "{unix_timestamp}:{pnl_value}"`
  - Score: unix timestamp (for ordering and range queries)
  - Member: `{timestamp}:{value}` (unique per sample, prevents collision on repeated values)
- Retention: 60 data points (1 hour at 1-minute intervals)
- Cleanup: Trim only if count exceeds 60 - `if ZCARD > 60 then ZREMRANGEBYRANK key 0 (ZCARD-60-1)`
  - Safe trim: Remove oldest entries beyond the 60-point window
  - Alternative: `ZREMRANGEBYRANK key 0 -62` only when `ZCARD > 61` (guard prevents deleting all)
- TTL: 2 hours (auto-expire if no updates)
- Read: `ZRANGE key 0 -1 WITHSCORES` returns all points in chronological order

**Collection Service:**
- Owner: `SparklineDataService` class in `apps/web_console_ng/core/sparkline_service.py`
- Trigger: Runs on each position update callback (not separate timer)
- Rate limiting: At most once per minute per symbol (dedupe via last_collect timestamp)
- Failure handling: Log warning, continue (sparkline shows stale data, not crash)
- Backfill: Not required (sparkline fills as data arrives)

**Alternative Considered:**
- In-memory dict per session: Simpler but lost on refresh, not suitable for persistent history

**Key Components:**

1. **SparklineDataService** (in `core/sparkline_service.py`)
   - Collects P&L snapshots on each position update (rate-limited to 1/min per symbol)
   - Maintains rolling 60-point history per symbol in Redis
   - Provides data for sparkline rendering via `get_sparkline_data(user_id, symbol)`

2. **SparklineRenderer (AG Grid Cell Renderer)**
   ```javascript
   // Cell renderer that creates SVG sparkline
   window.sparklineRenderer = (params) => {
     const data = params.data.pnl_history || [];
     return createSparklineSVG(data, params.data.unrealized_pl);
   };
   ```

3. **Sparkline SVG Generator (Python)**
   ```python
   def create_sparkline_svg(
       data: list[float],
       width: int = 80,
       height: int = 20,
       trend_color: str = "green" | "red"
   ) -> str:
       # Returns SVG string for inline rendering
   ```

**Files:**
- Create: `apps/web_console_ng/core/sparkline_service.py`
  - `SparklineDataService` class (data collection and Redis storage)
- Create: `apps/web_console_ng/components/sparkline_renderer.py`
  - `create_sparkline_svg()` function (SVG generation)
- Create: `apps/web_console_ng/static/js/sparkline.js`
  - AG Grid cell renderer
  - SVG generation utilities
- Modify: `apps/web_console_ng/components/positions_grid.py`
  - Add sparkline column
  - Integrate data service

**Acceptance Criteria:**
- [ ] Sparkline visible in position row
- [ ] Shows last 1 hour of P&L (60 data points)
- [ ] Updates in real-time (throttled via P6T1)
- [ ] Color indicates trend direction (green up, red down)
- [ ] Graceful degradation with insufficient data points

**Testing:**
- Unit: SVG generation, data service
- Integration: Real-time updates, grid integration
- E2E: Visual verification, performance with 50+ positions

---

## Dependencies

```
P6T1.1 Throttling ──> T5.1 Hierarchical Blotter
                  ──> T5.3 DOM/Level 2
                  ──> T5.4 Sparklines

P6T1.4 Workspace ──> T5.2 Tabbed Panel (remember tab)
                 ──> T5.1 Hierarchical (remember expansion state)

P6T4 Order Entry ──> T5.3 DOM/Level 2 (price click prefills order form)

T5.2 Tabbed Panel ──> Provides container for T5.1, T5.4
```

---

## File Structure

```
apps/web_console_ng/
├── components/
│   ├── hierarchical_orders.py     # T5.1 - Tree data transformation
│   ├── tabbed_panel.py            # T5.2 - Tabbed container
│   ├── symbol_filter.py           # T5.2 - Reusable filter
│   ├── dom_ladder.py              # T5.3 - DOM ladder component
│   ├── depth_visualizer.py        # T5.3 - Depth bar rendering
│   ├── sparkline_renderer.py      # T5.4 - Sparkline SVG generation + AG Grid cell renderer
│   ├── orders_table.py            # Modified for T5.1
│   └── positions_grid.py          # Modified for T5.4
├── static/js/
│   ├── sparkline.js               # T5.4 - Cell renderer
│   ├── dom_ladder.js              # T5.3 - Ladder interactions
│   └── hierarchical_grid.js       # T5.1 - Tree data support
└── core/
    ├── level2_websocket.py        # T5.3 - Alpaca L2 WebSocket
    ├── sparkline_service.py       # T5.4 - SparklineDataService (P&L history)
    └── workspace_persistence.py   # Modified for T5.2

tests/apps/web_console_ng/
├── components/
│   ├── test_hierarchical_orders.py
│   ├── test_tabbed_panel.py
│   ├── test_symbol_filter.py
│   ├── test_dom_ladder.py
│   └── test_sparkline_renderer.py
├── core/
│   ├── test_level2_websocket.py
│   └── test_sparkline_service.py
```

---

## Testing Strategy

### Unit Tests
- Hierarchical order grouping and aggregate calculation
- Sparkline SVG generation with edge cases (empty data, single point)
- Tab state persistence serialization
- Symbol filter matching logic

### Integration Tests
- Parent/child order relationship with real database
- Tab state persistence with workspace service
- Sparkline data collection over time

### E2E Tests
- Hierarchical blotter expand/collapse interaction
- Tab switching and state preservation
- Sparkline visual rendering (screenshot comparison)

### Performance Tests
- Hierarchical grid with 100+ orders (50 parents, 10 children each)
- Sparklines with 50+ positions
- Tab switching latency

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| AG Grid Tree Data performance | Medium | Medium | Lazy load children, limit depth |
| Sparkline memory usage | Low | Medium | Rolling window (60 points), Redis TTL cleanup |
| Level 2 WebSocket disconnection | Medium | Medium | Auto-reconnect with exponential backoff, UI indicator |
| Level 2 data rate overwhelming UI | Medium | High | Throttle to 30fps via P6T1, batch updates |
| Tab state sync issues | Low | Low | Debounce saves, conflict resolution |
| History tab data volume | Medium | Medium | Pagination, date range limits (max 90 days) |
| Cross-account sparkline data bleed | Low | High | User-scoped Redis keys: `pnl_history:{user_id}:{symbol}` |

---

## Definition of Done

- [ ] T5.2 Tabbed Panel implemented and tested
- [ ] T5.1 Hierarchical Order Blotter implemented and tested
- [ ] T5.3 DOM/Level 2 Order Book implemented and tested
- [ ] T5.4 Position Sparklines implemented and tested
- [ ] All unit tests pass (>85% coverage for new code)
- [ ] Integration tests pass
- [ ] E2E tests pass
- [ ] Performance verified (no degradation with 100+ orders)
- [ ] WebSocket reconnection verified (T5.3)
- [ ] Code reviewed and approved
- [ ] Documentation updated

---

## Open Questions

1. **T5.3 DOM Symbol Selection:** How should the DOM ladder symbol be selected? Options: follow watchlist selection, follow last traded symbol, manual dropdown.
   - **Recommendation:** Follow watchlist selection with manual override option

2. **T5.3 DOM Position in Layout:** Where should the DOM ladder be placed? Options: side panel (right), overlay/modal, tab within tabbed panel.
   - **Recommendation:** Side panel (right) that can be collapsed

3. **T5.2 History Tab Retention:** How far back should history be accessible? Options: 7 days, 30 days, 90 days, configurable.
   - **Recommendation:** Default 30 days, max 90 days (configurable)

4. **T5.2 Fills Data Source:** Should fills come from local database (webhook-populated) or query Alpaca API directly?
   - **Recommendation:** Local database (already populated by webhook events) for performance and offline resilience

5. **T5.4 Sparkline CPU Cost:** With 50+ positions each rendering SVG sparklines, should we use virtualization or lazy rendering?
   - **Recommendation:** AG Grid's built-in row virtualization handles this; only render visible rows

---

**Last Updated:** 2026-01-27
**Status:** PLANNING
