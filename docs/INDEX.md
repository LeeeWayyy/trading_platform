# Documentation Index

**Canonical Entry Point for AI Coding Assistants and Developers**

This index provides a structured guide to all documentation, organized by purpose and priority. For AI assistants: **start here** to understand which documents are normative (must-follow) versus informational (context).

---

## 🎯 Quick Start for AI Assistants

**When starting a new task, read in this order:**

1. **[STANDARDS/](./STANDARDS/)** - Normative guidance (MUST follow)
2. **[TASKS/](./TASKS/)** - Current work items
3. **[IMPLEMENTATION_GUIDES/](./IMPLEMENTATION_GUIDES/)** - How-to references
4. **[ADRs/](./ADRs/)** - Architecture decisions
5. **[CONCEPTS/](./CONCEPTS/)** - Domain knowledge

---

## 📋 Document Categories

### 1. Normative Standards (MUST Follow) ⚠️

**Location:** `docs/STANDARDS/`

These documents define **mandatory** practices for all code and contributions:

| Document | Purpose | When to Read |
|----------|---------|--------------|
| [CODING_STANDARDS.md](./STANDARDS/CODING_STANDARDS.md) | Python style, type hints, error handling | Before writing code |
| [DOCUMENTATION_STANDARDS.md](./STANDARDS/DOCUMENTATION_STANDARDS.md) | Docstring format, examples, comments | Before documenting |
| [GIT_WORKFLOW.md](./STANDARDS/GIT_WORKFLOW.md) | Commit messages, PR process | Before committing |
| [TESTING.md](./STANDARDS/TESTING.md) | Test structure, coverage requirements | Before writing tests |
| [ADR_GUIDE.md](./STANDARDS/ADR_GUIDE.md) | When/how to write ADRs | Before making architectural decisions |

**Priority:** 🔴 **CRITICAL** - AI assistants MUST read these first for any code task

---

### 2. Getting Started (Setup & Orientation)

**Location:** `docs/GETTING_STARTED/`

Onboarding and environment setup:

| Document | Purpose | Audience |
|----------|---------|----------|
| [SETUP.md](./GETTING_STARTED/SETUP.md) | Development environment setup | New developers |
| [TESTING_SETUP.md](./GETTING_STARTED/TESTING_SETUP.md) | Test environment configuration | QA, developers |
| [PROJECT_STATUS.md](./GETTING_STARTED/PROJECT_STATUS.md) | Current implementation status | All stakeholders |
| [REPO_MAP.md](./GETTING_STARTED/REPO_MAP.md) | Codebase navigation guide | New developers |
| [GLOSSARY.md](./GETTING_STARTED/GLOSSARY.md) | Trading and ML terminology | All developers |

**Priority:** 🟡 **HIGH** - Read during onboarding or when confused about structure

---

### 3. Architecture & Decisions (ADRs)

**Location:** `docs/ADRs/`

Architectural Decision Records documenting **why** technical choices were made:

| ADR | Decision | Status |
|-----|----------|--------|
| [0001](./ADRs/0001-data-pipeline-architecture.md) | Data pipeline (Polars, Parquet, backward adjustment) | ✅ Accepted |
| [0002](./ADRs/0002-exception-hierarchy.md) | Exception hierarchy for data quality | ✅ Accepted |
| [0003](./ADRs/0003-baseline-strategy-with-qlib-and-mlflow.md) | Qlib + MLflow for baseline strategy | ✅ Accepted |
| [0004](./ADRs/0004-signal-service-architecture.md) | Signal service design (FastAPI, hot reload) | ✅ Accepted |
| [0005](./ADRs/0005-execution-gateway-architecture.md) | Execution gateway (idempotency, DRY_RUN) | ✅ Accepted |
| [0006](./ADRs/0006-orchestrator-service.md) | Orchestrator service (async, position sizing) | ✅ Accepted |
| [0007](./ADRs/0007-paper-run-automation.md) | Paper run automation (CLI script vs service) | ✅ Accepted |
| [0008](./ADRs/0008-enhanced-pnl-calculation.md) | Enhanced P&L calculation (realized/unrealized) | ✅ Accepted |
| [0009](./ADRs/0009-redis-integration.md) | Redis integration (feature cache, event bus) | ✅ Accepted |

**How to use ADRs:**
- **Before modifying architecture:** Check if ADR exists, follow its decisions
- **When making new architectural choice:** Write new ADR (see [ADR_GUIDE.md](./STANDARDS/ADR_GUIDE.md))
- **When questioning a decision:** Read the ADR to understand rationale and alternatives

