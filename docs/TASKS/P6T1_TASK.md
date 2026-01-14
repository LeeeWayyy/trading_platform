---
id: P6T1
title: "Professional Trading Terminal - Core Infrastructure"
phase: P6
task: T1
priority: P0
owner: "@development-team"
state: TASK
created: 2026-01-13
dependencies: [P5]
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P6_PLANNING.md]
features: [T1.1-T1.4]
---

# P6T1: Core Infrastructure

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** TASK (Not Started)
**Priority:** P0 (Foundation - Must Complete First)
**Owner:** @development-team
**Created:** 2026-01-13
**Track:** Track 1 of 18
**Dependency:** P5 (NiceGUI Migration) must be complete

---

## Objective

Establish the foundational infrastructure for the professional trading terminal: update throttling, dark mode, high-density layout, and workspace persistence.

**Success looks like:**
- All grids properly throttled for high-frequency updates (~10fps via server throttle + 50ms batching)
- Dark mode with surface levels enabled by default
- Information density 3x current (no scroll for primary view)
- Workspace persistence survives refresh (DB-backed)

---

## Pre-Implementation Analysis Summary

### Existing Infrastructure (from codebase analysis)

**Throttling (`apps/web_console_ng/core/realtime.py`):**
- Python-level: `MAX_UPDATES_PER_SECOND = 10` (100ms min interval)
- Bounded queue: `MAX_QUEUE_SIZE = 100` with backpressure (drop oldest)
- Latest-message-wins pattern in `_worker()` method
- Fire-and-forget grid updates with 5s timeout

**Grid Components:**
- `positions_grid.py`: Uses `applyTransaction()` with delta updates, row ID = `symbol`
- `orders_table.py`: Uses `applyTransaction()` with delta updates, row ID = `client_order_id`
- No AG Grid JavaScript-level throttling configured

**Layout (`apps/web_console_ng/ui/layout.py`):**
- Light theme: `bg-slate-100`, `bg-gray-50`
- Header already dark: `bg-slate-900`
- No surface level system
- Standard padding: `p-6`, `gap-1`

**State Persistence (`apps/web_console_ng/core/state_manager.py`):**
- `UserStateManager` with Redis-backed storage (24h TTL)
- `save_preferences()` for user preferences
- No grid-specific state persistence
- Next migration number: `0022`

**Static Assets:**
- `static/css/custom.css`: Only fadeHighlight animation
- `static/js/aggrid_renderers.js`: Custom cell renderers
- `static/js/trading_state_listener.js`: Kill switch/circuit breaker state

---

## Tasks (4 total)

### T1.1: Update Throttling & Batching Strategy - HIGH PRIORITY (FOUNDATION)

**Goal:** Prevent browser meltdown from high-frequency updates. **MUST be implemented FIRST** as foundation for all grid-based components.

**Rationale:** This is the critical foundation task. Without throttling, high-density grids will freeze the browser.

#### Current State Analysis

| Layer | Current | Target |
|-------|---------|--------|
| Python throttle | 10/sec (100ms) | Keep as-is (backend limit, ~10fps render rate) |
| AG Grid batching | None | `asyncTransactionWaitMillis: 50` (smooths bursty traffic) |
| Animation control | `animateRows: true` | Conditional (disable >120 ops/sec per grid) |

#### Implementation Details

**1. AG Grid Configuration Update**

Modify grid options in `positions_grid.py` and `orders_table.py`:

```python
# Add to grid options dict
{
    # Existing options...
    "asyncTransactionWaitMillis": 50,  # Batch async updates every 50ms
    "suppressAnimationFrame": False,    # Allow RAF for smooth rendering
    "animateRows": True,                # Enable by default (GridThrottle disables when degraded)
}
```

**IMPORTANT:** `asyncTransactionWaitMillis` only affects `applyTransactionAsync()`, not sync `applyTransaction()`.
The existing code uses `run_grid_method("applyTransaction", ...)` which is synchronous.

**Change required:** Update Python grid update calls to use async method:
```python
# BEFORE (sync - asyncTransactionWaitMillis has no effect):
grid.run_grid_method("applyTransaction", {"add": added, "update": updated, "remove": removed}, timeout=5)

# AFTER (async - batching is enabled via asyncTransactionWaitMillis):
grid.run_grid_method("applyTransactionAsync", {"add": added, "update": updated, "remove": removed}, timeout=5)
```

**Wiring Backpressure Metrics:**

The existing `realtime.py` implements backpressure via `MAX_QUEUE_SIZE = 100` (drop oldest when full).
To track dropped updates in `GridPerformanceMonitor`, modify `realtime.py` to emit a callback:

```python
# In realtime.py, when dropping updates due to backpressure:
from apps.web_console_ng.core.grid_performance import get_monitor

# In the backpressure drop logic (where oldest message is discarded):
if len(queue) >= MAX_QUEUE_SIZE:
    dropped = queue.popleft()  # Drop oldest
    # Record dropped update in performance monitor if available
    monitor = get_monitor_by_grid_id(grid_id)  # Need to pass grid_id context
    if monitor:
        monitor.metrics.record_dropped(1)
    logger.warning("realtime_update_dropped", extra={"grid_id": grid_id})
```

**Note:** Full wiring requires passing `grid_id` context through the realtime update path. This is an enhancement - the current plan focuses on browser-side monitoring. Server-side dropped-update wiring can be added in a follow-up if needed.

**Note:** Do NOT use `suppressChangeDetection: True` - it can leave cells stale with transaction-based updates. AG Grid's default change detection works correctly with our update pattern.

**2. Browser-Side Throttle Module**

Create `apps/web_console_ng/static/js/grid_throttle.js`:

