# Quick Review Workflow (Clink + Codex)

**Purpose:** Fast safety check of staged changes before each commit (MANDATORY quality gate)
**Tool:** clink + codex codereviewer (Tier 1 review)
**Prerequisites:** Changes staged with `git add`, ready to commit
**Expected Outcome:** Code validated for trading safety issues, approved for commit or issues identified for fixing
**Owner:** @development-team
**Last Reviewed:** 2025-10-21

---

## When to Use This Workflow

**MANDATORY before EVERY commit that touches code:**
- ✅ Before each progressive commit (every 30-60 min)
- ✅ After implementing any trading-related logic
- ✅ Before committing fixes to review feedback
- ✅ After modifying order placement, risk checks, or data handling

**Can skip only for:**
- 📝 Documentation-only changes (add `#docs-only` to commit message)
- 🔧 Auto-generated files (package-lock.json, poetry.lock)
- 🚨 Emergency hotfixes (with explicit user approval + mandatory post-commit review)

**Frequency:** ~30 seconds every 30-60 minutes = **< 1% of development time** for massive safety benefit

---

## Step-by-Step Process

### 1. Stage Your Changes

```bash
git add <files you want to commit>

# Verify what's staged
git status
git diff --cached
```

**What this does:** Prepares changes for review and commit

### 2. Request Quick Review (Clink + Codex)

**Tell Claude to use clink with codex codereviewer:**
```
"Please review my staged changes using clink + codex codereviewer.
Focus on trading safety: circuit breakers, idempotency, position limits."
```

**Alternative (specify files):**
```
"Review apps/execution_gateway/order_placer.py using clink + codex codereviewer"
```

**Alternative (use review prompt):**
```
"Use the quick-safety-review prompt from .claude/prompts/clink-reviews/ to review my staged changes"
```

**What happens:**
- Claude Code uses clink (zen-mcp) with codex CLI
- Codex automatically uses gpt-5-codex model (configured in CLI)
- Analyzes staged changes for trading safety issues
- Review completes in ~20-30 seconds
- Results returned with severity ratings and workflow reminder
- continuation_id provided for follow-up verification

### 3. Review the Findings

**Codex (via clink) will report issues in this format:**

```
**Findings**

- MEDIUM – Missing logging (apps/execution_gateway/order_placer.py:42):
   Issue: Missing logging when position limit exceeded
   Impact: Harder to debug limit violations in production
   Fix: Add structured logging:
   logger.warning(
       "Position limit exceeded",
       extra={
           "symbol": symbol,
           "current": current_pos,
           "limit": max_pos,
           "client_order_id": client_order_id
       }
   )

- LOW – Variable naming (apps/execution_gateway/order_placer.py:78):
   Issue: Variable name 'pos' is unclear
   Impact: Minor readability issue
   Fix: Rename to 'current_position'

**Positives**
- Circuit breaker checks present
- Idempotent client_order_id implementation

<SUMMARY>Safe to commit after addressing MEDIUM issue.</SUMMARY>

continuation_id: abc123-def456 (for follow-up verification)
```

**Severity levels:**
- **HIGH/CRITICAL:** ❌ MUST fix before committing (blocking)
- **MEDIUM:** ⚠️ MUST fix OR document deferral
- **LOW:** ℹ️ Fix if time permits

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

**Normal:** 20-30 seconds
**Acceptable:** Up to 60 seconds
**Too long:** > 90 seconds

**If taking > 90 seconds:**
1. Check zen-mcp server status
2. Check network connection
3. Try again
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

### Example 1: Clean Approval

```bash
$ git add apps/execution_gateway/order_placer.py

$ "Review my staged changes using clink + codex codereviewer"

Claude: [Uses clink with codex CLI]

Codex: "**Findings**
(none)

**Positives**
- Circuit breaker integration: ✅ Correct
- Position limit logic: ✅ Correct
- Error handling: ✅ Comprehensive
- Logging: ✅ Properly structured

<SUMMARY>Approved - No issues found. Safe to commit.</SUMMARY>

continuation_id: abc123-def456"

$ git commit -m "Add position limit validation

Codex review (clink): Approved (continuation_id: abc123-def456)"
```

