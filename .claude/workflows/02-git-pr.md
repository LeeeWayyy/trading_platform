# Pull Request Creation Workflow

**Purpose:** Create well-documented pull requests with automated quality checks
**When:** Feature complete, deep zen-mcp review passed, all tests green
**Prerequisites:** All progressive commits done, documentation updated
**Expected Outcome:** PR ready for team review and merge

---

## Quick Reference

**Git Commands:** See [Git Commands Reference](./_common/git-commands.md)
**Testing:** See [Test Commands Reference](./_common/test-commands.md)
**Zen Review:** See [Zen-MCP Review Process](./_common/zen-review-process.md)

---

## When to Create PR

**Create PR when:**
- ✅ Feature/fix complete (all requirements met)
- ✅ Deep zen-mcp review passed (Tier 2 - MANDATORY)
- ✅ All tests passing (`make ci-local`)
- ✅ Documentation updated
- ✅ Ready for team review

**Do NOT create PR if:**
- ❌ Feature incomplete (use draft PR)
- ❌ Tests failing
- ❌ Haven't run deep zen review
- ❌ Breaking changes without ADR

---

## Step-by-Step Process

### 1. Run Deep Zen-MCP Review (MANDATORY)

**🔒 Before creating ANY pull request, comprehensive Tier 2 review is MANDATORY.**

See [Zen-MCP Review Process](./_common/zen-review-process.md) for complete Tier 2 (Deep Review) workflow.

**Quick summary:**
- Reviews: architecture, test coverage, edge cases, integration, docs
- Uses gemini + codex (3-5 minutes)
- Address ALL HIGH/CRITICAL findings
- Document any deferred MEDIUM/LOW issues
- Only proceed after approval

**See Also:** [04-zen-review-deep.md](./04-zen-review-deep.md) for detailed workflow

### 2. Verify All Tests Pass

**Run exact same checks as CI:**

```bash
make ci-local
```

See [Test Commands Reference](./_common/test-commands.md) for complete testing guide.

**Expected:** ✅ All green, coverage ≥80%

**If failures:**
- Fix immediately
- Run zen review of fixes
- Re-run `make ci-local`
- Don't create PR until passing

### 3. Verify Branch Status

```bash
git branch --show-current  # Should be feature branch
git log master..HEAD --oneline  # Check commits
git status  # Should be clean
```

See [Git Commands Reference](./_common/git-commands.md) for git operations.

### 4. Push Latest Changes

```bash
git push
# Or if first time: git push -u origin feature/your-branch-name
```

### 5. Mark Task as Complete

```bash
# Mark PROGRESS → DONE
./scripts/tasks.py complete P1T9

# Update all links to task file (MANDATORY)
grep -r "P1T9_TASK.md\|P1T9_PROGRESS.md" docs/
# Update each to point to P1T9_DONE.md

# Commit completion
git add docs/TASKS/P1T9_DONE.md
git commit -m "Mark P1T9 as complete and update links"
git push
```

**Why before PR:** Prevents broken links in PR

### 6. Update Documentation

**A. Concept Documentation (if applicable)**

Create docs explaining new patterns/architecture:
- `docs/CONCEPTS/` - What, how, why explanations
- Examples for beginners
- Best practices

**B. README.md Updates**

- Add new features to "Key Achievements"
- Update relevant sections
- Add concept doc links
- Update statistics

**C. Getting Started Guides**

Update if your changes affect developer workflow:
- `docs/GETTING_STARTED/` guides
- `docs/RUNBOOKS/` procedures

**D. Commit Documentation**

```bash
git add docs/CONCEPTS/ README.md docs/GETTING_STARTED/
git commit -m "Add concept documentation and update README

- Added [concept].md concept doc
- Updated README with [feature] section
- Added [guide] for developers"
git push
```

### 7. Create PR Using GitHub CLI

**Basic creation:**
```bash
gh pr create
```

**PR title format:** `[Type] Brief description (Ticket)`

**PR description template:**

See [PR Body Template](../prompts/pr-body-template.md) for complete PR template.

**Required sections:**
- Summary & Related Work
- Changes Made checklist
- Zen-MCP Review evidence (continuation_id - MANDATORY)
- Testing completed
- Documentation updated
- Educational value
- Reviewer notes

### 8. Wait for Automated Reviews

GitHub Action automatically requests reviews from `@codex` and `@gemini-code-assist`.

**DO NOT merge until:**
- ✅ All automated reviewers approve
- ✅ All HIGH/CRITICAL issues fixed
- ✅ All MEDIUM issues fixed or deferred
- ✅ CI passes

