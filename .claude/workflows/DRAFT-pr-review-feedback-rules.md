# Draft: PR Review Feedback Handling Rules

**Context:** This is a DRAFT of new workflow rules based on issues found during PR #26 (workflow improvements).

**Purpose:** Ensure systematic, complete handling of PR review feedback to avoid multiple CI failures and incomplete fixes.

---

## Core Principles

### 1. Never Hide or Ignore Issues

**CRITICAL RULE:** Do NOT use ignore patterns, comments, or workarounds to hide actual issues.

**Required Actions:**
- ✅ **Fix the issue** - Address root cause immediately
- ✅ **Add to TODO list** - If fix requires separate work, create explicit task
- ❌ **NEVER ignore** - Don't add ignore patterns for actual broken links, tests, or checks

**Examples:**

❌ **BAD - Hiding broken links:**
```json
// .github/markdown-link-check-config.json
{
  "ignorePatterns": [
    {"pattern": "docs/ARCHIVE"},           // Hiding broken archive links
    {"pattern": "IMPLEMENTATION_GUIDES"},  // Hiding unmigrated paths
    {"pattern": "00-TEMPLATE"}             // Hiding template placeholders
  ]
}
```

✅ **GOOD - Fixing the issues:**
```bash
# Fix broken archive links
# Option 1: Fix the links
sed -i '' 's|docs/ARCHIVE/old.md|docs/TASKS/new.md|g' *.md

# Option 2: Remove archive entirely if obsolete
rm -rf docs/ARCHIVE/

# Fix template placeholders with real examples
sed -i '' 's|XXX-title|0001-example-adr|g' templates/*.md
```

**Only Valid Ignore Patterns:**
- External HTTP URLs that 404 (third-party sites)
- Localhost URLs (development only)
- Staging/test environments

---

## Step 9 (REVISED): Address Review Feedback Systematically

**When reviewers (Codex, Gemini, or CI) find issues, follow this MANDATORY process:**

### Phase 1: Collect ALL Issues (DO NOT START FIXING YET)

**⏱️ Expected Time:** 5-10 minutes

**1A. Gather Issues from ALL Sources:**

```bash
# Check all review comments (Codex)
gh pr view <PR_NUMBER> --json comments --jq '.comments[] | select(.author.login=="codex") | .body'

# Check all review comments (Gemini)
gh pr view <PR_NUMBER> --json comments --jq '.comments[] | select(.author.login=="gemini-code-assist") | .body'

# Check CI failures
gh pr checks <PR_NUMBER>

# Get detailed CI logs if needed
gh run view <RUN_ID> --log-failed
```

**1B. Create Comprehensive Issue List:**

Create a todo list with ALL issues found:

```markdown
## PR Review Feedback - Comprehensive List

### Issues from Codex Review:
- [ ] HIGH: Missing null check in position calculation (line 42)
- [ ] MEDIUM: Add logging for limit violations (line 67)
- [ ] LOW: Variable `pos` should be `current_position`

### Issues from Gemini Review:
- [ ] MEDIUM: Improve error message clarity (line 89)
- [ ] LOW: Consider caching position lookups

### Issues from CI:
- [ ] CRITICAL: 11 broken links to IMPLEMENTATION_GUIDES
- [ ] CRITICAL: Test failure - emoji assertion mismatch
- [ ] ERROR: 3 template placeholder links to non-existent files
```

**1C. Verify You Found EVERYTHING:**

```bash
# Run link check locally to catch ALL link issues
npm install -g markdown-link-check
find docs -name "*.md" -exec markdown-link-check {} \; > link-check-results.txt

# Count remaining Status: 400 errors (broken file links)
grep "Status: 400" link-check-results.txt | wc -l
# Must be 0 before proceeding!

# Run tests locally
make test

# Run linting locally
make lint
```

**DO NOT PROCEED** until you have a complete list of ALL issues from ALL sources.

**1D. Special Case: Large Issue Lists (>20 issues):**

If you have more than 20 issues total:

**Option 1: Break into batches by severity**
```markdown
## Batch 1: CRITICAL + HIGH (5 issues)
- [ ] CRITICAL: Broken links
- [ ] HIGH: Null check missing
- [ ] HIGH: ...

## Batch 2: MEDIUM (10 issues)
- [ ] MEDIUM: Logging
- [ ] MEDIUM: ...

## Batch 3: LOW (8 issues)
- [ ] LOW: Variable naming
- [ ] LOW: ...
```

Work through batches sequentially:
1. Fix Batch 1 → Test → Zen review → Commit
2. Fix Batch 2 → Test → Zen review → Commit
3. Fix Batch 3 → Test → Zen review → Commit

