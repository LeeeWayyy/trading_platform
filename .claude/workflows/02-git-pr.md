# Pull Request Creation Workflow

**Purpose:** Create well-documented pull requests with automated quality checks
**Prerequisites:** Feature complete, all progressive commits done, deep zen-mcp review completed
**Expected Outcome:** PR created with complete context, ready for team review and merge
**Owner:** @development-team
**Last Reviewed:** 2025-10-21

---

## When to Use This Workflow

**Create a PR when:**
- ✅ Feature/fix is complete (all requirements met)
- ✅ All progressive commits completed
- ✅ Deep zen-mcp review passed ([04-zen-review-deep.md](./04-zen-review-deep.md))
- ✅ All tests passing locally and in CI
- ✅ Documentation updated
- ✅ Ready for team review and merge

**Do NOT create PR if:**
- ❌ Feature incomplete (use draft PR instead)
- ❌ Tests failing
- ❌ Haven't run deep zen review
- ❌ Breaking changes without ADR
- ❌ Still in experimental/WIP mode

---

## Step-by-Step Process

### 1. Run Deep Zen-MCP Review (MANDATORY)

**Before creating ANY pull request, comprehensive review is required:**

```
Use slash command: /zen-review deep

Or tell Claude: "Deep review all branch changes with zen-mcp"
```

**This reviews:**
- Overall architecture and design
- Test coverage completeness
- Edge cases and error handling
- Integration points
- Documentation quality

**Address ALL findings:**
- **HIGH/CRITICAL:** MUST fix (blocking)
- **MEDIUM:** MUST fix OR document deferral with justification
- **LOW:** Fix if time permits, or document as future improvement

**Only proceed after zen-mcp approval!**

See [04-zen-review-deep.md](./04-zen-review-deep.md) for detailed deep review workflow.

### 2. Verify All Tests Pass

**CRITICAL:** Run the EXACT same checks that CI runs using `make ci-local`:

```bash
# Run CI checks locally (mirrors GitHub Actions exactly)
make ci-local
```

**This runs (in order):**
1. `mypy --strict` (type checking)
2. `ruff check` (linting)
3. `pytest -m "not integration and not e2e"` (unit tests with coverage)

**Expected:** ✅ All green, no failures, coverage ≥80%

**If any failures:**
- Fix them immediately
- Run zen review of fixes
- Re-run `make ci-local` to verify
- Don't create PR until all pass

**Why use `make ci-local`?**
- Eliminates local/CI testing gap
- Runs exact same commands CI uses
- Catches issues before pushing
- Saves time (no waiting for CI feedback loop)

### 3. Verify Branch Status

```bash
# Check current branch
git branch --show-current

# Check commits ahead of master
git log master..HEAD --oneline

# Check for uncommitted changes
git status
```

**Expected:**
- On feature branch (not master)
- Clean working tree
- 1+ commits ahead of master

### 4. Push Latest Changes

```bash
# Push all commits to remote
git push

# Or if first time pushing this branch
git push -u origin feature/your-branch-name
```

**What this does:** Ensures remote has all your commits before creating PR

### 5. Mark Task as Complete (MANDATORY)

**After implementation is complete, mark the task as DONE:**

```bash
# Complete the task (PROGRESS → DONE)
./scripts/tasks.py complete P1T9

# This will:
# 1. Rename P1T9_PROGRESS.md → P1T9_DONE.md
# 2. Update front matter: state=DONE, completed date, duration
# 3. Calculate duration automatically
```

**CRITICAL: Update all links to the task file:**

```bash
# Find all references to the task
grep -r "P1T9_TASK.md\|P1T9_PROGRESS.md" docs/

# Update each file to point to P1T9_DONE.md
# This is REQUIRED for link checker to pass!
```

**Example updates needed:**
- `[P1T9_TASK.md](./P1T9_TASK.md)` → `[P1T9_DONE.md](./P1T9_DONE.md)`
- `[P1T9_PROGRESS.md](./P1T9_PROGRESS.md)` → `[P1T9_DONE.md](./P1T9_DONE.md)`

**Commit the completion:**
```bash
# Stage all changes (task file + link updates)
git add docs/TASKS/P1T9_DONE.md docs/TASKS/P1T10_TASK.md  # etc.

# Commit with clear message
git commit -m "Mark P1T9 as complete and update links"

# Push to remote
git push
```

