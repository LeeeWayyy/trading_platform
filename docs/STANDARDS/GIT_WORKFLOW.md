# Git Workflow & Pull Request Automation

## Overview

This document explains how to work with Git and automate pull request creation when using Claude Code or other AI assistants.

## Prerequisites

### 1. Install GitHub CLI

```bash
# macOS
brew install gh

# Linux
# See https://github.com/cli/cli/blob/trunk/docs/install_linux.md

# Windows
# See https://github.com/cli/cli#windows
```

### 2. Authenticate GitHub CLI

```bash
# Authenticate with GitHub
gh auth login

# Select:
# - GitHub.com
# - HTTPS
# - Login with a web browser (recommended)
# - Follow the prompts

# Verify authentication
gh auth status
```

### 3. Configure Git

```bash
# Set your identity (if not already done)
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"

# Recommended: Set default branch name
git config --global init.defaultBranch main

# Recommended: Enable commit signing (optional but good practice)
git config --global commit.gpgsign true
```

## Feature Development Workflow

### Two Development Modes

**Mode 1: Direct to Master (Small Fixes)**
- For trivial changes, documentation updates, or hotfixes
- Commit directly to master with comprehensive commit message
- Example: T1 implementation (complete feature with all tests passing)

**Mode 2: Feature Branch with Incremental Commits (Recommended for Tx Tickets)**
- Create feature branch (e.g., `feature/t2-alpaca-connector`)
- Make incremental commits as you build the feature
- Create PR when ticket goal is complete
- Allows for regular backups and progress tracking

### Feature Branch Development Process

When implementing a Tx ticket (T2, T3, T4, etc.):

1. **Create Feature Branch**
   ```bash
   git checkout -b feature/t2-alpaca-connector
   ```

2. **Make Incremental Commits**
   - Commit small, logical units of work frequently
   - Each commit should compile/pass basic checks
   - Commit messages can be concise during development

   ```bash
   # Example incremental commits during T2 development:
   git commit -m "Add Alpaca API client wrapper"
   git commit -m "Implement historical data fetching"
   git commit -m "Add rate limiting logic"
   git commit -m "Implement corporate actions fetching"
   git commit -m "Add unit tests for API client"
   git commit -m "Add integration tests"
   git commit -m "Update documentation"
   ```

3. **Push Regularly (Optional but Recommended)**
   ```bash
   # Push to backup your work and track progress
   git push -u origin feature/t2-alpaca-connector
   ```

4. **When Ticket Goal Complete**
   - Ensure all tests pass
   - Ensure documentation is updated
   - Create PR for review and merge

### Progressive Committing Philosophy

**REQUIRED: Commit Early, Commit Often**

When working on any non-trivial feature, you MUST use progressive commits throughout development. Do NOT wait until everything is complete to make your first commit.

**Why Incremental Commits During Feature Development?**

**Benefits:**
- ✅ Regular backups of work in progress
- ✅ Easier to revert specific changes if needed
- ✅ Better tracking of development progress
- ✅ Clearer history of how feature was built
- ✅ Can resume work after interruptions
- ✅ Enables collaboration and review at each stage
- ✅ Makes debugging easier (bisect to find regressions)

**When to Commit (Progressive Strategy):**
- After implementing a logical component (even if incomplete)
- After tests pass for that component
- Before taking a break or ending session
- Before attempting risky refactoring
- **At minimum: every 30-60 minutes of active development**
- After fixing a bug or addressing review feedback
- When switching between different parts of the feature

**Example Progressive Commit Sequence:**
```bash
# Session 1: Initial setup (30 min)
git commit -m "Add Alpaca API client skeleton"

# Session 2: Core functionality (1 hour)
git commit -m "Implement authentication and connection"
git commit -m "Add historical data fetching method"

# Session 3: Error handling (45 min)
git commit -m "Add rate limiting with exponential backoff"
git commit -m "Handle API errors with retry logic"

# Session 4: Testing (1 hour)
git commit -m "Add unit tests for API client"
git commit -m "Add integration tests with mock server"

# Session 5: Documentation (30 min)
git commit -m "Add docstrings and update implementation guide"
```

