# P6 Planning: Professional Trading Terminal

**Phase:** P6
**Status:** Planning Complete
**Dependency:** P5 (NiceGUI Migration) complete
**Created:** 2026-01-13
**Last Updated:** 2026-01-13

---

## Executive Summary

Transform the web console from an "Admin Panel" into a "Professional Trading Terminal" with trader-focused UX patterns, advanced execution capabilities, and comprehensive research tools.

**Total Tasks:** 70 tasks across 18 tracks (3-5 tasks per track for manageable PRs)

---

## Track Overview

| Track | Title | Tasks | Priority | Dependencies |
|-------|-------|-------|----------|--------------|
| **P6T1** | Core Infrastructure | 4 | P0 | P5 |
| **P6T2** | Header & Status Bar | 5 | P0 | P6T1 |
| **P6T3** | Notifications & Hotkeys | 4 | P0 | P6T1 |
| **P6T4** | Order Entry Context | 4 | P0 | P6T1, P6T2, P6T3 |
| **P6T5** | Grid Enhancements | 4 | P1 | P6T1 |
| **P6T6** | Advanced Orders | 4 | P0 | P6T1, P6T2, P6T4 |
| **P6T7** | Order Actions | 4 | P0 | P6T2, P6T6 |
| **P6T8** | Execution Analytics | 3 | P1 | P6T6 |
| **P6T9** | Cost Model & Capacity | 4 | P1 | P5 |
| **P6T10** | Quantile & Attribution | 4 | P1 | P5 |
| **P6T11** | Walk-Forward & Parameters | 4 | P1 | P5 |
| **P6T12** | Backtest Tools | 4 | P1 | P5, P6T9 |
| **P6T13** | Data Infrastructure | 4 | P1 | P5 |
| **P6T14** | Data Services | 4 | P2 | P6T13 |
| **P6T15** | Universe & Exposure | 3 | P1 | P5 |
| **P6T16** | Admin Pages | 3 | P2 | P5 |
| **P6T17** | Strategy & Models | 3 | P2 | P5 |
| **P6T18** | Documentation & QA | 4 | P2 | P6T1-T17 |

---

## Dependency Graph

```
                              P5 (NiceGUI Migration)
                                       │
        ┌──────────────────────────────┼──────────────────────────────┐
        │                              │                              │
        ▼                              ▼                              ▼
    ┌───────┐                      ┌───────┐                      ┌───────┐
    │ P6T1  │                      │ P6T9  │                      │ P6T13 │
    │ Core  │                      │ Cost  │                      │ Data  │
    └───┬───┘                      └───┬───┘                      └───┬───┘
        │                              │                              │
   ┌────┼────┬────────┐               ▼                              ▼
   │    │    │        │           ┌───────┐                      ┌───────┐
   ▼    ▼    ▼        ▼           │ P6T12 │                      │ P6T14 │
┌────┐┌────┐┌────┐┌────┐          │Backtest│                     │ Data  │
│T2  ││T3  ││T5  ││T10 │          │ Tools │                      │Service│
│Hdr ││Ntfy││Grid││Quan│          └───────┘                      └───────┘
└─┬──┘└─┬──┘└────┘└────┘
  │     │
  ▼     ▼
┌─────────┐
│  P6T4   │
│Order Ent│
└────┬────┘
     │
     ▼
┌─────────┐
│  P6T6   │
│Adv Order│
└────┬────┘
     │
┌────┴────┐
▼         ▼
┌────┐  ┌────┐
│T7  │  │T8  │
│Acts│  │TCA │
└────┘  └────┘

Parallel Tracks (can run concurrently):
- P6T9-T12 (Research Platform)
- P6T13-T14 (Data Infrastructure)
- P6T15 (Universe & Exposure)
- P6T16-T17 (Admin)

P6T18 (Docs & QA) runs last after all others complete
```

---

## Track Details

### Trading Terminal Core (Tracks 1-8)

#### P6T1: Core Infrastructure (4 tasks)
Foundation for all other tracks.
- T1.1: Update Throttling & Batching (FOUNDATION)
- T1.2: Dark Mode Implementation
- T1.3: High-Density Trading Layout
- T1.4: Workspace Persistence

#### P6T2: Header & Status Bar (5 tasks)
Critical trading information always visible.
- T2.1: Net Liquidation Value (NLV) Display
- T2.2: Connection Latency Indicator
- T2.3: Connection Status & Graceful Degradation
- T2.4: Kill Switch "Panic Button" UX
- T2.5: Market Clock & Session State

