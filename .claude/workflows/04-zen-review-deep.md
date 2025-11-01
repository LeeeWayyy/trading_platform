# Deep Review Workflow

**Purpose:** Comprehensive review of all branch changes before creating pull request (MANDATORY quality gate)
**When:** Before creating ANY pull request (~3-5 minutes)
**Prerequisites:** Feature complete, all progressive commits done, local tests passing
**Expected Outcome:** Branch validated for architecture, testing, edge cases; ready for PR creation

---

## Quick Reference

**Clink Policy:** See [Clink-Only Tool Usage Policy](./_common/clink-policy.md)
**Zen Review:** See [Zen-MCP Review Process](./_common/zen-review-process.md) - Tier 2 (Deep Review)
**Git Commands:** See [Git Commands Reference](./_common/git-commands.md)
**Testing:** See [Test Commands Reference](./_common/test-commands.md)

---

## When to Use This Workflow

**MANDATORY before creating ANY pull request:**
- ✅ After completing all progressive commits for a feature
- ✅ Before running `gh pr create`
- ✅ After all tests pass locally
- ✅ When feature/fix is ready for team review

**This is different from quick review:**
- **Quick review (03):** Individual commits, ~2-3 minutes, two-phase safety focus
- **Deep review (04):** All branch changes, ~3-5 minutes, comprehensive two-phase analysis

**Even if all quick reviews passed, deep review catches:**
- Integration issues between commits
- Architecture patterns across changes
- Test coverage completeness
- Edge cases in combined logic
- Documentation quality

**See Also:** [Zen-MCP Review Process](./_common/zen-review-process.md) for complete Tier 2 workflow details

---

## Step-by-Step Process

### 1. Verify Feature Completion

**Check that feature is actually complete:**
- [ ] All requirements from ticket implemented
- [ ] All progressive commits completed
- [ ] All quick zen reviews passed
- [ ] Local tests passing (`make ci-local`)
- [ ] Documentation updated

**If incomplete:**
- Finish implementation first OR create as draft PR
- Don't request deep review for WIP

### 2. Review Your Branch Changes

```bash
git log master..HEAD --oneline
git diff master..HEAD --stat
```

**Quick self-check:**
- Are there obvious issues?
- Did you forget to commit something?
- Are there debug/temp files?

### 3. Request Deep Review

**Simple request:**
```
"Review all branch changes with zen-mcp deep review (master..HEAD)"
```

**Or more specific:**
```
"Deep review my feature branch for architecture, testing, and edge cases"
```

**Two-phase process (gemini → codex):**
1. Phase 1: Gemini analyzes architecture, safety, scalability (~2-3 min)
2. Phase 2: Codex synthesizes action plan, prioritizes fixes (~1-2 min)
3. Total: ~3-5 minutes

See [Zen-MCP Review Process](./_common/zen-review-process.md) for detailed two-phase Tier 2 workflow.

### 4. Review Comprehensive Findings

**Deep review covers:**
1. Architecture & Design
2. Code Quality (type safety, error handling, logging)
3. Trading Safety (circuit breakers, idempotency, position limits)
4. Testing (coverage, edge cases, quality)
5. Documentation (docstrings, ADRs, concept docs)
6. Integration Points (API contracts, schema changes)

**Severity levels:**
- **HIGH/CRITICAL:** ❌ MUST fix before PR
- **MEDIUM:** ⚠️ MUST fix OR document deferral
- **LOW:** ℹ️ Fix if time permits OR note for future

### 5. Address Findings

**Fix HIGH/CRITICAL immediately:**
```bash
# Make fixes
git add <files>
"I've fixed [issue], please verify"
```

**MEDIUM issues - fix or defer:**
- Fix if straightforward (<30 min total)
- OR document deferral with justification

**LOW issues:**
- Fix if trivial
- OR create follow-up task

### 6. Get Final Approval

**Zen will provide:**
- ✅ "Approved - Ready for PR" OR
- ⚠️ "Fix [issues] before PR" OR
- ℹ️ "Approved with [n] deferred issues"

**Include continuation_id in PR description for audit trail**

### 7. Proceed to PR Creation

