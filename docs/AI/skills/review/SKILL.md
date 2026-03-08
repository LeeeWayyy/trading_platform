---
name: review
description: Cross-platform code review workflow. Shared review process for Claude Code, Gemini CLI, and Codex CLI. Trigger on review, code review, pre-commit, zen trailer, review iteration.
---

# Review — Cross-Platform Code Review Workflow

Shared review workflow for all AI CLIs. Ensures review discipline before commits on feature branches.

## Overview

Every feature/fix/bugfix branch commit requires review approval. This skill defines the workflow logic; each CLI invokes it through its own mechanism.

## Invocation

- **Claude Code:** `/review` (command wrapper delegates to this workflow)
- **Gemini CLI:** Invoke the `review` skill directly
- **Codex CLI:** Invoke the `review` skill directly

## Step 1: Determine Diff Scope

**Branch mode** (review all branch changes):
```bash
git diff origin/master...HEAD --name-only --diff-filter=ACMDRT
```

**Staged mode** (default — review staged changes):
```bash
git diff --cached --name-only --diff-filter=ACMDRT
```

**Guard rails:**
- If on master branch: create a feature branch first, STOP
- If no changes found: "No changes to review", STOP
- Display the file list before proceeding

## Step 2: Send to Reviewers

**Default reviewers:** Gemini + Codex (both, sequentially)
**Single reviewer:** `--reviewer gemini` or `--reviewer codex`

Each reviewer receives the same review prompt covering:

1. **Architecture & Design (HIGH)** — patterns, maintainability, integration impact
2. **Trading Safety (CRITICAL)** — circuit breaker checks, idempotency, position limits, order state transitions
3. **Concurrency & Data Safety (HIGH)** — Redis WATCH/MULTI/EXEC, DB transactions, atomic read-modify-write
4. **Error Handling (HIGH)** — catch, log with context (strategy_id, client_order_id, symbol), re-raise
5. **Security (HIGH)** — no hardcoded credentials, parameterized SQL, input validation
6. **Code Quality (MEDIUM)** — type annotations, Pydantic validation, docstrings, ADRs for architecture changes
7. **Testing & Edge Cases (MEDIUM)** — code path coverage, boundary conditions, failure modes
8. **Domain-Specific (HIGH)** — feature parity (research/production), UTC timestamps

**Independent reviewers:** Each reviewer runs as a fresh CLI invocation with the same prompt. There is no shared session between reviewers.

## Step 3: Summarize Findings

Combine all reviewer responses into a categorized issue list:
```
Review Results:
  Files reviewed: X
  Total issues: Y
  CRITICAL: [count]  HIGH: [count]  MEDIUM: [count]  LOW: [count]

Issues:
  1. [SEVERITY] file.py:line — Description (Reviewer: name)
  ...
```

**If zero issues: APPROVED** — proceed to Step 5.

## Step 4: Fix and Re-Review

**ZERO TOLERANCE — ALL issues must be fixed, including LOW severity.**

1. Fix each issue systematically
2. Re-stage fixed files
3. Re-review using fresh CLI invocations with the same prompt (do not bias reviewers with fix context)
4. If new issues found: repeat
5. If ALL reviewers approve: proceed to Step 5

## Step 5: Report Result

**When approved:**
```
Review APPROVED
  continuation-id: <uuid>
  Reviewers: [list]

Ready to commit. Include in commit message:
  zen-mcp-review: approved
  continuation-id: <uuid>
```

**If fixes were made:** Run `/review` again (fresh iteration) to confirm clean approval.
**If zero issues on first try:** Code is fully approved — ready to commit.

## Zen Trailer Requirements

Feature branch commits require these trailers:
```
zen-mcp-review: approved
continuation-id: <generated-uuid>
```

Generate the continuation-id with `uuidgen` (or equivalent). This provides a unique audit trail per review iteration.

**Docs-only commits** (prefix `docs:`, no code/config files staged) skip zen trailers.

## Override Policy

If reviewers are unavailable:
1. **Wait and retry** (recommended)
2. **Override** (requires explicit human approval):
   ```
   ZEN_REVIEW_OVERRIDE: Server unavailable
   User approved by: [user name]
   ```

**NEVER use `git commit --no-verify`** — detected by CI.
