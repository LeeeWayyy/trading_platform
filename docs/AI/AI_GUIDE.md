# AI Assistant Development Guide

**Comprehensive guidance for all AI coding assistants working with this repository.**

This guide provides complete instructions for Claude Code, Codex, Gemini, and other AI assistants. It consolidates all AI-related guidance into a single source of truth.

---

## ⚠️ CRITICAL: Review Override Policy

**NEVER use `ZEN_REVIEW_OVERRIDE` without explicit human approval.**

- Skipping reviews caused 7 fix commits and 10-15 hours of wasted time in past incidents
- `ZEN_REVIEW_OVERRIDE` is ONLY for genuine emergencies (server outages, critical hotfixes)
- If clink is unavailable, ask the user: "Wait for server, or use override (requires your approval)?"
- Include user's name in override commit message: `User approved by: [name]`
- NEVER use `git commit --no-verify` (detected by CI)

---

## ⚠️ CRITICAL: CI-Local Single Instance Rule

**NEVER run multiple `make ci-local` instances simultaneously.**

1. **ONLY ONE** `make ci-local` at a time — the Makefile has a lock mechanism
2. Wait for the current CI to complete before starting a new one
3. If CI needs restart: `pkill -f "make ci-local" && rm -f .ci-local.lock`

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

## 🚀 Quick Start — Analyze → Build → Ship

1. **Start:** Create a feature branch, read the task file
   ```bash
   git checkout -b feature/<task-id>-description
   ```

2. **Analyze (recommended for non-trivial changes):** `/analyze <task-file>`
   Discovers impacted files, tests, and patterns before you code.
   Skip only for docs-only changes.

3. **Build:** Implement + write tests (TDD preferred)

4. **Review:** `/review`
   Each invocation handles one full iteration (review + fix + re-review until approved).
   If fixes were made, run `/review` again for a fresh iteration.
   ```
   /review     → fix + re-review within iteration → approved (fixes made)
   /review     → zero issues on first try → APPROVED, ready to commit
   ```

5. **Commit:** `git commit` with conventional format + zen trailers
   ```
   feat(scope): description

   zen-mcp-review: approved
   continuation-id: <uuid>
   ```

6. **Repeat** steps 2-5 for each component.

7. **CI + PR:** Run `make ci-local`, then create PR
   ```bash
   make ci-local
   gh pr create --title "..." --body "..."
   ```

8. **PR Feedback:** `/pr-fix` — collects all review comments and batch-fixes them
   After fixing, run `make ci-local` again before pushing.

---

## 📖 Essential Documentation

| Category | Key Files |
|----------|-----------|
| **Standards** | [/../STANDARDS/](../STANDARDS/) (CODING, DOCUMENTATION, GIT_WORKFLOW, TESTING, ADR_GUIDE) |
| **Implementation** | [/../TASKS/](../TASKS/) (tickets), [/../ADRs/](../ADRs/) (architecture decisions), [/../CONCEPTS/](../CONCEPTS/) (trading glossary) |
| **Navigation** | [/../INDEX.md](../INDEX.md) (canonical index), this file (AI quick-start) |

---

## 🤖 AI Agent Roles

### Claude Code (Main Development Agent)
- **Primary role:** Writes, refactors, and implements code
- **Tools:** Full codebase access, can run commands, create files
- **Skills:** `/review`, `/pr-fix`, `/analyze`

### Codex (Code Reviewer via clink)
- **Primary role:** Comprehensive code review
- **Access:** Via `mcp__pal__clink` with `cli_name='codex'` and `role='codereviewer'`

### Gemini (Code Reviewer via clink)
- **Primary role:** Comprehensive code review
- **Access:** Via `mcp__pal__clink` with `cli_name='gemini'` and `role='codereviewer'`

---

## 🔍 Code Review

**Default reviewers:** Gemini + Codex (via clink shared-context)
**Skill:** `/review` — runs one full iteration (review + fix + re-review until all approve)
**Override:** `/review --reviewer gemini` or `/review --reviewer codex` for single reviewer

How it works:
1. Run `/review` — one iteration: sends to reviewers, fixes issues, re-reviews with same continuation_id until all reviewers approve
2. If fixes were made → run `/review` again (fresh iteration, new continuation_id)
3. Repeat until reviewers approve with zero issues on first try
4. Include the final continuation_id in your commit message

**Fix ALL issues** — even LOW severity. No deferral, no "cleanup later".

---

## 🛠️ Skills Reference

| When | Skill | What it does |
|------|-------|-------------|
| Before coding a non-trivial feature | `/analyze <task-file>` | Parallel subagent analysis of impacted files, tests, patterns |
| Before committing code changes | `/review` | Shared-context review iteration (Gemini + Codex) |
| Single reviewer only | `/review --reviewer gemini` or `codex` | Review with one reviewer |
| PR has review comments or CI failures | `/pr-fix` or `/pr-fix <number>` | Collect + batch-fix all PR feedback |
| Everything else | Standard CLI | `git`, `make ci-local`, `gh pr create` |

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

Context monitoring is manual — check token usage in IDE status bar. For complex tasks, use subagents (Task tool) to keep main context clean.

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

### Key Principles

- **Analyze before coding:** Run `/analyze <task-file>` for non-trivial changes
- **TDD:** Write tests before implementation
- **Review before commit:** Run `/review` until approved with zero issues
- **CI before PR:** Run `make ci-local` before creating PR and after PR fixes
- **No bypasses:** NEVER use `git commit --no-verify` (detected by CI)
- **Conventional commits:** `feat(scope):`, `fix(scope):`, `docs:`, etc.
- **Architectural changes require an ADR** (see `/../ADRs/`)

### Commit Message Format

```
feat(scope): description

zen-mcp-review: approved
continuation-id: <uuid-from-final-clean-review>
```

Docs-only commits (no `.py/.sh/.js/.ts/.yml/.yaml/.toml/.cfg/.ini`, `Makefile`, `Dockerfile*`, or `.claude/skills/*.md`/`.claude/commands/*.md` files changed) can skip zen trailers:
```
docs: update README
```

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

**Process:**
- ❌ No coding without analysis for non-trivial changes (run `/analyze` first)
- ❌ NEVER use `git commit --no-verify` (detected by CI)
- ❌ No skipping `/review` before commit (caused 7 fix commits, 10-15 hours wasted)
- ❌ No skipping `make ci-local` before PR creation or after PR fixes
- ❌ NEVER create documents outside of `docs` folder

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

- [/../GETTING_STARTED/PROJECT_STATUS.md](../GETTING_STARTED/PROJECT_STATUS.md) — Current implementation status
- [/../GETTING_STARTED/REPO_MAP.md](../GETTING_STARTED/REPO_MAP.md) — Directory structure and ownership
- [/../GETTING_STARTED/GLOSSARY.md](../GETTING_STARTED/GLOSSARY.md) — Trading and ML terminology
- [/../INDEX.md](../INDEX.md) — Canonical documentation index
- [/../STANDARDS/](../STANDARDS/) — Coding, documentation, testing standards
- [/../LESSONS_LEARNED/](../LESSONS_LEARNED/) — Post-implementation learnings

---

**Last Updated:** 2026-02-26
**Maintained By:** Development Team
**Version:** 3.0