### Example 2: Critical Issue Found and Fixed

```bash
$ git add apps/execution_gateway/order_placer.py

$ "Review my staged changes using clink + codex codereviewer"

Claude: [Uses clink with codex CLI]

Codex: "**Findings**

- CRITICAL – Missing circuit breaker (line 42):
   Missing circuit breaker check before order submission
   Impact: Could place orders when risk limits breached!
   Fix: Add before line 45:
   if self.breaker.is_tripped():
       raise CircuitBreakerTripped('Cannot place order: breaker TRIPPED')

<SUMMARY>BLOCKED - Do NOT commit until CRITICAL issue fixed.</SUMMARY>

continuation_id: abc123-def456"

# Fix immediately!
# Add circuit breaker check

$ git add apps/execution_gateway/order_placer.py

$ "I've added the circuit breaker check, please verify (continuation_id: abc123-def456)"

Claude: [Uses clink with codex, preserves context]

Codex: "✅ Verified fix

Circuit breaker check added correctly at line 42.
All safety checks now in place.

<SUMMARY>Safe to commit.</SUMMARY>

continuation_id: abc123-def456"

$ git commit -m "Add position validation with circuit breaker

- Implement check_position_limits()
- Add circuit breaker check (zen critical fix)
- Add comprehensive error handling

Zen-review: Critical issue found and fixed (abc123)"
```

### Example 3: Medium Issue Deferred

```bash
$ git add apps/execution_gateway/order_placer.py

$ "Review my staged changes using clink + codex codereviewer"

Claude: [Uses clink with codex CLI]

Codex: "**Findings**

- MEDIUM – Missing logging (line 67):
   Missing logging when position limit exceeded
   Impact: Harder to debug in production
   Fix: Add structured logging

<SUMMARY>Approved with conditions - Address MEDIUM issue or document deferral.</SUMMARY>

continuation_id: xyz789-abc123"

# Decide to defer
$ "The logging issue is MEDIUM and requires logging infrastructure.
I'm deferring this to address in next commit after logging setup is complete.
This is an operational improvement, not a safety issue.

Is this deferral acceptable?"

User: "Yes, that's fine. Create a follow-up task."

Zen: "Deferral accepted. Create follow-up task for logging.
Safe to commit with deferral documented."

$ git commit -m "Add position validation

- Implement check_position_limits()
- Add circuit breaker integration

Deferred: Logging for limit violations (requires logging setup first)
Follow-up: Created task T5.1

Zen-review: Approved with deferral (abc123)"
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

**Standards & Policies:**
- [/docs/STANDARDS/GIT_WORKFLOW.md](../../docs/STANDARDS/GIT_WORKFLOW.md) - Review policy and requirements
- [/docs/STANDARDS/CODING_STANDARDS.md](../../docs/STANDARDS/CODING_STANDARDS.md) - Code safety standards

**Implementation Details:**
- [/CLAUDE.md](../../CLAUDE.md#🤖-zen-mcp--clink-integration) - Clink + zen-mcp integration overview
- [/docs/CONCEPTS/zen-mcp-clink-optimization-proposal.md](../../docs/CONCEPTS/zen-mcp-clink-optimization-proposal.md) - Clink-based workflow design
- [.claude/prompts/clink-reviews/quick-safety-review.md](../prompts/clink-reviews/quick-safety-review.md) - Review prompt template

**Focus Areas (What Zen Checks):**
- Circuit breaker checks before order placement
- Idempotent order IDs (deterministic, no duplicates)
- Position limit validation (per-symbol and portfolio)
- Race conditions in concurrent code
- Proper error handling (no swallowing exceptions)
- Structured logging with context (strategy_id, client_order_id)
- Type hints and documentation

---

**Maintenance Notes:**
- Update when zen-mcp prompts change
- Review when new safety patterns added
- Adjust if false positive rate > 10%
- Notify @development-team + zen-mcp maintainers for substantial changes
