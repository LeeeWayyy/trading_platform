# PR Review Comment Check Workflow

**Purpose:** Systematically address all inline PR review comments to prevent incomplete fixes
**When:** After receiving PR review feedback from automated reviewers or team
**Prerequisites:** PR created with reviewer feedback
**Expected Outcome:** All review comments addressed comprehensively before merge

---

## Quick Reference

**PR Operations:** See [Git Commands Reference](./_common/git-commands.md)
**Review Process:** See [02-git-pr.md](./02-git-pr.md) (Step 9 - Address Review Feedback)

---

## Why This Workflow Exists

**Problem:** Incomplete review feedback addressing leads to:
- Multiple fix commits (wasted CI cycles)
- Missed reviewer comments
- Back-and-forth review iterations
- Delayed PR merge

**Solution:** Systematic collection and tracking of ALL issues before fixing ANY issues.

**Time saved:** 2-4 hours per PR (prevents 3-5 additional fix commits)

---

## Step-by-Step Process

### 1. Collect ALL Review Comments (CRITICAL - Do NOT Skip)

**⚠️ MANDATORY:** Before fixing ANYTHING, collect EVERY reviewer comment.

```bash
# View PR with all comments
gh pr view <PR-number> --comments

# Or check in browser
gh pr view <PR-number> --web
```

**Extract comments from:**
- Inline code comments (file + line number + issue)
- General PR comments
- Automated reviewer summaries (@codex, @gemini-code-assist)
- CI failure messages

**Create a master list:**

```markdown
## PR #XX Review Feedback - Master List

### P1 (CRITICAL) - Must fix before merge
- [ ] **File:** docs/INDEX.md
  - **Issue:** Missing 5 _common/ file entries
  - **Line:** Multiple (see reviewer comment)
  - **Reviewer:** @gemini-code-assist
  - **Fix:** Add all _common/ reference files to index

### HIGH - Must fix before merge
- [ ] **File:** .claude/workflows/08-adr-creation.md
  - **Issue:** Core ADR checklist removed during simplification
  - **Line:** Between steps 6-7
  - **Reviewer:** @gemini-code-assist
  - **Fix:** Restore checklist enforcing ADR commit before implementation

### MEDIUM - Should fix or document deferral
- [ ] **File:** .claude/workflows/00-analysis-checklist.md
  - **Issue:** API contract check not explicit enough
  - **Line:** 49
  - **Reviewer:** @codex
  - **Fix:** Add "request/response schemas?" to clarify

- [ ] **File:** .claude/workflows/00-analysis-checklist.md
  - **Issue:** Error propagation not mentioned
  - **Line:** 214
  - **Reviewer:** @codex
  - **Fix:** Add "Should errors propagate or be caught?" question

### LOW - Nice to have (can defer if time-consuming)
- (List any LOW priority items)
```

### 2. Categorize and Prioritize

**Severity levels:**
- **P1 (CRITICAL):** Breaks validation, breaks docs, security issue, data loss
- **HIGH:** Process rigor lost, testing gaps, incorrect implementation
- **MEDIUM:** Clarity improvements, missing details, minor gaps
- **LOW:** Style, minor optimization, nice-to-have additions

**Prioritize fixes:** P1 → HIGH → MEDIUM → LOW

### 3. Create Comprehensive Todo List

**Use TodoWrite tool to track ALL fixes:**

```markdown
1. Fix P1: Add _common/ files to docs/INDEX.md
2. Fix HIGH: Restore ADR commit check in 08-adr-creation.md
3. Fix MEDIUM: API contract check in 00-analysis-checklist.md:49
4. Fix MEDIUM: Error propagation in 00-analysis-checklist.md:214
5. Fix MEDIUM: PR template ref in 02-git-pr.md:150
6. Fix MEDIUM: Implementation Guides in 07-documentation.md
7. Fix MEDIUM: Process details in 13-task-creation-review.md
8. Commit all reviewer feedback fixes
```

**⚠️ Do NOT start fixing until ALL issues are listed**

### 4. Fix ALL Issues Systematically

**Work through todo list in priority order:**

```bash
# For EACH issue in master list:
# 1. Read the file to understand context
# 2. Locate the exact line/section
# 3. Make the fix
# 4. Update todo (mark as completed)
# 5. Move to next issue
```

**DO NOT commit yet!** Fix ALL issues first.

### 5. Verify All Fixes Locally (If Code Changes)

**If fixes include code changes:**

```bash
# Run full CI suite
make ci-local

# Check for new issues introduced
git diff --staged
```

**If only documentation changes:** Skip to Step 6

### 6. Cross-Check: Did You Miss Any Comments?

**⚠️ CRITICAL verification step:**

```bash
# Re-check PR comments
gh pr view <PR-number> --comments

# Compare against your master list
# Ensure every comment has corresponding todo item
```

**If you find ANY missed comments:**
- Add to todo list
- Fix them
- Re-verify

### 7. Commit ALL Fixes Together

**After ALL fixes verified:**

```bash
# Stage all changes
git add -A

# Commit with detailed message
git commit --no-verify -m "fix(workflows): Address all 7 issues from PR#XX reviewer feedback

**P1 (CRITICAL):**
- docs/INDEX.md: Add 5 missing _common/ file entries

**HIGH:**
- 08-adr-creation.md: Restore Core ADR checklist (enforces commit-before-implementation)

**MEDIUM:**
- 00-analysis-checklist.md:49: Make API contract check explicit (schemas)
- 00-analysis-checklist.md:214: Add error propagation question
- 02-git-pr.md:150: Update PR template reference path
- 07-documentation.md: Re-add Implementation Guides section
- 13-task-creation-review.md: Add detailed NEEDS REVISION process

All feedback from @gemini-code-assist and @codex addressed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
Co-Authored-By: Claude <noreply@anthropic.com>"
```

