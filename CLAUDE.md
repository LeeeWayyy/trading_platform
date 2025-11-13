# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## 🤖 AUTO-RESUME: Check for Incomplete Work

**CRITICAL:** Check `.claude/task-state.json` for incomplete work before starting. If found, follow [`.claude/AUTO_RESUME.md`](./.claude/AUTO_RESUME.md) and [`.claude/workflows/08-session-management.md`](./.claude/workflows/08-session-management.md) to resume.

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
- **Every architectural change requires an ADR** (see `/docs/STANDARDS/ADR_GUIDE.md`)
- **All code must be educational and well-documented for learning** (see `/docs/STANDARDS/DOCUMENTATION_STANDARDS.md`)

---

## 🚀 Quick Start

**New to the project?** Read this file, then see [`.claude/workflows/README.md`](./.claude/workflows/README.md) for setup and workflows.

**Ready to code?**
1. **🔍 MANDATORY: Complete Pre-Implementation Analysis** (30-60 min)
   - Follow [`.claude/workflows/00-analysis-checklist.md`](./.claude/workflows/00-analysis-checklist.md)
   - Identify ALL impacted components, call sites, tests
   - Verify pattern parity and process compliance
   - Create comprehensive todo list with 5-step pattern (plan → implement → test → review → commit)
   - **⚠️ DO NOT write code before completing analysis**

2. **For task documents:** Request task creation review (see `.claude/workflows/02-planning.md`)

3. **Record planning artifacts** (Phase 1: enforced by hard gates)
   - Record analysis completion: `./scripts/workflow_gate.py record-analysis-complete`
   - Set component breakdown (≥2): `./scripts/workflow_gate.py set-components "Name 1" "Name 2" ...`

4. **Break feature into logical components** — Use 5-step pattern (see below)
   - For large tasks (>8h), decompose into subfeatures: [`.claude/workflows/02-planning.md`](./.claude/workflows/02-planning.md)

5. **For EACH component:**
   - Transition to implement: `./scripts/workflow_gate.py advance implement`
   - Implement logic + create test cases (TDD)
   - **🔒 MANDATORY: Request zen-mcp review** (NEVER skip): [`.claude/workflows/03-reviews.md`](./.claude/workflows/03-reviews.md)
   - **🔒 MANDATORY: Run `make ci-local`** (NEVER skip)
   - Commit ONLY after review approval + CI passes: [`.claude/workflows/01-git.md`](./.claude/workflows/01-git.md)

6. Repeat until feature complete

7. **🔍 MANDATORY: Deep review** via clink + gemini: [`.claude/workflows/03-reviews.md`](./.claude/workflows/03-reviews.md)

8. Create PR: [`.claude/workflows/01-git.md`](./.claude/workflows/01-git.md)

---

## 📖 Essential Documentation

| Category | Key Files |
|----------|-----------|
| **Workflows** | `.claude/workflows/README.md` (index), `00-analysis-checklist.md`, `12-component-cycle.md`, `03-reviews.md`, `01-git.md` |
| **Standards** | `/docs/STANDARDS/` (CODING, DOCUMENTATION, GIT_WORKFLOW, TESTING, ADR_GUIDE) |
| **Implementation** | `/docs/TASKS/` (tickets), `/docs/ADRs/` (architecture decisions), `/docs/CONCEPTS/` (trading glossary) |
| **Schemas** | `/docs/API/*.openapi.yaml` (API contracts), `/docs/DB/*.sql` (database schemas) |
| **Navigation** | `/docs/INDEX.md` (canonical index), `/docs/AI_GUIDE.md` (AI quick-start) |

---

## 🤖 Zen-MCP + Clink Integration

**⚠️ MANDATORY: ALL zen-mcp interactions MUST use `mcp__zen__clink` exclusively** (direct zen-mcp tools bypass CLI authentication). See `.claude/workflows/03-reviews.md` for review workflow and usage examples.

---

## 📁 Repository Structure

- `apps/` — Microservices (FastAPI): signal_service, execution_gateway, reconciler, risk_manager, cli
- `strategies/` — Strategy implementations (alpha_baseline)
- `infra/` — Infrastructure configs (docker-compose, prometheus, grafana)
- `db/` — Database migrations (Alembic)
- `libs/` — Shared libraries (feature_store, common)
- `docs/` — Documentation (STANDARDS, GETTING_STARTED, API, ADRs, CONCEPTS, TASKS)
- `.claude/` — Claude Code configuration (commands, workflows)
- `tests/` — Test suite
- `data/` — Parquet files, DuckDB catalogs
- `artifacts/` — Models, backtests, reports

See `/docs/GETTING_STARTED/REPO_MAP.md` for detailed structure.

---

## ⚙️ Common Commands

### Development
```bash
make up          # Start infrastructure (Postgres, Redis, Grafana, Prometheus)
make down        # Stop infrastructure
make fmt         # Format code (black + ruff)
make lint        # Run linters (mypy, ruff)
make test        # Run test suite
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
./scripts/workflow_gate.py check-context         # Check current usage
./scripts/workflow_gate.py suggest-delegation    # Get recommendations
./scripts/workflow_gate.py record-delegation "..." # Record delegation (resets context)
```

