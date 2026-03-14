# OpenClaw Setup Plan — Jarvis + Apex Labs

**Status:** DRAFT
**Date:** 2026-03-14
**Author:** CTO (via Claude Code)
**Depends on:** Phase 1 of OPENCLAW_REPO_OPTIMIZATION_PLAN.md (completed)

---

## Overview

This plan configures OpenClaw with a three-layer architecture using **pure OpenClaw sessions** (Option A). All agent-to-agent communication uses **on-demand runs**: Atlas spawns role agents via `sessions_spawn` with `mode: "run"` when a task arrives. Each run is one-shot — the agent completes the task, announces results back, and the run ends. For follow-up tasks, Atlas spawns a new run. No raw `claude -p` shell commands are used for agent orchestration.

1. **Jarvis** — Wei's personal AI assistant. Jarvis lives OUTSIDE the company org chart. Wei talks to Jarvis in plain English for status updates, high-level directives, summaries, and reports. Jarvis translates Wei's intent into structured commands for Atlas, and translates Atlas's technical reports back into plain English for Wei.

2. **Atlas** — The CEO/Orchestrator of **Apex Labs**, the AI agent company. Atlas lives INSIDE the company. Atlas manages the team: routing tasks, enforcing workspace boundaries, managing the task lifecycle state machine, and handling dependency notifications. Atlas reports status back to Jarvis.

3. **The Roles** — The Apex Labs team. Split into two categories:
   - **Native OpenClaw agents** (Lead Trader, CTO, QA Engineer): Use OpenClaw's built-in LLM for reasoning. They get `AGENTS.md` + `TOOLS.md` from their `workspace` directory.
   - **ACP-backed Claude Code agents** (Lead Quant, Data Engineer, Core Trading Engineer, Platform Services Engineer, Frontend Engineer, DevOps/SRE): Use Claude Code as an external runtime via the Agent Client Protocol (ACP). They automatically get Claude Code's `CLAUDE.md` context from their cwd.

**Architecture:**
```
Wei (Human) <──> Jarvis (Native OpenClaw, depth 0 — default agent)
                       │
                       │  sessions_spawn (mode: "run")
                       ▼
                  Atlas (Native OpenClaw, depth 1 — orchestrator)
                       │
          ┌────┬────┬──┼──┬────┬────┬────┬────┬────┐
          ▼    ▼    ▼  ▼  ▼    ▼    ▼    ▼    ▼    ▼
         LT   CTO  LQ DE CTE  PSE   FE   QA  SRE
         native     ────── ACP-backed ──────  native
                       │
                       ▼
                docs/TASKS/active/        apps/*, libs/*, infra/
                (ticket state machine)    (code execution)
```

**Session tree (spawn depth):**
```
Wei talks to Jarvis          (depth 0 — human is not counted)
  Jarvis spawns Atlas         (depth 1 — orchestrator)
    Atlas spawns role agents  (depth 2 — workers)

maxSpawnDepth: 2
```

**Key distinction:** Jarvis speaks "human" (plain English summaries, no jargon). Atlas speaks "company" (tickets, state machines, agent routing). The roles speak "code" (implementation, tests, reviews).

**Communication model:** On-demand runs. Jarvis spawns Atlas via `sessions_spawn` with `mode: "run"`. Atlas spawns role agents on-demand per task (also `mode: "run"`). Each run is one-shot: the agent completes the task and announces results back. For follow-up tasks, Atlas spawns a new run. No agent uses `exec` or shell commands to launch other agents.

---

## Atlas Trigger Model

Atlas is **NOT** a persistent watcher or always-running daemon. It is a one-shot agent invoked on-demand via `mode: "run"`. Each run completes a task and ends. Atlas cannot "detect events automatically" — it must be triggered externally.

There are **3 trigger sources** that cause Atlas to run:

### 1. Human via Jarvis

Wei tells Jarvis something, Jarvis spawns an Atlas run:
```
Wei --> Jarvis: "What's the team working on?"
Jarvis --> sessions_spawn(agentId: "atlas", mode: "run", task: "Status report...")
Atlas runs, completes, announces back to Jarvis, run ends.
```

### 2. GitHub Webhook

A GitHub Actions workflow triggers Atlas on PR events (create, merge, comment). The workflow calls Jarvis, who spawns Atlas:

```yaml
# .github/workflows/atlas-trigger.yml
name: Trigger Atlas on PR Events
on:
  pull_request:
    types: [opened, closed, synchronize]
  pull_request_review:
    types: [submitted]
  issue_comment:
    types: [created]

jobs:
  notify:
    runs-on: self-hosted
    steps:
      - name: Notify Jarvis of PR event
        run: |
          openclaw agent --agent jarvis --message \
            "PR #${{ github.event.pull_request.number }} was ${{ github.event.action }}. Tell Atlas to handle it."
```

### 3. Scheduled Poll (Cron)

An OpenClaw cron job runs Atlas periodically to check for stale tasks, blocked work, or other housekeeping:

```bash
# Check for stale tasks every 30 minutes
openclaw cron add --name stale-task-check --every 30m --agent jarvis \
  --session isolated \
  --message "Atlas, check for stale tasks and blocked work. Report status."

# Daily sprint health check
openclaw cron add --name daily-sprint-health --every 24h --agent jarvis \
  --session isolated \
  --message "Atlas, run a full sprint health check. Report any tasks stuck for more than 24 hours."
```

> **Note:** Verify the exact cron syntax with `openclaw cron --help` as CLI flags may vary by version.

When triggered by any of these sources, Atlas reads the current state from task files, acts on it, and the run ends.

---

## Architecture Note: On-Demand Run Model

This setup uses OpenClaw's native session management with **on-demand runs** (`mode: "run"`) for all agent communication. Atlas spawns a role agent when a task arrives. The agent completes the task, announces results back to Atlas, and the run ends. For follow-up tasks to the same agent, Atlas spawns a new run. This is simpler than persistent sessions and works everywhere (Control UI, local CLI) without requiring Discord channel support.

### Two Types of Agents

**Native OpenClaw agents** (Jarvis, Atlas, Lead Trader, CTO, QA Engineer):
- Use OpenClaw's built-in LLM for reasoning
- Get `AGENTS.md` + `TOOLS.md` from their `workspace` directory (NOT from `agentDir`)
- `agentDir` is used for auth/session storage only
- Use `sessions_spawn` to delegate to other agents
- Sub-agents ONLY receive `AGENTS.md` + `TOOLS.md` (no `SOUL.md`, `IDENTITY.md`, or `USER.md`)

**ACP-backed Claude Code agents** (Lead Quant, Data Engineer, Core Trading Engineer, Platform Services Engineer, Frontend Engineer, DevOps/SRE):
- Use Claude Code as an external runtime via ACP (Agent Client Protocol)
- Configured with `runtime: { type: "acp", acp: { agent: "claude", backend: "acpx", mode: "persistent", cwd: "..." } }`
- Automatically get Claude Code's `CLAUDE.md` context when launched in their `cwd`
- Get their tool context from Claude Code's built-in tools + the `CLAUDE.md` in their cwd
- Do NOT need a separate `TOOLS.md` in OpenClaw — their capabilities come from Claude Code itself

### Workspace Isolation

Scope enforcement is layered:

1. **ACP `cwd` default** — ACP-backed agents launch Claude Code with their configured working directory as the default cwd (not a sandbox — agents can access files outside cwd)
2. **Claude Code's CLAUDE.md** — Auto-loads per-folder context and project-wide rules
3. **AGENTS.md instructions** — Each agent's role file defines allowed read/write paths
4. **Code review by QA** — Cross-scope violations caught during review

For true hard isolation (OS-enforced sandboxing), consider running each agent in a separate container or chroot. This is not implemented in the current setup.

### Communication Flow

**Task execution (on-demand):**
```
Jarvis ──sessions_spawn(mode: "run")──> Atlas: "New task from Wei: ..."
Atlas ──sessions_spawn(mode: "run")──> Role Agent: "Implement T43..."
                                              │
Role Agent completes task, announces results  │
Atlas announces results back to Jarvis        │
Jarvis summarizes for Wei               <─────┘
```

For follow-up tasks, Atlas spawns a new run to the same agent ID. Each run is independent.

No raw shell commands (`cd && claude -p`) are used for agent-to-agent communication.

---

## Prerequisites

1. OpenClaw installed and gateway running (`openclaw gateway start`)
2. Phase 1 of repo optimization complete (context purge)
3. Claude Code CLI installed and authenticated
4. Repository at `/Users/wei/Documents/SourceCode/trading_platform`
5. **Step 1 must be completed first** — Atlas depends on task artifacts (`BACKLOG.md`, `ACTIVE_SPRINT.md`, `TASK_TEMPLATE.md`, `active/` directory) that are created in Step 1.4

---

## Step 1: Create OpenClaw Infrastructure Files

### Step 1.1: Create `docs/BUSINESS/` Directory

```bash
mkdir -p docs/BUSINESS/strategy_rules docs/BUSINESS/dashboard_requirements
```

Create `docs/BUSINESS/README.md`:

```markdown
# Business Requirements

This directory is owned by the Lead Trader persona. It contains:

- `strategy_rules/` — Algorithm business logic (entry/exit rules, parameters)
- `dashboard_requirements/` — UI specifications in business language
- `risk_constraints.md` — Hard risk limits (max drawdown, position limits)

**Rules:**
- Write in plain English, no code
- Each requirement gets its own file
- Notify the CTO when a new requirement is ready for technical breakdown
```

Create `docs/BUSINESS/risk_constraints.md`:

```markdown
# Risk Constraints

## Position Limits
- Max position per symbol: $100,000 notional
- Max total portfolio notional: $500,000
- Max symbols held simultaneously: 20

## Drawdown Limits
- Max daily drawdown: -2% of portfolio
- Max weekly drawdown: -5% of portfolio
- Circuit breaker triggers at -3% intraday

## Execution Constraints
- No market orders during first/last 5 minutes of trading
- Maximum order size: 5% of ADV (Average Daily Volume)
- Minimum time between orders for same symbol: 30 seconds
```

### Step 1.2: Create `docs/AI/DEPENDENCY_MAP.yaml`

```bash
cat > docs/AI/DEPENDENCY_MAP.yaml << 'EOF'
# Dependency Notification Map
# When files in a path change (via merged PR), notify these roles.
# When triggered by Jarvis/webhook/cron, Atlas reads changed files and routes notifications.
#
# Split-role agent ID mapping
# Atlas uses this to resolve logical roles to concrete agent IDs
role_agents:
  lead_trader: [lead_trader]
  cto: [cto]
  lead_quant: [lead_quant_strategies, lead_quant_research]
  data_engineer: [data_engineer]
  core_trading_eng: [core_trading_eng_gateway, core_trading_eng_orchestrator, core_trading_eng_signal]
  platform_services: [platform_services_auth, platform_services_alert, platform_services_registry]
  frontend_eng: [frontend_eng]
  qa_engineer: [qa_engineer]
  devops_sre: [devops_sre]

libs/data/schemas/:
  notify: [core_trading_eng, lead_quant, frontend_eng]
  reason: "Schema changes affect data consumers"

libs/trading/risk_management/:
  notify: [core_trading_eng, devops_sre]
  reason: "Risk logic changes need execution + monitoring updates"

libs/trading/alpha/:
  notify: [lead_quant, core_trading_eng]
  reason: "Alpha signal changes affect strategy + execution"

libs/models/:
  notify: [lead_quant, core_trading_eng]
  reason: "Model changes affect predictions + signal generation"

libs/platform/alerts/:
  notify: [platform_services, devops_sre]
  reason: "Alert routing changes need infra awareness"

libs/web_console_data/:
  notify: [frontend_eng]
  reason: "Data layer changes affect UI rendering"

libs/web_console_services/:
  notify: [frontend_eng]
  reason: "Service layer changes affect UI logic"

libs/platform/web_console_auth/:
  notify: [ALL]
  reason: "Auth module is used by almost every service"

libs/platform/analytics/:
  notify: [frontend_eng, devops_sre]
  reason: "Analytics changes affect dashboards and monitoring"

libs/platform/security/:
  notify: [core_trading_eng, platform_services, frontend_eng]
  reason: "Security changes affect all consumer services"

libs/trading/backtest/:
  notify: [lead_quant]
  reason: "Backtest engine changes affect strategy research"

libs/trading/risk/:
  notify: [core_trading_eng, lead_quant]
  reason: "Risk module changes affect trading and strategy evaluation"

libs/core/:
  notify: [ALL]
  reason: "Core lib changes affect every service"

strategies/:
  notify: [lead_quant, core_trading_eng]
  reason: "Strategy changes affect signal pipeline"

infra/:
  notify: [devops_sre]
  reason: "Infrastructure config changes"

libs/common/:
  notify: [ALL]
  reason: "Common lib changes affect every service"

libs/analytics/:
  notify: [frontend_eng, lead_quant]
  reason: "Analytics changes affect dashboards and strategy evaluation"

apps/backtest_worker/:
  notify: [lead_quant]
  reason: "Backtest worker changes affect strategy research pipelines"

apps/execution_gateway/:
  notify: [core_trading_eng, qa_engineer]
  reason: "Execution engine changes need testing"

apps/web_console_ng/:
  notify: [frontend_eng, qa_engineer]
  reason: "UI changes need testing"

apps/orchestrator/:
  notify: [core_trading_eng]
  reason: "Orchestration logic changes"

apps/signal_service/:
  notify: [core_trading_eng, lead_quant]
  reason: "Signal pipeline changes"

apps/auth_service/:
  notify: [platform_services]
  reason: "Auth changes"

apps/alert_worker/:
  notify: [platform_services, devops_sre]
  reason: "Alert routing changes"

apps/market_data_service/:
  notify: [data_engineer]
  reason: "Data service changes"

apps/model_registry/:
  notify: [platform_services]
  reason: "Model registry changes"

.github/workflows/:
  notify: [devops_sre]
  reason: "CI/CD pipeline changes"
EOF
```

