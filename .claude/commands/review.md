---
description: Run comprehensive zen-mcp shared-context iteration review (Gemini + Codex) before commit or PR. Follows 03-reviews.md golden source.
---

# Review Command

Run the mandatory comprehensive code review using shared-context iterations.

**Golden source:** `docs/AI/Workflows/03-reviews.md`

## Usage

- `/review` — Review staged changes (pre-commit)
- `/review branch` — Review all branch changes vs origin/master (pre-PR)

---

## Step 1: Determine Review Scope

**If argument is `branch`:**
```bash
git diff origin/master...HEAD --name-only --diff-filter=ACM
```

**Otherwise (default — staged changes):**
```bash
git diff --cached --name-only --diff-filter=ACM
```

**Guard rails:**
- If on master branch → tell user to create a feature branch first, STOP
- If no changes found → tell user "No changes to review", STOP
- If no files staged (and not branch mode) → ask user if they want to stage files or switch to branch mode

**Display the file list to the user before proceeding.**

---

## Step 2: First Reviewer — Gemini (Fresh Start)

Use `mcp__pal__clink` with these parameters:
- `cli_name`: `"gemini"`
- `role`: `"codereviewer"`
- `absolute_file_paths`: Array of absolute paths for ALL changed files
- **No `continuation_id`** (fresh start — this generates the iteration's continuation_id)

**Prompt (use EXACTLY this — same prompt every iteration):**

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

**Save the `continuation_id` from Gemini's response** — this is the iteration's shared context ID.

---

## Step 3: Second Reviewer — Codex (Shared Context)

Use `mcp__pal__clink` with these parameters:
- `cli_name`: `"codex"`
- `role`: `"codereviewer"`
- `continuation_id`: **Use the continuation_id from Step 2**
- `absolute_file_paths`: Same file list as Step 2

**Prompt (EXACTLY the same as Step 2 plus one line):**

```
[SAME REVIEW PROMPT AS STEP 2]

Build upon the shared context by adding your own independent findings.
```

---

## Step 4: Analyze Findings

**Parse BOTH responses and create a combined issue list:**

```
Review Results (Iteration N):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Files reviewed: X
Total issues: Y

CRITICAL: [count] — blocks commit
HIGH:     [count] — blocks commit
MEDIUM:   [count] — must fix, no deferral
LOW:      [count] — must fix before commit

Issues:
1. [CRITICAL] file.py:123 — Description (Reviewer: gemini)
2. [HIGH] file.py:456 — Description (Reviewer: codex)
...
```

**Display this summary to the user.**

---

## Step 5: Apply Zero-Tolerance Rules

**ZERO TOLERANCE — ALL issues must be fixed. No exceptions.**

| Scenario | Action |
|----------|--------|
| CRITICAL/HIGH/MEDIUM found | Fix ALL → start fresh iteration (Step 6) |
| Only LOW found | Fix ALL → commit (no fresh iteration needed) |
| Zero issues, both approved | APPROVED — proceed to Step 7 |

**Ask user:** "Found X issues. Should I fix them all?"

**If user approves fixes:**
1. Fix each issue systematically (read file → apply fix → mark done)
2. Re-stage fixed files: `git add <fixed-files>`
3. Proceed to Step 6 (fresh iteration) or Step 7 (if only LOW fixes)

---

## Step 6: Fresh Iteration (If Fixes Were Made for MEDIUM+ Issues)

**Start a completely new iteration:**
1. Go back to Step 2 with **NO continuation_id** (fresh start)
2. Use **EXACTLY THE SAME PROMPT** — do NOT mention previous iterations or fixes
3. This generates a NEW continuation_id
4. Repeat Steps 2-5 until both reviewers approve with zero issues

**CRITICAL:** Never say "review my fixes" or "check what changed". The reviewer must see the code with completely fresh eyes.

---

## Step 7: Record Approvals

**When BOTH reviewers approve with zero issues:**

```bash
# Record reviews with workflow gate
./scripts/workflow_gate.py record-review gemini approved --continuation-id <final-continuation-id>
./scripts/workflow_gate.py record-review codex approved --continuation-id <final-continuation-id>
```

**Tell user the review is complete:**

```
Review APPROVED
━━━━━━━━━━━━━━
Iterations: N
Final continuation_id: <uuid>
Gemini: APPROVED
Codex: APPROVED

Ready to commit. Include in commit message:

zen-mcp-review: approved
continuation-id: <final-uuid>
```

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
- Note the gap to the user
- Retry the missing reviewer before committing

---

## Key Rules

1. **Same prompt every iteration** — never bias reviewers with fix context
2. **Same continuation_id within iteration** — both reviewers share context
3. **Fresh start between iterations** — no continuation_id reuse across iterations
4. **ALL approvals before new iteration** — never start iteration N+1 until iteration N has all approvals
5. **Fix ALL issues including LOW** — zero tolerance, no deferral
6. **Never use --no-verify** — workflow gates are mandatory

---

## See Also

- `docs/AI/Workflows/03-reviews.md` — Golden source for review process
- `docs/AI/Workflows/12-component-cycle.md` — 6-step component pattern
- `.ai_workflow/config.json` — Reviewer configuration
