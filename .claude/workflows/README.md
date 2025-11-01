# Workflow Index

**Quick reference index for finding specific step-by-step workflows.** See [CLAUDE.md](../../CLAUDE.md) for primary guidance, principles, and mandatory process steps.

---

## 📁 Shared Reference Documents

**Common patterns extracted for reuse across workflows:**

| Reference | Purpose | Referenced By |
|-----------|---------|---------------|
| [_common/git-commands.md](./_common/git-commands.md) | Git operations and conventions | 01, 02, 09, 11, 12, 13 |
| [_common/test-commands.md](./_common/test-commands.md) | Testing commands and CI workflows | 05, 06, 09, 10, 11, 12 |
| [_common/zen-review-process.md](./_common/zen-review-process.md) | Zen-MCP review system (3-tier) | 03, 04, 05, 06, 13 |

---

## 📋 Workflow Index

### Task Management & Planning (00)

| Workflow | Purpose | When to Use |
|----------|---------|-------------|
| [00-template.md](./00-template.md) | Template for creating new workflows | When creating new workflow documentation |
| [00-analysis-checklist.md](./00-analysis-checklist.md) | Pre-implementation analysis (MANDATORY) | Before writing ANY code (30-60 min) |
| [00-task-breakdown.md](./00-task-breakdown.md) | Break down large tasks into PxTy-Fz subfeatures | Before starting complex tasks (>8 hours) |
| [component-cycle.md](./component-cycle.md) | 4-step pattern for implementing components | Every logical component (Implement → Test → Review → Commit) |

### Git & Version Control (01-02)

| Workflow | Purpose | When to Use |
|----------|---------|-------------|
| [01-git-commit.md](./01-git-commit.md) | Progressive commits with zen-mcp review | Every 30-60 minutes during development |
| [02-git-pr.md](./02-git-pr.md) | Create pull requests with automation | When feature/fix is complete and ready for merge |

### Code Review & Quality (03-04)

| Workflow | Purpose | When to Use |
|----------|---------|-------------|
| [03-zen-review-quick.md](./03-zen-review-quick.md) | Quick safety check before commits | Before every commit (MANDATORY) |
| [04-zen-review-deep.md](./04-zen-review-deep.md) | Comprehensive review before PR | Before creating any pull request (MANDATORY) |

### Development & Testing (05-06)

| Workflow | Purpose | When to Use |
|----------|---------|-------------|
| [05-testing.md](./05-testing.md) | Running tests and validating code | Before commits and after implementation |
| [06-debugging.md](./06-debugging.md) | Debugging workflow and troubleshooting | When tests fail or bugs occur |

### Documentation & Architecture (07-08)

| Workflow | Purpose | When to Use |
|----------|---------|-------------|
| [07-documentation.md](./07-documentation.md) | Writing docs and docstrings | During and after implementation |
| [08-adr-creation.md](./08-adr-creation.md) | Creating Architecture Decision Records | Before making architectural changes |

### Operations & Deployment (09-11)

| Workflow | Purpose | When to Use |
|----------|---------|-------------|
| [09-deployment-rollback.md](./09-deployment-rollback.md) | Deploy to staging/prod and rollback | During releases and incident response |
| [10-ci-triage.md](./10-ci-triage.md) | Handling CI/CD pipeline failures | When CI checks fail |
| [11-environment-bootstrap.md](./11-environment-bootstrap.md) | Setting up development environment | Onboarding and fresh setup |

### Task Continuity & Session Management (12-15)

| Workflow | Purpose | When to Use |
|----------|---------|-------------|
| [12-phase-management.md](./12-phase-management.md) | Managing multi-phase feature development | During complex feature rollouts |
| [13-task-creation-review.md](./13-task-creation-review.md) | Validate task docs before starting work | Before implementing any complex task |
| [14-task-resume.md](./14-task-resume.md) | 🤖 **AUTO-RESUME** incomplete tasks | **AUTOMATIC** at session start |
| [15-update-task-state.md](./15-update-task-state.md) | Keep task-state.json synchronized | After completing each component |

