# Zen-MCP Review Workflows

**Purpose:** Two-tier review system for code quality and trading safety validation
**Tools:** zen-mcp via clink (gemini → codex two-phase reviews)
**Policy:** See [Clink-Only Tool Usage](./_common/clink-policy.md) and [Zen-MCP Review Process](./_common/zen-review-process.md)

---

## Two-Tier Review System

| Tier | When | Duration | Scope | Phase Pattern |
|------|------|----------|-------|---------------|
| **Tier 1: Quick Review** | Before EVERY commit | ~2-3 min | Staged changes (safety focus) | Gemini → Codex |
| **Tier 2: Deep Review** | Before creating PR | ~3-5 min | All branch changes (comprehensive) | Gemini → Codex |

**Two-phase process (both tiers):**
1. **Phase 1 (Gemini):** Comprehensive analysis (~1-2 min for quick, ~2-3 min for deep)
2. **Phase 2 (Codex):** Synthesis and validation (~30-60 sec for quick, ~1-2 min for deep)

**Total overhead:** ~5% of development time for massive safety benefit

---

## Tier 1: Quick Review (Pre-Commit)

**MANDATORY before EVERY commit that touches code**

### When to Use

**✅ Required for:**
- Every progressive commit (30-60 min intervals)
- Any trading logic, order placement, risk checks
- Circuit breaker code, data handling, API endpoints

**🚫 Can skip only for:**
- Auto-generated files (package-lock.json, poetry.lock)
- Documentation-only changes (mark with `#docs-only`)
- Emergency hotfixes (with user approval + mandatory post-commit review)

### Quick Review Process

```bash
# 1. Stage changes
git add <files>
git status
git diff --cached

# 2. Request review
"Review my staged changes with zen-mcp quick review"

# 3. Fix HIGH/CRITICAL immediately (context is fresh!)
git add <fixed-files>
"I've fixed [issue], please verify"

# 4. Get approval and commit
git commit -m "feat: Add feature

zen-mcp-review: approved
continuation-id: abc123-def456"
```

### Severity Handling

| Severity | Action |
|----------|--------|
| **HIGH/CRITICAL** | ❌ MUST fix before commit (non-negotiable) |
| **MEDIUM** | ⚠️ Fix if <5 min OR defer with justification |
| **LOW** | ℹ️ Fix if trivial OR note in TODO |

**Don't spend >15 min total on review fixes**

---

## Tier 2: Deep Review (Pre-PR)

**MANDATORY before creating ANY pull request**

### When to Use

**✅ Required for:**
- Feature complete, all progressive commits done
- All quick reviews passed, local tests passing
- Before running `gh pr create`

**Why both tiers?**
- Quick reviews catch per-commit issues
- Deep review catches integration issues, architecture patterns, test coverage gaps

### Deep Review Process

```bash
# 1. Verify completion
make ci-local  # All tests pass
git log master..HEAD --oneline  # Review commits

# 2. Request deep review
"Review all branch changes with zen-mcp deep review (master..HEAD)"

# 3. Fix HIGH/CRITICAL immediately
git add <files>
"I've fixed [issue], please verify"

# 4. Get approval
# Result: ✅ "Approved - Ready for PR"
# Include continuation_id in PR description
```

### Deep Review Scope

1. Architecture & Design
2. Code Quality (types, errors, logging)
3. Trading Safety (breakers, idempotency, limits)
4. Testing (coverage, edge cases)
5. Documentation (docstrings, ADRs)
6. Integration (API contracts, schemas)

**Fix or defer:** HIGH/CRITICAL must fix; MEDIUM fix if <30 min OR defer with justification; LOW create follow-up task

---

## Decision Points

### Should I skip review?

**Quick review:**
- ✅ Skip for docs-only (`git commit -m "... #docs-only"`)
- ❌ NEVER skip for trading logic, order code, risk checks

**Deep review:**
- ❌ NEVER skip before PR
- Even if all quick reviews passed, integration issues can exist

### Review taking too long?

**Normal:** 2-3 min (quick), 3-5 min (deep)
**Too long:** > 6 minutes

**If timeout:**
1. Check zen-mcp server status
2. Check network connection
3. Try single-phase (codex only) as fallback
4. See "Server Unavailable" below

### Too many issues to fix?

**Quick review:**
- Fix ALL HIGH/CRITICAL
- Fix MEDIUM if <5 min each
- Don't spend >15 min total

**Deep review:**
- Fix ALL HIGH/CRITICAL
- Fix top MEDIUM issues (<30 min total)
- Defer rest with justification in PR description

