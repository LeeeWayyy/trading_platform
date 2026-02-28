---
description: Run one complete review iteration (review + fix + re-review) with Gemini + Codex via clink. Each invocation is a fresh iteration.
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

## Step 2: First Reviewer (Fresh Start)

Use `mcp__pal__clink` with these parameters:
- `cli_name`: first reviewer name (default: `"gemini"`)
- `role`: `"codereviewer"`
- `absolute_file_paths`: Array of absolute paths for ALL changed files
- **No `continuation_id`** (fresh start — generates this iteration's continuation_id)

**Prompt (use EXACTLY this — same prompt every time):**

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

**Save the `continuation_id` from the response.**

**Validate `continuation_id` format:** Must match UUID format (`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`). If not, reject and ask the reviewer for a valid ID. Never pass unvalidated IDs to shell commands.

---

## Step 3: Second Reviewer (Shared Context)

**Skip if only one reviewer was requested.**

Use clink with these parameters:
- `cli_name`: second reviewer name (default: `"codex"`)
- `role`: `"codereviewer"`
- `continuation_id`: **Use the validated continuation_id from Step 2**
- `absolute_file_paths`: Same file list as Step 2

**Prompt (EXACTLY the same as Step 2 plus one line):**

```
[SAME REVIEW PROMPT AS STEP 2]

Build upon the shared context by adding your own independent findings.
```

---

## Step 4: Summarize Findings

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

**If zero issues → go to Step 6 (APPROVED).**

---

## Step 5: Fix Issues & Re-Review (Same Continuation ID)

**ZERO TOLERANCE — ALL issues must be fixed. No exceptions.**

1. **Ask user:** "Found X issues. Should I fix them all?"
2. Fix each issue systematically (read file → apply fix → mark done)
3. Re-stage fixed files: `git add <fixed-files>`
4. **Re-review using the SAME continuation_id:**
   - Send to first reviewer with existing `continuation_id`
   - Send to second reviewer with existing `continuation_id`
   - Use each reviewer's original prompt (Step 2 prompt for first reviewer, Step 3 prompt for second reviewer — never mention fixes or previous rounds)
5. Summarize new findings
6. **If new issues found → repeat from step 1**
7. **If ALL reviewers approve → go to Step 6**

**Critical:** Continue this loop until ALL reviewers explicitly approve within this iteration. Do NOT stop early.

---

## Step 6: Report Result

**When ALL reviewers approve with zero issues:**

```
Review APPROVED
━━━━━━━━━━━━━━
continuation_id: <uuid>
Reviewers: [list of reviewers who approved]

Ready to commit. Include in commit message:

zen-mcp-review: approved
continuation-id: <final-uuid>
```

**If fixes were made during this iteration**, tell the user:

```
Fixes were made during this iteration.
Run /review again for a fresh iteration to confirm.
```

**If zero issues on first try** (no fixes needed), the code is fully approved.

---

## Error Handling

**If clink is unavailable:**
1. Tell user: "Zen-MCP unavailable: [error]"
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
2. **Same continuation_id within this invocation** — all reviewers share context; re-reviews use same ID
3. **Fresh start between /review invocations** — each `/review` call generates a new continuation_id
4. **ALL approvals before completing** — don't report APPROVED until every reviewer approves
5. **Fix ALL issues including LOW** — zero tolerance, no deferral
6. **Validate continuation_id** — must match UUID format before use in any tool call or shell command
