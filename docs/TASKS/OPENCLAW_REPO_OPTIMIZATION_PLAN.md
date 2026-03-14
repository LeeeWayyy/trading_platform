# OpenClaw Repository Optimization Plan

**Status:** DRAFT
**Date:** 2026-03-13
**Author:** CTO (via Claude Code)
**Related ADRs:** ADR-0036 (AI Context Optimization)

## Context

We are transitioning from a single-agent development workflow to a 9-role AI agent company managed by OpenClaw. The current repository has ~462 files in `docs/` (425 markdown files, 9.9 MB, ~238K lines of markdown) that create excessive context noise for AI agents. Additionally, the repo lacks the structural conventions needed for multi-agent orchestration with strict scope boundaries.

This plan restructures the repository to:
1. Eliminate context waste (dead history, unused specs, scattered tasks)
2. Create per-folder agent context files for Claude Code executors
3. Establish the ticket flow protocol between OpenClaw and Claude Code
4. Add OpenClaw infrastructure files (dependency map, execution modes)

## The 9-Role Agent Architecture

| # | Role | Executor | cwd | Scope |
|---|------|----------|-----|-------|
| 1 | Lead Trader | File writer only | `docs/BUSINESS/` | Business requirements, no code |
| 2 | CTO / Architect | Claude Code (markdown only) | `/` (repo root) | Tasks, ADRs, architecture |
| 3 | Lead Quant | Claude Code | `strategies/` or `research/` | Strategies, models, factors |
| 4 | Data Engineer | Claude Code | `apps/market_data_service/` | Data pipelines, providers |
| 5 | Core Trading Eng | Claude Code | `apps/execution_gateway/` | Execution, orchestration, signals |
| 6 | Platform Services | Claude Code | `apps/auth_service/` | Auth, alerts, model registry |
| 7 | Frontend Engineer | Claude Code | `apps/web_console_ng/` | Web console UI |
| 8 | QA Engineer | Claude Code | `/` (repo root) | Tests, coverage, PR review |
| 9 | DevOps / SRE | Claude Code | `infra/` | Infrastructure, CI/CD, Docker |

### Orchestration Model

- **OpenClaw** owns the horizontal: persona definitions, routing logic, execution triggers, permission enforcement
- **Claude Code** owns the vertical: local file reading, code writing, test running, per-folder CLAUDE.md context
- **Ticket files** (`docs/TASKS/active/T*.md`) bridge the two: carry context from orchestrator into executor scope

### Ticket Lifecycle

```
CTO creates   → active/T42.md (Ticket section only, Status: PLANNING)
Executor adds  → Implementation Plan section
CTO approves   → Status: IN_PROGRESS
Executor codes → PR created, Status: REVIEW
QA reviews     → Status: DONE
CTO moves      → Entry in ACTIVE_SPRINT.md "Done", delete active/T42.md
```

---

## Phase 1: Context Purge

**Goal:** Remove ~8 MB / ~300 files of dead weight from the docs tree.

### Step 1.1: Archive Tag

```bash
git tag archive-pre-ai-company
```

Preserves all history at a retrievable point before deletion.

### Reference Sweep Protocol (Applies to ALL Deletions)

**Every directory or file deletion in this plan MUST be preceded by a reference sweep.** Before deleting any path, run:

```bash
rg -l "path/being/deleted" --type md --type py --type yaml --type json .
rg -l "path/being/deleted" .github/ scripts/ repomix.config.json
```

Fix or remove ALL references found in the results **in the same commit as the deletion**. This protocol replaces the approach of enumerating individual files — the codebase has deep cross-referencing that is impractical to list exhaustively in a plan document.

**Known high-reference-count deletions** (sweep carefully):
- `docs/SPECS/` — referenced in `generate_architecture.py`, `system_map.config.json`, `system_map_flow.md`, `system_map_deps.md`, `system_map.canvas`, `validate_doc_index.sh`, `docs/INDEX.md`, `test_generate_architecture.py`, `test_check_doc_freshness.py`
- `docs/ARCHIVE/` — referenced in `docs/INDEX.md`, various ADRs, `README.md`, `docs/GETTING_STARTED/PROJECT_STATUS.md`, `docs/LESSONS_LEARNED/t6-paper-run-retrospective.md`
- `docs/AI/Workflows/` — referenced in `GIT_WORKFLOW.md`, `CI_CD_GUIDE.md`, `TEMPLATES/`, `ADRs/README.md`, `scripts/README.md`, `markdown-link-check.yml`, `docs/INDEX.md`
- `AGENTS.md`/`GEMINI.md` — referenced in `AI/README.md`, `docs/INDEX.md`, `CI_CD_GUIDE.md`, `PROJECT_STATUS.md`, `ADR-0036`, `repomix.config.json`, `markdown-link-check.yml`, `zen_commit_msg.sh`, `REPO_MAP.md`
- `docs/TASKS/*.md` plans — referenced in `docs/INDEX.md`, various ADRs (`0020`, `0026`, `0027`, `0028`), `tests/apps/backtest_worker/README.md`
- `P6T*_TASK.md` files — referenced in `docs/INDEX.md`, `docs/AI/skills/analyze/SKILL.md`
- Root `README.md` — links to `docs/INDEX.md`, `CLAUDE.md`, `GEMINI.md`, `AGENTS.md`. Must be updated when any of these are deleted/removed.