**Anti-Pattern to Avoid:**
```bash
# ❌ BAD - Single massive commit after 8 hours of work
git commit -m "Implement entire Alpaca connector (2000 lines changed)"
```

---

## MANDATORY: Pre-Commit Code Review with Zen MCP

### Policy

**ALL code commits by Claude Code (AI assistant) MUST be reviewed by zen-mcp before committing.**

This is a **MANDATORY** quality gate, not optional. Exceptions require explicit user approval with documented justification.

### Workflow

**Every progressive commit (30-60 min cadence):**

1. **Stage changes**
   ```bash
   git add <files>
   ```

2. **Request zen-mcp review (REQUIRED)**
   ```
   "Use zen clink with codex codereviewer to review my staged changes.
   Check for: trading safety (circuit breakers, idempotent IDs, position limits),
   concurrency (race conditions, transactions), error handling, type hints,
   security (secrets, SQL injection), configuration (DRY_RUN), standards
   (docstrings, tests), and domain-specific (feature parity, timezones, API contracts).
   Focus on HIGH/CRITICAL issues."
   ```

3. **Address ALL findings**
   - Fix HIGH/CRITICAL issues immediately (blocking)
   - Fix MEDIUM issues or document deferral reason
   - Fix LOW issues if time permits
   - Re-request review to verify: "I've fixed the issues, please verify"
   - Use continuation_id for context preservation

4. **Commit only when approved**
   ```bash
   git commit -m "Progressive commit message"
   ```

5. **Repeat every 30-60 minutes**

### Review Focus Areas

Zen-mcp MUST check for:

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

### Enforcement Rules

**For Claude Code (AI Assistant):**
- ❌ CANNOT commit without zen-mcp review
- ❌ CANNOT commit if zen finds HIGH or CRITICAL issues
- ✅ CAN commit with user override if zen unavailable
- ✅ MUST document override reason in commit message

**Override Format (emergencies only):**
```bash
git commit -m "Add position limit validation

ZEN_REVIEW_OVERRIDE: Server temporarily unavailable
Reason: Urgent hotfix for production issue
Will perform post-commit review and create follow-up PR if issues found"
```

### Exemptions

Only these commits may skip zen review:
- ✅ Documentation-only changes (add `#docs-only` to commit message)
- ✅ Auto-generated files (package-lock.json, poetry.lock, etc.)
- ✅ Emergency hotfixes (with explicit user approval + mandatory post-commit review)

**Example docs-only commit:**
```bash
git commit -m "Update README with setup instructions #docs-only"
```

### Before PR: Deep Review (MANDATORY)

Before creating ANY pull request, Claude Code MUST:

1. **Request comprehensive zen-mcp review**
   ```
   "Use zen clink with codex codereviewer for comprehensive review of all branch changes.
   Review all files changed in this branch (compare HEAD to origin/main).
   Check: trading safety (circuit breakers, idempotent IDs, position limits, order validation),
   concurrency (race conditions, transactions, atomic operations), error handling,
   type hints, security (secrets, SQL injection, input validation), code quality,
   configuration (DRY_RUN, no hardcoding), standards (docstrings, tests),
   domain-specific (feature parity, timezones, API contracts), architecture,
   test coverage, edge cases, integration points, documentation, performance.
   Provide detailed analysis with severity levels (CRITICAL/HIGH/MEDIUM/LOW).
   Be thorough - this is the final gate before PR."
   ```

2. **Address ALL findings by severity:**
   - **HIGH/CRITICAL**: MUST fix before creating PR (blocking)
   - **MEDIUM**: MUST fix OR document deferral with justification
   - **LOW**: Fix if time permits, or document as future improvement

