# Workflow Optimization with Zen MCP

**Status:** ✅ Recommended (Based on Codex Planning Analysis)
**Created:** 2025-10-19
**Purpose:** Optimize progressive commit workflow with automated quality gates

---

## Executive Summary

**Problem:** Current workflow only reviews code AFTER PR creation, missing opportunity to catch issues during development.

**Solution:** **Claude Code implements → zen-mcp reviews → Claude Code fixes → commit**

**Role Model:**
- 🤖 **Claude Code** = Primary contributor (writes code, fixes issues, commits)
- 🔍 **Zen MCP (Codex)** = Reviewer/validator (finds issues, suggests improvements)
- 👤 **Human** = Decision maker (approves PRs, guides direction)

**Impact:**
- **Quality:** Claude Code gets immediate feedback before committing
- **Speed:** Issues caught in seconds, not days
- **Learning:** Claude Code improves by learning from zen-mcp reviews
- **Risk:** Trading safety issues blocked before commit

**ROI:**
- Review: ~30 seconds per commit (Claude asks zen to review)
- Prevents: 10-15 min PR review cycles finding basic issues
- **Net benefit:** 9-14 min saved per issue caught early

---

## Role Model: Who Does What

### Clear Responsibilities

**🤖 Claude Code (Contributor)**
- Writes all code based on user requirements
- Implements features incrementally (commits every 30-60 min)
- **BEFORE each commit:** Asks zen-mcp to review changes
- Fixes issues found by zen-mcp
- Only commits when zen-mcp approves (or user overrides)
- Creates PRs after feature completion

**🔍 Zen MCP / Codex (Reviewer)**
- Reviews Claude Code's implementation
- Finds bugs, safety issues, edge cases
- Provides specific, actionable feedback
- Validates fixes (via continuation_id context)
- Acts as quality gate before commit

**👤 Human (Decision Maker)**
- Assigns tasks to Claude Code
- Reviews PRs (high-level validation)
- Approves architectural decisions
- Can override zen-mcp if needed
- Final merge authority

### Interaction Pattern

```
User                    Claude Code                 Zen MCP (Codex)
  │                          │                            │
  │──"Implement X"──────────>│                            │
  │                          │                            │
  │                          │──[writes code]             │
  │                          │                            │
  │                          │──"Review my code"────────>│
  │                          │                            │
  │                          │<──"Found issue Y"──────────│
  │                          │                            │
  │                          │──[fixes Y]                 │
  │                          │                            │
  │                          │──"Verify fix"───────────>│
  │                          │                            │
  │                          │<──"Looks good"──────────────│
  │                          │                            │
  │                          │──git commit ✅              │
  │                          │                            │
  │<──"Committed! Next?"─────│                            │
  │                          │                            │
```

**Key Insight:** Claude Code stays autonomous but gets safety validation before each commit.

---

## Current Workflow Analysis

### Current State

**Progressive Commits (from GIT_WORKFLOW.md):**
```bash
# Claude Code workflow today:
1. User: "Implement feature X"
2. Claude Code: Writes code for 30-60 minutes
3. Claude Code: git add <files>
4. Claude Code: git commit -m "Add feature X"  # NO quality check
5. Claude Code: Continues coding...
6. Repeat until feature complete
7. Claude Code: Creates PR
8. WAIT for @gemini-code-assist @codex reviews (10-15 min)
9. Claude Code: Fixes issues found
10. Claude Code: Pushes fixes, waits for re-review
11. Repeat until approved
```

**Problem:** Claude Code commits without validation, issues discovered days later in PR review.

**Pain Points:**
- ❌ No quality gates during development (issues accumulate)
- ❌ Bugs discovered late (in PR review, not during commit)
- ❌ Context switching cost (fix issues days after writing code)
- ❌ Wasted review time on trivial issues automated tools catch instantly

### Gap Analysis

| Stage | Current | Missing |
|-------|---------|---------|
| During development | None | Quick safety check |
| Each commit (30-60 min) | None | Automated review |
| Before PR | Tests only | Pre-commit validation |
| PR creation | Manual @mention | Automated request |
| PR review | 10-15 min wait | Real-time feedback |