### Step 1.2: Delete `docs/INDEX.md`

The top-level docs index (~700+ lines) catalogs every document in the repo. It creates unnecessary context for agents and becomes stale as files are added/removed. Per-folder CLAUDE.md files and `BACKLOG.md` replace its function. Run reference sweep before deletion — `docs/INDEX.md` is referenced from `docs/AI/README.md`, `scripts/dev/validate_doc_index.sh`, and other files.

**Also delete `scripts/dev/validate_doc_index.sh`** — its sole purpose is validating `docs/INDEX.md`. With the index gone, this script should be deleted. **Update `Makefile`** to remove the `validate-docs` target and any `ci-local` references to `validate_doc_index.sh`, otherwise `make validate-docs` and `make ci-local` will break.

### Step 1.3: Delete `docs/ARCHIVE/`

- `docs/ARCHIVE/TASKS_HISTORY/` — 95 files, 96K lines of completed task docs
- `docs/ARCHIVE/PLANS/` — 26 old routing/implementation plans
- `docs/ARCHIVE/workflow-gate/` — 31 filesystem entries (7 markdown files + scripts/tests) of superseded workflow orchestration

**Rationale:** Completed tasks are already in git commit history. No agent needs to read `P1T3_DONE.md` to write code. The archive is 4.9 MB (50% of all docs).

### Step 1.3: Update Tooling, Then Delete `docs/SPECS/`

**This is a two-part step: update tooling FIRST, then delete.**

- ~50+ specification files covering services, libs, strategies, infrastructure
- NOT imported by code — purely documentation artifacts

**Rationale:** Key spec information will be redistributed into per-folder CLAUDE.md files (Phase 3), giving each agent only the specs relevant to its scope. The global SPECS folder forces every agent to see all 50+ specs.

**Documentation gap:** SPECS is deleted in Phase 1 but per-folder CLAUDE.md files are created in Phase 3. This creates a temporary gap where component-level documentation is only available in git history. This is acceptable because: (1) the git tag preserves full SPECS content, (2) the existing code and tests remain the authoritative source during the gap, and (3) Phases 1-3 should be executed in quick succession.

**Part A — Redesign architecture doc model and update tooling (before deletion):**

The current architecture doc system assumes every component has a `spec` field pointing to `docs/SPECS/...`. This must be redesigned, not just patched:

1. **Redesign `docs/ARCHITECTURE/system_map.config.json`** — remove the `spec` field from all component and external node entries. Do NOT replace with CLAUDE.md links — the architecture map has more granular nodes (library subpackages, research strategies) than the CLAUDE.md files cover. Simply omit spec links from the architecture map; it documents system topology, not component documentation. **Also update `docs/ARCHITECTURE/system_map.schema.json`** — the schema currently requires `"spec"` as a required field on component objects (line ~54). Make `spec` optional or remove it from the schema entirely.
2. **Update `docs/ARCHITECTURE/README.md`** — remove instructions telling maintainers to create `docs/SPECS/...` files. Update to reference per-folder CLAUDE.md files as the new component documentation model.
3. **Update `scripts/dev/generate_architecture.py`** — this is the **primary CI blocker**, it hard-fails `--check` on missing spec files (line ~851). Remove all SPECS path references and spec-link generation.
4. **Update `tests/scripts/test_generate_architecture.py`** — remove spec-path assertions.
5. **`scripts/dev/validate_doc_index.sh`** — already deleted in Step 1.2 (along with `docs/INDEX.md`). No update needed.
6. **Decide on `check_doc_freshness.py` spec subsystem** — the script has spec-specific behavior (mappings at line ~144, expected-spec generation at line ~335) that is dormant when `docs/SPECS/` is absent but still exists as dead code. Remove this subsystem entirely for clarity. Update `tests/scripts/test_check_doc_freshness.py` accordingly.
7. **Regenerate architecture artifacts** — after removing `spec` fields from `system_map.config.json` and updating `generate_architecture.py`, run the generator to regenerate `docs/ARCHITECTURE/system_map_flow.md`, `docs/ARCHITECTURE/system_map_deps.md`, and `docs/ARCHITECTURE/system_map.canvas` without `../SPECS/...` links. Commit the regenerated files.
8. Run reference sweep (see protocol above) and fix all remaining references.

