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

**Before creating ANY pull request, comprehensive review is MANDATORY.**

See [04-zen-review-deep.md](./04-zen-review-deep.md) for complete deep review workflow.

**Quick summary:**
- Reviews: architecture, test coverage, edge cases, integration, docs
- Address ALL HIGH/CRITICAL findings before PR
- Document any deferred MEDIUM/LOW issues
- Only proceed after zen-mcp approval

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
# Prompts for: Title, Body, Base branch
```

**Use format:** `[Type] Brief description (Ticket)`

**PR description template:**

See [.claude/examples/git-pr/good-pr-description-template.md](../examples/git-pr/good-pr-description-template.md) for complete template with all required sections:
- Summary & Related Work
- Changes Made checklist
- Zen-MCP Review evidence (MANDATORY)
- Testing completed
- Documentation updated
- Educational value
- Reviewer notes

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

**⚠️ MANDATORY 5-Phase Process** to avoid repeated CI failures:

#### Core Principle
- ✅ Fix the root cause
- ❌ Never hide issues with ignore patterns
- ✅ Create TODO for deferred work

**Only valid ignores:** External HTTP URLs, localhost, staging environments

---

#### Phase 1: Collect ALL Issues First

```bash
# Gather from all sources
gh pr view <PR> --json comments
gh pr checks <PR>
make ci-local  # Reproduce locally
```

Create comprehensive todo list with ALL issues before fixing anything.

---

#### Phase 2: Fix ALL Issues Together

- Fix all HIGH/CRITICAL (mandatory)
- Fix all MEDIUM (or document deferral)
- Fix LOW if quick (<10 min total)
- Test each fix: `make ci-local`
- **Don't commit yet!**

---

#### Phase 3: Verify Locally

```bash
make ci-local  # Must pass 100%
git diff --staged  # Review all changes
```

Only proceed when ALL checks pass locally.

---

#### Phase 4: Zen-MCP Review of Fixes (MANDATORY)

```bash
git add -A
# Request detailed review with context
```

**Template:**
```
"Reviewing PR feedback fixes for PR #XX.

## Issues Fixed:
- [Severity] [Brief description] - [Fix applied]
[List all fixes]

## Local Validation:
- ✅ All tests pass
- ✅ Linting passes
- ✅ [Type-specific checks]

Please verify all fixes before commit."
```

**Don't commit** until zen-mcp approves!

If zen finds new issues → Loop back to Phase 2.

---

#### Phase 5: Commit Once When Approved

```bash
git commit -m "Address all PR review feedback from Codex, Gemini, and CI

[List all fixes by source]

All tests pass locally.
Zen-mcp review: ALL fixes verified ✅

🤖 Generated with [Claude Code](https://claude.com/claude-code)
Co-Authored-By: Claude <noreply@anthropic.com>"

git push
```

Notify reviewers with summary of fixes.

---

#### Anti-Patterns to Avoid

❌ Committing before finding all issues
❌ Hiding issues with ignore patterns  
❌ Not testing locally (`make ci-local`)
❌ Skipping zen review of fixes

---

#### Summary Checklist

- [ ] Collected issues from ALL sources (Codex, Gemini, CI)
- [ ] Fixed ALL HIGH/CRITICAL issues
- [ ] All local tests pass (`make ci-local`)
- [ ] Zen-mcp reviewed and approved fixes
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

<details>
<summary><h2>📖 Appendix: Common Issues & Solutions</h2></summary>

### CI Failing But Tests Pass Locally

**Solution:** Use `make ci-local` to mirror CI exactly

```bash
make ci-local  # Runs: mypy → ruff → pytest (same as CI)
```

If still fails: Check Python version, dependencies (`poetry install --sync`), DB state (`make db-reset`)

### Automated Reviewers Not Responding

```bash
gh pr comment <PR_NUMBER> --body "@codex @gemini-code-assist please review"
```

### Merge Conflicts

```bash
git checkout master && git pull
git checkout feature/your-branch
git merge master  # Or: git rebase master
# Resolve conflicts, then push
```

### Forgot Deep Zen Review

Request immediately: `"Deep review all branch changes with zen-mcp"`, then update PR description

### Missing PR Information

```bash
gh pr edit <PR_NUMBER> --body "$(cat updated_description.md)"
```

</details>

---

## Examples

**See extracted examples in `.claude/examples/git-pr/`:**

- **[Standard PR Creation](../examples/git-pr/example-standard-pr-creation.md)** - Complete workflow from feature completion to merge
- **[Review Feedback Loop](../examples/git-pr/example-review-feedback-loop.md)** - Handling multiple rounds of review feedback

These examples show real scenarios with complete commands, timing, and key decision points.

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
