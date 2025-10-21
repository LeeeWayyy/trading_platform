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

```bash
# Run full test suite
make test

# Run linting
make lint

# Check coverage if available
make coverage
```

**Expected:** ✅ All green, no failures

**If any failures:**
- Fix them immediately
- Run zen review of fixes
- Don't create PR until all pass

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

### 5. Gather PR Information

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

### 6. Create PR Using GitHub CLI

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
- **ADR:** [ADR-0011: Risk Management System](../docs/ADRs/0011-risk-management-system.md)
- **Implementation Guide:** [P1.2T3 Risk Management](../docs/IMPLEMENTATION_GUIDES/p1.2t3-risk-management.md)

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

### 7. Request Automated Reviews

**GitHub Action will automatically request reviews** from:
- `@codex`
- `@gemini-code-assist`

See `.github/workflows/pr-auto-review-request.yml`

**Automated review happens:**
- When PR is created
- When PR is reopened
- Can manually trigger with comment: `@codex @gemini-code-assist please review`

### 8. Wait for Review Feedback

**DO NOT merge until:**
- ✅ All automated reviewers approve (explicitly say "no issues")
- ✅ All HIGH/CRITICAL issues fixed
- ✅ All MEDIUM issues fixed or explicitly deferred by owner
- ✅ CI passes (all checks green)

**While waiting:**
- Monitor PR for comments
- Check CI status
- Be ready to respond to questions

### 9. Address Review Feedback

**When reviewers find issues:**

```bash
# Fix the issues locally
# Stage and commit fixes
git add <files>

# Run zen review of fixes (quick review OK for small fixes)
"Review my staged changes with zen-mcp"

# Commit after approval
git commit -m "Address review feedback: improve error messages"

# Push fixes
git push

# Request re-review
gh pr comment <PR_NUMBER> --body "Fixed the issues.

@codex @gemini-code-assist please review the latest commit to verify the fixes.

Changes made:
- Improved error message clarity
- Added missing edge case handling
- Fixed type hint

Please check out the latest commit to avoid cached review results."
```

**IMPORTANT:** Explicitly mention checking latest commit to avoid cache issues!

**Iterate until approved:**
1. Reviewers comment with issues
2. You fix issues
3. Push fixes
4. Request re-review
5. Repeat until "no issues" or "approved"

### 10. Handle Conflicting Reviewer Feedback (If Needed)

**If reviewers disagree on specific implementation:**

See [/docs/STANDARDS/GIT_WORKFLOW.md](../../docs/STANDARDS/GIT_WORKFLOW.md#handling-conflicting-reviewer-feedback) for the tie-breaker policy.

**Summary:**
- Use Codex as tie-breaker when specific conflict exists
- Document the decision clearly in PR comment
- Only for the specific conflicting change
- All other feedback must still be addressed

### 11. Merge When Approved

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

**Causes & Solutions:**

**1. Environment differences:**
```bash
# Check Python version matches CI
python --version  # Should match .github/workflows/

# Check dependencies
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
make test
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

# 2. Tests
$ make test && make lint
===================== 82 passed in 3.41s ======================
✅ All checks passed

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