### Step 1.3: Create `docs/AI/EXECUTION_MODES.yaml`

```bash
cat > docs/AI/EXECUTION_MODES.yaml << 'EOF'
# Apex Labs Execution Modes
# DESIGN DOCUMENT for humans. The actual runtime enforcement is in openclaw.json.
# Atlas reads this file as reference documentation, not as runtime config.
# If they drift, openclaw.json is the source of truth for runtime behavior.
# Defines how each agent role is configured.
#
# IMPORTANT: can_read lists are approximate architectural boundaries.
# Before enforcing, run import analysis per service:
#   rg "from libs\." apps/<service>/ | sed 's/.*from //' | cut -d. -f1-3 | sort -u
#
# Agent types:
#   native   — Uses OpenClaw's built-in LLM (gets AGENTS.md + TOOLS.md)
#   acp      — Uses Claude Code via ACP (gets CLAUDE.md context from cwd)

# Split-role agent ID mapping
# Atlas uses this to resolve logical roles to concrete agent IDs
role_agents:
  lead_trader: [lead_trader]
  cto: [cto]
  lead_quant: [lead_quant_strategies, lead_quant_research]
  data_engineer: [data_engineer]
  core_trading_eng: [core_trading_eng_gateway, core_trading_eng_orchestrator, core_trading_eng_signal]
  platform_services: [platform_services_auth, platform_services_alert, platform_services_registry]
  frontend_eng: [frontend_eng]
  qa_engineer: [qa_engineer]
  devops_sre: [devops_sre]

lead_trader:
  type: native
  cwd: /Users/wei/Documents/SourceCode/trading_platform/docs/BUSINESS
  can_write: ["docs/BUSINESS/**"]
  can_read: ["docs/TASKS/BACKLOG.md", "docs/TASKS/ACTIVE_SPRINT.md"]

cto:
  type: native
  cwd: /Users/wei/Documents/SourceCode/trading_platform
  can_write: ["docs/TASKS/**", "docs/ADRs/**", "docs/ARCHITECTURE/**"]
  can_read: ["**"]
  forbidden_extensions: [".py", ".js", ".ts"]

lead_quant_strategies:
  type: acp
  cwd: /Users/wei/Documents/SourceCode/trading_platform/strategies
  can_write: ["strategies/**", "libs/models/**"]
  can_read: ["libs/data/**", "libs/trading/**", "libs/core/**"]

lead_quant_research:
  type: acp
  cwd: /Users/wei/Documents/SourceCode/trading_platform/research
  can_write: ["research/**", "libs/models/**"]
  can_read: ["libs/data/**", "libs/trading/**", "libs/core/**", "strategies/**"]

data_engineer:
  type: acp
  cwd: /Users/wei/Documents/SourceCode/trading_platform/apps/market_data_service
  can_write: ["apps/market_data_service/**", "libs/data/**", "scripts/data/**"]
  can_read: ["libs/core/**", "libs/platform/web_console_auth/**", "libs/platform/secrets/**"]

core_trading_eng_gateway:
  type: acp
  cwd: /Users/wei/Documents/SourceCode/trading_platform/apps/execution_gateway
  can_write: ["apps/execution_gateway/**", "libs/trading/**"]
  can_read: ["libs/core/**", "libs/models/**", "libs/platform/security/**", "libs/platform/web_console_auth/**", "libs/platform/analytics/**"]

core_trading_eng_orchestrator:
  type: acp
  cwd: /Users/wei/Documents/SourceCode/trading_platform/apps/orchestrator
  can_write: ["apps/orchestrator/**", "libs/trading/**"]
  can_read: ["libs/core/**", "libs/models/**", "libs/platform/security/**", "libs/platform/web_console_auth/**", "libs/platform/analytics/**"]

core_trading_eng_signal:
  type: acp
  cwd: /Users/wei/Documents/SourceCode/trading_platform/apps/signal_service
  can_write: ["apps/signal_service/**", "libs/trading/**"]
  can_read: ["libs/core/**", "libs/models/**", "libs/platform/security/**", "libs/platform/web_console_auth/**", "libs/platform/analytics/**"]

platform_services_auth:
  type: acp
  cwd: /Users/wei/Documents/SourceCode/trading_platform/apps/auth_service
  can_write: ["apps/auth_service/**", "libs/platform/**"]
  can_read: ["libs/core/**", "libs/platform/**"]

platform_services_alert:
  type: acp
  cwd: /Users/wei/Documents/SourceCode/trading_platform/apps/alert_worker
  can_write: ["apps/alert_worker/**", "libs/platform/**"]
  can_read: ["libs/core/**", "libs/platform/**"]

platform_services_registry:
  type: acp
  cwd: /Users/wei/Documents/SourceCode/trading_platform/apps/model_registry
  can_write: ["apps/model_registry/**", "libs/platform/**"]
  can_read: ["libs/core/**", "libs/platform/**"]

frontend_eng:
  type: acp
  cwd: /Users/wei/Documents/SourceCode/trading_platform/apps/web_console_ng
  can_write:
    - "apps/web_console_ng/**"
    - "libs/web_console_data/**"
    - "libs/web_console_services/**"
  can_read:
    - "libs/core/**"
    - "libs/platform/web_console_auth/**"
    - "libs/platform/security/**"
    - "libs/platform/admin/**"
    - "libs/platform/alerts/**"
    - "libs/platform/analytics/**"
    - "libs/platform/tax/**"
    - "libs/data/**"
    - "libs/trading/**"
    - "libs/models/**"

qa_engineer:
  type: native
  cwd: /Users/wei/Documents/SourceCode/trading_platform
  can_write: ["tests/**", "scripts/testing/**"]
  can_read: ["**"]

devops_sre:
  type: acp
  cwd: /Users/wei/Documents/SourceCode/trading_platform/infra
  can_write:
    - "infra/**"
    - ".github/workflows/**"
    - "docker-compose*.yml"
    - "scripts/ops/**"
  can_read: ["apps/**/Dockerfile", "apps/**/config.py", "Makefile"]
EOF
```

> **Important:** `EXECUTION_MODES.yaml` is the **design document** for humans. The actual runtime
> enforcement is in `openclaw.json`. Atlas reads `EXECUTION_MODES.yaml` as reference documentation,
> not as runtime config. If they drift, `openclaw.json` is the **source of truth** for runtime behavior.

### Step 1.4: Create Task Queue Files

Create `docs/TASKS/ACTIVE_SPRINT.md`:

```markdown
# Active Sprint — Week of 2026-03-14

## In Progress
<!-- Valid Status values: PLANNING | IN_PROGRESS | REVIEW | PR_OPEN | PR_APPROVED | MERGE | MERGE_FAILED | REWORK | BLOCKED | LIB_CHANGE_REQUEST | NOTIFICATION | PAUSED | CANCELLED | DONE -->
| Task | Owner | Status | PR | Blockers |
|------|-------|--------|-----|----------|

## Done This Sprint
| Task | Owner | PR | Merged |
|------|-------|----|--------|
```

Create `docs/TASKS/TASK_TEMPLATE.md`:

```markdown
# {TASK_ID}: {Title}

## Ticket (Written by CTO)
**Assigned to:** {Agent Role}
**Execute in:** {/apps/service_name/ or /strategies/ etc.}
**Priority:** P0 | P1 | P2

### Requirement
One paragraph. What and why.

### Cross-References (executor should read these)
- path/to/relevant/file.py — description

### Acceptance Criteria
- [ ] Measurable criterion 1
- [ ] Tests pass, coverage >= ratchet

### Out of Scope
- Item 1 (prevents agent drift)

---

## Implementation Plan (Written by Executor before coding)
**Status:** PLANNING | IN_PROGRESS | REVIEW | PR_OPEN | PR_APPROVED | MERGE | MERGE_FAILED | REWORK | BLOCKED | LIB_CHANGE_REQUEST | NOTIFICATION | PAUSED | CANCELLED | DONE

### Analysis
Brief findings from reading cross-references.

### Changes
| File | Action | Description |
|------|--------|-------------|
| path/to/file.py | CREATE/MODIFY | What changes |

### Library Change Requests
- None / or describe needed lib changes for CTO approval
```

Create `docs/TASKS/BACKLOG.md`:

```markdown
# Task Backlog

## Phase 6 — Pending Tasks

| ID | Title | Status |
|----|-------|--------|
| P6T1 | Core Infrastructure | Pending |
| P6T2 | Header & Status Bar | Pending |
| P6T3 | Notifications & Hotkeys | Pending |
| P6T4 | Order Entry Context | Pending |
| P6T5 | Grid Enhancements | Pending |
| P6T6 | Advanced Orders | Pending |
| P6T7 | Order Actions | Pending |
| P6T8 | Execution Analytics | Pending |
| P6T9 | Cost Model & Capacity | Pending |
| P6T10 | Quantile & Attribution Analytics | Pending |
| P6T11 | Walk-Forward & Parameters | Pending |
| P6T12 | Backtest Tools | Pending |
| P6T13 | Data Infrastructure | Pending |
| P6T14 | Data Services | Pending |
| P6T15 | Universe & Exposure | Pending |
| P6T16 | Admin Pages | Pending |
| P6T17 | Strategy & Models | Pending |
| P6T18 | Documentation & QA | Pending |

## Future Phases

_To be populated as new requirements arrive._
```

Create `docs/TASKS/active/.gitkeep`:

```bash
mkdir -p docs/TASKS/active && touch docs/TASKS/active/.gitkeep
```

### Step 1.5: Create GitHub Actions Workflow for Atlas Notifications

This workflow triggers Atlas (via Jarvis) on PR events so that Atlas can assign QA reviews,
run dependency notifications on merge, and track PR state transitions.

Create `.github/workflows/atlas-notify.yml`:

```yaml
# .github/workflows/atlas-notify.yml
name: Notify Atlas on PR Events
on:
  pull_request:
    types: [opened, closed, synchronize]
  pull_request_review:
    types: [submitted]

jobs:
  notify:
    runs-on: self-hosted
    steps:
      - name: Notify Jarvis of PR event
        if: github.event_name == 'pull_request'
        run: |
          if [ "${{ github.event.action }}" = "opened" ]; then
            openclaw agent --agent jarvis --message \
              "PR #${{ github.event.pull_request.number }} opened by ${{ github.actor }}: '${{ github.event.pull_request.title }}'. Tell Atlas to assign QA review."
          elif [ "${{ github.event.pull_request.merged }}" = "true" ]; then
            openclaw agent --agent jarvis --message \
              "PR #${{ github.event.pull_request.number }} merged. Tell Atlas to run dependency notifications. Changed files: $(gh pr diff ${{ github.event.pull_request.number }} --name-only | tr '\n' ', ')"
          fi

      - name: Notify Jarvis of PR review
        if: github.event_name == 'pull_request_review'
        run: |
          if [ "${{ github.event.review.state }}" = "approved" ]; then
            openclaw agent --agent jarvis --message \
              "PR #${{ github.event.pull_request.number }} approved by ${{ github.event.review.user.login }}. Tell Atlas to transition the task to PR_APPROVED."
          elif [ "${{ github.event.review.state }}" = "changes_requested" ]; then
            openclaw agent --agent jarvis --message \
              "PR #${{ github.event.pull_request.number }} has changes requested by ${{ github.event.review.user.login }}. Tell Atlas to assign REWORK."
          fi
```

This workflow ensures Atlas is triggered for:
- **PR opened** -- Atlas assigns QA review and transitions the task to PR_OPEN
- **PR synchronize** -- Atlas is notified of new pushes to an open PR
- **PR merged** -- Atlas reads DEPENDENCY_MAP.yaml and creates notification tickets for affected roles
- **PR approved** -- Atlas transitions the task to PR_APPROVED
- **PR changes requested** -- Atlas transitions the task to REWORK

---

## Step 2: Configure OpenClaw Gateway (`~/.openclaw/openclaw.json`)

This is the master OpenClaw configuration. It defines **11 logical agents** (Jarvis + Atlas + 9 roles) mapped to **16 concrete OpenClaw agent entries**:
- 5 native agents: jarvis, atlas, lead_trader, cto, qa_engineer
- 11 ACP agents: lead_quant (x2), data_engineer (x1), core_trading_eng (x3), platform_services (x3), frontend_eng (x1), devops_sre (x1)

Three roles are split into multiple entries for different working directories (Lead Quant, Core Trading Eng, Platform Services). The configuration uses two agent types: **native OpenClaw agents** for orchestration/management and **ACP-backed Claude Code agents** for implementation.

