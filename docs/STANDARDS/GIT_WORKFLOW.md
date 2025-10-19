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

**Manual re-review after fixes:**

After addressing review feedback and pushing new commits, you **MUST manually request re-review**:

```bash
gh pr comment <PR_NUMBER> --body "Fixed the issues you identified.

@codex @gemini-code-assist please review the latest changes on this branch."
```

**WAIT for reviewers to respond and confirm no issues before merging.**

This ensures multiple automated code reviewers catch issues before human review, providing diverse perspectives on code quality, security, and best practices.

**Additional Automation:**

- **Gemini Code Assist**: Configured via `.gemini/config.yaml` and `GEMINI.md`
- **CI/CD Pipeline**: `.github/workflows/ci-tests-coverage.yml` runs tests and coverage automatically
- **Codex CLI** (MANDATORY): See `docs/IMPLEMENTATION_GUIDES/codex-mcp-integration.md` for local review setup

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

## Automated Code Review & CI/CD Integration

### Overview of Automation

This project uses multiple layers of automated code quality checks:

1. **Automatic Review Requests** (GitHub Actions)
2. **CI/CD Pipeline** (Tests + Coverage)
3. **Gemini Code Assist** (AI-powered code review)
4. **Codex CLI** (Optional local review)

### 1. Automatic Review Requests

**File:** `.github/workflows/pr-auto-review-request.yml`

**Triggers:** When PR is opened, reopened, or marked ready for review

**What it does:**
- Automatically posts comment mentioning `@codex` and `@gemini-code-assist`
- Requests comprehensive review (quality, security, testing, documentation)
- References review policy from this document

**No configuration needed** - works out of the box!

### 2. CI/CD Pipeline (Tests & Coverage)

**File:** `.github/workflows/ci-tests-coverage.yml`

**Triggers:** On all PRs and pushes to master

**What it does:**
- Spins up Redis and Postgres test services
- Runs full test suite with pytest
- Checks test coverage (fails if < 80%)
- Runs linting (mypy, ruff)
- Posts coverage report as PR comment
- Uploads coverage HTML report as artifact

**Configuration:**
- Minimum coverage threshold: 80% (configurable in workflow)
- Coverage report posted to PRs automatically
- Integrates with Codecov (optional)

**Example workflow output:**
```
✅ Tests: 74 passed
✅ Coverage: 87%
✅ Linting: Passed
```

### 3. Gemini Code Assist Integration

**Configuration files:**
- `.gemini/config.yaml` - Review settings and focus areas
- `GEMINI.md` - Project-specific context and standards

**What it does:**
- Automatically reviews all new PRs within 5 minutes
- Posts inline comments on code issues
- Generates PR summaries
- Severity filtering (CRITICAL, HIGH, MEDIUM, LOW)

**Focus areas:**
- Security vulnerabilities
- Logic errors and bugs
- Performance issues
- Test coverage gaps
- Documentation completeness
- Best practices violations

**Customization:**

Edit `.gemini/config.yaml` to adjust:
```yaml
code_review:
  comment_severity_threshold: MEDIUM  # Change to HIGH to reduce noise
  inline_suggestions: true
  severity_filters:
    critical: true
    high: true
    medium: true
    low: false  # Disable low-priority nitpicks
```

### 4. Codex via MCP (MANDATORY Pre-Commit Review)

**Purpose:** Integrate OpenAI Codex directly into Claude Code for code review and test generation

**Setup:** See `docs/IMPLEMENTATION_GUIDES/codex-mcp-integration.md`

**What it provides:**
- Code review using GPT-5 or O3 models
- Automatic test generation
- Coverage gap analysis
- Bug fixing assistance
- Direct integration with Claude Code (no separate CLI needed)

**Quick start:**
```bash
# Install Codex CLI
npm install -g @openai/codex

# Authenticate
codex exec "echo test"  # Follow prompts

# Test MCP server
npx @modelcontextprotocol/inspector codex mcp-server

# Register with Claude Code
claude mcp add --transport stdio codex-mcp -- codex mcp-server

# Restart Claude Code
# Then invoke codex from Claude Code
```

