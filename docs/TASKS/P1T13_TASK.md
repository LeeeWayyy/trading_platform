---
id: P1T13
title: "Documentation Enhancement & AI Navigation"
phase: P1
task: T13
priority: P1
owner: "@development-team"
state: TODO
created: 2025-10-24
dependencies: ["P1T11"]
estimated_effort: "3-4 hours"
related_adrs: []
related_docs: ["docs/INDEX.md", "docs/AI_GUIDE.md"]
features: []
---

# P1T13: Documentation Enhancement & AI Navigation

**Phase:** P1 (Hardening, 46-90 days)
**Status:** TODO (Not Started)
**Priority:** P1 (MEDIUM)
**Owner:** @development-team
**Created:** 2025-10-24
**Estimated Effort:** 3-4 hours
**Dependencies:** P1T11 (Workflow Optimization & Testing Fixes)

---

## Objective

Enhance existing documentation structure for better AI discoverability and human navigation by improving `docs/INDEX.md`, `docs/AI_GUIDE.md`, and adding READMEs to subdirectories.

## Background

After completing P1T11, we have improved workflow documentation but the overall `docs/` structure could be more discoverable:

**Current State:**
- `docs/INDEX.md` exists as canonical entry point
- `docs/AI_GUIDE.md` provides AI quick-start
- Documentation scattered across multiple subdirectories (ADRs, CONCEPTS, LESSONS_LEARNED, TASKS, STANDARDS, etc.)
- No READMEs in subdirectories
- `ls` commands don't reveal logical structure

**Problem:**
- Hard for AI to build complete picture of documentation landscape
- No self-documenting directory structure
- Manual navigation required to understand doc organization
- No metadata (type, status, last updated) in directory listings

**Goal:**
Enhance existing documentation infrastructure (not replace) to improve discoverability for both AI and humans.

---

## Scope

### 1. Enhance docs/INDEX.md (1-1.5 hours)

**Current State:**
- Manual curation of documentation
- Basic categorization by type

**Enhancements:**
- Add metadata to each document entry:
  - `[Status]` - CURRENT, OUTDATED, DRAFT
  - `[Updated]` - Last review date
  - `[Type]` - Standard, Concept, ADR, Task, Workflow, Guide, Runbook
- Improve categorization and grouping
- Add "Quick Links" section for AI navigation
- Document update policy (when to refresh INDEX.md)

**Example Entry:**
```markdown
### Core Standards
- [CURRENT, 2025-10-24, Standard] [CODING_STANDARDS.md](./STANDARDS/CODING_STANDARDS.md) - Python patterns and conventions
- [CURRENT, 2025-10-24, Standard] [GIT_WORKFLOW.md](./STANDARDS/GIT_WORKFLOW.md) - Git workflow and branch naming (PxTy-Fz)
```

**Deliverables:**
- Enhanced INDEX.md with metadata
- Clear categorization
- Update policy documented

### 2. Enhance docs/AI_GUIDE.md (1-1.5 hours)

**Current State:**
- Basic AI quick-start guide
- Entry point for AI assistants

**Enhancements:**
- Add "Preferred Discovery Patterns":
  - Start with INDEX.md → identify relevant category → read specific docs
  - Document → workflow relationships (e.g., ADR creation → 08-adr-creation.md)
- Add "Navigation Tips":
  - How to find relevant documentation quickly
  - Common AI query patterns and where to look
- Document relationship between workflows and docs:
  - Which workflows require which documentation updates
  - Cross-reference patterns

**Example Pattern:**
```markdown
## Discovery Pattern: Implementing New Feature

1. Start: `docs/TASKS/PxTy_TASK.md` (task definition)
2. Architecture: `docs/ADRs/` (related decisions)
3. Concepts: `docs/CONCEPTS/` (domain knowledge)
4. Standards: `docs/STANDARDS/` (code patterns)
5. Workflows: `.claude/workflows/` (process guidance)
```

**Deliverables:**
- Enhanced AI_GUIDE.md with discovery patterns
- Navigation tips section
- Workflow-docs relationship map

### 3. Add READMEs to Subdirectories (30 min - 1 hour)

**Goal:** Make directory structure self-documenting via `ls`

**Subdirectories needing READMEs:**
- `docs/ADRs/README.md` - Architecture Decision Records index
- `docs/CONCEPTS/README.md` - Trading/ML concepts index
- `docs/LESSONS_LEARNED/README.md` - Retrospectives index
- `docs/TASKS/README.md` - Task tracking index
- `docs/STANDARDS/README.md` - Development standards index
- `docs/RUNBOOKS/README.md` - Operational procedures index
- `docs/GETTING_STARTED/README.md` - Setup guides index