#### P6T3: Notifications & Hotkeys (4 tasks)
Professional trading workflows.
- T3.1: Notification Center / Quiet Mode
- T3.2: Keyboard Hotkeys
- T3.3: State Feedback Loops
- T3.4: Cell Flash Updates

#### P6T4: Order Entry Context (4 tasks)
Order entry with market context.
- T4.1: Always-Visible Order Ticket
- T4.2: Real-Time Market Context
- T4.3: TradingView Chart Integration
- T4.4: Watchlist Component

#### P6T5: Grid Enhancements (4 tasks)
Advanced AG Grid features.
- T5.1: Hierarchical Order Blotter
- T5.2: Tabbed Positions/Orders Panel
- T5.3: DOM/Level 2 Order Book
- T5.4: Position Sparklines

#### P6T6: Advanced Orders (4 tasks)
Expose backend execution capabilities.
- T6.1: Advanced Order Types (stop, stop-limit)
- T6.2: TWAP Execution Controls
- T6.3: Fat Finger Pre-Validation
- T6.4: Order Modification

#### P6T7: Order Actions (4 tasks)
Critical order management actions.
- T7.1: Flatten Strategy/Symbol Button
- T7.2: Cancel All Orders Button
- T7.3: Order Replay/Duplicate
- T7.4: One-Click Trading (Shift+Click)

#### P6T8: Execution Analytics (3 tasks)
Execution quality and audit.
- T8.1: Execution Quality (TCA) Dashboard
- T8.2: Order Entry Audit Trail
- T8.3: CSV Export on All Grids

---

### Research Platform (Tracks 9-12)

#### P6T9: Cost Model & Capacity (4 tasks)
Realistic cost simulation.
- T9.1: Transaction Cost Model
- T9.2: Gross vs Net P&L Toggle
- T9.3: Turnover & Capacity Analysis
- T9.4: Backtest Timeseries Export

#### P6T10: Quantile & Attribution (4 tasks)
Signal validation analytics.
- T10.1: Quantile Tear Sheets
- T10.2: Factor Attribution
- T10.3: Drawdown Underwater Chart
- T10.4: Monthly Returns Heatmap

#### P6T11: Walk-Forward & Parameters (4 tasks)
Strategy robustness validation.
- T11.1: Walk-Forward Visualization
- T11.2: Parameter Stability Heatmap
- T11.3: Decay Curve Visualization
- T11.4: Alpha Cluster Map

#### P6T12: Backtest Tools (4 tasks)
Advanced backtest capabilities.
- T12.1: Config as Code (JSON Editor)
- T12.2: Backtest Comparison Mode
- T12.3: Live vs Backtest Overlay
- T12.4: Data Health Widget

---

### Data Infrastructure (Tracks 13-15)

#### P6T13: Data Infrastructure (4 tasks)
Data validation and monitoring.
- T13.1: Point-in-Time Data Inspector
- T13.2: Data Coverage Heatmap
- T13.3: Wire Data Management Services
- T13.4: Data Quality Dashboard

#### P6T14: Data Services (4 tasks)
Data service UIs.
- T14.1: SQL Explorer (Validated)
- T14.2: Data Source Status
- T14.3: Feature Store Browser
- T14.4: Shadow Mode Results

#### P6T15: Universe & Exposure (3 tasks)
Universe management and risk.
- T15.1: Universe Management / Selector
- T15.2: Universe Analytics
- T15.3: Net Exposure by Strategy

---

### Admin & Documentation (Tracks 16-18)

#### P6T16: Admin Pages (3 tasks)
Missing admin functionality.
- T16.1: Tax Lot Management
- T16.2: User Management / RBAC Admin
- T16.3: API Key Revoke/Rotate

#### P6T17: Strategy & Models (3 tasks)
Strategy and model management.
- T17.1: Strategy Management
- T17.2: Model Registry Browser
- T17.3: Alert Configuration UI

#### P6T18: Documentation & QA (4 tasks)
Documentation and testing.
- T18.1: P6 Architecture Decision Record
- T18.2: Trading Terminal UX Guide
- T18.3: Research Platform Guide
- T18.4: Comprehensive Testing

---

## Implementation Order

