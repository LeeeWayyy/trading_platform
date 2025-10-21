# Claude Code Workflows

**Purpose:** Step-by-step operational guides for common development tasks
**Last Updated:** 2025-10-21
**Maintained By:** Development Team

---

## 📖 Quick Start

1. **New to the project?** Start with [CLAUDE.md](../../CLAUDE.md) for project overview
2. **Need to do a specific task?** Find the workflow below
3. **Workflow doesn't exist?** Use [00-template.md](./00-template.md) to create one

---

## 📋 Workflow Index

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

### Project Management (12-19)

| Workflow | Purpose | When to Use |
|----------|---------|-------------|
| [12-phase-management.md](./12-phase-management.md) | Manage project phases and generate tasks | Starting new phase or breaking down work |

---

## 🎯 Workflow Categories

### By Frequency

**Every Development Session:**
- 01-git-commit.md (every 30-60 min)
- 03-zen-review-quick.md (before each commit)
- 05-testing.md (before commits)

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
- 01-git-commit.md
- 02-git-pr.md
- 03-zen-review-quick.md
- 04-zen-review-deep.md
- 05-testing.md
- 06-debugging.md
- 07-documentation.md

**Architecture/Lead Developers:**
- 08-adr-creation.md
- 09-deployment-rollback.md
- 12-phase-management.md

**DevOps/Infrastructure:**
- 10-ci-triage.md
- 11-environment-bootstrap.md

---

## 🔄 Typical Development Flow

```
Start Feature
    ↓
[11-environment-bootstrap.md] ← (if first time)
    ↓
Implement Code (30-60 min)
    ↓
[05-testing.md] ← Run tests
    ↓
[03-zen-review-quick.md] ← MANDATORY review
    ↓
Fix Issues Found
    ↓
[01-git-commit.md] ← Commit
    ↓
Repeat until feature complete
    ↓
[04-zen-review-deep.md] ← MANDATORY comprehensive review
    ↓
Fix Issues Found
    ↓
[02-git-pr.md] ← Create PR
    ↓
[10-ci-triage.md] ← (if CI fails)
    ↓
Merge & Deploy
    ↓
[09-deployment-rollback.md] ← (if needed)
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
- [docs/IMPLEMENTATION_GUIDES/](../../docs/IMPLEMENTATION_GUIDES/) - Detailed feature implementation guides

**Architecture Decisions:**
- [docs/ADRs/](../../docs/ADRs/) - All architectural decision records

---

## 🆘 Getting Help

**Can't find the workflow you need?**
1. Check [docs/INDEX.md](../../docs/INDEX.md) for related documentation
2. Search existing workflows for similar tasks
3. Ask in team chat or create an issue
4. Create new workflow using template

**Workflow seems outdated?**
1. Check "Last Reviewed" date in workflow header
2. Test the workflow and document issues
3. Update workflow and increment review date
4. Create PR with changes

**Workflow conflicts with standards?**
1. Standards documents in `/docs/STANDARDS/` take precedence
2. Update workflow to match standards
3. If standards need updating, create ADR first

---

## 📊 Workflow Metrics

**Total Workflows:** 12 (complete coverage)
**Core Development:** 01-08 (Git, Review, Testing, Debugging, Docs, Architecture)
**Operations:** 09-11 (Deployment, CI, Bootstrap)
**Project Management:** 12 (Phase Management)
**Average Length:** Target ≤10 steps per workflow
**Review Frequency:** Quarterly or after major process changes

**Last Repository-Wide Review:** 2025-10-20

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