**Priority:** 🟠 **MEDIUM** - Read relevant ADRs before modifying related systems

---

### 4. Domain Concepts (Trading & ML Knowledge)

**Location:** `docs/CONCEPTS/`

Educational explanations of trading and ML concepts:

| Concept | Topic | Complexity |
|---------|-------|------------|
| [corporate-actions.md](./CONCEPTS/corporate-actions.md) | Stock splits, dividends | Beginner |
| [pnl-calculation.md](./CONCEPTS/pnl-calculation.md) | Notional, realized, unrealized P&L | Beginner |
| [alpha158-features.md](./CONCEPTS/alpha158-features.md) | Alpha158 feature set | Intermediate |
| [qlib-data-providers.md](./CONCEPTS/qlib-data-providers.md) | Qlib integration patterns | Intermediate |
| [lightgbm-training.md](./CONCEPTS/lightgbm-training.md) | Model training pipeline | Intermediate |
| [model-registry.md](./CONCEPTS/model-registry.md) | Model lifecycle management | Intermediate |
| [hot-reload.md](./CONCEPTS/hot-reload.md) | Zero-downtime model updates | Advanced |
| [feature-parity.md](./CONCEPTS/feature-parity.md) | Research-production consistency | Advanced |
| [webhook-security.md](./CONCEPTS/webhook-security.md) | HMAC signature verification | Advanced |
| [redis-patterns.md](./CONCEPTS/redis-patterns.md) | Redis caching and event patterns | Intermediate |

**Priority:** 🟢 **LOW** - Read when you need to understand domain-specific concepts

---

### 5. Implementation Guides (How-To)

**Location:** `docs/IMPLEMENTATION_GUIDES/`

Step-by-step implementation instructions for each major task:

| Guide | Task | Lines | Test Coverage |
|-------|------|-------|---------------|
| [p0t1-data-etl.md](./IMPLEMENTATION_GUIDES/p0t1-data-etl.md) | Data ETL pipeline | 800+ | 53 tests, 100% |
| [p1.1t2-redis-integration.md](./IMPLEMENTATION_GUIDES/p1.1t2-redis-integration.md) | Redis feature cache & event bus | 850+ | 85 tests, 100% |
| [p0t2-baseline-strategy.md](./IMPLEMENTATION_GUIDES/p0t2-baseline-strategy.md) | Baseline ML strategy | 700+ | Unit tests |
| [p0t3-signal-service.md](./IMPLEMENTATION_GUIDES/p0t3-signal-service.md) | Signal service (main guide) | 1,940+ | 57 tests, 95% |
| [p0t3-p4-fastapi-application.md](./IMPLEMENTATION_GUIDES/p0t3-p4-fastapi-application.md) | FastAPI implementation | 600+ | Phase 4 tests |
| [p0t3-p5-hot-reload.md](./IMPLEMENTATION_GUIDES/p0t3-p5-hot-reload.md) | Hot reload mechanism | 500+ | Phase 5 tests |
| [p0t3-p6-integration-tests.md](./IMPLEMENTATION_GUIDES/p0t3-p6-integration-tests.md) | Integration testing | 400+ | Phase 6 tests |
| [p0t4-execution-gateway.md](./IMPLEMENTATION_GUIDES/p0t4-execution-gateway.md) | Execution gateway | 827+ | 56 tests, 100% |
| [p0t5-orchestrator.md](./IMPLEMENTATION_GUIDES/p0t5-orchestrator.md) | Orchestrator service | 754+ | 13 tests, 100% |
| [p0t6-paper-run.md](./IMPLEMENTATION_GUIDES/p0t6-paper-run.md) | Paper run automation | 1,059+ | 26 tests, 100% |

**Priority:** 🟡 **HIGH** - Read the relevant guide when implementing or modifying a task

---

### 6. Tasks & Planning

**Location:** `docs/TASKS/`

Current and future work items:

| Document | Purpose | Status |
|----------|---------|--------|
| [P0_TASKS.md](./TASKS/P0_TASKS.md) | MVP core tasks (P0T1-P0T6) | ✅ 100% Complete |
| [P1_PLANNING.md](./TASKS/P1_PLANNING.md) | P1 roadmap and priorities | 🔄 73% Complete (8/11 tasks) |
| [TASKS/INDEX.md](./TASKS/INDEX.md) | Task index and status | 🔄 In Progress |
| [trading_platform_realization_plan.md](./trading_platform_realization_plan.md) | Original master plan | 📚 Reference |

