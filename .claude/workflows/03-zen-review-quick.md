# Quick Review Workflow

**Purpose:** Two-phase safety check of staged changes before each commit (MANDATORY quality gate)
**When:** Before EVERY commit (~2-3 minutes)
**Prerequisites:** Changes staged with `git add`, ready to commit
**Expected Outcome:** Code validated for trading safety issues, approved for commit or issues identified for fixing

---

## Quick Reference

**Clink Policy:** See [Clink-Only Tool Usage Policy](./_common/clink-policy.md)
**Zen Review:** See [Zen-MCP Review Process](./_common/zen-review-process.md) - Tier 1 (Quick Review)

---

## When to Use This Workflow

**MANDATORY before EVERY commit that touches code:**
- ✅ Before each progressive commit (every 30-60 min)
- ✅ After implementing any trading-related logic
- ✅ Before committing fixes to review feedback
- ✅ After modifying order placement, risk checks, or data handling

**Can skip only for:**
- 🔧 Auto-generated files (package-lock.json, poetry.lock)
- 🚨 Emergency hotfixes (with explicit user approval + mandatory post-commit review)

**Frequency:** ~2-3 minutes every 30-60 minutes = **~5% of development time** for massive safety benefit

**See Also:** [Zen-MCP Review Process](./_common/zen-review-process.md) for complete Tier 1 review details

---

## Step-by-Step Process

### 1. Stage Your Changes

```bash
git add <files you want to commit>
git status
git diff --cached
```

### 2. Request Quick Review

**Simple request:**
```
"Review my staged changes with zen-mcp quick review"
```

**Or specify files:**
```
"Quick review apps/execution_gateway/order_placer.py"
```

**Two-phase process (gemini → codex):**
1. Phase 1: Gemini analyzes safety, architecture, quality (~1-2 min)
2. Phase 2: Codex synthesizes recommendations (~30-60 sec)
3. Total: ~2-3 minutes

See [Zen-MCP Review Process](./_common/zen-review-process.md) for detailed two-phase workflow.

### 3. Review the Findings

**Severity levels:**
- **HIGH/CRITICAL:** ❌ MUST fix before committing
- **MEDIUM:** ⚠️ MUST fix OR document deferral
- **LOW:** ℹ️ Fix if time permits

**Zen will provide:**
- Detailed findings with file:line references
- Impact assessment
- Concrete fix suggestions
- continuation_id for follow-up

### 4. Fix HIGH/CRITICAL Issues Immediately

**If HIGH or CRITICAL issues found:**

1. **Fix the code immediately** (context is fresh!)
2. **Stage the fixes:**
   ```bash
   git add <fixed files>
   ```
3. **Request verification** (zen remembers context via continuation_id):
   ```
   "I've fixed the [issue description], please verify"
   ```
4. **Wait for approval** before proceeding

**Do NOT:**
- ❌ Commit without fixing HIGH/CRITICAL
- ❌ Defer critical safety issues
- ❌ Override without user approval

### 5. Handle MEDIUM Issues

**Fix if straightforward (<5 min):**
```bash
# Make the fix
# Stage it
git add <file>

# Request verification
"I've added the logging, please verify"
```

**Document deferral if complex:**
```
"Zen found MEDIUM issue about logging. I'm deferring this because:
- Requires logging infrastructure not yet built
- Will address in follow-up commit after logging setup
- Not a safety issue, just operational improvement

User: Is this deferral acceptable?"
```

### 6. Note LOW Issues

**Fix if trivial:**
- Variable renaming
- Comment improvements
- Minor style issues

**Or note for future cleanup:**
- Add TODO comment
- Create follow-up task
- Don't block on LOW issues

### 7. Get Final Approval

**Zen-mcp will say one of:**

✅ **"All issues resolved, safe to commit"**
- Proceed to commit!

⚠️ **"Fix [issue] before committing"**
- Go back to step 4

ℹ️ **"MEDIUM/LOW issues noted, approved with deferral"**
- Document deferral in commit message
- Proceed to commit

### 8. Proceed to Commit

**After zen approval:**
```bash
git commit -m "Add position limit validation

- Implement check_position_limits() function
- Add circuit breaker integration
- Add logging for limit violations (zen review)

Zen-review: Approved (continuation_id: abc123-def456)"
```

**Include in commit message:**
- Summary of zen review
- Continuation ID (for audit trail)
- Note any deferred issues

---

## Decision Points

### Should I skip zen review?

**✅ Skip ONLY if:**
- Documentation-only changes (no code)
- Auto-generated files
- Emergency hotfix with user approval

**❌ Never skip for:**
- Any trading logic
- Order placement code
- Risk checks
- Circuit breaker code
- Data handling
- API endpoints

**If skipping docs-only:**
```bash
git commit -m "Update README with setup instructions #docs-only"
```

### Zen review taking too long?

**Normal:** 2-3 minutes (two-phase: gemini + codex)
**Acceptable:** Up to 5 minutes
**Too long:** > 6 minutes

**If taking > 6 minutes:**
1. Check zen-mcp server status
2. Check network connection
3. Try again with single-phase (codex only) as fallback
4. If still fails, see "Zen Server Unavailable" below

### Zen found many issues - should I fix all now?

**Fix immediately:**
- ALL HIGH/CRITICAL (non-negotiable)
- MEDIUM if straightforward (<5 min each)

