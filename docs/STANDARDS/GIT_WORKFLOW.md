# Git Workflow Standards & Policies

**Purpose:** Define mandatory git practices, commit standards, and PR policies
**Audience:** All developers and AI assistants
**Type:** Normative (MUST follow)
**Last Updated:** 2025-10-21

---

## 🎯 Quick Reference

**For step-by-step procedures, see:**
- **Git commits:** [`.claude/workflows/01-git-commit.md`](../../.claude/workflows/01-git-commit.md)
- **Pull requests:** [`.claude/workflows/02-git-pr.md`](../../.claude/workflows/02-git-pr.md)
- **Zen-mcp review (quick):** [`.claude/workflows/03-zen-review-quick.md`](../../.claude/workflows/03-zen-review-quick.md)
- **Zen-mcp review (deep):** [`.claude/workflows/04-zen-review-deep.md`](../../.claude/workflows/04-zen-review-deep.md)

**This document defines:**
- ✅ What you MUST do (policies)
- ❌ What you MUST NOT do (anti-patterns)
- 📋 Standards and requirements

---

## 📜 Core Policies

### Rule #1: Never Work on Master Directly

**POLICY:** ❌ NEVER commit directly to `master` or `main` branch

**REQUIRED:**
- ✅ ALWAYS create a feature branch for your work
- ✅ Use pull requests for all changes
- ✅ Merge via PR review process only

**Exemptions:** None. Emergency hotfixes still require branch + expedited PR process.

### Rule #2: Progressive Commits (MANDATORY)

**POLICY:** Commit early, commit often during development

**REQUIRED:**
- ✅ Commit at minimum every 30-60 minutes of active development
- ✅ Make logical, incremental commits (not one massive commit)
- ✅ Each commit should compile and pass basic checks
- ✅ Push regularly to backup work

**See:** [`.claude/workflows/01-git-commit.md`](../../.claude/workflows/01-git-commit.md) for commit procedures

**Benefits:**
- Regular backups of work in progress
- Easier to revert specific changes if needed
- Better tracking of development progress
- Can resume work after interruptions
- Makes debugging easier (git bisect)

**Anti-Pattern:**
```bash
# ❌ BAD - Single massive commit after 8 hours
git commit -m "Implement entire Alpaca connector (2000 lines changed)"

# ✅ GOOD - Progressive commits every 30-60 min
git commit -m "Add Alpaca API client skeleton"
# ... 30 min later ...
git commit -m "Implement authentication and connection"
# ... 45 min later ...
git commit -m "Add rate limiting with exponential backoff"
```

### Rule #3: Mandatory Zen-MCP Review (CRITICAL)

**POLICY:** ALL code commits by AI assistants MUST be reviewed by zen-mcp before committing

**REQUIRED:**
- ✅ **Quick review** before each progressive commit ([`.claude/workflows/03-zen-review-quick.md`](../../.claude/workflows/03-zen-review-quick.md))
- ✅ **Deep review** before creating PR ([`.claude/workflows/04-zen-review-deep.md`](../../.claude/workflows/04-zen-review-deep.md))
- ✅ Fix ALL HIGH/CRITICAL issues before committing (blocking)
- ✅ Address or document MEDIUM issues
- ✅ Cannot commit if zen finds blocking issues

**Exemptions (only these may skip zen review):**
- Documentation-only changes (add `#docs-only` to commit message)
- Auto-generated files (package-lock.json, poetry.lock)
- Emergency hotfixes with explicit user approval + mandatory post-commit review

**Override format (emergencies only):**
```bash
git commit -m "Add position limit validation

ZEN_REVIEW_OVERRIDE: Server temporarily unavailable
Reason: Urgent hotfix for production issue
Will perform post-commit review and create follow-up PR if issues found"
```

**Enforcement:**
- ❌ CANNOT commit without zen-mcp review
- ❌ CANNOT commit if zen finds HIGH/CRITICAL issues
- ✅ CAN commit with user override if zen unavailable (document reason)

**See:**
- Quick review: [`.claude/workflows/03-zen-review-quick.md`](../../.claude/workflows/03-zen-review-quick.md)
- Deep review: [`.claude/workflows/04-zen-review-deep.md`](../../.claude/workflows/04-zen-review-deep.md)
- Implementation: `/docs/CONCEPTS/workflow-optimization-zen-mcp.md`

### Rule #4: Mandatory Testing Before Commit

**POLICY:** All tests MUST pass before committing

**REQUIRED:**
- ✅ Run `make test` before every commit
- ✅ Run `make lint` before every commit
- ✅ Fix all test failures before committing
- ✅ Fix all lint errors before committing

**Anti-Pattern:**
```bash
# ❌ BAD - Committing with failing tests
git commit -m "Add feature X"  # 5 tests failing
# Now CI fails, requires another commit to fix

# ✅ GOOD - Tests pass locally before commit
make test && make lint
# All pass ✅
git commit -m "Add feature X"
```

