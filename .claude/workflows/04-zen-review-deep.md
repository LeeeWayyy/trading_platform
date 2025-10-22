# Deep Review Workflow (Clink + Gemini → Codex)

**Purpose:** Comprehensive review of all branch changes before creating pull request (MANDATORY quality gate)
**Tool:** clink + gemini planner → codex planner (Tier 2 review, multi-phase)
**Prerequisites:** Feature complete, all progressive commits done, local tests passing
**Expected Outcome:** Branch validated for architecture, testing, edge cases; ready for PR creation
**Owner:** @development-team
**Last Reviewed:** 2025-10-21

---

## When to Use This Workflow

**MANDATORY before creating ANY pull request:**
- ✅ After completing all progressive commits for a feature
- ✅ Before running `gh pr create`
- ✅ After all tests pass locally
- ✅ When feature/fix is ready for team review

**This is different from quick review:**
- **Quick review (03):** Individual commits, ~30 seconds, safety focus
- **Deep review (04):** All branch changes, ~3-5 minutes, comprehensive analysis

**Even if all quick reviews passed:**
- Integration issues between commits
- Architecture patterns across changes
- Test coverage completeness
- Edge cases in combined logic
- Documentation quality

---

## Step-by-Step Process

### 1. Verify Feature Completion

**Check that feature is actually complete:**
- [ ] All requirements from ticket implemented
- [ ] All progressive commits completed
- [ ] All quick zen reviews passed
- [ ] Local tests passing
- [ ] Documentation updated

**If incomplete:**
- Finish implementation first
- OR create as draft PR
- Don't request deep review for WIP

### 2. Review Your Branch Changes

```bash
# See all commits in your branch
git log master..HEAD --oneline

# See all file changes
git diff master..HEAD --stat

# Review actual changes
git diff master..HEAD
```

**What this does:** Gives you context on what zen will review

**Quick self-check:**
- Are there obvious issues?
- Did you forget to commit something?
- Are there debug/temp files?
- Does the diff make sense?

### 3. Ensure Clean Working Tree

```bash
git status
```

**Expected:** "nothing to commit, working tree clean"

**If uncommitted changes:**
```bash
# Commit them first using progressive commit workflow
# See 01-git-commit.md
```

### 4. Run Full Test Suite

```bash
make test
make lint
```

**Expected:** ✅ All tests pass, no lint errors

**If failures:**
- Fix issues first
- Don't request deep review until green
- Deep review assumes tests pass

### 5. Request Deep Review (Two-Phase: Gemini → Codex)

**Phase 1: Architecture Analysis (Gemini Planner)**
```
"Please review all branch changes using clink + gemini planner.
Analyze architecture, scalability, trading safety, and code quality.
Compare master..HEAD for this feature branch."
```

**Alternative (use review prompt):**
```
"Use the deep-architecture-review prompt from .claude/prompts/clink-reviews/
to review all branch changes from master to HEAD"
```

**What happens in Phase 1:**
- Claude uses clink with gemini CLI planner role (gemini-2.5-pro, 1M context)
- Gemini analyzes ALL changes since branching from master
- Comprehensive review: architecture, safety, scalability, maintainability
- Review takes ~2-3 minutes
- Returns detailed findings WITH continuation_id

**Phase 2: Recommendations Synthesis (Codex)**
```
"Now use clink + codex planner with the same continuation_id
to synthesize recommendations and create an action plan"
```

**What happens in Phase 2:**
- Claude uses clink with codex CLI (preserves continuation_id context)
- Codex synthesizes gemini's findings into actionable plan
- Prioritizes fixes, suggests testing strategy, identifies documentation needs
- Takes ~1-2 minutes
- Total review time: ~3-5 minutes across both phases

### 6. Review Comprehensive Findings

**Zen-mcp deep review covers:**

**1. Architecture & Design:**
- Overall design patterns
- Service boundaries
- Code organization
- Coupling and cohesion

**2. Code Quality:**
- Type safety
- Error handling completeness
- Logging sufficiency
- Code duplication

**3. Trading Safety:**
- Circuit breaker integration
- Idempotency patterns
- Position limit logic
- Risk check completeness

**4. Testing:**
- Unit test coverage
- Integration test coverage
- Edge cases covered
- Test quality (mocking, assertions)

**5. Documentation:**
- Docstrings complete
- ADR if needed
- Concept docs if needed
- Implementation guide updates

