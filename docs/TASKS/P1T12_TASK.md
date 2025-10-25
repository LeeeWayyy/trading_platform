---
id: P1T12
title: "Workflow Review & Pre-commit Automation"
phase: P1
task: T12
priority: P1
owner: "@development-team"
state: TODO
created: 2025-10-24
dependencies: ["P1T11"]
estimated_effort: "4-6 hours"
related_adrs: []
related_docs: [".claude/workflows/README.md", "docs/STANDARDS/GIT_WORKFLOW.md"]
features: []
---

# P1T12: Workflow Review & Pre-commit Automation

**Phase:** P1 (Hardening, 46-90 days)
**Status:** TODO (Not Started)
**Priority:** P1 (HIGH)
**Owner:** @development-team
**Created:** 2025-10-24
**Estimated Effort:** 4-6 hours
**Dependencies:** P1T11 (Workflow Optimization & Testing Fixes)

---

## Objective

Audit and simplify the `.claude/workflows/` directory to improve usability, reduce redundancy, and design pre-commit automation for critical workflow enforcement.

## Background

After completing P1T11, we have:
- 13 workflows in `.claude/workflows/`
- Pre-commit framework with 3 gates (branch naming, TodoWrite, CI tests)
- Documented 4-step pattern and subfeature branching strategy

**Problem:**
- Some workflows are verbose (>10 steps)
- Potential redundancy across workflow guides
- No systematic enforcement of workflow best practices (ADR updates, documentation, etc.)
- ADR creation doesn't automatically trigger updates to README.md, CONCEPTS/, or LESSONS_LEARNED/

**Goal:**
Simplify workflows and design (not implement) additional pre-commit gates to enforce documentation best practices.

---

## Scope

### 1. Workflow Audit (1-2 hours)

**Tasks:**
- Review all 13 workflows in `.claude/workflows/`
- Identify verbose workflows (>10 steps)
- Find redundant or overlapping content
- Assess clarity and ease of discovery

**Deliverables:**
- Audit report documenting findings
- List of workflows requiring simplification

### 2. Workflow Simplification (2-3 hours)

**Tasks:**
- Consolidate duplicate information across workflows
- Simplify verbose workflows (target: ≤10 steps each)
- Improve cross-referencing between related workflows
- Update `.claude/workflows/README.md` with better navigation

**Deliverables:**
- Simplified workflow guides
- Improved README with categorization
- Better cross-links between workflows

### 3. Pre-commit Gate Design (1-2 hours)

**Tasks:**
- Enumerate workflow steps that should be enforced via pre-commit hooks
- Design enforcement mechanisms (e.g., check for ADR creation, documentation updates)
- Document gates in ADR (ADR-00XX: Workflow Automation Gates)
- Mark enforced steps in workflows with "🔒 ENFORCED:" prefix

**Candidate Gates:**
- **ADR Documentation Gate:** When ADR is created/modified, ensure:
  - README.md updated (relevant sections)
  - CONCEPTS/ has corresponding concept doc (if needed)
  - LESSONS_LEARNED/ retrospective created (after completion)
- **Test Coverage Gate:** Ensure tests exist for code changes
- **Documentation Update Gate:** Code changes require docstring updates

**Important:** This task is **design only** - implementation deferred to future task

**Deliverables:**
- ADR documenting proposed pre-commit gates
- Updated workflows with "🔒 ENFORCED:" markers
- Implementation plan (scope, effort estimate)

### 4. ADR Update Checklist (30 min - 1 hour)

**Tasks:**
- Create systematic checklist for ADR lifecycle
- Integrate checklist into `.claude/workflows/08-adr-creation.md`
- Document enforcement approach (manual checklist vs. pre-commit gate)

**Checklist Items:**
- ✓ ADR created in `docs/ADRs/`
- ✓ README.md updated (if introducing new component/service)
- ✓ CONCEPTS/ doc created (if introducing new concept)
- ✓ LESSONS_LEARNED/ retrospective planned (after implementation)
- ✓ Related workflows updated (if process changes)

**Deliverables:**
- ADR update checklist
- Updated 08-adr-creation.md workflow
- Decision on enforcement mechanism

---

## Implementation Plan

### Phase 1: Audit (1-2 hours)
1. Review all workflows systematically
2. Document findings in audit report
3. Prioritize workflows for simplification

### Phase 2: Simplification (2-3 hours)
1. Simplify top 3-5 verbose workflows
2. Consolidate duplicate content
3. Improve cross-references
4. Update README

### Phase 3: Gate Design (1-2 hours)
1. Enumerate candidate gates
2. Create ADR for workflow automation
3. Document enforcement mechanisms
4. Mark workflows with enforcement indicators

### Phase 4: ADR Checklist (30 min - 1 hour)
1. Create ADR update checklist
2. Integrate into 08-adr-creation.md
3. Document enforcement approach

---

## Success Criteria

- [ ] All workflows ≤10 steps (or clearly justified if longer)
- [ ] No redundant content across workflows
- [ ] `.claude/workflows/README.md` has clear navigation
- [ ] ADR created documenting proposed pre-commit gates
- [ ] ADR update checklist integrated into workflow
- [ ] Implementation plan for gates documented
- [ ] Zen-mcp review approval

---

## Out of Scope

- **Gate implementation:** Deferred to separate task
- **Major workflow restructuring:** Focus on simplification, not redesign
- **New workflow creation:** Only modify existing workflows

---

## Related Work

**Builds on:**
- P1T11: Workflow Optimization & Testing Fixes
  - Component B: Hard Gates via Pre-commit Framework
  - Component C: Subfeature Branching Strategy Documentation

**Enables:**
- Future pre-commit gate implementation
- Better workflow adoption and compliance
- Systematic documentation updates

---

## Testing Strategy

**Validation:**
- Manual review of simplified workflows for clarity
- Check all cross-references work
- Verify checklist completeness
- Zen-mcp review for design soundness

**No automated tests required** (documentation-only task)

---

## Notes

- Codex recommended splitting from P1T11 due to scope (16+ workflows to audit)
- Focus on design over implementation for gates
- Leverage existing `.claude/workflows/07-documentation.md` as baseline
- Consider consolidating with 00-task-breakdown.md patterns

---

## References

- `.claude/workflows/README.md` - Current workflow index
- `docs/STANDARDS/GIT_WORKFLOW.md` - Git workflow standards
- `scripts/hooks/zen_pre_commit.sh` - Existing pre-commit orchestrator
- P1T11_DONE.md - Completed workflow optimization task
