# AI Assistant Guide

**Purpose:** Quick-start guide for AI coding assistants working in this repository.

**Your role:** Implement tickets safely, incrementally, and test-first following established standards.

---

## 🚀 Start Here

**First time in this repo?** Read these documents in order:

1. **[INDEX.md](./INDEX.md)** — Canonical documentation index (read this first!)
2. **[STANDARDS/CODING_STANDARDS.md](./STANDARDS/CODING_STANDARDS.md)** — Python style, patterns, error handling (MUST follow)
3. **[STANDARDS/DOCUMENTATION_STANDARDS.md](./STANDARDS/DOCUMENTATION_STANDARDS.md)** — Docstring format (MUST follow)
4. **[STANDARDS/GIT_WORKFLOW.md](./STANDARDS/GIT_WORKFLOW.md)** — Commit messages, PR process (MUST follow)
5. **[STANDARDS/TESTING.md](./STANDARDS/TESTING.md)** — Test structure, coverage requirements (MUST follow)
6. **[GETTING_STARTED/REPO_MAP.md](./GETTING_STARTED/REPO_MAP.md)** — Directory structure and module owners

---

## 📐 Normative Standards (MUST Follow)

All standards are in **[STANDARDS/](./STANDARDS/)** directory:

- **[CODING_STANDARDS.md](./STANDARDS/CODING_STANDARDS.md)** — Type hints, error handling, async patterns
- **[DOCUMENTATION_STANDARDS.md](./STANDARDS/DOCUMENTATION_STANDARDS.md)** — Docstrings, ADRs, guides
- **[GIT_WORKFLOW.md](./STANDARDS/GIT_WORKFLOW.md)** — Branching, commits, PRs
- **[TESTING.md](./STANDARDS/TESTING.md)** — Unit/integration tests, mocking, coverage
- **[ADR_GUIDE.md](./STANDARDS/ADR_GUIDE.md)** — When/how to write Architecture Decision Records

---

## 🎯 Implementation Workflow

### 1. Read the Task
- Find current task: `./scripts/tasks.py list --state PROGRESS`
- Or check next task: `./scripts/tasks.py list --state TASK --limit 1`
- Read the task file: **[TASKS/PxTy_TASK.md](./TASKS/)** or **[TASKS/PxTy_PROGRESS.md](./TASKS/)**
- Read relevant **[IMPLEMENTATION_GUIDES/](./TASKS/)** if available

### 2. Review Architecture Decisions
- Check **[ADRs/](./ADRs/)** for relevant decisions
- If making new architectural choice, write new ADR per **[STANDARDS/ADR_GUIDE.md](./STANDARDS/ADR_GUIDE.md)**

### 3. Write Tests First (TDD)
- Follow **[STANDARDS/TESTING.md](./STANDARDS/TESTING.md)**
- Run tests: `make test` (expect failures = red)

### 4. Implement
- Follow **[STANDARDS/CODING_STANDARDS.md](./STANDARDS/CODING_STANDARDS.md)**
- Document per **[STANDARDS/DOCUMENTATION_STANDARDS.md](./STANDARDS/DOCUMENTATION_STANDARDS.md)**
- Implement in specified module only (see **[GETTING_STARTED/REPO_MAP.md](./GETTING_STARTED/REPO_MAP.md)**)

### 5. Request Quick Review ⚠️ **MANDATORY**

**BEFORE each commit (every 30-60 min):**

1. **Stage changes:** `git add <files>`

2. **Request review:**
   ```
   Tell Claude: "Review my staged changes using clink + codex codereviewer"

   (See ../.claude/workflows/03-zen-review-quick.md for full workflow)
   ```

3. **Fix ALL issues found:**
   - HIGH/CRITICAL: Blocking (must fix)
   - MEDIUM: Must fix or document deferral
   - LOW: Fix if time permits

4. **Re-request review to verify:** "I've fixed the issues, please verify (continuation_id: xyz)"

5. **Commit only when approved:** `git commit -m "Message"`

**Progressive commits:** Review → Fix → Commit (repeat every 30-60 min)

**See:** **[CONCEPTS/zen-mcp-clink-optimization-proposal.md](./CONCEPTS/zen-mcp-clink-optimization-proposal.md)**

### 6. Verify
- Run `make test` and `make lint` (expect success = green)
- All tests must pass (100%)

### 7. Deep Review Before PR ⚠️ **MANDATORY**

**Before creating ANY pull request:**