### 9. Address Review Feedback (5-Phase Process)

**⚠️ MANDATORY to avoid repeated CI failures:**

#### Phase 1: Collect ALL Issues

```bash
gh pr view <PR> --json comments
gh pr checks <PR>
make ci-local  # Reproduce locally
```

Create comprehensive todo with ALL issues before fixing.

#### Phase 2: Fix ALL Issues Together

- Fix HIGH/CRITICAL (mandatory)
- Fix MEDIUM (or document deferral)
- Fix LOW if quick (<10 min)
- Test: `make ci-local`
- **Don't commit yet!**

#### Phase 3: Verify Locally

```bash
make ci-local  # Must pass 100%
git diff --staged  # Review changes
```

#### Phase 4: Zen-MCP Review (MANDATORY)

```bash
git add -A
# Request review with context
```

**Template:**
```
"Reviewing PR feedback fixes for PR #XX.

## Issues Fixed:
- [Severity] [Description] - [Fix applied]

## Local Validation:
- ✅ All tests pass
- ✅ Linting passes

Please verify all fixes before commit."
```

**Don't commit** until zen-mcp approves!

See [Zen-MCP Review Process](./_common/zen-review-process.md) for Tier 1 quick review.

#### Phase 5: Commit Once When Approved

```bash
git commit -m "Address all PR review feedback

[List all fixes]

All tests pass locally.
Zen-mcp review: ALL fixes verified ✅

🤖 Generated with [Claude Code](https://claude.com/claude-code)
Co-Authored-By: Claude <noreply@anthropic.com>"

git push
```

### 10. Merge When Approved

**Merge only when:**
- ✅ All reviewers approve
- ✅ All comments addressed
- ✅ CI passes
- ✅ No merge conflicts

**Merge method:**
```bash
gh pr merge --merge  # Preserves progressive commits (recommended)
# Or: gh pr merge --squash  # If requested
```

**After merge:**
- Delete feature branch
- Close related issues
- Task already marked DONE from Step 5

---

## Decision Points

### Should I create a Draft PR?

**Use Draft PR if:**
- Feature not complete but want feedback
- CI failing and need help
- Testing approach needs validation

**Create:**
```bash
gh pr create --draft
```

**Convert to ready:**
```bash
gh pr ready
```

### Should I squash commits?

**Default: Keep progressive commits**
- Clear history
- Easier debugging
- Shows incremental progress

**Squash only if:**
- User/team requests
- Multiple typo fixes
- Project convention requires

### Defer or fix review issues?

**Fix immediately if:**
- HIGH/CRITICAL severity
- Quick fix (<30 min)
- Related to current change

**Defer if:**
- LOW severity + low impact
- Requires separate investigation
- Out of scope
- User approves deferral

**Document deferred:**
```markdown
## Deferred Issues
1. **LOW:** Optimize query in `get_positions()`
   - Reason: Requires profiling
   - Follow-up: P1.4T2
```

---

## Common Issues

### CI Failing But Tests Pass Locally

**Solution:** Use `make ci-local` to mirror CI exactly

See [Test Commands Reference](./_common/test-commands.md)

### Merge Conflicts

```bash
git checkout master && git pull
git checkout feature/your-branch
git merge master
# Resolve conflicts, then push
```

See [Git Commands Reference](./_common/git-commands.md) for git operations.

### Forgot Deep Zen Review

Request immediately:
```
"Deep review all branch changes with zen-mcp"
```

Then update PR description with continuation_id.

---

## Validation Checklist

- [ ] PR created on GitHub
- [ ] PR description complete (summary, zen review, testing, docs)
- [ ] Automated reviewers mentioned
- [ ] CI checks running/passed
- [ ] Zen-mcp deep review confirmation included

---

## Related Workflows

- [04-zen-review-deep.md](./04-zen-review-deep.md) - MANDATORY deep review
- [01-git-commit.md](./01-git-commit.md) - Progressive commits
- [10-ci-triage.md](./10-ci-triage.md) - CI failure handling
- [05-testing.md](./05-testing.md) - Running tests

## References

- [Git Commands Reference](./_common/git-commands.md) - Git operations and PR creation
- [Test Commands Reference](./_common/test-commands.md) - Testing commands
- [Zen-MCP Review Process](./_common/zen-review-process.md) - Three-tier review system
- [/docs/STANDARDS/GIT_WORKFLOW.md](../../docs/STANDARDS/GIT_WORKFLOW.md) - PR policies
- [/docs/STANDARDS/TESTING.md](../../docs/STANDARDS/TESTING.md) - PR testing checklist