**See:** [`.claude/workflows/05-testing.md`](../../.claude/workflows/05-testing.md) for testing procedures

---

## 📋 Branch Naming Standards

**REQUIRED:** Use consistent branch naming conventions

**Format:** `<type>/<ticket>-<brief-description>`

**Types:**
- `feature/` - New features
- `fix/` - Bug fixes
- `docs/` - Documentation only
- `refactor/` - Refactoring
- `chore/` - Maintenance

**Examples:**
```bash
feature/t4-idempotent-orders     # ✅ GOOD
fix/circuit-breaker-recovery      # ✅ GOOD
docs/update-testing-guide         # ✅ GOOD
refactor/extract-risk-checks      # ✅ GOOD
chore/upgrade-dependencies        # ✅ GOOD

my-changes                        # ❌ BAD - No type
new-feature                       # ❌ BAD - No ticket reference
```

---

## 📝 Commit Message Standards

### Progressive Commit Messages (30-60 min cadence)

**Format:** Concise but clear description of what changed

**REQUIRED:**
- ✅ Start with imperative verb ("Add", "Fix", "Update", "Remove")
- ✅ Describe what changed (not how or why)
- ✅ Keep first line ≤72 characters
- ✅ Can be concise since PR provides context

**Examples:**
```bash
# ✅ GOOD
"Add Alpaca API client wrapper"
"Implement rate limiting with exponential backoff"
"Add unit tests for historical data fetching"
"Fix type hints in corporate actions module"

# ACCEPTABLE during development
"WIP: Adding authentication logic"
"Draft: Initial market data connector structure"

# ❌ BAD - Too vague
"Fixed stuff"
"Updates"
"Changes"
```

### Final PR Merge Commit (comprehensive)

**Format:** Detailed multi-line commit message

**REQUIRED:**
- ✅ Summary line ≤72 characters
- ✅ Blank line after summary
- ✅ Detailed description of changes
- ✅ Reference ticket/ADR
- ✅ Include co-author attribution if using Claude Code

**Template:**
```bash
Implement idempotent order submission (ADR-0004)

- Add SHA256-based hash function
- Include order params and date in hash
- Truncate to 24 chars for Alpaca compatibility
- Add unit tests for collision resistance

Closes #T4

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
```

**See:** [`.claude/workflows/01-git-commit.md`](../../.claude/workflows/01-git-commit.md) for commit procedures

---

## 🔍 Zen-MCP Review Requirements

### Trading Safety Focus

Zen-mcp MUST verify these critical patterns:

**Trading Safety:**
- ✅ Circuit breaker checks before order placement
- ✅ Idempotent order IDs (deterministic, no duplicates)
- ✅ Position limit validation (per-symbol and portfolio-wide)
- ✅ DRY_RUN mode handling
- ✅ Risk check failures must block orders

**Code Quality:**
- ✅ Race conditions in concurrent code (Redis WATCH/MULTI/EXEC)
- ✅ Proper error handling (no swallowing exceptions)
- ✅ Structured logging with context (strategy_id, client_order_id)
- ✅ Type hints and documentation
- ✅ Test coverage for changes

**Data Quality:**
- ✅ Freshness checks (<30 min old data)
- ✅ Quality gate validations
- ✅ Proper timezone handling (UTC)

### Review Workflow

**Progressive commits (every 30-60 min):**
1. Stage changes
2. Request zen-mcp quick review (MANDATORY)
3. Fix ALL findings (HIGH/CRITICAL blocking)
4. Re-request review to verify fixes
5. Commit only when approved

**Before PR:**
1. Request zen-mcp deep review (MANDATORY)
2. Fix ALL HIGH/CRITICAL issues (blocking)
3. Address or document MEDIUM issues
4. Include zen review summary in PR description

**See:**
- Quick review workflow: [`.claude/workflows/03-zen-review-quick.md`](../../.claude/workflows/03-zen-review-quick.md)
- Deep review workflow: [`.claude/workflows/04-zen-review-deep.md`](../../.claude/workflows/04-zen-review-deep.md)

---

## 📋 Pull Request Policies

### PR Creation Requirements

**BEFORE creating PR, MUST:**
- ✅ All progressive commits completed
- ✅ Deep zen-mcp review completed and approved
- ✅ All tests passing (`make test && make lint`)
- ✅ Documentation updated
- ✅ ADR created if architectural change
- ✅ All files committed

**PR Description MUST include:**
- Summary of changes (1-2 sentences)
- Related work (ticket, ADR, implementation guide)
- Changes made checklist
- Testing completed checklist
- Documentation updated checklist
- Zen-mcp review summary

**See:** [`.claude/workflows/02-git-pr.md`](../../.claude/workflows/02-git-pr.md) for PR creation procedures

