# Task Breakdown & Subfeature Branching

**Purpose:** Break down large tasks into manageable subfeatures with independent branches
**Prerequisites:** Task ticket exists in `/docs/TASKS/`, understand task scope
**Expected Outcome:** Clear decomposition plan with PxTy-Fz subfeature branches
**Owner:** @development-team
**Last Reviewed:** 2025-10-24

---

## Quick Reference

**Git:** See [Git Commands Reference](./_common/git-commands.md)
**Component Pattern:** See [component-cycle.md](./component-cycle.md)

---

## When to Use This Workflow

**Use subfeature branching when:**
- Task is complex (>8 hours estimated time)
- Task has multiple independent logical components
- Want to create progressive PRs instead of one large PR
- Different components can be reviewed/merged separately

**DON'T use when:**
- Task is simple (<4 hours)
- Components are tightly coupled (can't split cleanly)
- Already following 4-step pattern at component level

---

## Task Decomposition Strategy

### Naming Convention

**Pattern:** `PxTy-Fz` where:
- **Px** = Phase number (e.g., P0, P1, P2)
- **Ty** = Task number within phase (e.g., T1, T2, T11)
- **Fz** = Subfeature number (optional, e.g., F1, F2, F3)

**Branch naming:** `<type>/PxTy(-Fz)?-<description>`

**Examples:**
```bash
# Main task branch (if not using subfeatures)
feature/P1T11-workflow-optimization

# Subfeature branches (when decomposing large task)
feature/P1T11-F1-tool-restriction
feature/P1T11-F2-hard-gates
feature/P1T11-F3-subfeature-docs
```

### Decomposition Process

**Step 1: Analyze task complexity**

- Total time: <4h (single branch), 4-8h (consider splitting), >8h (split!)
- Number of independent components
- Dependencies between components
- Risk of merge conflicts

**Step 2: Identify logical subfeatures**

Break task into independent components:
- Each subfeature solves one focused problem
- Subfeatures can be implemented in any order (minimal dependencies)
- Each subfeature provides incremental value
- Each subfeature is reviewable independently (< 500 lines)
- Each subfeature follows 4-step pattern internally

**Step 3: Create decomposition plan**

Document in task ticket or create `PxTy_PROGRESS.md`:

```markdown
# P1T11: Workflow Optimization & Testing Fixes

## Decomposition

**Subfeatures:**
1. **P1T11-F1:** Tool Access Restriction Documentation (2-3h)
   - Branch: feature/P1T11-F1-tool-restriction

2. **P1T11-F2:** Hard Gates via Pre-commit Framework (4-6h)
   - Branch: feature/P1T11-F2-hard-gates

3. **P1T11-F3:** Subfeature Branching Strategy (2-3h)
   - Branch: feature/P1T11-F3-subfeature-docs

**Total:** 8-12 hours (split into 3 subfeatures)
```

---

## Workflow Steps

### Step 1: Create Subfeature Branches

```bash
# Branch from master (or main task branch if using one)
git checkout master
git checkout -b feature/P1T11-F1-tool-restriction
```

### Step 2: Implement Using 4-Step Pattern

**CRITICAL:** Each subfeature must use the [component development cycle](./component-cycle.md) for its logical components.

See [component-cycle.md](./component-cycle.md) for complete pattern documentation.

### Step 3: Create Subfeature PR

After all components in subfeature are complete:

```bash
# Deep zen-mcp review for entire subfeature (see 04-zen-review-deep.md)

# Create PR
gh pr create --base master --title "P1T11-F1: Tool access restriction documentation" \
  --body "$(cat <<EOF
## Summary
Document read-only file access patterns and update workflow guides.

## Related Work
- Task: P1T11 (/docs/TASKS/P1T11_PROGRESS.md)
- Subfeature: F1 (Tool Access Restriction Documentation)

## Zen MCP Review
- ✅ Progressive reviews: All commits reviewed
- ✅ Deep review: Completed before PR

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

See [02-git-pr.md](./02-git-pr.md) for PR creation workflow.

### Step 4: Repeat for Next Subfeature

```bash
# Update master
git checkout master && git pull

# Create next subfeature branch
git checkout -b feature/P1T11-F2-hard-gates
```

### Step 5: Complete Task

After all subfeatures merged:

```bash
# Update task ticket YAML front-matter, then rename
mv docs/TASKS/P1T11_PROGRESS.md docs/TASKS/P1T11_DONE.md

# Commit ticket status update
git add docs/TASKS/P1T11_DONE.md
git commit -m "Mark P1T11 complete: All subfeatures merged"
```

---

## Decision Tree

```
Is task >8 hours?
├─ YES → Split into subfeatures
│
└─ NO → Is task 4-8 hours?
    ├─ YES → Consider splitting (if components independent)
    └─ NO (<4 hours) → Single branch, use 4-step pattern
```

---

## Best Practices

### DO:
- ✅ Split large tasks (>8h) into subfeatures
- ✅ Make subfeatures independent (minimal dependencies)
- ✅ Use descriptive subfeature names
- ✅ Follow 4-step pattern within each subfeature
- ✅ Create separate PR for each subfeature
- ✅ Keep each PR < 500 lines when possible

### DON'T:
- ❌ Create subfeatures for simple tasks (<4h)
- ❌ Make subfeatures dependent on each other (F2 needs F1)
- ❌ Skip 4-step pattern within subfeatures
- ❌ Create one massive PR for all subfeatures

---

## Troubleshooting

### Problem: Subfeatures have dependencies

**Solution:**
- Merge F1 first, then branch F2 from updated master
- OR reconsider decomposition - maybe they should be one subfeature

### Problem: Main branch getting stale

**Solution:**
```bash
# Rebase subfeature on latest master
git checkout feature/P1T11-F3-subfeature-docs
git fetch origin
git rebase origin/master
git push --force-with-lease
```

---

## Related Workflows

- [01-git-commit.md](./01-git-commit.md) - Progressive commits within subfeatures
- [02-git-pr.md](./02-git-pr.md) - Creating PRs for subfeatures
- [component-cycle.md](./component-cycle.md) - 4-step pattern for components

---

## Quick Reference

**Subfeature branch format:**
```
<type>/PxTy-Fz-<description>
```

**When to split:**
- Task >8 hours → MUST split
- Task 4-8 hours → CONSIDER splitting
- Task <4 hours → DON'T split

**Subfeature workflow:**
1. Create branch from master: `git checkout -b feature/PxTy-Fz-description`
2. Implement using 4-step pattern
3. Deep zen review
4. Create PR
5. Merge
6. Repeat for next subfeature