**Why this must happen before PR:**
- ❌ If you create PR with broken links → link checker fails
- ✅ Mark DONE and update links first → all checks pass

**Verify completion:**
```bash
# Check task was marked complete
./scripts/tasks.py list --state DONE

# Sync project status
./scripts/tasks.py sync-status

# Verify no broken links
make check-links  # or your link checker command
```

### 6. Update Documentation (MANDATORY)

**Before creating PR, update all relevant documentation:**

**A. Concept Documentation (docs/CONCEPTS/)**

If your task introduces important concepts, algorithms, or architectural patterns that beginners should understand:

```bash
# Create concept documents explaining:
# - What problem does this solve?
# - How does it work? (with examples)
# - Why did we choose this approach?
# - Common patterns and best practices

# Examples:
docs/CONCEPTS/centralized-logging.md      # Loki/Promtail/Grafana architecture
docs/CONCEPTS/distributed-tracing.md      # Trace ID propagation
docs/CONCEPTS/hot-reload.md               # Zero-downtime model updates
docs/CONCEPTS/feature-parity.md           # Research-production consistency
```

**B. README.md Updates**

Update README.md to reflect new capabilities:

```bash
# 1. Add new features to "Key Achievements" section
# 2. Update "Observability Stack" or relevant sections
# 3. Add new concept doc links to "Concept Documentation" section
# 4. Update statistics (code metrics, components delivered)
# 5. Add usage examples if applicable
```

**Example updates for P1T9 (Centralized Logging):**
- Added "Observability Stack" section with Loki/Promtail/Grafana
- Added 3 concept doc links (centralized-logging, distributed-tracing, structured-logging)
- Updated "Key Achievements" with logging capabilities
- Updated code metrics with logging library stats

**C. Getting Started Guides**

Update relevant guides if your task changes how developers work:

```bash
# Examples:
docs/GETTING_STARTED/LOGGING_GUIDE.md    # How to use logging library
docs/GETTING_STARTED/SETUP.md            # Environment setup changes
docs/RUNBOOKS/logging-queries.md         # LogQL query examples
```

**D. Commit Documentation Updates**

```bash
# Stage all documentation
git add docs/CONCEPTS/ README.md docs/GETTING_STARTED/ docs/RUNBOOKS/

# Commit with clear message
git commit -m "Add concept documentation and update README for P1T9

- Added centralized-logging.md concept doc
- Added distributed-tracing.md concept doc
- Added structured-logging.md concept doc
- Updated README with Observability Stack section
- Added logging guide for developers
"

# Push
git push
```

**Why this must happen before PR:**
- ✅ Documentation reviewed alongside code changes
- ✅ Complete picture of what was implemented
- ✅ Helps reviewers understand architectural decisions
- ✅ Educational value maintained (key project principle)

**Checklist:**
- [ ] Created concept docs for new patterns/architecture (if applicable)
- [ ] Updated README.md with new capabilities
- [ ] Updated relevant getting started guides
- [ ] Added usage examples or query patterns
- [ ] All documentation links working (no broken links)
- [ ] Documentation committed and pushed

### 7. Gather PR Information

**Collect this information before creating PR:**

**A. Ticket Reference:**
- Task number (e.g., P0T5, P1.3T1)
- Link to `/docs/TASKS/` file

**B. Related ADRs:**
- List any ADRs created or referenced
- Link to `/docs/ADRs/` files

**C. Changes Summary:**
- What was implemented
- Why it was needed
- How it works

**D. Zen-MCP Review Evidence:**
- Continuation ID from deep review
- Summary of issues found and fixed
- Final approval confirmation

**E. Testing Evidence:**
- Test pass rate
- Coverage changes
- Manual testing performed

### 8. Create PR Using GitHub CLI

**Basic PR creation:**
```bash
gh pr create
```

**This will prompt you for:**
1. Title (use format: "[Type] Brief description (Ticket)")
2. Body (use template below)
3. Base branch (usually `master`)