**6. Integration Points:**
- API contract compatibility
- Database schema changes
- Service dependencies
- External API usage

**Example deep review output:**
```
🔍 Deep Zen-MCP Review Results
Continuation ID: deep-abc123-def456-ghi789

═══════════════════════════════════════════════════
SUMMARY
═══════════════════════════════════════════════════
Files Changed: 8
Lines Added: 487
Lines Removed: 42
Commits: 6

Overall Assessment: APPROVED with 3 MEDIUM issues to address

═══════════════════════════════════════════════════
ARCHITECTURE & DESIGN
═══════════════════════════════════════════════════
✅ Service boundary well-defined
✅ No circular dependencies
✅ Follows existing patterns
ℹ️  Suggestion: Consider extracting position logic to separate module

═══════════════════════════════════════════════════
TRADING SAFETY
═══════════════════════════════════════════════════
✅ Circuit breaker checked in all paths
✅ Idempotent order IDs used correctly
⚠️  MEDIUM (apps/execution_gateway/order_placer.py:156):
    Edge case: What if position is exactly at limit?
    Current: Rejects order
    Consider: Should >= vs > depend on order direction?

═══════════════════════════════════════════════════
TESTING
═══════════════════════════════════════════════════
Test Coverage: 95% (target: 80%) ✅
Unit Tests: 15 new tests ✅
Integration Tests: 5 new tests ✅

⚠️  MEDIUM: Missing test for concurrent position updates
    Scenario: Two orders for same symbol at same time
    Risk: Race condition in position calculation
    Add: tests/apps/execution_gateway/test_concurrent_orders.py

═══════════════════════════════════════════════════
DOCUMENTATION
═══════════════════════════════════════════════════
✅ All functions have docstrings
✅ ADR created (ADR-0011)
⚠️  MEDIUM: Concept doc incomplete
    /docs/CONCEPTS/risk-management.md has TODOs
    Complete before PR

═══════════════════════════════════════════════════
INTEGRATION POINTS
═══════════════════════════════════════════════════
✅ No breaking API changes
✅ Database migration included
✅ OpenAPI spec updated

═══════════════════════════════════════════════════
ISSUES TO ADDRESS
═══════════════════════════════════════════════════
Total: 3 MEDIUM, 0 HIGH, 0 CRITICAL

1. MEDIUM: Edge case in position limit check
   File: apps/execution_gateway/order_placer.py:156
   Action: Clarify >= vs > logic

2. MEDIUM: Missing concurrent update test
   File: tests/ (new file needed)
   Action: Add test_concurrent_position_updates.py

3. MEDIUM: Complete concept documentation
   File: docs/CONCEPTS/risk-management.md
   Action: Fill in TODO sections

═══════════════════════════════════════════════════
RECOMMENDATION
═══════════════════════════════════════════════════
Address 3 MEDIUM issues before creating PR.
Estimated time: 30-45 minutes

After fixes, re-request verification:
"I've fixed the 3 issues, please verify"

Continuation ID for follow-up: deep-abc123-def456-ghi789
```

### 7. Address ALL Findings

**Priority order:**

**HIGH/CRITICAL (Blocking - MUST fix):**
- Fix immediately
- No exceptions
- PR creation blocked until fixed

**MEDIUM (Required unless explicitly deferred):**
- Fix if straightforward (<30 min total)
- OR document deferral with user approval
- Examples: Missing tests, incomplete docs, edge cases

**LOW (Optional):**
- Fix if trivial
- OR note for future improvement
- Don't block PR on LOW issues

### 8. Fix Issues and Re-Request Verification

**After fixing issues:**

```bash
# Implement fixes
# Run tests to verify
make test && make lint

# Commit fixes
git add <files>
git commit -m "Address deep review findings

- Clarify position limit edge case (>= vs >)
- Add concurrent position update test
- Complete risk management concept doc

Zen deep review: 3 MEDIUM issues fixed"

# Re-request verification (zen remembers via continuation_id)
"I've fixed the 3 MEDIUM issues from deep review, please verify"
```

**Zen will:**
- Use continuation_id to remember previous findings
- Check if issues actually fixed
- Verify fixes are correct
- Give final approval

### 9. Get Final Approval

**Zen will say:**

✅ **"All issues resolved. Branch ready for PR creation."**
- Proceed to create PR!
- Include zen review summary in PR description

⚠️ **"Issue X not fully resolved"**
- Fix remaining issues
- Re-request verification