---

## Recommended Workflow

### Hybrid Approach (Option C)

**Quick safety check on EVERY commit + Deep review at milestones**

**Rationale** (from Codex):
> "At 12–30 seconds per run the zen clink sweep is fast enough to cover the 30–60 minute commit cadence while dramatically lowering the risk of silent regressions in high-impact code."

### Three-Tier Quality Gates

#### Tier 1: Quick Safety Check (Every Commit)

**When:** Before EVERY commit that touches code (not docs-only)

**Duration:** ≤90 seconds

**What:**
1. **Zen clink quick review** (~20s)
   - Trading guardrails (circuit breakers, position limits)
   - Idempotency checks
   - Concurrency issues
   - Logging regressions

2. **Diff-scoped linting** (~20s)
   ```bash
   poetry run ruff check $(git diff --cached --name-only | tr '\n' ' ')
   ```

3. **Focused smoke tests** (~45s)
   ```bash
   poetry run pytest tests/test_quality_gate.py \
     tests/apps/signal_service/test_redis_integration.py -q
   ```

4. **Coverage gate** (cached)
   - No drop >2% on changed files

**Commands:**
```bash
# Automated via pre-commit hook
zen clink review \
  --cli codex \
  --role codereviewer \
  --mode quick \
  --files $(git diff --cached --name-only | tr '\n' ',') \
  --prompt "Review staged trading diff for: order throttles, circuit breakers, position limits, concurrency"

# Saves continuation_id to .cache/zen/last_review for follow-up
```

**Skip policy:**
```bash
# Docs-only commits can skip
git commit -m "Update README #docs-only"
```

#### Tier 2: Deep Review (Milestones)

**When:**
- End of day / component complete
- Before PR creation
- After any medium/high findings in quick check
- Before merging to master

**Duration:** 3-5 minutes

**What:**
```bash
# Full context review with tests and configs
zen clink review \
  --cli codex \
  --role codereviewer \
  --mode full \
  --target commit HEAD \
  --include-tests \
  --prompt-file ./AI/Prompts/deep_trading_safety.md
```

**Includes:**
- All changed files
- Related tests
- Configuration files
- Integration check
- Security audit

#### Tier 3: PR Review (Before Merge)

**When:** After creating PR, before merge

**Duration:** 10-15 min (with automated reviewers)

**What:**
- GitHub App automated reviews (@codex @gemini-code-assist)
- Cross-check findings from Tier 1 + Tier 2
- Human senior review for trading-path changes

---

## Concrete Workflow Examples

### Example 1: Normal Progressive Commit

```
User: "Implement position limit validation in order placer"

Claude Code: [Implements code for 45 minutes]
  - Adds check_position_limits() function
  - Integrates into place_order() endpoint
  - Adds basic error handling

Claude Code: "Before I commit, let me ask zen-mcp to review for trading safety issues"

Claude Code → zen clink:
  "Review apps/execution_gateway/order_placer.py for:
   - Circuit breaker checks
   - Position limit logic correctness
   - Error handling completeness"

Zen MCP (Codex): ✅ "Good implementation. Minor suggestions:
  - Add logging for limit violations
  - Consider edge case: position=0
  Otherwise looks safe to commit."

Claude Code: [Adds logging and edge case handling]

Claude Code: git add apps/execution_gateway/order_placer.py
Claude Code: git commit -m "Add position limit validation with logging"

Claude Code: "Committed. Ready for next component."
```

**Time:** 30 seconds for review, immediate fix, clean commit

### Example 2: Zen Review Catches Critical Issue

