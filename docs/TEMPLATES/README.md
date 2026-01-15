# Task and Documentation Templates

This directory contains templates for creating new tasks, features, and documentation.

## Task Templates

### 00-TEMPLATE_TASK.md
Template for creating individual task documents.

**Usage:**
```bash
cp docs/TEMPLATES/00-TEMPLATE_TASK.md docs/TASKS/P7T1_TASK.md
# Edit P7T1_TASK.md with task details
```

**When to use:** Creating a new task ticket for a specific feature or fix.

---

### 00-TEMPLATE_FEATURE.md
Template for larger feature development spanning multiple tasks.

**Usage:**
```bash
cp docs/TEMPLATES/00-TEMPLATE_FEATURE.md docs/TASKS/P7T1-F1_FEATURE.md
# Edit for feature specification
```

**When to use:** Breaking down a large task into sub-features.

---

### 00-TEMPLATE_PHASE_PLANNING.md
Template for phase planning documents (P0, P1, P2, etc.).

**Usage:**
```bash
cp docs/TEMPLATES/00-TEMPLATE_PHASE_PLANNING.md docs/TASKS/P7_PLANNING.md
# Edit with phase objectives and task breakdown
```

**When to use:** Planning a new development phase with multiple tasks.

---

### 00-TEMPLATE_PROGRESS.md
Template for tracking progress on ongoing tasks.

**Usage:**
```bash
cp docs/TEMPLATES/00-TEMPLATE_PROGRESS.md docs/TASKS/P7T1_PROGRESS.md
# Update with daily/weekly progress
```

**When to use:** Tracking implementation progress for long-running tasks.

---

### 00-TEMPLATE_DONE.md
Template for completed task documentation.

**Usage:**
```bash
cp docs/TEMPLATES/00-TEMPLATE_DONE.md docs/ARCHIVE/TASKS_HISTORY/P7T1_DONE.md
# Fill in completion details, learnings, follow-ups
```

**When to use:** Archiving completed tasks with lessons learned.

---

## Template Selection Guide

| Scenario | Template |
|----------|----------|
| Single task (1-3 days) | TEMPLATE_TASK.md |
| Large feature (1-2 weeks) | TEMPLATE_FEATURE.md |
| Phase planning (multiple weeks) | TEMPLATE_PHASE_PLANNING.md |
| Track progress on active task | TEMPLATE_PROGRESS.md |
| Archive completed work | TEMPLATE_DONE.md |

---

## Best Practices

### Task Documents
- **Be specific:** Clear acceptance criteria
- **Estimate effort:** Include time estimates
- **Link dependencies:** Reference related tasks
- **Define scope:** What's in, what's out

### Progress Tracking
- **Update frequently:** Daily or after significant milestones
- **Document blockers:** Issues preventing progress
- **Note decisions:** Architecture choices made during implementation

### Completion Documentation
- **Capture learnings:** What went well, what didn't
- **Document surprises:** Unexpected complexity or simplicity
- **List follow-ups:** Technical debt or future improvements

---

## Related Documentation

- [Task Index](../TASKS/INDEX.md) - All tasks and their status
- [AI Workflows](../AI/Workflows/) - AI-assisted development processes
- [Project Status](../GETTING_STARTED/PROJECT_STATUS.md) - Current implementation status

---

**Last Updated:** 2026-01-14
**Maintained By:** Development Team
