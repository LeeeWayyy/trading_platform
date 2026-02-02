# AI Assistant Development Guide

**Comprehensive guidance for all AI coding assistants working with this repository.**

This guide provides complete instructions for Claude Code, Codex, Gemini, and other AI assistants. It consolidates all AI-related guidance into a single source of truth.

---

## 🤖 AUTO-RESUME: Check for Incomplete Work

**CRITICAL:** Check `.ai_workflow/workflow-state.json` for incomplete work before starting. If found, follow [Workflows/08-session-management.md](./Workflows/08-session-management.md) to resume.

---

## ⚠️ CRITICAL: Review Override Policy for AI Agents

**AI agents (Claude Code, Codex, Gemini, etc.) MUST NEVER use `ZEN_REVIEW_OVERRIDE` without explicit human approval.**

**Why this matters:**
- Skipping reviews caused 7 fix commits and 10-15 hours of wasted time in past incidents
- Review gates are the PRIMARY defense against trading bugs, security issues, and architectural problems
- `ZEN_REVIEW_OVERRIDE` is ONLY for genuine emergencies (server outages, critical hotfixes)

**If zen-mcp server is unavailable, AI agents MUST:**
1. **Ask user explicitly:** "Zen-MCP server unavailable. Wait for server, or use override (requires your approval)?"
2. **Wait for explicit approval** before using `ZEN_REVIEW_OVERRIDE`
3. **Include user's name** in override commit message: `User approved by: [name]`
4. **NEVER assume** user wants to bypass review for convenience or time-saving

**Violating this policy:**
- Commits with `git commit --no-verify` are detected by CI and will fail the build
- Commits with `ZEN_REVIEW_OVERRIDE` without user approval will be flagged in code review
- Bypassing review gates undermines the entire quality assurance system