**PR with pre-filled template:**
```bash
gh pr create --title "Add position limit validation (P0T5)" --body "$(cat <<'EOF'
## Summary
Implements position limit validation to prevent order placement beyond risk limits.

## Related Work
- **Ticket:** P0T5 - Position Limit Validation
- **ADR:** [ADR-0011: Risk Management System](../../docs/ADRs/0011-risk-management-system.md)
- **Implementation Guide:** [P1T7 Risk Management](../../docs/TASKS/P1T7_DONE.md)

## Changes Made
- [x] Implement `check_position_limits()` function
- [x] Add circuit breaker check before validation
- [x] Integrate into order placement flow
- [x] Add comprehensive error handling
- [x] Add unit tests (15 new tests)
- [x] Add integration tests (5 tests)
- [x] Update OpenAPI spec
- [x] Add concept documentation

## Zen-MCP Review ⚠️ MANDATORY

### Progressive Reviews (Commits 1-6):
- Total commits: 6
- All commits reviewed by zen-mcp before committing
- Issues caught early: 2 HIGH, 4 MEDIUM, 3 LOW
- All issues fixed before reaching PR

### Deep Review (Before PR): ✅ APPROVED
- Continuation ID: `abc123-def456-ghi789`
- Architecture: No issues
- Test coverage: 95% (target: 80%) ✅
- Edge cases: 1 MEDIUM issue found and fixed
- Integration points: Verified with execution gateway
- Final approval: Granted by zen-mcp

**Review prevented 9 issues from reaching PR stage**

## Testing Completed
- [x] Unit tests pass (70/70 - 100%)
- [x] Integration tests pass (12/12 - 100%)
- [x] Linting passes (mypy --strict + ruff)
- [x] Manual testing in DRY_RUN mode
- [x] Manual testing in paper trading
- [x] Performance test: <50ms per check ✅

## Documentation Updated
- [x] Concept doc created: `/docs/CONCEPTS/risk-management.md`
- [x] Implementation guide updated
- [x] ADR created and approved
- [x] Code has comprehensive docstrings
- [x] OpenAPI spec updated
- [x] REPO_MAP.md updated

## Educational Value
This PR demonstrates:
- Pre-trade risk validation patterns
- Circuit breaker integration
- Position tracking and limits
- Error handling for risk violations
- Test strategies for safety-critical code

## Checklist
- [x] Tests added/updated
- [x] OpenAPI updated (API changed)
- [x] Migrations included (N/A - no DB changes)
- [x] Docs updated (ADR, concepts, guides)
- [x] ADR created (architectural change)
- [x] Zen-mcp deep review completed ✅

## Reviewer Notes
- Focus on risk calculation logic in `check_position_limits()`
- Verify circuit breaker integration is correct
- Check error messages are clear and actionable

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

### 9. Request Automated Reviews

**GitHub Action will automatically request reviews** from:
- `@codex`
- `@gemini-code-assist`

See `.github/workflows/pr-auto-review-request.yml`

**Automated review happens:**
- When PR is created
- When PR is reopened
- Can manually trigger with comment: `@codex @gemini-code-assist please review`

### 10. Wait for Review Feedback

**DO NOT merge until:**
- ✅ All automated reviewers approve (explicitly say "no issues")
- ✅ All HIGH/CRITICAL issues fixed
- ✅ All MEDIUM issues fixed or explicitly deferred by owner
- ✅ CI passes (all checks green)

**While waiting:**
- Monitor PR for comments
- Check CI status
- Be ready to respond to questions

### 11. Address Review Feedback Systematically

**⚠️ CRITICAL:** When reviewers (Codex, Gemini, or CI) find issues, follow this MANDATORY 5-phase process to avoid repeated CI failures and incomplete fixes.

#### Core Principle: Never Hide or Ignore Issues

**CRITICAL RULE:** Do NOT use ignore patterns, comments, or workarounds to hide actual issues.

**Required Actions:**
- ✅ **Fix the issue** - Address root cause immediately
- ✅ **Add to TODO list** - If fix requires separate work, create explicit task
- ❌ **NEVER ignore** - Don't add ignore patterns for actual broken links, tests, or checks

**Only Valid Ignore Patterns:**
- External HTTP URLs that 404 (third-party sites)
- Localhost URLs (development only)
- Staging/test environments

---

#### Phase 1: Collect ALL Issues (DO NOT START FIXING YET)

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

If you have more than 20 issues total, break into batches by severity:

```markdown
## Batch 1: CRITICAL + HIGH (5 issues)
- [ ] CRITICAL: Broken links
- [ ] HIGH: Null check missing