See [02-git-pr.md](./02-git-pr.md) for PR creation workflow.

---

## Decision Points

### Should I defer MEDIUM issues?

**Defer if:**
- Requires separate investigation (>30 min)
- Out of scope for current feature
- User approves deferral

**Document deferral:**
```markdown
## Deferred Issues from Zen Deep Review
1. **MEDIUM:** Optimize query in `get_positions()`
   - Reason: Requires profiling analysis
   - Follow-up: Created task P1T15
   - Zen continuation_id: abc123-def456
```

### Should I split this into multiple PRs?

**Split if:**
- Zen suggests feature too large
- Multiple independent concerns
- >500 lines changed

**Keep together if:**
- Changes are tightly coupled
- Feature requires all pieces
- Already implemented and tested

---

## Common Issues & Solutions

### Issue: Deep Review Finds Issues Quick Reviews Missed

**Why:** Quick reviews focus on individual commits; deep review sees integration

**Solution:**
- Fix the issues (this is working as designed!)
- Consider splitting large features earlier next time

### Issue: Too Many Issues to Fix Now

**Solution:**
```
"Zen found [n] issues. Which are blocking for PR vs. can be deferred?"

[Get prioritized list]

"I'll fix HIGH/CRITICAL and top 3 MEDIUM. Can I defer the rest?"
```

**Document all deferrals in PR description**

---

## Examples

### Example: Clean Deep Review

```bash
$ git log master..HEAD --oneline
abc1234 Add position limits
def5678 Add circuit breaker integration
ghi9012 Update documentation

$ "Deep review all branch changes (master..HEAD)"

# Two-phase review (gemini → codex)
# Phase 1: Gemini comprehensive analysis
# Phase 2: Codex synthesis
# Result: ✅ Approved - Ready for PR
# Continuation ID: deep-abc123-def456

$ # Include in PR description:
  # Zen deep review: Approved
  # Continuation-id: deep-abc123-def456
```

### Example: Issues Found and Fixed

```bash
$ "Deep review my feature branch"

# Phase 1: Gemini finds 2 CRITICAL, 3 MEDIUM issues
# Phase 2: Codex prioritizes fixes

# Fix CRITICAL issues
$ git add <files>
$ "I've fixed the CRITICAL issues, please verify"

# Codex verifies fixes
# Result: ⚠️ 3 MEDIUM issues remain

# Fix 2 MEDIUM, defer 1
$ "Fixed 2 MEDIUM issues. Deferring logging enhancement (requires infrastructure). OK?"

# User approves
# Result: ✅ Approved with 1 deferred issue

$ # Include in PR:
  # Zen deep review: Approved with deferral
  # Deferred: Logging enhancement (see PR description)
  # Continuation-id: deep-xyz789-abc123
```

---

## Validation

**How to verify this workflow succeeded:**
- [ ] Deep zen-mcp review requested and completed
- [ ] ALL HIGH/CRITICAL issues fixed
- [ ] MEDIUM issues fixed or explicitly deferred
- [ ] Zen explicitly approved for PR
- [ ] Continuation ID captured for PR description

**What to check if something seems wrong:**
- Verify all tests pass (`make ci-local`)
- Check branch is up to date with master
- Confirm no uncommitted changes
- Look for explicit "ready for PR" statement

---

## Related Workflows

- [02-git-pr.md](./02-git-pr.md) - Creating pull requests (use this next)
- [03-zen-review-quick.md](./03-zen-review-quick.md) - Quick pre-commit reviews
- [01-git-commit.md](./01-git-commit.md) - Progressive commits

---

## References

- [Zen-MCP Review Process](./_common/zen-review-process.md) - Complete Tier 2 review details
- [Clink-Only Tool Usage Policy](./_common/clink-policy.md) - Tool usage requirements
- [Git Commands Reference](./_common/git-commands.md) - Git operations
- [Test Commands Reference](./_common/test-commands.md) - Testing commands
- [/docs/STANDARDS/GIT_WORKFLOW.md](../../docs/STANDARDS/GIT_WORKFLOW.md) - Review policy
- [/docs/STANDARDS/TESTING.md](../../docs/STANDARDS/TESTING.md) - Test requirements