**Option 2: Consult user**
```
"I found 35 issues across Codex, Gemini, and CI:
- 5 CRITICAL
- 8 HIGH
- 15 MEDIUM
- 7 LOW

Should I:
A) Fix all in one commit (may take 2+ hours)
B) Break into 3 batches by severity
C) Defer all LOW issues to follow-up PR

User, which approach do you prefer?"
```

**Default:** If >20 issues, use Option 1 (batching by severity)

---

### Phase 2: Fix ALL Issues (No Partial Fixes)

**⏱️ Expected Time:** 10-60 minutes (depends on issue count and complexity)

**2A. Work Through Entire List Systematically:**

Fix ALL issues in the todo list:
- All HIGH/CRITICAL issues (mandatory)
- All MEDIUM issues (fix or document deferral)
- LOW issues if time permits (5-10 min total)

**2B. Test Each Fix Locally:**

```bash
# After fixing each category, test locally
make test
make lint

# For link fixes, verify ZERO Status: 400 errors
find docs -name "*.md" -exec markdown-link-check {} \; | grep "Status: 400"
# Should return nothing!

# For code fixes, verify specific tests pass
pytest path/to/affected/tests -v
```

**2C. DO NOT COMMIT Yet!**

Common mistake: Committing after each fix → multiple commits → repeated CI failures

✅ **Correct approach:** Fix ALL issues, verify ALL tests pass, THEN commit once.

---

### Phase 3: Verify Locally Before Committing

**⏱️ Expected Time:** 5-15 minutes

**3A. Run Complete Local Validation:**

```bash
# 1. All tests must pass
make test
# Expected: 100% pass rate

# 2. All linting must pass
make lint
# Expected: No errors

# 3. Link checks must pass (if docs changed)
find docs -name "*.md" -exec markdown-link-check {} \; | grep "Status: 400" | wc -l
# Expected: 0

# 4. Manual spot-check of fixes
# Review your changes one more time
git diff --staged
```

**3B. Only Proceed When:**
- ✅ ALL issues from Phase 1 are fixed
- ✅ ALL local tests pass
- ✅ ALL local lint checks pass
- ✅ ALL link checks pass (if applicable)
- ✅ No new errors introduced

**DO NOT COMMIT** until ALL checks pass locally!

---

### Phase 4: Mandatory Zen-MCP Review of Fixes

**⏱️ Expected Time:** 5-10 minutes (including zen response)

**4A. Stage ALL Fixes:**

```bash
# Stage all files with fixes
git add -A

# Verify what's staged
git status
git diff --cached --stat
```

**4B. Request Zen-MCP Review with Detailed Context:**

**CRITICAL:** You MUST explain to zen-mcp:
1. What issues were found by reviewers/CI
2. How you fixed each issue
3. What you want zen-mcp to verify

**Structured Template:**

```
Request zen-mcp review:

"Reviewing PR feedback fixes for [PR #XX]. Requesting verification before commit.

## Context
- **PR:** #XX - [Brief PR title]
- **Review Sources:** Codex, Gemini, CI
- **Total Issues:** [N] issues found ([X] CRITICAL, [Y] HIGH, [Z] MEDIUM, [W] LOW)

## Issues Identified and Fixes Applied

### Issue 1: [Severity] - [Brief description]
- **Source:** [Codex/Gemini/CI]
- **Problem:** [What was wrong]
- **Fix Applied:** [Specific code change or action taken]
- **Location:** [File:line or files affected]

### Issue 2: [Severity] - [Brief description]
- **Source:** [Codex/Gemini/CI]
- **Problem:** [What was wrong]
- **Fix Applied:** [Specific code change or action taken]
- **Location:** [File:line or files affected]

[Repeat for all issues]

## Verification Requested

Please verify:
1. All issues are truly fixed (not hidden/ignored)
2. Fixes are correct and safe
3. No new issues introduced by these changes
4. [Specific technical verification for critical fixes]

## Local Validation Completed

- ✅ All tests pass: [X/X passed]
- ✅ Linting passes: No errors
- ✅ [Type-specific checks]: [e.g., Zero Status: 400 link errors]
- ✅ Manual spot-check: Reviewed all changes

Ready for zen-mcp verification before commit."
```

**Example (Real Usage):**