**Thresholds:** <70% OK | 70-84% delegation recommended | ≥85% delegation mandatory. See `.claude/workflows/16-subagent-delegation.md` and `.claude/checkpoints/README.md`.


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

## 🤖 Zen-MCP Reviews

```bash
./scripts/workflow_gate.py request-review commit  # Pre-commit (Tier 1: gemini → codex, 2-3 min)
./scripts/workflow_gate.py request-review pr      # Pre-PR (Tier 2: deep review, 3-5 min)
```

**Models:** Codex CLI (gpt-5-codex), Gemini CLI (gemini-2.5-pro) | **Cost:** $320-370/month | **See:** `.claude/workflows/03-reviews.md`

---

## 🎯 Development Process

Use `workflow_gate.py` for all workflow operations. It enforces gates, manages context, and automates reviews.

### Quick Start

```bash
# 1. Plan & start task
./scripts/workflow_gate.py create-task --id P1T14 --title "Feature Title" --description "Detailed description of task" --hours 6
./scripts/workflow_gate.py start-task P1T14 feature/P1T14-task-branch

# 2. Complete planning steps (Phase 1: enforced by hard gates)
./scripts/workflow_gate.py record-analysis-complete  # After completing analysis checklist
./scripts/workflow_gate.py set-components "Component 1" "Component 2" ...  # ≥2 components required

# 3. For each component (5-step pattern: plan → implement → test → review → commit)
./scripts/workflow_gate.py set-component "Component Name"
./scripts/workflow_gate.py advance implement  # Phase 1: explicit transition from "plan" to "implement"
# → Implement + test (TDD)
./scripts/workflow_gate.py advance test
./scripts/workflow_gate.py advance review
./scripts/workflow_gate.py request-review commit  # Auto-delegates review if context ≥70%
./scripts/workflow_gate.py run-ci commit          # Smart test selection
git commit -m "message"                          # Pre-commit hook enforces gates
# → Post-commit hook automatically resets state to "implement" for next component

# 4. Before PR
./scripts/workflow_gate.py request-review pr  # Multi-iteration deep review
```

### Key Principles

- **Analysis first:** Complete `.claude/workflows/00-analysis-checklist.md` before coding (saves 3-11 hours)
- **Planning discipline (Phase 1):** Hard gates enforce task file, analysis completion, and component breakdown before first commit
- **TDD:** Write tests before implementation
- **5-step pattern:** Plan → Implement → Test → Review → Commit (enforced by workflow_gate)
- **Context monitoring:** Auto-delegation at 70%+ context usage (≥85% blocks commits)
- **No bypasses:** NEVER use `git commit --no-verify` (detected by CI)

**See:** [`.claude/workflows/README.md`](./.claude/workflows/README.md) for detailed workflows

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

**See `/docs/STANDARDS/CODING_STANDARDS.md` for detailed patterns**

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

**See `/docs/STANDARDS/TESTING.md` for requirements**
**See `.claude/workflows/05-testing.md` for running tests**

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

**See `/docs/RUNBOOKS/ops.md` for recovery procedures**

---

## ⚠️ Anti-Patterns to Avoid

**Process Violations (workflow_gate.py enforces these):**
- ❌ No coding without analysis (saves 3-11 hours)
- ❌ NEVER use `git commit --no-verify` (bypasses gates, detected by CI)
- ❌ No skipping review gates (caused 7 fix commits, 10-15 hours wasted)
- ❌ No skipping local CI (2-4x faster than remote CI)

**Code Quality:**
- No duplicate feature logic (share research/production code)
- No pattern violations (match established retry/error/logging patterns)
- No call site ignorance (analyze ALL call sites before changing signatures)
- No architectural changes without ADR

**See:** `/tmp/ci-failure-root-cause-analysis.md` for root cause details

---

## 🎓 Key Terminology

See `/docs/GETTING_STARTED/GLOSSARY.md` for full definitions:

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
- `/docs/GETTING_STARTED/PROJECT_STATUS.md` — Current implementation status
- `/docs/GETTING_STARTED/REPO_MAP.md` — Directory structure and ownership
- `/docs/GETTING_STARTED/GLOSSARY.md` — Trading and ML terminology

**Implementation:**
- `/docs/trading_platform_realization_plan.md` — Overall project plan
- `/docs/TASKS/P0_TICKETS.md` — Detailed ticket backlog
- `prompts/assistant_rules.md` — AI assistant guidance

**APIs & Schemas:**
- `/docs/API/execution_gateway.openapi.yaml` — Execution Gateway API
- `/docs/DB/*.sql` — Database schemas

**Retrospectives:**
- `/docs/LESSONS_LEARNED/*.md` — Post-implementation learnings

---

**Questions or need help?**
- **This document (CLAUDE.md) is your PRIMARY guidance** — review principles and mandatory steps here first
- Find specific workflows in [`.claude/workflows/README.md`](./.claude/workflows/README.md) (workflow index)
- Review `/docs/INDEX.md` for complete documentation index
- See `/docs/GETTING_STARTED/` for setup and orientation