## Batch 2: MEDIUM (10 issues)
- [ ] MEDIUM: Logging

## Batch 3: LOW (8 issues)
- [ ] LOW: Variable naming
```

Work through batches sequentially: Fix Batch 1 → Test → Zen review → Commit. Repeat for each batch.

---

#### Phase 2: Fix ALL Issues (No Partial Fixes)

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

#### Phase 3: Verify Locally Before Committing

**⏱️ Expected Time:** 5-15 minutes

**3A. Run Complete Local Validation:**

```bash
# Run CI checks locally (mirrors exact CI environment)
make ci-local
# This runs: mypy → ruff → pytest with coverage
# Expected: ✅ All checks pass, coverage ≥80%

# If documentation changed, also check links
find docs -name "*.md" -exec markdown-link-check {} \; | grep "Status: 400" | wc -l
# Expected: 0

# Manual spot-check of fixes
git diff --staged
```

**3B. Only Proceed When:**
- ✅ ALL issues from Phase 1 are fixed
- ✅ `make ci-local` passes (all checks green)
- ✅ ALL link checks pass (if docs changed)
- ✅ No new errors introduced

**DO NOT COMMIT** until ALL checks pass locally!

---

#### Phase 4: Mandatory Zen-MCP Review of Fixes

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

Use this structured template:

```
"Reviewing PR feedback fixes for PR #XX. Requesting verification before commit.

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

**4C. Wait for Zen-MCP Approval:**

Zen-mcp will verify:
- Issues are truly fixed (not hidden/ignored)
- Fixes are correct and safe
- No new issues introduced
- Nothing was missed

**DO NOT COMMIT** until zen-mcp approves!

**4D. If Zen-MCP Finds NEW Issues:**

If zen-mcp identifies additional problems:
1. Add new issues to todo list
2. Loop back to Phase 2 (fix new issues)
3. Re-run Phase 3 (local validation)
4. Return to Phase 4 with updated fixes
5. Iterate until zen-mcp approves: "All fixes verified ✅"

**Important:** Zen-mcp catching additional issues is GOOD - it prevents bugs from reaching production.

---

#### Phase 5: Commit Once When Everything Approved

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

Fix issues found by CI:
- Map 11 IMPLEMENTATION_GUIDES references to TASKS files (CRITICAL)
- Fix emoji test assertion to use plain text (CRITICAL)

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

#### Anti-Patterns to AVOID

❌ **Anti-Pattern 1: Committing Before Finding All Issues**
- Bad: Fix one issue → commit → CI fails → fix another → commit → CI fails again
- Good: Collect ALL issues → fix ALL → test ALL → commit ONCE

❌ **Anti-Pattern 2: Hiding Issues with Ignore Patterns**
- Bad: Add ignore patterns for broken links/tests instead of fixing them
- Good: Fix or remove broken links, update tests

❌ **Anti-Pattern 3: Not Testing Locally**
- Bad: Fix → commit → push → wait for CI → CI fails
- Good: Fix → test locally → verify passes → commit → push → CI passes

❌ **Anti-Pattern 4: Skipping Zen Review**
- Bad: Fix → commit → push introduces new bug
- Good: Fix → zen review finds issue → fix properly → zen approves → commit

---

#### Summary: The 5-Phase Process

1. **COLLECT** - Gather issues from ALL sources (Codex, Gemini, CI)
2. **FIX** - Fix ALL issues (no partial fixes)
3. **VERIFY** - Test everything locally
4. **ZEN REVIEW** - Mandatory review with detailed context
5. **COMMIT ONCE** - Single commit with all fixes

**Validation Checklist:**
- [ ] Collected issues from ALL sources
- [ ] Fixed ALL HIGH/CRITICAL issues
- [ ] All local tests pass
- [ ] Zen-mcp reviewed and approved
- [ ] Ready to commit ALL fixes in ONE commit

### 12. Handle Conflicting Reviewer Feedback (If Needed)

**If reviewers disagree on specific implementation:**