3. **Include in PR description:**
   ```markdown
   ## Zen MCP Review

   **Progressive Reviews:**
   - Total commits: 6
   - All commits reviewed and approved by zen-mcp
   - Issues caught and fixed: 3 HIGH, 5 MEDIUM, 2 LOW

   **Deep Review (Before PR):**
   - ✅ Completed comprehensive review
   - ✅ Architecture: No issues
   - ✅ Test coverage: 95% (target met)
   - ✅ Edge cases: 2 MEDIUM issues found and fixed
   - ✅ Final approval: Granted by zen-mcp

   **Zen Conversation:**
   - Continuation ID: abc123-def456-ghi789
   - Total review time: ~5 minutes
   - Issues prevented from reaching PR: 10
   ```

### Example Workflow

**Scenario: Implementing position limit validation**

```
Claude Code: [Implements check_position_limits() function for 45 minutes]

Claude Code: git add apps/execution_gateway/order_placer.py

Claude Code: "Use zen clink with codex codereviewer to review my staged changes for trading safety"

Zen MCP (Codex): ❌ "CRITICAL ISSUE found:
  - Line 42: Missing circuit breaker check before position validation
  - This could validate positions even when breaker is TRIPPED!

  Required fix:
  if self.breaker.is_tripped():
      raise CircuitBreakerTripped('Cannot validate: breaker TRIPPED')

  Also found:
  - MEDIUM: Add logging when position limit exceeded
  - LOW: Variable name 'pos' unclear, use 'current_position'"

Claude Code: [Fixes all 3 issues]

Claude Code: "I've fixed the circuit breaker check, added logging, and renamed the variable. Please verify."

Zen MCP (Codex): ✅ "All issues resolved:
  ✅ Circuit breaker check added correctly (line 41)
  ✅ Logging added with proper context
  ✅ Variable renamed for clarity
  Safe to commit."

Claude Code: git commit -m "Add position limit validation with circuit breaker check"

Claude Code → User: "Committed! Zen-mcp caught a critical bug before it entered the codebase."
```

### Metrics & Monitoring

Track and report weekly in ops sync:

| Metric | Target | Purpose |
|--------|--------|---------|
| Commits reviewed | 100% | Ensure compliance |
| Review time (median) | <60s | Avoid friction |
| Issues caught (HIGH/CRITICAL) | Track trend | Measure value |
| Override rate | <5% | Monitor exceptions |
| False positive rate | <10% | Refine prompts |

**Weekly Report Format:**
```bash
# Zen MCP Review Metrics: Week of 2025-10-19

Compliance:
- Commits reviewed: 47/47 (100%) ✅
- Overrides: 2 (4.3%) ✅
- Docs-only skips: 8

Performance:
- Median review time: 28s ✅
- 95th percentile: 65s ✅

Issues Found:
- HIGH: 5 (prevented from reaching PR)
- MEDIUM: 12 (caught early)
- LOW: 18 (improved quality)

Developer Feedback:
- Satisfaction: 8.5/10 ✅
- Most valuable: "Catches circuit breaker issues I always forget"
- Friction point: None reported this week
```

### Benefits Realized

**Time Savings:**
- Find issues in ~30s vs 10-15min PR review
- Fix while context fresh (not days later)
- 50-66% reduction in PR review cycles

**Quality Improvements:**
- 70-90% fewer issues per PR
- Trading safety enforced at commit time
- Consistent quality standards

**Learning:**
- AI assistant learns trading patterns from reviews
- Knowledge transfer through feedback
- Improved code quality over time

---

## Automated PR Workflow with Claude Code

### Workflow Overview

When you ask Claude Code to implement a feature, it can automatically:

1. ✅ Create a feature branch
2. ✅ Make code changes with incremental commits
3. ✅ Write tests
4. ✅ Run tests and linting
5. ✅ Commit changes with descriptive messages
6. ✅ Push to remote repository regularly
7. ✅ Create pull request when feature complete
8. ✅ Link related ADRs and documentation

### How to Enable Automatic PR Creation

**Option 1: Explicit Request (Recommended for Learning)**
```
User: "Implement idempotent order submission (ticket T4),
       then create a PR when done"
```

**Option 2: Default Behavior (Configure in Prompts)**

