---
description: Review plan/task documents by verifying all claims against the actual codebase. Use /review-plan for .md docs. For source code review use /review-code.
---

# Plan Review

Review plan/task documents by verifying every factual claim (file paths, function signatures, permissions, DB schema) against the actual codebase. Uses Gemini + Codex via direct CLI.

## Usage

- `/review-plan` — Review changed plan/doc files (both reviewers)
- `/review-plan docs/TASKS/P6T16_TASK.md` — Review a specific plan file
- `/review-plan --reviewer gemini` — Gemini only
- `/review-plan --reviewer codex` — Codex only

## Step 1: Determine Scope

**Parse `$ARGUMENTS`:**
- File path → single-file mode
- `--reviewer <name>` → specified reviewer(s). Default: gemini + codex

**Get changed files:**

| Mode | Command |
|------|---------|
| Staged | `git diff --cached --name-only --diff-filter=ACM` |
| Unstaged | `git diff --name-only` |

Filter to docs/plan files only (`.md`, `.txt`, files in `docs/`). If no plan files changed → STOP.

Display file list before proceeding.

## Step 2: Build & Send Prompt

Write prompt to temp file with single-quoted heredoc (`<<'ENDPROMPT'`). Substitute `<GIT_FILES_CMD>` and `<FILE_LIST>` with actual values.

For single-file mode, set `<FILE_LIST>` to the specified file path.

**Prompt template:**

```
You are reviewing a task/plan document in a git repository. Do NOT expect the diff on stdin.

To discover what changed, run these git commands yourself:
- List changed files: <GIT_FILES_CMD>
- Read a file directly: cat <file>

Changed files: <FILE_LIST>

Review the plan/task document(s) by verifying claims against the actual codebase:

**Accuracy (CRITICAL):**
- Do all referenced file paths actually exist? Verify with: ls <path>
- Do function/method signatures match the actual source? Read the source files to confirm.
- Are class names, dataclass fields, and return types correct?
- Are database table and column names accurate? Check the migration files.

**Permission & Security Mappings (HIGH):**
- Do all referenced Permission enum values exist in the permissions module?
- Are role-to-permission mappings accurate? Check ROLE_PERMISSIONS dict.
- Are RBAC guards correctly specified for each page and action?
- Are audit logging calls using the correct AuditLogger method signatures?

**Architecture & Patterns (HIGH):**
- Does the plan follow established patterns in the codebase? Read existing similar pages.
- Are the navigation, page registration, and DB pool patterns consistent with existing pages?
- Is the implementation order logical?

**Code Examples (MEDIUM):**
- Are code examples syntactically valid?
- Do they use correct import paths, parameter names, and keyword arguments?
- Are async/await patterns used correctly?

**Completeness (MEDIUM):**
- Are acceptance criteria testable and complete?
- Are edge cases covered?
- Are there important codebase details the plan missed?

For EVERY claim about file paths, function signatures, or permissions, verify by reading the actual source file. Do not trust the plan — verify independently.

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

Otherwise: fix ALL issues (zero tolerance, including LOW) → re-send same prompt to all reviewers → repeat until all approve.

## Step 4: Report

```
Review APPROVED
━━━━━━━━━━━━━━
Reviewers: [list]

zen-mcp-review: approved
continuation-id: <uuidgen>
```

If fixes were made: "Run /review-plan again for a fresh iteration."
If zero issues on first try: fully approved.

Clean up: `rm -f "$PROMPT_FILE"`

## Error Handling

Reviewer CLI unavailable → tell user, offer wait/retry or override (requires explicit user approval per CLAUDE.md policy). Document overrides in commit message: `ZEN_REVIEW_OVERRIDE: [reason] / User approved by: [name]`

## Rules

1. Same prompt every time — never bias with fix context
2. Fresh start per `/review-plan` invocation
3. ALL reviewers must approve before reporting APPROVED
4. Fix ALL issues including LOW
5. Single-quoted heredocs to prevent shell injection
6. Both reviewers get identical custom prompt via stdin
7. Reviewers must READ actual source files to verify — never trust the plan at face value
