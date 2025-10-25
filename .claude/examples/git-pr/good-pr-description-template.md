# Good PR Description Template

Use this template when creating pull requests with `gh pr create`:

```bash
gh pr create --title "[Type] Brief description (Ticket)" --body "$(cat <<'EOF'
## Summary
[Brief 1-2 sentence description of what was implemented and why]

## Related Work
- **Ticket:** [PxTy] - [Task title]
- **ADR:** [Link to ADR if architectural change]
- **Implementation Guide:** [Link to task DONE file]

## Changes Made
- [x] [Specific change 1]
- [x] [Specific change 2]
- [x] [Specific change 3]
- [x] Add comprehensive error handling
- [x] Add unit tests ([N] new tests)
- [x] Add integration tests ([N] tests)
- [x] Update OpenAPI spec (if API changed)
- [x] Add concept documentation

## Zen-MCP Review ⚠️ MANDATORY

### Progressive Reviews (Commits 1-N):
- Total commits: [N]
- All commits reviewed by zen-mcp before committing
- Issues caught early: [X] HIGH, [Y] MEDIUM, [Z] LOW
- All issues fixed before reaching PR

### Deep Review (Before PR): ✅ APPROVED
- Continuation ID: `[continuation-id-from-zen]`
- Architecture: [No issues / Issues found and fixed]
- Test coverage: [X]% (target: 80%) ✅
- Edge cases: [Issues found and status]
- Integration points: [Verification summary]
- Final approval: Granted by zen-mcp

**Review prevented [N] issues from reaching PR stage**

## Testing Completed
- [x] Unit tests pass ([X]/[X] - 100%)
- [x] Integration tests pass ([X]/[X] - 100%)
- [x] Linting passes (mypy --strict + ruff)
- [x] Manual testing in DRY_RUN mode
- [x] Manual testing in paper trading (if applicable)
- [x] Performance test: [specific metric] ✅

## Documentation Updated
- [x] Concept doc created: `/docs/CONCEPTS/[name].md` (if applicable)
- [x] Implementation guide updated
- [x] ADR created and approved (if architectural change)
- [x] Code has comprehensive docstrings
- [x] OpenAPI spec updated (if API changed)
- [x] README.md updated (if new features)

## Educational Value
This PR demonstrates:
- [Key pattern or concept 1]
- [Key pattern or concept 2]
- [Key pattern or concept 3]

## Checklist
- [x] Tests added/updated
- [x] OpenAPI updated (if API changed)
- [x] Migrations included (if DB changes)
- [x] Docs updated (ADR, concepts, guides)
- [x] ADR created (if architectural change)
- [x] Zen-mcp deep review completed ✅

## Reviewer Notes
- [Specific area for reviewers to focus on]
- [Technical detail worth noting]

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

## What Makes This a Good Description

1. **Complete context** - Ticket, ADR, related docs all linked
2. **Comprehensive checklist** - Every requirement tracked
3. **Zen-mcp evidence** - Proves MANDATORY review completed
4. **Testing details** - Specific pass rates and coverage
5. **Documentation updates** - Shows educational value maintained
6. **Educational value** - Highlights learning opportunities
7. **Reviewer guidance** - Tells reviewers what to focus on

## Example Values

Replace placeholders with actual values:
- `[PxTy]` → `P0T5`, `P1T9`, etc.
- `[continuation-id-from-zen]` → actual ID from zen-mcp review
- `[N]` → actual numbers (commits, tests, issues)
- `[X]%` → actual coverage percentage
