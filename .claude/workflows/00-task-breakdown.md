# Task Breakdown & Subfeature Branching

**Purpose:** Break down large tasks into manageable subfeatures with independent branches
**Prerequisites:** Task ticket exists in `/docs/TASKS/`, understand task scope
**Expected Outcome:** Clear decomposition plan with PxTy-Fz subfeature branches
**Owner:** @development-team
**Last Reviewed:** 2025-10-24

---

## When to Use This Workflow

**Use subfeature branching when:**
- Task is complex (>8 hours estimated time)
- Task has multiple independent logical components
- Want to create progressive PRs instead of one large PR
- Different components can be reviewed/merged separately
- Risk of conflicts if working in single branch too long

**DON'T use subfeature branching when:**
- Task is simple (<4 hours)
- Components are tightly coupled (can't split cleanly)
- Single focused change
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

Read the task ticket and estimate:
- Total time: <4h (single branch), 4-8h (consider splitting), >8h (split!)
- Number of independent components
- Dependencies between components
- Risk of merge conflicts

**Step 2: Identify logical subfeatures**

Break task into independent components with these criteria:
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

**Main Branch:** feature/P1T11-workflow-optimization

**Subfeatures:**
1. **P1T11-F1:** Tool Access Restriction Documentation (2-3h)
   - Document read-only file access patterns
   - Update workflow guides
   - Branch: feature/P1T11-F1-tool-restriction

2. **P1T11-F2:** Hard Gates via Pre-commit Framework (4-6h)
   - Implement pre-commit hooks
   - Add test validation
   - Branch: feature/P1T11-F2-hard-gates

3. **P1T11-F3:** Subfeature Branching Strategy (2-3h)
   - Document PxTy-Fz convention
   - Update GIT_WORKFLOW.md
   - Branch: feature/P1T11-F3-subfeature-docs

**Total:** 8-12 hours (split into 3 subfeatures)
```

---

## Workflow Steps

### Step 1: Create Main Task Branch (Optional)

If you want to merge all subfeatures before submitting final PR:

```bash
# Create main task branch
git checkout master
git pull
git checkout -b feature/P1T11-workflow-optimization

# Push empty branch to track progress
git push -u origin feature/P1T11-workflow-optimization
```

**OR** merge subfeatures directly to master (simpler):

```bash
# No main branch needed - each subfeature PR goes to master
```

### Step 2: Create Subfeature Branches

For each subfeature:

```bash
# Branch from master (or main task branch if using one)
git checkout master  # or feature/P1T11-workflow-optimization
git checkout -b feature/P1T11-F1-tool-restriction
```

### Step 3: Implement Using 4-Step Pattern

**CRITICAL:** Each subfeature must use the 4-step pattern for its logical components.

Example for P1T11-F1 (Tool Access Restriction Documentation):

```markdown
## P1T11-F1 Implementation Plan

Component 1: Read-only pattern documentation
- [ ] Implement: Document read-only access patterns
- [ ] Test: Validate examples are correct
- [ ] Review: Request zen-mcp quick review
- [ ] Commit: Commit documentation changes

Component 2: Workflow guide updates
- [ ] Implement: Update workflow guides with new patterns
- [ ] Test: Verify all links work
- [ ] Review: Request zen-mcp quick review
- [ ] Commit: Commit workflow updates
```

**See:** [01-git-commit.md](./01-git-commit.md) for 4-step pattern details

### Step 4: Create Subfeature PR

After all components in subfeature are complete:

```bash
# Deep zen-mcp review for entire subfeature
# See: 04-zen-review-deep.md

# Create PR
gh pr create --base master --title "P1T11-F1: Tool access restriction documentation" \
  --body "$(cat <<EOF
## Summary
Document read-only file access patterns and update workflow guides.

## Related Work
- Task: P1T11 (/docs/TASKS/P1T11_PROGRESS.md)
- Subfeature: F1 (Tool Access Restriction Documentation)

## Changes Made
- [x] Documented read-only access patterns
- [x] Updated workflow guides

## Testing
- [x] Validated examples
- [x] Verified all links work

## Zen MCP Review
- ✅ Progressive reviews: All commits reviewed
- ✅ Deep review: Completed before PR
- ✅ Final approval: Granted

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

**See:** [02-git-pr.md](./02-git-pr.md) for PR creation workflow

### Step 5: Merge Subfeature

After PR approval:

```bash
# Merge via GitHub
gh pr merge --squash

# Or via web UI
```

### Step 6: Repeat for Next Subfeature

```bash
# Update master
git checkout master
git pull

# Create next subfeature branch
git checkout -b feature/P1T11-F2-hard-gates

# Repeat steps 3-5
```

### Step 7: Complete Task

After all subfeatures merged:

```bash
# 1. Update task ticket YAML front-matter
# Edit P1T11_PROGRESS.md:
#   - Change: state: PROGRESS → state: DONE
#   - Add: completed: 2025-10-24

# 2. Rename ticket file
mv docs/TASKS/P1T11_PROGRESS.md docs/TASKS/P1T11_DONE.md

# 3. Commit ticket status update
git add docs/TASKS/P1T11_DONE.md
git commit -m "Mark P1T11 complete: All subfeatures merged"
```

**IMPORTANT:** Always update the YAML front-matter **before** renaming the file to ensure metadata consistency.

---

## Examples

### Example 1: P1T11 Workflow Optimization (Large Task)

**Original estimate:** 10-15 hours
**Decision:** Split into 3 subfeatures

```
Main Task: P1T11 Workflow Optimization & Testing Fixes

Subfeature F1 (3h): Tool Access Restriction Docs
├─ Branch: feature/P1T11-F1-tool-restriction
├─ PR #31: 2 files, 150 lines
└─ Merged: 2025-10-23

Subfeature F2 (6h): Hard Gates via Pre-commit
├─ Branch: feature/P1T11-F2-hard-gates
├─ PR #32: 11 files, 800 lines
└─ Merged: 2025-10-24

Subfeature F3 (3h): Subfeature Branching Docs
├─ Branch: feature/P1T11-F3-subfeature-docs
├─ PR #33: 4 files, 400 lines
└─ Status: In Progress

Total: 3 focused PRs instead of 1 massive PR
```

**Benefits:**
- Each PR < 500 lines (easier review)
- F1 and F2 merged while F3 still in progress
- Clear incremental progress
- Reduced merge conflict risk

### Example 2: P0T4 Idempotent Orders (Medium Task)

**Original estimate:** 6 hours
**Decision:** Split into 2 subfeatures

```
Main Task: P0T4 Idempotent Order Submission

Subfeature F1 (3h): Deterministic ID Generation
├─ Branch: feature/P0T4-F1-id-generation
├─ Components: Hash function, unit tests
└─ PR #15: Merged

Subfeature F2 (3h): Duplicate Detection Logic
├─ Branch: feature/P0T4-F2-duplicate-detection
├─ Components: Dedup logic, integration tests
└─ PR #16: Merged
```

### Example 3: P0T1 Project Bootstrap (Simple Task)

**Original estimate:** 3 hours
**Decision:** Single branch (no subfeatures needed)

```
Task: P0T1 Initial Project Setup
├─ Branch: feature/P0T1-initial-setup
├─ Components:
│   ├─ Directory structure (commit 1)
│   ├─ Docker compose (commit 2)
│   └─ README and docs (commit 3)
└─ PR #1: Merged

Reason: Tightly coupled components, simple task, single PR is fine
```

---

## Decision Tree

```
Is task >8 hours?
├─ YES → Split into subfeatures
│   ├─ Create PxTy-F1, PxTy-F2, PxTy-F3 branches
│   ├─ Each subfeature = independent PR
│   └─ Merge subfeatures progressively
│
└─ NO → Is task 4-8 hours?
    ├─ YES → Consider splitting
    │   ├─ Are components independent?
    │   │   ├─ YES → Split into subfeatures
    │   │   └─ NO → Single branch
    │   └─ Would splitting reduce review burden?
    │       ├─ YES → Split into subfeatures
    │       └─ NO → Single branch
    │
    └─ NO (<4 hours) → Single branch
        ├─ Use 4-step pattern for components
        └─ Create one PR when complete
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
- ✅ Merge subfeatures as they complete (progressive progress)

### DON'T:
- ❌ Create subfeatures for simple tasks (<4h)
- ❌ Make subfeatures dependent on each other (F2 needs F1)
- ❌ Skip 4-step pattern within subfeatures
- ❌ Create one massive PR for all subfeatures
- ❌ Use subfeature branches as long-running feature branches

### Subfeature Naming Guidelines

**Good subfeature names:**
```bash
feature/P1T11-F1-tool-restriction        # ✅ Clear, focused
feature/P0T4-F1-id-generation           # ✅ Describes what it does
feature/P2T5-F2-backtest-replay         # ✅ Specific component
```

**Bad subfeature names:**
```bash
feature/P1T11-F1-misc                   # ❌ Too vague
feature/P0T4-F1-part1                   # ❌ Not descriptive
feature/P2T5-F2-fixes                   # ❌ Not specific
```

---

## Integration with Other Workflows

**This workflow integrates with:**

1. **Progressive commits:** [01-git-commit.md](./01-git-commit.md)
   - Use 4-step pattern within each subfeature
   - Commit every 30-60 min

2. **Pull requests:** [02-git-pr.md](./02-git-pr.md)
   - Create separate PR for each subfeature
   - Merge progressively

3. **Zen reviews:** [03-zen-review-quick.md](./03-zen-review-quick.md), [04-zen-review-deep.md](./04-zen-review-deep.md)
   - Quick review before each commit
   - Deep review before each subfeature PR

4. **Testing:** [05-testing.md](./05-testing.md)
   - Tests must pass for each subfeature PR

---

## Troubleshooting

### Problem: Subfeatures have dependencies

**Symptom:** F2 requires F1 to be merged first

**Solution:**
- Merge F1 first, then branch F2 from updated master
- OR reconsider decomposition - maybe they should be one subfeature
- OR use stacked PRs (F2 targets F1 branch, not master)

### Problem: Too many subfeatures

**Symptom:** Task split into 8+ subfeatures

**Solution:**
- You're over-decomposing
- Group related components into larger subfeatures
- Target 2-4 subfeatures max per task

### Problem: Subfeatures are too small

**Symptom:** Each subfeature is <1 hour of work

**Solution:**
- Combine into larger subfeatures
- OR use single branch with 4-step pattern at component level

### Problem: Main branch getting stale

**Symptom:** Subfeature branch diverged significantly from master

**Solution:**
```bash
# Rebase subfeature on latest master
git checkout feature/P1T11-F3-subfeature-docs
git fetch origin
git rebase origin/master

# Resolve conflicts
# Push (may need force push if already pushed)
git push --force-with-lease
```

---

## Related Workflows

- [01-git-commit.md](./01-git-commit.md) - Progressive commits within subfeatures
- [02-git-pr.md](./02-git-pr.md) - Creating PRs for subfeatures
- [03-zen-review-quick.md](./03-zen-review-quick.md) - Review before commits
- [04-zen-review-deep.md](./04-zen-review-deep.md) - Review before subfeature PRs
- [05-testing.md](./05-testing.md) - Testing requirements

---

## Related Standards

- [GIT_WORKFLOW.md](../../docs/STANDARDS/GIT_WORKFLOW.md) - Branch naming policies
- [TESTING.md](../../docs/STANDARDS/TESTING.md) - Test requirements
- [DOCUMENTATION_STANDARDS.md](../../docs/STANDARDS/DOCUMENTATION_STANDARDS.md) - Doc requirements

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