---

## 🎯 Workflow Categories

### By Frequency

**Before Starting ANY Implementation:**
- 00-analysis-checklist.md (MANDATORY pre-implementation analysis, 30-60 min)
- 13-task-creation-review.md (validate task docs before work)

**Before Starting Complex Tasks:**
- 00-task-breakdown.md (for tasks >8 hours, decompose into subfeatures)

**Every Logical Component:**
- component-cycle.md (4-step pattern: Implement → Test → Review → Commit)

**Every Development Session:**
- 01-git-commit.md (every 30-60 min per component)
- 03-zen-review-quick.md (before each commit, MANDATORY)
- 05-testing.md (before commits)
- 15-update-task-state.md (after completing each component)

**Each Feature/Fix:**
- 04-zen-review-deep.md (before PR)
- 02-git-pr.md (when complete)
- 07-documentation.md (during implementation)

**As Needed:**
- 06-debugging.md (when issues arise)
- 08-adr-creation.md (for architecture changes)
- 10-ci-triage.md (when CI fails)

**Occasionally:**
- 09-deployment-rollback.md (releases)
- 11-environment-bootstrap.md (onboarding)
- 12-phase-management.md (phase kickoff)

### By User Role

**All Developers:**
- 00-analysis-checklist.md (MANDATORY before coding)
- component-cycle.md (4-step pattern for all components)
- 01-git-commit.md
- 02-git-pr.md
- 03-zen-review-quick.md (MANDATORY before commits)
- 04-zen-review-deep.md (MANDATORY before PRs)
- 05-testing.md
- 06-debugging.md
- 07-documentation.md
- 13-task-creation-review.md
- 14-task-resume.md (auto-resumes incomplete tasks)
- 15-update-task-state.md

**Architecture/Lead Developers:**
- 00-task-breakdown.md
- 08-adr-creation.md
- 09-deployment-rollback.md
- 12-phase-management.md

**DevOps/Infrastructure:**
- 10-ci-triage.md
- 11-environment-bootstrap.md

---

## 🔄 Typical Development Flow

```
Start New Task
    ↓
[11-environment-bootstrap.md] ← (if first time setup)
    ↓
[14-task-resume.md] ← (🤖 AUTOMATIC: auto-resume incomplete tasks)
    ↓
[13-task-creation-review.md] ← Validate task document
    ↓
[00-analysis-checklist.md] ← MANDATORY 30-60 min analysis BEFORE coding
    ↓
[00-task-breakdown.md] ← (if complex task >8h, decompose into subfeatures)
    ↓
For Each Logical Component:
    ↓
  [component-cycle.md] ← 4-step pattern:
    ↓
  1. Implement Code (30-60 min)
    ↓
  2. [05-testing.md] ← Create tests, run locally
    ↓
  3. [03-zen-review-quick.md] ← MANDATORY quick review (codex, ~30 sec)
    ↓
     Fix Issues Found
    ↓
  4. [01-git-commit.md] ← Commit after review + tests pass
    ↓
  [15-update-task-state.md] ← Update task state after component
    ↓
Repeat until all components complete
    ↓
[04-zen-review-deep.md] ← MANDATORY deep review (gemini + codex, 3-5 min)
    ↓
Fix Issues Found
    ↓
[02-git-pr.md] ← Create PR
    ↓
[10-ci-triage.md] ← (if CI fails)
    ↓
Merge & Deploy
    ↓
[09-deployment-rollback.md] ← (if deployment issues)
```

---

## 📝 Creating New Workflows

1. Copy [00-template.md](./00-template.md)
2. Choose appropriate number (see numbering scheme below)
3. Follow template structure exactly
4. Add entry to this README
5. Link from relevant docs (CLAUDE.md, standards, etc.)
6. Request review before committing

### Numbering Scheme

