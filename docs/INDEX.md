# Documentation Index

**Canonical Entry Point for AI Coding Assistants and Developers**

This index provides a structured guide to all documentation, organized by purpose and priority. For AI assistants: **start here** to understand which documents are normative (must-follow) versus informational (context).

---

## 📌 Quick Links (AI Navigation)

**Essential First Reads:**
- [INDEX.md](./INDEX.md) (this file) → Documentation structure and navigation
- [AI_GUIDE.md](./AI_GUIDE.md) → AI assistant quick-start and discovery patterns
- [STANDARDS/](./STANDARDS/) → Normative standards directory (MUST follow)

**By Task Type:**
- **New Feature Implementation** → TASKS/ → ADRs/ → CONCEPTS/ → STANDARDS/
- **Bug Fix** → RUNBOOKS/ops.md → Relevant implementation guide
- **Architecture Change** → STANDARDS/ADR_GUIDE.md → ADRs/ → Write new ADR
- **Documentation** → STANDARDS/DOCUMENTATION_STANDARDS.md → Update relevant docs
- **Testing** → STANDARDS/TESTING.md → Write tests

**By Document Type:**
- **Standards** (mandatory rules) → [STANDARDS/](#1-normative-standards-must-follow-️)
- **Architecture** (decisions and rationale) → [ADRs/](#3-architecture--decisions-adrs)
- **Concepts** (domain knowledge) → [CONCEPTS/](#4-domain-concepts-trading--ml-knowledge)
- **Tasks** (work tracking) → [TASKS/](#6-tasks--planning)
- **Runbooks** (operations) → [RUNBOOKS/](#8-runbooks-operations)

---

## 🎯 Quick Start for AI Assistants

**When starting a new task, read in this order:**

1. **[STANDARDS/](./STANDARDS/)** - Normative guidance (MUST follow)
2. **[TASKS/](./TASKS/)** - Current work items
3. **[Task Implementation Guides](./TASKS/)** - How-to references
4. **[ADRs/](./ADRs/)** - Architecture decisions
5. **[CONCEPTS/](./CONCEPTS/)** - Domain knowledge

---

## 📋 Document Categories

### 0. Project Root Files

**Location:** Project root directory

Essential project-level documentation:

- [CURRENT, 2025-10-31, Guide] [README.md](../README.md) - Project overview and quick start
- [CURRENT, 2025-10-31, Guide] [CLAUDE.md](../CLAUDE.md) - AI assistant primary guidance and workflow index
- [CURRENT, 2025-10-19, Guide] [AGENTS.md](../AGENTS.md) - AI agent configuration and usage

**Priority:** 🔴 **CRITICAL** - Read CLAUDE.md first for complete guidance

---

### 1. Normative Standards (MUST Follow) ⚠️

**Location:** `docs/STANDARDS/`

These documents define **mandatory** practices for all code and contributions:

- [CURRENT, 2025-01-17, Standard] [CODING_STANDARDS.md](./STANDARDS/CODING_STANDARDS.md) - Python style, type hints, error handling (read before writing code)
- [CURRENT, 2025-01-17, Standard] [DOCUMENTATION_STANDARDS.md](./STANDARDS/DOCUMENTATION_STANDARDS.md) - Docstring format, examples, comments (read before documenting)
- [CURRENT, 2025-10-24, Standard] [GIT_WORKFLOW.md](./STANDARDS/GIT_WORKFLOW.md) - Commit messages, PR process, PxTy-Fz branching (read before committing)
- [CURRENT, 2025-11-16, Standard] [BRANCH_PROTECTION.md](./STANDARDS/BRANCH_PROTECTION.md) - GitHub branch protection setup and verification (repository administrators)
- [CURRENT, 2025-01-17, Standard] [TESTING.md](./STANDARDS/TESTING.md) - Test structure, coverage requirements (read before writing tests)
- [CURRENT, 2025-01-17, Standard] [ADR_GUIDE.md](./STANDARDS/ADR_GUIDE.md) - When/how to write ADRs (read before making architectural decisions)

**Priority:** 🔴 **CRITICAL** - AI assistants MUST read these first for any code task

---

### 2. Getting Started (Setup & Orientation)

**Location:** `docs/GETTING_STARTED/`

Onboarding and environment setup:

- [CURRENT, 2025-01-17, Guide] [GLOSSARY.md](./GETTING_STARTED/GLOSSARY.md) - Trading and ML terminology (all developers)
- [CURRENT, 2025-10-21, Guide] [LOGGING_GUIDE.md](./GETTING_STARTED/LOGGING_GUIDE.md) - Structured logging patterns and best practices
- [CURRENT, 2025-10-18, Guide] [PROJECT_STATUS.md](./GETTING_STARTED/PROJECT_STATUS.md) - Current implementation status (all stakeholders)
- [CURRENT, 2025-01-17, Guide] [REPO_MAP.md](./GETTING_STARTED/REPO_MAP.md) - Codebase navigation guide (new developers)
- [CURRENT, 2025-01-17, Guide] [SETUP.md](./GETTING_STARTED/SETUP.md) - Development environment setup (new developers)
- [CURRENT, 2025-01-17, Guide] [TESTING_SETUP.md](./GETTING_STARTED/TESTING_SETUP.md) - Test environment configuration (QA, developers)

**Priority:** 🟡 **HIGH** - Read during onboarding or when confused about structure

---

### 2.5. Workflow Guides (Development Process)

**Location:** `.claude/workflows/`

Step-by-step procedures for development workflows:

- [CURRENT, 2025-10-31, Workflow] [README.md](../.claude/workflows/README.md) - Workflow index and quick reference
- [CURRENT, 2025-10-31, Workflow] [00-analysis-checklist.md](../.claude/workflows/00-analysis-checklist.md) - Pre-implementation analysis (MANDATORY)
- [CURRENT, 2025-10-21, Workflow] [01-git.md](../.claude/workflows/01-git.md) - Git workflow: progressive commits and pull request creation
- [CURRENT, 2025-10-24, Workflow] [02-planning.md](../.claude/workflows/02-planning.md) - Task decomposition, subfeature branching, and task document review
- [CURRENT, 2025-10-27, Workflow] [03-reviews.md](../.claude/workflows/03-reviews.md) - Zen-mcp reviews: quick pre-commit (codex) and deep pre-PR (gemini)
- [CURRENT, 2025-10-21, Workflow] [04-development.md](../.claude/workflows/04-development.md) - Test execution, debugging procedures, and documentation writing
- [CURRENT, 2025-10-18, Workflow] [05-operations.md](../.claude/workflows/05-operations.md) - Operations: ADRs, deployment, CI triage, environment setup, phase/task management
- [CURRENT, 2025-10-29, Workflow] [08-session-management.md](../.claude/workflows/08-session-management.md) - Auto-resume from task state and task state tracking
- [CURRENT, 2025-11-01, Workflow] [16-pr-review-comment-check.md](../.claude/workflows/16-pr-review-comment-check.md) - Systematic PR review comment addressing
- [CURRENT, 2025-11-15, Workflow] [16-subagent-delegation.md](../.claude/workflows/16-subagent-delegation.md) - Context monitoring and subagent delegation at 70%+ usage
- [CURRENT, 2025-11-15, Workflow] [17-automated-analysis.md](../.claude/workflows/17-automated-analysis.md) - Automated pre-implementation analysis checklist execution
- [CURRENT, 2025-10-24, Workflow] [12-component-cycle.md](../.claude/workflows/12-component-cycle.md) - 4-step component development cycle
- [CURRENT, 2025-11-01, Reference] [_common/clink-policy.md](../.claude/workflows/_common/clink-policy.md) - Clink-only tool usage policy for zen-mcp
- [CURRENT, 2025-11-01, Reference] [_common/git-commands.md](../.claude/workflows/_common/git-commands.md) - Git operations and branch naming conventions
- [CURRENT, 2025-11-01, Reference] [_common/test-commands.md](../.claude/workflows/_common/test-commands.md) - Testing commands and CI workflows
- [CURRENT, 2025-11-01, Reference] [_common/zen-review-process.md](../.claude/workflows/_common/zen-review-process.md) - Three-tier zen-mcp review system
- [DRAFT, 2025-10-18, Template] [02-planning.md](../.claude/workflows/02-planning.md) - Workflow template

**Priority:** 🔴 **CRITICAL** - Follow workflows for all development activities

---

### 3. Architecture & Decisions (ADRs)

**Location:** `docs/ADRs/`

Architectural Decision Records documenting **why** technical choices were made:

- [CURRENT, 2025-11-15, Index] [README.md](./ADRs/README.md) - ADR index and overview

| ADR | Decision | Status |
|-----|----------|--------|
| [0000](./ADRs/0000-template.md) | ADR template | 📝 Template |
| [0001](./ADRs/0001-data-pipeline-architecture.md) | Data pipeline (Polars, Parquet, backward adjustment) | ✅ Accepted |
| [0002](./ADRs/0002-exception-hierarchy.md) | Exception hierarchy for data quality | ✅ Accepted |
| [0003](./ADRs/0003-baseline-strategy-with-qlib-and-mlflow.md) | Qlib + MLflow for baseline strategy | ✅ Accepted |
| [0004](./ADRs/0004-signal-service-architecture.md) | Signal service design (FastAPI, hot reload) | ✅ Accepted |
| [0005](./ADRs/0005-centralized-logging-architecture.md) | Centralized logging architecture | ✅ Accepted |
| [0014](./ADRs/0014-execution-gateway-architecture.md) | Execution gateway (idempotency, DRY_RUN) | ✅ Accepted |
| [0006](./ADRs/0006-orchestrator-service.md) | Orchestrator service (async, position sizing) | ✅ Accepted |
| [0007](./ADRs/0007-paper-run-automation.md) | Paper run automation (CLI script vs service) | ✅ Accepted |
| [0008](./ADRs/0008-enhanced-pnl-calculation.md) | Enhanced P&L calculation (realized/unrealized) | ✅ Accepted |
| [0009](./ADRs/0009-redis-integration.md) | Redis integration (feature cache, event bus) | ✅ Accepted |
| [0010](./ADRs/0010-realtime-market-data.md) | Real-time market data streaming | 🚧 Proposed |
| [0011](./ADRs/0011-risk-management-system.md) | Risk management system | 🚧 Proposed |
| [0012](./ADRs/0012-prometheus-grafana-monitoring.md) | Prometheus and Grafana monitoring | ✅ Accepted |
| [0013](./ADRs/0013-workflow-automation-gates.md) | Workflow automation gates | ✅ Accepted |
| [0015](./ADRs/0015-twap-order-slicer.md) | TWAP order slicer with APScheduler | ✅ Accepted |
| [0016](./ADRs/0016-multi-alpha-allocation.md) | Multi-alpha capital allocation system | ✅ Accepted |
| [0017](./ADRs/0017-secrets-management.md) | Secrets management with Google Cloud Secret Manager | ✅ Accepted |

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
| [centralized-logging.md](./CONCEPTS/centralized-logging.md) | Centralized logging patterns | Intermediate |
| [distributed-tracing.md](./CONCEPTS/distributed-tracing.md) | Distributed tracing for microservices | Advanced |
| [duckdb-basics.md](./CONCEPTS/duckdb-basics.md) | DuckDB embedded analytics | Beginner |
| [execution-algorithms.md](./CONCEPTS/execution-algorithms.md) | Order execution strategies (TWAP, VWAP) | Intermediate |
| [hot-reload.md](./CONCEPTS/hot-reload.md) | Zero-downtime model updates | Advanced |
| [feature-parity.md](./CONCEPTS/feature-parity.md) | Research-production consistency | Advanced |
| [monitoring-and-observability.md](./CONCEPTS/monitoring-and-observability.md) | Metrics, logs, and traces | Intermediate |
| [multi-alpha-allocation.md](./CONCEPTS/multi-alpha-allocation.md) | Multi-strategy capital allocation | Advanced |
| [parquet-format.md](./CONCEPTS/parquet-format.md) | Columnar storage format | Beginner |
| [python-testing-tools.md](./CONCEPTS/python-testing-tools.md) | pytest and testing frameworks | Beginner |
| [redis-patterns.md](./CONCEPTS/redis-patterns.md) | Redis caching and event patterns | Intermediate |
| [risk-management.md](./CONCEPTS/risk-management.md) | Position limits and circuit breakers | Intermediate |
| [sql-analytics-patterns.md](./CONCEPTS/sql-analytics-patterns.md) | SQL window functions and CTEs | Intermediate |
| [structured-logging.md](./CONCEPTS/structured-logging.md) | JSON logging with context | Intermediate |
| [webhook-security.md](./CONCEPTS/webhook-security.md) | HMAC signature verification | Advanced |
| [websocket-streaming.md](./CONCEPTS/websocket-streaming.md) | Real-time data streaming | Intermediate |
| [workflow-optimization-zen-mcp.md](./CONCEPTS/workflow-optimization-zen-mcp.md) | Zen-MCP workflow integration | Advanced |
| [zen-mcp-clink-optimization-proposal.md](./CONCEPTS/zen-mcp-clink-optimization-proposal.md) | Clink optimization proposal | Advanced |
| [zen-mcp-integration-proposal.md](./CONCEPTS/zen-mcp-integration-proposal.md) | Zen-MCP integration design | Advanced |

**Priority:** 🟢 **LOW** - Read when you need to understand domain-specific concepts

---

### 5. Implementation Guides (How-To)

**Location:** `docs/IMPLEMENTATION_GUIDES/`

Step-by-step implementation instructions for each major task:

| Guide | Task | Lines | Test Coverage |
|-------|------|-------|---------------|
| [P0T1: Data ETL Pipeline](./TASKS/P0T1_DONE.md) | Data ETL pipeline | 800+ | 53 tests, 100% |
| [P1T1: Redis Integration](./TASKS/P1T1_DONE.md) | Redis feature cache & event bus | 850+ | 85 tests, 100% |
| [P0T2: Baseline Qlib Strategy](./TASKS/P0T2_DONE.md) | Baseline ML strategy | 700+ | Unit tests |
| [P0T3: Signal Service](./TASKS/P0T3_DONE.md) | Signal service (main guide) | 1,940+ | 57 tests, 95% |
| [P0T3-F4: FastAPI Application](./TASKS/P0T3-F4_DONE.md) | FastAPI implementation | 600+ | Phase 4 tests |
| [P0T3-F5: Model Hot Reload](./TASKS/P0T3-F5_DONE.md) | Hot reload mechanism | 500+ | Phase 5 tests |
| [P0T3-F6: Integration Tests](./TASKS/P0T3-F6_DONE.md) | Integration testing | 400+ | Phase 6 tests |
| [P0T4: Execution Gateway](./TASKS/P0T4_DONE.md) | Execution gateway | 827+ | 56 tests, 100% |
| [P0T5: Trade Orchestrator](./TASKS/P0T5_DONE.md) | Orchestrator service | 754+ | 13 tests, 100% |
| [P0T6: Paper Trading Runner](./TASKS/P0T6_DONE.md) | Paper run automation | 1,059+ | 26 tests, 100% |

**Priority:** 🟡 **HIGH** - Read the relevant guide when implementing or modifying a task

---

### 6. Tasks & Planning

**Location:** `docs/TASKS/`

Current and future work items organized by phase:

**Templates:**
- [Template, 2025-10-18, Template] [00-TEMPLATE_DONE.md](./TASKS/00-TEMPLATE_DONE.md) - Template for completed task documents
- [Template, 2025-10-18, Template] [00-TEMPLATE_FEATURE.md](./TASKS/00-TEMPLATE_FEATURE.md) - Template for feature-level task documents
- [Template, 2025-10-18, Template] [00-TEMPLATE_PHASE_PLANNING.md](./TASKS/00-TEMPLATE_PHASE_PLANNING.md) - Template for phase planning documents
- [Template, 2025-10-18, Template] [00-TEMPLATE_PROGRESS.md](./TASKS/00-TEMPLATE_PROGRESS.md) - Template for in-progress task documents
- [Template, 2025-10-18, Template] [00-TEMPLATE_TASK.md](./TASKS/00-TEMPLATE_TASK.md) - Template for new task documents
- [Template, 2025-11-15, Template] [00-PLANNING_WORKFLOW_TEMPLATE.md](./TASKS/00-PLANNING_WORKFLOW_TEMPLATE.md) - Template for planning workflow documentation

**Phase Planning:**
- [CURRENT, 2025-10-18, Planning] [P0_TASKS.md](./TASKS/P0_TASKS.md) - MVP core tasks (P0T1-P0T6) - ✅ 100% Complete
- [CURRENT, 2025-10-26, Planning] [P1_PLANNING.md](./TASKS/P1_PLANNING.md) - P1 roadmap and priorities - 🔄 73% Complete (8/11 tasks)
- [CURRENT, 2025-10-26, Planning] [P2_PLANNING.md](./TASKS/P2_PLANNING.md) - P2 advanced features planning - 📋 0% (0/6 tasks)
- [CURRENT, 2025-10-18, Planning] [INDEX.md](./TASKS/INDEX.md) - Task index and status tracker
- [CURRENT, 2025-10-18, Planning] [trading_platform_realization_plan.md](./trading_platform_realization_plan.md) - Original master plan (reference)

**Phase 1 Tasks:**
- [CURRENT, 2025-10-18, Task] [P1T0_DONE.md](./TASKS/P1T0_DONE.md) - Phase 1 initialization and planning
- [CURRENT, 2025-10-18, Task] [P1T2_DONE.md](./TASKS/P1T2_DONE.md) - Task tracking and CLI tools
- [CURRENT, 2025-10-18, Task] [P1T3_DONE.md](./TASKS/P1T3_DONE.md) - DuckDB analytics layer
- [CURRENT, 2025-10-19, Task] [P1T4_DONE.md](./TASKS/P1T4_DONE.md) - Multi-model code review system
- [CURRENT, 2025-10-20, Task] [P1T5-F1_DONE.md](./TASKS/P1T5-F1_DONE.md) - Real-time market data subfeature 1
- [CURRENT, 2025-10-20, Task] [P1T5-F3_DONE.md](./TASKS/P1T5-F3_DONE.md) - Real-time market data subfeature 3
- [CURRENT, 2025-10-20, Task] [P1T6_DONE.md](./TASKS/P1T6_DONE.md) - Risk management implementation
- [CURRENT, 2025-10-20, Task] [P1T7_DONE.md](./TASKS/P1T7_DONE.md) - Reconciliation system hardening
- [CURRENT, 2025-10-21, Task] [P1T8_DONE.md](./TASKS/P1T8_DONE.md) - Monitoring and alerting
- [CURRENT, 2025-10-21, Task] [P1T9_DONE.md](./TASKS/P1T9_DONE.md) - Centralized logging infrastructure
- [CURRENT, 2025-10-25, Task] [P1T10_DONE.md](./TASKS/P1T10_DONE.md) - Multi-alpha capital allocation system
- [CURRENT, 2025-10-27, Task] [P1T11_DONE.md](./TASKS/P1T11_DONE.md) - Workflow automation and testing gates
- [CURRENT, 2025-10-29, Task] [P1T12_DONE.md](./TASKS/P1T12_DONE.md) - Auto-resume task state tracking
- [CURRENT, 2025-10-31, Task] [P1T13_TASK.md](./TASKS/P1T13_TASK.md) - Documentation and workflow optimization (current task)
- [CURRENT, 2025-11-15, Task] [P1T13_F3_DONE.md](./TASKS/P1T13_F3_DONE.md) - P1T13 Feature 3: Systematic PR review comment addressing workflow
- [CURRENT, 2025-11-15, Task] [P1T13_F4_PROGRESS.md](./TASKS/P1T13_F4_PROGRESS.md) - P1T13 Feature 4: Dependency validation and auto-update system (in progress)
- [CURRENT, 2025-11-15, Task] [P1T13-F5_TASK.md](./TASKS/P1T13-F5_TASK.md) - P1T13 Feature 5: Workflow refinement phase 1 (Phase 0-A2 in progress)

**Phase 2 Tasks:**
- [CURRENT, 2025-10-26, Task] [P2T0_DONE.md](./TASKS/P2T0_DONE.md) - TWAP order slicer implementation
- [CURRENT, 2025-10-26, Task] [P2T1_DONE.md](./TASKS/P2T1_DONE.md) - Advanced order types and execution
- [CURRENT, 2025-11-15, Task] [P2T2_TASK.md](./TASKS/P2T2_TASK.md) - Secrets management with Google Cloud Secret Manager
- [CURRENT, 2025-11-17, Task] [P2T3_TASK.md](./TASKS/P2T3_TASK.md) - Web console for operational oversight and manual intervention

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

- [CURRENT, 2025-10-25, Retrospective] [AUDIT_REPORT_2025-10-25.md](./LESSONS_LEARNED/AUDIT_REPORT_2025-10-25.md) - Workflow audit identifying verbose workflows and redundancy (P1T12)
- [CURRENT, 2025-10-18, Retrospective] [automated-code-review-fixes-p1.1t3.md](./LESSONS_LEARNED/automated-code-review-fixes-p1.1t3.md) - Security vulnerabilities and code quality fixes from automated reviewers (P1.1T3)
- [CURRENT, 2025-10-19, Retrospective] [mypy-strict-migration.md](./LESSONS_LEARNED/mypy-strict-migration.md) - Comprehensive mypy --strict migration fixing 279 type errors across 67 files
- [CURRENT, 2025-10-20, Retrospective] [p1.3t1-monitoring-alerting.md](./LESSONS_LEARNED/p1.3t1-monitoring-alerting.md) - Monitoring and alerting implementation with Prometheus and Grafana (P1.3T1)
- [CURRENT, 2025-10-18, Retrospective] [p1-p3-testing-journey.md](./LESSONS_LEARNED/p1-p3-testing-journey.md) - Testing strategy evolution from P1 to P3
- [CURRENT, 2025-10-18, Retrospective] [t1.2-redis-integration-fixes.md](./LESSONS_LEARNED/t1.2-redis-integration-fixes.md) - Redis integration fixes and graceful degradation (T1.2)
- [CURRENT, 2025-10-18, Retrospective] [t6-paper-run-retrospective.md](./LESSONS_LEARNED/t6-paper-run-retrospective.md) - Paper run retrospective with MVP simplifications (T6)

**Priority:** 🟢 **LOW** - Read after completing tasks to learn from past experiences

---

### 8. Runbooks (Operations)

**Location:** `docs/RUNBOOKS/`

Operational procedures and troubleshooting:

- [CURRENT, 2025-10-21, Runbook] [logging-queries.md](./RUNBOOKS/logging-queries.md) - Common LogQL queries for debugging production issues with Loki
- [CURRENT, 2025-10-20, Runbook] [ops.md](./RUNBOOKS/ops.md) - Core operational procedures for deployment and troubleshooting
- [CURRENT, 2025-11-15, Runbook] [secret-rotation.md](./RUNBOOKS/secret-rotation.md) - Secret rotation procedures for Google Cloud Secret Manager
- [CURRENT, 2025-11-15, Runbook] [secrets-migration.md](./RUNBOOKS/secrets-migration.md) - Migration from .env to Google Cloud Secret Manager
- [CURRENT, 2025-10-20, Runbook] [staging-deployment.md](./RUNBOOKS/staging-deployment.md) - Staging environment deployment, credentials, and rollback procedures
- [CURRENT, 2025-11-17, Runbook] [web-console-user-guide.md](./RUNBOOKS/web-console-user-guide.md) - Web console usage, authentication, manual order entry, kill switch operations

**Priority:** 🟡 **HIGH** - Read when deploying or troubleshooting production issues

---

### 8.5. Incidents (Post-Mortems)

**Location:** `docs/INCIDENTS/`

Incident reports and post-mortem analysis:

- [CURRENT, 2025-11-15, Index] [README.md](./INCIDENTS/README.md) - Incident index and post-mortem template

**Priority:** 🟢 **LOW** - Read after incidents to learn from failures

---

### 9. Configuration and Tooling

**Location:** `.claude/`, `.github/`, `prompts/`, `strategies/`

Configuration files, templates, prompts, and tooling:

**.claude/ Configuration:**
- [CURRENT, 2025-10-31, Guide] [AUTO_RESUME.md](../.claude/AUTO_RESUME.md) - Auto-resume task state tracking configuration
- [CURRENT, 2025-10-27, Guide] [TROUBLESHOOTING.md](../.claude/TROUBLESHOOTING.md) - Troubleshooting guide for Claude Code workflows and zen-mcp integration
- [CURRENT, 2025-10-26, Guide] [commands/zen-review.md](../.claude/commands/zen-review.md) - Zen-mcp review slash command configuration
- [CURRENT, 2025-10-25, Guide] [state/README.md](../.claude/state/README.md) - Task state tracking system documentation
- [CURRENT, 2025-11-15, Guide] [checkpoints/README.md](../.claude/checkpoints/README.md) - Context checkpointing system for session delegation

**.claude/audits/ (Code Audits):**
- [CURRENT, 2025-11-15, Audit] [audits/P1T13-F5-phase0-findings.md](../.claude/audits/P1T13-F5-phase0-findings.md) - P1T13-F5 Phase 0 audit findings
- [CURRENT, 2025-11-15, Audit] [audits/P1T13-F5-phase0-fixes-summary.md](../.claude/audits/P1T13-F5-phase0-fixes-summary.md) - P1T13-F5 Phase 0 fixes summary

**.claude/implementation-plans/ (Implementation Plans):**
- [CURRENT, 2025-11-15, Plan] [implementation-plans/P1T13-F5-phase1-implementation-plan.md](../.claude/implementation-plans/P1T13-F5-phase1-implementation-plan.md) - P1T13-F5 Phase 1 implementation plan

**.claude/research/ (Research Documents):**
- [CURRENT, 2025-11-15, Research] [research/automated-coding-research.md](../.claude/research/automated-coding-research.md) - Automated coding workflow research
- [CURRENT, 2025-11-15, Research] [research/automated-planning-research.md](../.claude/research/automated-planning-research.md) - Automated planning system research
- [CURRENT, 2025-11-15, Research] [research/context-optimization-measurement.md](../.claude/research/context-optimization-measurement.md) - Context optimization and measurement techniques
- [CURRENT, 2025-11-15, Research] [research/delegation-decision-tree.md](../.claude/research/delegation-decision-tree.md) - Subagent delegation decision framework
- [CURRENT, 2025-11-15, Research] [research/P1T13-workflow-simplification-analysis.md](../.claude/research/P1T13-workflow-simplification-analysis.md) - P1T13 workflow simplification analysis
- [CURRENT, 2025-11-15, Research] [research/subagent-capabilities-research.md](../.claude/research/subagent-capabilities-research.md) - Subagent capabilities and limitations

**tests/ci/ (CI Test Documentation):**
- [CURRENT, 2025-11-15, Test] [../tests/ci/test_workflow_config.md](../tests/ci/test_workflow_config.md) - CI configuration validation and manual testing procedures

**.claude/prompts/ (Clink Review Templates):**
- [CURRENT, 2025-10-27, Template] [prompts/clink-reviews/quick-safety-review.md](../.claude/prompts/clink-reviews/quick-safety-review.md) - Quick safety review prompt template for clink + codex
- [CURRENT, 2025-10-27, Template] [prompts/clink-reviews/deep-architecture-review.md](../.claude/prompts/clink-reviews/deep-architecture-review.md) - Deep architecture review prompt template for clink + gemini
- [CURRENT, 2025-10-27, Template] [prompts/clink-reviews/security-audit.md](../.claude/prompts/clink-reviews/security-audit.md) - Security audit prompt template for clink + gemini
- [CURRENT, 2025-10-27, Template] [prompts/clink-reviews/task-creation-review.md](../.claude/prompts/clink-reviews/task-creation-review.md) - Task creation review prompt template for clink + gemini
- [CURRENT, 2025-10-27, Template] [prompts/pr-body-template.md](../.claude/prompts/pr-body-template.md) - Pull request body template

**.claude/examples/ (Usage Examples):**
- [CURRENT, 2025-10-26, Example] [examples/git-pr/example-standard-pr-creation.md](../.claude/examples/git-pr/example-standard-pr-creation.md) - Standard PR creation example
- [CURRENT, 2025-10-26, Example] [examples/git-pr/example-review-feedback-loop.md](../.claude/examples/git-pr/example-review-feedback-loop.md) - Review feedback loop example
- [CURRENT, 2025-10-26, Example] [examples/git-pr/good-pr-description-template.md](../.claude/examples/git-pr/good-pr-description-template.md) - Good PR description template

**.claude/snippets/ (Reusable Snippets):**
- [CURRENT, 2025-10-27, Snippet] [snippets/clink-only-warning.md](../.claude/snippets/clink-only-warning.md) - Warning snippet about clink-only tool usage policy

**.github/ Templates:**
- [CURRENT, 2025-10-26, Template] [pull_request_template.md](../.github/pull_request_template.md) - GitHub pull request template

**prompts/ (AI Assistant Prompts):**
- [CURRENT, 2025-10-18, Guide] [assistant_rules.md](../prompts/assistant_rules.md) - Original AI assistant guidance (superseded by CLAUDE.md and docs/AI_GUIDE.md)
- [CURRENT, 2025-10-18, Template] [implement_ticket.md](../prompts/implement_ticket.md) - Ticket implementation prompt template

**strategies/ (Strategy Documentation):**
- [CURRENT, 2025-10-20, Guide] [alpha_baseline/README.md](../strategies/alpha_baseline/README.md) - Alpha baseline strategy documentation
- [CURRENT, 2025-10-20, Guide] [mean_reversion/README.md](../strategies/mean_reversion/README.md) - Mean reversion strategy documentation (placeholder)
- [CURRENT, 2025-10-20, Guide] [momentum/README.md](../strategies/momentum/README.md) - Momentum strategy documentation (placeholder)

**tests/strategies/ (Test Documentation):**
- [CURRENT, 2025-10-20, Guide] [alpha_baseline/README.md](../tests/strategies/alpha_baseline/README.md) - Alpha baseline strategy test documentation

**Priority:** 🟢 **LOW** - Reference as needed for configuration and templates

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
| Implement P0T6 | [p0t6-paper-run.md](./TASKS/P0T6_DONE.md) |
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

### Update Policy for INDEX.md

**When to update:**
- **New document added** → Add entry with metadata in relevant category
- **Document modified** → Update date field in metadata
- **Document deprecated** → Change status to [OUTDATED], link to replacement
- **Quarterly review** → Refresh all metadata dates (every 3 months)
- **Category restructuring** → Update Quick Links and navigation

**Metadata format:**
```
- [STATUS, YYYY-MM-DD, TYPE] Filename.md - Description with path
```

Example: `- [CURRENT, 2025-01-15, Standard] [CODING_STANDARDS.md](./STANDARDS/CODING_STANDARDS.md) - Python style guide`

**Status values:**
- `CURRENT` - Active, up-to-date document
- `OUTDATED` - Superseded or needs refresh
- `DRAFT` - Work in progress

**Type values:**
- `Standard` - Normative rules (STANDARDS/)
- `Guide` - How-to documentation (GETTING_STARTED/, TASKS/)
- `ADR` - Architecture decisions (ADRs/)
- `Concept` - Domain knowledge (CONCEPTS/)
- `Runbook` - Operations (RUNBOOKS/)

### When to Update This Index

- New document added → Add to relevant category with metadata
- Document moved → Update all references and paths
- New category needed → Add section with description
- Document deprecated → Mark status as [OUTDATED] and link to replacement
- Quarterly review → Update metadata dates for all active documents

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
- **Stuck implementing?** Follow the [IMPLEMENTATION_GUIDE](./TASKS/)
- **Need context?** Read [CONCEPTS](./CONCEPTS/) for domain knowledge
- **Lost in the codebase?** See [REPO_MAP.md](./GETTING_STARTED/REPO_MAP.md)

---

**Last Updated:** 2025-10-31
**Maintained By:** Development Team
**Format Version:** 1.2 (Added metadata, Quick Links, Update Policy - P1T13)
