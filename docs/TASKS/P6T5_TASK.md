---
id: P6T5
title: "Professional Trading Terminal - Grid Enhancements"
phase: P6
task: T5
priority: P1
owner: "@development-team"
state: TASK
created: 2026-01-13
dependencies: [P6T1]
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P6_PLANNING.md]
features: [T5.1-T5.4]
---

# P6T5: Grid Enhancements

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** TASK (Not Started)
**Priority:** P1 (Enhanced UX)
**Owner:** @development-team
**Created:** 2026-01-13
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

## Tasks (4 total)

### T5.1: Hierarchical Order Blotter - MEDIUM PRIORITY

**Goal:** View parent/child orders in expandable hierarchy.

**Dependency:** Requires P6T1.1 (Throttling) for smooth updates.

**Acceptance Criteria:**
- [ ] TWAP parent orders show expandable children
- [ ] Aggregate progress for parent orders (e.g., "400/1000 filled")
- [ ] Cancel parent cancels all children
- [ ] Tree-style expandable rows with smooth animation
- [ ] Updates throttled via P6T1 infrastructure

**Files:**
- Create: `apps/web_console_ng/components/hierarchical_orders.py`
- Modify: `apps/web_console_ng/components/orders_table.py`

---

### T5.2: Tabbed Positions/Orders Panel - LOW PRIORITY

**Goal:** Consolidate bottom panel with tabs.

**Acceptance Criteria:**
- [ ] Tabbed panel (Positions | Working | Fills | History)
- [ ] Remember selected tab across sessions (via P6T1.4)
- [ ] Badge counts for pending orders
- [ ] Cross-tab filtering by symbol

---

### T5.3: DOM/Level 2 Order Book - MEDIUM PRIORITY

**Goal:** Display market depth for informed execution decisions.

**Dependency:** Requires P6T1.1 (Throttling) for smooth 30fps updates.

**Acceptance Criteria:**
- [ ] DOM ladder displays bid/ask depth (5-10 levels)
- [ ] Click price to pre-fill order form
- [ ] Large orders highlighted (>2x average size)
- [ ] Real-time updates throttled to 30fps
- [ ] Fallback when Level 2 data unavailable (show "L2 unavailable")
- [ ] Data source/licensing documented

**Files:**
- Create: `apps/web_console_ng/components/dom_ladder.py`, `apps/web_console_ng/components/depth_visualizer.py`

---

### T5.4: Position Sparklines - MEDIUM PRIORITY

**Goal:** Mini trend charts in position table rows.

**Dependency:** Requires P6T1.1 (Throttling) for smooth updates.

**Acceptance Criteria:**
- [ ] Sparkline visible in position row
- [ ] Shows last 1 hour of P&L (60 data points)
- [ ] Updates in real-time (throttled via P6T1)
- [ ] Color indicates trend direction (green up, red down)

**Files:**
- Create: `apps/web_console_ng/components/sparkline_renderer.py`, `apps/web_console_ng/static/js/sparkline.js`

---

## Dependencies

```
P6T1.1 Throttling ──> T5.1 Hierarchical Blotter
                  ──> T5.3 DOM/Level 2
                  ──> T5.4 Sparklines

P6T1.4 Workspace ──> T5.2 Tabbed Panel (remember tab)
```

---

## Testing Strategy

### Unit Tests
- Hierarchical order grouping
- Sparkline data aggregation
- DOM ladder level calculations

### Integration Tests
- Parent/child order relationship
- Tab state persistence

### E2E Tests
- Hierarchical blotter expand/collapse
- DOM ladder price click

---

## Definition of Done

- [ ] All 4 tasks implemented
- [ ] Hierarchical order blotter functional
- [ ] Tabbed panel working
- [ ] DOM ladder displaying market depth
- [ ] Sparklines in position rows
- [ ] Unit tests > 85% coverage
- [ ] E2E tests pass
- [ ] Code reviewed and approved

---

**Last Updated:** 2026-01-13
**Status:** TASK