```
User: "Add order submission logic to execution gateway"

Claude Code: [Implements order placement code]
  - Adds submit_order() function
  - Validates order parameters
  - Calls Alpaca API

Claude Code: "Let me ask zen-mcp to review before committing"

Claude Code → zen clink:
  "Review apps/execution_gateway/order_placer.py for trading safety"

Zen MCP (Codex): ❌ "CRITICAL ISSUE:
  Missing circuit breaker check before order submission (line 42).
  This could place orders when risk limits are breached!

  Required fix:
  ```python
  if self.breaker.is_tripped():
      raise CircuitBreakerTripped('Cannot place order: breaker TRIPPED')
  ```"

Claude Code: "Good catch! Let me fix that immediately."

Claude Code: [Adds circuit breaker check]

Claude Code → zen clink (with continuation_id):
  "I've added the circuit breaker check. Please verify the fix."

Zen MCP (Codex): ✅ "Circuit breaker check added correctly.
  Placement: Perfect - before API call.
  No remaining blockers. Safe to commit."

Claude Code: git add apps/execution_gateway/order_placer.py
Claude Code: git commit -m "Add order submission with circuit breaker check"

Claude Code: "Issue caught and fixed in 2 minutes, before it entered the codebase!"
```

**Time Saved:**
- Without zen review: Bug found in PR review (10-15 min later)
- With zen review: Fixed immediately (2 min)
- **Net savings: 8-13 min + critical bug prevented**

### Example 3: Milestone Deep Review Before PR

```
User: "Complete the position limit feature and create a PR"

Claude Code: [Has made 6 progressive commits over 3 hours]
  ✅ Commit 1: Add position limit config
  ✅ Commit 2: Add check_position_limits() function
  ✅ Commit 3: Integrate into order placer
  ✅ Commit 4: Add unit tests
  ✅ Commit 5: Add integration tests
  ✅ Commit 6: Update documentation

Claude Code: "Feature complete! Before creating PR, let me ask zen-mcp for a comprehensive review."

Claude Code → zen clink:
  "Perform deep review of all changes in this branch for:
   - Overall architecture
   - Test coverage
   - Edge cases
   - Integration points
   - Documentation completeness"

Zen MCP (Codex): [3-minute comprehensive review]
  ✅ "Overall implementation solid. Found:
  - HIGH: 0 (all caught in progressive reviews)
  - MEDIUM: 2 (edge case: position=None, concurrent updates)
  - LOW: 1 (could use better variable name)

  Recommendations:
  1. Handle None position case in check_position_limits()
  2. Add test for concurrent position updates
  3. Rename 'pos' to 'current_position' for clarity"

Claude Code: "Thanks! Let me address those before creating the PR."

Claude Code: [Fixes 3 issues]

Claude Code → zen clink (continuation):
  "I've addressed all three issues. Please verify."

Zen MCP (Codex): ✅ "All issues resolved. Clean implementation ready for PR."

Claude Code: git add apps/execution_gateway/order_placer.py tests/
Claude Code: git commit -m "Handle edge cases and improve readability"

Claude Code: gh pr create --title "Implement position limit validation"

Claude Code: "PR created! Should be quick to merge since zen-mcp validated everything."
```

**PR Review is FAST because:**
- Progressive zen reviews caught basic issues (not in PR)
- Deep zen review caught logic issues (fixed before PR)
- Human reviewers only validate high-level decisions

---

## Implementation Guide

### Phase 1: Setup (1 hour)

**1. Install Pre-Commit Hook**

Create `.git/hooks/pre-commit`:
```bash
#!/bin/bash
# Pre-commit quality gate
# Runs zen clink quick review on every commit

# Check if this is a docs-only commit
if git log -1 --pretty=%B | grep -q "#docs-only"; then
  echo "Skipping quality gate for docs-only commit"
  exit 0
fi

# Get staged files
STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM | grep '\.py$')

if [ -z "$STAGED_FILES" ]; then
  echo "No Python files staged, skipping review"
  exit 0
fi

echo "🔍 Running quick safety check..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 1. Zen clink quick review
echo "1/4: Zen MCP review (trading safety)..."
zen clink review \
  --cli codex \
  --role codereviewer \
  --mode quick \
  --files "$(echo $STAGED_FILES | tr '\n' ',')" \
  --prompt "Review for: circuit breakers, position limits, idempotency, concurrency"

if [ $? -ne 0 ]; then
  echo "❌ Zen review found blocking issues"
  echo "Fix issues and try again, or use --no-verify to skip (not recommended)"
  exit 1
fi

# 2. Diff-scoped linting
echo "2/4: Linting changed files..."
poetry run ruff check $STAGED_FILES
if [ $? -ne 0 ]; then
  echo "❌ Linting failed"
  exit 1
fi

# 3. Smoke tests
echo "3/4: Running smoke tests..."
poetry run pytest tests/test_quality_gate.py \
  tests/apps/signal_service/test_redis_integration.py -q
if [ $? -ne 0 ]; then
  echo "❌ Smoke tests failed"
  exit 1
fi

# 4. Coverage gate
echo "4/4: Checking coverage..."
# TODO: Implement diff coverage check

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Quick safety check passed"
exit 0
```