See [/docs/STANDARDS/GIT_WORKFLOW.md](../../docs/STANDARDS/GIT_WORKFLOW.md#handling-conflicting-reviewer-feedback) for the tie-breaker policy.

**Summary:**
- Use Codex as tie-breaker when specific conflict exists
- Document the decision clearly in PR comment
- Only for the specific conflicting change
- All other feedback must still be addressed

### 13. Merge When Approved

**Merge only when:**
- ✅ All reviewers explicitly approve or say "no issues"
- ✅ All review comments addressed or explicitly deferred
- ✅ CI passes (all checks green)
- ✅ No merge conflicts

**Merge method:**
```bash
# Squash merge (if requested)
gh pr merge --squash

# Regular merge (preserves progressive commits - recommended)
gh pr merge --merge

# Or merge via GitHub UI
```

**After merge:**
- Delete feature branch (GitHub offers this automatically)
- Close any related issues/tickets
- Update project status docs if needed
- Task should already be marked DONE from Step 5

---

## Decision Points

### Should I create a Draft PR?

**Use Draft PR if:**
- Feature not complete but want early feedback
- CI is failing and you need help
- Want to show progress to team
- Testing approach needs validation

**Convert to regular PR when:**
- Feature complete
- All tests passing
- Ready for final review

**Create draft PR:**
```bash
gh pr create --draft
```

**Convert to ready:**
```bash
gh pr ready
```

### Should I squash commits before merging?

**Default: Keep progressive commits (don't squash)**
- ✅ Preserves development history
- ✅ Easier debugging with git bisect
- ✅ Shows incremental progress

**Squash only if:**
- User/team explicitly requests it
- Multiple commits fixing same typo
- Cleaning up accidental debug commits
- Project convention requires it

### Reviewer found issues - defer or fix now?

**Fix immediately if:**
- HIGH or CRITICAL severity
- Quick fix (<30 min)
- Related to current change

**Defer if:**
- LOW severity AND low impact
- Requires separate investigation
- Out of scope for this PR
- User explicitly approves deferral

**Document deferred issues:**
```markdown
## Deferred Issues
1. **LOW:** Optimize database query in `get_positions()`
   - Reason: Requires performance profiling
   - Follow-up: Created ticket P1.4T2
```

---

## Common Issues & Solutions

### Issue: CI Failing But Tests Pass Locally

**Symptom:** Local tests pass, CI fails with same tests

**ROOT CAUSE:** Not running exact same commands CI uses

**SOLUTION: Use `make ci-local` (mirrors CI exactly):**

```bash
# Run exact CI checks locally
make ci-local

# This automatically runs:
# 1. mypy --strict (same flags as CI)
# 2. ruff check (same config as CI)
# 3. pytest -m "not integration and not e2e" (same filter as CI)
```

**If `make ci-local` passes but CI still fails, check:**

**1. Environment differences:**
```bash
# Check Python version matches CI
python --version  # Should match .github/workflows/

# Check dependencies are in sync
poetry install --sync
```

**2. Missing environment variables:**
```bash
# Check .env.example for required vars
# Ensure CI has access to secrets
```

**3. Database state:**
```bash
# CI uses fresh DB, you might have stale data
# Reset local DB to match CI
make db-reset
make ci-local  # Re-test with fresh DB
```

### Issue: Automated Reviewers Not Responding

**Symptom:** Created PR but no automated review

**Solution:**
```bash
# Check GitHub Actions ran
gh pr checks

# Manually trigger review via comment
gh pr comment <PR_NUMBER> --body "@codex @gemini-code-assist please review this PR"

# Check if workflow file exists
ls .github/workflows/pr-auto-review-request.yml
```

### Issue: Merge Conflicts

**Symptom:** PR shows merge conflicts with master

**Solution:**
```bash
# Update master
git checkout master
git pull

# Go back to feature branch
git checkout feature/your-branch

# Merge or rebase master
git merge master  # Preserves all commits
# OR
git rebase master  # Cleaner history but rewrites commits

# Resolve conflicts
# Edit conflicting files
git add <resolved files>
git commit  # If merge
# OR
git rebase --continue  # If rebase

# Push (may need force push if rebased)
git push
# OR
git push --force-with-lease  # If rebased
```

### Issue: Forgot to Run Deep Zen Review

**Symptom:** Created PR without deep review

**Solution:**
```bash
# Request deep review immediately
"Deep review all branch changes with zen-mcp"

# Address findings
# Push fixes if needed

# Update PR description with zen review results
gh pr edit <PR_NUMBER> --body "$(cat updated_description.md)"
```

### Issue: PR Description Missing Information

**Symptom:** Reviewers asking for context you forgot to include

**Solution:**
```bash
# Edit PR description
gh pr edit <PR_NUMBER> --body "$(cat <<'EOF'
[Updated description with missing info]
EOF
)"

# Or edit via GitHub UI
gh pr view <PR_NUMBER> --web
```

---

## Examples

### Example 1: Standard PR Creation

```bash
# Feature complete after 6 progressive commits

# 1. Deep zen review
"Deep review all branch changes with zen-mcp"
# ✅ Approved - 1 MEDIUM issue found and fixed

# 2. Run CI checks locally
$ make ci-local
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Step 1/3: Type checking with mypy --strict
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Success: no issues found in 95 source files
Step 2/3: Linting with ruff
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
All checks passed!
Step 3/3: Running tests
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
======== 1015 passed, 15 skipped, 84 deselected in 53.04s =========
✓ All CI checks passed!

# 3. Push
$ git push

# 4. Create PR
$ gh pr create --title "Add position limit validation (P0T5)"
# (Enter description using template from step 6)

Creating pull request for feature/position-limits into master in LeeeWayyy/trading_platform

https://github.com/LeeeWayyy/trading_platform/pull/26

# 5. Automated reviews requested automatically

# 6. Wait for feedback...

# 7. Address feedback
# (Fix issues found)
$ git add <files>
$ git commit -m "Address review feedback"
$ git push

$ gh pr comment 26 --body "@codex @gemini-code-assist updated to address your feedback, please verify"

# 8. Reviewers approve
# Codex: "All issues resolved, approved ✅"
# Gemini: "No issues found ✅"

# 9. Merge
$ gh pr merge 26 --merge
✓ Merged Pull Request #26 (Add position limit validation)
✓ Deleted branch feature/position-limits

# Done!
```

### Example 2: Handling Review Feedback Loop

```bash
# PR created, Codex finds issues

# Codex review:
# - HIGH: Missing null check in position calculation
# - MEDIUM: Add logging for limit violations
# - LOW: Variable naming could be clearer

# Fix HIGH and MEDIUM immediately
# (Fix code)
$ git add apps/execution_gateway/order_placer.py
$ git commit -m "Add null check and logging per Codex review"
$ git push

# Request re-review
$ gh pr comment 26 --body "Fixed the null check and added logging.

@codex please review latest commit to verify fixes."

# Codex: "Fixes look good, but now notice edge case in error handling"

# Fix new issue
$ git add apps/execution_gateway/order_placer.py
$ git commit -m "Improve error handling for edge case"
$ git push

$ gh pr comment 26 --body "@codex verified edge case fix, please approve if no further issues"

# Codex: "All issues resolved ✅"
# Gemini: "No issues ✅"

# Merge!
$ gh pr merge 26 --merge
```

---

## Validation

**How to verify this workflow succeeded:**
- [ ] PR created and visible on GitHub
- [ ] PR description complete (summary, zen review, testing, docs)
- [ ] Automated reviewers mentioned (@codex @gemini-code-assist)
- [ ] CI checks running or passed
- [ ] PR linked to relevant issue/ticket
- [ ] Zen-mcp deep review confirmation included

**What to check if something seems wrong:**
- Check `gh pr list` - PR should be visible
- Check `gh pr checks` - CI status
- Check GitHub Actions tab - review automation ran
- Verify branch was pushed: `git ls-remote origin feature/your-branch`

---

## Related Workflows

- [04-zen-review-deep.md](./04-zen-review-deep.md) - MANDATORY deep review before PR
- [01-git-commit.md](./01-git-commit.md) - Progressive commits leading to PR
- [10-ci-triage.md](./10-ci-triage.md) - Handling CI failures
- [05-testing.md](./05-testing.md) - Running tests before PR

---

## References

**Standards & Policies:**
- [/docs/STANDARDS/GIT_WORKFLOW.md](../../docs/STANDARDS/GIT_WORKFLOW.md) - PR policies and review requirements
- [/docs/STANDARDS/TESTING.md](../../docs/STANDARDS/TESTING.md) - PR testing checklist

**Tools:**
- GitHub CLI: https://cli.github.com/
- GitHub Actions: `.github/workflows/pr-auto-review-request.yml`

---

**Maintenance Notes:**
- Update when PR template changes
- Review when GitHub Actions workflows updated
- Notify @development-team if automated review process changes