```javascript
// Grid throttle - monitors update rates and toggles degradation mode PER GRID
// NOTE: Actual backpressure (queue + drop oldest) is handled Python-side in realtime.py
// This module only monitors browser-side update rates for degradation control
window.GridThrottle = {
    // Config loaded from server via data attribute on <body> element
    // Set by Python: <body data-grid-degrade-threshold="120" data-grid-debug="false">
    config: {
        degradeThreshold: 120,       // Default, overridden by data-grid-degrade-threshold
        hysteresisCount: 2,          // Consecutive windows before toggling (prevents flapping)
        debug: false,                // Gated logging, set via data-grid-debug="true"
    },

    // Per-grid state (not global - each grid has independent rate tracking)
    gridStates: {},

    // Track last row count per grid for fallback delta calculation
    lastCounts: {},

    // Track which grids use async transactions (to gate onRowDataUpdated)
    asyncGrids: new Set(),

    // Initialize config from server-provided data attributes (call once at page load)
    initConfig() {
        const body = document.body;
        const threshold = body.dataset.gridDegradeThreshold;
        const debug = body.dataset.gridDebug;
        if (threshold) this.config.degradeThreshold = parseInt(threshold, 10);
        if (debug === 'true') this.config.debug = true;
    },

    // Register a grid as using async transactions (call at grid init, before first update)
    // This prevents double-counting if onRowDataUpdated fires before first async flush
    registerAsyncGrid(gridId) {
        this.asyncGrids.add(gridId);
    },

    // Get or create state for a grid
    _getGridState(gridId) {
        if (!this.gridStates[gridId]) {
            this.gridStates[gridId] = {
                updateCount: 0,
                lastResetTime: Date.now(),
                degradedMode: false,
                consecutiveAbove: 0,   // Consecutive windows above threshold (hysteresis)
                consecutiveBelow: 0,   // Consecutive windows below threshold (hysteresis)
            };
        }
        return this.gridStates[gridId];
    },

    // PRIMARY: Record transaction result from onAsyncTransactionsFlushed
    // This accurately counts adds/updates/removes from transaction results
    recordTransactionResult(gridApi, gridId, results) {
        // Mark this grid as using async transactions (gates onRowDataUpdated)
        this.asyncGrids.add(gridId);

        let totalUpdates = 0;
        for (const result of results) {
            totalUpdates += (result.add?.length || 0) +
                           (result.update?.length || 0) +
                           (result.remove?.length || 0);
        }
        // Only count if there were actual operations (avoid overcounting empty flushes)
        if (totalUpdates > 0) {
            this._addToUpdateCount(gridApi, gridId, totalUpdates);
        }
    },

    // FALLBACK: Record update from onRowDataUpdated (for setRowData path ONLY)
    // Gated to avoid double-counting with async transactions
    recordUpdate(gridApi, gridId) {
        // GATE: Skip if this grid uses async transactions (avoid double-counting)
        if (this.asyncGrids.has(gridId)) {
            return;
        }

        // For setRowData, use currentCount as the update size
        // This handles full refresh scenarios where row count stays same but all data changed
        // (delta=0 would undercount in that case)
        const currentCount = gridApi.getDisplayedRowCount();
        const lastCount = this.lastCounts[gridId] || 0;
        const delta = Math.abs(currentCount - lastCount);
        this.lastCounts[gridId] = currentCount;

        // If delta is 0, assume full refresh (setRowData replaces all rows)
        // Use currentCount to capture the full refresh cost
        const updateSize = delta > 0 ? delta : currentCount || 1;
        this._addToUpdateCount(gridApi, gridId, updateSize);
    },

    // Internal: Add to update count and check degradation threshold (per-grid)
    // Uses hysteresis to prevent flapping: requires N consecutive windows above/below threshold
    _addToUpdateCount(gridApi, gridId, count) {
        const state = this._getGridState(gridId);
        state.updateCount += count;
        const elapsed = Date.now() - state.lastResetTime;

        if (elapsed >= 1000) {
            const rate = state.updateCount;
            state.updateCount = 0;
            state.lastResetTime = Date.now();

            // Hysteresis: track consecutive windows above/below threshold
            const aboveThreshold = rate > this.config.degradeThreshold;
            if (aboveThreshold) {
                state.consecutiveAbove++;
                state.consecutiveBelow = 0;
            } else {
                state.consecutiveBelow++;
                state.consecutiveAbove = 0;
            }

            // Toggle degradation mode only after N consecutive windows (prevents flapping)
            const shouldDegrade = state.consecutiveAbove >= this.config.hysteresisCount;
            const shouldRecover = state.consecutiveBelow >= this.config.hysteresisCount;

            if (shouldDegrade && !state.degradedMode) {
                state.degradedMode = true;
                this.setDegradedMode(gridApi, gridId, true);
                if (this.config.debug) {
                    console.log(`Grid ${gridId} degradation mode: ON (rate: ${rate}/sec, threshold: ${this.config.degradeThreshold})`);
                }
            } else if (shouldRecover && state.degradedMode) {
                state.degradedMode = false;
                this.setDegradedMode(gridApi, gridId, false);
                if (this.config.debug) {
                    console.log(`Grid ${gridId} degradation mode: OFF (rate: ${rate}/sec, threshold: ${this.config.degradeThreshold})`);
                }
            }
        }
    },

    setDegradedMode(gridApi, gridId, degraded) {
        // Disable animations in degraded mode for THIS grid
        gridApi.setGridOption('animateRows', !degraded);
        // Emit event for metrics
        window.dispatchEvent(new CustomEvent('grid_degradation_change', {
            detail: { gridId, degraded, timestamp: Date.now() }
        }));
    },

    // Get current metrics for debugging/monitoring
    getMetrics(gridId) {
        const state = this._getGridState(gridId);
        return {
            gridId,
            updateCount: state.updateCount,
            degradedMode: state.degradedMode,
            consecutiveAbove: state.consecutiveAbove,
            consecutiveBelow: state.consecutiveBelow,
        };
    }
};

// Initialize config from server data attributes
// Handle both cases: script loaded before or after DOMContentLoaded
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => window.GridThrottle.initConfig());
} else {
    // DOM already loaded, init immediately
    window.GridThrottle.initConfig();
}
```

**Note on Throttle Architecture (Server vs Browser):**

Throttling happens at **three layers** with different purposes:

| Layer | Location | Rate | Purpose |
|-------|----------|------|---------|
| **Server Throttle** | `realtime.py` | 10/sec (100ms interval) | Limits server→browser messages to prevent WebSocket flooding |
| **AG Grid Batching** | Browser | 50ms window | Batches multiple transactions arriving within 50ms before rendering |
| **Degradation Control** | `GridThrottle.js` | 120 ops/sec threshold | Disables animations when update rate exceeds threshold |

**Expected render rates with current throttle:**
- **Normal operation (10/sec server throttle):** ~10 renders/sec (one render per server message)
- **Bursty conditions:** AG Grid's 50ms batching smooths bursts (e.g., 5 rapid messages → 1 render)
- **Maximum theoretical:** 20 renders/sec (if server sent 20+ messages/sec, limited by 50ms batch window)

**Why 10/sec server + 50ms batching works well:**
- Server sends up to 10 messages/sec with ~100ms spacing
- Each message can contain multiple row changes (delta updates)
- AG Grid's `asyncTransactionWaitMillis: 50` provides smoothing buffer for any sub-100ms bursts
- Result: stable ~10fps rendering under normal conditions, graceful handling of bursty traffic

**Backpressure (queue + drop oldest):**
- Implemented in Python (`realtime.py` - `MAX_QUEUE_SIZE = 100`)
- Browser (`GridThrottle`) monitors only - doesn't queue or drop

**3. Python Performance Monitor**

Create `apps/web_console_ng/core/grid_performance.py`:

```python
"""Performance monitoring and metrics for high-frequency grid updates.

Note: This module MONITORS update rates and triggers degradation mode.
Actual batching is handled by AG Grid's asyncTransactionWaitMillis config.
"""

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from weakref import WeakKeyDictionary

from nicegui import ui

logger = logging.getLogger(__name__)


@dataclass
class UpdateMetrics:
    """Track grid update performance metrics."""

    updates_total: int = 0
    updates_dropped: int = 0
    batches_sent: int = 0
    last_batch_time_ms: float = 0.0
    degradation_events: int = 0
    _window_start: float = field(default_factory=time.time)
    _window_updates: int = 0

    def record_update(self, row_count: int) -> None:
        """Record an update batch."""
        self.updates_total += row_count
        self._window_updates += row_count

    def record_batch(self, batch_time_ms: float) -> None:
        """Record batch send timing."""
        self.batches_sent += 1
        self.last_batch_time_ms = batch_time_ms

    def record_dropped(self, count: int) -> None:
        """Record dropped updates (backpressure)."""
        self.updates_dropped += count

    def get_rate(self) -> float:
        """Get current update rate (updates/sec)."""
        elapsed = time.time() - self._window_start
        if elapsed < 1.0:
            return 0.0
        rate = self._window_updates / elapsed
        # Reset window every 5 seconds
        if elapsed >= 5.0:
            self._window_start = time.time()
            self._window_updates = 0
        return rate

    def to_dict(self) -> dict[str, Any]:
        """Export metrics for logging/monitoring."""
        return {
            "updates_total": self.updates_total,
            "updates_dropped": self.updates_dropped,
            "batches_sent": self.batches_sent,
            "last_batch_time_ms": round(self.last_batch_time_ms, 2),
            "degradation_events": self.degradation_events,
            "current_rate": round(self.get_rate(), 1),
        }


class GridPerformanceMonitor:
    """Monitors grid update rates and triggers degradation mode.

    Note: This class does NOT batch updates. AG Grid handles batching via
    asyncTransactionWaitMillis. This class monitors update rates and toggles
    degradation mode (disabling animations) when updates exceed threshold.
    """

    # Configurable via environment variables
    MAX_BATCH_SIZE = int(os.environ.get("GRID_MAX_BATCH_SIZE", "500"))
    DEGRADE_THRESHOLD = int(os.environ.get("GRID_DEGRADE_THRESHOLD", "120"))

    def __init__(self, grid_id: str) -> None:
        self.grid_id = grid_id
        self.metrics = UpdateMetrics()
        self._degraded = False

    def should_degrade(self) -> bool:
        """Check if degradation mode should be active and update state.

        IMPORTANT: This method has side effects (updates _degraded, logs, increments counter).
        Call ONCE per update cycle and cache the result.
        """
        rate = self.metrics.get_rate()
        should = rate > self.DEGRADE_THRESHOLD
        if should != self._degraded:
            self._degraded = should
            self.metrics.degradation_events += 1
            logger.info(
                "grid_degradation_mode_change",
                extra={
                    "grid_id": self.grid_id,
                    "degraded": should,
                    "rate": round(rate, 1),
                },
            )
        return should

    def is_degraded(self) -> bool:
        """Check current degradation state WITHOUT recalculating.

        Use this to read state without side effects. Call should_degrade() first
        to update state, then use is_degraded() for subsequent checks.
        """
        return self._degraded

    def log_metrics(self) -> None:
        """Log current metrics for observability."""
        logger.info(
            "grid_update_metrics",
            extra={"grid_id": self.grid_id, **self.metrics.to_dict()},
        )

    def attach_to_grid(self, grid: ui.aggrid) -> None:
        """Attach this monitor to a NiceGUI AG Grid instance and register globally.

        Uses WeakKeyDictionary to avoid monkey-patching the grid instance directly.
        """
        _grid_to_monitor[grid] = self  # WeakKeyDictionary - auto-cleanup when grid is GC'd
        _monitor_registry[self.grid_id] = self  # Register for metrics logging


# Module-level registry for periodic metrics logging
_monitor_registry: dict[str, GridPerformanceMonitor] = {}

# WeakKeyDictionary to map grid instances to monitors without monkey-patching
# When grid is garbage collected, the entry is automatically removed
_grid_to_monitor: WeakKeyDictionary[ui.aggrid, GridPerformanceMonitor] = WeakKeyDictionary()


def get_monitor(grid: ui.aggrid) -> GridPerformanceMonitor | None:
    """Retrieve attached monitor from grid instance via WeakKeyDictionary."""
    return _grid_to_monitor.get(grid)


def get_all_monitors() -> dict[str, GridPerformanceMonitor]:
    """Get all registered monitors for periodic metrics logging."""
    return _monitor_registry.copy()


def get_monitor_by_grid_id(grid_id: str) -> GridPerformanceMonitor | None:
    """Retrieve monitor by grid_id string (for use in realtime.py backpressure logging).

    This is useful when you have the grid_id but not the grid instance,
    such as in the realtime update path where backpressure drops occur.
    """
    return _monitor_registry.get(grid_id)
```

**4. Browser-Side Integration**

Wire `GridThrottle` to grid updates using event handlers:

1. **INIT - `onGridReady`**: Register grid as using async transactions (prevents double-counting on first update)
2. **PRIMARY - `onAsyncTransactionsFlushed`**: For accurate counting of transaction-based updates (adds/updates/removes)
3. **FALLBACK - `onRowDataUpdated`**: For `setRowData` path where transactions aren't used (gated for async grids)

```javascript
// In grid initialization (add to positions_grid.py and orders_table.py)
// INIT: Register as async grid BEFORE first update (prevents double-counting on first flush)
":onGridReady": "params => { if (window.GridThrottle) window.GridThrottle.registerAsyncGrid('positions_grid'); }",
// PRIMARY: Use transaction results for accurate counting (works with applyTransactionAsync)
":onAsyncTransactionsFlushed": "params => { if (window.GridThrottle) window.GridThrottle.recordTransactionResult(params.api, 'positions_grid', params.results); }",
// FALLBACK: For setRowData path (gated - skips async grids)
":onRowDataUpdated": "params => { if (window.GridThrottle) window.GridThrottle.recordUpdate(params.api, 'positions_grid'); }",
```

For orders_table.py:
```javascript
":onGridReady": "params => { if (window.GridThrottle) window.GridThrottle.registerAsyncGrid('orders_grid'); }",
":onAsyncTransactionsFlushed": "params => { if (window.GridThrottle) window.GridThrottle.recordTransactionResult(params.api, 'orders_grid', params.results); }",
":onRowDataUpdated": "params => { if (window.GridThrottle) window.GridThrottle.recordUpdate(params.api, 'orders_grid'); }",
```

**Why three handlers?**
- `onGridReady` registers the grid as async IMMEDIATELY, preventing `onRowDataUpdated` from counting before the first async flush
- `onAsyncTransactionsFlushed` provides the actual `results` array with `add`, `update`, `remove` counts from `applyTransactionAsync()` - this is the accurate path for counting row operations
- `onRowDataUpdated` is gated for async grids (via `asyncGrids` Set) - only fires for grids using `setRowData` instead of transactions

This ensures degradation mode triggers correctly even during bursty update storms where rows are updated (not added/removed) and the row count doesn't change.

**⚠️ Implementation Note - AG Grid Event Support Validation:**

Before implementation, verify that the installed NiceGUI/AG Grid version supports `onAsyncTransactionsFlushed`:
1. Check AG Grid version in `package.json` or NiceGUI's bundled version (requires AG Grid 23.1+)
2. Test event firing with a simple console.log in the handler
3. If unsupported, fall back to `onRowDataUpdated` only (less accurate but functional)

```javascript
// Fallback handler if onAsyncTransactionsFlushed is not available
":onRowDataUpdated": "params => { if (window.GridThrottle) window.GridThrottle.recordUpdate(params.api, 'positions_grid'); }",
```

**5. Python-Side Integration**

Integration point in `update_positions_grid()`:

**IMPORTANT:** Degradation mode is controlled ONLY by browser-side `GridThrottle`. Python monitors
update rates for metrics/logging but does NOT toggle animations. This avoids conflicting control
paths and reduces per-update RPC overhead.

```python
from apps.web_console_ng.core.grid_performance import get_monitor

async def update_positions_grid(grid: ui.aggrid, positions: list[dict], ...) -> set[str]:
    """Update grid with positions data."""
    # ... compute delta (existing logic) ...
    added_positions = [p for p in valid_positions if p["symbol"] not in previous_symbols]
    updated_positions = [p for p in valid_positions if p["symbol"] in previous_symbols]
    removed_symbols = [{"symbol": s} for s in (previous_symbols - current_symbols)]

    # Record ACTUAL delta size, not full list (avoids over-counting)
    monitor = get_monitor(grid)
    if monitor:
        delta_size = len(added_positions) + len(updated_positions) + len(removed_symbols)
        monitor.metrics.record_update(delta_size)
        # NOTE: Degradation mode is controlled by browser-side GridThrottle ONLY
        # Python just monitors for metrics - no animation toggling here

    # ... existing applyTransaction call ...
```

**6. Pass Config to Browser via Data Attributes**

Add to `layout.py` when rendering the page body:

