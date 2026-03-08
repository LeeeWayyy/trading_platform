---
description: Run one complete review iteration (review + fix + re-review) with Gemini + Codex via direct CLI. Each invocation is a fresh iteration.
---

# Review Command

Run one complete shared-context review iteration. Each `/review` invocation handles: send to reviewers → fix issues → re-review (same continuation_id) → repeat until all approve.

## Usage

- `/review` — Review staged changes (default reviewers: gemini + codex)
- `/review branch` — Review all branch changes vs origin/master
- `/review --reviewer gemini` — Single reviewer only
- `/review --reviewer codex` — Single reviewer only
- `/review --reviewer gemini codex` — Explicit both (same as default)

---

## Step 1: Parse Arguments & Determine Scope

**Parse `$ARGUMENTS`:**
- If contains `branch` → branch mode
- If contains `--reviewer <name>` → use specified reviewer(s). Valid: `gemini`, `codex`
- Default reviewers: `gemini` then `codex`

**Determine diff scope:**

If branch mode:
```bash
git diff origin/master...HEAD --name-only --diff-filter=ACM
```

Otherwise (staged changes):
```bash
git diff --cached --name-only --diff-filter=ACM
```

**Guard rails:**
- If on master branch → tell user to create a feature branch first, STOP
- If no changes found → tell user "No changes to review", STOP
- If no files staged (and not branch mode) → ask user if they want to stage files or switch to branch mode

**Display the file list to the user before proceeding.**

---

## Step 2: Prepare Review Context

**Generate the diff for reviewers:**

```bash
# Write diff to a temp file for reviewer consumption (portable across GNU/BSD mktemp)
DIFF_FILE=$(mktemp "${TMPDIR:-/tmp}/review-diff-XXXXXX")
PROMPT_FILE=""
trap 'rm -f "${DIFF_FILE:-}" "${PROMPT_FILE:-}"' EXIT
```

If branch mode:
```bash
git diff origin/master...HEAD > "$DIFF_FILE"
```

Otherwise:
```bash
git diff --cached > "$DIFF_FILE"
```

**Build the review prompt:**

```
Review all staged changes with comprehensive analysis:

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

---

## Step 3: First Reviewer (Direct CLI)

Invoke the first reviewer directly via CLI. Which CLI to use depends on the reviewer list from Step 1 (default first reviewer: Gemini).

Write the review prompt to a temp file to avoid heredoc delimiter issues:

```bash
PROMPT_FILE=$(mktemp "${TMPDIR:-/tmp}/review-prompt-XXXXXX")
cat > "$PROMPT_FILE" <<'ENDPROMPT'
[REVIEW PROMPT FROM STEP 2]

Here is the diff to review:
ENDPROMPT
cat "$DIFF_FILE" >> "$PROMPT_FILE"
```

**Dispatch based on first reviewer from Step 1:**

If first reviewer is **Gemini**:
```bash
cat "$PROMPT_FILE" | gemini -p "Review the code diff provided on stdin."
```

If first reviewer is **Codex** (e.g., `/review --reviewer codex`):
```bash
# Branch mode:
codex review --base origin/master
# Staged/uncommitted mode:
codex review --uncommitted
```

**Shell safety rules:**
- Always use single-quoted heredocs (`<<'ENDPROMPT'`) to prevent shell expansion
- Pass diffs via temp files, never inline as arguments
- Validate any output before parsing

**Save the reviewer's response. If the CLI returns a session ID, save it for re-reviews.**

---

## Step 4: Second Reviewer

**Skip if only one reviewer was requested.**

**Dispatch based on second reviewer from Step 1 (default second reviewer: Codex):**

If second reviewer is **Codex**:
```bash
# Branch mode:
codex review --base origin/master
# Staged/uncommitted mode:
codex review --uncommitted
```

**Note:** Codex's `--uncommitted` reviews staged, unstaged, and untracked changes (broader than staged-only). This is a known Codex CLI limitation — there is no staged-only flag. Use `--base origin/master` for branch mode (preferred for precise scope). Codex uses its own built-in review rubric.

If second reviewer is **Gemini** (e.g., `/review --reviewer codex gemini`):
```bash
cat "$PROMPT_FILE" | gemini -p "Review the code diff provided on stdin."
```

---

## Step 5: Summarize Findings

**Parse ALL responses and create a combined issue list:**

```
Review Results:
━━━━━━━━━━━━━━
Files reviewed: X
Total issues: Y

