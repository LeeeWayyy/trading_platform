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

This project uses **zen-mcp** (Model Context Protocol server) with **clink** to orchestrate AI-assisted code reviews via authenticated CLI tools.

### 🚨 CRITICAL: Clink-Only Tool Usage Policy

**⚠️ MANDATORY: ALL zen-mcp interactions MUST use `mcp__zen__clink` exclusively.**

**WHY THIS MATTERS:**
- MCP server configuration is **system-level** (not project-level)
- Direct zen tools (chat, thinkdeep, debug, etc.) bypass CLI authentication
- Using wrong tools causes **API permission errors** and breaks workflows
- Cost model depends on CLI subscriptions, not direct API usage

**✅ CORRECT Tool Usage:**
```python
# Use clink with appropriate CLI and role
mcp__zen__clink(
    prompt="Review this implementation for trading safety",
    cli_name="codex",  # or "gemini"
    role="codereviewer"  # or "planner" or "default"
)
```

**❌ INCORRECT Tool Usage (NEVER DO THIS):**
```python
# ❌ WRONG: Direct zen-mcp tools bypass CLI authentication
mcp__zen-mcp__chat(...)           # API permission error
mcp__zen-mcp__thinkdeep(...)      # API permission error
mcp__zen-mcp__codereview(...)     # API permission error
mcp__zen-mcp__debug(...)          # API permission error
mcp__zen-mcp__consensus(...)      # API permission error
mcp__zen-mcp__planner(...)        # API permission error
```

**Technical Limitation:**
Tool restriction is **not enforceable at project level** because MCP config is system-level (`~/.claude/config/`). This policy relies on **documentation + workflow discipline** rather than technical gates.

**If you catch yourself using direct zen-mcp tools:**
1. STOP immediately
2. Use `mcp__zen__clink` instead with appropriate cli_name and role
3. Check `.claude/workflows/03-zen-review-quick.md` for correct patterns
4. See `.claude/TROUBLESHOOTING.md` for detailed error resolution

**See also:**
- [Quick Review Workflow](/.claude/workflows/03-zen-review-quick.md) - Clink usage examples
- [Troubleshooting Guide](/.claude/TROUBLESHOOTING.md) - Wrong-tool error fixes

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

### Workflows (see .claude/workflows/ for detailed guides)
- **🔍 Pre-Implementation Analysis (MANDATORY):** `.claude/workflows/00-analysis-checklist.md`
- **Task breakdown (for complex tasks >8h):** `.claude/workflows/00-task-breakdown.md`
- **Component cycle (4-step pattern):** `.claude/workflows/component-cycle.md`
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
- **Phase management:** `.claude/workflows/12-phase-management.md`
- **Task creation review (clink + gemini):** `.claude/workflows/13-task-creation-review.md`
- **🤖 Auto-resume tasks:** `.claude/workflows/14-task-resume.md`
- **Update task state:** `.claude/workflows/15-update-task-state.md`
- **🚀 Context optimization (subagent delegation - Phase 1):** `.claude/workflows/16-subagent-delegation.md`
- **🤖 Automated planning (Phase 2):** `.claude/workflows/17-automated-analysis.md`
- **✅ Context checkpointing (Phase 3 - COMPLETE):** See "Context Management" section above

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
- **Tool:** clink + gemini codereviewer → codex codereviewer (two-phase)
- **Purpose:** Safety check before every commit (~2-3 min)
- **Process:**
  - Phase 1: Gemini analyzes safety, architecture, quality (~1-2 min)
  - Phase 2: Codex synthesizes recommendations and validates (~30-60 sec)
  - **CRITICAL:** ALL issues from BOTH reviewers must be addressed
  - **CRITICAL:** BOTH gemini AND codex must approve before commit
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
- **Tool:** clink + gemini planner → codex codereviewer (two-phase)
- **Purpose:** Validate task documents before starting work (3-5 min)
- **Process:**
  - Phase 1: Gemini plans scope, validates requirements, checks completeness (~2-3 min)
  - Phase 2: Codex validates feasibility and implementation approach (~1-2 min)
  - **CRITICAL:** ALL issues from BOTH reviewers must be addressed
  - **CRITICAL:** BOTH gemini AND codex must approve before starting work
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

**Workflow Index:** [`.claude/workflows/README.md`](./.claude/workflows/README.md) — Find specific step-by-step workflows

### 🔍 PHASE 0: Pre-Implementation Analysis (MANDATORY - 30-60 min)

**⚠️ CRITICAL:** Complete comprehensive analysis BEFORE writing ANY code. Skipping this phase is the PRIMARY root cause of multiple fix commits.

**Requirements:**
- Follow [`.claude/workflows/00-analysis-checklist.md`](./.claude/workflows/00-analysis-checklist.md) completely
- Identify ALL impacted components, call sites, tests
- Verify pattern parity (retries, error handling, logging)
- Verify process compliance (review gates, CI gates)
- Create comprehensive todo list with 4-step pattern for EACH component