**Part B — Relocate OpenAPI artifacts, then delete:**

`docs/SPECS/openapi/` contains generated OpenAPI JSON files (`*.json`) for each service. These are real API contract artifacts, not just documentation. `docs/API/` already exists with `*.openapi.yaml` files. Before deleting SPECS:
- Relocate `docs/SPECS/openapi/*.json` files into `docs/API/` (alongside existing `.openapi.yaml` files) to consolidate API docs in one location
- Move `docs/SPECS/SCHEMAS.md` to `docs/API/SCHEMAS.md`, update path references, and validate content (the current file references the nonexistent `apps/web_console/metrics_server.py` — should be `apps/web_console_ng/main.py`)
- Run reference sweep for `docs/SPECS/openapi` to find and update all references

```bash
# After relocation:
git rm -r docs/SPECS/
```

### Step 1.4: Clean `docs/TASKS/` of Completed Plans

Remove files that are completed or superseded by this plan:
- `AI_CONTEXT_OPTIMIZATION_PLAN.md` (state: DONE, implemented via ADR-0036)
- `BUGFIX_CODE_REVIEW_CONCERNS.md` (superseded — remaining items absorbed into BACKLOG.md)
- `BUGFIX_RELIABILITY_SAFETY_IMPROVEMENTS.md` (superseded — remaining items absorbed into BACKLOG.md)
- `FORMATTING_ENHANCEMENT_PLAN.md` (superseded — remaining items absorbed into BACKLOG.md)
- `LOW_COVERAGE_MODULES_ANALYSIS.md` (superseded — analysis complete, action items in BACKLOG.md)
- `WEB_CONSOLE_MIGRATION_PLAN.md` (superseded — remaining items absorbed into BACKLOG.md)
- `REPO_CLEANUP_PLAN.md` (superseded by this plan)
- `skills-workflow-plan/` directory (superseded by OpenClaw orchestration)

**Note:** Files marked "superseded" may have unchecked acceptance criteria. Any remaining actionable items must be extracted into BACKLOG.md (Step 2.1) before deletion. This is "archive because superseded," not "completed."

### Step 1.5: (Merged into Step 1.3 Part A)

### Step 1.6: Add `.claudeignore`

```
docs/CONCEPTS/
docs/LESSONS_LEARNED/
docs/TEMPLATES/
docs/INCIDENTS/
data/*.parquet
```

Hides reference material from agent discovery without deleting it.

---

## Phase 2: Task Queue Restructure

**Goal:** Replace 18 individual task files with a scannable backlog system.

### Step 2.1: Generate `BACKLOG.md`

Extract from existing `P6T1_TASK.md` through `P6T18_TASK.md`:
- Task ID, title, priority, owner role, dependencies
- One-line acceptance criteria summary

Format:

```markdown
# Product Backlog

Last updated: 2026-03-13
Phase: P7 — AI Agent Company Infrastructure

## Priority Legend
P0: Must have | P1: Should have | P2: Nice to have

## Backlog

### P0 — Critical Path

- [ ] **P7T1** | Lead Quant | Mean reversion strategy v2
  - Acceptance: Sharpe > 1.2, max drawdown < 8%
  - Deps: None

### P1 — Important
...

## Completed This Phase
- [x] **P7T0** | CTO | Phase planning (PR #150)
```

### Step 2.2: Create `ACTIVE_SPRINT.md`

```markdown
# Active Sprint — Week of YYYY-MM-DD

## In Progress
| Task | Owner | Status | PR | Blockers |
|------|-------|--------|-----|----------|

## Done This Sprint
| Task | Owner | PR | Merged |
|------|-------|----|--------|
```

### Step 2.3: Create `TASK_TEMPLATE.md`

The canonical format for expanded tickets in `active/`:

```markdown
# {TASK_ID}: {Title}

## Ticket (Written by CTO)
**Assigned to:** {Agent Role}
**Execute in:** {/apps/service_name/ or /strategies/ etc.}
**Priority:** P0 | P1 | P2

### Requirement
One paragraph. What and why.

### Cross-References (executor should read these)
- path/to/relevant/file.py — description
- path/to/another/file.py — description

### Acceptance Criteria
- [ ] Measurable criterion 1
- [ ] Measurable criterion 2
- [ ] Tests pass, coverage >= ratchet

### Out of Scope
- Item 1 (prevents agent drift)

---

## Implementation Plan (Written by Executor before coding)
**Status:** PLANNING | IN_PROGRESS | REVIEW | DONE

### Analysis
Brief findings from reading cross-references.

### Changes
| File | Action | Description |
|------|--------|-------------|
| path/to/file.py | CREATE/MODIFY | What changes |

### Risks
- Risk description (or "None identified")

### Library Change Requests
- None / or describe needed lib changes for CTO approval
```

### Step 2.4: Create `docs/TASKS/active/` Directory

Empty directory for in-progress expanded tickets.

### Step 2.5: Remove Individual Task Files

After BACKLOG.md is generated:
```bash
git rm docs/TASKS/P6T*_TASK.md
git rm docs/TASKS/P6_PLANNING.md
```

### Step 2.6: Update `docs/TASKS/INDEX.md`

Rewrite to:
- Keep phase history (P0–P6 summary with completion dates)
- Replace P6 planning link with a pointer to `BACKLOG.md`
- Remove all broken links to deleted files

**Note:** `docs/INDEX.md` (the top-level docs index) is already deleted in Phase 1 Step 1.2.

### Step 2.7: Update Task Tooling

`scripts/admin/tasks.py` hardcodes the `PxTy_TASK/PROGRESS/DONE.md` naming scheme (line ~155) and reads phase plans from `P*_PLANNING.md` (line ~539). Related tests also assert this layout:
- `tests/test_tasks_cli.py` (line ~49)
- `tests/scripts/test_update_task_state_paths.py` (line ~15)

**Must delete/update (the old `_TASK/_PROGRESS/_DONE` lifecycle tooling):**
- `scripts/admin/tasks.py` — hardcodes `PxTy_TASK/PROGRESS/DONE.md` naming and `P*_PLANNING.md` discovery. OpenClaw replaces this CLI.
- `tests/test_tasks_cli.py` — asserts the current task layout contract.
- `scripts/admin/update_task_state.py` — while it doesn't hardcode lifecycle naming, it's part of the old task-tracking system (`start-task`, `complete`, `finish` workflow) that OpenClaw replaces. Delete alongside `tasks.py`.
- `scripts/dev/migrate_implementation_guides.py` — converts old guides to `PxTy_DONE.md` format. No longer needed.
- `scripts/dev/renumber_phase.py` — automates the old `PxTy` numbering scheme.
- `docs/TEMPLATES/README.md` — documents the old `_TASK/_PROGRESS/_DONE` template system.
- `docs/TEMPLATES/00-TEMPLATE_TASK.md`, `00-TEMPLATE_PROGRESS.md`, `00-TEMPLATE_DONE.md`, `00-TEMPLATE_FEATURE.md`, `00-TEMPLATE_PHASE_PLANNING.md` — old lifecycle templates used by `tasks.py`. Superseded by `TASK_TEMPLATE.md`.

**Must also delete (imports the deleted scripts directly):**
- `tests/scripts/test_update_task_state_paths.py` — imports `scripts/admin/update_task_state.py` directly. Deleting the script without deleting this test will break CI.

Run reference sweep for `PxTy_TASK` pattern to find any other references (e.g., `docs/AI/skills/analyze/SKILL.md` uses P6 task files as examples).

### Final Task Structure

```
docs/TASKS/
├── BACKLOG.md
├── ACTIVE_SPRINT.md
├── TASK_TEMPLATE.md
├── INDEX.md                          (rewritten — phase history + pointer to BACKLOG.md)
└── active/                           (CTO expands tickets here)
    └── .gitkeep
```

**Also delete:** `docs/TASKS/00-PLANNING_WORKFLOW_TEMPLATE.md` — not referenced by any script, superseded by `TASK_TEMPLATE.md`.

**Note:** `OPENCLAW_REPO_OPTIMIZATION_PLAN.md` (this file) will also exist during implementation and should be deleted upon completion.

---

## Phase 3: Per-Folder CLAUDE.md Files

**Goal:** Give each Claude Code executor scoped context that auto-loads when OpenClaw launches it in the target folder.

### Template

Each per-folder CLAUDE.md follows this structure:

```markdown
# {Service Name} — Agent Context

## Owner
{Role Name} (routed by OpenClaw)

## Scope
This folder. Additionally:
- WRITE access to: libs/{owned_lib}/  (if this role owns that lib per EXECUTION_MODES.yaml)
- READ-ONLY access to: libs/{other_lib}/  (for schemas and shared logic)

## Key Patterns
- Pattern 1 (e.g., FastAPI async handlers)
- Pattern 2 (e.g., Pydantic schemas for all configs)
- Pattern 3 (e.g., structured JSON logging)

## Commands
# Run from repo root:
make test                    # Full test suite
make lint                    # Full lint (black, ruff, mypy)
# Or run scoped tests directly:
PYTHONPATH=. poetry run pytest tests/apps/{service_name}/

## Rules
- Only modify files in this folder and your owned libs (per EXECUTION_MODES.yaml)
- If a lib change is needed outside your owned scope, STOP and escalate a
  Library Change Request back to the CTO via the ticket file
- Never duplicate logic that exists in libs/
```

### Files to Create/Update

| File | Owner Role | Read-Only Dependencies |
|------|-----------|------------------------|
| `apps/execution_gateway/CLAUDE.md` | Core Trading Eng | `libs/trading/`, `libs/core/` |
| `apps/orchestrator/CLAUDE.md` | Core Trading Eng | `libs/trading/`, `libs/core/` |
| `apps/signal_service/CLAUDE.md` | Core Trading Eng | `libs/models/`, `libs/trading/alpha/` |
| `apps/market_data_service/CLAUDE.md` | Data Engineer | `libs/data/` |
| `apps/backtest_worker/CLAUDE.md` | Lead Quant | `libs/models/`, `libs/trading/backtest/` |
| `apps/web_console_ng/CLAUDE.md` | Frontend Eng | Owns: `libs/web_console_data/`, `libs/web_console_services/`. Read-only: `libs/core/`, `libs/platform/web_console_auth/` |
| `apps/auth_service/CLAUDE.md` | Platform Services | `libs/platform/security/`, `libs/platform/secrets/`, `libs/platform/web_console_auth/` |
| `apps/alert_worker/CLAUDE.md` | Platform Services | `libs/platform/alerts/` |
| `apps/model_registry/CLAUDE.md` | Platform Services | `libs/models/` |
| `strategies/CLAUDE.md` | Lead Quant | `libs/models/`, `libs/trading/alpha/` |
| `infra/CLAUDE.md` | DevOps/SRE | None (self-contained) |

### Files to Update

| File | Changes |
|------|---------|
| Root `CLAUDE.md` | **Defer to Phase 5** (shared symlink). After symlinks removed: strip per-service details, keep project overview, critical patterns, commit format, guardrails. Add OpenClaw integration note. |
| `apps/CLAUDE.md` | **Defer to Phase 5** (shared symlink to `docs/AI/nested/apps.md`). After symlinks removed: reduce to minimal pointer. |
| `libs/CLAUDE.md` | **Defer to Phase 5** (shared symlink to `docs/AI/nested/libs.md`). After symlinks removed: add Library Change Request protocol with ownership map: Lead Quant owns `libs/models/`, Data Engineer owns `libs/data/`, Core Trading Eng owns `libs/trading/`, Platform Services owns `libs/platform/`, Frontend Eng owns `libs/web_console_data/` and `libs/web_console_services/`. Changes to libs outside an agent's owned scope require CTO approval. |
| `research/CLAUDE.md` | **Defer to Phase 5** (shared symlink). After symlinks removed: update for Lead Quant role context. |
| `tests/CLAUDE.md` | **Defer to Phase 5** (shared symlink). After symlinks removed: update for QA Engineer role context (repo-root access). |

### Context Inheritance: Parent vs. Child CLAUDE.md

Claude Code inherits CLAUDE.md files from parent directories. When OpenClaw launches Claude Code in `apps/execution_gateway/`, it will load:
1. Root `CLAUDE.md` (project-wide rules) — currently symlink to `docs/AI/AI_GUIDE.md`
2. `apps/CLAUDE.md` (apps-level rules) — currently symlink to `docs/AI/nested/apps.md`
3. `apps/execution_gateway/CLAUDE.md` (service-specific rules) — NEW file

**IMPORTANT: Shared symlink constraint.** Root `CLAUDE.md`, `GEMINI.md`, and `AGENTS.md` all point to the same `docs/AI/AI_GUIDE.md`. Similarly, `apps/CLAUDE.md`, `apps/GEMINI.md`, and `apps/AGENTS.md` all point to `docs/AI/nested/apps.md`. Any edit to these shared files changes behavior for ALL three CLIs (Claude, Gemini, Codex).

**Phase 3 must NOT modify the shared symlinked files** until Phase 5 removes the AGENTS.md/GEMINI.md symlinks. Instead:

- **Per-service `CLAUDE.md`** files (e.g., `apps/execution_gateway/CLAUDE.md`) are NEW standalone files — they don't affect Codex/Gemini since those tools don't read `CLAUDE.md`.
- **Root `CLAUDE.md`** and **parent `apps/CLAUDE.md`** modifications (slimming, removing service details) must be **deferred to Phase 5** when symlinks are removed and each file becomes independent.
- Until then, the parent-level context duplication is acceptable — the per-service CLAUDE.md adds scoped detail on top.

**To prevent double context (Phase 5, after symlinks removed):**
- **Root `CLAUDE.md`** must contain ONLY project-wide rules (no service-specific info)
- **`apps/CLAUDE.md`** (parent) should be reduced to a minimal pointer: "See per-service CLAUDE.md files for service-specific context."
- **`libs/CLAUDE.md`** (parent) should keep only the Library Change Request protocol and shared patterns — remove per-lib descriptions.
- **Per-service `CLAUDE.md`** files contain the actual scoped context.

---

## Phase 4: OpenClaw Infrastructure Files

**Goal:** Create the configuration files OpenClaw needs to orchestrate agents.

### Step 4.1: Create `docs/BUSINESS/`

```
docs/BUSINESS/
├── README.md
├── strategy_rules/
│   ├── mean_reversion.md
│   └── momentum.md
├── dashboard_requirements/
└── risk_constraints.md
```

The Lead Trader role writes business requirements here. No code, no technical details.

### Step 4.2: Create `docs/AI/DEPENDENCY_MAP.yaml`

```yaml
# When files in a path change, notify these roles.
# OpenClaw reads git diff of merged PRs and routes notifications.

libs/data/schemas/:
  notify: [Core Trading Eng, Lead Quant, Frontend Eng]
libs/trading/risk_management/:
  notify: [Core Trading Eng, DevOps/SRE]
libs/trading/alpha/:
  notify: [Lead Quant, Core Trading Eng]
libs/models/:
  notify: [Lead Quant, Core Trading Eng]
libs/platform/alerts/:
  notify: [Platform Services, DevOps/SRE]
libs/web_console_data/:
  notify: [Frontend Eng]
libs/web_console_services/:
  notify: [Frontend Eng]
libs/core/:
  notify: [ALL]
strategies/:
  notify: [Lead Quant, Core Trading Eng]
infra/:
  notify: [DevOps/SRE]
```

### Step 4.3: Create `docs/AI/EXECUTION_MODES.yaml`

```yaml
# OpenClaw uses this to configure each agent's launch parameters.

lead_trader:
  executor: file_writer_only
  cwd: docs/BUSINESS/
  can_write: ["docs/BUSINESS/**"]
  can_read: ["docs/TASKS/BACKLOG.md", "docs/TASKS/ACTIVE_SPRINT.md"]

cto:
  executor: claude_code
  cwd: "/"
  can_write: ["docs/TASKS/**", "docs/ADRs/**", "docs/ARCHITECTURE/**"]
  can_read: ["**"]
  forbidden_extensions: [".py", ".js", ".ts"]

lead_quant:
  executor: claude_code
  cwd: strategies/
  can_write: ["strategies/**", "research/**", "libs/models/**"]
  can_read: ["libs/data/**", "libs/trading/**"]

data_engineer:
  executor: claude_code
  cwd: apps/market_data_service/
  can_write: ["apps/market_data_service/**", "libs/data/**", "scripts/data/**"]
  can_read: ["libs/core/**", "libs/platform/web_console_auth/**", "libs/platform/secrets/**"]

core_trading_eng:
  executor: claude_code
  cwd: apps/execution_gateway/
  can_write:
    - "apps/execution_gateway/**"
    - "apps/orchestrator/**"
    - "apps/signal_service/**"
    - "libs/trading/**"
  can_read: ["libs/core/**", "libs/models/**", "libs/platform/security/**"]

platform_services:
  executor: claude_code
  cwd: apps/auth_service/
  can_write:
    - "apps/auth_service/**"
    - "apps/alert_worker/**"
    - "apps/model_registry/**"
    - "libs/platform/**"
  can_read: ["libs/core/**"]

frontend_eng:
  executor: claude_code
  cwd: apps/web_console_ng/
  can_write:
    - "apps/web_console_ng/**"
    - "libs/web_console_data/**"
    - "libs/web_console_services/**"
  can_read: ["libs/core/**", "libs/platform/web_console_auth/**", "libs/platform/security/**", "libs/platform/admin/**"]

qa_engineer:
  executor: claude_code
  cwd: "/"
  can_write: ["tests/**", "scripts/testing/**"]
  can_read: ["**"]

devops_sre:
  executor: claude_code
  cwd: infra/
  can_write:
    - "infra/**"
    - ".github/workflows/**"
    - "docker-compose*.yml"
    - "scripts/ops/**"
  can_read: ["apps/**/Dockerfile", "apps/**/config.py"]
```

