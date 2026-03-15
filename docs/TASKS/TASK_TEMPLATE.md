---
id: T{N}
title: "{Title}"
priority: P0|P1|P2
owner: "{agent_role}"
state: TASK
created: {YYYY-MM-DD}
dependencies: []
related_adrs: []
related_docs: []
components: [T{N}.1, T{N}.2]
estimated_effort: "{X-Y days}"
---

# T{N}: {Title}

**Status:** TASK | PLANNING | TICKET_REVIEW | RFC_REVIEW | RFC_REVISION | COMPONENT_PLANNING | IN_PROGRESS | CODE_REVIEW | PR_OPEN | QA_APPROVED | ARCHITECTURE_REVIEW | MERGE_READY | MERGE | MERGE_FAILED | REWORK | BLOCKED | LIB_CHANGE_REQUEST | NOTIFICATION | PAUSED | CANCELLED | DONE
**Priority:** P0|P1|P2
**Owner:** {Agent Role}
**Execute in:** {/apps/service_name/ or /strategies/ etc.}
**Created:** {YYYY-MM-DD}
**Track:** {N} of {total}
**Dependency:** {deps or None}
**Estimated Effort:** {X-Y days}

## Objective

{1-3 sentence problem statement}

**Success looks like:**
- {Measurable outcome 1}
- {Measurable outcome 2}

**Out of Scope:**
- {Item 1 — prevents agent drift}

## Pre-Implementation Analysis

> This section is filled by the Implementor during COMPONENT_PLANNING.
> Run `/analyze` first, then document findings here.

**Existing Infrastructure:**
| Component | Status | Location |
|-----------|--------|----------|
| {component} | EXISTS / DOES NOT EXIST | {path} |

**Key Findings:**
- {Finding from /analyze}

## Tasks

### T{N}.1: {Component Title}

**Goal:** {One sentence}

**Features:**
- {Feature detail 1}
- {Feature detail 2}

**Acceptance Criteria:**
- [ ] {Testable criterion 1}
- [ ] {Testable criterion 2}
- [ ] **Security/RBAC:** {Permission requirement if applicable}
- [ ] Unit tests > 85% coverage for new code

**Files:**
- Create: `{path/to/new/file.py}`
- Modify: `{path/to/existing/file.py}`

**Estimated Effort:** {X days}

### T{N}.2: {Component Title}
{Same structure as T{N}.1}

## Dependencies

```
T{N}.1 → T{N}.2 (sequential)
T{N}.3 → standalone
```

{Text description of cross-task dependencies}

## Testing Strategy

**Unit Tests:**
- {Specific test scenario 1}
- {Specific test scenario 2}

**Integration Tests:**
- {Cross-module workflow test}

## Library Change Requests

> If the implementor needs changes outside their scope, document here.
> CTO must approve before proceeding.

- None / {Describe needed lib changes}

## RFC Feedback

> This section is filled by implementors during RFC_REVIEW.
> Each implementor reviews their component scope and provides feedback.

### {Role Name} — {date}
**Status:** APPROVED / BLOCKING / ADVISORY

**Concerns:**
- [BLOCKING] {Describe concern and why it blocks implementation}
- [ADVISORY] {Suggest alternative approach, but can work with current plan}

**Analysis:**
{Any /review-plan findings or technical analysis supporting the concerns}

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
