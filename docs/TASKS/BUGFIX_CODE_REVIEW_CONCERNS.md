# Task: Address Code Review Concerns

**Branch:** `bugfix/address-code-review-concerns`
**Priority:** High
**Estimated Effort:** 8-12 hours

## Overview

Address 5 valid concerns identified from comprehensive code review analysis.

## Components

1. **Stale Slice Expiry** - Add `STALE_SLICE_EXPIRY_SECONDS` to cancel old slices during recovery
2. **X-Internal-Token Auth** - Add HMAC validation to auth middleware
3. **RecoveryManager** - Encapsulate 4-component recovery orchestration
4. **Strategy Status API** - Add backend API and UI for strategy status
5. **Constraint Relaxation** - Add hierarchical constraint relaxation to optimizer

## Acceptance Criteria

- [ ] All 5 components implemented with tests
- [ ] All tests pass (`make ci-local`)
- [ ] Code reviewed by Gemini and Codex via zen-mcp
- [ ] No security regressions
- [ ] Backward compatible (existing behavior preserved by default)

## Related Documents

- Plan: `/Users/leeewayyy/.claude/plans/frolicking-launching-ember.md`