**Expected final output:**
```
✅ APPROVED FOR PR CREATION

All findings addressed:
1. Position limit edge case: ✅ Fixed correctly
2. Concurrent update test: ✅ Added comprehensive test
3. Concept documentation: ✅ Completed all sections

Branch is ready for pull request.

Summary for PR description:
- 6 commits reviewed
- 3 MEDIUM issues found and fixed
- 0 HIGH/CRITICAL issues
- Test coverage: 95%
- Architecture: Sound
- Documentation: Complete

Continuation ID: deep-abc123-def456-ghi789
Include this summary in PR description.
```

### 10. Proceed to PR Creation

**Copy zen review summary for PR description:**
```bash
gh pr create --title "Add position limit validation (P0T5)"
# (Include zen review summary in PR body)
# See 02-git-pr.md for full PR workflow
```

---

## Decision Points

### Should I defer MEDIUM issues?

**Fix immediately if:**
- Quick to fix (<30 min total)
- Safety-related
- Test coverage gaps
- Documentation is incomplete

**Can defer if:**
- Requires separate investigation
- Out of scope for this PR
- User explicitly approves
- Created follow-up task

**Document deferral:**
```markdown
## Deferred Issues from Zen Deep Review

1. **MEDIUM:** Performance optimization for position queries
   - Reason: Requires profiling and index analysis
   - Follow-up: Created ticket P1.5T3
   - User approved: @username
```

### Deep review taking too long?

**Normal:** 3-5 minutes
**Acceptable:** Up to 10 minutes (complex changes)
**Too long:** > 15 minutes

**If taking > 15 minutes:**
- Large PR (maybe split into smaller PRs)
- Network issues
- Zen server overloaded
- Try again later

### Zen found many issues - should I fix all?

**Yes for HIGH/CRITICAL**
- Non-negotiable
- PR blocked until fixed

**Fix MEDIUM if reasonable**
- Most MEDIUM issues should be fixed
- Total fix time should be < 1 hour
- If > 1 hour, discuss scope with user

**Document LOW issues**
- Create follow-up tasks
- Note in PR for future cleanup
- Don't block PR

### Should I split this into multiple PRs?

**Consider splitting if:**
- Zen review finds > 10 issues
- Changes span multiple unrelated features
- Some changes are controversial
- PR diff > 1000 lines

**Benefits of splitting:**
- Easier to review
- Faster to merge
- Lower risk
- Clearer history

---

## Common Issues & Solutions

### Issue: Deep Review Finds Issues Quick Reviews Missed

**Symptom:** Quick reviews passed, but deep review finds problems

**This is normal!** Deep review has broader context:
- Integration between commits
- Cross-file patterns
- Overall architecture
- Test coverage across feature
- Documentation completeness

**Solution:**
- Fix the issues found
- Learn patterns for next feature
- Quick reviews catch most issues (70-80%)
- Deep review catches integration issues (20-30%)

### Issue: Unclear If Issue Applies to My Changes

**Symptom:** Zen flags something in code you didn't modify

**Solution:**
```
"Zen flagged issue in file X, but I didn't modify that file.
Is this a pre-existing issue or related to my changes?"

Zen will clarify:
- "This is pre-existing, safe to ignore for this PR"
- OR "Your changes interact with this code, please review"
```

**For pre-existing issues:**
- Note in PR: "Found pre-existing issue in X, created ticket Y"
- Don't fix in current PR (scope creep)
- Create follow-up task

### Issue: Can't Reproduce Issue Zen Found

**Symptom:** Zen reports bug, but tests pass

**Solution:**
```
"Zen reported issue with [X], but tests pass.
Can you provide a concrete example or test case that demonstrates the problem?"

Zen will provide:
- Specific scenario that triggers issue
- Test case to reproduce
- OR clarify it's potential issue, not current bug
```

### Issue: Disagree With Architecture Suggestion

**Symptom:** Zen suggests architecture change you don't agree with

**Solution:**
```
"Zen suggested [architecture change], but I think current approach is better because [reasons].
Can we discuss this?"
```

**Then involve user:**
```
User: "What did zen suggest?"
Claude: "Zen suggests [X] to improve [Y], but current approach does [Z]. What's your preference?"
User decides: Keep current OR Make change
```

**Architecture is collaborative!** Zen provides suggestions, user/team decides.

### Issue: Too Many Issues to Fix Now

**Symptom:** Deep review found 15+ issues