**README Template:**
```markdown
# [Directory Name]

**Purpose:** [What goes here]
**Audience:** [Who uses this]
**Last Updated:** [Date]

## Contents

[Auto-generated or manual list of files with brief descriptions]

## Related Documentation

- Parent index: [docs/INDEX.md](../INDEX.md)
- Related: [Links to related directories]

## Naming Convention

[How files in this directory should be named]
```

**Deliverables:**
- README.md in each major docs subdirectory
- Consistent format across READMEs
- Self-documenting structure

### 4. Improve Cross-References (30 min)

**Tasks:**
- Audit existing cross-references in documentation
- Add missing links between related docs
- Ensure bidirectional references (if A links to B, B should link to A)
- Update broken links

**Focus Areas:**
- ADRs ↔ Implementation Guides
- Workflows ↔ Standards
- Concepts ↔ Implementation Guides
- Tasks ↔ ADRs

**Deliverables:**
- Updated cross-references
- Link validation report
- No broken links

---

## Implementation Plan

### Phase 1: INDEX.md Enhancement (1-1.5 hours)
1. Add metadata fields to existing entries
2. Reorganize categories for better navigation
3. Add Quick Links section
4. Document update policy

### Phase 2: AI_GUIDE.md Enhancement (1-1.5 hours)
1. Document discovery patterns
2. Add navigation tips
3. Create workflow-docs relationship map
4. Add common query patterns

### Phase 3: Subdirectory READMEs (30 min - 1 hour)
1. Create README template
2. Generate README for each subdirectory
3. Populate with content listings
4. Add cross-references

### Phase 4: Cross-Reference Audit (30 min)
1. Audit existing links
2. Add missing cross-references
3. Fix broken links
4. Validate all links work

---

## Success Criteria

- [ ] INDEX.md has metadata for all entries (status, date, type)
- [ ] AI_GUIDE.md has discovery patterns and navigation tips
- [ ] All major docs subdirectories have README.md
- [ ] Cross-references are bidirectional and working
- [ ] No broken links in documentation
- [ ] `ls docs/` reveals self-documenting structure
- [ ] AI can navigate docs efficiently using INDEX.md and AI_GUIDE.md
- [ ] Zen-mcp review approval

---

## Out of Scope

**Not Included:**
- Auto-generated manifest (deferred pending tooling decision)
- Major directory restructuring (preserve existing organization)
- New documentation categories (only enhance existing)
- Documentation migration (keep files in current locations)

**Codex Recommendations Applied:**
- Enhance existing INDEX.md and AI_GUIDE.md (not replace)
- Manual curation (not auto-generated)
- Incremental improvements (not wholesale reorganization)

---

## Related Work

**Builds on:**
- P1T11: Workflow Optimization & Testing Fixes
  - Improved workflow documentation structure
  - Better cross-referencing patterns

**Complements:**
- P1T12: Workflow Review & Pre-commit Automation
  - ADR update checklist will reference these enhancements

**Enables:**
- Better AI-assisted development
- Faster human navigation
- Easier onboarding

---

## Testing Strategy

**Validation:**
- Manual testing of AI discovery patterns
- Link validation (all cross-references work)
- Review README completeness
- Check metadata accuracy in INDEX.md

**AI Navigation Test:**
1. Ask AI to find specific documentation
2. Verify it can navigate using INDEX.md → subdirectory README → specific doc
3. Confirm discovery patterns work as documented

**No automated tests required** (documentation-only task)

---

## Notes

- Codex recommended enhancing existing docs instead of creating new MANIFEST
- Manual curation preferred over auto-generation (simpler, more maintainable)
- Focus on improving existing INDEX.md and AI_GUIDE.md
- Preserve current directory structure (don't reorganize)
- Add metadata without changing file locations

---

## Maintenance Plan

**INDEX.md Updates:**
- Update when new docs added
- Refresh metadata quarterly
- Mark outdated docs as [OUTDATED]

**Subdirectory READMEs:**
- Update when files added/removed
- Refresh during quarterly reviews
- Keep in sync with INDEX.md

**AI_GUIDE.md:**
- Update when workflows change
- Refresh discovery patterns as needed
- Document new navigation patterns

---

## References

- `docs/INDEX.md` - Current canonical entry point
- `docs/AI_GUIDE.md` - Current AI quick-start
- `.claude/workflows/README.md` - Workflow index (good example)
- P1T11_DONE.md - Completed workflow optimization
- Codex review feedback (continuation_id: 07829478-d516-4bd6-9062-ceb973027321)
