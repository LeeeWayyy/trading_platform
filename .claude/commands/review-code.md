---
description: Review code changes with Gemini + Codex. Use /review-code for source code (.py, .sql, .yaml, etc). For plan/docs review use /review-plan.
---

# Code Review

Review source code changes with Gemini + Codex via direct CLI.

## Usage

- `/review-code` — Review staged/unstaged code changes (both reviewers)
- `/review-code branch` — Review all branch changes vs origin/master
- `/review-code path/to/file.py` — Review a single file
- `/review-code --reviewer gemini` — Gemini only
- `/review-code --reviewer codex` — Codex only

## Step 1: Determine Scope

**Parse `$ARGUMENTS`:**
- `branch` → branch mode
- File path → single-file mode
- `--reviewer <name>` → specified reviewer(s). Default: gemini + codex

**Get changed files:**

| Mode | Command |
|------|---------|
| Branch | `git diff origin/master...HEAD --name-only --diff-filter=ACM` |
| Staged | `git diff --cached --name-only --diff-filter=ACM` |
| Unstaged | `git diff --name-only` |

**Guards:** On master → STOP. No changes → STOP. No staged files (not branch mode) → ask user to stage or switch to branch mode.

Display file list before proceeding.

## Step 2: Build & Send Prompt

Write prompt to temp file with single-quoted heredoc (`<<'ENDPROMPT'`). Substitute `<GIT_SCOPE_CMD>` and `<GIT_FILES_CMD>` based on mode (branch: `git diff origin/master...HEAD`, staged: `git diff --cached`, unstaged: `git diff`).

For single-file mode, replace the git discovery block with: `Review the file at: <path>. Read it with: cat <path>`

**Prompt template:**

```
You are reviewing code changes in a git repository. Do NOT expect the diff on stdin.

To discover what changed, run these git commands yourself:
- List changed files: <GIT_FILES_CMD>
- View the full diff: <GIT_SCOPE_CMD>
- View diff for a specific file: <GIT_SCOPE_CMD> -- <file>
- Read a file directly: cat <file>

Review all changes with comprehensive analysis:

**Architecture & Design (HIGH):**
- Design patterns and maintainability standards followed?
- Integration impact on other components considered?

**Trading Safety (CRITICAL):**
- Circuit breaker checks before critical operations?
- Client_order_id values deterministic and collision-free?
- Per-symbol and total position limits enforced?
- Order state transitions valid and checked?

**Concurrency & Data Safety (HIGH):**
- Redis WATCH/MULTI/EXEC for concurrent updates?
- DB operations wrapped in proper transactions?
- Read-modify-write sequences atomic?

**Error Handling (HIGH):**
- Exceptions caught, logged with context, re-raised?
- Logs include strategy_id, client_order_id, symbol?

**Security (HIGH):**
- Credentials, API keys never hardcoded or logged?
- SQL queries parameterized?
- Input validation for external data?

**Code Quality (MEDIUM):**
- All function signatures properly typed?
- Input data validated (Pydantic, assertions)?
- Docstrings complete and accurate?
- ADRs created for architectural changes?

**Testing & Edge Cases (MEDIUM):**
- All code paths and boundary conditions tested?
- Failure modes handled correctly?

**Domain-Specific (HIGH):**
- Research and production share feature calculation code?
- All timestamps UTC and timezone-aware?

Provide comprehensive analysis with issues categorized by severity (CRITICAL/HIGH/MEDIUM/LOW).
```

**Dispatch to reviewers (both get the same prompt file):**

| Reviewer | Command |
|----------|---------|
| Gemini | `cat "$PROMPT_FILE" \| gemini` |
| Codex | `cat "$PROMPT_FILE" \| codex exec -` |

**NEVER use `codex review --uncommitted` or `codex review --base`** — these prevent custom prompts.

## Step 3: Summarize & Fix Loop

```
Review Results:
━━━━━━━━━━━━━━
Files reviewed: X | Total issues: Y
CRITICAL: N  HIGH: N  MEDIUM: N  LOW: N

1. [SEVERITY] file:line — Description (Reviewer: name)
```

**Zero issues → Step 4 (APPROVED).**

Otherwise: fix ALL issues (zero tolerance, including LOW) → re-stage → re-send same prompt to all reviewers → repeat until all approve.

## Step 4: Report

```
Review APPROVED
━━━━━━━━━━━━━━
Reviewers: [list]

zen-mcp-review: approved
continuation-id: <uuidgen>
```

If fixes were made: "Run /review-code again for a fresh iteration."
If zero issues on first try: fully approved.

Clean up: `rm -f "$PROMPT_FILE"`

## Error Handling

Reviewer CLI unavailable → tell user, offer wait/retry or override (requires explicit user approval per CLAUDE.md policy). Document overrides in commit message: `ZEN_REVIEW_OVERRIDE: [reason] / User approved by: [name]`

## Rules

1. Same prompt every time — never bias with fix context
2. Fresh start per `/review-code` invocation
3. ALL reviewers must approve before reporting APPROVED
4. Fix ALL issues including LOW
5. Single-quoted heredocs to prevent shell injection
6. Never pass diff content inline — reviewers use git commands
7. Both reviewers get identical custom prompt via stdin
