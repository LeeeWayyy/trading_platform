# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **Qlib + Alpaca trading platform** designed for algorithmic trading. The system produces signals using Qlib-based models and executes trades via Alpaca's API with emphasis on safety, idempotency, and parity between research and production.

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
1. **For task documents:** Request task creation review (see `.claude/workflows/13-task-creation-review.md`)
2. **Break feature into logical components** — Use 4-step pattern (see below)
3. **For EACH component:**
   - Implement logic
   - Create test cases (TDD)
   - Request quick review via clink + codex: [`.claude/workflows/03-zen-review-quick.md`](./.claude/workflows/03-zen-review-quick.md)
   - Commit: [`.claude/workflows/01-git-commit.md`](./.claude/workflows/01-git-commit.md)
4. Repeat until feature complete
5. Deep review via clink + gemini: [`.claude/workflows/04-zen-review-deep.md`](./.claude/workflows/04-zen-review-deep.md)
6. Create PR: [`.claude/workflows/02-git-pr.md`](./.claude/workflows/02-git-pr.md)

---

## 📖 Essential Documentation

**📂 Workflow Guides (step-by-step procedures):**
- [`.claude/workflows/README.md`](./.claude/workflows/README.md) — **Start here for all workflows**

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

### Workflows (see .claude/workflows/ for detailed guides)
- **Git commit:** `.claude/workflows/01-git-commit.md`
- **Create PR:** `.claude/workflows/02-git-pr.md`
- **Quick review (clink + codex):** `.claude/workflows/03-zen-review-quick.md`
- **Deep review (clink + gemini):** `.claude/workflows/04-zen-review-deep.md`
- **Run tests:** `.claude/workflows/05-testing.md`
- **Debug issues:** `.claude/workflows/06-debugging.md`
- **Write docs:** `.claude/workflows/07-documentation.md`
- **Create ADR:** `.claude/workflows/08-adr-creation.md`
- **Deploy/rollback:** `.claude/workflows/09-deployment-rollback.md`
- **Fix CI:** `.claude/workflows/10-ci-triage.md`
- **Bootstrap env:** `.claude/workflows/11-environment-bootstrap.md`
- **Task creation review (clink + gemini):** `.claude/workflows/13-task-creation-review.md`

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

## 🤖 Zen-MCP + Clink Integration

This project uses **zen-mcp** (Model Context Protocol server) with **clink** to orchestrate AI-assisted code reviews via authenticated CLI tools. All zen-mcp workflows use **clink exclusively** — no direct API calls.

### Model Selection Strategy

**Codex CLI (gpt-5-codex):**
- **When:** Quick safety reviews, pre-commit validation, implementation quality checks
- **Model:** Always uses `gpt-5-codex` (400K context, code-specialized)
- **Configuration:** Happens in codex CLI, NOT clink parameters
- **Duration:** ~30 seconds per review

**Gemini CLI (gemini-2.5-pro/flash):**
- **When:** Deep architecture reviews, task creation planning, comprehensive analysis
- **Models:** `gemini-2.5-pro` (1M context, planning) or `gemini-2.5-flash` (fast, efficient)
- **Configuration:** Happens in gemini CLI, NOT clink parameters
- **Duration:** 3-5 minutes for deep reviews, 2-3 minutes for task reviews

### Three-Tier Review System

**Tier 1: Quick Review (Pre-Commit)**
- **Tool:** clink + codex codereviewer
- **Purpose:** Safety check before every commit (~30 sec)
- **Example:**
  ```bash
  # Use clink with codex codereviewer role
  # Codex automatically uses gpt-5-codex (configured in CLI)
  # Review staged changes for trading safety, idempotency, test coverage
  ```
- **See:** `.claude/workflows/03-zen-review-quick.md`

**Tier 2: Deep Review (Pre-PR)**
- **Tool:** clink + gemini codereviewer → codex codereviewer
- **Purpose:** Comprehensive branch review (3-5 min)
- **Example:**
  ```bash
  # Phase 1: Use clink with gemini codereviewer
  # Gemini provides architecture analysis with continuation_id

  # Phase 2: Use clink with codex codereviewer (reuse continuation_id)
  # Codex synthesizes recommendations and next steps
  ```
- **See:** `.claude/workflows/04-zen-review-deep.md`

**Tier 3: Task Creation Review (Pre-Work)**
- **Tool:** clink + gemini planner
- **Purpose:** Validate task documents before starting work (2-3 min)
- **Example:**
  ```bash
  # Use clink with gemini planner role
  # Reviews task scope, requirements completeness, acceptance criteria
  # Prevents scope creep and unclear requirements
  ```
- **See:** `.claude/workflows/13-task-creation-review.md`

### Cost Model (Subscription-Based)

**Monthly costs:**
- Codex CLI subscription: $20-50/month (fixed)
- Gemini CLI tier: Free or $20/month (fixed)
- Maintenance hours: ~$300 (3 hours × $100/hr for ongoing maintenance)
- **Total: $320-370/month** (predictable, no per-token charges)
- Min scenario: $20 + $0 + $300 = $320
- Max scenario: $50 + $20 + $300 = $370

**Benefits over direct API calls:**
- Predictable budgeting (no usage spikes)
- Unlimited reviews within subscription
- Authenticated CLI tools (secure)
- 691% ROI vs pay-per-use API model ($468/month)