**IMPORTANT — `can_read` must be import-verified before deployment:**

The mappings above are approximate architectural boundaries. The actual codebase has pervasive cross-lib imports that are NOT fully captured here. Known gaps include:
- Nearly ALL services import `libs/core/**` (logging, secrets, health checks)
- Nearly ALL services import `libs/platform/web_console_auth/**` (permissions, rate limiting)
- `apps/web_console_ng` imports from `libs/data`, `libs/trading`, `libs/models`, `libs/platform/analytics`, `libs/common` — far broader than listed
- `apps/signal_service` imports `libs/platform/web_console_auth/permissions`
- `apps/alert_worker` imports `libs/core/common` and `libs/platform/web_console_auth/rate_limiter`

**Before deploying to OpenClaw**, run this for each service to generate accurate `can_read` lists:
```bash
rg "from libs\." apps/<service>/ | sed 's/.*from //' | cut -d. -f1-3 | sort -u
```
The EXECUTION_MODES.yaml above is a **starting template** — it MUST be reconciled with actual imports before enforcement.

---

## Phase 5: Cleanup Symlinks & Legacy AI Context

**Goal:** Remove redundant AI context files and consolidate the AI directory. This phase is an architectural change that supersedes parts of ADR-0036.

### Step 5.0: Write ADR-0039 (Prerequisite)

**This phase reverses the cross-platform AI-context architecture established in ADR-0036.** Before making any changes, write `docs/ADRs/ADR-0039-openclaw-agent-architecture.md` documenting:
- **Context:** Transitioning from single-agent (3-CLI shared context) to 9-role OpenClaw orchestration
- **Decision:** OpenClaw routes exclusively to Claude Code as executor. Gemini and Codex serve as reviewers only (invoked via `/review` skill). Standalone `AGENTS.md` and `GEMINI.md` context files are no longer needed.
- **Consequences:** Replaces the shared-symlink model from ADR-0036 with per-folder scoped CLAUDE.md files.

### Step 5.1: Remove Redundant Symlinks

**Timing: This step MUST wait until OpenClaw is operational.** The current `AGENTS.md` and `GEMINI.md` symlinks are live architecture — Codex and Gemini discover instructions through them today. Removing them before OpenClaw replaces the orchestration will break direct Codex/Gemini usage.

Remove (only after OpenClaw is handling all agent routing):
- `apps/AGENTS.md`, `apps/GEMINI.md` (keep `apps/CLAUDE.md`)
- `libs/AGENTS.md`, `libs/GEMINI.md` (keep `libs/CLAUDE.md`)
- `research/AGENTS.md`, `research/GEMINI.md` (keep `research/CLAUDE.md`)
- `tests/AGENTS.md`, `tests/GEMINI.md` (keep `tests/CLAUDE.md`)
- Root `AGENTS.md`, `GEMINI.md` (keep root `CLAUDE.md`)

**Tooling and reference updates required (run reference sweep for completeness):**
- Update `scripts/hooks/zen_commit_msg.sh` to remove `AGENTS.md` and `GEMINI.md` from the docs-only commit pathspec list
- Update `docs/GETTING_STARTED/REPO_MAP.md` to remove references to `AGENTS.md` and `GEMINI.md` symlinks
- Update `.github/workflows/markdown-link-check.yml` which explicitly references `CLAUDE.md/AGENTS.md`
- Update `docs/AI/README.md` which documents the three-CLI symlink model
- `docs/INDEX.md` — already deleted in Step 1.2, no update needed.
- Update `docs/GETTING_STARTED/CI_CD_GUIDE.md` which references these files
- Update `docs/GETTING_STARTED/PROJECT_STATUS.md` which references these files
- Update `docs/ADRs/ADR-0036-ai-context-architecture.md` — add note that this is superseded by ADR-0039
- Update `repomix.config.json` which includes `AGENTS.md`/`GEMINI.md` ignore patterns in its config. Also check `.github/workflows/repomix-context.yml` — it has a config-sync check comparing its hardcoded ignore list against `repomix.config.json`. While the workflow currently only warns on drift, both files should be kept in sync to avoid CI noise.

### Step 5.2: Consolidate `docs/AI/`

