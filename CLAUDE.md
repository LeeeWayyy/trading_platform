# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## 🤖 AUTO-RESUME: Check for Incomplete Work

**CRITICAL:** Before proceeding, check if there's incomplete work to resume:

```bash
# Check for incomplete tasks
if [ -f .claude/task-state.json ]; then
  TASK_STATE=$(jq -r '.current_task.state' .claude/task-state.json)
  if [ "$TASK_STATE" = "IN_PROGRESS" ]; then
    echo "🤖 INCOMPLETE TASK DETECTED"
    echo "📖 See .claude/AUTO_RESUME.md for automatic context restoration"
    echo "📋 Run: jq '.' .claude/task-state.json"
  fi
fi
```

**If incomplete work found:**
- Read [`.claude/AUTO_RESUME.md`](./.claude/AUTO_RESUME.md)
- Follow [`.claude/workflows/14-task-resume.md`](./.claude/workflows/14-task-resume.md)
- Load context from `.claude/task-state.json`
- Continue where previous session left off

**Otherwise:** Proceed with normal workflow below.

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

**New to the project?**
1. Read this file for overview
2. Follow [`.claude/workflows/11-environment-bootstrap.md`](./.claude/workflows/11-environment-bootstrap.md) to set up your environment
3. Review [`.claude/workflows/README.md`](./.claude/workflows/README.md) for development workflow guides

**Ready to code?**
1. **🔍 MANDATORY: Complete Pre-Implementation Analysis** (30-60 min)
   - Follow [`.claude/workflows/00-analysis-checklist.md`](./.claude/workflows/00-analysis-checklist.md)
   - Identify ALL impacted components, call sites, tests
   - Verify pattern parity and process compliance
   - Create comprehensive todo list with 4-step pattern
   - **⚠️ DO NOT write code before completing analysis**

2. **For task documents:** Request task creation review (see `.claude/workflows/13-task-creation-review.md`)

3. **Break feature into logical components** — Use 4-step pattern (see below)
   - For large tasks (>8h), decompose into subfeatures: [`.claude/workflows/00-task-breakdown.md`](./.claude/workflows/00-task-breakdown.md)

4. **For EACH component:**
   - Implement logic
   - Create test cases (TDD)
   - **🔒 MANDATORY: Request zen-mcp review** (NEVER skip): [`.claude/workflows/03-zen-review-quick.md`](./.claude/workflows/03-zen-review-quick.md)
   - **🔒 MANDATORY: Run `make ci-local`** (NEVER skip)
   - Commit ONLY after review approval + CI passes: [`.claude/workflows/01-git-commit.md`](./.claude/workflows/01-git-commit.md)

5. Repeat until feature complete

6. **🔍 MANDATORY: Deep review** via clink + gemini: [`.claude/workflows/04-zen-review-deep.md`](./.claude/workflows/04-zen-review-deep.md)

7. Create PR: [`.claude/workflows/02-git-pr.md`](./.claude/workflows/02-git-pr.md)

---

## 📖 Essential Documentation

**📂 Workflow Guides (step-by-step procedures):**
- This document (CLAUDE.md) is your **PRIMARY guidance** — start here for principles and process
- [`.claude/workflows/README.md`](./.claude/workflows/README.md) — **Workflow Index** (quick reference to find specific workflows)

**📖 First Time? Documentation Index:**
1. `/docs/INDEX.md` — Canonical entry point with navigation guide
2. `/docs/AI_GUIDE.md` — Quick-start for AI assistants

**⚠️ Standards (MUST follow):**
1. `/docs/STANDARDS/CODING_STANDARDS.md` — Python patterns and standards
2. `/docs/STANDARDS/DOCUMENTATION_STANDARDS.md` — Docstring requirements
3. `/docs/STANDARDS/GIT_WORKFLOW.md` — Commit messages and PR policies
4. `/docs/STANDARDS/TESTING.md` — Test pyramid and requirements
5. `/docs/STANDARDS/ADR_GUIDE.md` — Architecture Decision Records (MANDATORY for arch changes)

**🔧 Implementation References:**
- `/docs/API/*.openapi.yaml` — API contracts (strict, require ADR to change)
- `/docs/DB/*.sql` — Database schemas (strict)
- `/docs/TASKS/*.md` — Task tracking and implementation guides
- `/docs/ADRs/*.md` — All architectural decisions
- `/docs/CONCEPTS/*.md` — Trading concepts explained for beginners

---

## 🤖 Zen-MCP + Clink Integration

**⚠️ MANDATORY: ALL zen-mcp interactions MUST use `mcp__zen__clink` exclusively.**

**Correct Usage:**
```python
mcp__zen__clink(
    prompt="Review this implementation",
    cli_name="codex",  # or "gemini"
    role="codereviewer"
)
```

**WHY:** Direct zen-mcp tools bypass CLI authentication and cause API permission errors.

**See:** `.claude/workflows/03-zen-review-quick.md` for review workflow details

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