Add to your `prompts/assistant_rules.md`:
```markdown
## Automatic Pull Request Creation

After successfully implementing and testing any feature:
1. Create a feature branch (if not already on one)
2. Commit all changes with descriptive messages
3. Push to remote repository
4. Create a pull request using `gh pr create`
5. Include in PR description:
   - Summary of changes
   - Related ADR references
   - Testing completed
   - Checklist from /docs/STANDARDS/TESTING.md
```

**Option 3: Use a Slash Command**

Create `.claude/commands/implement-and-pr.md`:
```markdown
You are implementing a ticket and creating a PR afterwards.

Workflow:
1. Ask user which ticket to implement
2. Read ticket from /docs/TASKS/
3. Follow implementation process from CLAUDE.md
4. After validation phase passes:
   a. Create feature branch if not exists
   b. Commit all changes
   c. Push to remote
   d. Create PR with gh pr create
5. Return PR URL to user
```

Then use: `/implement-and-pr`

## PR Creation Template

When Claude creates a PR, it should use this structure:

```markdown
## Summary
Brief description of what was implemented (1-2 sentences).

## Related Work
- Ticket: #T4 (or link to /docs/TASKS/P0_TICKETS.md#t4)
- ADR: ADR-0004 (if applicable)
- Implementation Guide: /docs/IMPLEMENTATION_GUIDES/phase-6-execution-gateway.md

## Changes Made
- [ ] Implemented `deterministic_id()` function
- [ ] Added order deduplication in `place_order()` endpoint
- [ ] Created unit tests for ID generation
- [ ] Created integration tests for duplicate detection
- [ ] Updated OpenAPI spec with new error responses
- [ ] Added concept documentation for idempotency

## Testing Completed
- [x] Unit tests pass (`make test`)
- [x] Linting passes (`make lint`)
- [x] Manual testing in DRY_RUN mode
- [x] Contract tests against OpenAPI spec
- [x] Integration test with Alpaca paper API

## Documentation Updated
- [x] /docs/CONCEPTS/idempotency.md created
- [x] /docs/IMPLEMENTATION_GUIDES/phase-6-execution-gateway.md updated
- [x] ADR-0004 created and accepted
- [x] Code has comprehensive docstrings
- [x] OpenAPI spec updated

## Educational Value
This PR demonstrates:
- Hash-based idempotency pattern
- Retry safety without duplicates
- Deterministic ID generation
- Handling broker 409 conflicts

## Checklist
- [x] Tests added/updated
- [x] OpenAPI updated (if API changed)
- [x] Migrations included (if DB changed)
- [x] Docs updated (REPO_MAP / ADR / TASKS)
- [x] ADR created (if architectural change)
- [x] Concept docs created (if trading-specific)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

## Example Commands Claude Will Use

### Creating a Feature Branch
```bash
# Claude will run:
git checkout -b feature/t4-idempotent-orders
```

### Committing Changes
```bash
# If ADR exists, commit it first
git add docs/ADRs/0004-idempotent-order-submission.md
git commit -m "ADR-0004: Use deterministic client_order_id for idempotency"

# Then commit implementation
git add apps/execution_gateway/
git add tests/
git commit -m "Implement idempotent order submission (ADR-0004)

- Add deterministic_id() function using SHA256
- Update place_order() to check for duplicates
- Handle Alpaca 409 conflicts
- Add comprehensive tests

Closes #T4"
```

### Pushing and Creating PR
```bash
# Push branch
git push -u origin feature/t4-idempotent-orders

# Create PR with gh CLI
gh pr create \
  --title "Implement idempotent order submission (T4)" \
  --body "$(cat <<'EOF'
## Summary
Implements idempotent order submission using deterministic client_order_id generation.

## Related Work
- Ticket: T4 (/docs/TASKS/P0_TICKETS.md)
- ADR: ADR-0004

## Changes Made
...
EOF
)"
```

## Branch Naming Conventions

Use consistent branch naming:

```bash
feature/t4-idempotent-orders     # New features
fix/circuit-breaker-recovery     # Bug fixes
docs/update-testing-guide        # Documentation only
refactor/extract-risk-checks     # Refactoring
chore/upgrade-dependencies       # Maintenance
```

## Controlling PR Creation

### When You Want Automatic PRs

Tell Claude:
```
"Implement ticket T4 and create a PR when tests pass"
```

### When You Want Manual Control

Tell Claude:
```
"Implement ticket T4 but don't create a PR yet -
 I want to review the changes first"