**DO NOT write code before completing this analysis.**

**Time saved by thorough analysis:** 3-11 hours (vs. reactive fixing)

---

### ⚠️ MANDATORY: 4-Step Pattern for Each Logical Component

**CRITICAL:** After completing Phase 0 analysis, implement EVERY logical component using this pattern:

1. **Implement** the logic component
2. **Create test cases** for comprehensive coverage (TDD)
3. **🔒 MANDATORY: Request zen-mcp review** (NEVER skip) via clink + gemini → codex (two-phase, see Tier 1 above)
4. **🔒 MANDATORY: Run `make ci-local`** (NEVER skip)
5. **Commit** ONLY after BOTH reviewers approve + CI passes

**Example:** When implementing "position limit validation", create these 5 todo tasks:
```markdown
- [ ] Implement position limit validation logic
- [ ] Create test cases for position limit validation
- [ ] Request quick review (clink + gemini → codex) for position limit validation
- [ ] Run `make ci-local` for position limit validation
- [ ] Commit position limit validation (after BOTH reviewers approve + CI pass)
```

**⚠️ PROCESS VIOLATION WARNING:**
- Committing without zen-mcp review = PRIMARY root cause of 7 fix commits (10-15 hours wasted)
- Committing without `make ci-local` = 2-4x slower than running locally first
- **Using `git commit --no-verify` is ABSOLUTELY FORBIDDEN** — bypasses review approval gates and pre-commit hooks (detected by CI)
- **NEVER skip review gates regardless of urgency**
- **ALL issues from ALL reviewers must be fixed** — no cherry-picking approvals

**Never skip or combine steps!** See [`.claude/workflows/01-git-commit.md`](./.claude/workflows/01-git-commit.md) for detailed guidance and examples.

#### 🔒 Workflow Gate Enforcement (AUTOMATIC)

**CRITICAL:** Commits are now automatically enforced via hard gates. The 4-step pattern is enforced programmatically:

**Hard Gate System:**
- **Pre-commit hook** blocks commits unless prerequisites met
- **CI verification** detects `--no-verify` bypasses
- **State machine** tracks progress through workflow steps

**Workflow State Transitions:**
```
implement → test → review → (commit succeeds) → implement
```

**Prerequisites for Commit (enforced by pre-commit hook):**
1. ✅ Current workflow step must be `review`
2. ✅ Zen-MCP review status must be `APPROVED`
3. ✅ CI must have passed (`make ci-local`)

**Workflow Gate CLI Commands:**

```bash
# Set component name (at start)
./scripts/workflow_gate.py set-component "Position Limit Validation"

# Check current state
./scripts/workflow_gate.py status

# Advance workflow steps
./scripts/workflow_gate.py advance test      # implement → test
./scripts/workflow_gate.py advance review    # test → review

# Record review approval
./scripts/workflow_gate.py record-review <continuation_id> APPROVED

# Record CI result
make ci-local && ./scripts/workflow_gate.py record-ci true

# Now commit (hook checks prerequisites automatically)
git commit -m "message"
```

**If commit blocked:**
```bash
./scripts/workflow_gate.py status  # Shows what's missing
```

**⚠️ NEVER use `git commit --no-verify`** — bypasses gates, detected by CI via `verify_gate_compliance.py`