### Context Management (Phase 3: Checkpointing System)
```bash
# Create checkpoint before delegation or session end
./scripts/context_checkpoint.py create --type delegation    # Before using Task tool
./scripts/context_checkpoint.py create --type session_end   # Before ending session

# Restore context from checkpoint
./scripts/context_checkpoint.py restore --id <checkpoint_id>

# List available checkpoints
./scripts/context_checkpoint.py list                        # All checkpoints
./scripts/context_checkpoint.py list --type delegation      # Only delegation checkpoints

# Clean up old checkpoints (auto-deletes >7 days, keeps last 10 per type)
./scripts/context_checkpoint.py cleanup --older-than 7d
./scripts/context_checkpoint.py cleanup --older-than 14d --keep-latest 20
```

**When to use:**
- Before Task tool delegation (see `.claude/workflows/16-subagent-delegation.md`)
- Before ending long coding sessions (auto-resume workflow)
- When context loss risk is high (complex multi-step work)
- See `.claude/checkpoints/README.md` for complete documentation

### Context-Aware Workflow Automation (Component 3: Context Monitoring)
```bash
# Check current context usage status
./scripts/workflow_gate.py check-context

# Record current token usage manually
./scripts/workflow_gate.py record-context <tokens>

# Get delegation recommendations if thresholds exceeded
./scripts/workflow_gate.py suggest-delegation

# Record subagent delegation (resets context to 0)
./scripts/workflow_gate.py record-delegation "<task_description>"
```

**Context thresholds:**
- **< 70%:** ✅ OK - Continue normal workflow
- **70-84%:** ⚠️ WARNING - Delegation RECOMMENDED
- **≥ 85%:** 🚨 CRITICAL - Delegation MANDATORY

**When to use:**
- Check context at workflow transitions (implement → test → review → commit)
- Delegate non-core tasks when context ≥ 70% (see `.claude/workflows/16-subagent-delegation.md`)
- Mandatory delegation at 85% threshold to prevent mid-task interruptions
- Context automatically resets after delegation and after commit
- See `.claude/workflows/component-cycle.md#context-aware-workflow-pattern-component-3` for integration with 4-step pattern


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

**Idempotency (prevents duplicate orders):**
```python
client_order_id = hash(symbol + side + qty + price + strategy + date)[:24]
```

**Circuit Breaker Check (MANDATORY before every order):**
```python
if redis.get("cb:state") == b"TRIPPED":
    raise CircuitBreakerTripped()
```

**Risk Check (MANDATORY before every order):**
```python
if abs(current_pos + order.qty) > limits.max_pos_per_symbol:
    raise RiskViolation()
```

**Feature Parity (shared code for research/production):**
```python
# strategies/alpha_baseline/features.py
def compute_features(df: pl.DataFrame) -> pl.DataFrame:
    """Shared by offline research AND online signal service."""
    # Never duplicate this logic
```

### Reconciliation
Boot-time and periodic reconciliation:
1. Pull broker positions/orders
2. Diff against DB
3. Heal discrepancies (cancel stale >15m, adjust positions)
4. Alert on failures
5. Unlock after successful reconciliation

---

## 🤖 Zen-MCP Reviews

All reviews automated via `workflow_gate.py`:

```bash
# Pre-commit review (Tier 1: gemini → codex, 2-3 min)
./scripts/workflow_gate.py request-review commit

# Pre-PR review (Tier 2: deep multi-iteration, 3-5 min)
./scripts/workflow_gate.py request-review pr
```

**Models:**
- Codex CLI: `gpt-5-codex` (400K context, ~30s reviews)
- Gemini CLI: `gemini-2.5-pro` (1M context, planning)

**Cost:** $320-370/month (subscription-based, unlimited reviews)

**See:** `.claude/workflows/03-zen-review-quick.md` for review details

---

## 🎯 Development Process

Use `workflow_gate.py` for all workflow operations. It enforces gates, manages context, and automates reviews.

### Quick Start

```bash
# 1. Plan & start task
./scripts/workflow_gate.py create-task --id P1T14 --title "..." --hours 6
./scripts/workflow_gate.py start-task P1T14

# 2. For each component (4-step pattern auto-enforced)
./scripts/workflow_gate.py set-component "Component Name"
# → Implement + test (TDD)
./scripts/workflow_gate.py advance test
./scripts/workflow_gate.py advance review
./scripts/workflow_gate.py request-review commit  # Auto-delegates review if context ≥70%
./scripts/workflow_gate.py run-ci commit          # Smart test selection
git commit -m "message"                          # Pre-commit hook enforces gates

# 3. Before PR
./scripts/workflow_gate.py request-review pr  # Multi-iteration deep review
```

### Key Principles

- **Analysis first:** Complete `.claude/workflows/00-analysis-checklist.md` before coding (saves 3-11 hours)
- **TDD:** Write tests before implementation
- **4-step pattern:** Implement → Test → Review → CI → Commit (enforced by workflow_gate)
- **Context monitoring:** Auto-delegation at 70%+ context usage
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