**Keep:**
- `AI_GUIDE.md` — primary guide (root CLAUDE.md points here)
- `skills/` — platform-agnostic skill definitions
- `nested/` — per-directory context files
- `DEPENDENCY_MAP.yaml` (new, from Phase 4)
- `EXECUTION_MODES.yaml` (new, from Phase 4)

**Delete:**
- `Analysis/` — one-time code analysis reports
- `Audits/` — one-time audit findings
- `Examples/` — PR guidelines (move essential bits to AI_GUIDE.md)
- `Implementation/` — phase implementation plans (completed)
- `Research/` — AI capability research (completed)
- `Workflows/` — superseded by OpenClaw orchestration
- `Prompts/` — superseded by OpenClaw persona prompts

**Reference cleanup required (run reference sweep, must be done in same commit):**
- Update `docs/AI/README.md` to remove links to deleted directories (Workflows/, Examples/, Prompts/, etc.)
- `docs/INDEX.md` — already deleted in Step 1.2, no update needed.
- Update `scripts/README.md` to remove the link to `docs/AI/Workflows/README.md`
- Update `.github/workflows/markdown-link-check.yml` which references `docs/AI/Workflows/`
- Update `docs/STANDARDS/GIT_WORKFLOW.md` which links to multiple `docs/AI/Workflows/*` files
- `docs/TEMPLATES/README.md` and `docs/TEMPLATES/00-TEMPLATE_TASK.md` — already deleted in Phase 2 Step 2.7. No update needed.
- Update `docs/ADRs/README.md` which links to Workflows/
- Update `docs/GETTING_STARTED/CI_CD_GUIDE.md` which references Workflows/ and other AI subdirs

### Step 5.3: Update Root `CLAUDE.md`

Strip content that now lives in per-folder files. Final root CLAUDE.md should contain only:
- Project overview (Qlib + Alpaca platform, master branch)
- Critical patterns (idempotency, circuit breaker, risk check, feature parity)
- Commit format (conventional + zen trailers)
- Operational guardrails (pre-trade, post-trade, circuit breakers)
- OpenClaw integration note: "If launched by OpenClaw, read the ticket path provided in your prompt first"

---

## Execution Order

```
Phase 1 (Purge)                ──→ No dependencies, do first
Phase 2 (Task Queue)           ──→ Depends on Phase 1 (clean TASKS/ first)
Phase 3 (Per-folder CLAUDE.md) ──→ Independent, can run parallel with Phase 2
Phase 4 (OpenClaw infra)       ──→ Depends on Phase 2 (BACKLOG.md must exist)
Phase 5 (Cleanup)              ──→ Do last (depends on Phase 3 completion)
```

## Expected Outcomes

| Metric | Before | After |
|--------|--------|-------|
| Total docs files (all types) | ~462 | ~150 |
| Total docs size | 9.9 MB | ~2 MB |
| Markdown lines | 237K | ~50K |
| Agent context per executor | Full 237K lines | ~50-100 lines (per-folder CLAUDE.md) |
| Task files to scan backlog | 18 files | 1 file (BACKLOG.md) |
| Cross-scope contamination | Unrestricted | Enforced by EXECUTION_MODES.yaml |

## Risks

| Risk | Mitigation |
|------|------------|
| Deleting SPECS loses institutional knowledge | Key info redistributed into per-folder CLAUDE.md files. Full SPECS preserved in git tag `archive-pre-ai-company`. |
| Deleting ARCHIVE loses task history | All history preserved in git commits and the archive tag. |
| Per-folder CLAUDE.md files drift out of sync | CTO reviews CLAUDE.md changes as part of architectural review. |
| BACKLOG.md becomes stale | CTO is the sole writer — OpenClaw enforces this via execution modes. |
| Library Change Request protocol adds friction | Intentional. Uncoordinated lib changes break multiple agents. The friction is a feature. Designated lib owners can modify their own libs directly. |
| Removing AGENTS.md/GEMINI.md breaks ADR-0036 | Write ADR-0039 first to document the architectural transition. Update zen_commit_msg.sh and REPO_MAP.md. |
| Deleting docs/AI/ subdirs leaves broken links | Reference cleanup in docs/AI/README.md, docs/INDEX.md, and scripts/README.md is bundled into the same commit. |
| Deleting SPECS breaks CI | `check_doc_freshness.py` already handles missing SPECS gracefully. The real blocker is `generate_architecture.py` which must be updated first, along with generated architecture docs and their tests. |
| Task tooling breaks after Phase 2 | `scripts/admin/tasks.py` and related tests hardcode `PxTy_TASK.md` naming. Either delete (recommended — OpenClaw replaces it) or update to new format. |
