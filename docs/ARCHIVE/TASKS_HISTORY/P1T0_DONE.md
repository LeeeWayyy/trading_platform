---
id: P1T0
title: "Enhanced P&L Calculation"
phase: P1
task: T0
priority: HIGH
owner: "@development-team"
state: DONE
created: 2025-10-20
started: 2025-10-26
completed: 2025-10-26
dependencies: [P0T4]
estimated_effort: "3-5 days"
related_adrs: [ADR-0008]
related_docs: [pnl-calculation.md]
features: []
---

# P1T0: Enhanced P&L Calculation

**Phase:** P1 (Hardening & Automation, 46-90 days)
**Status:** DONE (Completed Oct 26, 2025)
**Priority:** HIGH
**Owner:** @development-team
**Created:** 2025-10-20
**Estimated Effort:** 3-5 days

---

## Naming Convention

**This task:** `P1T0_TASK.md` → `P1T0_PROGRESS.md` → `P1T0_DONE.md`

**If this task has multiple features/sub-components:**
- Feature 0: `P1T0-F0_PROGRESS.md` (separate tracking for complex features)
- Feature 1: `P1T0-F1_PROGRESS.md`

**Where:**
- **Px** = Phase (P1 = MVP/0-45 days, P1 = Hardening/46-90 days, P2 = Advanced/91-120 days)
- **Ty** = Task number within phase (T0, T1, T2, ...)
- **Fz** = Feature/sub-component within task (F0, F1, F2, ...)

---

## Objective

Replace notional P&L with comprehensive realized/unrealized breakdown for accurate performance tracking.

**Current State (P0):**
- `paper_run.py` calculates total notional value only
- Validates order sizing but doesn't track actual profit/loss
- No distinction between realized (closed) and unrealized (open) positions

**Success looks like:**
- Complete P&L calculation with realized/unrealized breakdown
- Fee tracking and tax implications calculated
- JSON export with detailed P&L components
- Tests verify P&L accuracy across scenarios

---

## Acceptance Criteria

- [ ] **AC1:** Calculate realized P&L from closed positions
- [ ] **AC2:** Calculate unrealized P&L (mark-to-market) on open positions
- [ ] **AC3:** Track fee breakdown (commission + other fees)
- [ ] **AC4:** JSON export includes complete P&L structure
- [ ] **AC5:** Unit tests cover multi-day, partial fills, buy-sell sequences
- [ ] **AC6:** Integration test validates P&L against known scenarios

---

## Approach

### High-Level Plan

1. **Define P&L data structure** - Create Pydantic models for P&L components
2. **Implement calculation logic** - Add functions for realized/unrealized P&L
3. **Update paper_run.py** - Replace notional with complete P&L calculation
4. **Update JSON export** - Include full P&L structure in output
5. **Add comprehensive tests** - Cover all P&L scenarios
6. **Update documentation** - Add examples and formulas
7. **Verify with historical data** - Validate against known scenarios

### Logical Components

**Component 1: P&L Data Models**
- Create Pydantic models for realized/unrealized/total P&L
- Define fee breakdown structure
- Add tests for model validation
- Request zen-mcp review & commit

**Component 2: Realized P&L Calculation**
- Implement FIFO-based cost basis tracking
- Calculate realized gains on position closes
- Track fees per transaction
- Add unit tests for buy-sell sequences
- Request zen-mcp review & commit

**Component 3: Unrealized P&L Calculation**
- Implement mark-to-market for open positions
- Handle partial fills correctly
- Add unit tests for open positions
- Request zen-mcp review & commit

**Component 4: Integration & Export**
- Update paper_run.py to use new P&L system
- Export complete P&L to JSON
- Add integration tests
- Request zen-mcp review & commit

---

## Technical Details

### Files to Modify/Create
- `libs/common/pnl_models.py` - NEW: Pydantic models for P&L structure
- `libs/common/pnl_calculator.py` - NEW: Realized/unrealized P&L calculation logic
- `scripts/paper_run.py` - MODIFY: Replace notional P&L with complete calculation
- `tests/unit/test_pnl_calculator.py` - NEW: Unit tests for P&L logic
- `tests/integration/test_paper_run_pnl.py` - NEW: End-to-end P&L validation

### APIs/Contracts
- No API changes (internal calculation only)
- JSON export structure will change (backward compatible field addition)

### Database Changes
- No database changes required (P&L calculated on-demand from position data)

---

## Dependencies

**Blockers (must complete before starting):**
- P0T4: Execution Gateway - Provides position tracking data required for P&L calculation

**Nice-to-have (can start without):**
- None

**Blocks (other tasks waiting on this):**
- None (standalone enhancement)

---

## Risks & Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| [Risk description] | High/Med/Low | High/Med/Low | [How to mitigate] |

---

## Testing Strategy

### Test Coverage Needed
- **Unit tests:** [What to test]
- **Integration tests:** [What to test]
- **E2E tests:** [What scenarios]

### Manual Testing
- [ ] Test case 1
- [ ] Test case 2

---

## Documentation Requirements

### Must Create/Update
- [ ] ADR if architectural change (`./AI/Workflows/08-adr-creation.md`)
- [ ] Concept doc in `/docs/CONCEPTS/` if trading-specific
- [ ] API spec in `/docs/API/` if endpoint changes
- [ ] Database schema in `/docs/DB/` if schema changes

### Must Update
- [ ] `/docs/GETTING_STARTED/REPO_MAP.md` if structure changes
- [ ] `/docs/GETTING_STARTED/PROJECT_STATUS.md` when complete

---

## Related

**ADRs:**
- [ADR-0008: Enhanced P&L Calculation](../../ADRs/0008-enhanced-pnl-calculation.md) - Architecture for realized/unrealized P&L

**Documentation:**
- [P&L Calculation Concept](../../CONCEPTS/pnl-calculation.md) - Explains notional, realized, unrealized P&L
- [P1 Planning](../../TASKS/P1_PLANNING.md) - Full P1 task breakdown

**Tasks:**
- Depends on: [P0T4: Execution Gateway](./P0T4_DONE.md) - Position tracking
- Blocks: None

---

## Notes

[Any additional context, links, or considerations]

---

## State Transition Instructions

**When starting this task:**

```bash
# 1. Rename file
git mv docs/TASKS/P1T0_TASK.md docs/TASKS/P1T0_PROGRESS.md

# 2. Update front matter in P1T0_PROGRESS.md:
#    state: PROGRESS
#    started: 2025-10-20

# 3. Commit
git add docs/TASKS/P1T0_PROGRESS.md
git commit -m "Start P1T0: Task Title"
```

**Or use automation:**
```bash
./scripts/tasks.py start P1T0
```
