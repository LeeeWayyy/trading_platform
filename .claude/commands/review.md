---
description: Run one complete review iteration (review + fix + re-review) with Gemini + Codex via direct CLI. Each invocation is a fresh iteration.
---

# Review Command (Dispatcher)

Routes to the appropriate specialized review skill based on changed file types.

## Usage

- `/review` — Auto-detect review type from changed files
- `/review branch` — Review all branch changes vs origin/master
- `/review path/to/file` — Review a single file
- `/review --reviewer gemini` — Single reviewer only
- `/review --reviewer codex` — Single reviewer only

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

## Step 2: Classify & Dispatch

Classify based on changed files:

| Condition | Review Type | Skill |
|-----------|-------------|-------|
| ALL files are docs (`.md`, `.txt`, files in `docs/`) | Plan review | Follow `/review-plan` instructions |
| ANY file is source code (`.py`, `.sql`, `.yaml`, etc.) | Code review | Follow `/review-code` instructions |
| Mixed (code + docs) | Code review | Follow `/review-code` instructions (superset) |

**Pass through all arguments** (`$ARGUMENTS`) to the dispatched skill.

## Key Rules

1. **Same prompt every time** — never bias reviewers with fix context
2. **Fresh start per `/review` invocation** — each call starts fresh
3. **ALL reviewers must approve** before reporting APPROVED
4. **Fix ALL issues including LOW** — zero tolerance
5. **Never pass diff content to reviewers** — they use git commands
6. **Both reviewers get identical custom prompt** — Gemini via `cat | gemini`, Codex via `cat | codex exec -`
7. **NEVER use `codex review --uncommitted` or `codex review --base`** — these prevent custom prompts

See `/review-code` and `/review-plan` for full review procedures.
