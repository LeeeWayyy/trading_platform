---
id: T1
title: "Bootstrap Apex Labs OpenClaw operating model"
priority: P0
owner: "cto"
state: PLANNING
created: 2026-03-14
dependencies: []
related_adrs: []
related_docs:
  - "docs/TASKS/OPENCLAW_APEX_LABS_SETUP.md"
  - "docs/TASKS/JARVIS_APEX_LABS_BRIEFING.md"
components: [T1.1, T1.2, T1.3, T1.4]
estimated_effort: "2-4 days"
---

# T1: Bootstrap Apex Labs OpenClaw operating model

**Status:** PLANNING
**Priority:** P0
**Owner:** CTO
**Execute in:** `/Users/wei/Documents/SourceCode/trading_platform`, `~/.openclaw/agents`, and gateway config
**Created:** 2026-03-14
**Track:** 1 of 1
**Dependency:** None
**Estimated Effort:** 2-4 days

## Objective

Establish the Apex Labs agent-company operating model on top of the trading platform repo and the existing Jarvis/OpenClaw environment. This includes repo-side control documents, OpenClaw agent configuration, per-agent workspace scaffolding, automation hooks, and validation so Wei can route platform work through Atlas instead of coordinating specialists manually.

**Success looks like:**
- Jarvis can invoke Atlas using the documented one-shot run model.
- The repo contains the required task, business, and AI routing artifacts used by Atlas and role agents.
- OpenClaw is configured with the Apex Labs agent roster, workspace files, and validation evidence.

**Out of Scope:**
- Implementing product features in trading services.
- Reorganizing source-code ownership beyond what the setup plan already defines.
- Long-term process tuning after the initial bootstrap is complete.

## Pre-Implementation Analysis

> This section is filled by the Implementor during COMPONENT_PLANNING.
> Run `/analyze` first, then document findings here.

**Existing Infrastructure:**
| Component | Status | Location |
|-----------|--------|----------|
| Apex Labs setup plan | EXISTS | `docs/TASKS/OPENCLAW_APEX_LABS_SETUP.md` |
| Jarvis briefing | EXISTS | `docs/TASKS/JARVIS_APEX_LABS_BRIEFING.md` |
| Task queue scaffolding | EXISTS | `docs/TASKS/ACTIVE_SPRINT.md`, `docs/TASKS/BACKLOG.md`, `docs/TASKS/active/` |
| Business + AI routing docs | PARTIAL | `docs/BUSINESS/`, `docs/AI/DEPENDENCY_MAP.yaml`, `docs/AI/EXECUTION_MODES.yaml` |
| GitHub notification workflow | PARTIAL / UNTRACKED | `.github/workflows/atlas-notify.yml` |
| OpenClaw agent config merge | DOES NOT EXIST (verified in this ticket) | gateway config |

**Key Findings:**
- Repo-side planning artifacts exist, but no active ticket currently tracks the bootstrap work end-to-end.
- The setup plan spans repo docs, OpenClaw runtime config, local agent workspaces, and validation; it needs explicit phased ownership to avoid drift.
- Validation should be treated as a first-class deliverable, not an afterthought, because the architecture depends on agent spawning and gateway behavior.

## Tasks

### T1.1: Finalize repo control-plane artifacts

**Goal:** Ensure all repo-side business, AI-routing, and task-management files required by Atlas are present and aligned with the setup plan.

**Features:**
- Validate `docs/BUSINESS/**`, `docs/AI/DEPENDENCY_MAP.yaml`, and `docs/AI/EXECUTION_MODES.yaml` against the setup design.
- Confirm task queue templates and active-task directory support Atlas state-machine operation.
- Capture any repo-side deltas between plan and implementation.

**Acceptance Criteria:**
- [ ] Required files from Step 1 of the setup plan exist and are internally consistent.
- [ ] `docs/TASKS/ACTIVE_SPRINT.md`, `docs/TASKS/BACKLOG.md`, and `docs/TASKS/active/` are ready for live use.
- [ ] Any missing or ambiguous repo artifacts are documented before runtime setup continues.
- [ ] **Security/RBAC:** No task broadens source-write permissions beyond documented role boundaries.
- [ ] Unit tests > 85% coverage for new code

**Files:**
- Modify: `docs/BUSINESS/**`
- Modify: `docs/AI/DEPENDENCY_MAP.yaml`
- Modify: `docs/AI/EXECUTION_MODES.yaml`
- Modify: `docs/TASKS/ACTIVE_SPRINT.md`
- Modify: `docs/TASKS/BACKLOG.md`

**Estimated Effort:** 0.5 day

### T1.2: Merge Apex Labs into OpenClaw runtime configuration

**Goal:** Add Atlas and the role-agent roster to the existing OpenClaw gateway configuration without breaking Jarvis.

**Features:**
- Merge the 15-agent partial config into the current gateway config.
- Preserve existing Jarvis settings while adding Atlas compatibility requirements.
- Verify spawn-depth, auth, sandbox, and workspace references.