**Checking Current/Next Task:**
```bash
# Show current task in progress
./scripts/tasks.py list --state PROGRESS

# Show next pending task
./scripts/tasks.py list --state TASK --limit 1
```

**Priority:** 🔴 **CRITICAL** - Check before starting any new task to understand scope and priorities

---

### 7. Lessons Learned (Retrospectives)

**Location:** `docs/LESSONS_LEARNED/`

Post-implementation analysis and learnings:

| Document | Task | Key Learnings |
|----------|------|---------------|
| [p1-p3-testing-journey.md](./LESSONS_LEARNED/p1-p3-testing-journey.md) | T3 testing evolution | Testing strategy evolution |
| [t1.2-redis-integration-fixes.md](./LESSONS_LEARNED/t1.2-redis-integration-fixes.md) | T1.2 Redis integration | 5 issues found during testing, graceful degradation |
| [t6-paper-run-retrospective.md](./LESSONS_LEARNED/t6-paper-run-retrospective.md) | T6 retrospective | Intentional MVP simplifications, P1 action items |

**Priority:** 🟢 **LOW** - Read after completing tasks to learn from past experiences

---

### 8. Runbooks (Operations)

**Location:** `docs/RUNBOOKS/`

Operational procedures and troubleshooting:

| Document | Purpose | When to Use |
|----------|---------|-------------|
| [ops.md](./RUNBOOKS/ops.md) | Operational procedures | During deployment, troubleshooting |

**Priority:** 🟡 **HIGH** - Read when deploying or troubleshooting production issues

---

### 9. Archived Documentation

**Location:** `docs/ARCHIVE/`

Deprecated documents retained for historical reference:

| Document | Archived Date | Reason | Replacement |
|----------|---------------|--------|-------------|
| [NEXT_TASK_20241021.md](./ARCHIVE/NEXT_TASK_20241021.md) | Oct 21, 2024 | Redundant with task files | CLI: `./scripts/tasks.py next` |
| [P1_PROGRESS_20241021.md](./ARCHIVE/P1_PROGRESS_20241021.md) | Oct 21, 2024 | Merged into P1_PLANNING | [P1_PLANNING.md](./TASKS/P1_PLANNING.md) |
| [FILE_RENAME_MAP_20241018.md](./ARCHIVE/FILE_RENAME_MAP_20241018.md) | Oct 18, 2024 | Historical reference | N/A (migration complete) |

**See:** [ARCHIVE/README.md](./ARCHIVE/README.md) for migration guide and rationale

**Priority:** 🟢 **LOW** - Reference only if needed for historical context

---

### 10. AI Assistant Guidance

**Location:** `docs/`

Special guidance for AI coding assistants:

| Document | Purpose | When to Read |
|----------|---------|--------------|
| [AI_GUIDE.md](./AI_GUIDE.md) | Specific instructions for Claude Code | Always (first document) |
| [INDEX.md](./INDEX.md) | This file - documentation structure | Always (navigation) |

**Priority:** 🔴 **CRITICAL** - AI assistants MUST read AI_GUIDE.md first

---

## 🤖 AI Assistant Reading Order

### For New Tasks (Code Implementation)

```
1. docs/AI_GUIDE.md                                    [If not already read]
2. docs/INDEX.md                                       [This file - for navigation]
3. docs/STANDARDS/CODING_STANDARDS.md                  [MUST read]
4. docs/STANDARDS/DOCUMENTATION_STANDARDS.md           [MUST read]
5. docs/STANDARDS/GIT_WORKFLOW.md                      [MUST read]
6. docs/TASKS/P0_TASKS.md or P1_PLANNING.md            [Understand current task]
7. docs/IMPLEMENTATION_GUIDES/p{phase}t{N}-{task}.md   [Relevant guide]
8. docs/ADRs/000{N}-{related}.md                       [Related decisions]
9. docs/CONCEPTS/{relevant}.md                         [As needed for domain knowledge]
```

### For Architectural Changes

```
1. docs/STANDARDS/ADR_GUIDE.md                         [How to write ADRs]
2. docs/ADRs/*.md                                      [Review existing decisions]
3. Create new ADR following template                   [Document your decision]
```

### For Bug Fixes