```
"Reviewing PR feedback fixes for PR #26. Requesting verification before commit.

## Context
- **PR:** #26 - Workflow improvements
- **Review Sources:** Codex, Gemini, CI
- **Total Issues:** 14 issues found (2 CRITICAL, 1 HIGH, 8 MEDIUM, 3 LOW)

## Issues Identified and Fixes Applied

### Issue 1: CRITICAL - Broken links to IMPLEMENTATION_GUIDES
- **Source:** CI (markdown-link-check)
- **Problem:** 11 broken file links after folder restructure
- **Fix Applied:** Mapped all IMPLEMENTATION_GUIDES → TASKS paths using sed batch script
- **Location:** 8 files (CONCEPTS/, GETTING_STARTED/, TASKS/)

### Issue 2: CRITICAL - Test failure with emoji assertion
- **Source:** CI (pytest)
- **Problem:** Emoji character in assertion causing encoding mismatch
- **Fix Applied:** Updated assertion to use plain text comparison
- **Location:** tests/test_signal_generator.py:42

### Issue 3: HIGH - Null check missing in position calculation
- **Source:** Codex
- **Problem:** position can be None, causing TypeError downstream
- **Fix Applied:** Added `if position is None: return 0` guard clause
- **Location:** apps/execution_gateway/order_placer.py:42

[... continue for all 14 issues ...]

## Verification Requested

Please verify:
1. All 11 link mappings are correct (IMPLEMENTATION_GUIDES → TASKS)
2. Null check logic doesn't break position tracking edge cases
3. Test fix doesn't reduce test coverage
4. No new issues introduced by batch sed replacements

## Local Validation Completed

- ✅ All tests pass: 7/7 passed
- ✅ Linting passes: No mypy/ruff errors
- ✅ Link check: Zero Status: 400 errors remain
- ✅ Manual spot-check: Reviewed all 9 changed files

Ready for zen-mcp verification before commit."
```

**4C. Wait for Zen-MCP Approval:**

Zen-mcp will verify:
- Issues are truly fixed (not hidden/ignored)
- Fixes are correct and safe
- No new issues introduced
- Nothing was missed

**DO NOT COMMIT** until zen-mcp approves!

**4D. If Zen-MCP Finds NEW Issues:**

If zen-mcp identifies additional problems not caught by reviewers/CI:

**Step 1: Add new issues to todo list**
```markdown
## NEW Issues Found by Zen-MCP:
- [ ] HIGH: [New issue zen found]
- [ ] MEDIUM: [Another new issue]
```

**Step 2: Loop back to Phase 2**
- Fix the NEW issues zen-mcp found
- Re-run Phase 3 (local validation)
- Return to Phase 4 with updated fixes

**Step 3: Request re-review**
```
"Fixed the additional issues zen-mcp identified:

**New Issues Fixed:**
1. [HIGH issue] - [How you fixed it]
2. [MEDIUM issue] - [How you fixed it]

**Re-validation:**
- All tests still pass: [X/X]
- No new errors introduced

Please re-verify all fixes (original + new) are complete."
```

**Step 4: Iterate until approved**
- Repeat Phase 2 → Phase 3 → Phase 4 until zen-mcp says "All fixes verified ✅"
- Only proceed to Phase 5 when zen-mcp fully approves

**Important:** This is normal! Zen-mcp catching additional issues during review is GOOD - it prevents bugs from reaching production.

---

### Phase 5: Commit Once When Everything Approved

**⏱️ Expected Time:** 5-10 minutes

**5A. Write Comprehensive Commit Message:**

```bash
git commit -m "Address all PR review feedback from Codex, Gemini, and CI

Fix issues found by Codex:
- Add null check for position calculation (HIGH)
- Add logging for limit violations (MEDIUM)
- Rename 'pos' to 'current_position' for clarity (LOW)

Fix issues found by Gemini:
- Improve error message clarity in validation (MEDIUM)
- Add position lookup caching (LOW)

Fix issues found by CI:
- Map 11 IMPLEMENTATION_GUIDES references to TASKS files (CRITICAL)
- Fix emoji test assertion to use plain text (CRITICAL)
- Update 3 template placeholder links to real examples (ERROR)

All tests pass locally (7/7 passed).
Zero broken file links remain (Status: 400).
make lint passes with no errors.

Zen-mcp review: ALL fixes verified, no new issues introduced

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

**5B. Commit and Push:**

```bash
# Commit all fixes together
git commit
# (Message from 5A)

# Push to update PR
git push
```

**5C. Notify Reviewers:**

```bash
gh pr comment <PR_NUMBER> --body "All review feedback addressed in latest commit.

**Fixed:**
- Codex: 3 issues (1 HIGH, 1 MEDIUM, 1 LOW)
- Gemini: 2 issues (1 MEDIUM, 1 LOW)
- CI: 3 critical failures

**Verification:**
- All tests pass locally (7/7)
- All link checks pass (0 broken file links)
- Linting passes
- Zen-mcp reviewed and approved all fixes

@codex @gemini-code-assist Please review latest commit to verify all issues are resolved. Thank you for the thorough reviews!"
```

---

## Anti-Patterns to AVOID

### ❌ Anti-Pattern 1: Committing Before Finding All Issues

**Bad:**
```bash
# Fix Codex issue
git commit -m "Fix null check"
git push
# CI fails → forgot about broken links!