**Key Points:**
- All reviews use **clink** — no direct zen tools or API calls
- Model selection via **CLI configuration**, NOT clink parameters
- `continuation_id` preserves context across multi-turn conversations (up to 49 exchanges)
- Workflow reminders embedded in every review response to prevent forgetting established patterns

---

## 🎯 Development Process

**See [`.claude/workflows/README.md`](./.claude/workflows/README.md) for complete workflow guides.**

### ⚠️ MANDATORY: 4-Step Pattern for Each Logical Component

**CRITICAL:** To prevent skipping testing or review steps, EVERY logical component implementation MUST follow this pattern:

1. **Implement** the logic component
2. **Create test cases** for comprehensive coverage (TDD)
3. **Request quick review** via clink + codex codereviewer (see Tier 1 above)
4. **Commit** changes after review approval

**Example:** When implementing "position limit validation", create these 4 todo tasks:
```markdown
- [ ] Implement position limit validation logic
- [ ] Create test cases for position limit validation
- [ ] Request quick review (clink + codex) for position limit validation
- [ ] Commit position limit validation
```

**Never skip or combine steps!** See [`.claude/workflows/01-git-commit.md`](./.claude/workflows/01-git-commit.md) for detailed guidance and examples.

### Quick Reference

1. **Pre-Implementation**
   - Read ticket in `/docs/TASKS/`
   - Create ADR if architectural change (see `.claude/workflows/08-adr-creation.md`)
   - Document trading concepts in `/docs/CONCEPTS/` if needed
   - **Break feature into logical components** for 4-step pattern

2. **Write Tests First** (TDD)
   - Follow `/docs/STANDARDS/TESTING.md`
   - See `.claude/workflows/05-testing.md`

3. **Implement Code**
   - **Use 4-step pattern for EACH logical component** (see above)
   - Follow `/docs/STANDARDS/CODING_STANDARDS.md`
   - Add comprehensive docstrings (see `/docs/STANDARDS/DOCUMENTATION_STANDARDS.md`)
   - See `.claude/workflows/07-documentation.md`

4. **Progressive Commits (every 30-60 min per component)**
   - **MANDATORY quick review (clink + codex) before each commit**
   - See `.claude/workflows/03-zen-review-quick.md` (Tier 1 quick review)
   - See `.claude/workflows/01-git-commit.md` (commit workflow with 4-step pattern)

5. **Before PR**
   - **MANDATORY deep review (clink + gemini) of all branch changes**
   - See `.claude/workflows/04-zen-review-deep.md` (Tier 2 deep review)
   - See `.claude/workflows/02-git-pr.md`

6. **If Issues Occur**
   - Debugging: `.claude/workflows/06-debugging.md`
   - CI failures: `.claude/workflows/10-ci-triage.md`
   - Deployment issues: `.claude/workflows/09-deployment-rollback.md`

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

- **No skipping the 4-step pattern** — MANDATORY: Implement → Test → Review → Commit (`.claude/workflows/01-git-commit.md`)
- **No committing without quick review** — MANDATORY clink + codex quality gate (`.claude/workflows/03-zen-review-quick.md`)
- **No committing without passing tests** — Run `make test && make lint` before every commit
- **No combining logical components in one commit** — Use 4-step pattern for each component separately
- **No PRs without deep review** — MANDATORY clink + gemini comprehensive review (`.claude/workflows/04-zen-review-deep.md`)
- **No starting work without task review** — Use clink + gemini planner to validate task documents (`.claude/workflows/13-task-creation-review.md`)
- **No duplicate feature logic** — Share code between research/production
- **No in-memory state** — Use DB for positions/orders/breakers
- **No silent failures** — Always log and raise with context
- **No scattered configs** — Centralize in Pydantic Settings
- **No unvalidated data** — Quality gate before storage
- **No untested order paths** — Require backtest replay parity
- **No live without paper** — Paper validation required first
- **No architectural changes without ADR** — MANDATORY (`.claude/workflows/08-adr-creation.md`)

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

## 🆘 When Making Changes

**Quick checklist:**
1. Check existing docs (`/docs/GETTING_STARTED/REPO_MAP.md`, API specs, DB schemas)
2. **For task documents:** Request task creation review via clink + gemini (`.claude/workflows/13-task-creation-review.md`)
3. Create ADR for architectural changes (`.claude/workflows/08-adr-creation.md`)
4. Document trading concepts in `/docs/CONCEPTS/` (`.claude/workflows/07-documentation.md`)
5. **Break feature into logical components** — Use 4-step pattern per component:
   - Implement logic
   - Create test cases (TDD)
   - Request quick review via clink + codex (`.claude/workflows/03-zen-review-quick.md`)
   - Commit after approval
6. Add comprehensive docstrings (see `/docs/STANDARDS/DOCUMENTATION_STANDARDS.md`)
7. Run `make test && make lint` before every commit
8. **Never skip the 4-step pattern** (see `.claude/workflows/01-git-commit.md`)
9. Update affected docs
10. Before PR: deep review via clink + gemini (`.claude/workflows/04-zen-review-deep.md`)

**See [`.claude/workflows/README.md`](./.claude/workflows/README.md) for detailed workflows.**

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
- Check [`.claude/workflows/README.md`](./.claude/workflows/README.md) for workflow guides
- Review `/docs/INDEX.md` for complete documentation index
- See `/docs/GETTING_STARTED/` for setup and orientation
