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
features: [T15.1-T15.3]
---

# P6T15: Universe & Exposure

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** TASK (Not Started)
**Priority:** P1 (Research Infrastructure)
**Owner:** @development-team
**Created:** 2026-01-13
**Track:** Track 15 of 18
**Dependency:** P5 complete

---

## Objective

Build universe management and strategy exposure dashboards for research and risk monitoring.

**Success looks like:**
- Universe management for dynamic trading universes
- Universe analytics with characteristics
- Net exposure by strategy dashboard

---

## Tasks (3 total)

### T15.1: Universe Management / Selector - HIGH PRIORITY

**Goal:** Define and manage tradable universes dynamically.

**Display:**
```
Universe Manager:
├─ [Default] SP500 (503 symbols) — auto-updated
├─ Liquid Large Cap (287 symbols) — ADV > $10M
├─ Tech + Healthcare (156 symbols) — custom sectors
└─ + Create New Universe

Universe Builder:
├─ Base: [SP500] [Russell 1000] [Custom List]
├─ Filters:
│   └─ ADV > $[10M]
│   └─ Market Cap > $[5B]
│   └─ Sector IN [Tech, Healthcare]
│   └─ Exclude: [TSLA, MEME, ...]
└─ Preview: 156 symbols matching
```

**Acceptance Criteria:**
- [ ] Universe list page functional
- [ ] Filter builder with real-time preview
- [ ] Custom universes saveable
- [ ] Universes selectable in backtest config

**Files:**
- Create: `apps/web_console_ng/pages/universes.py`, `apps/web_console_ng/components/universe_builder.py`, `libs/data/universe_manager.py`

---

### T15.2: Universe Analytics - MEDIUM PRIORITY

**Goal:** Analyze characteristics of trading universes.

**Display:**
```
Universe: Liquid Large Cap (287 symbols)

Characteristics:
├─ Avg Market Cap: $85B
├─ Median ADV: $45M
├─ Sector Distribution: [pie chart]
├─ Beta Distribution: [histogram]
└─ Factor Exposures: [bar chart]
```

**Acceptance Criteria:**
- [ ] Universe statistics calculated
- [ ] Sector distribution (pie chart)
- [ ] Market cap distribution
- [ ] Factor exposures for universe
- [ ] Compare universes side-by-side

**Files:**
- Create: `apps/web_console_ng/components/universe_analytics.py`

---

### T15.3: Net Exposure by Strategy - HIGH PRIORITY

**Goal:** Show per-strategy risk exposure for multi-algo traders.

**Display:**
```
Strategy Exposure Dashboard:
Strategy         | Net Delta | Gross | Long  | Short
─────────────────────────────────────────────────────
Momentum Alpha   | +$125K    | $450K | $287K | $162K
Mean Reversion   | -$50K     | $200K | $75K  | $125K
Stat Arb         | +$5K      | $180K | $92K  | $88K
─────────────────────────────────────────────────────
TOTAL            | +$80K     | $830K | $454K | $375K

⚠️ Warning: Net Long bias across strategies (+9.6%)
```

**Acceptance Criteria:**
- [ ] Exposure breakdown by strategy visible
- [ ] Net/Gross/Long/Short shown per strategy
- [ ] Total across strategies calculated
- [ ] Warning for significant directional bias

**Files:**
- Create: `apps/web_console_ng/components/strategy_exposure.py`

---

## Dependencies

```
T15.1 Universe Manager ──> T15.2 Universe Analytics
                       ──> Backtest Config (universe selection)

T15.3 Net Exposure ──> Dashboard Widget
                   ──> Strategy Risk Dashboard
```

---

## Testing Strategy

### Unit Tests
- Universe filter evaluation
- Exposure calculations

### Integration Tests
- Universe persistence
- Exposure aggregation

### E2E Tests
- Universe builder workflow
- Exposure dashboard display

---

## Definition of Done

- [ ] All 3 tasks implemented
- [ ] Universe management functional
- [ ] Universe analytics working
- [ ] Net exposure dashboard available
- [ ] Unit tests > 85% coverage
- [ ] E2E tests pass
- [ ] Code reviewed and approved

---

**Last Updated:** 2026-01-13
**Status:** TASK