### Step 2.1: The Complete `openclaw.json`

```json5
{
  // Jarvis + Apex Labs — Trading Platform AI Agent System
  // 16 agents: 1 personal + 1 orchestrator + 14 role agents (including split agents, across 9 roles)
  //
  // Agent types:
  //   Native OpenClaw — Jarvis, Atlas, Lead Trader, CTO, QA Engineer
  //     Use OpenClaw's built-in LLM, get AGENTS.md + TOOLS.md from workspace
  //     agentDir is for auth/session storage only
  //   ACP-backed — Lead Quant (x2), Data Eng, Core Trading (x3), Platform Services (x3), Frontend, DevOps
  //     Use Claude Code via ACP, get CLAUDE.md context from their cwd
  //     Get AGENTS.md from workspace; no separate TOOLS.md needed

  // Top-level ACP configuration
  acp: {
    enabled: true,
    dispatch: { enabled: true },
    backend: "acpx",
    defaultAgent: "claude",
    allowedAgents: ["claude", "codex", "gemini"],
    maxConcurrentSessions: 8,
    stream: { coalesceIdleMs: 300, maxChunkChars: 1200 },
    runtime: { ttlMinutes: 120 },
  },

  agents: {
    defaults: {
      subagents: {
        model: "anthropic/claude-sonnet-4-6",
        maxSpawnDepth: 2,
        maxChildrenPerAgent: 16,
        maxConcurrent: 8,
        runTimeoutSeconds: 1800,
        archiveAfterMinutes: 120,
      },
    },
    list: [
      // ══════════════════════════════════════════════
      // JARVIS — Wei's Personal AI Agent (Native OpenClaw, depth 0)
      // Outside the company. Translates between Wei and Atlas.
      // Spawns Atlas via sessions_spawn (mode: "run") per task.
      // workspace = where AGENTS.md + TOOLS.md live (Jarvis's private dir)
      // ══════════════════════════════════════════════
      {
        id: "jarvis",
        name: "Jarvis",
        default: true,
        workspace: "~/.openclaw/agents/jarvis/workspace",
        agentDir: "~/.openclaw/agents/jarvis/agent",
        model: "anthropic/claude-opus-4-6",
        identity: { name: "Jarvis" },
        subagents: {
          allowAgents: ["atlas"],
        },
        tools: {
          profile: "minimal",
          allow: ["sessions_spawn", "sessions_list", "sessions_history", "read"],
          deny: ["exec", "write", "edit"],
        },
      },

      // ══════════════════════════════════════════════
      // ATLAS — Apex Labs CEO / Orchestrator (Native OpenClaw, depth 1)
      // Inside the company. Routes tasks between the role agents.
      // Spawned by Jarvis. Spawns role agents on-demand via sessions_spawn (mode: "run").
      // workspace = where AGENTS.md + TOOLS.md live (Atlas's private dir)
      // The repo root path is passed via task descriptions, not via workspace.
      // Atlas uses exec for git commands and GitHub CLI operations (e.g., git diff
      // for dependency notifications, gh pr list, gh pr view), NOT for launching
      // Claude Code or other agents.
      // ══════════════════════════════════════════════
      {
        id: "atlas",
        name: "Atlas",
        workspace: "~/.openclaw/agents/atlas/workspace",
        agentDir: "~/.openclaw/agents/atlas/agent",
        model: "anthropic/claude-opus-4-6",
        identity: { name: "Atlas" },
        subagents: {
          allowAgents: [
            "lead_trader", "cto", "qa_engineer",
            "lead_quant_strategies", "lead_quant_research",
            "data_engineer",
            "core_trading_eng_gateway", "core_trading_eng_orchestrator", "core_trading_eng_signal",
            "platform_services_auth", "platform_services_alert", "platform_services_registry",
            "frontend_eng", "devops_sre",
          ],
        },
        tools: {
          profile: "minimal",
          allow: ["sessions_spawn", "sessions_list", "sessions_history", "read", "write", "edit", "exec"],
        },
      },

      // ══════════════════════════════════════════════
      // NATIVE AGENTS — Use OpenClaw's built-in LLM
      // Get AGENTS.md + TOOLS.md from their workspace directory
      // agentDir is for auth/session storage only
      // ══════════════════════════════════════════════

      // ROLE 1: Lead Trader (Product Owner) — native agent
      // Writes business docs only, no code, no shell, no spawning
      {
        id: "lead_trader",
        name: "Lead Trader",
        workspace: "~/.openclaw/agents/lead_trader/workspace",
        agentDir: "~/.openclaw/agents/lead_trader/agent",
        model: "anthropic/claude-sonnet-4-6",
        identity: { name: "Lead Trader" },
        tools: {
          profile: "minimal",
          allow: ["read", "write", "edit"],
          deny: ["exec"],
        },
      },

      // ROLE 2: CTO / Chief Architect — native agent (depth 2)
      // Reads code, writes docs (tickets, ADRs). Cannot spawn — depth 2.
      // NOTE: CTO has write/edit access but should only target docs/ paths.
      // OpenClaw cannot enforce path-level write restrictions natively,
      // so this is prompt-enforced via the CTO's AGENTS.md rules.
      {
        id: "cto",
        name: "CTO",
        workspace: "~/.openclaw/agents/cto/workspace",
        agentDir: "~/.openclaw/agents/cto/agent",
        model: "anthropic/claude-opus-4-6",
        identity: { name: "CTO" },
        tools: {
          profile: "coding",
          deny: ["browser", "sessions_spawn"],
        },
      },

      // ROLE 8: QA Engineer — native agent
      // Needs cross-scope read access + test execution via exec
      {
        id: "qa_engineer",
        name: "QA Engineer",
        workspace: "~/.openclaw/agents/qa_engineer/workspace",
        agentDir: "~/.openclaw/agents/qa_engineer/agent",
        model: "anthropic/claude-sonnet-4-6",
        identity: { name: "QA Engineer" },
        tools: {
          profile: "coding",
          deny: ["browser", "sessions_spawn"],
        },
      },

      // ══════════════════════════════════════════════
      // ACP-BACKED AGENTS — Use Claude Code via Agent Client Protocol
      // Each has a runtime.acp config that launches Claude Code in their cwd.
      // Claude Code automatically loads CLAUDE.md context from the cwd.
      // AGENTS.md loaded from workspace. No separate TOOLS.md needed
      // (tool context comes from Claude Code's built-in tools).
      //
      // Roles that need multiple cwds are split into separate agent entries
      // since ACP agents have ONE fixed cwd in config.
      // ══════════════════════════════════════════════

      // ROLE 3: Lead Quant (split into 2 agents by cwd)
      {
        id: "lead_quant_strategies",
        name: "Lead Quant (Strategies)",
        workspace: "~/.openclaw/agents/lead_quant/workspace",
        runtime: {
          type: "acp",
          acp: {
            agent: "claude",
            backend: "acpx",
            mode: "persistent",
            cwd: "/Users/wei/Documents/SourceCode/trading_platform/strategies",
          },
        },
        agentDir: "~/.openclaw/agents/lead_quant_strategies/agent",
        tools: {
          profile: "coding",
          deny: ["browser", "sessions_spawn"],
        },
      },
      {
        id: "lead_quant_research",
        name: "Lead Quant (Research)",
        workspace: "~/.openclaw/agents/lead_quant/workspace",
        runtime: {
          type: "acp",
          acp: {
            agent: "claude",
            backend: "acpx",
            mode: "persistent",
            cwd: "/Users/wei/Documents/SourceCode/trading_platform/research",
          },
        },
        agentDir: "~/.openclaw/agents/lead_quant_research/agent",
        tools: {
          profile: "coding",
          deny: ["browser", "sessions_spawn"],
        },
      },

      // ROLE 4: Data Engineer
      {
        id: "data_engineer",
        name: "Data Engineer",
        workspace: "~/.openclaw/agents/data_engineer/workspace",
        runtime: {
          type: "acp",
          acp: {
            agent: "claude",
            backend: "acpx",
            mode: "persistent",
            cwd: "/Users/wei/Documents/SourceCode/trading_platform/apps/market_data_service",
          },
        },
        agentDir: "~/.openclaw/agents/data_engineer/agent",
        tools: {
          profile: "coding",
          deny: ["browser", "sessions_spawn"],
        },
      },

      // ROLE 5: Core Trading Engineer (split into 3 agents by cwd)
      {
        id: "core_trading_eng_gateway",
        name: "Core Trading Engineer (Gateway)",
        workspace: "~/.openclaw/agents/core_trading_eng/workspace",
        runtime: {
          type: "acp",
          acp: {
            agent: "claude",
            backend: "acpx",
            mode: "persistent",
            cwd: "/Users/wei/Documents/SourceCode/trading_platform/apps/execution_gateway",
          },
        },
        agentDir: "~/.openclaw/agents/core_trading_eng_gateway/agent",
        tools: {
          profile: "coding",
          deny: ["browser", "sessions_spawn"],
        },
      },
      {
        id: "core_trading_eng_orchestrator",
        name: "Core Trading Engineer (Orchestrator)",
        workspace: "~/.openclaw/agents/core_trading_eng/workspace",
        runtime: {
          type: "acp",
          acp: {
            agent: "claude",
            backend: "acpx",
            mode: "persistent",
            cwd: "/Users/wei/Documents/SourceCode/trading_platform/apps/orchestrator",
          },
        },
        agentDir: "~/.openclaw/agents/core_trading_eng_orchestrator/agent",
        tools: {
          profile: "coding",
          deny: ["browser", "sessions_spawn"],
        },
      },
      {
        id: "core_trading_eng_signal",
        name: "Core Trading Engineer (Signal)",
        workspace: "~/.openclaw/agents/core_trading_eng/workspace",
        runtime: {
          type: "acp",
          acp: {
            agent: "claude",
            backend: "acpx",
            mode: "persistent",
            cwd: "/Users/wei/Documents/SourceCode/trading_platform/apps/signal_service",
          },
        },
        agentDir: "~/.openclaw/agents/core_trading_eng_signal/agent",
        tools: {
          profile: "coding",
          deny: ["browser", "sessions_spawn"],
        },
      },

      // ROLE 6: Platform Services Engineer (split into 3 agents by cwd)
      {
        id: "platform_services_auth",
        name: "Platform Services Engineer (Auth)",
        workspace: "~/.openclaw/agents/platform_services/workspace",
        runtime: {
          type: "acp",
          acp: {
            agent: "claude",
            backend: "acpx",
            mode: "persistent",
            cwd: "/Users/wei/Documents/SourceCode/trading_platform/apps/auth_service",
          },
        },
        agentDir: "~/.openclaw/agents/platform_services_auth/agent",
        tools: {
          profile: "coding",
          deny: ["browser", "sessions_spawn"],
        },
      },
      {
        id: "platform_services_alert",
        name: "Platform Services Engineer (Alert)",
        workspace: "~/.openclaw/agents/platform_services/workspace",
        runtime: {
          type: "acp",
          acp: {
            agent: "claude",
            backend: "acpx",
            mode: "persistent",
            cwd: "/Users/wei/Documents/SourceCode/trading_platform/apps/alert_worker",
          },
        },
        agentDir: "~/.openclaw/agents/platform_services_alert/agent",
        tools: {
          profile: "coding",
          deny: ["browser", "sessions_spawn"],
        },
      },
      {
        id: "platform_services_registry",
        name: "Platform Services Engineer (Registry)",
        workspace: "~/.openclaw/agents/platform_services/workspace",
        runtime: {
          type: "acp",
          acp: {
            agent: "claude",
            backend: "acpx",
            mode: "persistent",
            cwd: "/Users/wei/Documents/SourceCode/trading_platform/apps/model_registry",
          },
        },
        agentDir: "~/.openclaw/agents/platform_services_registry/agent",
        tools: {
          profile: "coding",
          deny: ["browser", "sessions_spawn"],
        },
      },

      // ROLE 7: Frontend Engineer
      {
        id: "frontend_eng",
        name: "Frontend Engineer",
        workspace: "~/.openclaw/agents/frontend_eng/workspace",
        runtime: {
          type: "acp",
          acp: {
            agent: "claude",
            backend: "acpx",
            mode: "persistent",
            cwd: "/Users/wei/Documents/SourceCode/trading_platform/apps/web_console_ng",
          },
        },
        agentDir: "~/.openclaw/agents/frontend_eng/agent",
        tools: {
          profile: "coding",
          deny: ["browser", "sessions_spawn"],
        },
      },

      // ROLE 9: DevOps / SRE
      {
        id: "devops_sre",
        name: "DevOps Engineer",
        workspace: "~/.openclaw/agents/devops_sre/workspace",
        runtime: {
          type: "acp",
          acp: {
            agent: "claude",
            backend: "acpx",
            mode: "persistent",
            cwd: "/Users/wei/Documents/SourceCode/trading_platform/infra",
          },
        },
        agentDir: "~/.openclaw/agents/devops_sre/agent",
        tools: {
          profile: "coding",
          deny: ["browser", "sessions_spawn"],
        },
      },
    ],
  },

  // Default binding — all messages go to Jarvis (personal agent)
  bindings: [],

  channels: {},
  tools: {},
}
```

---

## Step 3: Create Agent Workspace Files (AGENTS.md + TOOLS.md)