**This suggests:**
- PR is too large (split it)
- Skipped progressive reviews (go back to 30-60 min cadence)
- Foundational issues (might need architectural discussion)

**Solution:**
1. Discuss with user: "Zen found 15 issues. Should we:"
   - Fix all now (might take 2-3 hours)
   - Split into multiple smaller PRs
   - Defer some MEDIUM/LOW issues
   - Revisit architecture

2. Most common: Split into smaller PRs
   - Part 1: Core functionality (5 issues to fix)
   - Part 2: Additional features (10 issues to fix later)

---

## Examples

### Example 1: Clean Deep Review

```bash
# Feature complete with 4 progressive commits

$ git status
On branch feature/position-limits
nothing to commit, working tree clean

$ make test && make lint
===================== 58 passed in 2.82s ======================
✅ All checks passed

$ "Review all branch changes using clink + gemini planner. Compare master..HEAD."

Claude: [Uses clink with gemini CLI planner - Phase 1]

# (2-3 minutes later)

Gemini: "**Architecture Assessment**
- 4 commits, 287 lines changed
- Clean microservice boundaries ✓
- Trading safety requirements met ✓
- Comprehensive test coverage (97%)

**Findings**
- LOW – Validation logic could be extracted to separate class
- LOW – Performance benchmarks would be helpful

**Positives**
- Circuit breaker integration correct
- Idempotency preserved
- Position limit logic sound

<SUMMARY>Excellent implementation, ready for PR.</SUMMARY>

continuation_id: deep-abc123"

$ "Now use clink + codex planner with continuation_id: deep-abc123 to synthesize recommendations"

Claude: [Uses clink with codex CLI - Phase 2, preserves context]

# (1-2 minutes later)

Codex: "**Priority Recommendations**
All CRITICAL/HIGH: ✅ None
Medium: ✅ None
Low: 2 suggestions (defer to follow-up)

**Next Steps**
1. Create PR (ready to merge)
2. Create follow-up tasks for LOW suggestions
3. Include deep review summary in PR description

continuation_id: deep-abc123"

$ # Proceed to create PR
$ gh pr create
# (Include review summary and continuation_id)
```

### Example 2: Issues Found and Fixed

```bash
$ "Review all branch changes using clink + gemini planner. Compare master..HEAD."

Claude: [Uses clink with gemini CLI planner - Phase 1]

# (2-3 minutes later)

Gemini: "**Critical Findings**
(none)

**High Priority**
(none)

**Medium Priority**
- Missing test for edge case (position at exactly limit)
- Incomplete error handling in concurrent scenario
- Concept doc has TODO sections

<SUMMARY>Needs fixes before PR - 3 MEDIUM issues.</SUMMARY>

continuation_id: deep-xyz789"

$ "Now use clink + codex planner with continuation_id: deep-xyz789 to synthesize recommendations"

Claude: [Uses clink with codex CLI - Phase 2]

Codex: "**Priority Recommendations**
Fix before PR:
1. Add edge case test (15 min)
2. Add error handling for concurrent scenario (20 min)
3. Complete concept doc TODOs (10 min)

Estimated fix time: 45 minutes
Then re-request verification.

continuation_id: deep-xyz789"

# Fix all 3 issues (35 minutes)
# Add edge case test
# Improve error handling
# Complete documentation

$ git add tests/ apps/ docs/
$ git commit -m "Address deep review findings

- Add test for position-at-limit edge case
- Handle concurrent position updates with locks
- Complete risk management concept doc

Codex deep review: 3 MEDIUM issues fixed (continuation_id: deep-xyz789)"

$ "I've fixed all 3 issues, please verify (continuation_id: deep-xyz789)"

Claude: [Uses clink with codex, preserves context]

Codex: "✅ Verification complete

1. Edge case test: ✅ Added, covers scenario
2. Concurrent handling: ✅ Fixed with proper locking
3. Documentation: ✅ All TODOs completed

<SUMMARY>All issues resolved. Ready for PR!</SUMMARY>

continuation_id: deep-xyz789"

$ gh pr create
# (Include zen review + fix summary)
```

### Example 3: Deferring Some Issues