```python
import os

# Pass grid config from env to browser via data attributes
degrade_threshold = os.environ.get("GRID_DEGRADE_THRESHOLD", "120")
debug_mode = os.environ.get("GRID_DEBUG", "false").lower() == "true"

# In the main_layout wrapper, set body data attributes:
ui.add_body_html(f'<script>document.body.dataset.gridDegradeThreshold = "{degrade_threshold}";</script>')
ui.add_body_html(f'<script>document.body.dataset.gridDebug = "{str(debug_mode).lower()}";</script>')
```

**7. Metrics Logging**

Metrics are emitted via structured logging and can be consumed by:
1. **Structured logs** → JSON logs scraped by log aggregator (existing pattern)
2. **Prometheus metrics** (optional) → Expose via `/metrics` endpoint

Add periodic metrics logging in `layout.py` with cleanup callback:

```python
from apps.web_console_ng.core.grid_performance import get_all_monitors
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager

# In main_layout wrapper, after grid setup:
async def log_grid_metrics() -> None:
    """Periodic metrics logging for all grids."""
    for grid_id, monitor in get_all_monitors().items():
        monitor.log_metrics()

metrics_timer = ui.timer(60.0, log_grid_metrics)  # Log every 60 seconds

# Register cleanup on client disconnect to prevent timer leaks
# (same pattern as status_timer in layout.py)
client_id = ui.context.client.storage.get("client_id")
if client_id:
    lifecycle_mgr = ClientLifecycleManager.get()
    await lifecycle_mgr.register_cleanup_callback(client_id, lambda: metrics_timer.cancel())
```

**Files to Create/Modify:**

| File | Action | Description |
|------|--------|-------------|
| `apps/web_console_ng/static/js/grid_throttle.js` | Create | Browser-side throttle module |
| `apps/web_console_ng/core/grid_performance.py` | Create | Python metrics and performance monitoring |
| `apps/web_console_ng/components/positions_grid.py` | Modify | Add AG Grid throttle config & attach monitor |
| `apps/web_console_ng/components/orders_table.py` | Modify | Add AG Grid throttle config & attach monitor |
| `apps/web_console_ng/ui/layout.py` | Modify | Load grid_throttle.js |

