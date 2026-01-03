# Documentation Index

**Canonical Entry Point for AI Coding Assistants and Developers**

This index provides a structured guide to all documentation, organized by purpose and priority. For AI assistants: **start here** to understand which documents are normative (must-follow) versus informational (context).

---

## 📌 Quick Links (AI Navigation)

**Essential First Reads:**
- [INDEX.md](./INDEX.md) (this file) → Documentation structure and navigation
- [AI_GUIDE.md](./AI/AI_GUIDE.md) → AI assistant quick-start and discovery patterns
- [AI/](./AI/) → All AI workflows, prompts, research, and examples
- [AI/Workflows/](./AI/Workflows/) → Step-by-step development workflows
- [STANDARDS/](./STANDARDS/) → Normative standards directory (MUST follow)
- [SPECS/](./SPECS/) → Technical specifications (services, libraries, strategies)

**By Task Type:**
- **New Feature Implementation** → TASKS/ → ADRs/ → CONCEPTS/ → STANDARDS/
- **Bug Fix** → RUNBOOKS/ops.md → Relevant implementation guide
- **Architecture Change** → STANDARDS/ADR_GUIDE.md → ADRs/ → Write new ADR
- **Documentation** → STANDARDS/DOCUMENTATION_STANDARDS.md → Update relevant docs
- **Testing** → STANDARDS/TESTING.md → Write tests