```
1. docs/STANDARDS/CODING_STANDARDS.md                  [Coding standards]
2. docs/RUNBOOKS/ops.md                                [Troubleshooting procedures]
3. Relevant implementation guide                       [Understand system design]
```

### For Documentation

```
1. docs/STANDARDS/DOCUMENTATION_STANDARDS.md           [Docstring format]
2. Existing similar documents                          [Follow established patterns]
```

---

## 📂 Directory Conventions

### File Naming

- **Normative standards:** ALL_CAPS (e.g., `CODING_STANDARDS.md`)
- **ADRs:** Numbered `0000-kebab-case.md`
- **Guides:** Prefixed `p{phase}t{N}-kebab-case.md` or `p{phase}.{track}t{N}-kebab-case.md`
- **Concepts:** `kebab-case.md`
- **Retrospectives:** `{phase}-description.md`

### Document Status

Documents can have one of these statuses:

| Status | Meaning | Used In |
|--------|---------|---------|
| ✅ Accepted | Decision made, actively followed | ADRs |
| 🚧 Draft | Work in progress | All types |
| ⏸️ Deprecated | Superseded by newer doc | ADRs, guides |
| 📋 Planning | Future work | Tasks |
| 📚 Reference | Historical context only | Plans |

---

## 🔍 Finding What You Need

### By Task Type

| I want to... | Read this... |
|-------------|--------------|
| Write Python code | [CODING_STANDARDS.md](./STANDARDS/CODING_STANDARDS.md) |
| Add docstrings | [DOCUMENTATION_STANDARDS.md](./STANDARDS/DOCUMENTATION_STANDARDS.md) |
| Commit code | [GIT_WORKFLOW.md](./STANDARDS/GIT_WORKFLOW.md) |
| Write tests | [TESTING.md](./STANDARDS/TESTING.md) |
| Understand P&L | [pnl-calculation.md](./CONCEPTS/pnl-calculation.md) |
| Implement P0T6 | [p0t6-paper-run.md](./IMPLEMENTATION_GUIDES/p0t6-paper-run.md) |
| Plan P1 work | [P1_PLANNING.md](./TASKS/P1_PLANNING.md) |
| Deploy to prod | [ops.md](./RUNBOOKS/ops.md) |

### By Document Type

| Type | Purpose | Location |
|------|---------|----------|
| **Standards** | Normative rules (MUST follow) | `docs/STANDARDS/` |
| **ADRs** | Architecture decisions (WHY) | `docs/ADRs/` |
| **Concepts** | Domain knowledge (WHAT) | `docs/CONCEPTS/` |
| **Guides** | Implementation steps (HOW) | `docs/IMPLEMENTATION_GUIDES/` |
| **Tasks** | Work items (TODO) | `docs/TASKS/` |
| **Retrospectives** | Learnings (LEARNED) | `docs/LESSONS_LEARNED/` |
| **Runbooks** | Operations (OPS) | `docs/RUNBOOKS/` |

---

## 📊 Documentation Metrics

- **Total Documents:** 45+ files
- **Lines of Documentation:** 20,400+ lines
- **ADRs:** 9 accepted decisions
- **Implementation Guides:** 10 detailed guides
- **Concept Docs:** 10 educational explanations
- **Lessons Learned:** 3 retrospectives
- **Test Coverage:** 293/296 tests passing (99.0%)

---

## 🔄 Maintenance

### When to Update This Index

- New document added → Add to relevant category
- Document moved → Update all references
- New category needed → Add section with description
- Document deprecated → Mark with ⏸️ and link to replacement

### Document Owners

- **Standards:** Architecture team (changes require ADR)
- **ADRs:** Author + reviewers (immutable after acceptance)
- **Guides:** Task implementer (updated during implementation)
- **Concepts:** Domain experts (updated as understanding evolves)
- **Tasks:** Product owner (updated during planning)
- **Retrospectives:** Task implementer (written after completion)

---

## 🆘 Getting Help

- **Question about standards?** Check [STANDARDS/](./STANDARDS/) first
- **Don't understand a decision?** Read the relevant [ADR](./ADRs/)
- **Stuck implementing?** Follow the [IMPLEMENTATION_GUIDE](./IMPLEMENTATION_GUIDES/)
- **Need context?** Read [CONCEPTS](./CONCEPTS/) for domain knowledge
- **Lost in the codebase?** See [REPO_MAP.md](./GETTING_STARTED/REPO_MAP.md)

---

**Last Updated:** 2025-10-18
**Maintained By:** Development Team
**Format Version:** 1.1