**Acceptance Criteria:**
- [ ] Existing gateway config is extended, not replaced.
- [ ] Atlas and all role agents resolve with correct runtime type, workspace, and agentDir.
- [ ] Jarvis can legally spawn Atlas under the resulting sandbox/spawn-depth rules.
- [ ] **Security/RBAC:** Config preserves documented scope boundaries and does not silently expand agent privileges.
- [ ] Unit tests > 85% coverage for new code

**Files:**
- Modify: `~/.openclaw/openclaw.json` or equivalent active gateway config
- Modify: `~/.openclaw/agents/jarvis/**` (only if required by spawn compatibility)

**Estimated Effort:** 0.5-1 day

### T1.3: Bootstrap agent workspace scaffolding and automation hooks

**Goal:** Create the workspace metadata and automation glue required for native and ACP-backed Apex Labs agents.

**Features:**
- Create `AGENTS.md` and `TOOLS.md` for the role-specific workspaces defined by the plan.
- Initialize agent directories under `~/.openclaw/agents/`.
- Add the GitHub workflow used to notify Jarvis/Atlas of PR events.

**Acceptance Criteria:**
- [ ] Every planned Apex Labs agent has the required workspace files.
- [ ] ACP-backed agents point at the intended repo subdirectories.
- [ ] The PR-notification workflow exists and matches the selected runtime invocation pattern.
- [ ] **Security/RBAC:** Workspace instructions clearly limit read/write scope per role.
- [ ] Unit tests > 85% coverage for new code

**Files:**
- Create: `~/.openclaw/agents/*/workspace/AGENTS.md`
- Create: `~/.openclaw/agents/*/workspace/TOOLS.md`
- Modify: `.github/workflows/atlas-notify.yml`

**Estimated Effort:** 1-1.5 days

### T1.4: Validate end-to-end orchestration and document handoff

**Goal:** Prove the Apex Labs operating model works and record the handoff path for Wei → Jarvis → Atlas.

**Features:**
- Restart the gateway and validate agent discovery.
- Smoke-test Jarvis → Atlas spawning and Atlas role delegation.
- Record known gaps, follow-up issues, and operational instructions.

**Acceptance Criteria:**
- [ ] Gateway restart succeeds after config merge.
- [ ] At least one smoke test confirms Jarvis can trigger Atlas in one-shot mode.
- [ ] Validation evidence and follow-up actions are documented in task history or linked notes.
- [ ] **Security/RBAC:** Validation does not require disabling protections beyond the design assumptions already documented.
- [ ] Unit tests > 85% coverage for new code

**Files:**
- Modify: `docs/TASKS/JARVIS_APEX_LABS_BRIEFING.md`
- Modify: `docs/TASKS/OPENCLAW_APEX_LABS_SETUP.md`
- Create: `docs/TASKS/active/T1-validation-notes.md` (optional)

**Estimated Effort:** 0.5-1 day

## Dependencies

    T1.1 → T1.2 → T1.3 → T1.4

Repo-side control artifacts should be finalized before the runtime config merge so the role boundaries are explicit. Runtime configuration should land before workspace bootstrapping and validation. Validation depends on both config and workspace scaffolding being present.

## Testing Strategy

**Unit Tests:**
- Validate YAML/Markdown/config syntax for newly created routing and task files.
- Verify agent workspace metadata paths resolve to intended directories.

**Integration Tests:**
- Restart OpenClaw gateway and confirm the agent roster loads successfully.
- Execute a Jarvis → Atlas smoke test and record the result.

## Library Change Requests

> If the implementor needs changes outside their scope, document here.
> CTO must approve before proceeding.

- None currently. If runtime setup requires source-repo changes outside docs and workflow/config assets, capture them before implementation.

## RFC Feedback

> This section is filled by implementors during RFC_REVIEW.
> Each implementor reviews their component scope and provides feedback.

### CTO — 2026-03-14
**Status:** APPROVED

**Concerns:**
- [ADVISORY] The setup plan spans both repo docs and local machine OpenClaw state, so completion evidence must distinguish committed repo artifacts from machine-local configuration.
- [ADVISORY] The PR notification workflow should be validated against the installed OpenClaw CLI syntax before relying on it in production.

**Analysis:**
The largest risk is false confidence from creating docs without proving runtime spawn behavior. The ticket therefore treats validation as a required component instead of a nice-to-have.

## Definition of Done

- [ ] All acceptance criteria met
- [ ] `/review-plan` approved by Gemini + Codex (zero tolerance)
- [ ] All code implemented per plan
- [ ] `/review` approved by Gemini + Codex (zero tolerance)
- [ ] All tests pass, coverage >= ratchet
- [ ] PR created with zen trailers
- [ ] QA review passed
- [ ] CTO architecture review passed
- [ ] No outstanding Library Change Requests