- **01-09:** Core development lifecycle (git, reviews, testing, docs)
- **10-19:** Operations and infrastructure (CI, deployment, monitoring)
- **20-29:** Advanced workflows (performance, security, scaling)
- **30-39:** Team collaboration (onboarding, knowledge transfer)
- **90-99:** Emergency procedures (hotfix, incident response, rollback)

**When to renumber:**
- Insert workflow between existing numbers: Add +1 to all following workflows
- Document renumbering in git commit message
- Update all references in docs

---

## ✅ Workflow Quality Standards

All workflows must:
- [ ] Follow the template structure in [00-template.md](./00-template.md)
- [ ] Include clear prerequisites and expected outcomes
- [ ] Provide concrete examples (not just theory)
- [ ] Link to relevant standards and ADRs
- [ ] List related workflows for navigation
- [ ] Include troubleshooting section
- [ ] Specify owner and last review date
- [ ] Keep steps ≤10 for clarity (split if longer)
- [ ] Use consistent terminology with project glossary

---

## 🔗 Related Documentation

**Project Overview:**
- [CLAUDE.md](../../CLAUDE.md) - Project introduction and quick reference
- [docs/INDEX.md](../../docs/INDEX.md) - Complete documentation index

**Standards (MUST follow):**
- [docs/STANDARDS/CODING_STANDARDS.md](../../docs/STANDARDS/CODING_STANDARDS.md)
- [docs/STANDARDS/DOCUMENTATION_STANDARDS.md](../../docs/STANDARDS/DOCUMENTATION_STANDARDS.md)
- [docs/STANDARDS/GIT_WORKFLOW.md](../../docs/STANDARDS/GIT_WORKFLOW.md)
- [docs/STANDARDS/TESTING.md](../../docs/STANDARDS/TESTING.md)
- [docs/STANDARDS/ADR_GUIDE.md](../../docs/STANDARDS/ADR_GUIDE.md)

**Implementation Guides:**
- [docs/TASKS/](../../docs/TASKS/) - Task implementation files with detailed guides

**Architecture Decisions:**
- [docs/ADRs/](../../docs/ADRs/) - All architectural decision records

---

## 🆘 Getting Help

**Start with [CLAUDE.md](../../CLAUDE.md)** for project principles, mandatory steps, and process overview. This index helps you find specific workflow details.

---

## 📊 Workflow Metrics

**Total Workflows:** 20 workflows + 3 shared reference docs
**Shared References:** `_common/` (git-commands, test-commands, zen-review-process)
**Task Management:** 00-template, 00-analysis-checklist, 00-task-breakdown, component-cycle, 14-15
**Core Development:** 01-08 (Git, Review, Testing, Debugging, Docs, Architecture)
**Operations:** 09-11 (Deployment, CI, Bootstrap)
**Project Management:** 12-13 (Phase Management, Task Creation Review)

**Documentation Size:**
- Baseline (before Phase 2): 8,854 lines
- Current (after Phase 2): 5,354 lines
- Reduction: 3,500 lines (39.5%)
- Target achieved: Exceeded 50% floor (4,427 lines)

**Review Frequency:** Quarterly or after major process changes
**Last Repository-Wide Review:** 2025-11-01 (P1T13 Phase 2: Workflow Simplification)

---

## 🎓 Workflow Maintenance

**Quarterly Review Process:**
1. Check each workflow for accuracy (test steps manually)
2. Update screenshots/examples if UI changed
3. Verify all links still work
4. Update "Last Reviewed" date
5. Archive obsolete workflows to `/archive/`

**Trigger for Updates:**
- Tool/framework version changes
- Process improvements discovered
- Feedback from team members
- Standards documents updated
- New tools/automation added

**Owners:**
- Git workflows (01-02): @development-team
- Review workflows (03-04): @development-team + zen-mcp maintainers
- Testing/Debugging (05-06): @qa-team
- Docs/ADR (07-08): @tech-writers + architecture-team
- Operations (09-11): @devops-team
- Project Management (12): @architecture-team

---

**Questions or suggestions?** Open an issue or PR with the `documentation` label.