**Can defer:**
- MEDIUM if complex (with justification)
- LOW issues (note in TODO)

**Don't:**
- Spend > 15 min total on review fixes
- Let perfect be enemy of good
- Defer critical safety issues

---

## Common Issues & Solutions

### Issue: Zen-MCP Server Unavailable

**Symptom:** Review request times out or errors

**Solution (Emergency Only with User Approval):**
```
User: "Zen server is down, can I skip review for this commit?"

Claude: "Yes, but with conditions:
1. Add ZEN_REVIEW_OVERRIDE to commit message
2. State reason and urgency
3. Request post-commit review when server returns
4. Create follow-up PR if issues found"

Commit message:
git commit -m "Add position validation

ZEN_REVIEW_OVERRIDE: Server temporarily unavailable
Reason: Critical bugfix needed for production
Will request post-commit review and create follow-up PR if issues"
```

**Then:**
- Document in team chat
- Request review ASAP when server returns
- Create follow-up PR if issues found

### Issue: Can't Tell If Zen Approved

**Symptom:** Unclear if safe to proceed

**Solution:**
```
"Zen, can you confirm: is this code approved for commit, or are there blocking issues?"
```

**Look for explicit statements:**
- ✅ "Safe to commit"
- ✅ "All issues resolved"
- ✅ "Approved"
- ❌ "Fix [X] before committing"
- ❌ "Blocking issue"
- ❌ "HIGH/CRITICAL severity"

### Issue: Zen Found Something I Don't Understand

**Symptom:** Zen reports issue but fix isn't clear

**Solution:**
```
"Zen, can you explain why [issue] is a problem and provide a concrete code example of the fix?"
```

**Zen will provide:**
- Detailed explanation
- Why it's a trading safety issue
- Concrete code example
- Link to relevant docs

### Issue: Disagreement With Zen's Assessment

**Symptom:** You think zen is wrong about an issue

**Solution:**
```
"I think zen's assessment about [issue] might be incorrect because [reason].
Can you explain the concern in more detail?"
```

**Then discuss with user:**
```
User: "What did zen say?"
Claude: "Zen flagged [X] as HIGH because [reason]. I think it might be OK because [Y]. Should I defer this or fix it?"
User: "Fix it" OR "Override it because [justification]"
```

**Don't silently ignore zen's feedback!**

### Issue: False Positive From Zen

**Symptom:** Zen flags something that's actually correct

**Example:**
```
Zen: "Missing circuit breaker check at line 42"
You: "Circuit breaker is checked at line 35 (before this)"
```

**Solution:**
```
"Zen flagged missing circuit breaker check, but it's actually checked earlier at line 35. Can you verify this is a false positive?"

(Zen reviews context)

Zen: "You're correct, breaker is checked at line 35. This is a false positive. Safe to proceed."
```

**Document for future improvements:**
- Note false positive type
- Helps improve zen prompts
- Reduce false positives over time

---

## Examples

### Example: Clean Approval

```bash
$ git add apps/execution_gateway/order_placer.py
$ "Review my staged changes with zen-mcp quick review"

# Two-phase review (gemini → codex)
# Result: ✅ Approved - All safety checks passed

$ git commit -m "Add position limit validation

Zen-review: Approved (continuation_id: abc123-def456)"
```

### Example: Critical Issue Found and Fixed

```bash
$ git add apps/execution_gateway/order_placer.py
$ "Quick review my staged changes"

# Phase 1: Gemini finds CRITICAL missing circuit breaker
# Phase 2: Codex confirms - DO NOT commit

# Fix the issue
$ git add apps/execution_gateway/order_placer.py
$ "I've added the circuit breaker check, please verify"

# Codex-only verification (faster for simple fixes)
# Result: ✅ Verified fix - Safe to commit

$ git commit -m "Add position validation with circuit breaker

- Implement check_position_limits()
- Add circuit breaker check (zen critical fix)

Zen-review: Critical issue found and fixed
Continuation-id: abc123-def456"
```

---

## Validation

**How to verify this workflow succeeded:**
- [ ] Zen-mcp review requested and completed
- [ ] ALL HIGH/CRITICAL issues fixed
- [ ] MEDIUM issues fixed or explicitly deferred
- [ ] Zen explicitly approved ("safe to commit")
- [ ] Continuation ID captured for audit trail

**What to check if something seems wrong:**
- Verify zen-mcp server is running (check status endpoint)
- Check if files were actually staged (`git diff --cached`)
- Confirm review was for correct files
- Look for explicit "safe to commit" statement

---

## Related Workflows

- [01-git-commit.md](./01-git-commit.md) - Full progressive commit workflow (uses this)
- [04-zen-review-deep.md](./04-zen-review-deep.md) - Comprehensive review before PR
- [06-debugging.md](./06-debugging.md) - When zen finds bugs
- [05-testing.md](./05-testing.md) - Running tests after fixes

---

## References

- [Zen-MCP Review Process](./_common/zen-review-process.md) - Complete Tier 1 review details
- [Clink-Only Tool Usage Policy](./_common/clink-policy.md) - Tool usage requirements
- [/docs/STANDARDS/GIT_WORKFLOW.md](../../docs/STANDARDS/GIT_WORKFLOW.md) - Review policy and requirements
- [/docs/STANDARDS/CODING_STANDARDS.md](../../docs/STANDARDS/CODING_STANDARDS.md) - Code safety standards