1. **Request comprehensive review (two-phase):**
   ```
   Phase 1: "Review all branch changes using clink + gemini planner. Compare master..HEAD."
   Phase 2: "Now use clink + codex planner with continuation_id to synthesize recommendations"

   (See ../.claude/workflows/04-zen-review-deep.md for full workflow)
   ```

2. **Fix all HIGH/CRITICAL issues** (blocking)

3. **Document any deferred issues** in PR description

### 8. Document
- Update relevant docs if needed
- Update ADRs if architecture changed

### 9. Create PR
- **Include review confirmation with continuation_id** (MANDATORY)
- Follow checklist from **[STANDARDS/TESTING.md](./STANDARDS/TESTING.md)**
- Request GitHub App reviews (@codex @gemini-code-assist)
- Reference ticket number

---

## 🔒 Critical Guardrails

### Idempotency (Trading-Specific)
- **Never** produce duplicate orders
- Always check existing positions before creating new ones
- Use idempotency keys for all order submissions

### Feature Parity (ML-Specific)
- Research and production feature definitions **must share code**
- No divergence between training and inference feature calculations
- See **[CONCEPTS/feature-parity.md](./CONCEPTS/feature-parity.md)**

### Security
- **Never commit secrets** (use `.env` files, gitignored)
- Reference `.env.example` for required variables
- Enable `DRY_RUN=true` by default in development

### Rate Limiting
- Respect Alpaca API rate limits (200 req/min)
- Implement backoff policies in execution gateway
- Circuit breakers override signals (if TRIPPED, halt new orders)

---

## 🔧 Python Environment Setup

### ⚠️ CRITICAL: Always Use Virtual Environment

**NEVER use system Python directly!** Always activate the virtual environment first:

```bash
# Activate virtual environment (REQUIRED before ANY Python command)
source .venv/bin/activate

# Verify you're in venv (should show .venv path)
which python3
# Output: /Users/.../trading_platform/.venv/bin/python3
```

### Running Python Commands

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

### Why Virtual Environment?

1. **Isolated Dependencies**: Project packages don't interfere with system Python
2. **Reproducible Environment**: Everyone uses same package versions
3. **Clean Testing**: Tests run with correct dependencies
4. **Prevents Version Conflicts**: System Python may have incompatible packages

---

## 🌍 Environments

### Development (Default)
```bash
DRY_RUN=true                          # No real orders
DATABASE_URL=postgresql://...         # Local PostgreSQL
ALPACA_BASE_URL=https://paper-api... # Paper trading
```

### Paper Trading
- Requires valid Alpaca paper API keys
- Still uses `DRY_RUN=true` for safety
- Real market data, simulated executions

### Production (Future)
- `DRY_RUN=false` required
- Live Alpaca API keys
- Real money — extra validation required

---

## 📚 Reference Documents by Purpose

### For Understanding the Codebase
- **[GETTING_STARTED/REPO_MAP.md](./GETTING_STARTED/REPO_MAP.md)** — Directory structure
- **[GETTING_STARTED/GLOSSARY.md](./GETTING_STARTED/GLOSSARY.md)** — Trading and ML terms
- **[GETTING_STARTED/PROJECT_STATUS.md](./GETTING_STARTED/PROJECT_STATUS.md)** — What's implemented

### For Domain Knowledge
- **[CONCEPTS/](./CONCEPTS/)** — Trading and ML concepts explained
  - corporate-actions.md, pnl-calculation.md, alpha158-features.md, etc.

### For Implementation Guidance
- **[IMPLEMENTATION_GUIDES/](./TASKS/)** — Step-by-step task guides
  - t1-data-etl.md, t2-baseline-strategy-qlib.md, t6-paper-run.md, etc.

### For Architecture Context
- **[ADRs/](./ADRs/)** — Architecture Decision Records
  - Why we chose Polars, Qlib, FastAPI, etc.

### For Operations
- **[RUNBOOKS/ops.md](./RUNBOOKS/ops.md)** — Deployment and troubleshooting

---

## 🚨 No Silent Scope Changes

If a ticket lacks detail or conflicts with existing architecture:

1. **Do NOT** invent new APIs, folders, or patterns
2. **DO** propose an ADR following **[STANDARDS/ADR_GUIDE.md](./STANDARDS/ADR_GUIDE.md)**
3. **DO** add TODOs with clear rationale
4. **DO** ask for clarification in PR description

---

## 📖 Full Documentation Index

For complete documentation structure, see **[INDEX.md](./INDEX.md)**

---

**Last Updated:** 2025-01-17
**Maintained By:** Development Team
**Format Version:** 2.0 (Reorganized with directory structure)