**Usage in Claude Code:**
- "Use codex to review libs/risk_management/breaker.py"
- "Ask codex to generate tests for this file"
- "Use codex to analyze coverage gaps"

**See full guide:** `docs/IMPLEMENTATION_GUIDES/codex-mcp-integration.md`

## Summary: Complete Automation Workflow

### For Developers

**Minimal setup:**
```bash
# 1. Install and authenticate GitHub CLI
brew install gh
gh auth login

# 2. Tell Claude to create PRs
"Implement ticket T4 and create a PR when done"
```

**What happens automatically:**

1. **During PR creation:**
   - Claude creates feature branch
   - Commits with progressive commits throughout development
   - Runs tests locally
   - Pushes to remote
   - Creates PR with detailed description
   - **GitHub Actions automatically requests reviews from @codex and @gemini-code-assist** ✨

2. **After PR is created:**
   - CI/CD pipeline runs tests and coverage checks
   - Gemini Code Assist reviews code within 5 minutes
   - @codex and @gemini-code-assist post review comments
   - Coverage report posted to PR
   - Test results visible in GitHub UI

3. **After you fix review issues:**
   - Push fixes to same branch
   - CI/CD pipeline re-runs automatically
   - **Manually request re-review:** `gh pr comment <PR> --body "Fixed the issues you identified. @codex @gemini-code-assist please review the latest changes on this branch."`
   - Wait for approval from all reviewers
   - Merge when all checks pass ✅

### For AI Assistants (Claude Code)

**Your automated workflow:**

1. **During implementation:**
   - Make progressive commits every 30-60 minutes
   - Push regularly to backup work
   - Run tests locally before pushing

2. **When creating PR:**
   - Create PR with comprehensive description
   - Include checklist from TESTING.md
   - Reference ADRs and implementation guides
   - **No need to manually request reviews** - automated by GitHub Actions! ✨

3. **After receiving reviews:**
   - Address ALL HIGH and MEDIUM priority issues
   - Create test cases for reported bugs
   - Push fixes to same branch
   - **Manually comment to request re-review:**
     ```bash
     gh pr comment <PR> --body "Fixed issues X, Y, Z.

     @codex @gemini-code-assist please review latest commit."
     ```
   - Wait for explicit approval from reviewers
   - Only merge when all reviewers approve

### File Structure Summary

```
.github/
├── workflows/
│   ├── pr-auto-review-request.yml    # ✨ Auto-requests reviews on PR creation
│   └── ci-tests-coverage.yml         # Runs tests, coverage, linting
├── CODEOWNERS                         # Code ownership rules
└── pull_request_template.md          # PR template

.gemini/
└── config.yaml                        # Gemini Code Assist settings

GEMINI.md                              # Project context for Gemini reviews

docs/
├── STANDARDS/
│   └── GIT_WORKFLOW.md               # This file
└── IMPLEMENTATION_GUIDES/
    └── codex-mcp-integration.md      # Codex MCP integration (optional)
```

### Benefits

**For developers:**
- ✅ Never forget to request automated reviews
- ✅ Consistent review quality across all PRs
- ✅ Faster feedback (reviews within 5 minutes)
- ✅ Multiple AI perspectives (Codex + Gemini)
- ✅ Test coverage automatically tracked

**For AI assistants:**
- ✅ Reduced cognitive load (one less step to remember)
- ✅ Consistent workflow across all PRs
- ✅ Clear expectations for review policy
- ✅ Automated initial review request

**For the project:**
- ✅ Higher code quality (caught by AI before human review)
- ✅ Better security (automated security scanning)
- ✅ Improved test coverage (coverage gates enforced)
- ✅ Faster iteration (early feedback)
- ✅ Educational value (AI reviewers explain issues)

### You Retain Control

Despite automation, you remain in control:
- Review PR before merging (human approval still required)
- Can bypass specific review comments with justification
- Can adjust automation settings (`.gemini/config.yaml`)
- Can disable workflows if needed
- Merge only when satisfied with quality

**This workflow keeps you in the driver's seat while automating the tedious parts.**