**Acceptance Criteria:**
- [ ] Browser render rate stable at ~10fps (10/sec server throttle + 50ms batch smoothing)
- [ ] No UI freeze with 100+ simultaneous row changes
- [ ] Delta updates (only changed rows, not full refresh)
- [ ] Performance metrics logged per-grid (update rate, degradation events)
- [ ] Budget thresholds defined with explicit defaults:
  - Degradation threshold: 120 ops/sec per grid (configurable via `GRID_DEGRADE_THRESHOLD` env var)
  - Batch window: 50ms (AG Grid's `asyncTransactionWaitMillis`)
- [ ] Server-side throttle: existing 10 messages/sec limit in `realtime.py`
- [ ] Backpressure handled by Python (`realtime.py` - existing `MAX_QUEUE_SIZE = 100`, drop oldest)
- [ ] Browser-side degradation mode: disable animations per-grid when >120 ops/sec
- [ ] Transaction-based counting via `onAsyncTransactionsFlushed` (accurate add/update/remove counts)
- [ ] Double-counting prevention: `onRowDataUpdated` gated for grids using async transactions
- [ ] Per-grid state: degradation tracked independently for each grid
- [ ] Unit tests verify throttle timing accuracy

---

### T1.2: Dark Mode Implementation - HIGH PRIORITY

**Goal:** Transform to professional dark theme with high contrast for trading environments.

#### Current State Analysis

| Component | Current | Target |
|-----------|---------|--------|
| Background | `bg-gray-50` | `#121212` (level_0) |
| Sidebar | `bg-slate-100` | `#1E1E1E` (level_1) |
| Header | `bg-slate-900` | Keep (already dark) |
| Cards | White | `#1E1E1E` (level_1) |
| P&L positive | `#16a34a` | `#00E676` (neon green) |
| P&L negative | `#dc2626` | `#FF5252` (neon red) |

#### Implementation Details

**1. Theme Module**

Create `apps/web_console_ng/ui/dark_theme.py`:

```python
"""Dark theme constants and utilities for professional trading terminal."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class SurfaceLevels:
    """Material Design-inspired surface elevation for dark mode.

    Higher levels = lighter grays (not shadows like light mode).
    """

    LEVEL_0: ClassVar[str] = "#121212"  # App background
    LEVEL_1: ClassVar[str] = "#1E1E1E"  # Cards, panels, sidebar
    LEVEL_2: ClassVar[str] = "#2D2D2D"  # Popups, modals, dropdowns
    LEVEL_3: ClassVar[str] = "#383838"  # Tooltips, overlays
    LEVEL_4: ClassVar[str] = "#454545"  # Elevated buttons, active states


@dataclass(frozen=True)
class SemanticColors:
    """Trading-specific semantic colors with high contrast for dark backgrounds."""

    # P&L and directional colors
    PROFIT: ClassVar[str] = "#00E676"      # Green - positive P&L, buy
    LOSS: ClassVar[str] = "#FF5252"        # Red - negative P&L, sell
    BUY: ClassVar[str] = "#00E676"         # Green - buy actions
    SELL: ClassVar[str] = "#FF5252"        # Red - sell actions

    # Status colors
    WARNING: ClassVar[str] = "#FFB300"     # Orange - warnings, alerts
    INFO: ClassVar[str] = "#2196F3"        # Blue - informational
    NEUTRAL: ClassVar[str] = "#90A4AE"     # Gray - disabled, placeholder

    # Trading states
    ACTIVE: ClassVar[str] = "#00E676"      # Active/enabled
    INACTIVE: ClassVar[str] = "#757575"    # Inactive/disabled
    PENDING: ClassVar[str] = "#FFB300"     # Pending operations

    # Text colors
    TEXT_PRIMARY: ClassVar[str] = "#FFFFFF"
    TEXT_SECONDARY: ClassVar[str] = "#B0B0B0"
    TEXT_DISABLED: ClassVar[str] = "#757575"


@dataclass(frozen=True)
class DarkTheme:
    """Complete dark theme configuration."""

    surface: ClassVar[type[SurfaceLevels]] = SurfaceLevels
    semantic: ClassVar[type[SemanticColors]] = SemanticColors

    @classmethod
    def get_tailwind_config(cls) -> dict[str, str]:
        """Return Tailwind-compatible color mappings."""
        return {
            # Surface levels
            "bg-surface-0": f"background-color: {SurfaceLevels.LEVEL_0}",
            "bg-surface-1": f"background-color: {SurfaceLevels.LEVEL_1}",
            "bg-surface-2": f"background-color: {SurfaceLevels.LEVEL_2}",
            "bg-surface-3": f"background-color: {SurfaceLevels.LEVEL_3}",
            # Semantic colors
            "text-profit": f"color: {SemanticColors.PROFIT}",
            "text-loss": f"color: {SemanticColors.LOSS}",
            "text-warning": f"color: {SemanticColors.WARNING}",
            "text-info": f"color: {SemanticColors.INFO}",
        }


def enable_dark_mode() -> None:
    """Enable dark mode globally via NiceGUI."""
    from nicegui import ui
    ui.dark_mode().enable()


def get_pnl_color(value: float) -> str:
    """Return appropriate color for P&L value."""
    if value >= 0:
        return SemanticColors.PROFIT
    return SemanticColors.LOSS


def get_side_color(side: str) -> str:
    """Return color for buy/sell side."""
    if side.lower() == "buy":
        return SemanticColors.BUY
    return SemanticColors.SELL


__all__ = [
    "SurfaceLevels",
    "SemanticColors",
    "DarkTheme",
    "enable_dark_mode",
    "get_pnl_color",
    "get_side_color",
]
```

**2. CSS Custom Properties**

Add to `apps/web_console_ng/static/css/custom.css`:

```css
/* Dark theme custom properties */
:root {
    --surface-0: #121212;
    --surface-1: #1E1E1E;
    --surface-2: #2D2D2D;
    --surface-3: #383838;
    --surface-4: #454545;

    --profit: #00E676;
    --loss: #FF5252;
    --warning: #FFB300;
    --info: #2196F3;
    --neutral: #90A4AE;

    --text-primary: #FFFFFF;
    --text-secondary: #B0B0B0;
    --text-disabled: #757575;
}

/* Surface level utilities */
.bg-surface-0 { background-color: var(--surface-0); }
.bg-surface-1 { background-color: var(--surface-1); }
.bg-surface-2 { background-color: var(--surface-2); }
.bg-surface-3 { background-color: var(--surface-3); }

/* Semantic color utilities */
.text-profit { color: var(--profit); }
.text-loss { color: var(--loss); }
.text-warning { color: var(--warning); }
.text-info { color: var(--info); }

/* Text color utilities (for dark theme typography) */
.text-text-primary { color: var(--text-primary); }
.text-text-secondary { color: var(--text-secondary); }
.text-text-disabled { color: var(--text-disabled); }

/* AG Grid dark theme overrides */
.ag-theme-alpine-dark {
    --ag-background-color: var(--surface-1);
    --ag-header-background-color: var(--surface-2);
    --ag-odd-row-background-color: var(--surface-0);
    --ag-row-hover-color: var(--surface-3);
}
```

**3. Layout Updates**

Modify `apps/web_console_ng/ui/layout.py` to enable dark mode:

```python
# At start of main_layout wrapper
from apps.web_console_ng.ui.dark_theme import enable_dark_mode
enable_dark_mode()

# Update sidebar classes
drawer = ui.left_drawer(value=True).classes("bg-surface-1 w-64")

# Update main content
with ui.column().classes("w-full p-6 bg-surface-0 min-h-screen"):
```

**Files to Create/Modify:**

| File | Action | Description |
|------|--------|-------------|
| `apps/web_console_ng/ui/dark_theme.py` | Create | Theme constants and utilities |
| `apps/web_console_ng/static/css/custom.css` | Modify | Add CSS custom properties |
| `apps/web_console_ng/ui/layout.py` | Modify | Enable dark mode, update classes |
| `apps/web_console_ng/components/positions_grid.py` | Modify | Use semantic colors for P&L |
| `apps/web_console_ng/components/orders_table.py` | Modify | Use semantic colors for side |
| `apps/web_console_ng/static/js/aggrid_renderers.js` | Modify | Use CSS variables for colors |

**Acceptance Criteria:**
- [ ] Dark mode enabled by default on all pages
- [ ] P&L values use neon green/red for visibility
- [ ] Surface Levels create visual hierarchy without shadows
- [ ] Semantic colors are consistent (buy=green, sell=red everywhere)
- [ ] No accessibility issues (WCAG AA contrast ratios)
- [ ] All existing pages updated to use dark theme classes
- [ ] AG Grid uses `ag-theme-alpine-dark`

---

### T1.3: High-Density Trading Layout - HIGH PRIORITY

**Goal:** Maximize information density to match professional trading terminals.

#### Current State Analysis

| Element | Current | Target |
|---------|---------|--------|
| Body padding | `p-6` (24px) | `p-2` (8px) |
| Row height | Default (~40px) | 20-24px |
| Font | System sans-serif | Monospace for numbers |
| Grid gap | `gap-1` (4px) | `gap-0.5` (2px) |
| Cards | Standard padding | Compact variant |

#### Implementation Details

**1. Density CSS Classes**

Create `apps/web_console_ng/static/css/density.css`:

```css
/* High-density trading layout utilities */

/* Compact AG Grid rows */
.ag-grid-compact .ag-row {
    height: 22px !important;
}
.ag-grid-compact .ag-header-row {
    height: 28px !important;
}
.ag-grid-compact .ag-cell {
    padding: 0 4px !important;
    line-height: 22px !important;
}

/* Monospace for numeric columns */
.font-mono-numbers {
    font-family: 'JetBrains Mono', 'Fira Code', 'SF Mono', monospace;
    font-variant-numeric: tabular-nums;
}

/* Compact cards */
.card-compact {
    padding: 8px !important;
}
.card-compact .q-card__section {
    padding: 4px 8px !important;
}

/* Compact form elements */
.input-compact .q-field__control {
    height: 28px !important;
    min-height: 28px !important;
}
.input-compact .q-field__native {
    padding: 0 8px !important;
}

/* Reduced margins */
.trading-layout {
    gap: 4px !important;
}
.trading-layout > * {
    margin: 0 !important;
}

/* Dense header */
.header-compact {
    height: 40px !important;
    padding: 0 8px !important;
}

/* Information density grid */
.grid-dense {
    display: grid;
    gap: 4px;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
}
```

**2. Trading Layout Component**

Create `apps/web_console_ng/ui/trading_layout.py`:

```python
"""High-density trading layout utilities."""

from __future__ import annotations

from nicegui import ui


def compact_card(title: str | None = None) -> ui.card:
    """Create a compact card with reduced padding."""
    card = ui.card().classes("card-compact bg-surface-1")
    if title:
        with card:
            ui.label(title).classes("text-xs font-semibold text-text-secondary uppercase")
    return card


def trading_grid() -> ui.element:
    """Create a dense grid container for trading widgets."""
    return ui.element("div").classes("grid-dense trading-layout")


def stats_row() -> ui.row:
    """Create a compact row for statistics."""
    return ui.row().classes("gap-2 items-center h-6")


def numeric_label(value: str | float, prefix: str = "", suffix: str = "") -> ui.label:
    """Create a monospace label for numeric values."""
    text = f"{prefix}{value}{suffix}"
    return ui.label(text).classes("font-mono-numbers text-sm")


def apply_compact_grid_options(options: dict) -> dict:
    """Add compact styling options to AG Grid config."""
    options.update({
        "rowHeight": 22,
        "headerHeight": 28,
        "suppressCellFocus": True,  # Cleaner appearance
    })
    return options


def apply_compact_grid_classes(grid: ui.aggrid) -> ui.aggrid:
    """Apply compact CSS classes to AG Grid wrapper.

    IMPORTANT: Must be called after grid creation to add the .ag-grid-compact class
    which applies the compact row/header heights via CSS.
    """
    grid.classes("ag-grid-compact")
    return grid


__all__ = [
    "compact_card",
    "trading_grid",
    "stats_row",
    "numeric_label",
    "apply_compact_grid_options",
    "apply_compact_grid_classes",
]
```

**Files to Create/Modify:**

| File | Action | Description |
|------|--------|-------------|
| `apps/web_console_ng/static/css/density.css` | Create | High-density CSS utilities |
| `apps/web_console_ng/ui/trading_layout.py` | Create | Layout components |
| `apps/web_console_ng/ui/layout.py` | Modify | Load density.css, reduce padding |
| `apps/web_console_ng/components/positions_grid.py` | Modify | Apply compact grid options AND classes |
| `apps/web_console_ng/components/orders_table.py` | Modify | Apply compact grid options AND classes |
| `apps/web_console_ng/pages/dashboard.py` | Modify | Use trading_grid layout |

**Grid Integration Example:**

```python
from apps.web_console_ng.ui.trading_layout import apply_compact_grid_options, apply_compact_grid_classes

def create_positions_grid() -> ui.aggrid:
    options = {...}  # existing options
    options = apply_compact_grid_options(options)  # Add compact row/header heights
    grid = ui.aggrid(options)
    apply_compact_grid_classes(grid)  # Add .ag-grid-compact CSS class to wrapper
    return grid
```

**Acceptance Criteria:**
- [ ] Dashboard shows 3x more information above the fold
- [ ] No scrolling required for primary trading view
- [ ] Monospace font for all numerical data
- [ ] AG Grid rows at 20-24px height
- [ ] `.ag-grid-compact` class applied to all trading grids
- [ ] Compact mode toggle available (future enhancement)
- [ ] CSS `!important` overrides verified working (manual test: inspect computed styles in DevTools to confirm density.css rules apply over AG Grid defaults)

---

### T1.4: Workspace Persistence - HIGH PRIORITY

**Goal:** Save and restore user's grid/panel customizations.

#### Current State Analysis

| Feature | Current | Target |
|---------|---------|--------|
| Preferences storage | Redis (24h TTL) | PostgreSQL (permanent) |
| Grid state | None | Column order, width, sort, filter |
| Scope | User preferences | + Grid state per user |
| Schema versioning | None | Migration-based |
| Size limit | None | 64KB per user |

#### Implementation Details

**1. Database Migration**

Create `db/migrations/0022_create_workspace_state.sql`:

```sql
-- Workspace persistence for grid/panel state
-- User-specific, survives refresh, roams across devices

CREATE TABLE IF NOT EXISTS workspace_state (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    workspace_key TEXT NOT NULL,        -- e.g., 'grid.positions_grid'
    state_json JSONB NOT NULL,           -- Grid state (columns, sort, filter)
    schema_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Constraints
    CONSTRAINT workspace_state_user_key_unique UNIQUE (user_id, workspace_key),
    CONSTRAINT workspace_state_size_limit CHECK (octet_length(state_json::text) <= 65536)
);

-- Index for user lookups
CREATE INDEX IF NOT EXISTS idx_workspace_state_user_id ON workspace_state(user_id);

-- Trigger for updated_at
CREATE OR REPLACE FUNCTION update_workspace_state_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER workspace_state_updated_at
    BEFORE UPDATE ON workspace_state
    FOR EACH ROW
    EXECUTE FUNCTION update_workspace_state_timestamp();
```

**2. Workspace Persistence Service**

Create `apps/web_console_ng/core/workspace_persistence.py`:

```python
"""Workspace persistence for grid/panel state."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from apps.web_console_ng.core.database import get_db_pool

logger = logging.getLogger(__name__)

# Current schema versions for each workspace type
SCHEMA_VERSIONS = {
    "grid": 1,
    "panel": 1,
}

MAX_STATE_SIZE = 65536  # 64KB


class DatabaseUnavailableError(Exception):
    """Raised when database pool is not configured."""

    pass


@dataclass
class WorkspaceState:
    """Workspace state container."""

    user_id: str
    workspace_key: str
    state: dict[str, Any]
    schema_version: int


def _require_db_pool():
    """Get DB pool or raise DatabaseUnavailableError.

    IMPORTANT: get_db_pool() returns None when DATABASE_URL is unset.
    All callers must handle this case to avoid AttributeError on None.
    """
    pool = get_db_pool()
    if pool is None:
        raise DatabaseUnavailableError("Database pool not configured (DATABASE_URL unset)")
    return pool


class WorkspacePersistenceService:
    """Service for saving/loading workspace state.

    Note: Uses psycopg AsyncConnectionPool with async context managers.
    The project's DB pool is AsyncConnectionPool, so we must use async APIs.
    """

    async def save_grid_state(
        self,
        user_id: str,
        grid_id: str,
        state: dict[str, Any],
    ) -> bool:
        """Save grid column state (order, width, sort, filter).

        Args:
            user_id: User identifier
            grid_id: Grid identifier (e.g., 'positions_grid')
            state: AG Grid state object from getColumnState()

        Returns:
            True if saved successfully

        Raises:
            DatabaseUnavailableError: If database pool is not configured
        """
        workspace_key = f"grid.{grid_id}"
        state_json = json.dumps(state)

        # Use byte length for parity with DB constraint (octet_length)
        state_bytes = len(state_json.encode("utf-8"))
        if state_bytes > MAX_STATE_SIZE:
            logger.warning(
                "workspace_state_too_large",
                extra={
                    "user_id": user_id,
                    "workspace_key": workspace_key,
                    "size_bytes": state_bytes,
                    "limit": MAX_STATE_SIZE,
                },
            )
            return False

        pool = _require_db_pool()
        # Use async context managers for AsyncConnectionPool
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO workspace_state (user_id, workspace_key, state_json, schema_version)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id, workspace_key)
                    DO UPDATE SET state_json = EXCLUDED.state_json, schema_version = EXCLUDED.schema_version
                    """,
                    (user_id, workspace_key, state_json, SCHEMA_VERSIONS["grid"]),
                )
            await conn.commit()

        logger.info(
            "workspace_state_saved",
            extra={
                "user_id": user_id,
                "workspace_key": workspace_key,
                "size_bytes": state_bytes,
            },
        )
        return True

    async def load_grid_state(
        self,
        user_id: str,
        grid_id: str,
    ) -> dict[str, Any] | None:
        """Load grid column state.

        Returns None if no state saved or schema version mismatch.

        Raises:
            DatabaseUnavailableError: If database pool is not configured
        """
        workspace_key = f"grid.{grid_id}"

        pool = _require_db_pool()
        # Use async context managers for AsyncConnectionPool
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT state_json, schema_version
                    FROM workspace_state
                    WHERE user_id = %s AND workspace_key = %s
                    """,
                    (user_id, workspace_key),
                )
                row = await cur.fetchone()

        if not row:
            return None

        state_json, saved_version = row
        current_version = SCHEMA_VERSIONS["grid"]

        if saved_version != current_version:
            logger.warning(
                "workspace_state_schema_mismatch",
                extra={
                    "user_id": user_id,
                    "workspace_key": workspace_key,
                    "saved_version": saved_version,
                    "current_version": current_version,
                },
            )
            # Return None to use defaults (don't apply stale state)
            return None

        return json.loads(state_json)

    async def reset_workspace(self, user_id: str, workspace_key: str | None = None) -> None:
        """Reset workspace state to defaults.

        Args:
            user_id: User identifier
            workspace_key: Optional specific key to reset (None = reset all)

        Raises:
            DatabaseUnavailableError: If database pool is not configured
        """
        pool = _require_db_pool()
        # Use async context managers for AsyncConnectionPool
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                if workspace_key:
                    await cur.execute(
                        "DELETE FROM workspace_state WHERE user_id = %s AND workspace_key = %s",
                        (user_id, workspace_key),
                    )
                else:
                    await cur.execute(
                        "DELETE FROM workspace_state WHERE user_id = %s",
                        (user_id,),
                    )
            await conn.commit()

        logger.info(
            "workspace_state_reset",
            extra={"user_id": user_id, "workspace_key": workspace_key or "all"},
        )


# Singleton instance
_workspace_service: WorkspacePersistenceService | None = None


def get_workspace_service() -> WorkspacePersistenceService:
    """Get workspace persistence service singleton."""
    global _workspace_service
    if _workspace_service is None:
        _workspace_service = WorkspacePersistenceService()
    return _workspace_service


__all__ = [
    "WorkspaceState",
    "WorkspacePersistenceService",
    "DatabaseUnavailableError",
    "get_workspace_service",
    "SCHEMA_VERSIONS",
]
```

**3. API Endpoints (Secure)**

Create `apps/web_console_ng/api/workspace.py`:

```python
"""API endpoints for workspace persistence.

SECURITY: User identity is derived from authenticated session cookie,
NOT from client-provided headers. This prevents spoofing attacks.
"""

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel

from apps.web_console_ng.auth.csrf import verify_csrf_token  # Reuse existing CSRF helper (hmac.compare_digest)
from apps.web_console_ng.core.workspace_persistence import (
    DatabaseUnavailableError,
    get_workspace_service,
)

router = APIRouter(prefix="/api/workspace", tags=["workspace"])

# Allowlist of valid grid IDs to prevent storage fan-out attacks
#
# MAINTENANCE: When adding a new grid that needs workspace persistence:
# 1. Add the grid_id to this set
# 2. Update the browser-side GridThrottle.recordUpdate() call in the new grid's initialization
# 3. Add the grid_id to the test fixtures in test_workspace_api_security.py
# 4. Run tests: pytest tests/apps/web_console_ng/test_workspace_api_security.py -v
#
# Valid grid IDs should match the pattern used in create_*_grid() functions.
VALID_GRID_IDS = frozenset({
    "positions_grid",
    "orders_grid",
    "backtest_results_grid",
    "risk_metrics_grid",
})
MAX_GRID_ID_LENGTH = 64


def validate_grid_id(grid_id: str) -> None:
    """Validate grid_id against allowlist to prevent storage fan-out.

    SECURITY: Unvalidated grid_id allows arbitrary key creation, leading to storage bloat.
    """
    if len(grid_id) > MAX_GRID_ID_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"grid_id exceeds max length ({MAX_GRID_ID_LENGTH})",
        )
    if grid_id not in VALID_GRID_IDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid grid_id. Must be one of: {', '.join(sorted(VALID_GRID_IDS))}",
        )


class GridState(BaseModel):
    """Grid state model."""
    columns: list[dict[str, Any]] | None = None
    filters: dict[str, Any] | None = None
    sort: list[dict[str, Any]] | None = None


async def require_authenticated_user(request: Request) -> dict[str, Any]:
    """FastAPI dependency to get authenticated user from session cookie.

    SECURITY: Derives user from session cookie via request.state.user (set by SessionMiddleware).
    Falls back to direct session validation if middleware hasn't run.
    Raises 401 if not authenticated.
    """
    # FIRST: Check request.state.user (set by SessionMiddleware)
    user = getattr(request.state, "user", None)
    if user and user.get("user_id"):
        return user

    # FALLBACK: Direct session validation (if SessionMiddleware didn't run)
    from apps.web_console_ng.auth.middleware import _validate_session_and_get_user
    user_data, _ = await _validate_session_and_get_user(request)
    if not user_data or not user_data.get("user_id"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return user_data


@router.post("/grid/{grid_id}")
async def save_grid_state(
    grid_id: str,
    request: Request,
    state: GridState = Body(...),
    user: dict[str, Any] = Depends(require_authenticated_user),
) -> dict[str, bool]:
    """Save grid state.

    SECURITY: User derived from session, CSRF validated, grid_id validated against allowlist.
    """
    validate_grid_id(grid_id)  # Prevent storage fan-out attacks
    await verify_csrf_token(request)  # Uses hmac.compare_digest for timing-safe comparison

    service = get_workspace_service()
    try:
        success = await service.save_grid_state(
            user_id=user["user_id"],  # From session, NOT client header
            grid_id=grid_id,
            state=state.model_dump(exclude_none=True),
        )
    except DatabaseUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured",
        )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="State too large",
        )
    return {"success": True}


@router.get("/grid/{grid_id}")
async def load_grid_state(
    grid_id: str,
    user: dict[str, Any] = Depends(require_authenticated_user),
) -> dict[str, Any] | None:
    """Load grid state.

    SECURITY: User derived from session, grid_id validated against allowlist.
    """
    validate_grid_id(grid_id)  # Prevent probing invalid grid IDs
    service = get_workspace_service()
    try:
        return await service.load_grid_state(user_id=user["user_id"], grid_id=grid_id)
    except DatabaseUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured",
        )


@router.delete("/grid/{grid_id}")
async def reset_grid_state(
    grid_id: str,
    request: Request,
    user: dict[str, Any] = Depends(require_authenticated_user),
) -> dict[str, bool]:
    """Reset grid state to defaults.

    SECURITY: User derived from session, CSRF validated, grid_id validated against allowlist.
    """
    validate_grid_id(grid_id)  # Prevent arbitrary key deletion probing
    await verify_csrf_token(request)  # Uses hmac.compare_digest for timing-safe comparison

    service = get_workspace_service()
    try:
        await service.reset_workspace(user["user_id"], f"grid.{grid_id}")
    except DatabaseUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured",
        )
    return {"success": True}
```

**Note:** The `require_authenticated_user` dependency uses `request.state.user` (set by `SessionMiddleware`)
with a fallback to `_validate_session_and_get_user()` from the existing auth middleware. No new
middleware function is needed - we reuse the existing session validation infrastructure.

**6. Router Registration**

Register the workspace router in `apps/web_console_ng/main.py`:

```python
# In main.py, after existing router imports
from apps.web_console_ng.api.workspace import router as workspace_router

# After existing app.include_router calls (e.g., auth_api_router)
app.include_router(workspace_router)
```

This follows the existing pattern used for `auth_api_router`.

**4. Browser-Side Grid State Manager (Secure)**

Create `apps/web_console_ng/static/js/grid_state_manager.js`:

```javascript
// Grid state manager for workspace persistence
// SECURITY: User identity derived from session cookie server-side, not client header

window.GridStateManager = {
    // Debounce state saves (500ms)
    saveTimeouts: {},

    // Track grids currently restoring state (to suppress save loops)
    // When restoring, column/sort/filter changes trigger events - we must ignore them
    _restoring: new Set(),

    // Get CSRF token from cookie (for POST/DELETE requests)
    _getCsrfToken() {
        const match = document.cookie
            .split('; ')
            .find(row => row.startsWith('ng_csrf='));
        return match ? match.split('=')[1] : '';
    },

    // Save grid state to backend (user derived from session server-side)
    async saveState(gridApi, gridId) {
        // GUARD: Skip save if currently restoring (prevents save loops)
        if (this._restoring.has(gridId)) {
            return;
        }

        // Clear pending save
        if (this.saveTimeouts[gridId]) {
            clearTimeout(this.saveTimeouts[gridId]);
        }

        // Debounce to avoid excessive saves
        this.saveTimeouts[gridId] = setTimeout(async () => {
            // Double-check we're not restoring (could have started during debounce)
            if (this._restoring.has(gridId)) {
                return;
            }

            const columnState = gridApi.getColumnState();
            const filterModel = gridApi.getFilterModel();
            const sortModel = gridApi.getSortModel();

            const state = {
                columns: columnState,
                filters: filterModel,
                sort: sortModel,
            };

            try {
                const response = await fetch('/api/workspace/grid/' + gridId, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': this._getCsrfToken(),  // CSRF protection
                    },
                    credentials: 'same-origin',  // Include session cookie
                    body: JSON.stringify(state),
                });
                if (!response.ok) {
                    console.warn('Failed to save grid state:', response.status);
                }
            } catch (e) {
                console.warn('Failed to save grid state:', e);
            }
        }, 500);
    },

    // Restore grid state from backend (user derived from session server-side)
    // Sets _restoring flag to suppress auto-save during restoration
    async restoreState(gridApi, gridId) {
        // Mark as restoring to suppress saves from change events
        this._restoring.add(gridId);

        try {
            const response = await fetch('/api/workspace/grid/' + gridId, {
                credentials: 'same-origin',  // Include session cookie
            });

            if (!response.ok) return false;

            const state = await response.json();
            if (!state) return false;

            if (state.columns) gridApi.applyColumnState({ state: state.columns, applyOrder: true });  // applyOrder: true ensures column ORDER is restored
            if (state.filters) {
                gridApi.setFilterModel(state.filters);
                // SAFETY: Emit event when filters are restored on critical grids
                // UI should show "Filters Active" warning to prevent hidden risk
                if (Object.keys(state.filters).length > 0) {
                    window.dispatchEvent(new CustomEvent('grid_filters_restored', {
                        detail: { gridId, filterCount: Object.keys(state.filters).length }
                    }));
                }
            }
            if (state.sort) gridApi.setSortModel(state.sort);

            return true;
        } catch (e) {
            console.warn('Failed to restore grid state:', e);
            return false;
        } finally {
            // Clear restoring flag after a short delay to ensure all events have fired
            // AG Grid may fire events asynchronously after applyColumnState
            setTimeout(() => this._restoring.delete(gridId), 100);
        }
    },

    // Register event listeners for auto-save (no user_id needed - from session)
    registerAutoSave(gridApi, gridId) {
        const saveHandler = () => this.saveState(gridApi, gridId);

        gridApi.addEventListener('columnMoved', saveHandler);
        gridApi.addEventListener('columnResized', saveHandler);
        gridApi.addEventListener('sortChanged', saveHandler);
        gridApi.addEventListener('filterChanged', saveHandler);
    },

    // Reset grid state to defaults
    async resetState(gridApi, gridId) {
        try {
            const response = await fetch('/api/workspace/grid/' + gridId, {
                method: 'DELETE',
                headers: {
                    'X-CSRF-Token': this._getCsrfToken(),
                },
                credentials: 'same-origin',
            });
            if (response.ok) {
                gridApi.resetColumnState();
                gridApi.setFilterModel(null);
                gridApi.setSortModel([]);
            }
        } catch (e) {
            console.warn('Failed to reset grid state:', e);
        }
    }
};
```

**5. Grid Integration**

Modify `create_positions_grid` in `apps/web_console_ng/components/positions_grid.py`:

```python
def create_positions_grid() -> ui.aggrid:
    """Create positions grid with throttling and state persistence.

    Note: User identity for state persistence is derived from session
    cookie server-side - no user_id parameter needed here.
    """
    # ... setup columns ...
    grid = ui.aggrid(...)

    # Attach batcher for update throttling
    batcher = GridPerformanceMonitor("positions_grid")
    batcher.attach_to_grid(grid)

    # Restore state on load (user derived from session server-side)
    grid.on(
        "gridReady",
        lambda _: ui.run_javascript(
            "window.GridStateManager.restoreState(window._positionsGridApi, 'positions_grid')"
        ),
    )

    # Auto-save on column/sort/filter changes (user derived from session server-side)
    grid.on(
        "gridReady",
        lambda _: ui.run_javascript(
            "window.GridStateManager.registerAutoSave(window._positionsGridApi, 'positions_grid')"
        ),
    )
    return grid
```

**Files to Create/Modify:**

| File | Action | Description |
|------|--------|-------------|
| `db/migrations/0022_create_workspace_state.sql` | Create | Database schema |
| `apps/web_console_ng/core/workspace_persistence.py` | Create | Persistence service |
| `apps/web_console_ng/api/workspace.py` | Create | REST endpoints (secure) |
| `apps/web_console_ng/main.py` | Modify | Register workspace router |
| `apps/web_console_ng/static/js/grid_state_manager.js` | Create | Browser-side manager (secure) |
| `apps/web_console_ng/ui/layout.py` | Modify | Load grid_state_manager.js |
| `apps/web_console_ng/components/positions_grid.py` | Modify | Add state persistence & batcher integration |
| `apps/web_console_ng/components/orders_table.py` | Modify | Add state persistence & batcher integration |

**Acceptance Criteria:**
- [ ] Column order persists across page refresh
- [ ] Column widths persist
- [ ] Sort/filter state persists
- [ ] Reset button restores defaults
- [ ] **Server-side persistence:** Store in DB tied to user_id (not just localStorage/cookies)
- [ ] **Schema versioning:** Handle migrations when grid schema changes
- [ ] **Max size limit:** Cap stored state at 64KB per user
- [ ] **Conflict resolution:** New defaults vs saved state (prefer saved, warn if schema mismatch)
- [ ] **Roaming:** Workspace follows user across devices
- [ ] **Security:** User identity derived from session cookie (not client headers)
- [ ] **Security:** CSRF protection on POST/DELETE endpoints
- [ ] **Security:** Unit tests verify unauthorized requests return 401/403
- [ ] **Safety:** Filter restoration emits `grid_filters_restored` event for UI warning display
- [ ] **Safety:** Positions grid shows "Filters Active" warning banner when filters are restored (prevents hidden risk)

---

## Dependencies

```
T1.1 Throttling ──> All subsequent P6 tracks (foundation)
T1.2 Dark Mode ──> All UI components
T1.3 Density ──> Dashboard, grid components
T1.4 Workspace ──> Grid components in later tracks
```

**Implementation Order:** T1.1 → T1.2 → T1.3 → T1.4

---

## Testing Strategy

### Unit Tests

**T1.1 Throttling:**
- `tests/apps/web_console_ng/test_update_batcher.py`
  - Test `UpdateMetrics.get_rate()` accuracy
  - Test `GridPerformanceMonitor.should_degrade()` threshold logic
  - Test metrics export format

**T1.2 Dark Mode:**
- `tests/apps/web_console_ng/test_dark_theme.py`
  - Test `get_pnl_color()` returns correct colors
  - Test `get_side_color()` for buy/sell
  - Test `DarkTheme.get_tailwind_config()` format

**T1.3 Density:**
- `tests/apps/web_console_ng/test_trading_layout.py`
  - Test `apply_compact_grid_options()` adds correct properties
  - Test component factory functions

**T1.4 Workspace:**
- `tests/apps/web_console_ng/test_workspace_persistence.py`
  - Test save/load round-trip
  - Test schema version mismatch handling
  - Test size limit enforcement
  - Test reset functionality

**T1.4 Security (Auth/CSRF Negative Tests):**
- `tests/apps/web_console_ng/test_workspace_api_security.py`
  - Test unauthenticated POST returns 401
  - Test unauthenticated GET returns 401
  - Test unauthenticated DELETE returns 401
  - Test missing CSRF token on POST returns 403
  - Test invalid CSRF token on POST returns 403
  - Test missing CSRF token on DELETE returns 403
  - Test invalid grid_id returns 400
  - Test grid_id exceeding max length returns 400

### Integration Tests

- `tests/integration/test_workspace_persistence_db.py`
  - Test database CRUD operations
  - Test concurrent saves (ON CONFLICT)
  - Test user isolation

### E2E Tests

- `tests/e2e/test_trading_terminal_e2e.py`
  - Full dashboard with dark mode
  - Grid state persistence across refresh
  - Degradation mode under load

---

## Definition of Done

- [ ] All 4 tasks implemented
- [ ] Throttling foundation in place
- [ ] Dark mode with surface levels
- [ ] High-density layout (3x information)
- [ ] Workspace persistence working (DB-backed)
- [ ] Unit tests > 85% coverage
- [ ] E2E tests pass
- [ ] Code reviewed and approved
- [ ] Migration 0022 applied successfully

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| AG Grid perf regression | Benchmark before/after with 1000 row updates |
| Dark mode accessibility | Verify WCAG AA contrast ratios |
| Workspace state bloat | 64KB limit + monitoring |
| Schema migration conflicts | Version numbers + graceful fallback |

---

**Last Updated:** 2026-01-13
**Status:** TASK