```

Then later:
```
"Create a PR for the changes we just made"
```

## PR Creation Safety Checks

Before Claude creates a PR, it should verify:

1. ✅ All tests pass (`make test`)
2. ✅ All linting passes (`make lint`)
3. ✅ On a feature branch (not main/master)
4. ✅ Changes are committed
5. ✅ ADR created if needed
6. ✅ Documentation updated

Claude will **not** automatically:
- ❌ Push to main/master directly
- ❌ Force push (unless explicitly requested and justified)
- ❌ Merge PRs without explicit reviewer approval (see "CRITICAL: Review Feedback and Merge Policy" above)
- ❌ Delete branches
- ❌ Modify git history with rebase/amend (unless explicitly requested)

## Working with Draft PRs

For work-in-progress, create draft PRs:

```bash
gh pr create --draft \
  --title "[WIP] Implement idempotent orders (T4)" \
  --body "Work in progress. Do not merge yet.

## TODO
- [ ] Add integration tests
- [ ] Update documentation
- [ ] Get ADR reviewed
"
```

Then convert to ready when done:
```bash
gh pr ready
```

## Updating Existing PRs

Claude can update PRs by adding commits:

```bash
# Make additional changes
git add apps/execution_gateway/
git commit -m "Address review feedback: improve error messages"
git push

# Add comment to PR notifying automated reviewers
gh pr comment <PR_NUMBER> --body "Updated to address review feedback.

@codex @gemini-code-assist please review the latest changes on this branch."
```

### IMPORTANT: Automated Code Review Requirement

**✨ NEW: Automatic Review Requests (2025-10-19)**

Review requests are now **automated via GitHub Actions**! When you create or reopen a PR, the workflow automatically:
- Posts a comment mentioning `@codex` and `@gemini-code-assist`
- Requests comprehensive code review (quality, security, testing, documentation)
- References this document for review policy

**See:** `.github/workflows/pr-auto-review-request.yml`

**What this means for you:**
- ✅ No need to manually request reviews on PR creation
- ✅ Consistent review requests across all PRs
- ✅ Never forget to request automated reviews
- ⚠️  Still need to manually request re-review after fixing issues

**WAIT for reviewers to respond and confirm no issues before merging.**

This ensures multiple automated code reviewers catch issues before human review, providing diverse perspectives on code quality, security, and best practices.

**Additional Automation:**

- **CI/CD Pipeline**: `.github/workflows/ci-tests-coverage.yml` runs tests and coverage automatically
- **ZEN MCP** (MANDATORY): See `docs/IMPLEMENTATION_GUIDES/workflow-optimization-zen-mcp.md` for local review setup


### CRITICAL: Review Feedback and Merge Policy

**ALL review feedback MUST be addressed before merging. You are NOT allowed to merge unless:**

1. **All High Priority Issues**: MUST be considered and fixed immediately if the issue is confirmed to exist, with test cases created to cover the fix if necessary
2. **All Medium Priority Issues**: MUST be considered and fixed immediately if the issue is confirmed to exist, with test cases created to cover the fix if necessary
3. **All Low Priority Issues**: MUST be considered and fixed immediately if the issue is confirmed to exist, with test cases created to cover the fix if necessary
4. **Only Exception**: Owner explicitly says you can defer specific issues to future work

**After fixing review feedback:**
- Push fixes and request re-review from `@codex` and `@gemini-code-assist`, asking them to check out the latest commit to avoid caching issues.
- **WAIT for reviewers to explicitly confirm "no issues" or approve the PR**
- Do NOT assume fixes are sufficient - reviewers must explicitly approve

**You are ONLY allowed to merge when:**
- ✅ All reviewers explicitly say "no issues" or approve the PR
- ✅ All review comments have been addressed or explicitly deferred by owner
- ✅ All tests pass
- ❌ **NEVER** merge without explicit reviewer approval

**If you are unsure about deferring an issue:**
- Ask the owner: "Reviewer X raised issue Y. Should I fix it now or defer to future work?"
- Wait for owner's explicit approval before deferring
- Document deferred issues in the PR description or create follow-up tickets

### Handling Conflicting Reviewer Feedback

**Problem: Review Deadlocks**

Sometimes reviewers may provide conflicting feedback that creates a loop:
1. Gemini suggests adding feature X
2. You implement feature X
3. Codex says feature X causes regression issues
4. You remove feature X
5. Gemini complains feature X is missing again
6. **Review deadlock** - cannot satisfy both reviewers

**Resolution Strategy: Codex as Tie-Breaker**

When conflicting feedback creates a review loop on a **specific change**:

1. **Identify the Conflict**: Recognize when two reviewers disagree on the same specific implementation detail
2. **Use Codex as Golden Standard**: If Codex approves the implementation, defer Gemini's conflicting suggestion
3. **Document the Decision**: Add a comment explaining why Gemini's suggestion was not implemented
4. **Scope is Limited**: This only applies to the specific conflicting change, NOT all of Gemini's feedback

**Example Scenario:**

```bash
# Round 1: Gemini review
Gemini: "Add error handling for network timeouts in fetch_positions()"