See [Workflows/03-reviews.md](./Workflows/03-reviews.md#zen-mcp-server-unavailable) for complete override procedure.

---

## ⚠️ CRITICAL: CI-Local Single Instance Rule

**AI agents MUST NEVER run multiple `make ci-local` instances simultaneously.**

**Why this matters:**
- Multiple CI instances consume excessive system resources (CPU, memory)
- Tests may interfere with each other causing flaky failures
- Background processes accumulate and become difficult to manage
- User experience degrades significantly

**Rules for CI execution:**
1. **ONLY ONE** `make ci-local` at a time - the Makefile has a lock mechanism
2. **NEVER** kill a running CI and immediately start another
3. **WAIT** for the current CI to complete before starting a new one
4. **DO NOT** run CI in background mode repeatedly - monitor ONE instance to completion

**If CI fails or needs to be restarted:**
1. Wait for current CI to finish OR
2. Kill it cleanly: `pkill -f "make ci-local" && rm -f .ci-local.lock`
3. Then start a fresh instance

---

## Project Overview

This is a **Qlib + Alpaca trading platform** designed for algorithmic trading. The system produces signals using Qlib-based models and executes trades via Alpaca's API with emphasis on safety, idempotency, and parity between research and production.

> **ℹ️ Repository Default Branch:**
> This project uses `master` as the default branch (NOT `main`).
> All PRs merge to `master`, and all branch comparisons use `origin/master`.

**Key principles:**
- Idempotency first for all order paths
- Feature parity: research and production feature definitions must share code
- Never produce duplicate orders
- Circuit breakers override signals
- **Every architectural change requires an ADR** (see `/../STANDARDS/ADR_GUIDE.md`)
- **All code must be educational and well-documented for learning** (see `/../STANDARDS/DOCUMENTATION_STANDARDS.md`)

---

## 🚀 Quick Start

**New to the project?** Read this file, then see [Workflows/README.md](./Workflows/README.md) for setup and workflows.

**Ready to code?**
1. **🔍 MANDATORY: Complete Pre-Implementation Analysis** (30-60 min)
   - Follow [Workflows/00-analysis-checklist.md](./Workflows/00-analysis-checklist.md)
   - Identify ALL impacted components, call sites, tests
   - Verify pattern parity and process compliance
   - Create comprehensive todo list with 6-step pattern (plan → plan-review → implement → test → review → commit)
   - **⚠️ DO NOT write code before completing analysis**

2. **For task documents:** Request task creation review (see [Workflows/02-planning.md](./Workflows/02-planning.md))

3. **Start task and set component:**
   - Start task: `./scripts/workflow_gate.py start-task docs/TASKS/TASK.md feature/branch`
   - Set component: `./scripts/workflow_gate.py set-component "Component-Name"`

4. **Break feature into logical components** — Use 6-step pattern (see below)
   - For large tasks (>8h), decompose into subfeatures: [Workflows/02-planning.md](./Workflows/02-planning.md)

5. **For EACH component:**
   - Request plan review: `./scripts/workflow_gate.py advance plan-review`
   - After plan approval, transition to implement: `./scripts/workflow_gate.py advance implement`
   - Implement logic + create test cases (TDD)
   - **🔒 MANDATORY: Request zen-mcp review** (NEVER skip): [Workflows/03-reviews.md](./Workflows/03-reviews.md)
   - **🔒 MANDATORY: Run `make ci-local`** (NEVER skip)
   - Commit ONLY after review approval + CI passes: [Workflows/01-git.md](./Workflows/01-git.md)

6. Repeat until feature complete

7. **🔍 MANDATORY: Comprehensive review** via shared-context iterations (Gemini + Codex): [Workflows/03-reviews.md](./Workflows/03-reviews.md)

8. Create PR: [Workflows/01-git.md](./Workflows/01-git.md)

---

## 📖 Essential Documentation

| Category | Key Files |
|----------|-----------|
| **Workflows** | [Workflows/README.md](./Workflows/README.md) (index), [00-analysis-checklist.md](./Workflows/00-analysis-checklist.md), [12-component-cycle.md](./Workflows/12-component-cycle.md), [03-reviews.md](./Workflows/03-reviews.md), [01-git.md](./Workflows/01-git.md) |
| **Tools** | [06-repomix.md](./Workflows/06-repomix.md) (codebase analysis), [repomix.config.json](../../repomix.config.json) (project config) |
| **Standards** | [/../STANDARDS/](../STANDARDS/) (CODING, DOCUMENTATION, GIT_WORKFLOW, TESTING, ADR_GUIDE) |
| **Implementation** | [/../TASKS/](../TASKS/) (tickets), [/../ADRs/](../ADRs/) (architecture decisions), [/../CONCEPTS/](../CONCEPTS/) (trading glossary) |
| **Navigation** | [/../INDEX.md](../INDEX.md) (canonical index), this file (AI quick-start) |

---

## 🤖 AI Agent Roles

### Claude Code (Main Development Agent)
- **Primary role:** Writes, refactors, and implements code
- **Tools:** Full codebase access, can run commands, create files
- **Workflow:** Follows 6-step pattern for all development
- **Reviews:** Requests reviews from Codex/Gemini via zen-mcp

### Codex (Code Review Agent via zen-mcp)
- **Primary role:** Comprehensive code review (shared-context with other reviewers)
- **Focus:** Trading safety, architecture, code quality, security, testing
- **Speed:** 1-2 minutes per review
- **Access:** Via `mcp__zen__clink` with `cli_name='codex'` and `role='codereviewer'`

### Gemini (Code Review Agent via zen-mcp)
- **Primary role:** Comprehensive code review (shared-context with other reviewers)
- **Focus:** Architecture, integration concerns, test coverage, all comprehensive criteria
- **Speed:** 2-3 minutes per review
- **Access:** Via `mcp__zen__clink` with `cli_name='gemini'` and `role='codereviewer'`

### Planning Review Agents (via zen-mcp)
- **Primary role:** Task creation and planning validation
- **Access:** Gemini planner + Codex planner (shared-context iterations)
- **Speed:** ~3-5 minutes total (comprehensive planning review)
- **Usage:** For task documents before implementation starts

---

## 🤖 Zen-MCP + Clink Integration

**⚠️ MANDATORY: ALL zen-mcp interactions MUST use `mcp__zen__clink` exclusively** (direct zen-mcp tools bypass CLI authentication).

**Review approach:** Shared-context iterations
- First reviewer starts fresh (generates continuation_id)
- Subsequent reviewers use same continuation_id
- Fix issues, re-review with same ID until all approve
- If fixes made, start new iteration fresh (same prompt as iteration 1)
- Approved = all reviewers approve on first try of an iteration

**⚠️ CRITICAL: Fix ALL issues even if reviewers say "APPROVED"**
- Reviewers may approve with LOW severity suggestions
- These are still issues that MUST be fixed
- If approved with only LOW issues, fix them before committing (no new iteration needed)
- If approved with any MEDIUM/HIGH issues, fix them and start a new fresh review iteration
- Never rationalize skipping issues (e.g., "it's minor", "cleanup later", "pre-existing code")

**See [Workflows/03-reviews.md](./Workflows/03-reviews.md) for complete workflow**

---

## ⚙️ Common Commands

### Python Environment Setup

**⚠️ CRITICAL: Always Use Virtual Environment**

**NEVER use system Python directly!** Always activate the virtual environment first:

```bash
# Activate virtual environment (REQUIRED before ANY Python command)
source .venv/bin/activate

# Verify you're in venv (should show .venv path)
which python3
# Output: /Users/.../trading_platform/.venv/bin/python3
```

**DO:**
```bash
source .venv/bin/activate     # Activate first
python3 -m pytest             # Then run commands
pip install -r requirements.txt
PYTHONPATH=. python3 scripts/paper_run.py
```

**DON'T:**
```bash
# ❌ WRONG - Uses system Python
python3 -m pytest

# ❌ WRONG - Missing virtual environment
pip install package
```

**Why Virtual Environment?**
1. **Isolated Dependencies**: Project packages don't interfere with system Python
2. **Reproducible Environment**: Everyone uses same package versions
3. **Clean Testing**: Tests run with correct dependencies
4. **Prevents Version Conflicts**: System Python may have incompatible packages

---

### Development
```bash
make up          # Start infrastructure (Postgres, Redis, Grafana, Prometheus)
make down        # Stop infrastructure
make fmt         # Format code (black + ruff)
make lint        # Run linters (mypy, ruff)
make test        # Run test suite
make ci-local    # Run full CI suite locally
```

### Paper Trading
```bash
make paper-run   # Execute end-to-end paper trading for today
```

### Operations
```bash
make status       # Check positions, open orders, P&L
make circuit-trip # Manually trip circuit breaker
make kill-switch  # Cancel all orders, flatten positions, block new signals
```

### Context Management
```bash
# Checkpointing (before delegation/session end)
./scripts/context_checkpoint.py create --type {delegation|session_end}
./scripts/context_checkpoint.py list              # Show available checkpoints
./scripts/context_checkpoint.py restore --id <checkpoint_id>
./scripts/context_checkpoint.py cleanup --older-than 7d  # Clean old checkpoints

# Context monitoring (auto-delegation at 70%+ usage)
# Context monitoring is manual - check token usage in status
./scripts/workflow_gate.py status                 # Check current workflow state
```

**Thresholds:** <70% OK | 70-84% delegation recommended | ≥85% delegation mandatory.

See [Workflows/16-subagent-delegation.md](./Workflows/16-subagent-delegation.md) for details.

---

## 🏗️ Code Architecture

### Multi-Service Design
Microservices pattern with FastAPI communicating via:
- **Redis** Streams/pub-sub for events
- **Postgres** for persistent state (orders, positions, snapshots)
- **Redis** for circuit breaker state and online features

### Key Data Flows

**Signal Generation:**
```
Model Registry → Signal Service → Target Weights → Risk Manager → Execution Gateway
```

**Order Execution:**
```
Execution Gateway → Alpaca API → Webhook → Position Tracker → Reconciler
```

**Circuit Breaker:**
```
Post-Trade Monitor → Redis Breaker State → All Services Check Before Action
```

### Critical Patterns

| Pattern | Implementation |
|---------|----------------|
| **Idempotency** | `client_order_id = hash(symbol + side + qty + price + strategy + date)[:24]` |
| **Circuit Breaker** | Check `redis.get("cb:state") != b"TRIPPED"` before every order |
| **Risk Check** | Validate `abs(current_pos + order.qty) <= limits.max_pos_per_symbol` |
| **Feature Parity** | Share code between research/production (never duplicate logic) |

### Reconciliation
Boot-time and periodic reconciliation:
1. Pull broker positions/orders
2. Diff against DB
3. Heal discrepancies (cancel stale >15m, adjust positions)
4. Alert on failures
5. Unlock after successful reconciliation

---

## 🎯 Development Process

Use `workflow_gate.py` for all workflow operations. It enforces gates, manages context, and automates reviews.

### Quick Start

```bash
# 1. Start task (create task file first, then register)
./scripts/workflow_gate.py start-task docs/TASKS/P1T14_TASK.md feature/P1T14-task-branch

# 2. For each component (6-step pattern)
./scripts/workflow_gate.py set-component "Component-Name"  # Use alphanumeric, dots, hyphens
./scripts/workflow_gate.py advance plan-review             # Request plan review via zen-mcp
# → After plan approval:
./scripts/workflow_gate.py advance implement               # Transition to implementation
# → Implement + test (TDD)
./scripts/workflow_gate.py advance test
./scripts/workflow_gate.py advance review
# → Request shared-context iteration reviews (see 03-reviews.md)
./scripts/workflow_gate.py record-review gemini approved --continuation-id <id>
./scripts/workflow_gate.py record-review codex approved --continuation-id <id>  # Same ID
./scripts/workflow_gate.py record-ci passed                # After make ci-local passes
./scripts/workflow_gate.py check-commit                    # Verify gates met
git commit -m "message"                                    # Pre-commit hook enforces gates
./scripts/workflow_gate.py record-commit                   # Record completion

# 3. Create PR and switch to PR phase
gh pr create --title "..." --body "..."                    # Create the PR
./scripts/workflow_gate.py start-pr-phase --pr-url <url>   # Switch to PR review phase

# 4. PR review cycle (different commands than component phase!)
./scripts/workflow_gate.py pr-check                        # Check PR status from GitHub
# → Fix issues from reviewers
make ci-local                                              # Run local CI
# → Request shared-context iteration reviews (see 03-reviews.md)
./scripts/workflow_gate.py record-review gemini approved --continuation-id <id>
./scripts/workflow_gate.py record-review codex approved --continuation-id <id>  # Same ID
git commit -m "fix: Address PR feedback"                   # Commit fixes
./scripts/workflow_gate.py pr-record-commit --hash $(git rev-parse HEAD)  # Push and record
./scripts/workflow_gate.py pr-check                        # Re-check until approved
```

### Key Principles

- **Analysis first:** Complete [Workflows/00-analysis-checklist.md](./Workflows/00-analysis-checklist.md) before coding (saves 3-11 hours)
- **Planning discipline:** Create task file, set component, follow 6-step pattern
- **TDD:** Write tests before implementation
- **6-step pattern:** Plan → Plan Review → Implement → Test → Code Review → Commit
- **Shared-context iterations:** First reviewer starts fresh → subsequent reviewers share continuation ID → fix issues → re-review with same ID → if fixes made, start new iteration fresh. Approval = all reviewers approve on first try of an iteration.
- **No bypasses:** NEVER use `git commit --no-verify` (detected by CI)

**See [Workflows/README.md](./Workflows/README.md) for detailed workflows**

---

## 📝 Coding Standards

**Language:** Python 3.11, type hints required, `mypy --strict` must pass

**Key patterns:**
- Pydantic for all config/models
- Structured logging (JSON) with `strategy_id`, `client_order_id`
- Never swallow exceptions
- Always UTC timezone-aware timestamps
- Parameterized queries only (no SQL injection)
- Migrations for schema changes (Alembic)
- Async FastAPI + httpx for HTTP

**See [/../STANDARDS/CODING_STANDARDS.md](../STANDARDS/CODING_STANDARDS.md) for detailed patterns**

---

## 🧪 Testing Strategy

### Test Pyramid
- **Unit:** Pure functions, features, allocators (many, fast)
- **Integration:** API endpoints, database workflows (some, medium)
- **E2E:** Full paper run, backtest replay (few, slow)

### Must-Haves
- Reproducible `paper_run` for any date (backtest parity)
- Circuit breaker tests (trip + recovery)
- Stale order cleanup test (>15m → cancel)
- Backtest replay test (same signals from saved data)

**See [/../STANDARDS/TESTING.md](../STANDARDS/TESTING.md) for requirements**

---

## 🚨 Environment Modes

- **DRY_RUN=true** (default): Logs orders, doesn't submit to broker
- **Paper Trading:** `DRY_RUN=false` + paper API (real API, fake money)
- **Live Trading:** Live API credentials (graduated rollout required)

---

## 🔒 Operational Guardrails

### Pre-Trade Checks
- Per-symbol position limits
- Total notional limits
- Blacklist enforcement
- Daily loss limits
- Circuit breaker state (MANDATORY)

### Post-Trade Monitoring
- Continuous drawdown calculation
- Realized volatility tracking
- Exposure monitoring
- Metrics to Prometheus

### Circuit Breakers
Trip on: drawdown breach, broker errors, data staleness (>30min)

**When TRIPPED:**
- Block new entries
- Allow risk-reducing exits only
- Recovery: conditions normalized + manual approval + quiet period

**See [/../RUNBOOKS/ops.md](../RUNBOOKS/ops.md) for recovery procedures**

---

## ⚠️ Anti-Patterns to Avoid

**Process Violations (workflow_gate.py enforces these):**
- ❌ No coding without analysis (saves 3-11 hours)
- ❌ NEVER use `git commit --no-verify` (bypasses gates, detected by CI)
- ❌ No skipping review gates (caused 7 fix commits, 10-15 hours wasted)
- ❌ No skipping local CI (2-4x faster than remote CI)
- ❌ NEVER create documents out side of `docs` folder, place them where is suitable

**Code Quality:**
- No duplicate feature logic (share research/production code)
- No pattern violations (match established retry/error/logging patterns)
- No call site ignorance (analyze ALL call sites before changing signatures)
- No architectural changes without ADR

---

## 🎓 Key Terminology

See [/../GETTING_STARTED/GLOSSARY.md](../GETTING_STARTED/GLOSSARY.md) for full definitions:

- **Alpha** — Model signal predicting returns
- **Circuit Breaker** — Safety mechanism (OPEN/TRIPPED)
- **Client Order ID** — Unique ID for idempotent submissions
- **Dry-Run** — Simulated orders (no broker submission)
- **Idempotent** — Safe to retry without side effects
- **Paper Trading** — Real APIs, simulated money
- **Reconciler** — Syncs DB vs broker state
- **TWAP** — Time-Weighted Average Price execution

---

## 📚 Additional Resources

**Project Context:**
- [/../GETTING_STARTED/PROJECT_STATUS.md](../GETTING_STARTED/PROJECT_STATUS.md) — Current implementation status
- [/../GETTING_STARTED/REPO_MAP.md](../GETTING_STARTED/REPO_MAP.md) — Directory structure and ownership
- [/../GETTING_STARTED/GLOSSARY.md](../GETTING_STARTED/GLOSSARY.md) — Trading and ML terminology

**Implementation:**
- [/../ARCHIVE/TASKS_HISTORY/P1_PLANNING_DONE.md](../ARCHIVE/TASKS_HISTORY/P1_PLANNING_DONE.md) — Phase 1 planning
- [/../ARCHIVE/TASKS_HISTORY/P2_PLANNING_DONE.md](../ARCHIVE/TASKS_HISTORY/P2_PLANNING_DONE.md) — Phase 2 planning
- [Prompts/](./Prompts/) — Reusable AI prompts

**Retrospectives:**
- [/../LESSONS_LEARNED/](../LESSONS_LEARNED/) — Post-implementation learnings

---

## 🆘 Getting Help

**Questions or need help?**
- **This document (AI_GUIDE.md) is your PRIMARY guidance** — review principles and mandatory steps here first
- Find specific workflows in [Workflows/README.md](./Workflows/README.md)
- Review [/../INDEX.md](../INDEX.md) for complete documentation index
- See [/../GETTING_STARTED/](../GETTING_STARTED/) for setup and orientation

---

**Last Updated:** 2025-12-31
**Maintained By:** Development Team
**Version:** 2.2 (Added Repomix integration for AI-optimized codebase analysis)