### Phase 1: Foundation (P6T1-T4)
Sequential - must complete in order.
1. P6T1: Core Infrastructure (foundation for all)
2. P6T2: Header & Status Bar (kill switch, connection gating)
3. P6T3: Notifications & Hotkeys (UX foundation)
4. P6T4: Order Entry Context (order ticket)

### Phase 2: Trading Features (P6T5-T8)
Can overlap with Phase 3.
5. P6T5: Grid Enhancements (hierarchical blotter for TWAP)
6. P6T6: Advanced Orders (stop, TWAP, fat finger)
7. P6T7: Order Actions (flatten, one-click)
8. P6T8: Execution Analytics (TCA, audit, export)

### Phase 3: Research Platform (P6T9-T12)
Can run in parallel with Phase 2.
9. P6T9: Cost Model & Capacity
10. P6T10: Quantile & Attribution
11. P6T11: Walk-Forward & Parameters
12. P6T12: Backtest Tools

### Phase 4: Data & Admin (P6T13-T17)
Can run in parallel with Phase 2-3.
13. P6T13: Data Infrastructure
14. P6T14: Data Services
15. P6T15: Universe & Exposure
16. P6T16: Admin Pages
17. P6T17: Strategy & Models

### Phase 5: Documentation (P6T18)
After all other tracks complete.
18. P6T18: Documentation & QA

---

## Success Criteria

### Trading Terminal
- [ ] Dark mode with surface levels and semantic color consistency
- [ ] Information density 3x current (no scroll for primary view)
- [ ] NLV and leverage ratio always visible in header
- [ ] Connection latency indicator (ping) in header
- [ ] State feedback on all trading buttons
- [ ] Workspace persistence (survives refresh)
- [ ] Notification center with quiet mode
- [ ] Market clock showing session state

### Execution
- [ ] Advanced order types accessible (stop, stop-limit, TWAP)
- [ ] One-click trading (Shift+Click for instant orders)
- [ ] Fat finger validation pre-submission
- [ ] DOM/Level 2 view for market depth
- [ ] CSV export on all data grids

### Research Platform
- [ ] Transaction costs in backtests
- [ ] Quantile analysis for signal validation
- [ ] Live vs backtest overlay for alpha decay detection
- [ ] Data health widget for feature freshness
- [ ] Capacity analysis for strategy sizing

### Data & Admin
- [ ] Net exposure by strategy dashboard
- [ ] Universe management for dynamic universes
- [ ] Tax lots page functional
- [ ] User management page functional

---

## Performance Targets

| Metric | Target |
|--------|--------|
| Order submission UI | < 50ms |
| State feedback visible | < 50ms after click |
| Cell flash updates | < 100ms |
| Chart render | < 200ms |
| Hotkey response | < 30ms |
| AG Grid updates | 30-60fps (no freeze) |
| Latency indicator updates | Every 5s |
| Workspace state save | < 100ms |

---

## Task File Index

| Track | File |
|-------|------|
| P6T1 | [P6T1_TASK.md](P6T1_TASK.md) |
| P6T2 | [P6T2_TASK.md](P6T2_TASK.md) |
| P6T3 | [P6T3_TASK.md](P6T3_TASK.md) |
| P6T4 | [P6T4_TASK.md](P6T4_TASK.md) |
| P6T5 | [P6T5_TASK.md](P6T5_TASK.md) |
| P6T6 | [P6T6_TASK.md](P6T6_TASK.md) |
| P6T7 | [P6T7_TASK.md](P6T7_TASK.md) |
| P6T8 | [P6T8_TASK.md](P6T8_TASK.md) |
| P6T9 | [P6T9_TASK.md](P6T9_TASK.md) |
| P6T10 | [P6T10_TASK.md](P6T10_TASK.md) |
| P6T11 | [P6T11_TASK.md](P6T11_TASK.md) |
| P6T12 | [P6T12_TASK.md](P6T12_TASK.md) |
| P6T13 | [P6T13_TASK.md](P6T13_TASK.md) |
| P6T14 | [P6T14_TASK.md](P6T14_TASK.md) |
| P6T15 | [P6T15_TASK.md](P6T15_TASK.md) |
| P6T16 | [P6T16_TASK.md](P6T16_TASK.md) |
| P6T17 | [P6T17_TASK.md](P6T17_TASK.md) |
| P6T18 | [P6T18_TASK.md](P6T18_TASK.md) |

---

**Last Updated:** 2026-01-13
**Status:** Planning Complete - Ready for Implementation