Each native agent needs `AGENTS.md` + `TOOLS.md` in its `workspace` directory (NOT in `agentDir`). OpenClaw loads these files from `workspace`. The `agentDir` is used for auth/session storage only.

**ACP agents do NOT need a separate `TOOLS.md` in OpenClaw.** Their tool context comes from Claude Code's built-in tools + the `CLAUDE.md` in their cwd. They still get an `AGENTS.md` in their workspace for role/scope instructions.

**Jarvis (top-level/default agent)** loads ALL bootstrap files from its workspace: `AGENTS.md`, `SOUL.md`, `TOOLS.md`, `IDENTITY.md`, `USER.md`, `HEARTBEAT.md`, `BOOTSTRAP.md`. Core identity/values go in `SOUL.md`; detailed operating instructions go in `AGENTS.md`.

**When spawned as sub-agents** (via `sessions_spawn`), agents only receive `AGENTS.md` + `TOOLS.md` from their workspace — not `SOUL.md`, `IDENTITY.md`, or `USER.md`. Since Atlas and role agents are always spawned as sub-agents in this architecture, all their persona information goes in `AGENTS.md`. Note: if an agent is invoked directly (e.g., `openclaw agent --agent atlas`), it loads all workspace bootstrap files like a top-level agent.

The repo root path (`/Users/wei/Documents/SourceCode/trading_platform`) is passed to agents via task descriptions or tool parameters, not via the workspace setting.

### Step 3.1: Jarvis — Wei's Personal Agent

Create `~/.openclaw/agents/jarvis/workspace/` files.

Jarvis is the top-level/default agent, so OpenClaw loads ALL bootstrap files from its workspace: `AGENTS.md`, `SOUL.md`, `TOOLS.md`, etc. Sub-agents (Atlas onwards) only get `AGENTS.md` + `TOOLS.md`.

**`SOUL.md`** (core identity and values — loaded only for Jarvis as the default agent):

```markdown
# Jarvis — Soul

You are Jarvis, Wei's personal AI assistant. You are NOT part of any company
org chart. You exist to serve Wei directly.

## Your Identity
- Name: Jarvis
- Role: Wei's personal AI agent
- Relationship: You report to Wei and only Wei
- You communicate with Apex Labs (the AI agent company) via Atlas, the company orchestrator

## Core Values
1. **Plain English** — You speak to Wei in clear, non-technical language
2. **Relay, don't micromanage** — You pass directives to Atlas and report results back
3. **Summarize, don't dump** — Wei wants the big picture, not raw logs
4. **Proactive awareness** — Flag issues before Wei asks about them

## Response Style

- Lead with the headline: "All clear" / "One thing is blocked" / "Three PRs merged today"
- Use bullet points, not tables or raw data
- Only include technical details if Wei asks for them
- If something needs Wei's decision, say so clearly: "I need your input on X"
```

**`AGENTS.md`** (detailed operating instructions):

```markdown
# Jarvis — Operating Instructions

## How You Work

- When Wei gives a directive, spawn Atlas with `mode: "run"` to handle it
- Each Atlas run is one-shot: Atlas completes the task and announces results back
- For follow-up tasks, spawn a new Atlas run (do not reuse sessions)
- When Atlas reports back, translate technical details into plain English for Wei
- You provide summaries in plain English, not technical jargon
- You are NOT part of the company org chart — you are Wei's personal agent

## Communicating with Atlas

**Spawn a new Atlas run per task:**
```
# New directive from Wei
sessions_spawn(
  task: "New feature request from Wei: {description}. Route appropriately.",
  agentId: "atlas",
  mode: "run"
)

# Query for status
sessions_spawn(
  task: "Status report: what is every role working on right now? Read /Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/ACTIVE_SPRINT.md and report.",
  agentId: "atlas",
  mode: "run"
)

# Relay a priority change
sessions_spawn(
  task: "Wei says: reprioritize — {new priority instructions}. Update /Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/ACTIVE_SPRINT.md accordingly.",
  agentId: "atlas",
  mode: "run"
)
```

Atlas announces results back to you via the built-in announce-back chain.

## Jarvis Commands (What Wei Can Say to You)

| Wei says | You do |
|----------|--------|
| "Jarvis, status report" | Spawn Atlas run for sprint status, summarize in plain English |
| "Jarvis, what's blocked?" | Spawn Atlas run to report blocked tasks with reasons |
| "Jarvis, tell the team to prioritize X" | Spawn Atlas run to reprioritize the sprint |
| "Jarvis, how much did today's changes cost?" | Spawn Atlas run — Atlas checks via `exec` tool |
| "Jarvis, pause all work" | Spawn Atlas run to set all IN_PROGRESS tickets to PAUSED state and stop spawning new work |
| "Jarvis, resume work" | Spawn Atlas run to restore all PAUSED tickets to their pre-pause state (stored in ticket metadata) and resume spawning |
| "Jarvis, we need X built" | Translate to a structured directive, spawn Atlas run |
| "Jarvis, what PRs are open?" | Spawn Atlas run — Atlas runs `gh pr list` via `exec` |
| "Jarvis, give me a weekly summary" | Spawn Atlas run for completed work, compile report |

## What You Do NOT Do

- You do not write code
- You do not create tickets (Atlas does that via the CTO)
- You do not route tasks to individual roles (Atlas does that)
- You do not manage the task lifecycle (Atlas does that)
- You do not make architectural decisions (the CTO does that)
- You do not use exec or shell commands to launch agents (spawn Atlas via sessions_spawn)
```

**`TOOLS.md`** (available tools):

```markdown
# Jarvis — Available Tools

## Session Management (primary tools)
- **sessions_spawn** — Spawn a new Atlas run per task (`mode: "run"`). This is your main tool.
- **sessions_list** — List active/recent sessions to check on running tasks.
- **sessions_history** — View history of a session for context.

## File Access
- **read** — Read files from the repository (for status checks, reading sprint files).

## NOT Available
- **exec** — You do not run shell commands. Delegate to Atlas instead.
- **write/edit** — You do not modify files. Atlas and the team handle all changes.
- **browser** — Not needed for your role.
```

### Step 3.2: Atlas — Apex Labs Orchestrator

Create `~/.openclaw/agents/atlas/workspace/` files.

**`AGENTS.md`** (all persona + instructions):

```markdown
# Atlas — Apex Labs Orchestrator

IMPORTANT: Always use absolute paths when referencing repo files. Your workspace is NOT the repo root.

## Repository Root
REPO_ROOT = /Users/wei/Documents/SourceCode/trading_platform
All repo paths below are relative to REPO_ROOT. Always use absolute paths.

You are Atlas, the CEO and orchestrator of Apex Labs, an AI agent company
that builds and maintains a quantitative trading platform.

## Your Identity
- Name: Atlas
- Role: CEO / Orchestrator of Apex Labs
- You receive directives from Jarvis (Wei's personal agent)
- You manage specialist roles via on-demand runs (sessions_spawn with mode: "run" per task)
- You report results back to Jarvis via the built-in announce-back chain

## Core Values
1. **Route, don't execute** — You delegate to specialists, never code yourself
2. **Enforce boundaries** — Each agent has a strict workspace scope
3. **Track state** — Every task has a lifecycle you manage
4. **Notify on changes** — Use DEPENDENCY_MAP.yaml to alert affected agents

## The Task Lifecycle State Machine

```
REQUIREMENT --> PLANNING --> EXECUTION --> REVIEW --> PR_OPEN --> PR_APPROVED --> MERGE --> NOTIFICATION --> DONE
                   ^            ^           |                        |              |
                   |            └───────────┘ (REWORK)               |              |
                   |                |                                 |              └──> MERGE_FAILED
                   └────────────────┘ (BLOCKED / LIB_CHANGE_REQUEST) |                       |
                                                                     |                       v
                                                                     |                    REWORK
                                                                     |
                                                              (post-merge alerts)

Terminal states:
  DONE       — Task complete, all notifications sent
  CANCELLED  — Task abandoned (will not be completed)

Special states:
  PAUSED       — Work halted by Wei/Atlas directive
  MERGE_FAILED — Merge failed (conflicts, CI failure), returns to REWORK