# Round 2: You implement
git commit -m "Add network timeout handling to fetch_positions()"

# Round 3: Codex review
Codex: "The timeout handling in fetch_positions() will cause regression -
        it prevents graceful degradation when Execution Gateway is temporarily down.
        The existing error handling is correct."

# Round 4: You revert
git revert <commit-hash>
git commit -m "Revert timeout handling - causes regression per Codex review"

# Round 5: Gemini review
Gemini: "Still missing timeout handling in fetch_positions()"

# RESOLUTION: Break the loop
gh pr comment <PR_NUMBER> --body "@codex confirmed the existing error
handling is correct and adding timeout handling would cause regression.
Deferring Gemini's suggestion on this specific change.

@codex please confirm this implementation is still acceptable."
```

**When to Apply This Rule:**

✅ **Apply tie-breaker when:**
- Same specific change reviewed multiple times
- Clear conflict between reviewer suggestions (not just different perspectives)
- Codex explicitly says implementation is correct
- Loop has occurred 2+ times on same issue
- Regression or correctness is at stake

❌ **Do NOT apply tie-breaker when:**
- Reviewers comment on different parts of the code
- Suggestions are complementary (can implement both)
- Only 1 round of feedback (not yet a loop)
- Owner has not explicitly approved using tie-breaker
- Issue is about code style (not correctness)

**Proper Documentation:**

When using Codex as tie-breaker, document clearly:

```bash
# In PR comment:
gh pr comment <PR_NUMBER> --body "## Conflicting Reviewer Feedback Resolution

**Issue:** Gemini suggests adding timeout handling, Codex says it causes regression

**Attempts:**
1. Implemented Gemini's suggestion (commit abc123)
2. Codex identified regression risk
3. Reverted (commit def456)
4. Gemini re-requested same change

**Resolution:** Using Codex as tie-breaker per GIT_WORKFLOW.md
- Codex confirmed existing implementation is correct
- Timeout handling would prevent graceful degradation
- Keeping current implementation

**Scope:** This decision applies ONLY to timeout handling in fetch_positions()
- All other Gemini feedback is still being addressed
- Not deferring any other suggestions

@codex please confirm this is still acceptable"
```

**Important Notes:**

1. **Limited Scope**: Tie-breaker only applies to the specific conflicting change
2. **All Other Feedback Remains**: Continue addressing all non-conflicting feedback
3. **Owner Awareness**: If uncertain, ask owner before using tie-breaker
4. **Final Approval Still Required**: Codex must still explicitly approve the PR
5. **Document Everything**: Clear audit trail of decision process

## Best Practices

### 1. One Feature Per PR
Keep PRs focused:
- ✅ Single ticket implementation
- ✅ Related tests and docs
- ❌ Multiple unrelated changes
- ❌ Mixing features and refactoring

### 2. Meaningful Commit Messages

**For Final PR Commits (Mode 1: Direct to Master):**
```bash
# GOOD - Comprehensive with details
"Implement deterministic order ID generation (ADR-0004)