# Fix broken links
git commit -m "Fix links"
git push
# CI fails → still have template placeholders!
```

**Result:** 3+ commits, 3+ CI failures, frustrated user

**Good:**
```bash
# Collect ALL issues first
# Fix ALL issues
# Test ALL fixes locally
# Zen review ALL fixes
# Commit ONCE
```

**Result:** 1 commit, CI passes, happy user

---

### ❌ Anti-Pattern 2: Hiding Issues with Ignore Patterns

**Bad:**
```json
// Just ignore the broken links instead of fixing them
{
  "ignorePatterns": [
    {"pattern": "docs/ARCHIVE"},
    {"pattern": "00-TEMPLATE"},
    {"pattern": "IMPLEMENTATION_GUIDES"}
  ]
}
```

**Result:** Issues hidden but not fixed, confusion later

**Good:**
```bash
# Fix or remove broken links
rm -rf docs/ARCHIVE/
sed -i '' 's|IMPLEMENTATION_GUIDES/x.md|TASKS/X_DONE.md|g' docs/**/*.md
sed -i '' 's|XXX-placeholder|real-example|g' templates/*.md
```

**Result:** Issues actually resolved

---

### ❌ Anti-Pattern 3: Not Testing Locally

**Bad:**
```bash
# Fix issues
git commit -m "Fix stuff"
git push
# Wait for CI...
# CI fails with new error!
```

**Result:** Repeated CI failures, back-and-forth

**Good:**
```bash
# Fix issues
make test && make lint
# All pass ✅
git commit -m "Fix all issues"
git push
# CI passes ✅
```

**Result:** One commit, one CI run, passes

---

### ❌ Anti-Pattern 4: Skipping Zen Review

**Bad:**
```bash
# Fix issues
git commit -m "Address feedback"
# No zen review!
# Push introduces new bug
```

**Result:** New issues introduced

**Good:**
```bash
# Fix issues
# Request zen review: "I fixed X, Y, Z - please verify"
# Zen: "Issue Z fix is incorrect, should be..."
# Fix issue Z properly
# Zen: "All fixes verified ✅"
git commit
```

**Result:** All fixes verified correct

---

## Summary: The 5-Phase Process

**Phase 1: COLLECT**
- Gather issues from ALL sources (Codex, Gemini, CI)
- Create comprehensive todo list
- Verify you found EVERYTHING

**Phase 2: FIX**
- Fix ALL issues (no partial fixes)
- Test each fix locally
- DO NOT commit yet

**Phase 3: VERIFY**
- Run ALL tests locally
- Run ALL lint checks locally
- Verify ZERO errors remain
- DO NOT commit yet

**Phase 4: ZEN REVIEW**
- Stage all fixes
- Request zen-mcp review with detailed context
- Explain what was broken and how you fixed it
- Wait for approval
- DO NOT commit until approved

**Phase 5: COMMIT ONCE**
- Write comprehensive commit message
- Commit all fixes together
- Push and notify reviewers
- ONE commit, not multiple

---

## Validation Checklist

Before committing PR review fixes:

- [ ] Collected issues from ALL sources (Codex, Gemini, CI)
- [ ] Created comprehensive todo list of ALL issues
- [ ] Fixed ALL HIGH/CRITICAL issues
- [ ] Fixed or documented ALL MEDIUM issues
- [ ] All local tests pass (`make test`)
- [ ] All local lint checks pass (`make lint`)
- [ ] All link checks pass (0 Status: 400 errors)
- [ ] Zen-mcp reviewed ALL fixes with detailed context
- [ ] Zen-mcp approved (verified no new issues)
- [ ] Ready to commit ALL fixes in ONE commit

**DO NOT commit** unless ALL checkboxes are checked!

---

## Questions for Zen-MCP Review

**Please review this draft workflow and answer:**

1. Are these rules comprehensive enough to prevent the issues we encountered?
2. Is the 5-phase process (COLLECT → FIX → VERIFY → ZEN REVIEW → COMMIT ONCE) clear and actionable?
3. Are there any edge cases or scenarios not covered?
4. Is the zen-mcp review context requirement (explaining what was broken and how it was fixed) sufficiently detailed?
5. Should this replace step 9 in `02-git-pr.md` or be added as a separate section?
6. Are the anti-patterns realistic and helpful?
7. Any concerns about the workflow adding too much overhead?
8. Suggestions for improvement?

**Context of issues we're preventing:**
- Made 4 commits while issues still existed
- Didn't test link checks locally before committing
- Added ignore patterns for actual broken links instead of fixing them
- Didn't collect ALL issues before starting fixes
- Multiple back-and-forth with CI
- User frustration with repeated failures

**Goal:** Ensure this never happens again by making the systematic process mandatory and explicit.