```bash
$ "Review all branch changes using clink + gemini planner. Compare master..HEAD."

Claude: [Uses clink with gemini CLI planner - Phase 1]

# (2-3 minutes later)

Gemini: "**Critical Findings**
(none)

**High Priority**
- Circuit breaker check missing in rollback path

**Medium Priority**
- Performance: N+1 query in position loading
- Missing logging in error scenarios
- Concept doc could be more detailed

<SUMMARY>Fix HIGH before PR. MEDIUM require discussion.</SUMMARY>

continuation_id: deep-pqr456"

$ "Now use clink + codex planner with continuation_id: deep-pqr456 to synthesize recommendations"

Claude: [Uses clink with codex CLI - Phase 2]

# (1-2 minutes later)

Codex: "**Priority Recommendations**
MUST FIX: Circuit breaker in rollback (20 min)
Consider: Logging (quick, 10 min)
Defer: Performance optimization (needs analysis)
Defer: Concept doc enhancement (nice-to-have)

**Next Steps**
1. Fix circuit breaker immediately
2. Add logging if time permits
3. Create follow-up tasks for deferred items

continuation_id: deep-pqr456"

# Fix HIGH immediately
$ git add apps/execution_gateway/rollback.py
$ git commit -m "Add circuit breaker check to rollback path (continuation_id: deep-pqr456)"

# Also fix logging (quick)
$ git add apps/execution_gateway/
$ git commit -m "Add error logging per codex review (continuation_id: deep-pqr456)"

$ "I fixed HIGH (circuit breaker) and one MEDIUM (logging).
Deferring performance optimization to ticket P1.5T3 (needs analysis).
Concept doc is sufficient for now.
Please verify fixes (continuation_id: deep-pqr456)"

Claude: [Uses clink with codex, preserves context]

Codex: "✅ Verification complete

1. Circuit breaker: ✅ Fixed correctly in rollback path
2. Logging: ✅ Added with proper context
3. Performance: Deferred with ticket created ✅
4. Concept doc: Current version acceptable ✅

<SUMMARY>Ready for PR with deferred items documented.</SUMMARY>

continuation_id: deep-pqr456"

$ gh pr create
# (Include review summary + document deferred performance optimization)
```

---

## Validation

**How to verify this workflow succeeded:**
- [ ] Deep zen review completed (3-5 minutes)
- [ ] ALL HIGH/CRITICAL issues fixed
- [ ] MEDIUM issues fixed or explicitly deferred with approval
- [ ] Zen explicitly approved for PR creation
- [ ] Continuation ID captured for PR description
- [ ] Review summary ready to include in PR

**What to check if something seems wrong:**
- Verify zen-mcp server is running
- Check that you're comparing against correct base branch (master)
- Confirm all commits are pushed
- Look for explicit "Ready for PR" statement from zen
- Check that continuation ID was provided

---

## Related Workflows

- [02-git-pr.md](./02-git-pr.md) - Creating PR after deep review approval
- [03-zen-review-quick.md](./03-zen-review-quick.md) - Quick reviews during development
- [01-git-commit.md](./01-git-commit.md) - Progressive commits before deep review
- [05-testing.md](./05-testing.md) - Running tests before deep review
- [08-adr-creation.md](./08-adr-creation.md) - Creating ADR if zen suggests it

---

## References

**Standards & Policies:**
- [/docs/STANDARDS/GIT_WORKFLOW.md](../../docs/STANDARDS/GIT_WORKFLOW.md) - Deep review requirements
- [/docs/STANDARDS/TESTING.md](../../docs/STANDARDS/TESTING.md) - Test coverage standards

**Implementation Details:**
- [/CLAUDE.md](../../CLAUDE.md#🤖-zen-mcp--clink-integration) - Clink + zen-mcp integration overview
- [/docs/CONCEPTS/zen-mcp-clink-optimization-proposal.md](../../docs/CONCEPTS/zen-mcp-clink-optimization-proposal.md) - Clink-based workflow design
- [.claude/prompts/clink-reviews/deep-architecture-review.md](../prompts/clink-reviews/deep-architecture-review.md) - Deep review prompt template

**Deep Review Coverage:**
- Architecture patterns and design quality
- Trading safety (circuit breakers, idempotency, limits)
- Test coverage and quality (unit, integration, edge cases)
- Documentation completeness (docstrings, ADRs, concepts)
- Integration points (API, DB, services)
- Performance and scalability concerns
- Security considerations

---

**Maintenance Notes:**
- Update when deep review criteria change
- Review when new architectural patterns added
- Adjust if review time exceeds 10 minutes regularly
- Notify @development-team + zen-mcp maintainers for substantial changes
- Quarterly review of effectiveness metrics
