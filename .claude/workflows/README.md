# Workflow Index & Getting Started

**Quick reference for all workflows.** See [CLAUDE.md](../../CLAUDE.md) for primary guidance and mandatory process steps.

---

## 🚀 Getting Started (New Developer Setup)

**Time:** 30-60 minutes | **When:** First-time setup, onboarding, environment rebuild

```bash
# 1. Install prerequisites (macOS)
# Note: `brew install docker` installs the CLI. For the Docker Engine, install Docker Desktop.
brew install python@3.11 postgresql@15 redis git gh docker
curl -sSL https://install.python-poetry.org | python3.11 -
export PATH="$HOME/.local/bin:$PATH"

# 2. Clone and setup
gh auth login
git clone git@github.com:username/trading_platform.git
cd trading_platform
poetry install

# 3. Start infrastructure
make up  # Starts postgres, redis, grafana, prometheus

# 4. Initialize database
make db-create && make db-migrate

# 5. Configure environment
cp .env.example .env
# Edit .env: DATABASE_URL, REDIS_URL, ALPACA keys, DRY_RUN=true

# 6. Verify setup
make ci-local  # Should pass all tests

# 7. Next steps
# - Read CLAUDE.md for development workflow
# - Review 00-analysis-checklist.md before coding
# - Follow 12-component-cycle.md for each component
```

---

## 📋 Core Workflows (12 Total)

### Pre-Implementation (MANDATORY)

| Workflow | Purpose | When |
|----------|---------|------|
| [00-analysis-checklist.md](./00-analysis-checklist.md) | Comprehensive pre-implementation analysis | Before writing ANY code (30-60 min, MANDATORY) |
| [02-planning.md](./02-planning.md) | Task breakdown, subfeatures, task creation review | Complex tasks (>8h), phase management |

### Development Cycle (6-Step Pattern)

| Workflow | Purpose | When |
|----------|---------|------|
| [12-component-cycle.md](./12-component-cycle.md) | 6-step pattern: Plan → Plan Review → Implement → Test → Code Review → Commit | Every logical component (MANDATORY) |
| [04-development.md](./04-development.md) | Testing, debugging, documentation, ADRs | During implementation |
| [03-reviews.md](./03-reviews.md) | Quick (pre-commit) + Deep (pre-PR) reviews | Before commits & PRs (MANDATORY) |
| [01-git.md](./01-git.md) | Progressive commits + PR creation | Every 30-60 min, when feature complete |

### Operations & Continuity

| Workflow | Purpose | When |
|----------|---------|------|
| [05-operations.md](./05-operations.md) | Deployment, rollback, CI triage | Production operations, CI failures |
| [08-session-management.md](./08-session-management.md) | Resume tasks, update task state | Session start, context limits, multi-day work |

### Advanced Workflows

| Workflow | Purpose | When |
|----------|---------|------|
| [16-subagent-delegation.md](./16-subagent-delegation.md) | Context optimization via Task tool | Context ≥70%, non-core tasks |
| [17-automated-analysis.md](./17-automated-analysis.md) | Automated planning with zen-mcp | Complex task analysis |
| [16-pr-review-comment-check.md](./16-pr-review-comment-check.md) | Systematic PR feedback handling | After receiving PR review comments |

---

## 📁 Shared References (_common/)

| Reference | Purpose | Used By |
|-----------|---------|---------|
| [clink-policy.md](./_common/clink-policy.md) | Zen-MCP tool usage policy | All zen-mcp workflows |
| [zen-review-process.md](./_common/zen-review-process.md) | 3-tier review system details | 02, 03, 17 |
| [git-commands.md](./_common/git-commands.md) | Git operations reference | 01, 05 |
| [test-commands.md](./_common/test-commands.md) | Testing commands | 04, 05 |

---

## 🎯 Quick Navigation

### By Frequency

**Every Session Start:**
- 🤖 Auto-resume check (see 08-session-management.md)

**Before ANY Implementation:**
- 🔍 00-analysis-checklist.md (MANDATORY, 30-60 min)
- 📋 02-planning.md (for complex tasks >8h)

**Every Component (30-60 min):**
- 🔄 12-component-cycle.md (6 steps: Plan → Plan Review → Implement → Test → Code Review → Commit)
- 🧪 04-development.md (testing, debugging, docs)
- ✅ 03-reviews.md (quick review before commit)
- 💾 01-git.md (commit after approval)

**When Feature Complete:**
- 🔍 03-reviews.md (deep review before PR)
- 🚀 01-git.md (create PR)

**When Needed:**
- 🚨 05-operations.md (deploy, rollback, CI triage)
- 🔄 08-session-management.md (resume, update state)
- ⚡ 16-subagent-delegation.md (context ≥70%)

### By Purpose

**Planning:** 00-analysis-checklist.md, 02-planning.md, 17-automated-analysis.md
**Development:** 12-component-cycle.md, 04-development.md
**Quality:** 03-reviews.md (Tier 1 + Tier 2)
**Version Control:** 01-git.md
**Operations:** 05-operations.md
**Continuity:** 08-session-management.md
**Optimization:** 16-subagent-delegation.md
**PR Management:** 16-pr-review-comment-check.md

---

## 📊 Workflow Metrics

**Total Workflows:** 12 core workflows + 4 shared references = 16 docs
**Lines:** ~4,500 lines (down from 7,611 original, 41% reduction)

**Consolidation Summary (Component 6 Part 3):**
- Phase 1: Component cycle + planning (6 → 2 files)
- Phase 2: Git + reviews (4 → 2 files)
- Phase 3a: Development + operations (6 → 2 files)
- Phase 3b: Bootstrap → README (merged)
- **Result:** 27 → 12 workflow files (55% reduction)

---

## 🔗 See Also

- [CLAUDE.md](../../CLAUDE.md) - Primary guidance and principles
- [docs/INDEX.md](../../docs/INDEX.md) - Complete documentation index
- [docs/STANDARDS/](../../docs/STANDARDS/) - Coding, testing, git, documentation standards