CRITICAL: [count]
HIGH:     [count]
MEDIUM:   [count]
LOW:      [count]

Issues:
1. [CRITICAL] file.py:123 — Description (Reviewer: gemini)
2. [HIGH] file.py:456 — Description (Reviewer: codex)
...
```

**Display this summary to the user.**

**If zero issues → go to Step 7 (APPROVED).**

---

## Step 6: Fix Issues & Re-Review

**ZERO TOLERANCE — ALL issues must be fixed. No exceptions.**

1. **Ask user:** "Found X issues. Should I fix them all?"
2. Fix each issue systematically (read file → apply fix → mark done)
3. Re-stage fixed files: `git add <fixed-files>`
4. **Re-review using the same prompt (fresh invocation):**
   - Re-generate diff to temp file
   - Send to first reviewer (same prompt — never mention fixes or previous rounds)
   - Send to second reviewer (same prompt)
   - Note: Each re-review is a fresh CLI invocation; session resumption is not supported by current CLIs
5. Summarize new findings
6. **If new issues found → repeat from step 1**
7. **If ALL reviewers approve → go to Step 7**

**Critical:** Continue this loop until ALL reviewers explicitly approve within this iteration. Do NOT stop early.

---

## Step 7: Report Result

**When ALL reviewers approve with zero issues:**

```
Review APPROVED
━━━━━━━━━━━━━━
Reviewers: [list of reviewers who approved]

Ready to commit. Include in commit message:

zen-mcp-review: approved
continuation-id: <generated-uuid>
```

Generate the continuation-id with `uuidgen` (or equivalent). This provides a unique audit trail per review iteration, independent of CLI session support.

**If fixes were made during this iteration**, tell the user:

```
Fixes were made during this iteration.
Run /review again for a fresh iteration to confirm.
```

**If zero issues on first try** (no fixes needed), the code is fully approved.

**Clean up temp files** (also handled by the EXIT trap set in Step 2):
```bash
rm -f "$DIFF_FILE" "$PROMPT_FILE"
```

---

## Error Handling

**If a reviewer CLI is unavailable:**
1. Tell user: "Reviewer unavailable: [error]"
2. Offer options:
   - **Wait and retry** (recommended)
   - **Override** (requires explicit user approval — see CLAUDE.md policy)
3. If override approved, include in commit message:
   ```
   ZEN_REVIEW_OVERRIDE: Server unavailable
   User approved by: [user name]
   ```

**If only one reviewer available:**
- Complete review with available reviewer
- Note the gap to the user and recommend retrying the missing reviewer
- If retries fail, allow an override with explicit user approval
- Document in commit message:
  ```
  ZEN_REVIEW_OVERRIDE: [reviewer] unavailable after retries
  User approved by: [user name]
  ```

---

## Key Rules

1. **Same prompt every time** — never bias reviewers with fix context
2. **Same prompt within this invocation** — re-reviews use same prompt, fresh CLI invocations
3. **Fresh start between /review invocations** — each `/review` call starts fresh
4. **ALL approvals before completing** — don't report APPROVED until every reviewer approves
5. **Fix ALL issues including LOW** — zero tolerance, no deferral
6. **Single-quoted heredocs** — prevent shell injection in reviewer prompts
7. **Temp files for diffs** — never pass large diffs inline to CLI