### PR Size Guidelines

**RECOMMENDED:**
- < 500 lines of code changes
- < 10 files changed
- Single focused change

**If larger:** Split into multiple PRs
```bash
# ✅ GOOD - Split large feature
PR #1: Add deterministic ID generation (ADR-0004)
PR #2: Integrate ID generation into order submission
PR #3: Add duplicate detection logic
PR #4: Add integration tests

# ❌ BAD - One massive PR
PR #1: Implement entire idempotency system (2000 lines, 25 files)
```

### PR Template (REQUIRED)

**Minimum required sections:**
```markdown
## Summary
Brief description of what was implemented (1-2 sentences).

## Related Work
- Ticket: #T4 or /docs/TASKS/P0_TICKETS.md#t4
- ADR: ADR-0004 (if applicable)
- Implementation Guide: /docs/IMPLEMENTATION_GUIDES/...

## Changes Made
- [ ] Item 1
- [ ] Item 2

## Testing Completed
- [x] Unit tests pass (`make test`)
- [x] Linting passes (`make lint`)
- [x] Manual testing in DRY_RUN mode

## Documentation Updated
- [x] Concept docs (if trading-specific)
- [x] Implementation guide
- [x] ADR (if architectural change)
- [x] Code has docstrings
- [x] OpenAPI spec (if API changed)

## Zen MCP Review
- ✅ Progressive reviews: All commits reviewed and approved
- ✅ Deep review: Completed before PR creation
- ✅ Issues found and fixed: X HIGH, Y MEDIUM, Z LOW
- ✅ Final approval: Granted

## Checklist
- [x] Tests added/updated
- [x] OpenAPI updated (if API changed)
- [x] Migrations included (if DB changed)
- [x] Docs updated

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

---

## 🤖 Automated Code Review Policy

### GitHub App Reviewers (REQUIRED)

**POLICY:** All PRs MUST be reviewed by automated reviewers before merging

**Automated reviewers:**
- `@codex` - Code quality, security, testing
- `@gemini-code-assist` - Alternative perspective, best practices

**Automation:**
- GitHub Actions automatically requests reviews on PR creation/reopen
- See: `.github/workflows/pr-auto-review-request.yml`

**REQUIRED:**
- ✅ Wait for reviewers to respond
- ✅ Address ALL feedback (see Review Feedback Policy below)
- ✅ Request re-review after fixing issues
- ✅ Wait for explicit approval before merging

### Review Feedback Policy (CRITICAL)

**ALL review feedback MUST be addressed before merging.**

**REQUIRED:**
1. **High Priority Issues:** MUST fix immediately if confirmed
2. **Medium Priority Issues:** MUST fix immediately if confirmed
3. **Low Priority Issues:** MUST fix immediately if confirmed
4. **Only Exception:** Owner explicitly approves deferring specific issues

**After fixing:**
- ✅ Push fixes
- ✅ Request re-review from `@codex` and `@gemini-code-assist`
- ✅ Ask reviewers to check latest commit (avoid caching)
- ✅ **WAIT for explicit approval** - do NOT assume fixes are sufficient

**You are ONLY allowed to merge when:**
- ✅ All reviewers explicitly say "no issues" or approve
- ✅ All review comments addressed or explicitly deferred by owner
- ✅ All tests pass
- ❌ **NEVER** merge without explicit reviewer approval

**If unsure about deferring:**
- Ask owner: "Reviewer X raised issue Y. Should I fix now or defer?"
- Wait for owner's explicit approval before deferring
- Document deferred issues in PR description or create follow-up tickets

### Handling Conflicting Reviewer Feedback

**Problem:** Review deadlocks when reviewers provide conflicting feedback

**Example deadlock:**
1. Gemini suggests adding feature X
2. You implement feature X
3. Codex says feature X causes regression
4. You remove feature X
5. Gemini complains feature X missing again
6. **Review deadlock** - cannot satisfy both

**Resolution: Codex as Tie-Breaker**

**POLICY:** When conflicting feedback creates a review loop on a specific change:
1. Identify the conflict (reviewers disagree on same implementation detail)
2. Use Codex as golden standard if Codex approves
3. Document decision clearly
4. Scope is limited to specific conflicting change only

**When to apply tie-breaker:**
✅ Same specific change reviewed multiple times
✅ Clear conflict between reviewer suggestions
✅ Codex explicitly says implementation is correct
✅ Loop occurred 2+ times on same issue
✅ Regression or correctness at stake

**When NOT to apply:**
❌ Reviewers comment on different parts
❌ Suggestions are complementary (can implement both)
❌ Only 1 round of feedback (not yet a loop)
❌ Owner has not approved using tie-breaker
❌ Issue is about code style (not correctness)

**Documentation format:**
```markdown
## Conflicting Reviewer Feedback Resolution