### Should I defer MEDIUM issues?

**Defer if:**
- Requires separate investigation (>30 min)
- Out of scope for current feature
- User approves deferral

**Document deferral:**
```markdown
## Deferred from Zen Review
- **MEDIUM:** Optimize query in get_positions()
- Reason: Requires profiling analysis
- Follow-up: Task P1T15
- Continuation-id: abc123
```

---

## Common Issues

### Zen-MCP Server Unavailable

**Emergency override (with user approval ONLY):**
```bash
git commit -m "feat: Add feature

ZEN_REVIEW_OVERRIDE: Server unavailable
Reason: Critical bugfix
Will request post-commit review ASAP"
```

**Then:**
- Document in team chat
- Request review when server returns
- Create follow-up PR if issues found

### Can't Tell If Approved

**Ask explicitly:**
```
"Zen, confirm: is this code approved for commit, or are there blocking issues?"
```

**Look for:**
- ✅ "Safe to commit" / "Approved" / "Ready for PR"
- ❌ "Fix [X] before committing" / "Blocking issue"

### Don't Understand Issue

**Ask for details:**
```
"Zen, explain why [issue] is a problem and provide a code example of the fix"
```

### Disagreement with Zen

**Discuss, don't ignore:**
```
"I think zen's assessment might be incorrect because [reason]. Can you explain the concern?"
```

**Then ask user:**
```
User: "What did zen say?"
Claude: "Zen flagged [X] as HIGH because [Y]. Should I fix or override?"
User: "Fix it" OR "Override with justification"
```

**Don't silently ignore feedback!**

### False Positive

**Verify and document:**
```
"Zen flagged [issue], but it's actually handled at line [N]. Can you verify this is a false positive?"

# If confirmed false positive, document for future improvements
```

---

## Examples

### Example: Quick Review - Clean Approval

```bash
$ git add apps/execution_gateway/order_placer.py
$ "Quick review my staged changes"

# Two-phase: Gemini → Codex
# Result: ✅ Approved

$ git commit -m "feat: Add position validation

zen-mcp-review: approved
continuation-id: abc123"
```

### Example: Quick Review - Critical Issue Found

```bash
$ "Quick review my staged changes"

# Gemini finds CRITICAL: missing circuit breaker
# Codex confirms: DO NOT commit

# Fix immediately
$ git add apps/execution_gateway/order_placer.py
$ "I've added circuit breaker check, verify"

# Codex verifies
# Result: ✅ Fixed - Safe to commit

$ git commit -m "feat: Add position validation

- Implement check_position_limits()
- Add circuit breaker (zen critical fix)

zen-mcp-review: approved (critical issue fixed)
continuation-id: abc123"
```

### Example: Deep Review - Issues Found

```bash
$ "Deep review all branch changes (master..HEAD)"

# Gemini finds: 2 CRITICAL, 3 MEDIUM
# Codex prioritizes fixes

# Fix CRITICAL
$ git add <files>
$ "Fixed CRITICAL issues, verify"

# Fix 2 MEDIUM, defer 1
$ "Fixed 2 MEDIUM. Deferring logging (needs infrastructure). OK?"

# User approves
# Result: ✅ Approved with 1 deferred issue

# Include in PR description:
  Zen deep review: Approved with deferral
  Deferred: Logging enhancement (requires P1T15)
  Continuation-id: xyz789
```

---

## Validation Checklist

**Quick review succeeded:**
- [ ] Review requested and completed (~2-3 min)
- [ ] ALL HIGH/CRITICAL issues fixed
- [ ] MEDIUM fixed or explicitly deferred
- [ ] Explicit "safe to commit" approval
- [ ] Continuation ID in commit message

**Deep review succeeded:**
- [ ] Review requested and completed (~3-5 min)
- [ ] ALL HIGH/CRITICAL issues fixed
- [ ] MEDIUM fixed or deferred with justification
- [ ] Explicit "ready for PR" approval
- [ ] Continuation ID in PR description
- [ ] All tests pass (`make ci-local`)

---

## See Also

- [Zen-MCP Review Process](./_common/zen-review-process.md) - Complete tier details
- [Clink-Only Tool Usage](./_common/clink-policy.md) - Tool policy
- [01-git.md](./01-git.md) - Progressive commits (uses quick review)
- [01-git.md](./01-git.md) - Creating PRs (uses deep review)
- [/docs/STANDARDS/GIT_WORKFLOW.md](../../docs/STANDARDS/GIT_WORKFLOW.md) - Review requirements