```bash
# Make executable
chmod +x .git/hooks/pre-commit
```

**2. Create Deep Review Prompt**

Create `./AI/Prompts/deep_trading_safety.md`:
```markdown
# Deep Trading Safety Review

You are reviewing code for a production algorithmic trading platform.

## Critical Focus Areas

### 1. Order Execution Safety
- Circuit breaker checks before EVERY order placement
- Idempotent order IDs (deterministic, no duplicates)
- Position limit validation (per-symbol and portfolio-wide)
- DRY_RUN mode handling
- Risk check failures must block orders

### 2. Data Quality
- Freshness checks (<30 min old)
- Corporate action adjustments applied
- Quality gate outlier detection
- Survivorship bias prevention

### 3. Concurrency & Race Conditions
- Redis WATCH/MULTI/EXEC for state transitions
- Idempotent retries
- No check-then-act patterns
- Atomic operations for critical state

### 4. Monitoring & Observability
- Structured logging with context (strategy_id, client_order_id)
- Metrics to Prometheus
- Never swallow exceptions
- Alert on circuit breaker trips

### 5. Testing
- Unit tests for business logic
- Integration tests for external APIs
- Edge cases covered (staleness, network errors, broker errors)
- Backtest replay parity

## Review Output

For each issue found:
- **Severity**: CRITICAL / HIGH / MEDIUM / LOW
- **Location**: File:line
- **Issue**: What's wrong
- **Impact**: How it affects trading safety
- **Fix**: Specific recommendation

## Example

**Severity**: HIGH
**Location**: apps/execution_gateway/order_placer.py:42
**Issue**: Missing circuit breaker check before order submission
**Impact**: Could place orders when breaker is TRIPPED, violating risk limits
**Fix**: Add `if breaker.is_tripped(): raise CircuitBreakerTripped()` before line 45
```

### Phase 2: Pilot (2 weeks)

**Week 1:**
- Enable hooks for execution & risk teams only
- Gather metrics:
  - Median quick-check duration
  - Number of issues caught pre-PR
  - False positive rate
  - Developer feedback

**Week 2:**
- Adjust prompts based on false positives
- Optimize smoke test selection
- Refine coverage thresholds
- Document common issue patterns

**Success Criteria:**
- ✅ Median quick-check < 90 seconds
- ✅ No severity-1 issues bypass gates
- ✅ False positive rate < 10%
- ✅ Developer satisfaction ≥ 7/10

### Phase 3: Rollout (Ongoing)

**Expand to all teams:**
- Update CLAUDE.md with new workflow
- Update GIT_WORKFLOW.md with gate requirements
- Add CI guard ensuring PRs reference successful quick checks
- Weekly metrics review in ops sync

---

## Monitoring & Metrics

### Key Metrics

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Quick check duration | ≤90s median | Log hook execution time |
| Issues caught pre-PR | ≥80% of total | Compare quick check findings vs PR findings |
| False positive rate | <10% | Developer skip rate + feedback |
| Coverage delta | No drop >2% | Diff coverage tool |
| Developer satisfaction | ≥7/10 | Weekly survey |

### Dashboard

