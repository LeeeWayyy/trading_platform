---
id: P1T12
title: "Workflow Review & Pre-commit Automation"
phase: P1
task: T12
priority: P1
owner: "@development-team"
state: DONE
created: 2025-10-24
started: 2025-10-25
completed: 2025-10-25
duration: 0 days
dependencies: ["P1T11"]
estimated_effort: "4-6 hours"
related_adrs: []
related_docs: [".claude/workflows/README.md", "docs/STANDARDS/GIT_WORKFLOW.md"]
features: []
---

# P1T12: Workflow Review & Pre-commit Automation

**Phase:** P1 (Hardening, 46-90 days)
**Status:** ✅ DONE (Completed Oct 25, 2025, PR #32)
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
- **17 workflows** in `.claude/workflows/` (not 13 - updated count)
- Pre-commit framework with 3 gates (branch naming, TodoWrite, CI tests)
- Documented 4-step pattern and subfeature branching strategy

**Problems Identified:**
- **7 workflows exceed 500 lines** (readability suffers, target: ≤600 lines)
- **Documentation hierarchy unclear:** CLAUDE.md vs `.claude/workflows/README.md` roles not distinct
- Potential redundancy across workflow guides
- No systematic enforcement of workflow best practices (ADR updates, documentation, etc.)
- ADR creation doesn't automatically trigger updates to README.md, CONCEPTS/, or LESSONS_LEARNED/
- Task creation review (workflow 13) is manual, no enforcement

**Goals:**
1. **Clarify documentation hierarchy:** CLAUDE.md = PRIMARY guidance, README.md = PURE INDEX
2. Simplify workflows and extract examples to reduce verbosity
3. Design (not implement) additional pre-commit gates to enforce documentation best practices
4. Design non-blocking reminder for task creation review workflow

---

## Scope

### 0. Documentation Hierarchy Fix (30 min - 1 hour) **[NEW - HIGH PRIORITY]**

**Tasks:**
- Update CLAUDE.md to explicitly position it as PRIMARY guidance document
- Slim `.claude/workflows/README.md` to pure index (2 sentences + tables only)
- Find and fix all cross-references using `rg -n "workflows/README"`
- Update all workflows to reference CLAUDE.md (not README.md) for overview

**Deliverables:**
- Updated CLAUDE.md with clear "workflow index" positioning
- Slimmed README.md (pure navigational index)
- Fixed cross-references throughout all workflows

**Rationale:** Creates clean foundation before audit (Gemini/Codex recommendation)

### 1. Workflow Audit (1.5-2 hours)

**Tasks:**
- Review all **17 workflows** in `.claude/workflows/` (updated count)
- Use table template (timebox: 10 min per workflow)
- Capture: line count, redundancy notes, missing cross-links
- Identify verbose workflows (>500 lines, target: ≤600)
- Find redundant or overlapping content
- Assess clarity and ease of discovery

**Deliverables:**
- Structured audit report (table format) documenting findings
- Prioritized list of workflows requiring simplification (wave-based: largest first)

### 2. Workflow Simplification (3-4 hours)

**Tasks:**
- Simplify top 5 verbose workflows (wave-based: largest first)
  - 01-git.md (1,114 → ~600 lines)
  - 03-reviews.md (798 → ~500 lines)
  - 01-git.md (678 → ~450 lines)
  - 11-environment-bootstrap.md (678 → ~400 lines)
  - 13-task-creation-review.md (624 → ~400 lines)
- Extract examples to `.claude/examples/` directory
- Use shared snippets for repeated content (reduce duplication)
- Add expandable appendix sections (don't delete crucial content)
- Consolidate DRAFT-pr-review-feedback-rules.md into 01-git.md

**Deliverables:**
- Simplified workflow guides (≤600 lines or justified)
- `.claude/examples/` directory with extracted examples
- Better cross-links using shared snippets

### 3. Pre-commit Gate Design (2 hours)

**Tasks:**
- Design 4 pre-commit gates (enforcement mechanisms, exit codes, integration plan)
- Document in ADR-00XX: Workflow Automation Gates
- Mark enforced steps in workflows with "🔒 ENFORCED (planned):" prefix
- Create follow-up ticket placeholders for implementation

**Gates to Design:**

1. **Task Review Reminder (Non-blocking)** **[NEW - Gemini/Codex recommendation]**
   - **Trigger:** `docs/TASKS/*.md` staged
   - **Action:** Print reminder message (echo once per commit)
   - **Exit code:** Always 0 (warning only, not blocking)
   - **Rationale:** Hard gate can be spoofed; reminder reinforces process without friction

2. **ADR Documentation Gate**
   - **Trigger:** `docs/ADRs/*.md` created/modified
   - **Checks:** README.md updated (if needed), CONCEPTS/ exists (if new concept)
   - **Exit codes:** 0 (pass), 1 (warning), 2 (fail)

3. **Test Coverage Gate**
   - **Trigger:** `apps/**/*.py` or `libs/**/*.py` modified
   - **Checks:** Corresponding test file exists and updated
   - **Exit codes:** 0 (pass), 1 (new file warning), 2 (fail)

4. **Documentation Update Gate**
   - **Trigger:** Python function signatures changed
   - **Checks:** Docstrings present and updated
   - **Exit codes:** 0 (pass), 1 (warning), 2 (fail)

**Important:** This task is **design only** - implementation explicitly deferred to future task

**Deliverables:**
- ADR-00XX documenting 4 proposed gates with "Simplicity and Maintainability" as NFR
- Updated workflows with "🔒 ENFORCED (planned):" markers
- Implementation plan with scope, effort estimate, and follow-up tickets
- CI pre-merge checklist mirroring task review reminder

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

**Revised order based on Gemini/Codex strategic guidance:**

### Phase 1: Documentation Hierarchy + Audit (2-3 hours) **[HIGHEST PRIORITY]**
**Rationale:** Creates clean foundation; hierarchy fixes inform audit

1. **Fix documentation hierarchy (30 min - 1 hour)**
   - Update CLAUDE.md quick-start to position README as workflow index
   - Slim README.md to pure index (2 sentences + tables)
   - Fix cross-references: `rg -n "workflows/README"` → update all
   - Add link-check step to audit

2. **Conduct systematic audit (1.5-2 hours)**
   - Use table template (10 min per workflow, strict timebox)
   - Capture: line count, redundancy, missing cross-links
   - Create structured audit report

**Quick Wins (Codex):** Batch doc-link rewrites with `rg`, lightweight template keeps timeboxed

### Phase 2: Simplification + ADR Checklist (3-4 hours)
**Rationale:** Audit findings drive simplification targets

1. **Simplify workflows (2.5-3 hours)**
   - Wave-based approach (largest first = maximum impact quickly)
   - Extract examples to `.claude/examples/`
   - Use shared snippets for repeated content
   - Add appendix sections (preserve crucial content)

2. **Create ADR checklist (30 min - 1 hour)**
   - Design systematic ADR lifecycle checklist
   - Integrate into `08-adr-creation.md`
   - Mark "🔒 ENFORCED (planned)" items

### Phase 3: Pre-commit Gate Design (2 hours)
**Rationale:** Design based on final, simplified workflow state

1. Design 4 gates (task reminder + 3 automation gates)
2. Create ADR-00XX with "Simplicity and Maintainability" NFR
3. Mark workflows with enforcement indicators
4. Create follow-up implementation tickets
5. **Critical:** Explicitly state "Implementation deferred" in ADR

---

## Success Criteria

- [ ] **CLAUDE.md is PRIMARY guidance** (all workflows reference it, not README.md)
- [ ] **README.md is PURE INDEX** (2 sentences + tables only, no narrative)
- [ ] All cross-references fixed and validated (`rg -n "workflows/README"` returns clean)
- [ ] Audit report completed (17 workflows analyzed with table template)
- [ ] Top 5 workflows simplified (≤600 lines or justified)
- [ ] Examples extracted to `.claude/examples/` directory
- [ ] No redundant content across workflows (shared snippets used)
- [ ] ADR update checklist integrated into `08-adr-creation.md`
- [ ] **ADR-00XX created** (4 gates designed: task reminder + 3 automation)
- [ ] Implementation explicitly deferred in ADR with follow-up tickets
- [ ] Zen-mcp review approval (deep review before PR)

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

- **Updated:** Actually 17 workflows (not 13) - discovered during initial analysis
- Codex recommended splitting from P1T11 due to scope (17 workflows to audit)
- **Strategic guidance from Gemini + Codex (continuation_id: 424c08d2-7308-4ec1-86ed-35b14f4920e5):**
  - Documentation hierarchy fix is HIGH PRIORITY foundation
  - Non-blocking task review reminder preferred over hard gate (low friction)
  - Wave-based simplification (largest first) = maximum impact
  - "Simplicity and Maintainability" as explicit NFR for gates
  - Mirror task review reminder in CI pre-merge checklist
- Focus on design over implementation for gates (explicitly defer to follow-up task)
- Leverage existing `.claude/workflows/07-documentation.md` as baseline
- Consider consolidating with 00-task-breakdown.md patterns

**Risk Mitigations:**
- Timebox audit (10 min/workflow) to prevent scope creep
- Use appendix sections instead of deleting crucial content
- Add link-check step after hierarchy fix
- Create follow-up tickets for gate implementation (avoid scope creep)

---

## References

- `.claude/workflows/README.md` - Current workflow index
- `docs/STANDARDS/GIT_WORKFLOW.md` - Git workflow standards
- `scripts/hooks/zen_pre_commit.sh` - Existing pre-commit orchestrator
- P1T11_DONE.md - Completed workflow optimization task
