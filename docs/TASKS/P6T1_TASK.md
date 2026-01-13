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
- All grids properly throttled for high-frequency updates (30-60fps)
- Dark mode with surface levels enabled by default
- Information density 3x current (no scroll for primary view)
- Workspace persistence survives refresh (DB-backed)

---

## Tasks (4 total)

### T1.1: Update Throttling & Batching Strategy - HIGH PRIORITY (FOUNDATION)

**Goal:** Prevent browser meltdown from high-frequency updates. **MUST be implemented FIRST** as foundation for all grid-based components.

**Rationale:** This is the critical foundation task. Without throttling, high-density grids will freeze the browser.

**Requirements:**
```javascript
// AG Grid throttling configuration
const gridOptions = {
    asyncTransactionWaitMillis: 50,  // Batch every 50ms
    suppressChangeDetection: true,
    suppressAnimationFrame: false,
};

// Custom throttle layer (30fps max)
const throttledUpdate = _.throttle((data) => {
    grid.applyTransactionAsync({ update: data });
}, 33);
```

**Acceptance Criteria:**
- [ ] Grid updates batched at 30-60fps max
- [ ] No UI freeze with 100+ simultaneous updates
- [ ] Delta updates (only changed rows, not full refresh)
- [ ] Performance metrics logged (dropped frames, update latency)
- [ ] Budget thresholds defined with explicit defaults:
  - Max batch size: 500 rows (configurable via `GRID_MAX_BATCH_SIZE` env var)
  - Degradation threshold: 120 updates/sec (configurable via `GRID_DEGRADE_THRESHOLD` env var)
  - Frame budget: 16ms per frame (60fps target)
- [ ] Backpressure handling when updates exceed budget (queue + drop oldest)
- [ ] Degradation mode: disable flash animations when >120 updates/sec
- [ ] Unit tests verify throttle timing accuracy

**Files:**
- Create: `apps/web_console_ng/static/js/grid_throttle.js`
- Create: `apps/web_console_ng/core/update_batcher.py`

---

### T1.2: Dark Mode Implementation - HIGH PRIORITY

**Goal:** Transform to professional dark theme with high contrast for trading environments.

**Requirements:**
- Force dark mode globally via `ui.dark_mode().enable()`
- Surface Levels for visual elevation (lighter grays, not shadows)
- Semantic Color Consistency (profit=buy=green, loss=sell=red)

**Surface Levels:**
```python
SURFACE_LEVELS = {
    'level_0': '#121212',  # Background
    'level_1': '#1E1E1E',  # Cards/Panels
    'level_2': '#2D2D2D',  # Popups/Modals
    'level_3': '#383838',  # Tooltips/Overlays
}

SEMANTIC_COLORS = {
    'profit': '#00E676',   # Green - positive P&L, successful fills
    'loss': '#FF5252',     # Red - negative P&L, errors
    'buy': '#00E676',      # Green - buy actions
    'sell': '#FF5252',     # Red - sell actions
    'warning': '#FFB300',  # Orange - warnings, alerts
    'info': '#2196F3',     # Blue - informational
    'neutral': '#90A4AE',  # Gray - disabled, placeholder
}
```

**Acceptance Criteria:**
- [ ] Dark mode enabled by default on all pages
- [ ] P&L values use neon green/red for visibility
- [ ] Surface Levels create visual hierarchy without shadows
- [ ] Semantic colors are consistent (buy=green, sell=red everywhere)
- [ ] No accessibility issues (WCAG AA contrast ratios)
- [ ] All existing pages updated to use dark theme classes

**Files:**
- Create: `apps/web_console_ng/ui/dark_theme.py`
- Modify: `apps/web_console_ng/ui/theme.py`, `apps/web_console_ng/ui/layout.py`

---

### T1.3: High-Density Trading Layout - HIGH PRIORITY

**Goal:** Maximize information density to match professional trading terminals.

**Requirements:**
- CSS Grid layout for fixed component positions
- Reduced table row heights (20-24px)
- Monospace fonts for numerical columns
- Compact card variants with minimal padding

**Acceptance Criteria:**
- [ ] Dashboard shows 3x more information above the fold
- [ ] No scrolling required for primary trading view
- [ ] Monospace font for all numerical data
- [ ] AG Grid rows at 20-24px height
- [ ] Compact mode toggle available

**Files:**
- Create: `apps/web_console_ng/ui/trading_layout.py`, `apps/web_console_ng/ui/density_classes.css`

---

### T1.4: Workspace Persistence - HIGH PRIORITY

**Goal:** Save and restore user's grid/panel customizations.

**Acceptance Criteria:**
- [ ] Column order persists across page refresh
- [ ] Column widths persist
- [ ] Sort/filter state persists
- [ ] Panel sizes persist
- [ ] Reset button restores defaults
- [ ] **Server-side persistence:** Store in DB tied to user_id (not just localStorage/cookies)
- [ ] **Schema versioning:** Handle migrations when grid schema changes
- [ ] **Max size limit:** Cap stored state at 64KB per user
- [ ] **Conflict resolution:** New defaults vs saved state (prefer saved, warn if schema mismatch)
- [ ] **Roaming:** Workspace follows user across devices

**Files:**
- Create: `apps/web_console_ng/core/workspace_persistence.py`
- Create: `apps/web_console_ng/static/js/grid_state_manager.js`
- Create: `db/migrations/` (use next available number per `db/migrations/README.md` naming convention)

---

## Dependencies

```
T1.1 Throttling ──> All subsequent P6 tracks (foundation)
T1.2 Dark Mode ──> All UI components
T1.4 Workspace ──> Grid components in later tracks
```

---

## Testing Strategy

### Unit Tests
- Throttle timing accuracy (T1.1)
- Dark theme color constants (T1.2)
- Workspace state serialization (T1.4)

### Integration Tests
- Workspace persistence DB round-trip
- Theme application across pages

### E2E Tests
- Full dashboard with dark mode
- Workspace persistence across refresh

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

---

**Last Updated:** 2026-01-13
**Status:** TASK