**See:** [`.claude/workflows/component-cycle.md`](./.claude/workflows/component-cycle.md#workflow-gate-enforcement-mandatory) for complete workflow gate documentation.

### Quick Reference

0. **🔍 MANDATORY: Pre-Implementation Analysis (30-60 min)**
   - **ALWAYS complete [`.claude/workflows/00-analysis-checklist.md`](./.claude/workflows/00-analysis-checklist.md) FIRST**
   - Identify ALL impacted components, call sites, tests
   - Verify pattern parity and process compliance
   - Create comprehensive todo list (4-step pattern per component)
   - **DO NOT write code before completing analysis**

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
   - **🔒 MANDATORY: zen-mcp review** (NEVER skip, gemini → codex two-phase): `.claude/workflows/03-zen-review-quick.md`
   - **🔒 MANDATORY: ALL reviewers must approve** (gemini AND codex, fix ALL issues)
   - **🔒 MANDATORY: `make ci-local`** (NEVER skip)
   - **🚫 NEVER use `git commit --no-verify`** (bypasses review approval gates)
   - Commit workflow: `.claude/workflows/01-git-commit.md` (commit only after BOTH reviewers approve + CI pass)

5. **Before PR**
   - **🔒 MANDATORY: deep review** via clink + gemini: `.claude/workflows/04-zen-review-deep.md`
   - Create PR: `.claude/workflows/02-git-pr.md`

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

### 🔴 CRITICAL Process Violations (Root Cause of Multiple Fix Commits)

- **🚫 NEVER use `git commit --no-verify`** — **ABSOLUTELY FORBIDDEN**: Bypasses zen-mcp review approval gates and pre-commit hooks (violates quality process)
- **🚫 No coding without analysis** — **PRIMARY ROOT CAUSE**: Complete [`.claude/workflows/00-analysis-checklist.md`](./.claude/workflows/00-analysis-checklist.md) FIRST (saves 3-11 hours)
- **🚫 No direct zen-mcp tools** — **CRITICAL**: ONLY use `mcp__zen__clink` (direct tools cause API permission errors and break workflows)
- **🚫 No skipping review gates** — **CRITICAL**: Skipping zen-mcp review caused 7 fix commits (10-15 hours wasted) — ALL reviewers must approve (`.claude/workflows/03-zen-review-quick.md`)
- **🚫 No skipping reviewer issues** — **CRITICAL**: ALL issues from ALL reviewers (gemini AND codex) must be addressed — no cherry-picking approvals
- **🚫 No skipping local CI** — Run `make ci-local` BEFORE commit (2-4x faster than remote CI)
- **🚫 No incremental fixing** — Find ALL issues upfront via analysis, not reactively via reviews

### 🟡 Development Process Anti-Patterns

- **No skipping the 4-step pattern** — MANDATORY: Implement → Test → Review + CI → Commit (`.claude/workflows/01-git-commit.md`)
- **No committing without quick review** — MANDATORY clink + codex quality gate (`.claude/workflows/03-zen-review-quick.md`)
- **No committing without passing tests** — Run `make test && make lint` before every commit
- **No combining logical components in one commit** — Use 4-step pattern for each component separately
- **No PRs without deep review** — MANDATORY clink + gemini comprehensive review (`.claude/workflows/04-zen-review-deep.md`)
- **No starting work without task review** — Use clink + gemini planner to validate task documents (`.claude/workflows/13-task-creation-review.md`)

### 🟢 Code Quality Anti-Patterns

- **No duplicate feature logic** — Share code between research/production
- **No pattern violations** — New code MUST match established patterns (retries, error handling, logging)
- **No call site ignorance** — When changing function signatures, analyze ALL call sites first
- **No test mocking gaps** — Verify `@patch` paths, pytest markers, health endpoint tests
- **No in-memory state** — Use DB for positions/orders/breakers
- **No silent failures** — Always log and raise with context
- **No scattered configs** — Centralize in Pydantic Settings
- **No unvalidated data** — Quality gate before storage
- **No untested order paths** — Require backtest replay parity
- **No live without paper** — Paper validation required first
- **No architectural changes without ADR** — MANDATORY (`.claude/workflows/08-adr-creation.md`)

**See `/tmp/ci-failure-root-cause-analysis.md` for detailed analysis of root causes.**

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

0. **🔍 MANDATORY: Pre-Implementation Analysis (30-60 min)**
   - **Complete [`.claude/workflows/00-analysis-checklist.md`](./.claude/workflows/00-analysis-checklist.md) FIRST**
   - Identify ALL impacted components, call sites, tests
   - Verify pattern parity (retries, error handling, logging)
   - Verify process compliance (review gates, CI gates)
   - Create comprehensive todo list with 4-step pattern per component
   - **⚠️ DO NOT write code before completing analysis** (PRIMARY root cause of fix commits)

1. Check existing docs (`/docs/GETTING_STARTED/REPO_MAP.md`, API specs, DB schemas)

2. **For task documents:** Request task creation review via clink + gemini (`.claude/workflows/13-task-creation-review.md`)

3. Create ADR for architectural changes (`.claude/workflows/08-adr-creation.md`)

4. Document trading concepts in `/docs/CONCEPTS/` (`.claude/workflows/07-documentation.md`)

5. **Break feature into logical components** — Use 4-step pattern per component:
   - Implement logic
   - Create test cases (TDD)
   - **🔒 MANDATORY: Quick review** via clink + gemini → codex (two-phase) (`.claude/workflows/03-zen-review-quick.md`)
   - **🔒 MANDATORY: Fix ALL issues from ALL reviewers** (gemini AND codex must both approve)
   - **🔒 MANDATORY: Run `make ci-local`** (NEVER skip)
   - **🚫 NEVER use `git commit --no-verify`** (absolutely forbidden)
   - Commit ONLY after BOTH reviewers approve + CI passes

6. Add comprehensive docstrings (see `/docs/STANDARDS/DOCUMENTATION_STANDARDS.md`)

7. **Never skip the 4-step pattern** (see `.claude/workflows/01-git-commit.md`)

8. Update affected docs

9. **🔒 MANDATORY: Before PR** - deep review via clink + gemini (`.claude/workflows/04-zen-review-deep.md`)

**Workflow Index:** [`.claude/workflows/README.md`](./.claude/workflows/README.md) — Find specific step-by-step workflows

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