**Issue:** Gemini suggests X, Codex says X causes regression

**Attempts:**
1. Implemented X (commit abc123)
2. Codex identified regression risk
3. Reverted (commit def456)
4. Gemini re-requested same change

**Resolution:** Using Codex as tie-breaker per GIT_WORKFLOW.md
- Codex confirmed existing implementation is correct
- X would prevent graceful degradation
- Keeping current implementation

**Scope:** This applies ONLY to this specific change
- All other Gemini feedback is still being addressed

@codex please confirm this is still acceptable
```

---

## 🚫 Anti-Patterns & Prohibited Actions

### Prohibited Git Actions

Claude Code MUST NOT:
- ❌ Push to main/master directly
- ❌ Force push (unless explicitly requested and justified)
- ❌ Merge PRs without explicit reviewer approval
- ❌ Delete branches without confirmation
- ❌ Modify git history with rebase/amend (unless explicitly requested)
- ❌ Commit without zen-mcp review (except exemptions)
- ❌ Commit with failing tests
- ❌ Skip progressive commits (must commit every 30-60 min)

### Prohibited PR Practices

❌ **BAD:**
- Creating PR without deep zen-mcp review
- Creating PR with failing tests
- Creating PR without documentation updates
- Merging without reviewer approval
- Ignoring review feedback
- One massive commit after hours of work
- Vague commit messages ("Fixed stuff", "Updates")

✅ **GOOD:**
- Progressive commits every 30-60 min with zen review
- All tests passing before PR
- Documentation updated
- Zen deep review before PR creation
- All reviewer feedback addressed
- Explicit approval before merge

---

## 📊 Metrics & Monitoring

**Track weekly in ops sync:**

| Metric | Target | Purpose |
|--------|--------|---------|
| Commits reviewed by zen-mcp | 100% | Ensure compliance |
| Review time (median) | <60s | Avoid friction |
| Issues caught (HIGH/CRITICAL) | Track trend | Measure value |
| Zen override rate | <5% | Monitor exceptions |
| PR review cycles | 1-2 | Measure quality |
| Time to merge | <2 days | Track velocity |

**Benefits of zen-mcp integration:**
- Find issues in ~30s vs 10-15min PR review
- Fix while context fresh (not days later)
- 50-66% reduction in PR review cycles
- 70-90% fewer issues per PR
- Trading safety enforced at commit time

---

## 📚 Related Documentation

**Workflow procedures (step-by-step how-to):**
- [Git commit workflow](../../.claude/workflows/01-git-commit.md)
- [PR creation workflow](../../.claude/workflows/02-git-pr.md)
- [Zen quick review workflow](../../.claude/workflows/03-zen-review-quick.md)
- [Zen deep review workflow](../../.claude/workflows/04-zen-review-deep.md)
- [Testing workflow](../../.claude/workflows/05-testing.md)

**Other standards:**
- [CODING_STANDARDS.md](./CODING_STANDARDS.md) - Python style and patterns
- [TESTING.md](./TESTING.md) - Test requirements
- [DOCUMENTATION_STANDARDS.md](./DOCUMENTATION_STANDARDS.md) - Docstring requirements
- [ADR_GUIDE.md](./ADR_GUIDE.md) - Architecture Decision Records

**Implementation guides:**
- `/docs/IMPLEMENTATION_GUIDES/workflow-optimization-zen-mcp.md` - Zen-MCP setup

**CI/CD:**
- `.github/workflows/ci-tests-coverage.yml` - Automated test runner
- `.github/workflows/pr-auto-review-request.yml` - Automated review requests

---

## 🔧 Setup Prerequisites

**Required tools:**
- Git 2.x+
- GitHub CLI (`gh`)
- Python 3.11
- Poetry (package manager)

**Setup procedures:**
- See [`.claude/workflows/11-environment-bootstrap.md`](../../.claude/workflows/11-environment-bootstrap.md) for complete setup

**Authentication:**
```bash
# Authenticate GitHub CLI
gh auth login

# Verify
gh auth status
```

---

## ⚖️ Policy Hierarchy

When policies conflict, follow this priority:

1. **Trading Safety** - Circuit breakers, idempotency, risk checks (HIGHEST)
2. **Test Requirements** - All tests must pass
3. **Zen-MCP Review** - Mandatory review before commit
4. **Documentation** - Must be updated
5. **Code Style** - Formatting, linting

**Example:** If urgent hotfix needed for trading safety issue:
- Trading safety takes precedence
- Still requires zen review (can be quick)
- Tests must pass
- Can expedite PR process with owner approval
- Documentation can be updated in follow-up if truly urgent

---

**Questions or clarifications needed?**
- See workflow guides in `.claude/workflows/` for procedures
- See other STANDARDS docs for detailed requirements
- Ask team lead for policy interpretation