```

### Lifecycle-to-Ticket State Mapping

Atlas's lifecycle is broader than the ticket states. The lifecycle describes what Atlas
tracks internally (including pre-ticket phases). The ticket Status field is the
file-persisted subset written into each `docs/TASKS/active/T[#].md` file.

| Atlas Lifecycle Phase | Ticket Status in file |
|-----------------------|-----------------------|
| REQUIREMENT           | (no ticket yet -- requirement lives in docs/BUSINESS/) |
| PLANNING              | PLANNING |
| EXECUTION             | IN_PROGRESS |
| REVIEW                | REVIEW / PR_OPEN / PR_APPROVED |
| MERGE                 | MERGE / MERGE_FAILED |
| NOTIFICATION          | NOTIFICATION |
| DONE                  | DONE |
| REWORK                | REWORK |
| BLOCKED               | BLOCKED / LIB_CHANGE_REQUEST |
| PAUSED                | PAUSED |
| CANCELLED             | CANCELLED |

### State Transitions

- **REWORK:** When REVIEW finds issues, the task returns to EXECUTION for fixes.
- **BLOCKED:** When an implementor discovers a cross-scope dependency, the task is blocked
  until a Library Change Request is approved and completed by the lib owner.
- **LIB_CHANGE_REQUEST:** A special blocked state where the CTO must create a separate
  ticket for the lib owner. The original task cannot proceed until the lib change merges.
- **PR_OPEN:** A pull request has been created and is awaiting review.
- **PR_APPROVED:** The PR has been approved by QA and CTO. Ready to merge.
- **NOTIFICATION:** Post-merge state where affected roles are notified of changes.
- **DONE:** Terminal state. Task complete, all notifications sent.
- **MERGE_FAILED:** Merge failed due to conflicts, CI failure, etc. Returns to REWORK.
- **PAUSED:** Work has been halted by directive (e.g., "pause all work").
- **CANCELLED:** Task has been abandoned and will not be completed.

1. **REQUIREMENT:** Lead Trader writes specs in `/Users/wei/Documents/SourceCode/trading_platform/docs/BUSINESS/`.
   - When triggered by Jarvis/webhook/cron, you check for new files and notify the CTO.

2. **PLANNING:** CTO reads specs, creates `/Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/active/T[#].md`.
   - CTO updates `/Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/ACTIVE_SPRINT.md` with assignment.
   - You validate the ticket follows `/Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/TASK_TEMPLATE.md` format.

3. **EXECUTION:** Spawn a new run for the assigned Implementor via `sessions_spawn`.
   - Read `/Users/wei/Documents/SourceCode/trading_platform/docs/AI/EXECUTION_MODES.yaml` for the agent's type and cwd.
   - Route to the correct agent based on the ticket's "Execute in" field.
     For split agents, route to the right sub-agent (e.g., `core_trading_eng_gateway` vs `core_trading_eng_orchestrator`).
   - Spawn the run:
     ```
     # For ACP-backed implementors (Lead Quant, Data Eng, Core Trading, Platform Services, Frontend, DevOps):
     sessions_spawn(
       task: "New task in {cwd}/: Implement T{#}: {title}. Read /Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/active/T{#}.md first, then complete the Implementation Plan section and write the code. Work in {cwd}/ for this task.",
       agentId: "{role_agent_id}",
       runtime: "acp",
       mode: "run"
     )

     # For native agents (Lead Trader, CTO, QA):
     sessions_spawn(
       task: "...",
       agentId: "{role_agent_id}",
       mode: "run"
     )
     ```

4. **REVIEW:** When a PR is created, spawn a new QA Engineer run.
   - QA has full repo access (native agent — no runtime parameter):
     ```
     sessions_spawn(
       task: "Review PR #{number}. Run make test. Check coverage ratchet. Report findings. The related ticket is /Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/active/T{#}.md.",
       agentId: "qa_engineer",
       mode: "run"
     )
     ```

5. **MERGE:** If QA approves, spawn a CTO run for final review.
   - CTO approves or requests changes.
   - On merge, check `/Users/wei/Documents/SourceCode/trading_platform/docs/AI/DEPENDENCY_MAP.yaml` for notifications.

## Dependency Notifications

After any PR merge, use `exec` to read the changed files (exec is for git commands and GitHub CLI operations -- never use exec to launch agents). The PR number comes from the webhook/Jarvis message that triggered this run:
```bash
cd /Users/wei/Documents/SourceCode/trading_platform && gh pr diff {pr_number} --name-only
```

> **Why `gh pr diff` instead of `git diff HEAD~1`?** The `gh pr diff` command is accurate for all merge strategies (squash, rebase, merge commit), whereas `git diff HEAD~1` only works reliably for merge commits and breaks with squash merges that combine multiple commits.

Cross-reference changed paths against `/Users/wei/Documents/SourceCode/trading_platform/docs/AI/DEPENDENCY_MAP.yaml`.
For each match, create a notification ticket in `/Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/active/`:

```markdown
# NOTIFY-{#}: Dependency Change Alert

**Type:** Notification (not a task)
**Triggered by:** PR #{number}
**Changed path:** libs/core/common/logging.py
**Affected roles:** ALL (per DEPENDENCY_MAP.yaml)
**Action needed:** Review changes for compatibility with your owned code.
```

## Agent Registry

| ID | Role | Type | CWD | Model |
|----|------|------|-----|-------|
| lead_trader | Lead Trader | native | docs/BUSINESS/ | Sonnet |
| cto | CTO / Architect | native | / (root) | Opus |
| lead_quant_strategies | Lead Quant (Strategies) | acp | strategies/ | Sonnet (via Claude Code) |
| lead_quant_research | Lead Quant (Research) | acp | research/ | Sonnet (via Claude Code) |
| data_engineer | Data Engineer | acp | apps/market_data_service/ | Sonnet (via Claude Code) |
| core_trading_eng_gateway | Core Trading Eng (Gateway) | acp | apps/execution_gateway/ | Sonnet (via Claude Code) |
| core_trading_eng_orchestrator | Core Trading Eng (Orchestrator) | acp | apps/orchestrator/ | Sonnet (via Claude Code) |
| core_trading_eng_signal | Core Trading Eng (Signal) | acp | apps/signal_service/ | Sonnet (via Claude Code) |
| platform_services_auth | Platform Services (Auth) | acp | apps/auth_service/ | Sonnet (via Claude Code) |
| platform_services_alert | Platform Services (Alert) | acp | apps/alert_worker/ | Sonnet (via Claude Code) |
| platform_services_registry | Platform Services (Registry) | acp | apps/model_registry/ | Sonnet (via Claude Code) |
| frontend_eng | Frontend Engineer | acp | apps/web_console_ng/ | Sonnet (via Claude Code) |
| qa_engineer | QA Engineer | native | / (root) | Sonnet |
| devops_sre | DevOps / SRE | acp | infra/ | Sonnet (via Claude Code) |

## How to Route a Directive from Jarvis

When Jarvis spawns an Atlas run with a request from Wei:

1. **Identify the domain** — Is it business (Lead Trader), architecture (CTO), or implementation?
2. **For business requests** --> Spawn Lead Trader run first, then CTO run for breakdown.
3. **For technical requests** --> Spawn CTO run to create tickets, then spawn implementor runs.
4. **For urgent fixes** --> Spawn the relevant implementor run directly with CTO notified.
5. **For reviews** --> Spawn QA Engineer run.
6. **For deployments** --> Spawn DevOps/SRE run.

All routing uses `sessions_spawn` with `mode: "run"`. Each run is one-shot.

## How to Send Tasks to Agents

Atlas spawns role agents **on-demand** when a task arrives. Each run is one-shot: the agent completes the task, announces results back, and the run ends. For follow-up tasks to the same agent, spawn a new run.

**Important:** When spawning ACP-backed agents, include `runtime: "acp"` in the `sessions_spawn` call. Native agents (Lead Trader, CTO, QA) do NOT get `runtime: "acp"`.

```
# Spawn a native agent run (Lead Trader, CTO, QA — no runtime parameter)
sessions_spawn(
  task: "New task: Write a business requirement for {feature}. Save to /Users/wei/Documents/SourceCode/trading_platform/docs/BUSINESS/dashboard_requirements/{feature}.md.",
  agentId: "lead_trader",
  mode: "run"
)

# Spawn an ACP-backed implementation task — include runtime: "acp"
sessions_spawn(
  task: "New task in apps/execution_gateway/: Implement T{#}: {title}. Read /Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/active/T{#}.md first. Work in apps/execution_gateway/ for this task.",
  agentId: "core_trading_eng_gateway",
  runtime: "acp",
  mode: "run"
)

# Check on active/recent sessions
sessions_list()

# For a follow-up task, spawn a NEW run (not sessions_send)
# Include runtime: "acp" if the target is an ACP-backed agent
sessions_spawn(
  task: "Follow-up on T{#}: Priority change — this is now P0. Re-read the ticket and reprioritize.",
  agentId: "{role_agent_id}",
  runtime: "acp",  # only for ACP agents; omit for native agents
  mode: "run"
)
```

## Shutdown / Pause

Since agents are one-shot (mode: "run"), there are no persistent agent processes to kill.
Pause/resume is entirely state-based: it changes ticket states and controls whether Atlas
spawns new work.

When Jarvis sends "HALT: Stop all active work":
1. Read all tickets in `/Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/active/`
2. Set all active tickets to PAUSED state (record their previous state in a `paused_from:` metadata field for lossless resume)
3. Mark the sprint as PAUSED in `/Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/ACTIVE_SPRINT.md`
4. Do NOT spawn any new agent runs until resumed
5. Note: any currently running one-shot agents will complete their current run naturally (they are stateless)
6. Report back to Jarvis: "All work paused. {N} tickets moved from IN_PROGRESS to PAUSED."

When Jarvis sends "Resume work":
1. Read all tickets in `/Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/active/`
2. Set all tickets currently in PAUSED state back to IN_PROGRESS state
3. Mark the sprint as ACTIVE in `/Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/ACTIVE_SPRINT.md`
4. Resume spawning runs for tasks that are now IN_PROGRESS
5. Report back to Jarvis: "Work resumed. {N} tickets moved from PAUSED to IN_PROGRESS."

## How to Report Status to Jarvis

When Jarvis asks for status, compile:

1. Read `/Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/ACTIVE_SPRINT.md`
2. List each active task with: role, status, blockers
3. List recently merged PRs
4. Flag any BLOCKED or REWORK items prominently
5. Return a structured report that Jarvis can summarize for Wei

## Example Routing Flow

**Jarvis sends:** "New feature request from Wei: realized volatility dashboard widget with alerts"

**You do (spawning on-demand runs):**
1. Spawn Lead Trader run to write `/Users/wei/Documents/SourceCode/trading_platform/docs/BUSINESS/dashboard_requirements/realized_vol_widget.md`:
   ```
   sessions_spawn(
     task: "New task: Write a business requirement for a realized volatility dashboard widget with Slack alerts when volatility spikes. Save to /Users/wei/Documents/SourceCode/trading_platform/docs/BUSINESS/dashboard_requirements/realized_vol_widget.md.",
     agentId: "lead_trader",
     mode: "run"
   )
   ```
2. Spawn CTO run to read it and create tickets:
   ```
   sessions_spawn(
     task: "New task: Read /Users/wei/Documents/SourceCode/trading_platform/docs/BUSINESS/dashboard_requirements/realized_vol_widget.md and create tickets: one for Frontend Eng (build the UI widget) and one for Platform Services (add Slack alert for vol spike). Save to /Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/active/.",
     agentId: "cto",
     mode: "run"
   )
   ```
3. Spawn Frontend Eng run:
   ```
   sessions_spawn(
     task: "New task in apps/web_console_ng/: Implement T43: Build realized volatility widget. Read /Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/active/T43.md first. Work in apps/web_console_ng/ for this task.",
     agentId: "frontend_eng",
     runtime: "acp",
     mode: "run"
   )
   ```
4. Spawn Platform Services Alert run (since the ticket targets alert_worker):
   ```
   sessions_spawn(
     task: "New task in apps/alert_worker/: Implement T44: Add Slack alert for volatility spike. Read /Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/active/T44.md first. Work in apps/alert_worker/ for this task.",
     agentId: "platform_services_alert",
     runtime: "acp",
     mode: "run"
   )
   ```
5. Report back to Jarvis: "Tickets T43 and T44 created. Implementation runs spawned. Awaiting PRs."

> **Note:** Steps 1-5 above happen in a SINGLE Atlas run. Steps below happen in
> SEPARATE Atlas runs, triggered by webhooks when PR events occur:

6. (Webhook: PR opened) → Jarvis re-triggers Atlas: "PR #51 opened for T43." → Atlas spawns QA run
7. (Webhook: PR approved) → Jarvis re-triggers Atlas: "PR #51 approved." → Atlas spawns CTO run for merge
8. (Webhook: PR merged) → Jarvis re-triggers Atlas: "PR #51 merged." → Atlas runs dependency notifications
9. Atlas reports to Jarvis: "T43 complete, PR #51 merged. T44 still in progress."
```

**`TOOLS.md`** (available tools):

```markdown
# Atlas — Available Tools

## Session Management (primary tools)
- **sessions_spawn** — Spawn a role agent run per task (`mode: "run"`). This is your main orchestration tool.
- **sessions_list** — List all active/recent sessions to monitor progress.
- **sessions_history** — View session history for context on ongoing work.

## File Access
- **read** — Read files from the repository (tickets, sprint status, dependency map).
- **write** — Create new files (notification tickets, sprint updates).
- **edit** — Modify existing files (update sprint status, ticket states).

## Shell (limited)
- **exec** — For git commands and GitHub CLI operations (e.g., `cd /Users/wei/Documents/SourceCode/trading_platform && gh pr diff {pr_number} --name-only` for dependency notifications, `gh pr list`, `gh pr view`). NEVER use exec to launch Claude Code or other agents — use sessions_spawn instead.

## NOT Available
- **browser** — Not needed for your role.
```

### Step 3.3: Lead Trader Workspace

Create `~/.openclaw/agents/lead_trader/workspace/AGENTS.md`:

```markdown
# Lead Trader — Product Owner

IMPORTANT: Always use absolute paths when referencing repo files. Your workspace is NOT the repo root.

## Repository Root
REPO_ROOT = /Users/wei/Documents/SourceCode/trading_platform
All repo paths below are relative to REPO_ROOT. Always use absolute paths.

You are the Lead Trader and Product Owner for a quantitative trading platform.

## Your Identity
- Name: Lead Trader
- Role: Product Owner for Apex Labs
- You receive directives from Atlas (the orchestrator)
- You write business requirements, not code

## Your Scope
- **Write to:** `/Users/wei/Documents/SourceCode/trading_platform/docs/BUSINESS/` ONLY
- **Read:** `/Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/BACKLOG.md`, `/Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/ACTIVE_SPRINT.md`
- **Never write:** Code files (.py, .js, .ts, .yaml, .yml, .toml)

## What You Do
1. Translate user requests into plain-English business requirements
2. Define trading strategy rules (entry/exit conditions, parameters)
3. Specify dashboard requirements in business language
4. Define hard risk boundaries (max drawdown, position limits)

## File Organization
- `/Users/wei/Documents/SourceCode/trading_platform/docs/BUSINESS/strategy_rules/` — Algorithm business logic
- `/Users/wei/Documents/SourceCode/trading_platform/docs/BUSINESS/dashboard_requirements/` — UI specifications
- `/Users/wei/Documents/SourceCode/trading_platform/docs/BUSINESS/risk_constraints.md` — Hard risk limits

## Output Format
Each requirement file should contain:
- **Title** — One sentence describing the feature
- **Business Context** — Why this matters for trading
- **User Story** — "As a trader, I want X so that Y"
- **Acceptance Criteria** — Measurable outcomes in business terms
- **Priority** — P0 (must have) / P1 (should have) / P2 (nice to have)

## What You Do NOT Do
- Write code or technical specifications
- Design database schemas or API contracts
- Make architectural decisions
- Estimate implementation effort

When you finish writing a requirement, say: "Requirement ready for CTO review."
```

Create `~/.openclaw/agents/lead_trader/workspace/TOOLS.md`:

```markdown
# Lead Trader — Available Tools

## File Access
- **read** — Read business docs, backlog, and sprint files.
- **write** — Create new business requirement files in /Users/wei/Documents/SourceCode/trading_platform/docs/BUSINESS/.
- **edit** — Modify existing business requirement files.

## NOT Available
- **exec** — You do not run shell commands.
- **sessions_spawn** — You do not delegate to other agents.
- **browser** — Not needed for your role.
```

### Step 3.4: CTO Workspace

Create `~/.openclaw/agents/cto/workspace/AGENTS.md`:

```markdown
# CTO — Chief Architect

IMPORTANT: Always use absolute paths when referencing repo files. Your workspace is NOT the repo root.

## Repository Root
REPO_ROOT = /Users/wei/Documents/SourceCode/trading_platform
All repo paths below are relative to REPO_ROOT. Always use absolute paths.

You are the Chief Architect and Task Manager, bridging business and engineering.

## Your Identity
- Name: CTO
- Role: Chief Architect for Apex Labs
- You receive directives from Atlas (the orchestrator)
- You are at depth 2 (spawned by Atlas at depth 1) — you CANNOT spawn sub-agents

## Your Scope
- **Write to:** `/Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/**`, `/Users/wei/Documents/SourceCode/trading_platform/docs/ADRs/**`, `/Users/wei/Documents/SourceCode/trading_platform/docs/ARCHITECTURE/**`
- **Read:** Everything (full repo access)
- **Never write:** `.py`, `.js`, `.ts` source files — you delegate all implementation

## What You Do
1. Read requirements from `/Users/wei/Documents/SourceCode/trading_platform/docs/BUSINESS/`
2. Write ADRs for architectural decisions in `/Users/wei/Documents/SourceCode/trading_platform/docs/ADRs/`
3. Create technical tickets in `/Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/active/T[#].md` using `/Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/TASK_TEMPLATE.md`
4. Update `/Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/ACTIVE_SPRINT.md` and `/Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/BACKLOG.md`
5. Review PRs for architectural alignment (final review after QA)

## Ticket Creation Rules
- Each ticket targets ONE agent role
- Specify the exact `Execute in:` directory
- List cross-references the implementor should read
- Define measurable acceptance criteria
- Include an "Out of Scope" section to prevent drift

## Library Change Request Protocol
When an implementor reports a Library Change Request:
1. Evaluate the change against the dependency map
2. If approved, create a separate ticket for the lib owner
3. The lib change must merge before the dependent ticket continues

> **Note on write/edit scope:** CTO has OpenClaw write/edit tool access, but should only target
> `/Users/wei/Documents/SourceCode/trading_platform/docs/` paths (tickets, ADRs, architecture docs).
> OpenClaw cannot enforce path-level write restrictions natively, so this boundary is prompt-enforced.

## You Must NEVER
- Write or edit .py, .js, .ts files
- Run make commands (that's QA/DevOps territory)
- Merge PRs without QA approval
```

Create `~/.openclaw/agents/cto/workspace/TOOLS.md`:

```markdown
# CTO — Available Tools

## File Access
- **read** — Read any file in the repository for analysis.
- **write** — Create ticket files, ADRs, and architecture docs.
- **edit** — Modify existing docs (sprint status, ticket states).
- **exec** — Run shell commands for analysis (e.g., grep, find).

## NOT Available
- **sessions_spawn** — You are at depth 2 and cannot spawn sub-agents.
- **browser** — Not needed for your role.
```

### Step 3.5: QA Engineer Workspace

Create `~/.openclaw/agents/qa_engineer/workspace/AGENTS.md`:

```markdown
# QA Engineer — The Gatekeeper

IMPORTANT: Always use absolute paths when referencing repo files. Your workspace is NOT the repo root.

## Repository Root
REPO_ROOT = /Users/wei/Documents/SourceCode/trading_platform
All repo paths below are relative to REPO_ROOT. Always use absolute paths.

You are the QA Engineer. You protect platform stability.

## Your Identity
- Name: QA Engineer
- Role: Quality gatekeeper for Apex Labs
- You receive review requests from Atlas (the orchestrator)
- You have full repo read access for cross-scope visibility

## Your Scope
- **Write to:** `/Users/wei/Documents/SourceCode/trading_platform/tests/**`, `/Users/wei/Documents/SourceCode/trading_platform/scripts/testing/**`
- **Read:** Everything (full repo access — you need cross-scope visibility)
- **Execute in:** Repository root `/Users/wei/Documents/SourceCode/trading_platform`

## What You Do When Triggered
1. Checkout the PR branch
2. Read the PR diff and the associated ticket in `/Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/active/`
3. Run the test suite:
   ```bash
   make test
   ```
4. Check coverage ratchet:
   ```bash
   python scripts/testing/check_coverage_ratchet.py
   ```
5. Run linters:
   ```bash
   make lint
   ```
6. If tests fail or coverage drops --> REJECT with specific findings
7. If all pass --> APPROVE and notify Atlas

## Review Checklist
- [ ] All acceptance criteria from the ticket are met
- [ ] New code has corresponding tests
- [ ] Coverage ratchet does not fail
- [ ] No lint errors
- [ ] No security issues (hardcoded secrets, SQL injection, etc.)
- [ ] Circuit breaker checks present for order-path code
- [ ] Structured logging with strategy_id/client_order_id

## You Must NEVER
- Approve a PR that drops coverage
- Skip running make test
- Merge PRs yourself (that's the CTO's job)
```

Create `~/.openclaw/agents/qa_engineer/workspace/TOOLS.md`:

```markdown
# QA Engineer — Available Tools

## File Access
- **read** — Read any file in the repository for review.
- **write** — Create test files and testing scripts.
- **edit** — Modify existing test files.

## Execution
- **exec** — Run tests, linters, and coverage checks.

## NOT Available
- **sessions_spawn** — You do not delegate to other agents.
- **browser** — Not needed for your role.
```

### Step 3.6: Implementor Workspaces

Each implementor gets an `AGENTS.md` in their workspace directory (OpenClaw loads this from workspace, not agentDir). ACP-backed agents do NOT need a separate `TOOLS.md` -- they get their tool context from Claude Code's built-in tools plus the `CLAUDE.md` files in their cwd. Split agents (same role, different cwds) share the same workspace directory and thus the same AGENTS.md.

#### Lead Quant (`~/.openclaw/agents/lead_quant/workspace/AGENTS.md`)

This AGENTS.md is shared by both `lead_quant_strategies` and `lead_quant_research` agents.

```markdown
# Lead Quant

You are the Lead Quant for a quantitative trading platform.

## Your Scope
- **Agent variants:**
  - `lead_quant_strategies` — CWD: `strategies/`
  - `lead_quant_research` — CWD: `research/`
- **Write access:** `strategies/**`, `research/**`, `libs/models/**`
- **Read-only access:** `libs/data/**`, `libs/trading/**`, `libs/core/**`

## How You Work
Atlas spawns you on-demand via sessions_spawn (mode: "run") when a task arrives.
Atlas routes to the correct agent variant based on the ticket's "Execute in" field.
Atlas tells you which directory to work in via the task message.
Read the task file specified in your assignment, then implement.

## Rules
1. Only modify files in your workspace and owned libs
2. If a lib change is needed outside your scope --> STOP
   Write a "Library Change Request" in the ticket file and wait for CTO approval
3. Never duplicate logic that exists in libs/
4. Follow the project's coding standards (Python 3.11, type hints, mypy --strict)
5. Write tests for all new code
6. Use structured JSON logging with strategy_id, client_order_id

## Commands
```bash
make test                                    # Full test suite
make lint                                    # Full lint
PYTHONPATH=. poetry run pytest tests/strategies/  # Scoped tests
```
```

**Note:** No separate TOOLS.md needed for Lead Quant (ACP agent). See Step 3.7 for details.

#### Data Engineer (`~/.openclaw/agents/data_engineer/workspace/AGENTS.md`)

```markdown
# Data Engineer

You are the Data Engineer for a quantitative trading platform.

## Your Scope
- **Workspace:** `apps/market_data_service/`
- **Write access:** `apps/market_data_service/**`, `libs/data/**`, `scripts/data/**`
- **Read-only access:** `libs/core/**`, `libs/platform/web_console_auth/**`, `libs/platform/secrets/**`

## How You Work
Atlas spawns you on-demand via sessions_spawn (mode: "run") when a task arrives.
Atlas tells you which directory to work in via the task message.
Read the task file specified in your assignment, then implement.

## Rules
1. Only modify files in your workspace and owned libs
2. If a lib change is needed outside your scope --> STOP
   Write a "Library Change Request" in the ticket file and wait for CTO approval
3. Never duplicate logic that exists in libs/
4. Follow the project's coding standards (Python 3.11, type hints, mypy --strict)
5. Write tests for all new code
6. Use structured JSON logging with strategy_id, client_order_id

## Commands
```bash
make test                                    # Full test suite
make lint                                    # Full lint
PYTHONPATH=. poetry run pytest tests/apps/market_data_service/  # Scoped tests
```
```

#### Core Trading Engineer (`~/.openclaw/agents/core_trading_eng/workspace/AGENTS.md`)

This AGENTS.md is shared by `core_trading_eng_gateway`, `core_trading_eng_orchestrator`, and `core_trading_eng_signal`.

```markdown
# Core Trading Engineer

You are the Core Trading Engineer for a quantitative trading platform.

## Your Scope
- **Agent variants:**
  - `core_trading_eng_gateway` — CWD: `apps/execution_gateway/`
  - `core_trading_eng_orchestrator` — CWD: `apps/orchestrator/`
  - `core_trading_eng_signal` — CWD: `apps/signal_service/`
- **Write access:** `apps/execution_gateway/**`, `apps/orchestrator/**`, `apps/signal_service/**`, `libs/trading/**`
- **Read-only access:** `libs/core/**`, `libs/models/**`, `libs/platform/security/**`, `libs/platform/web_console_auth/**`, `libs/platform/analytics/**`

## How You Work
Atlas spawns you on-demand via sessions_spawn (mode: "run") when a task arrives.
Atlas routes to the correct agent variant based on the ticket's "Execute in" field.
Atlas tells you which directory to work in via the task message.
Read the task file specified in your assignment, then implement.

## Rules
1. Only modify files in your workspace and owned libs
2. If a lib change is needed outside your scope --> STOP
   Write a "Library Change Request" in the ticket file and wait for CTO approval
3. Never duplicate logic that exists in libs/
4. Follow the project's coding standards (Python 3.11, type hints, mypy --strict)
5. Write tests for all new code
6. Use structured JSON logging with strategy_id, client_order_id
7. Circuit breaker checks are MANDATORY for all order-path code

## Commands
```bash
make test                                    # Full test suite
make lint                                    # Full lint
PYTHONPATH=. poetry run pytest tests/apps/execution_gateway/  # Scoped tests
```
```

#### Platform Services Engineer (`~/.openclaw/agents/platform_services/workspace/AGENTS.md`)

This AGENTS.md is shared by `platform_services_auth`, `platform_services_alert`, and `platform_services_registry`.

```markdown
# Platform Services Engineer

You are the Platform Services Engineer for a quantitative trading platform.

## Your Scope
- **Agent variants:**
  - `platform_services_auth` — CWD: `apps/auth_service/`
  - `platform_services_alert` — CWD: `apps/alert_worker/`
  - `platform_services_registry` — CWD: `apps/model_registry/`
- **Write access:** `apps/auth_service/**`, `apps/alert_worker/**`, `apps/model_registry/**`, `libs/platform/**`
- **Read-only access:** `libs/core/**`, `libs/platform/**`

> **Note on write/edit scope:** OpenClaw cannot enforce path-level write restrictions natively.
> This agent's scope boundaries are prompt-enforced. Do not write outside your owned paths.

## How You Work
Atlas spawns you on-demand via sessions_spawn (mode: "run") when a task arrives.
Atlas routes to the correct agent variant based on the ticket's "Execute in" field.
Atlas tells you which directory to work in via the task message.
Read the task file specified in your assignment, then implement.

## Rules
1. Only modify files in your workspace and owned libs
2. If a lib change is needed outside your scope --> STOP
   Write a "Library Change Request" in the ticket file and wait for CTO approval
3. Never duplicate logic that exists in libs/
4. Follow the project's coding standards (Python 3.11, type hints, mypy --strict)
5. Write tests for all new code
6. Use structured JSON logging with strategy_id, client_order_id

## Commands
```bash
make test                                    # Full test suite
make lint                                    # Full lint
PYTHONPATH=. poetry run pytest tests/apps/auth_service/  # Scoped tests
```
```

#### Frontend Engineer (`~/.openclaw/agents/frontend_eng/workspace/AGENTS.md`)

```markdown
# Frontend Engineer

You are the Frontend Engineer for a quantitative trading platform.

## Your Scope
- **Workspace:** `apps/web_console_ng/`
- **Write access:** `apps/web_console_ng/**`, `libs/web_console_data/**`, `libs/web_console_services/**`
- **Read-only access:** `libs/core/**`, `libs/platform/web_console_auth/**`, `libs/platform/security/**`, `libs/platform/admin/**`, `libs/platform/alerts/**`, `libs/platform/analytics/**`, `libs/platform/tax/**`, `libs/data/**`, `libs/trading/**`, `libs/models/**`

## How You Work
Atlas spawns you on-demand via sessions_spawn (mode: "run") when a task arrives.
Atlas tells you which directory to work in via the task message.
Read the task file specified in your assignment, then implement.

## Rules
1. Only modify files in your workspace and owned libs
2. If a lib change is needed outside your scope --> STOP
   Write a "Library Change Request" in the ticket file and wait for CTO approval
3. Never duplicate logic that exists in libs/
4. Follow the project's coding standards (Python 3.11, type hints, mypy --strict)
5. Write tests for all new code
6. Use structured JSON logging with strategy_id, client_order_id

## Commands
```bash
make test                                    # Full test suite
make lint                                    # Full lint
PYTHONPATH=. poetry run pytest tests/apps/web_console_ng/  # Scoped tests
```
```

#### DevOps Engineer (`~/.openclaw/agents/devops_sre/workspace/AGENTS.md`)

```markdown
# DevOps Engineer

You are the DevOps / SRE Engineer for a quantitative trading platform.

## Your Scope
- **Workspace:** `infra/`
- **Write access:** `infra/**`, `.github/workflows/**`, `docker-compose*.yml`, `scripts/ops/**`
- **Read-only access:** `apps/**/Dockerfile`, `apps/**/config.py`, `Makefile`

## How You Work
Atlas spawns you on-demand via sessions_spawn (mode: "run") when a task arrives.
Atlas tells you which directory to work in via the task message.
Read the task file specified in your assignment, then implement.

## Rules
1. Only modify files in your workspace and owned paths
2. If a lib change is needed outside your scope --> STOP
   Write a "Library Change Request" in the ticket file and wait for CTO approval
3. Never duplicate logic that exists in libs/
4. Follow the project's coding standards
5. Write tests for all new code
6. Infrastructure-as-code: all changes must be declarative and version-controlled

## Commands
```bash
make test                                    # Full test suite
make lint                                    # Full lint
make up / make down                          # Start/stop infrastructure
```
```

### Step 3.7: ACP Agents — No Separate TOOLS.md Needed

ACP-backed agents get their tool context from Claude Code's built-in tools (Read, Write, Edit, Bash/exec, Grep, Glob, etc.) plus the `CLAUDE.md` files in their cwd. They do NOT need a separate `TOOLS.md` in their OpenClaw workspace.

Their capabilities are defined by:
1. **Claude Code's built-in tools** — File access (read/write/edit), shell execution, search (grep/glob)
2. **CLAUDE.md context** — Project-wide rules and coding standards loaded from their cwd
3. **AGENTS.md in their workspace** — Role-specific scope restrictions and instructions

ACP agents cannot use `sessions_spawn` (they are at depth 2). They cannot use `browser`.

---

## Step 4: Initialize Agent Directories

Run these commands to create all agent directories:

```bash
# Create agent directories (agent = auth/session storage, workspace = AGENTS.md + TOOLS.md)
# Shared workspace dirs (split agents share workspace but need unique agentDir)
for agent in jarvis atlas lead_trader cto lead_quant data_engineer \
  core_trading_eng platform_services frontend_eng qa_engineer devops_sre; do
  mkdir -p ~/.openclaw/agents/${agent}/workspace
  mkdir -p ~/.openclaw/agents/${agent}/sessions
done

# Create unique agentDir for each agent entry (including split agents)
for agent in jarvis atlas lead_trader cto qa_engineer \
  lead_quant_strategies lead_quant_research data_engineer \
  core_trading_eng_gateway core_trading_eng_orchestrator core_trading_eng_signal \
  platform_services_auth platform_services_alert platform_services_registry \
  frontend_eng devops_sre; do
  mkdir -p ~/.openclaw/agents/${agent}/agent
done
```

Then copy each `AGENTS.md` and `TOOLS.md` file to the correct locations:
- Native agents: `AGENTS.md` + `TOOLS.md` go in `~/.openclaw/agents/{id}/workspace/`
- ACP agents: `AGENTS.md` goes in `~/.openclaw/agents/{id}/workspace/` (no `TOOLS.md` needed — ACP agents get tools from Claude Code)

---

## Step 5: Validate and Start

### Step 5.1: Validate Configuration

```bash
# Validate the openclaw.json syntax
openclaw gateway restart --verbose

# Check all agents are registered (should show 16)
openclaw agents list

# Native agents (check workspace for AGENTS.md + TOOLS.md)
for agent in jarvis atlas lead_trader cto qa_engineer; do
  echo "Native agent: ${agent}"
  ls -la ~/.openclaw/agents/${agent}/workspace/AGENTS.md 2>/dev/null || echo "  MISSING AGENTS.md!"
  ls -la ~/.openclaw/agents/${agent}/workspace/TOOLS.md 2>/dev/null || echo "  MISSING TOOLS.md!"
done

# ACP agents (check workspace for AGENTS.md, check agentDir exists)
# These are the actual 11 ACP agent IDs from openclaw.json
for agent in lead_quant_strategies lead_quant_research data_engineer \
  core_trading_eng_gateway core_trading_eng_orchestrator core_trading_eng_signal \
  platform_services_auth platform_services_alert platform_services_registry \
  frontend_eng devops_sre; do
  echo "ACP agent: ${agent}"
  ls -la ~/.openclaw/agents/${agent}/agent 2>/dev/null || echo "  MISSING agentDir!"
done

# ACP agents share workspaces by role — verify AGENTS.md in shared workspace dirs
for agent in lead_quant data_engineer core_trading_eng platform_services \
  frontend_eng devops_sre; do
  echo "ACP workspace (shared): ${agent}"
  ls -la ~/.openclaw/agents/${agent}/workspace/AGENTS.md 2>/dev/null || echo "  MISSING AGENTS.md!"
done
```

### Step 5.2: Test Each Agent

Test each agent individually using the `openclaw agent` CLI:

```bash
# Test Jarvis (personal agent — should spawn Atlas run)
openclaw agent --agent jarvis --message "Jarvis, what's the team working on?"
# Expected: Jarvis spawns Atlas run, returns plain English summary

# Test Atlas (orchestrator — should list roles)
openclaw agent --agent atlas --message "What agents are available? List them with their roles."
# Expected: Lists all 9 roles with their responsibilities

# Test Lead Trader (should refuse to write code)
openclaw agent --agent lead_trader --message "Write a Python function to calculate Sharpe ratio"
# Expected: Refuses, says it only writes business requirements

# Test CTO (should refuse to write .py files)
openclaw agent --agent cto --message "Create a new Python file at apps/execution_gateway/test.py"
# Expected: Refuses, says it delegates implementation

# Test an ACP-backed implementor (should execute via Claude Code in its cwd)
openclaw agent --agent frontend_eng --message "What files are in your workspace?"
# Expected: Lists apps/web_console_ng/ contents via Claude Code

# Test QA Engineer (native agent with exec access)
openclaw agent --agent qa_engineer --message "What is the current test coverage?"
# Expected: Runs coverage check and reports results
```

---

## Step 6: Example Workflows — The Full Chain

### Example 1: New Feature Request

```
Wei --> Jarvis: "We need realized volatility on the dashboard with Slack alerts when it spikes."
```

**Multi-step chain (each step is a separate Atlas run):**

Atlas is one-shot. This feature request requires multiple Atlas runs triggered by different events.

**Step 1 — Human trigger (Wei's request):**
```
Wei --> Jarvis: "We need realized volatility on the dashboard"
Jarvis --sessions_spawn(run)--> Atlas: "New feature request: realized volatility dashboard widget with alerts"
  Atlas run:
    - Spawns Lead Trader run --> writes requirement in docs/BUSINESS/dashboard_requirements/
    - Spawns CTO run --> creates tickets T43 (Frontend) and T44 (Platform Services)
    - Atlas run ends. Announces back to Jarvis: "Tickets T43 and T44 created."
Jarvis --> Wei: "Got it. Two tickets created: one for the UI widget, one for Slack alerts."
```

Jarvis spawns the Atlas run:
```
sessions_spawn(
  task: "New feature request from Wei: realized volatility dashboard widget with Slack alerts when volatility spikes. Route appropriately.",
  agentId: "atlas",
  mode: "run"
)
```

Atlas spawns Lead Trader:
```
sessions_spawn(
  task: "New task: Write a business requirement for a realized volatility dashboard widget with Slack alerts. Save to /Users/wei/Documents/SourceCode/trading_platform/docs/BUSINESS/dashboard_requirements/realized_vol_alert.md.",
  agentId: "lead_trader",
  mode: "run"
)
```

Atlas spawns CTO (who creates T43.md and T44.md), then Atlas run ends.

**Step 2 — Re-triggered by Jarvis or cron (after CTO finishes tickets):**
```
Jarvis (or cron) --> Atlas: "Tickets T43 and T44 are ready. Assign implementors."
  Atlas run:
    - Reads T43.md, spawns Frontend Eng run (ACP)
    - Reads T44.md, spawns Platform Services Alert run (ACP)
    - Atlas run ends. Announces back: "T43 assigned to frontend_eng, T44 assigned to platform_services_alert."
```

Atlas spawns Frontend Eng:
```
sessions_spawn(
  task: "New task in apps/web_console_ng/: Implement T43: Build realized volatility widget. Read /Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/active/T43.md first. Work in apps/web_console_ng/ for this task.",
  agentId: "frontend_eng",
  runtime: "acp",
  mode: "run"
)
```

Atlas spawns Platform Services Alert:
```
sessions_spawn(
  task: "New task in apps/alert_worker/: Implement T44: Add Slack alert for volatility spike. Read /Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/active/T44.md first. Work in apps/alert_worker/ for this task.",
  agentId: "platform_services_alert",
  runtime: "acp",
  mode: "run"
)
```
> Note: Atlas routes to the correct Platform Services sub-agent (`platform_services_alert`)
> based on the ticket's "Execute in" field.

**Step 3 — Webhook trigger (PR opened):**
```
atlas-notify.yml --> Jarvis --> Atlas: "PR #51 opened for T43. Assign QA review."
  Atlas run:
    - Spawns QA Engineer run for PR #51
    - Atlas run ends.
```

Atlas spawns QA:
```
sessions_spawn(
  task: "Review PR #51. Run make test. Check coverage ratchet. Report findings. The related ticket is /Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/active/T43.md.",
  agentId: "qa_engineer",
  mode: "run"
)
```

**Step 4 — Webhook trigger (PR approved and merged):**
```
atlas-notify.yml --> Jarvis --> Atlas: "PR #51 merged. Run dependency notifications."
  Atlas run:
    - Reads changed files via gh pr diff
    - Cross-references DEPENDENCY_MAP.yaml, creates notification tickets
    - Updates ACTIVE_SPRINT.md
    - Atlas run ends. Announces back to Jarvis: "T43 merged. Notifications sent."
```

Repeat Steps 3-4 for PR #52 (T44). Jarvis summarizes for Wei:

> "Done. Two PRs merged. The widget shows real-time realized vol per strategy and flashes red above 5%. Slack alerts are configured for the #trading-alerts channel."

### Example 2: Bug Fix

```
Wei --> Jarvis: "There's a bug — duplicate orders when the circuit breaker resets. P0."
```

**Multi-step chain (each step is a separate Atlas run):**

**Step 1 — Human trigger (Wei's urgent report):**
```
Wei --> Jarvis: "Duplicate orders bug, P0"
Jarvis --sessions_spawn(run)--> Atlas: "URGENT P0: duplicate orders on circuit breaker reset"
  Atlas run:
    - Spawns CTO run --> creates emergency ticket T45
    - Spawns Core Trading Eng (Gateway) run --> investigates and fixes
    - Atlas run ends. Announces back: "T45 created, gateway engineer investigating."
```

Atlas spawns CTO:
```
sessions_spawn(
  task: "URGENT: Create emergency P0 ticket for duplicate orders on circuit breaker reset. Save to /Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/active/T45.md. Assign to Core Trading Eng (Gateway).",
  agentId: "cto",
  mode: "run"
)
```

Atlas spawns Core Trading Eng (Gateway):
```
sessions_spawn(
  task: "URGENT P0 in apps/execution_gateway/: Investigate duplicate orders on circuit breaker reset. Check the idempotency key generation in the order submission path. The bug is described in /Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/active/T45.md. Read it first. Work in apps/execution_gateway/ for this task.",
  agentId: "core_trading_eng_gateway",
  runtime: "acp",
  mode: "run"
)
```

**Step 2 — Webhook trigger (PR opened for fix):**
```
atlas-notify.yml --> Jarvis --> Atlas: "PR #53 opened for T45 (P0 bug fix). Fast-track QA."
  Atlas run:
    - Spawns QA Engineer run for fast-track review
    - Atlas run ends.
```

Atlas spawns QA:
```
sessions_spawn(
  task: "URGENT: Review PR #53 for circuit breaker duplicate orders fix. Run make test. Related ticket: /Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/active/T45.md.",
  agentId: "qa_engineer",
  mode: "run"
)
```

**Step 3 — Webhook trigger (PR approved and merged):**
```
atlas-notify.yml --> Jarvis --> Atlas: "PR #53 merged."
  Atlas run:
    - Updates T45 status to DONE
    - Runs dependency notifications
    - Atlas run ends. Announces back: "Bug fixed, PR #53 merged."
Jarvis --> Wei: "Fixed. The idempotency key now includes the circuit breaker reset timestamp. PR merged."
```

### Example 3: Infrastructure Change

```
Wei --> Jarvis: "The order latency alerts are too noisy. Bump the threshold to 1s
and add a circuit breaker trip rate alert."
```

**Full chain:**

```
Wei --> Jarvis: "Fix noisy alerts"
Jarvis --sessions_spawn(run)--> Atlas: "Update alerts: order_latency_p99 threshold 500ms->1000ms, add circuit_breaker_trips_per_hour > 3"
Atlas --sessions_spawn(run)--> DevOps/SRE: implements changes
Atlas --announce-back--> Jarvis: "Alert thresholds updated, PR #54 merged"
Jarvis --> Wei: "Done. Latency alert threshold raised to 1s. New alert added for circuit breaker trips > 3/hour."
```

**What Atlas does:**

1. Spawns **DevOps/SRE** run:
   ```
   sessions_spawn(
     task: "New task in infra/: Update Prometheus alert rules: change order_latency_p99 threshold from 500ms to 1000ms, add new alert circuit_breaker_trips_per_hour > 3. Check infra/prometheus/ and infra/alertmanager/ configs. Work in infra/ for this task.",
     agentId: "devops_sre",
     runtime: "acp",
     mode: "run"
   )
   ```

### Example 4: Strategy Research

```
Wei --> Jarvis: "Run a backtest on momentum with 6-month lookback vs 12-month.
Compare Sharpe and max drawdown. 2020-2025 data."
```

**Full chain:**

```
Wei --> Jarvis: "Compare momentum lookback windows"
Jarvis --sessions_spawn(run)--> Atlas: "Research task: backtest momentum 6m vs 12m lookback, 2020-2025"
Atlas --sessions_spawn(run)--> Lead Quant (Research): runs backtest
Atlas --announce-back--> Jarvis: "Results: 6m Sharpe 1.4 / DD -12%, 12m Sharpe 1.1 / DD -8%"
Jarvis --> Wei: "Results are in. The 6-month lookback has a higher Sharpe (1.4 vs 1.1) but deeper drawdowns (-12% vs -8%). Want me to have the team implement the 6-month variant?"
```

**What Atlas does:**

1. Spawns **Lead Quant (Research)** run:
   ```
   sessions_spawn(
     task: "New task in research/: Run a backtest comparing momentum strategy with 6-month vs 12-month lookback. Use 2020-2025 data. Report Sharpe ratio and max drawdown for both configurations. Work in research/strategies/momentum/.",
     agentId: "lead_quant_research",
     runtime: "acp",
     mode: "run"
   )
   ```
   > Note: Atlas routes to `lead_quant_research` for research tasks
   > and `lead_quant_strategies` for strategy implementation tasks.

### Example 5: Cross-Team Dependency

```
Wei --> Jarvis: "The Data Engineer needs to add a 'volatility' field to the
market data schema. Handle the downstream impact."
```

**Full chain:**

```
Wei --> Jarvis: "Add volatility field to market data schema"
Jarvis --sessions_spawn(run)--> Atlas: "Schema change: add volatility field, coordinate downstream"
Atlas --sessions_spawn(run)--> CTO: creates ticket with dependency awareness
Atlas --sessions_spawn(run)--> Data Engineer: implements schema change
Atlas --> (on merge) reads DEPENDENCY_MAP.yaml, notifies affected roles
Atlas --announce-back--> Jarvis: "Schema change merged. Downstream updates in progress."
Jarvis --> Wei: "The volatility field is in. Three downstream teams have been notified."
```

**What Atlas does:**

1. Sends to **CTO** to create the ticket with dependency awareness
2. CTO creates `/Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/active/T47.md` for Data Engineer
3. Spawns **Data Engineer** run:
   ```
   sessions_spawn(
     task: "New task in apps/market_data_service/: Implement T47: Add volatility field to market data schema. Read /Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/active/T47.md first. Work in apps/market_data_service/ for this task.",
     agentId: "data_engineer",
     runtime: "acp",
     mode: "run"
   )
   ```
4. After Data Engineer's PR merges, Atlas reads `/Users/wei/Documents/SourceCode/trading_platform/docs/AI/DEPENDENCY_MAP.yaml`:
   - `libs/data/schemas/` --> notify: `[core_trading_eng, lead_quant, frontend_eng]`
5. Creates notification tickets for each affected role
6. CTO coordinates the downstream updates

---

## Step 7: Ongoing Operations

### Daily Routine (via Jarvis)

```
Wei --> Jarvis: "Jarvis, status report."
```

Jarvis spawns an Atlas run:
```
sessions_spawn(
  task: "Status report: read /Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/ACTIVE_SPRINT.md and report all active tasks with role, status, and blockers. Also report recently merged PRs.",
  agentId: "atlas",
  mode: "run"
)
```

Atlas reads `/Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/ACTIVE_SPRINT.md` and returns a structured report. Jarvis summarizes it:

> "Three tasks in progress, one blocked. The frontend vol widget (T43) is almost done, waiting on QA. The Slack alerts (T44) are blocked on a missing lib dependency. DevOps has a clean PR for alert thresholds. Nothing else urgent."

### Checking for Blocked Work

```
Wei --> Jarvis: "Jarvis, what's blocked?"
```

Jarvis spawns an Atlas run:
```
sessions_spawn(
  task: "Report all BLOCKED tasks: scan /Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/active/ for tickets in BLOCKED or LIB_CHANGE_REQUEST state. Include the blocker reason for each.",
  agentId: "atlas",
  mode: "run"
)
```

Atlas scans active tickets and returns blockers. Jarvis summarizes:

> "One task blocked: T44 (Slack alerts) needs a change to libs/platform/alerts/ that Platform Services can't do alone. The CTO has been notified to create a Library Change Request ticket."

### Reprioritizing

```
Wei --> Jarvis: "Jarvis, tell the team to prioritize the circuit breaker bug over everything else."
```

Jarvis spawns an Atlas run:
```
sessions_spawn(
  task: "Wei says: reprioritize — the circuit breaker duplicate orders bug is now P0, everything else is secondary. Update /Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/ACTIVE_SPRINT.md and notify affected agents.",
  agentId: "atlas",
  mode: "run"
)
```

Atlas updates `/Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/ACTIVE_SPRINT.md` and notifies affected agents.

### Pausing All Work

```
Wei --> Jarvis: "Jarvis, pause all work."
```

Jarvis spawns an Atlas run:
```
sessions_spawn(
  task: "HALT: Stop all active work immediately. Set all active tickets to PAUSED state (store previous state in paused_from metadata). Mark the sprint as PAUSED in /Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/ACTIVE_SPRINT.md. Do not spawn any new agent runs.",
  agentId: "atlas",
  mode: "run"
)
```

Atlas handles the pause (state-based, since agents are one-shot):
1. Reads all tickets in `/Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/active/`
2. Sets all IN_PROGRESS tickets to PAUSED state
3. Marks the sprint as PAUSED in `/Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/ACTIVE_SPRINT.md`
4. Does not spawn any new agent runs
5. Any currently running one-shot agents will complete their current run naturally
6. Reports back to Jarvis: "All work paused. {N} tickets moved from IN_PROGRESS to PAUSED."

To resume: Jarvis spawns a new Atlas run with "Resume all work." Atlas sets all PAUSED tickets back to IN_PROGRESS, marks the sprint as ACTIVE, and resumes spawning runs.

### Weekly Review

```
Wei --> Jarvis: "Jarvis, give me the weekly summary."
```

Jarvis spawns an Atlas run:
```
sessions_spawn(
  task: "Weekly summary: compile completed tasks, merged PRs, open items, blockers, and sprint velocity for this week. Read /Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/ACTIVE_SPRINT.md and recent git log.",
  agentId: "atlas",
  mode: "run"
)
```

Jarvis compiles the digest:

> "This week: 4 PRs merged (T40-T43), 1 still in review (T44), 0 blocked. Total lines changed: ~1,200. No regressions. Sprint velocity is on track."

### Post-Merge Notifications

When triggered by Jarvis/webhook/cron after a merge, Atlas:
1. Reads changed files via `gh pr diff {pr_number} --name-only`
2. Cross-references `/Users/wei/Documents/SourceCode/trading_platform/docs/AI/DEPENDENCY_MAP.yaml`
3. Creates notification tickets for affected roles
4. Updates `/Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/ACTIVE_SPRINT.md`

---

## Step 8: Living Architecture & Proactive Auditing

These mechanisms keep the codebase healthy without manual intervention, using the on-demand run model.

### 8.1: Living Architecture (Pre-Merge Sync)

Add this rule to the CTO's `AGENTS.md`:

```markdown
## Architecture Sync Gate
Before merging ANY Pull Request, evaluate if the PR introduces new services,
alters API contracts, or changes database schemas. If it does, you MUST update
`/Users/wei/Documents/SourceCode/trading_platform/docs/ARCHITECTURE/` and
`system_map.config.json` in that same branch before approving the merge.
The architecture docs must reflect the code exactly at the moment of merge.
```

### 8.2: Weekly Architecture Audit (Cron)

```bash
openclaw cron add --name weekly-architecture-audit --every 7d --agent jarvis \
  --session isolated \
  --message "Tell Atlas to spawn a CTO run for an Architecture Audit. The CTO must compare the current state of apps/ and libs/ against docs/ARCHITECTURE/system_map.config.json. If there is drift, the CTO must create a PR to fix the documentation."
```

### 8.3: Proactive Domain Sweeps

A cron job triggers all implementors to audit their domains for tech debt, bugs, and missing tests.

**Cron trigger:**

```bash
openclaw cron add --name domain-sweep --every 3d --agent jarvis \
  --session isolated \
  --message "Tell Atlas to initiate a Proactive Domain Sweep. Atlas must spawn runs for all ACP-backed implementors asking them to audit their domains for bugs, TODOs, and tech debt."
```

**Add this routing rule to Atlas's `AGENTS.md`:**

```markdown
## Domain Sweeps
When Jarvis requests a "Domain Sweep", spawn runs for all ACP-backed implementors
using this task template:

"Audit Task: Run a proactive sweep of your workspace. Search for TODO comments,
run type-checkers, check for unused imports, and identify poorly covered logic.
Do NOT fix issues immediately. Instead, write a Bug Report in
/Users/wei/Documents/SourceCode/trading_platform/docs/TASKS/active/BUG_DISCOVERY_{RoleName}.md
detailing what you found and classifying each issue as P0, P1, or P2."

After all implementors complete their sweeps, spawn a CTO run:
"Read all BUG_DISCOVERY_*.md files in docs/TASKS/active/, consolidate them,
and convert valid issues into new tickets in BACKLOG.md. Delete the discovery files."
```

---

## Appendix A: Model Selection Rationale

| Agent | Type | Model | Reasoning |
|-------|------|-------|-----------|
| Jarvis (Personal) | Native | Opus | Needs nuanced understanding of Wei's intent |
| Atlas (Orchestrator) | Native | Opus | Needs complex reasoning for routing decisions |
| CTO | Native | Opus | Architecture decisions require deep understanding |
| Lead Trader | Native | Sonnet | Focused scope, business docs only |
| QA Engineer | Native | Sonnet | Test execution, structured checklist |
| Lead Quant | ACP | Claude Code (Sonnet default) | Mathematical reasoning via Claude Code |
| Core Trading Eng | ACP | Claude Code (Sonnet default) | Implementation via Claude Code |
| Data Engineer | ACP | Claude Code (Sonnet default) | Implementation via Claude Code |
| Platform Services | ACP | Claude Code (Sonnet default) | Implementation via Claude Code |
| Frontend Engineer | ACP | Claude Code (Sonnet default) | Implementation via Claude Code |
| DevOps / SRE | ACP | Claude Code (Sonnet default) | Implementation via Claude Code |

> **Note:** Model identifiers (e.g., `anthropic/claude-opus-4-6`) follow the `provider/model` convention
> used by OpenClaw. Verify these against the OpenClaw model registry (`openclaw models list`).
> If the exact identifier differs in your installation, update the `openclaw.json` accordingly.
> ACP-backed agents use whatever model Claude Code is configured with (defaults to Sonnet).

### Cost Optimization

- Native Sonnet agents (Lead Trader, QA) handle focused tasks at lower cost
- Native Opus reserved for roles requiring complex reasoning (Jarvis, Atlas, CTO)
- ACP-backed agents use Claude Code's default model (configurable per session)
- Sub-agents default to Sonnet (`agents.defaults.subagents.model`)
- Consider switching to Haiku for notification/status tasks

---

## Appendix B: Troubleshooting

| Issue | Fix |
|-------|-----|
| Agent can't find AGENTS.md/TOOLS.md | Check `workspace` path in `openclaw.json` — OpenClaw loads these from workspace, NOT agentDir |
| ACP agent has wrong cwd | Check `runtime.acp.cwd` path in `openclaw.json` |
| ACP agent fails to launch | Verify Claude Code CLI is installed and `acpx` backend is running |
| ACP session timeout | Increase `acp.runtime.ttlMinutes` or `agents.defaults.subagents.runTimeoutSeconds` |
| Agent writes outside scope | Check `AGENTS.md` rules; scope is prompt-enforced for both native and ACP agents |
| Duplicate sessions | Verify each agent has unique `agentDir`; check `sessions_list()` for stale sessions |
| Notifications not firing | Check `DEPENDENCY_MAP.yaml` path matches |
| Gateway won't start | `openclaw gateway restart --verbose` for error details |
| Agent timeout | Increase `runTimeoutSeconds` in config |
| Jarvis can't reach Atlas | Check `subagents.allowAgents` includes `"atlas"` |
| Atlas can't spawn roles | Check `subagents.allowAgents` includes the target agent ID and `maxSpawnDepth` >= 2 |
| sessions_spawn fails | Check `maxConcurrent` limit and `maxChildrenPerAgent` (must be >= 16); use `sessions_list()` to see active count |
| ACP agent gets wrong context | Verify `cwd` points to correct directory with appropriate `CLAUDE.md` |

---

## Appendix C: Agent Type Comparison

| Aspect | Native OpenClaw | ACP-backed (Claude Code) |
|--------|----------------|--------------------------|
| LLM runtime | OpenClaw's built-in LLM | Claude Code as external process |
| Context files | `AGENTS.md` + `TOOLS.md` from workspace | `AGENTS.md` from workspace + `CLAUDE.md` from cwd (no separate TOOLS.md needed) |
| Shell access | Via `exec` tool (if allowed) | Via Claude Code's built-in bash |
| File access | Via OpenClaw's read/write/edit tools | Via Claude Code's built-in file tools |
| Session management | Can use `sessions_spawn` (if allowed) | Cannot spawn sub-agents |
| Best for | Orchestration, docs, review | Code implementation, testing |
| Examples | Jarvis, Atlas, Lead Trader, CTO, QA | Lead Quant, Data Eng, Core Trading, etc. |

Sources:
- [OpenClaw Multi-Agent Routing](https://docs.openclaw.ai/concepts/multi-agent)
- [OpenClaw Sub-Agents](https://docs.openclaw.ai/tools/subagents)
- [OpenClaw ACP Integration](https://docs.openclaw.ai/acp)
- [OpenClaw GitHub](https://github.com/openclaw/openclaw)
- [OpenClaw CLI Reference](https://docs.openclaw.ai/cli)