- Add SHA256-based hash function
- Include order params and date in hash
- Truncate to 24 chars for Alpaca compatibility
- Add unit tests for collision resistance"

# BAD
"Fixed stuff"
"Updates"
```

**For Incremental Commits (Mode 2: Feature Branch Development):**
```bash
# GOOD - Concise but clear
"Add Alpaca API client wrapper"
"Implement rate limiting with exponential backoff"
"Add unit tests for historical data fetching"
"Fix type hints in corporate actions module"

# ACCEPTABLE during development
"WIP: Adding authentication logic"
"Draft: Initial market data connector structure"

# STILL BAD - Too vague
"Fixed stuff"
"Updates"
"Changes"
```

**Note:** Incremental commits can be more concise since the PR description will provide comprehensive context. The key is that each commit represents a logical unit of work.

### 3. Keep PRs Small
Aim for:
- < 500 lines of code changes
- < 10 files changed
- Single focused change

Large changes should be split into multiple PRs:
```
PR #1: Add deterministic ID generation (ADR-0004)
PR #2: Integrate ID generation into order submission
PR #3: Add duplicate detection logic
PR #4: Add integration tests
```

### 4. Link Everything
In PR description, link to:
- Ticket in /docs/TASKS/
- ADR in /docs/ADRs/
- Implementation guide
- Related concept docs
- Related PRs (if any)

### 5. Use PR Templates (Optional)

Create `.github/pull_request_template.md`:
```markdown
## Summary
<!-- Brief description of changes -->

## Related Work
- Ticket:
- ADR:
- Implementation Guide:

## Changes Made
- [ ]

## Testing Completed
- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] Manual testing in DRY_RUN mode

## Documentation Updated
- [ ] Concept docs
- [ ] Implementation guide
- [ ] ADR (if architectural change)
- [ ] Code has docstrings
- [ ] OpenAPI spec (if API changed)

## Checklist
- [ ] Tests added/updated
- [ ] OpenAPI updated (if API changed)
- [ ] Migrations included (if DB changed)
- [ ] Docs updated

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

## Troubleshooting

### Issue: `gh` command not found
```bash
# Install GitHub CLI
brew install gh  # macOS
# or follow instructions at https://cli.github.com/
```

### Issue: Authentication failed
```bash
# Re-authenticate
gh auth login

# Check status
gh auth status
```

### Issue: Permission denied
```bash
# Check remote URL uses HTTPS (not SSH if not configured)
git remote -v

# Update to HTTPS if needed
git remote set-url origin https://github.com/username/repo.git
```

### Issue: Branch protection rules
If main/master has branch protection:
- PRs are required (good!)
- Claude cannot push directly (good!)
- You'll need to review and merge via GitHub UI

### Issue: PR created on wrong branch
```bash
# Close the PR
gh pr close <PR_NUMBER>

# Create correct branch
git checkout -b correct-branch-name

# Cherry-pick commits
git cherry-pick <commit-hash>

# Push and create new PR
git push -u origin correct-branch-name
gh pr create
```

## Advanced: CI Integration

If you have GitHub Actions:

```yaml
# .github/workflows/ci.yml
name: CI
on: [pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install poetry
      - run: poetry install
      - run: poetry run pytest
      - run: poetry run mypy .
      - run: poetry run ruff check .
```

This will automatically run tests on every PR Claude creates.

## Summary: Enabling Automatic PRs

**Minimal setup:**
```bash
# 1. Install and authenticate
brew install gh
gh auth login

# 2. Tell Claude to create PRs
"Implement ticket T4 and create a PR when done"
```

**That's it!** Claude will handle:
- Branch creation
- Commits
- Testing
- Pushing
- PR creation with detailed description

You retain control:
- Review the PR before merging
- Request changes via GitHub UI
- Close/modify as needed
- Merge when satisfied

This workflow keeps you in the driver's seat while automating the tedious parts.