```bash
# Weekly report
./scripts/workflow_metrics.py --week-of 2025-10-19

# Output:
# Quick Check Performance:
# - Median duration: 78s ✅
# - 95th percentile: 110s ⚠️
# - Commits checked: 142
# - Commits skipped (#docs-only): 23
#
# Issues Found:
# - HIGH: 8 (6 in quick check, 2 in deep review) ✅
# - MEDIUM: 15 (12 in quick check, 3 in deep review) ✅
# - LOW: 22 (18 in quick check, 4 in deep review) ✅
# - Pre-PR catch rate: 86% ✅
#
# Developer Experience:
# - False positives: 4 (2.8%) ✅
# - Average satisfaction: 8.2/10 ✅
# - Most common issue: Circuit breaker checks (47%)
```

---

## Benefits

### Quantitative

| Benefit | Before | After | Improvement |
|---------|--------|-------|-------------|
| Time to find issues | 10-15 min (PR) | 90s (commit) | 89-92% faster |
| Issues per PR | 5-10 | 1-2 | 70-90% reduction |
| PR review cycles | 2-3 | 1 | 50-66% reduction |
| Context preserved | Low (days later) | High (immediate) | N/A |
| False positives | N/A | <10% | Acceptable |

### Qualitative

**Developer Experience:**
- ✅ Immediate feedback (not days later)
- ✅ Context preserved (fix while coding)
- ✅ Confidence in commits (passed safety gate)
- ✅ Fewer PR review cycles
- ✅ Learn trading safety patterns immediately

**Code Quality:**
- ✅ Fewer bugs reach PR review
- ✅ Trading safety enforced at every commit
- ✅ Consistent quality standards
- ✅ Knowledge transfer (prompts document best practices)

**Business Impact:**
- ✅ Reduced risk of money-losing bugs
- ✅ Faster development cycles
- ✅ Higher quality releases
- ✅ Better audit trail (every commit validated)

---

## Risks & Mitigations

### Risk 1: Developer Fatigue

**Risk:** Mandatory checks every 30-60 minutes slow down development

**Mitigation:**
- Automate via git hooks (zero manual effort)
- Target <90s total duration (acceptable for 30-60 min cadence)
- Allow #docs-only skip for documentation changes
- Show metrics: "Saved 12 min by catching issue early"

### Risk 2: False Positives

**Risk:** Quick check flags non-issues, frustrating developers

**Mitigation:**
- Pilot with small team first, refine prompts
- Target <10% false positive rate
- Allow --no-verify override (with justification)
- Weekly prompt tuning based on feedback

### Risk 3: Coverage Gate Noise

**Risk:** Flaky smoke tests or coverage fluctuations

**Mitigation:**
- Stabilize smoke test suite (quarantine flaky tests)
- Allow 2% coverage variance (not strict enforcement)
- Cache coverage results per commit
- Use diff coverage (only check changed files)

### Risk 4: Zen MCP Dependency

**Risk:** zen-mcp server downtime blocks commits

**Mitigation:**
- Fall back to offline checks (lint + tests only) if zen unavailable
- Cache last review for retries
- Allow --no-verify with mandatory PR review

---

## Next Steps

**Immediate (Today):**
1. ✅ Create pre-commit hook script
2. ✅ Create deep review prompt
3. ⬜ Test hook on sample commit
4. ⬜ Document in CLAUDE.md

**This Week:**
1. ⬜ Pilot with 2-3 developers
2. ⬜ Gather metrics
3. ⬜ Refine prompts based on feedback
4. ⬜ Update GIT_WORKFLOW.md

**Next Week:**
1. ⬜ Expand to all teams
2. ⬜ Add CI enforcement
3. ⬜ Create metrics dashboard
4. ⬜ Weekly review in ops sync

---

## References

- **Planning Source:** Codex analysis via zen clink (continuation_id: 8c08024e-58a1-44d4-a913-b7b039848527)
- **Current Workflow:** docs/STANDARDS/GIT_WORKFLOW.md
- **Testing Standards:** docs/STANDARDS/TESTING.md
- **Zen MCP Integration:** docs/IMPLEMENTATION_GUIDES/zen-mcp-integration-proposal.md

**Status:** ✅ Recommended for Adoption
**Owner:** Development Team
**Created:** 2025-10-19
**Last Updated:** 2025-10-19