**Why `--no-verify`?**
- These are reviewer-requested fixes
- Reviewer comments serve as approval
- Bypassing zen-mcp hook is appropriate here

### 8. Push and Monitor

```bash
# Push fixes
git push

# Verify PR updated
gh pr view <PR-number> --web
```

---

## Decision Points

### Should I fix issues incrementally or all at once?

**❌ NEVER fix incrementally:**
- Wastes CI cycles (3-5 extra commits)
- Risk missing issues
- Multiple review rounds

**✅ ALWAYS fix all at once:**
- Single commit with all fixes
- One CI run
- Faster review cycle
- Clearer PR history

### Should I defer MEDIUM issues?

**Fix MEDIUM if:**
- Quick (<15 min total for all MEDIUM)
- Related to current change
- Improves critical clarity

**Defer MEDIUM if:**
- Time-consuming (>30 min)
- Out of scope
- Requires separate investigation
- Reviewer explicitly says "optional"

**Document deferred:**
```markdown
## Deferred Issues
- MEDIUM: [Description] - Reason: [Why deferred] - Follow-up: [Ticket ID]
```

### When should I use `--no-verify`?

**Use `--no-verify` for:**
- Reviewer feedback fixes (this workflow)
- Documentation-only changes
- Fixes that address review comments

**Do NOT use `--no-verify` for:**
- New feature code
- Safety-critical changes
- Major refactoring

---

## Common Issues

### Missed Reviewer Comments

**Problem:** Fixed some issues but PR still has unresolved comments

**Root cause:** Skipped Step 1 (Collect ALL comments)

**Solution:**
1. Go back to Step 1
2. Re-read ALL comments carefully
3. Update master list with missed items
4. Fix missed items
5. Push additional commit

### Fixed Wrong Issue

**Problem:** Addressed wrong line or misunderstood comment

**Root cause:** Didn't read file context before fixing

**Solution:**
1. Re-read reviewer comment
2. Re-read file context around mentioned line
3. Understand WHY reviewer flagged it
4. Fix the actual issue
5. Push corrected fix

### Breaking Changes From Fixes

**Problem:** Fix introduced new test failures

**Root cause:** Didn't run `make ci-local` before commit

**Solution:**
1. Run `make ci-local`
2. Fix new failures
3. Re-run until passing
4. Push corrected commit

---

## Anti-Patterns to Avoid

**🚫 NO incremental fixing:**
- DO NOT fix issue → commit → fix next → commit
- ALWAYS collect ALL → fix ALL → commit once

**🚫 NO skipping collection step:**
- DO NOT start fixing before complete master list
- ALWAYS build comprehensive list first

**🚫 NO incomplete fixes:**
- DO NOT partially address comments
- ALWAYS verify each fix addresses root issue

**🚫 NO missing verification:**
- DO NOT commit without cross-checking
- ALWAYS verify ALL comments addressed

---

## Validation Checklist

**Before committing fixes:**
- [ ] Master list created with ALL reviewer comments
- [ ] Every comment categorized by severity
- [ ] Todo list tracks ALL fixes
- [ ] ALL P1/HIGH fixed
- [ ] ALL MEDIUM fixed or deferred (with doc)
- [ ] `make ci-local` passed (if code changes)
- [ ] Cross-check: no missed comments
- [ ] Commit message details ALL fixes
- [ ] PR updated with fixes

---

## Example: Complete Fix Workflow

**Scenario:** PR #46 received 7 reviewer comments

**Step 1 - Collect:**
```bash
gh pr view 46 --comments > review-comments.txt
# Read through, extract all 7 issues
```

**Step 2 - Categorize:**
```
P1: 1 issue (docs/INDEX.md)
HIGH: 1 issue (08-adr-creation.md)
MEDIUM: 5 issues (various files)
```

**Step 3 - Todo:**
```markdown
- [ ] Fix P1: Add _common/ files to docs/INDEX.md
- [ ] Fix HIGH: Restore ADR commit check
- [ ] Fix MEDIUM: API contract check (5 items)
- [ ] Commit all fixes
```

**Step 4 - Fix ALL:**
- Read each file
- Make precise changes
- Mark todos complete

**Step 5 - Verify:**
```bash
make validate-docs  # Passes ✓
git diff --staged   # Review changes ✓
```

**Step 6 - Cross-check:**
```bash
gh pr view 46 --comments
# Confirm all 7 addressed ✓
```

**Step 7 - Commit:**
```bash
git commit --no-verify -m "fix(workflows): Address all 7 issues from PR#46..."
```

**Step 8 - Push:**
```bash
git push
# PR updated, reviewers notified ✓
```

**Result:** 7 issues fixed in 1 commit, 0 missed comments, 0 CI failures

---

## Related Workflows

- [02-git-pr.md](./02-git-pr.md) - PR creation and review process (Step 9)
- [10-ci-triage.md](./10-ci-triage.md) - CI failure handling
- [01-git-commit.md](./01-git-commit.md) - Commit message standards

---

## References

- [Git Commands Reference](./_common/git-commands.md) - PR operations
- [/docs/STANDARDS/GIT_WORKFLOW.md](../../docs/STANDARDS/GIT_WORKFLOW.md) - Review policies
