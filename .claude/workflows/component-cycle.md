# Component Development Cycle (4-Step Pattern)

**Purpose:** Provide a reusable checklist for implementing any logical component safely.
**When to Use:** Every time you introduce, refactor, or retire a discrete unit of functionality (API endpoint, service, module, strategy, etc.).

---

## Why This Pattern Exists

Trading workflows fail when we skip validation steps. This pattern keeps the loop tight:

1. **Implementation remains focused** – prevents mixing unrelated changes.
2. **Tests are written before confidence fades** – regression risk drops.
3. **Zen-MCP review happens with full context** – reviewers see tested code.
4. **Commits stay small and auditable** – easier to bisect and roll back.

Treat these steps as a **mini life cycle** for each component. Never advance to the next stage until the current one is complete.

---

## The Four Steps

| Step | Description | Expected Outputs |
|------|-------------|------------------|
| 1. Implement | Write or update the production code. | Feature code, docs, configuration updates. |
| 2. Test | Create/extend automated tests and run them locally. | Passing unit/integration tests, updated fixtures. |
| 3. Review | Request zen-mcp quick review for the staged changes. | Review transcript + continuation_id, fixes applied. |
| 4. Commit | Commit only what was reviewed and approved. | Clean commit with descriptive message. |

**Markdown Todo Template**
```markdown
- [ ] Implement <component>
- [ ] Create tests for <component>
- [ ] Request zen-mcp review for <component>
- [ ] Commit <component>
```

Replace `<component>` with the concrete scope (e.g., "position limit validation").

---

## Usage Checklist

- Track the four todos in your task ticket or working notes.
- Only stage files related to the current component before requesting review.
- Capture review findings immediately while context is fresh.
- If a finding requires larger refactors, create a follow-up component cycle rather than expanding scope mid-stream.

---

## Anti-Patterns to Avoid

| Anti-Pattern | Why It Fails | Fix |
|--------------|-------------|-----|
| Single todo like "Implement & commit" | Skips tests and review. | Expand into four explicit todos. |
| Combining multiple components in one cycle | Creates mega commits and confusing reviews. | Run the cycle separately for each component. |
| Deferring zen review until "the end" | Reviewers lose context; bugs slip in. | Request review immediately after tests pass. |
| Treating documentation as optional | Trading systems rely on clarity. | Include docs/config updates in the implementation step. |

---

## FAQ

**Q: Can I merge the test and implementation steps if I practice TDD?**
A: Keep them separate in the checklist. You may iterate between them, but both boxes must be checked explicitly.

**Q: What if the review suggests additional tests?**
A: Add the tests, restage, rerun quick review using the continuation_id, then proceed to commit.

**Q: How does this align with deep review?**
A: Quick reviews happen per component cycle. Deep review aggregates all completed cycles before opening the PR.

---

Keep this file bookmarked; other workflows will reference it instead of duplicating the full explanation.