**By Document Type:**
- **Standards** (mandatory rules) → [STANDARDS](#1-normative-standards-must-follow)
- **Architecture** (decisions and rationale) → [ADRs/](#3-architecture--decisions-adrs)
- **Architecture Docs** (system overviews and diagrams) → [ARCHITECTURE/](#35-architecture-documentation)
- **Specifications** (service/library behavior) → [SPECS/](#225-technical-specifications)
- **Concepts** (domain knowledge) → [CONCEPTS/](#4-domain-concepts-trading--ml-knowledge)
- **Tasks** (work tracking) → [TASKS/](#6-tasks--planning)
- **Archive** (completed tasks + legacy plans) → [ARCHIVE/](#65-archive)
- **Runbooks** (operations) → [RUNBOOKS/](#8-runbooks-operations)

---

## 🎯 Quick Start for AI Assistants

**When starting a new task, read in this order:**

1. **[STANDARDS/](./STANDARDS/)** - Normative guidance (MUST follow)
2. **[TASKS/](./TASKS/)** - Current work items
3. **[Task Implementation Guides](./ARCHIVE/TASKS_HISTORY/)** - Completed task how-to references
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

### 1. Normative Standards (MUST Follow)

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

### 2.25. Technical Specifications

**Location:** `docs/SPECS/`

Centralized, code-adjacent specifications for services, libraries, strategies, and infrastructure:

- [CURRENT, 2026-01-02, Spec] [SPECS/README.md](./SPECS/README.md) - Spec overview, template reference, and coverage index
- [CURRENT, 2026-01-02, Spec] [SPECS/DATA_MODELS.md](./SPECS/DATA_MODELS.md) - Pydantic models, database schemas, and TypedDicts catalog
- [CURRENT, 2026-01-02, Spec] [SPECS/EVENTS.md](./SPECS/EVENTS.md) - Redis pub/sub channels, streams, and event payloads
- [CURRENT, 2026-01-02, Spec] [SPECS/SCHEMAS.md](./SPECS/SCHEMAS.md) - OpenAPI/FastAPI endpoint schemas and contracts
- [CURRENT, 2026-01-02, Spec] [SPECS/SYSTEM_MECHANISMS.md](./SPECS/SYSTEM_MECHANISMS.md) - Critical process walkthroughs with sequence diagrams
- [Services](./SPECS/README.md#services-apps) - Individual service specs (indexed in README)
- [Libraries](./SPECS/README.md#libraries-libs) - Individual library specs (indexed in README)
- [Strategies](./SPECS/README.md#strategies-strategies) - Individual strategy specs (indexed in README)
- [Infrastructure](./SPECS/README.md#infrastructure) - Infrastructure specs (indexed in README)

**Priority:** 🟡 **HIGH** - Use when implementing or reviewing component behavior

---

### 2.5. Workflow Guides (Development Process)

**Location:** `docs/AI/Workflows/`

Step-by-step procedures for development workflows:

- [CURRENT, 2025-10-31, Workflow] [README.md](./AI/Workflows/README.md) - Workflow index and quick reference
- [CURRENT, 2025-10-31, Workflow] [00-analysis-checklist.md](./AI/Workflows/00-analysis-checklist.md) - Pre-implementation analysis (MANDATORY)
- [CURRENT, 2025-10-21, Workflow] [01-git.md](./AI/Workflows/01-git.md) - Git workflow: progressive commits and pull request creation
- [CURRENT, 2025-10-24, Workflow] [02-planning.md](./AI/Workflows/02-planning.md) - Task decomposition, subfeature branching, and task document review
- [CURRENT, 2025-11-21, Workflow] [03-reviews.md](./AI/Workflows/03-reviews.md) - Zen-mcp comprehensive reviews: independent Gemini + Codex reviews for all commits and PRs
- [CURRENT, 2025-10-21, Workflow] [04-development.md](./AI/Workflows/04-development.md) - Test execution, debugging procedures, and documentation writing
- [CURRENT, 2025-10-18, Workflow] [05-operations.md](./AI/Workflows/05-operations.md) - Operations: ADRs, deployment, CI triage, environment setup, phase/task management
- [CURRENT, 2025-12-31, Workflow] [06-repomix.md](./AI/Workflows/06-repomix.md) - Repomix integration for AI-optimized codebase analysis and context generation
- [CURRENT, 2025-10-29, Workflow] [08-session-management.md](./AI/Workflows/08-session-management.md) - Auto-resume from task state and task state tracking
- [CURRENT, 2025-11-01, Workflow] [16-pr-review-comment-check.md](./AI/Workflows/16-pr-review-comment-check.md) - Systematic PR review comment addressing
- [CURRENT, 2025-11-15, Workflow] [16-subagent-delegation.md](./AI/Workflows/16-subagent-delegation.md) - Context monitoring and subagent delegation at 70%+ usage
- [CURRENT, 2025-11-15, Workflow] [17-automated-analysis.md](./AI/Workflows/17-automated-analysis.md) - Automated pre-implementation analysis checklist execution
- [CURRENT, 2025-10-24, Workflow] [12-component-cycle.md](./AI/Workflows/12-component-cycle.md) - 4-step component development cycle
- [CURRENT, 2025-11-01, Reference] [_common/clink-policy.md](./AI/Workflows/_common/clink-policy.md) - Clink-only tool usage policy for zen-mcp
- [CURRENT, 2025-11-01, Reference] [_common/git-commands.md](./AI/Workflows/_common/git-commands.md) - Git operations and branch naming conventions
- [CURRENT, 2025-11-01, Reference] [_common/test-commands.md](./AI/Workflows/_common/test-commands.md) - Testing commands and CI workflows
- [CURRENT, 2025-11-21, Reference] [_common/zen-review-process.md](./AI/Workflows/_common/zen-review-process.md) - Comprehensive independent review system (Gemini + Codex)
- [DRAFT, 2025-10-18, Template] [02-planning.md](./AI/Workflows/02-planning.md) - Workflow template

**Priority:** 🔴 **CRITICAL** - Follow workflows for all development activities

---

### 2.75. AI Documentation & Resources

**Location:** `docs/AI/`

Comprehensive AI assistant resources, workflows, prompts, and research:

**Main Guide:**
- [CURRENT, 2025-11-21, Guide] [AI_GUIDE.md](./AI/AI_GUIDE.md) - AI assistant quick-start and comprehensive guidance
- [CURRENT, 2025-11-21, Index] [README.md](./AI/README.md) - AI documentation structure and navigation

**Analysis:**
- [CURRENT, 2025-11-21, Index] [Analysis/README.md](./AI/Analysis/README.md) - Pre-implementation analysis artifacts index

**Audits:**
- [CURRENT, 2025-11-21, Index] [Audits/README.md](./AI/Audits/README.md) - Code audit artifacts index

**Examples:**
- [CURRENT, 2025-11-21, Index] [Examples/README.md](./AI/Examples/README.md) - AI workflow examples index
- [CURRENT, 2025-11-21, Example] [Examples/git-pr/example-review-feedback-loop.md](./AI/Examples/git-pr/example-review-feedback-loop.md) - PR review feedback loop example
- [CURRENT, 2025-11-21, Example] [Examples/git-pr/example-standard-pr-creation.md](./AI/Examples/git-pr/example-standard-pr-creation.md) - Standard PR creation example
- [CURRENT, 2025-11-21, Example] [Examples/git-pr/good-pr-description-template.md](./AI/Examples/git-pr/good-pr-description-template.md) - PR description template
- [CURRENT, 2025-11-21, Guide] [Examples/pr-guidelines.md](./AI/Examples/pr-guidelines.md) - Pull request guidelines for contributors

**Implementation Plans:**
- [CURRENT, 2025-11-17, Plan] [Implementation/P1T13-F5-phase1-implementation-plan.md](./AI/Implementation/P1T13-F5-phase1-implementation-plan.md) - P1T13-F5 Phase 1 implementation plan
- [CURRENT, 2025-11-21, Plan] [Implementation/P2T3_PHASE2_PLAN.md](./AI/Implementation/P2T3_PHASE2_PLAN.md) - P2T3 Phase 2 Web Console mTLS + JWT authentication implementation plan
- [CURRENT, 2025-11-21, Index] [Implementation/README.md](./AI/Implementation/README.md) - Implementation plan artifacts index
- [CURRENT, 2025-12-07, Plan] [ARCHIVE/PLANS/P4T2_Track1_T1.7_T1.8.md](./ARCHIVE/PLANS/P4T2_Track1_T1.7_T1.8.md) - P4T2 Track 1: Fama-French Integration (T1.7) and yfinance Integration (T1.8)
- [CURRENT, 2025-12-07, Plan] [ARCHIVE/PLANS/P4T2_Track3_T3.1.md](./ARCHIVE/PLANS/P4T2_Track3_T3.1.md) - P4T2 Track 3: T3.1 Microstructure Analytics implementation plan
- [CURRENT, 2025-12-10, Plan] [ARCHIVE/PLANS/P4T3_T6.1a_PLAN.md](./ARCHIVE/PLANS/P4T3_T6.1a_PLAN.md) - T6.1a Auth/RBAC Core implementation plan
- [CURRENT, 2025-12-10, Plan] [ARCHIVE/PLANS/P4T3_T6.1b_PLAN.md](./ARCHIVE/PLANS/P4T3_T6.1b_PLAN.md) - T6.1b Admin User Management UI implementation plan
- [CURRENT, 2025-12-14, Plan] [ARCHIVE/PLANS/P4T3_T6.5_PLAN.md](./ARCHIVE/PLANS/P4T3_T6.5_PLAN.md) - T6.5 Trade Journal & Analysis implementation plan
- [CURRENT, 2025-12-07, Plan] [ARCHIVE/PLANS/P4T1.9-ETL-Pipeline.md](./ARCHIVE/PLANS/P4T1.9-ETL-Pipeline.md) - Data storage and ETL pipeline implementation plan
- [CURRENT, 2025-12-07, Plan] [ARCHIVE/PLANS/T1.3-CRSP-Local-Provider.md](./ARCHIVE/PLANS/T1.3-CRSP-Local-Provider.md) - CRSP local data provider implementation plan
- [CURRENT, 2025-12-07, Plan] [ARCHIVE/PLANS/T1.4-Compustat-Local-Provider.md](./ARCHIVE/PLANS/T1.4-Compustat-Local-Provider.md) - Compustat local data provider implementation plan
- [CURRENT, 2025-12-07, Plan] [ARCHIVE/PLANS/T1.5-Fama-French-Local-Provider.md](./ARCHIVE/PLANS/T1.5-Fama-French-Local-Provider.md) - Fama-French local data provider implementation plan
- [CURRENT, 2025-12-07, Plan] [ARCHIVE/PLANS/T1.6-dataset-versioning.md](./ARCHIVE/PLANS/T1.6-dataset-versioning.md) - Dataset versioning implementation plan
- [CURRENT, 2025-12-10, Plan] [ARCHIVE/PLANS/T2.1-factor-model-plan.md](./ARCHIVE/PLANS/T2.1-factor-model-plan.md) - Multi-factor model construction implementation plan
- [CURRENT, 2025-12-10, Plan] [ARCHIVE/PLANS/T2.2-plan.md](./ARCHIVE/PLANS/T2.2-plan.md) - Covariance estimation (v2) implementation plan
- [CURRENT, 2025-12-10, Plan] [ARCHIVE/PLANS/T2.4-plan.md](./ARCHIVE/PLANS/T2.4-plan.md) - Portfolio optimizer and stress testing implementation plan
- [CURRENT, 2025-12-10, Plan] [ARCHIVE/PLANS/T2.6-alpha-advanced-plan.md](./ARCHIVE/PLANS/T2.6-alpha-advanced-plan.md) - Alpha advanced analytics implementation plan
- [CURRENT, 2025-12-10, Plan] [ARCHIVE/PLANS/T2.7-factor-attribution-plan.md](./ARCHIVE/PLANS/T2.7-factor-attribution-plan.md) - Factor attribution analysis implementation plan
- [CURRENT, 2025-12-08, Plan] [ARCHIVE/PLANS/T3.2_execution_quality_plan.md](./ARCHIVE/PLANS/T3.2_execution_quality_plan.md) - T3.2 Execution Quality Analysis implementation plan
- [CURRENT, 2025-12-08, Plan] [ARCHIVE/PLANS/T3.3_event_study_plan.md](./ARCHIVE/PLANS/T3.3_event_study_plan.md) - T3.3 Event Study Framework implementation plan
- [CURRENT, 2025-12-08, Plan] [ARCHIVE/PLANS/T2.5_PLAN.md](./ARCHIVE/PLANS/T2.5_PLAN.md) - T2.5 Alpha Research Framework implementation plan
- [CURRENT, 2025-12-08, Plan] [ARCHIVE/PLANS/T2.8_PLAN.md](./ARCHIVE/PLANS/T2.8_PLAN.md) - T2.8 Model Registry implementation plan
- [CURRENT, 2025-12-11, Plan] [ARCHIVE/PLANS/T6.2_Performance_Dashboard_Plan.md](./ARCHIVE/PLANS/T6.2_Performance_Dashboard_Plan.md) - T6.2 Performance Dashboard implementation plan
- [CURRENT, 2025-12-13, Plan] [ARCHIVE/PLANS/T6.3_RISK_DASHBOARD_PLAN.md](./ARCHIVE/PLANS/T6.3_RISK_DASHBOARD_PLAN.md) - T6.3 Risk Analytics Dashboard implementation plan
- [CURRENT, 2025-12-13, Plan] [ARCHIVE/PLANS/T6.4-implementation-plan.md](./ARCHIVE/PLANS/T6.4-implementation-plan.md) - T6.4 Strategy Comparison Tool & Risk Dashboard DB integration plan
- [CURRENT, 2025-12-15, Plan] [ARCHIVE/PLANS/T6.6-Backend-API-Manual-Controls.md](./ARCHIVE/PLANS/T6.6-Backend-API-Manual-Controls.md) - T6.6 Backend API Manual Trade Controls implementation plan
- [CURRENT, 2025-12-15, Plan] [ARCHIVE/PLANS/T6.6-Web-Console-UI-Manual-Controls.md](./ARCHIVE/PLANS/T6.6-Web-Console-UI-Manual-Controls.md) - T6.6 Web Console UI Manual Trade Controls implementation plan
- [CURRENT, 2025-12-14, Plan] [TASKS/FORMATTING_ENHANCEMENT_PLAN.md](./TASKS/FORMATTING_ENHANCEMENT_PLAN.md) - Code formatting enhancement plan (ruff config, Makefile improvements)
- [CURRENT, 2025-12-17, Task] [TASKS/BUGFIX_RELIABILITY_SAFETY_IMPROVEMENTS.md](./TASKS/BUGFIX_RELIABILITY_SAFETY_IMPROVEMENTS.md) - Reliability and safety bugfixes (reconciliation, hydration, shadow mode)
- [CURRENT, 2025-12-18, Task] [TASKS/BUGFIX_CODE_REVIEW_CONCERNS.md](./TASKS/BUGFIX_CODE_REVIEW_CONCERNS.md) - Address code review concerns (stale slice expiry, auth, recovery manager)

**Prompts:**
- [CURRENT, 2025-11-21, Index] [Prompts/README.md](./AI/Prompts/README.md) - Reusable AI prompts index
- [CURRENT, 2025-11-21, Prompt] [Prompts/assistant-rules.md](./AI/Prompts/assistant-rules.md) - AI assistant behavioral rules
- [CURRENT, 2025-11-21, Prompt] [Prompts/implement-ticket.md](./AI/Prompts/implement-ticket.md) - Ticket implementation prompt
- [CURRENT, 2025-11-21, Prompt] [Prompts/pr-body-template.md](./AI/Prompts/pr-body-template.md) - Pull request body template
- [CURRENT, 2025-11-21, Prompt] [Prompts/clink-reviews/deep-architecture-review.md](./AI/Prompts/clink-reviews/deep-architecture-review.md) - Deep architecture review prompt (deprecated)
- [CURRENT, 2025-11-21, Prompt] [Prompts/clink-reviews/quick-safety-review.md](./AI/Prompts/clink-reviews/quick-safety-review.md) - Quick safety review prompt (deprecated)
- [CURRENT, 2025-11-21, Prompt] [Prompts/clink-reviews/security-audit.md](./AI/Prompts/clink-reviews/security-audit.md) - Security audit review prompt
- [CURRENT, 2025-11-21, Prompt] [Prompts/clink-reviews/task-creation-review.md](./AI/Prompts/clink-reviews/task-creation-review.md) - Task creation review prompt

**Research:**
- [CURRENT, 2025-11-21, Index] [Research/README.md](./AI/Research/README.md) - AI workflow research index
- [CURRENT, 2025-11-21, Research] [Research/automated-coding-research.md](./AI/Research/automated-coding-research.md) - Automated coding workflow research
- [CURRENT, 2025-11-21, Research] [Research/automated-planning-research.md](./AI/Research/automated-planning-research.md) - Automated planning workflow research
- [CURRENT, 2025-11-21, Research] [Research/context-optimization-measurement.md](./AI/Research/context-optimization-measurement.md) - Context window optimization research
- [CURRENT, 2025-11-21, Research] [Research/delegation-decision-tree.md](./AI/Research/delegation-decision-tree.md) - Subagent delegation decision tree
- [CURRENT, 2025-11-21, Research] [Research/P1T13-workflow-simplification-analysis.md](./AI/Research/P1T13-workflow-simplification-analysis.md) - P1T13 workflow simplification analysis
- [CURRENT, 2025-11-21, Research] [Research/subagent-capabilities-research.md](./AI/Research/subagent-capabilities-research.md) - Subagent capabilities research

**Workflow References:**
- [CURRENT, 2025-11-21, Reference] [Workflows/_common/zen-review-command.md](./AI/Workflows/_common/zen-review-command.md) - Zen review slash command implementation
- [CURRENT, 2025-11-21, Reference] [Workflows/session-management.md](./AI/Workflows/session-management.md) - Auto-resume and session management
- [CURRENT, 2025-11-21, Reference] [Workflows/troubleshooting.md](./AI/Workflows/troubleshooting.md) - Workflow troubleshooting guide

**Priority:** 🟡 **HIGH** - Essential for AI assistants; informational for human developers

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
| [0018](./ADRs/0018-web-console-mtls-authentication.md) | Web console mTLS authentication with JWT session management | ✅ Accepted |
| [0019](./ADRs/0019-data-quality-framework.md) | Data quality and validation framework for WRDS data syncs | ✅ Accepted |
| [ADR-012](./ADRs/ADR-012-local-data-warehouse.md) | Local data warehouse architecture (Single-Writer Multi-Reader) | ✅ Accepted |
| [ADR-015](./ADRs/ADR-015-auth0-idp-selection.md) | Auth0 for Production OAuth2/OIDC Identity Provider | 🚧 Proposed |
| [ADR-016](./ADRs/ADR-016-data-provider-protocol.md) | Data Provider Protocol for unified market data access | ✅ Accepted |
| [ADR-0021](./ADRs/ADR-0021-risk-model-implementation.md) | Risk Model Implementation (Portfolio Optimizer & Stress Testing) | ✅ Accepted |
| [ADR-0022](./ADRs/ADR-0022-qlib-integration.md) | Qlib Integration Strategy | ✅ Accepted |
| [ADR-0023](./ADRs/ADR-0023-model-deployment.md) | Model Registry & Deployment Versioning | ✅ Accepted |
| [ADR-024](./ADRs/ADR-024-analytics-security.md) | Web console analytics security & RBAC design | 🚧 Proposed |
| [ADR-0025](./ADRs/ADR-0025-backtest-job-queue.md) | Backtest job queue infrastructure (RQ, Redis, psycopg) | ✅ Accepted |
| [ADR-0025-UI](./ADRs/ADR-0025-backtest-ui-worker-contract.md) | Backtest UI-Worker contract (status vocabulary, progress, results) | ✅ Accepted |
| [0020](./ADRs/0020-reconciliation-service-architecture.md) | Reconciliation service with startup gating and orphan handling | ✅ Accepted |
| [0026](./ADRs/0026-shadow-mode-model-validation.md) | Shadow mode model validation for safe hot-swap | ✅ Accepted |
| [0027](./ADRs/0027-liquidity-aware-slicing.md) | Liquidity-aware TWAP slicing with ADV constraints | ✅ Accepted |
| [0028](./ADRs/0028-market-data-fallback-buffer.md) | Market data fallback buffer for Redis outages | ✅ Accepted |
| [ADR-0029](./ADRs/ADR-0029-alerting-system.md) | Alerting system architecture (multi-channel delivery, rate limiting) | 🚧 Proposed |
| [ADR-0030](./ADRs/ADR-0030-reporting-architecture.md) | Reporting architecture for scheduled reports and PDF generation | 🚧 Proposed |
| [ADR-0031](./ADRs/ADR-0031-nicegui-migration.md) | NiceGUI migration from Streamlit for web console | ✅ Accepted |

**How to use ADRs:**
- **Before modifying architecture:** Check if ADR exists, follow its decisions
- **When making new architectural choice:** Write new ADR (see [ADR_GUIDE.md](./STANDARDS/ADR_GUIDE.md))
- **When questioning a decision:** Read the ADR to understand rationale and alternatives

**Priority:** 🟠 **MEDIUM** - Read relevant ADRs before modifying related systems

---

### 3.5. Architecture Documentation

**Location:** `docs/ARCHITECTURE/`

System-level architecture references, diagrams, and data schemas:

- [CURRENT, 2026-01-02, Architecture] [README.md](./ARCHITECTURE/README.md) - Architecture documentation index and overview
- [CURRENT, 2026-01-02, Architecture] [system_map_flow.md](./ARCHITECTURE/system_map_flow.md) - High-level data flow diagram with virtual edges
- [CURRENT, 2026-01-02, Architecture] [system_map_deps.md](./ARCHITECTURE/system_map_deps.md) - Filtered dependency diagram from code imports
- [CURRENT, 2026-01-02, Architecture] [system_map.canvas](./ARCHITECTURE/system_map.canvas) - Obsidian Canvas with layered layout and subgroups
- [CURRENT, 2026-01-02, Architecture] [system_map.config.json](./ARCHITECTURE/system_map.config.json) - Configuration for architecture visualization
- [CURRENT, 2025-11-23, Architecture] [redis-session-schema.md](./ARCHITECTURE/redis-session-schema.md) - Redis session store schema for OAuth2 tokens with AES-256-GCM encryption

**Priority:** 🟠 **MEDIUM** - Read before making cross-service or topology changes

---

### 4. Domain Concepts (Trading & ML Knowledge)

**Location:** `docs/CONCEPTS/`

Educational explanations of trading and ML concepts:

| Concept | Topic | Complexity |
|---------|-------|------------|
| [corporate-actions.md](./CONCEPTS/corporate-actions.md) | Stock splits, dividends | Beginner |
| [covariance-estimation.md](./CONCEPTS/covariance-estimation.md) | Factor covariance estimation for risk models | Advanced |
| [crsp-data.md](./CONCEPTS/crsp-data.md) | CRSP data, PERMNOs, survivorship bias | Intermediate |
| [fundamental-data.md](./CONCEPTS/fundamental-data.md) | Compustat fundamentals, GVKEY, PIT correctness | Intermediate |
| [pnl-calculation.md](./CONCEPTS/pnl-calculation.md) | Notional, realized, unrealized P&L | Beginner |
| [alpha158-features.md](./CONCEPTS/alpha158-features.md) | Alpha158 feature set | Intermediate |
| [alpha-signal-explorer.md](./CONCEPTS/alpha-signal-explorer.md) | Alpha signal browsing and IC analysis | Intermediate |
| [backtest-result-storage.md](./CONCEPTS/backtest-result-storage.md) | Backtest result storage (Postgres + Parquet) | Intermediate |
| [backtest-web-ui.md](./CONCEPTS/backtest-web-ui.md) | Backtest Web UI (Streamlit interface for job management) | Intermediate |
| [walk-forward-optimization.md](./CONCEPTS/walk-forward-optimization.md) | Walk-forward optimization methodology | Intermediate |
| [monte-carlo-backtesting.md](./CONCEPTS/monte-carlo-backtesting.md) | Monte Carlo simulation for backtest robustness | Intermediate |
| [qlib-data-providers.md](./CONCEPTS/qlib-data-providers.md) | Qlib integration patterns | Intermediate |
| [lightgbm-training.md](./CONCEPTS/lightgbm-training.md) | Model training pipeline | Intermediate |
| [model-registry.md](./CONCEPTS/model-registry.md) | Model lifecycle management | Intermediate |
| [centralized-logging.md](./CONCEPTS/centralized-logging.md) | Centralized logging patterns | Intermediate |
| [distributed-tracing.md](./CONCEPTS/distributed-tracing.md) | Distributed tracing for microservices | Advanced |
| [duckdb-basics.md](./CONCEPTS/duckdb-basics.md) | DuckDB embedded analytics | Beginner |
| [execution-algorithms.md](./CONCEPTS/execution-algorithms.md) | Order execution strategies (TWAP, VWAP) | Intermediate |
| [factor-exposure-visualization.md](./CONCEPTS/factor-exposure-visualization.md) | Factor exposure heatmaps and drill-down | Intermediate |
| [fama-french-factors.md](./CONCEPTS/fama-french-factors.md) | Fama-French factor models (3F, 5F, 6F) | Intermediate |
| [yfinance-limitations.md](./CONCEPTS/yfinance-limitations.md) | yfinance limitations and production gating | Beginner |
| [unified-data-fetcher.md](./CONCEPTS/unified-data-fetcher.md) | Unified Data Fetcher for provider-agnostic data access | Intermediate |
| [hot-reload.md](./CONCEPTS/hot-reload.md) | Zero-downtime model updates | Advanced |
| [feature-parity.md](./CONCEPTS/feature-parity.md) | Research-production consistency | Advanced |
| [mtls-jwt-authentication.md](./CONCEPTS/mtls-jwt-authentication.md) | Mutual TLS and JWT authentication concepts | Advanced |
| [monitoring-and-observability.md](./CONCEPTS/monitoring-and-observability.md) | Metrics, logs, and traces | Intermediate |
| [oauth2-mtls-fallback-architecture.md](./CONCEPTS/oauth2-mtls-fallback-architecture.md) | OAuth2/OIDC with mTLS fallback architecture | Advanced |
| [multi-alpha-allocation.md](./CONCEPTS/multi-alpha-allocation.md) | Multi-strategy capital allocation | Advanced |
| [parquet-format.md](./CONCEPTS/parquet-format.md) | Columnar storage format | Beginner |
| [qlib-comparison.md](./CONCEPTS/qlib-comparison.md) | Qlib vs custom implementation comparison | Intermediate |
| [scheduled-reports.md](./CONCEPTS/scheduled-reports.md) | Scheduled report generation and RBAC | Intermediate |
| [tax-lot-tracking.md](./CONCEPTS/tax-lot-tracking.md) | Tax lot tracking service and RBAC | Intermediate |
| [python-testing-tools.md](./CONCEPTS/python-testing-tools.md) | pytest and testing frameworks | Beginner |
| [research-notebook-launcher.md](./CONCEPTS/research-notebook-launcher.md) | Research notebook session management | Intermediate |
| [redis-patterns.md](./CONCEPTS/redis-patterns.md) | Redis caching and event patterns | Intermediate |
| [risk-management.md](./CONCEPTS/risk-management.md) | Position limits and circuit breakers | Intermediate |
| [risk-models.md](./CONCEPTS/risk-models.md) | Multi-factor Barra-style risk model methodology | Advanced |
| [microstructure.md](./CONCEPTS/microstructure.md) | Market microstructure analysis (VPIN, RV, spread/depth) | Intermediate |
| [realized-volatility.md](./CONCEPTS/realized-volatility.md) | Realized volatility and HAR forecasting models | Intermediate |
| [sql-analytics-patterns.md](./CONCEPTS/sql-analytics-patterns.md) | SQL window functions and CTEs | Intermediate |
| [strategy-comparison.md](./CONCEPTS/strategy-comparison.md) | Strategy comparison tool for multi-strategy analysis | Intermediate |
| [structured-logging.md](./CONCEPTS/structured-logging.md) | JSON logging with context | Intermediate |
| [webhook-security.md](./CONCEPTS/webhook-security.md) | HMAC signature verification | Advanced |
| [websocket-streaming.md](./CONCEPTS/websocket-streaming.md) | Real-time data streaming | Intermediate |
| [backtest-regression.md](./CONCEPTS/backtest-regression.md) | Backtest regression testing harness | Intermediate |
| [workflow-optimization-zen-mcp.md](./CONCEPTS/workflow-optimization-zen-mcp.md) | Zen-MCP workflow integration | Advanced |
| [zen-mcp-clink-optimization-proposal.md](./CONCEPTS/zen-mcp-clink-optimization-proposal.md) | Clink optimization proposal | Advanced |
| [zen-mcp-integration-proposal.md](./CONCEPTS/zen-mcp-integration-proposal.md) | Zen-MCP integration design | Advanced |
| [circuit-breaker-ui.md](./CONCEPTS/circuit-breaker-ui.md) | Circuit breaker dashboard with step-up confirmation | Intermediate |
| [system-health-monitoring.md](./CONCEPTS/system-health-monitoring.md) | System health monitor with graceful degradation | Intermediate |
| [alerting.md](./CONCEPTS/alerting.md) | Alert configuration and notification channels | Intermediate |
| [alert-delivery.md](./CONCEPTS/alert-delivery.md) | Alert delivery service with retry and poison queue | Intermediate |
| [platform-administration.md](./CONCEPTS/platform-administration.md) | Admin dashboard with API keys and config management | Intermediate |
| [data-quality-monitoring.md](./CONCEPTS/data-quality-monitoring.md) | Data quality monitoring with validation rules | Intermediate |
| [data-sync-operations.md](./CONCEPTS/data-sync-operations.md) | Data sync operations and WRDS integration | Intermediate |
| [dataset-explorer.md](./CONCEPTS/dataset-explorer.md) | Dataset explorer for browsing data warehouse | Beginner |

**Priority:** 🟢 **LOW** - Read when you need to understand domain-specific concepts

---

### 5. Implementation Guides (How-To)

**Location:** `docs/ARCHIVE/TASKS_HISTORY/` (completed task guides)

Step-by-step implementation instructions for each major task:

| Guide | Task | Lines | Test Coverage |
|-------|------|-------|---------------|
| [P0T1: Data ETL Pipeline](ARCHIVE/TASKS_HISTORY/P0T1_DONE.md) | Data ETL pipeline | 800+ | 53 tests, 100% |
| [P1T1: Redis Integration](ARCHIVE/TASKS_HISTORY/P1T1_DONE.md) | Redis feature cache & event bus | 850+ | 85 tests, 100% |
| [P0T2: Baseline Qlib Strategy](ARCHIVE/TASKS_HISTORY/P0T2_DONE.md) | Baseline ML strategy | 700+ | Unit tests |
| [P0T3: Signal Service](ARCHIVE/TASKS_HISTORY/P0T3_DONE.md) | Signal service (main guide) | 1,940+ | 57 tests, 95% |
| [P0T3-F4: FastAPI Application](ARCHIVE/TASKS_HISTORY/P0T3-F4_DONE.md) | FastAPI implementation | 600+ | Phase 4 tests |
| [P0T3-F5: Model Hot Reload](ARCHIVE/TASKS_HISTORY/P0T3-F5_DONE.md) | Hot reload mechanism | 500+ | Phase 5 tests |
| [P0T3-F6: Integration Tests](ARCHIVE/TASKS_HISTORY/P0T3-F6_DONE.md) | Integration testing | 400+ | Phase 6 tests |
| [P0T4: Execution Gateway](ARCHIVE/TASKS_HISTORY/P0T4_DONE.md) | Execution gateway | 827+ | 56 tests, 100% |
| [P0T5: Trade Orchestrator](ARCHIVE/TASKS_HISTORY/P0T5_DONE.md) | Orchestrator service | 754+ | 13 tests, 100% |
| [P0T6: Paper Trading Runner](ARCHIVE/TASKS_HISTORY/P0T6_DONE.md) | Paper run automation | 1,059+ | 26 tests, 100% |

**Priority:** 🟡 **HIGH** - Read the relevant guide when implementing or modifying a task

---

### 6. Tasks & Planning

**Location:** `docs/TASKS/`

Current and future work items organized by phase. Completed tasks are archived in `docs/ARCHIVE/TASKS_HISTORY/`.

**Templates:**
- [Template, 2025-10-18, Template] [00-TEMPLATE_DONE.md](./TASKS/00-TEMPLATE_DONE.md) - Template for completed task documents
- [Template, 2025-10-18, Template] [00-TEMPLATE_FEATURE.md](./TASKS/00-TEMPLATE_FEATURE.md) - Template for feature-level task documents
- [Template, 2025-10-18, Template] [00-TEMPLATE_PHASE_PLANNING.md](./TASKS/00-TEMPLATE_PHASE_PLANNING.md) - Template for phase planning documents
- [Template, 2025-10-18, Template] [00-TEMPLATE_PROGRESS.md](./TASKS/00-TEMPLATE_PROGRESS.md) - Template for in-progress task documents
- [Template, 2025-10-18, Template] [00-TEMPLATE_TASK.md](./TASKS/00-TEMPLATE_TASK.md) - Template for new task documents
- [Template, 2025-11-15, Template] [00-PLANNING_WORKFLOW_TEMPLATE.md](./TASKS/00-PLANNING_WORKFLOW_TEMPLATE.md) - Template for planning workflow documentation

**Phase Planning:**
- [CURRENT, 2026-01-02, Planning] [D0_PLANNING.md](./TASKS/D0_PLANNING.md) - D0 Documentation Infrastructure Overhaul planning - 📋 0% (0/6 tasks)
- [CURRENT, 2025-10-18, Planning] [P0_TASKS_DONE.md](./ARCHIVE/TASKS_HISTORY/P0_TASKS_DONE.md) - MVP core tasks (P0T1-P0T6) - ✅ 100% Complete
- [CURRENT, 2025-10-26, Planning] [P1_PLANNING_DONE.md](./ARCHIVE/TASKS_HISTORY/P1_PLANNING_DONE.md) - P1 roadmap and priorities - 🔄 73% Complete (8/11 tasks)
- [CURRENT, 2025-10-26, Planning] [P2_PLANNING_DONE.md](./ARCHIVE/TASKS_HISTORY/P2_PLANNING_DONE.md) - P2 advanced features planning (archived) - 📋 0% (0/6 tasks)
- [CURRENT, 2025-12-31, Planning] [P5_PLANNING.md](./TASKS/P5_PLANNING.md) - P5 NiceGUI Migration phase planning
- [CURRENT, 2025-10-18, Planning] [INDEX.md](./TASKS/INDEX.md) - Task index and status tracker
- [CURRENT, 2025-10-18, Planning] [trading_platform_realization_plan.md](./trading_platform_realization_plan.md) - Original master plan (reference)

**Phase 1 Tasks:**
- [CURRENT, 2025-10-18, Task] [P1T0_DONE.md](ARCHIVE/TASKS_HISTORY/P1T0_DONE.md) - Phase 1 initialization and planning
- [CURRENT, 2025-10-18, Task] [P1T2_DONE.md](ARCHIVE/TASKS_HISTORY/P1T2_DONE.md) - Task tracking and CLI tools
- [CURRENT, 2025-10-18, Task] [P1T3_DONE.md](ARCHIVE/TASKS_HISTORY/P1T3_DONE.md) - DuckDB analytics layer
- [CURRENT, 2025-10-19, Task] [P1T4_DONE.md](ARCHIVE/TASKS_HISTORY/P1T4_DONE.md) - Multi-model code review system
- [CURRENT, 2025-10-20, Task] [P1T5-F1_DONE.md](ARCHIVE/TASKS_HISTORY/P1T5-F1_DONE.md) - Real-time market data subfeature 1
- [CURRENT, 2025-10-20, Task] [P1T5-F3_DONE.md](ARCHIVE/TASKS_HISTORY/P1T5-F3_DONE.md) - Real-time market data subfeature 3
- [CURRENT, 2025-10-20, Task] [P1T6_DONE.md](ARCHIVE/TASKS_HISTORY/P1T6_DONE.md) - Risk management implementation
- [CURRENT, 2025-10-20, Task] [P1T7_DONE.md](ARCHIVE/TASKS_HISTORY/P1T7_DONE.md) - Reconciliation system hardening
- [CURRENT, 2025-10-21, Task] [P1T8_DONE.md](ARCHIVE/TASKS_HISTORY/P1T8_DONE.md) - Monitoring and alerting
- [CURRENT, 2025-10-21, Task] [P1T9_DONE.md](ARCHIVE/TASKS_HISTORY/P1T9_DONE.md) - Centralized logging infrastructure
- [CURRENT, 2025-10-25, Task] [P1T10_DONE.md](ARCHIVE/TASKS_HISTORY/P1T10_DONE.md) - Multi-alpha capital allocation system
- [CURRENT, 2025-10-27, Task] [P1T11_DONE.md](ARCHIVE/TASKS_HISTORY/P1T11_DONE.md) - Workflow automation and testing gates
- [CURRENT, 2025-10-29, Task] [P1T12_DONE.md](ARCHIVE/TASKS_HISTORY/P1T12_DONE.md) - Auto-resume task state tracking
- [CURRENT, 2025-10-31, Task] [P1T13_DONE.md](ARCHIVE/TASKS_HISTORY/P1T13_DONE.md) - Documentation and workflow optimization (completed)
- [CURRENT, 2025-11-15, Task] [P1T13_F3_DONE.md](ARCHIVE/TASKS_HISTORY/P1T13_F3_DONE.md) - P1T13 Feature 3: Systematic PR review comment addressing workflow
- [CURRENT, 2025-11-15, Task] [P1T13_F4_DONE.md](ARCHIVE/TASKS_HISTORY/P1T13_F4_DONE.md) - P1T13 Feature 4: Dependency validation and auto-update system (completed)
- [CURRENT, 2025-11-15, Task] [P1T13-F5_DONE.md](ARCHIVE/TASKS_HISTORY/P1T13-F5_DONE.md) - P1T13 Feature 5: Workflow refinement phase 1 (completed)

**Phase 2 Tasks:**
- [CURRENT, 2025-10-26, Task] [P2T0_DONE.md](ARCHIVE/TASKS_HISTORY/P2T0_DONE.md) - TWAP order slicer implementation
- [CURRENT, 2025-10-26, Task] [P2T1_DONE.md](ARCHIVE/TASKS_HISTORY/P2T1_DONE.md) - Advanced order types and execution
- [CURRENT, 2025-11-15, Task] [P2T2_DONE.md](ARCHIVE/TASKS_HISTORY/P2T2_DONE.md) - Secrets management with Google Cloud Secret Manager (completed)
- [CURRENT, 2025-11-17, Task] [P2T3_DONE.md](ARCHIVE/TASKS_HISTORY/P2T3_DONE.md) - Web console for operational oversight and manual intervention
- [CURRENT, 2025-11-22, Plan] [P2T3_Component2_Plan.md](./ARCHIVE/TASKS_HISTORY/P2T3_Component2_Plan.md) - Component 2: JWT token generation and validation implementation plan
- [CURRENT, 2025-11-23, Task] [P2T3-Phase3_DONE.md](ARCHIVE/TASKS_HISTORY/P2T3-Phase3_DONE.md) - P2T3 Phase 3: OAuth2/OIDC Authentication for Production (in progress)
- [CURRENT, 2025-11-23, Plan] [P2T3-Phase3_Component1_Plan.md](./ARCHIVE/TASKS_HISTORY/P2T3-Phase3_Component1_Plan.md) - Component 1: OAuth2 Config & IdP Setup detailed implementation plan
- [CURRENT, 2025-11-23, Plan] [P2T3-Phase3_Component2_Plan_v3.md](./ARCHIVE/TASKS_HISTORY/P2T3-Phase3_Component2_Plan_v3.md) - Component 2: OAuth2 Authorization Flow with PKCE (v3 FINAL - FastAPI sidecar architecture)
- [CURRENT, 2025-11-23, Errata] [P2T3-Phase3_Component2_Plan_v3_ERRATA.md](./ARCHIVE/TASKS_HISTORY/P2T3-Phase3_Component2_Plan_v3_ERRATA.md) - Component 2 Plan v3 errata and clarifications
- [CURRENT, 2025-11-23, Plan] [P2T3-Phase3_Component3_Plan_v2.md](./ARCHIVE/TASKS_HISTORY/P2T3-Phase3_Component3_Plan_v2.md) - Component 3: Session Management & Token Refresh (v2 FINAL - Security fixes for production)
- [CURRENT, 2025-11-25, Plan] [P2T3_Phase3_PLANNING_SUMMARY.md](./ARCHIVE/TASKS_HISTORY/P2T3_Phase3_PLANNING_SUMMARY.md) - P2T3 Phase 3 planning summary with all 4 components
- [CURRENT, 2025-11-25, Plan] [P2T3-Phase3_Component4_Plan.md](./ARCHIVE/TASKS_HISTORY/P2T3-Phase3_Component4_Plan.md) - Component 4: Streamlit UI Integration detailed implementation plan
- [CURRENT, 2025-11-26, Plan] [P2T3-Phase3_Component5_Plan.md](./ARCHIVE/TASKS_HISTORY/P2T3-Phase3_Component5_Plan.md) - Component 5: CSP Hardening + Nginx Integration detailed implementation plan
- [CURRENT, 2025-11-27, Plan] [P2T3-Phase3_Component6-7_Plan.md](./ARCHIVE/TASKS_HISTORY/P2T3-Phase3_Component6-7_Plan.md) - Components 6+7: mTLS Fallback + Operational Monitoring detailed implementation plan

**Phase 3 Tasks:**
- [CURRENT, 2025-11-30, Planning] [P3_PLANNING.md](./ARCHIVE/TASKS_HISTORY/P3_PLANNING_DONE.md) - P3 Review, Remediation & Modernization phase planning
- [CURRENT, 2025-11-30, Planning] [P3_ISSUES.md](./ARCHIVE/TASKS_HISTORY/P3_ISSUES_DONE.md) - P3 prioritized issues from triple-reviewer analysis (Claude, Gemini, Codex)
- [CURRENT, 2025-11-30, Task] [P3T1_DONE.md](ARCHIVE/TASKS_HISTORY/P3T1_DONE.md) - P3T1: Workflow Modernization - ai_workflow package ✅ Complete
- [CURRENT, 2025-11-30, Task] [P3T2_DONE.md](ARCHIVE/TASKS_HISTORY/P3T2_DONE.md) - P3T2: Critical Fixes (P0) - Security, Trading Safety, Data Integrity ✅ Complete
- [CURRENT, 2025-11-30, Task] [P3T3_DONE.md](ARCHIVE/TASKS_HISTORY/P3T3_DONE.md) - P3T3: High Priority Fixes (P1) - Performance, Reliability, Code Quality
- [CURRENT, 2025-11-30, Task] [P3T4_DONE.md](ARCHIVE/TASKS_HISTORY/P3T4_DONE.md) - P3T4: Medium Priority Fixes (P2) - Type Safety, Lifecycle, Performance, Cleanup
- [CURRENT, 2025-12-01, Task] [P3T5_DONE.md](ARCHIVE/TASKS_HISTORY/P3T5_DONE.md) - P3T5: External Review Findings - Risk Management Fixes (Kill Switch, Position Limits, Circuit Breaker)
- [CURRENT, 2025-12-02, Task] [P3T6_DONE.md](ARCHIVE/TASKS_HISTORY/P3T6_DONE.md) - P3T6: Docker Infrastructure and Runbook Fixes

**Phase 4 Tasks:**
- [CURRENT, 2025-12-03, Planning] [P4_PLANNING.md](./ARCHIVE/TASKS_HISTORY/P4_PLANNING_DONE.md) - P4 Data Infrastructure phase planning
- [CURRENT, 2025-12-03, Task] [P4T1_DONE.md](ARCHIVE/TASKS_HISTORY/P4T1_DONE.md) - P4T1: Data Infrastructure - Local Data Warehouse with WRDS Data Sources
- [CURRENT, 2025-12-07, Task] [P4T2_DONE.md](ARCHIVE/TASKS_HISTORY/P4T2_DONE.md) - P4T2: Analytics Infrastructure - Multi-Factor Model Construction
- [CURRENT, 2025-12-09, Task] [P4T3_DONE.md](ARCHIVE/TASKS_HISTORY/P4T3_DONE.md) - P4T3: Web Console - Core Analytics with RBAC
- [CURRENT, 2025-12-10, Task] [P4T4_DONE.md](ARCHIVE/TASKS_HISTORY/P4T4_DONE.md) - P4T4: Execution Quality & Trade Journal enhancements
- [CURRENT, 2025-12-12, Task] [P4T4_5.1_DONE.md](ARCHIVE/TASKS_HISTORY/P4T4_5.1_DONE.md) - P4T4-T5.1: Backtest Job Queue Infrastructure (PITBacktester callbacks, RQ workers)
- [CURRENT, 2025-12-12, Task] [P4T4_5.2_DONE.md](ARCHIVE/TASKS_HISTORY/P4T4_5.2_DONE.md) - P4T4-T5.2: Web Console Job Management API endpoints
- [CURRENT, 2025-12-12, Task] [P4T4_5.3_DONE.md](ARCHIVE/TASKS_HISTORY/P4T4_5.3_DONE.md) - P4T4-T5.3: Streamlit Backtest Dashboard with progress tracking
- [CURRENT, 2025-12-12, Task] [P4T4_5.4_DONE.md](ARCHIVE/TASKS_HISTORY/P4T4_5.4_DONE.md) - P4T4-T5.4: Scheduled Backtest Automation
- [CURRENT, 2025-12-12, Task] [P4T4_5.5_DONE.md](ARCHIVE/TASKS_HISTORY/P4T4_5.5_DONE.md) - P4T4-T5.5: Integration Tests & Monitoring
- [CURRENT, 2025-12-12, Task] [P4T4_5.6_DONE.md](ARCHIVE/TASKS_HISTORY/P4T4_5.6_DONE.md) - P4T4-T5.6: Documentation & Runbooks
- [CURRENT, 2025-12-18, Task] [P4T5_DONE.md](./ARCHIVE/TASKS_HISTORY/P4T5_DONE.md) - P4T5: Track 7 Web Console Operations (Circuit Breaker, Health Monitor, Alerts, Admin)
- [CURRENT, 2025-12-18, Plan] [P4T5_C0_PLAN.md](./ARCHIVE/TASKS_HISTORY/P4T5_C0_PLAN.md) - P4T5 C0: Prep & Validation component plan (auth stub, governance tests, ADR-0029)
- [CURRENT, 2025-12-18, Plan] [P4T5_C1_PLAN.md](./ARCHIVE/TASKS_HISTORY/P4T5_C1_PLAN.md) - P4T5 C1: Circuit Breaker Dashboard implementation plan (service, metrics, RBAC, UI)
- [CURRENT, 2025-12-19, Plan] [P4T5_C2_PLAN.md](./ARCHIVE/TASKS_HISTORY/P4T5_C2_PLAN.md) - P4T5 C2: System Health Monitor implementation plan (service grid, latency, connectivity)
- [CURRENT, 2025-12-20, Plan] [P4T5_C3C4_PLAN.md](./ARCHIVE/TASKS_HISTORY/P4T5_C3C4_PLAN.md) - P4T5 C3/C4: Alert Delivery Service and Configuration UI implementation plan
- [CURRENT, 2025-12-20, Plan] [P4T5_C5_PLAN.md](./ARCHIVE/TASKS_HISTORY/P4T5_C5_PLAN.md) - P4T5 C5: Admin Dashboard implementation plan (CB control, user management, system config)
- [CURRENT, 2025-12-21, Plan] [P4T5_C6_PLAN.md](./ARCHIVE/TASKS_HISTORY/P4T5_C6_PLAN.md) - P4T5 C6: Integration & Documentation (metrics, navigation, runbooks, SLA infrastructure)
- [CURRENT, 2025-12-03, Component] [ARCHIVE/PLANS/P4T1.1-data-quality-plan.md](./ARCHIVE/PLANS/P4T1.1-data-quality-plan.md) - T1.1: Data Quality & Validation Framework Implementation Plan
- [CURRENT, 2025-12-04, Component] [ARCHIVE/PLANS/P4T1.2-wrds-sync-manager-plan.md](./ARCHIVE/PLANS/P4T1.2-wrds-sync-manager-plan.md) - T1.2: WRDS Connection & Bulk Sync Manager Implementation Plan
- [CURRENT, 2025-12-05, Plan] [P4T1_T4.1_PLAN.md](./ARCHIVE/TASKS_HISTORY/P4T1_T4.1_PLAN.md) - T4.1: yfinance Integration Implementation Plan
- [CURRENT, 2025-12-05, Plan] [P4T1.8_PLAN.md](./ARCHIVE/TASKS_HISTORY/P4T1.8_PLAN.md) - P4T1.8: Unified Data Fetcher Protocol Implementation Plan

**Phase 5 Tasks (NiceGUI Migration):**
- [CURRENT, 2025-12-31, Task] [P5T1_DONE.md](./ARCHIVE/TASKS_HISTORY/P5T1_DONE.md) - P5T1: NiceGUI Migration Foundation (session, auth, client)
- [CURRENT, 2025-12-31, Task] [P5T2_DONE.md](./ARCHIVE/TASKS_HISTORY/P5T2_DONE.md) - P5T2: Page Shell & Navigation (layout, routing, theming)
- [CURRENT, 2025-12-31, Task] [P5T3_DONE.md](./ARCHIVE/TASKS_HISTORY/P5T3_DONE.md) - P5T3: Dashboard Migration (cards, charts, live data)
- [CURRENT, 2025-12-31, Task] [P5T4_TASK.md](./TASKS/P5T4_TASK.md) - P5T4: Strategy Analytics Migration (tables, parameters)
- [CURRENT, 2025-12-31, Task] [P5T5_TASK.md](./TASKS/P5T5_TASK.md) - P5T5: Risk & Trade Controls (circuit breaker, kill switch)
- [CURRENT, 2025-12-31, Task] [P5T6_TASK.md](./TASKS/P5T6_TASK.md) - P5T6: Data Management Migration (sync, quality, explorer)
- [CURRENT, 2025-12-31, Task] [P5T7_TASK.md](./TASKS/P5T7_TASK.md) - P5T7: Research & Reports Migration (notebooks, PDF)
- [CURRENT, 2025-12-31, Task] [P5T8_TASK.md](./TASKS/P5T8_TASK.md) - P5T8: Integration & Cutover (testing, deployment)

**Backlog Tasks (B0):**
- [CURRENT, 2025-12-21, Task] [B0T1_TASK.md](./ARCHIVE/TASKS_HISTORY/B0T1_DONE.md) - B0T1: Codebase Issues Remediation - Validated issues from multi-reviewer analysis
- [CURRENT, 2025-12-28, Task] [P4T6_DONE.md](ARCHIVE/TASKS_HISTORY/P4T6_DONE.md) - P4T6: Data Management module for web console
- [CURRENT, 2025-12-28, Task] [P4T7_DONE.md](ARCHIVE/TASKS_HISTORY/P4T7_DONE.md) - P4T7: Web Console Research & Reporting module
- [CURRENT, 2025-12-28, Plan] [P4T7_C0_PLAN.md](./ARCHIVE/TASKS_HISTORY/P4T7_C0_PLAN.md) - P4T7 C0: Prep & Validation (RBAC, migrations, dependencies)
- [CURRENT, 2025-12-28, Plan] [P4T7_C1_PLAN.md](./ARCHIVE/TASKS_HISTORY/P4T7_C1_PLAN.md) - P4T7 C1: Alpha Signal Explorer (IC/ICIR visualization)
- [CURRENT, 2025-12-28, Plan] [P4T7_C2_PLAN.md](./ARCHIVE/TASKS_HISTORY/P4T7_C2_PLAN.md) - P4T7 C2: Factor Exposure Heatmap (portfolio analytics)
- [CURRENT, 2025-12-28, Plan] [P4T7_C3_PLAN.md](./ARCHIVE/TASKS_HISTORY/P4T7_C3_PLAN.md) - P4T7 C3: Research Notebook Launcher (Docker sessions)
- [CURRENT, 2025-12-28, Plan] [P4T7_C4_PLAN.md](./ARCHIVE/TASKS_HISTORY/P4T7_C4_PLAN.md) - P4T7 C4: Scheduled Reports (PDF generation, email)
- [CURRENT, 2025-12-28, Plan] [P4T7_C5_PLAN.md](./ARCHIVE/TASKS_HISTORY/P4T7_C5_PLAN.md) - P4T7 C5: Tax Lot Core (FIFO/LIFO/SpecID)
- [CURRENT, 2025-12-28, Plan] [P4T7_C6_PLAN.md](./ARCHIVE/TASKS_HISTORY/P4T7_C6_PLAN.md) - P4T7 C6: Tax Lot Advanced (wash sales, 8949)

**Checking Current/Next Task:**
```bash
# Show current task in progress
./scripts/tasks.py list --state PROGRESS

# Show next pending task
./scripts/tasks.py list --state TASK --limit 1
```

**Priority:** 🔴 **CRITICAL** - Check before starting any new task to understand scope and priorities

---

### 6.5. Archive

**Location:** `docs/ARCHIVE/`

Completed and legacy planning artifacts preserved for reference:

- [TASKS_HISTORY/](./ARCHIVE/TASKS_HISTORY/) - Completed task documents and historical implementation guides
- [PLANS/](./ARCHIVE/PLANS/) - Archived implementation plans (historical reference)

**Priority:** 🟢 **LOW** - Reference when you need historical context

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
- [CURRENT, 2025-11-27, Runbook] [auth0-idp-outage.md](./RUNBOOKS/auth0-idp-outage.md) - Auth0 IdP outage response and mTLS fallback procedures
- [CURRENT, 2025-12-02, Runbook] [MAIN_RUNBOOK.md](./RUNBOOKS/MAIN_RUNBOOK.md) - Main runbook for local development setup, Docker operations, and troubleshooting
- [CURRENT, 2025-10-20, Runbook] [ops.md](./RUNBOOKS/ops.md) - Core operational procedures for deployment and troubleshooting
- [CURRENT, 2025-12-24, Runbook] [debug-runbook.md](./RUNBOOKS/debug-runbook.md) - Debugging checklist and incident triage workflow
- [CURRENT, 2025-11-27, Runbook] [mtls-fallback-admin-certs.md](./RUNBOOKS/mtls-fallback-admin-certs.md) - mTLS fallback emergency admin certificate generation and rotation
- [CURRENT, 2025-11-27, Runbook] [oauth2-session-cleanup.md](./RUNBOOKS/oauth2-session-cleanup.md) - OAuth2 session cleanup and expired token removal procedures
- [CURRENT, 2025-11-15, Runbook] [secret-rotation.md](./RUNBOOKS/secret-rotation.md) - Secret rotation procedures for Google Cloud Secret Manager
- [CURRENT, 2025-11-27, Runbook] [session-key-rotation.md](./RUNBOOKS/session-key-rotation.md) - Session encryption key rotation procedures for OAuth2
- [CURRENT, 2025-11-15, Runbook] [secrets-migration.md](./RUNBOOKS/secrets-migration.md) - Migration from .env to Google Cloud Secret Manager
- [CURRENT, 2025-10-20, Runbook] [staging-deployment.md](./RUNBOOKS/staging-deployment.md) - Staging environment deployment, credentials, and rollback procedures
- [CURRENT, 2025-11-17, Runbook] [web-console-user-guide.md](./RUNBOOKS/web-console-user-guide.md) - Web console usage, authentication, manual order entry, kill switch operations
- [CURRENT, 2025-11-21, Runbook] [web-console-cert-rotation.md](./RUNBOOKS/web-console-cert-rotation.md) - Certificate rotation procedures for web console mTLS authentication
- [CURRENT, 2025-11-22, Runbook] [web-console-mtls-setup.md](./RUNBOOKS/web-console-mtls-setup.md) - Web console mTLS setup guide with certificate generation and nginx configuration
- [CURRENT, 2025-12-04, Runbook] [wrds-lock-recovery.md](./RUNBOOKS/wrds-lock-recovery.md) - WRDS sync lock recovery and stale lock handling procedures
- [CURRENT, 2025-12-04, Runbook] [data-backup-restore.md](./RUNBOOKS/data-backup-restore.md) - WRDS data backup and restore procedures
- [CURRENT, 2025-12-04, Runbook] [duckdb-operations.md](./RUNBOOKS/duckdb-operations.md) - DuckDB cache management and reader configuration during syncs
- [CURRENT, 2025-12-04, Runbook] [wrds-credentials.md](./RUNBOOKS/wrds-credentials.md) - WRDS credential management, rotation, and expiry monitoring
- [CURRENT, 2025-12-04, Runbook] [data-storage.md](./RUNBOOKS/data-storage.md) - Disk monitoring, cleanup procedures, and storage expansion
- [CURRENT, 2025-12-08, Runbook] [model-registry-dr.md](./RUNBOOKS/model-registry-dr.md) - Model registry disaster recovery and backup procedures
- [CURRENT, 2025-12-28, Runbook] [data-quality-ops.md](./RUNBOOKS/data-quality-ops.md) - Data quality monitoring operational procedures
- [CURRENT, 2025-12-28, Runbook] [data-sync-ops.md](./RUNBOOKS/data-sync-ops.md) - Data sync operations and troubleshooting
- [CURRENT, 2025-12-28, Runbook] [dataset-explorer-ops.md](./RUNBOOKS/dataset-explorer-ops.md) - Dataset explorer operational procedures
- [CURRENT, 2025-12-21, Runbook] [circuit-breaker-ops.md](./RUNBOOKS/circuit-breaker-ops.md) - Circuit breaker operations, trip/reset procedures, and troubleshooting

**Priority:** 🟡 **HIGH** - Read when deploying or troubleshooting production issues

---

### 8.5. Incidents (Post-Mortems)

**Location:** `docs/INCIDENTS/`

Incident reports and post-mortem analysis:

- [CURRENT, 2025-11-15, Index] [README.md](./INCIDENTS/README.md) - Incident index and post-mortem template

**Priority:** 🟢 **LOW** - Read after incidents to learn from failures

---

### 9. Configuration and Tooling

**Location:** `docs/AI/`, `.github/`, `docs/AI/Prompts/`, `strategies/`

Configuration files, templates, prompts, and tooling:

**docs/AI/Workflows/ (Session Management):**
- [CURRENT, 2025-10-31, Guide] [session-management.md](./AI/Workflows/session-management.md) - Auto-resume task state tracking configuration
- [CURRENT, 2025-10-27, Guide] [troubleshooting.md](./AI/Workflows/troubleshooting.md) - Troubleshooting guide for Claude Code workflows and zen-mcp integration

**docs/AI/Workflows/_common/ (Tool-Specific Configuration):**
- [CURRENT, 2025-10-26, Guide] [zen-review-command.md](./AI/Workflows/_common/zen-review-command.md) - Zen-mcp review slash command configuration
- [CURRENT, 2025-10-25, Guide] [state-README.md](./AI/Workflows/_common/state-README.md) - Task state tracking system documentation
- [CURRENT, 2025-11-15, Guide] [checkpoints-README.md](./AI/Workflows/_common/checkpoints-README.md) - Context checkpointing system for session delegation

**docs/AI/Implementation/ (Implementation Plans):**
- [CURRENT, 2025-11-15, Plan] [P1T13-F5-phase1-implementation-plan.md](./AI/Implementation/P1T13-F5-phase1-implementation-plan.md) - P1T13-F5 Phase 1 implementation plan
- [CURRENT, 2025-11-22, Plan] [P2T3_PHASE2_PLAN.md](./AI/Implementation/P2T3_PHASE2_PLAN.md) - P2T3 Phase 2 complete mTLS authentication system implementation plan

**docs/AI/Research/ (Research Documents):**
- [CURRENT, 2025-11-15, Research] [research/automated-coding-research.md](../docs/AI/Research/automated-coding-research.md) - Automated coding workflow research
- [CURRENT, 2025-11-15, Research] [research/automated-planning-research.md](../docs/AI/Research/automated-planning-research.md) - Automated planning system research

**apps/ (Application-Level Documentation):**
- [CURRENT, 2025-11-21, Guide] [../apps/web_console/certs/README.md](../apps/web_console/certs/README.md) - Web console certificate management and rotation guide

**infra/ (Infrastructure Configuration):**
- [CURRENT, 2025-11-27, Dashboard] [../infra/grafana/dashboards/oauth2-sessions-spec.md](../infra/grafana/dashboards/oauth2-sessions-spec.md) - OAuth2 session monitoring Grafana dashboard specification
- [CURRENT, 2025-11-15, Research] [research/context-optimization-measurement.md](../docs/AI/Research/context-optimization-measurement.md) - Context optimization and measurement techniques
- [CURRENT, 2025-11-15, Research] [research/delegation-decision-tree.md](../docs/AI/Research/delegation-decision-tree.md) - Subagent delegation decision framework
- [CURRENT, 2025-11-15, Research] [research/P1T13-workflow-simplification-analysis.md](../docs/AI/Research/P1T13-workflow-simplification-analysis.md) - P1T13 workflow simplification analysis
- [CURRENT, 2025-11-15, Research] [research/subagent-capabilities-research.md](../docs/AI/Research/subagent-capabilities-research.md) - Subagent capabilities and limitations

**tests/ci/ (CI Test Documentation):**
- [CURRENT, 2025-11-15, Test] [../tests/ci/test_workflow_config.md](../tests/ci/test_workflow_config.md) - CI configuration validation and manual testing procedures

**tests/regression/ (Regression Test Infrastructure):**
- [CURRENT, 2025-12-15, Guide] [../tests/regression/golden_results/README.md](../tests/regression/golden_results/README.md) - Golden results governance and regeneration procedures

**docs/AI/Prompts/ (Clink Review Templates):**
- [CURRENT, 2025-10-27, Template] [prompts/clink-reviews/quick-safety-review.md](../docs/AI/Prompts/clink-reviews/quick-safety-review.md) - Quick safety review prompt template for clink + codex
- [CURRENT, 2025-10-27, Template] [prompts/clink-reviews/deep-architecture-review.md](../docs/AI/Prompts/clink-reviews/deep-architecture-review.md) - Deep architecture review prompt template for clink + gemini
- [CURRENT, 2025-10-27, Template] [prompts/clink-reviews/security-audit.md](../docs/AI/Prompts/clink-reviews/security-audit.md) - Security audit prompt template for clink + gemini
- [CURRENT, 2025-10-27, Template] [prompts/clink-reviews/task-creation-review.md](../docs/AI/Prompts/clink-reviews/task-creation-review.md) - Task creation review prompt template for clink + gemini
- [CURRENT, 2025-10-27, Template] [prompts/pr-body-template.md](../docs/AI/Prompts/pr-body-template.md) - Pull request body template

**docs/AI/Examples/ (Usage Examples):**
- [CURRENT, 2025-10-26, Example] [examples/git-pr/example-standard-pr-creation.md](../docs/AI/Examples/git-pr/example-standard-pr-creation.md) - Standard PR creation example
- [CURRENT, 2025-10-26, Example] [examples/git-pr/example-review-feedback-loop.md](../docs/AI/Examples/git-pr/example-review-feedback-loop.md) - Review feedback loop example
- [CURRENT, 2025-10-26, Example] [examples/git-pr/good-pr-description-template.md](../docs/AI/Examples/git-pr/good-pr-description-template.md) - Good PR description template

**docs/AI/Workflows/_common/ (Reusable Snippets):**
- [CURRENT, 2025-10-27, Snippet] [clink-only-warning.md](./AI/Workflows/_common/clink-only-warning.md) - Warning snippet about clink-only tool usage policy

**.github/ Templates:**
- [CURRENT, 2025-10-26, Template] [pull_request_template.md](../.github/pull_request_template.md) - GitHub pull request template

**docs/AI/Prompts/ (AI Assistant Prompts):**
- [CURRENT, 2025-10-18, Guide] [assistant-rules.md](./AI/Prompts/assistant-rules.md) - Original AI assistant guidance (superseded by CLAUDE.md and docs/AI/AI_GUIDE.md)
- [CURRENT, 2025-10-18, Template] [implement-ticket.md](./AI/Prompts/implement-ticket.md) - Ticket implementation prompt template

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
| [AI_GUIDE.md](./AI/AI_GUIDE.md) | Specific instructions for Claude Code | Always (first document) |
| [INDEX.md](./INDEX.md) | This file - documentation structure | Always (navigation) |

**Priority:** 🔴 **CRITICAL** - AI assistants MUST read AI_GUIDE.md first

---

## 🤖 AI Assistant Reading Order

### For New Tasks (Code Implementation)

```
1. docs/AI/AI_GUIDE.md                                 [If not already read]
2. docs/INDEX.md                                       [This file - for navigation]
3. docs/STANDARDS/CODING_STANDARDS.md                  [MUST read]
4. docs/STANDARDS/DOCUMENTATION_STANDARDS.md           [MUST read]
5. docs/STANDARDS/GIT_WORKFLOW.md                      [MUST read]
6. docs/ARCHIVE/TASKS_HISTORY/P0_TASKS_DONE.md or docs/ARCHIVE/TASKS_HISTORY/P1_PLANNING_DONE.md            [Understand current task]
7. docs/ARCHIVE/TASKS_HISTORY/p{phase}t{N}-{task}.md   [Relevant guide]
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
| Implement P0T6 | [p0t6-paper-run.md](ARCHIVE/TASKS_HISTORY/P0T6_DONE.md) |
| Plan P1 work | [P1_PLANNING_DONE.md](./ARCHIVE/TASKS_HISTORY/P1_PLANNING_DONE.md) |
| Deploy to prod | [ops.md](./RUNBOOKS/ops.md) |

### By Document Type

| Type | Purpose | Location |
|------|---------|----------|
| **Standards** | Normative rules (MUST follow) | [`docs/STANDARDS/`](./STANDARDS/) |
| **ADRs** | Architecture decisions (WHY) | [`docs/ADRs/`](./ADRs/) |
| **Specifications** | Service/library behavior and contracts | [`docs/SPECS/`](./SPECS/) |
| **Concepts** | Domain knowledge (WHAT) | [`docs/CONCEPTS/`](./CONCEPTS/) |
| **Guides** | Implementation steps (HOW) | [`docs/ARCHIVE/TASKS_HISTORY/`](./ARCHIVE/TASKS_HISTORY/) |
| **Tasks** | Work items (TODO) | [`docs/TASKS/`](./TASKS/) |
| **Archive** | Completed tasks + legacy plans | [`docs/ARCHIVE/`](./ARCHIVE/) |
| **Retrospectives** | Learnings (LEARNED) | [`docs/LESSONS_LEARNED/`](./LESSONS_LEARNED/) |
| **Runbooks** | Operations (OPS) | [`docs/RUNBOOKS/`](./RUNBOOKS/) |

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

**Last Updated:** 2026-01-02
**Maintained By:** Development Team
**Format Version:** 1.2 (Added metadata, Quick Links, Update Policy - P1T13)
