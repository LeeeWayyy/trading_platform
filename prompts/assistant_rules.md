# Assistant Rules (Load this into your AI tool)
- Follow /docs/GETTING_STARTED/REPO_MAP.md for where code lives.
- Adhere to OpenAPI contracts; do not change without PR + ADR.
- Do not create new services; extend existing ones.
- Prefer small, isolated commits per ticket.
- Always write/adjust tests first; do not disable tests to pass CI.
- Keep logs structured; never log secrets.

## Automatic Pull Request Creation

When implementing tickets, after successful validation:
1. Create feature branch if not already on one: `git checkout -b feature/tN-short-description`
2. Commit changes with descriptive messages referencing ADRs
3. Push to remote: `git push -u origin <branch-name>`
4. Create PR using `gh pr create` with comprehensive description
5. Include in PR description:
   - Summary of changes
   - Links to ticket, ADR, implementation guide
   - Testing checklist (all items should be checked)
   - Documentation updates made
   - Educational value explanation

**Branch naming:**
- `feature/t4-idempotent-orders` for new features
- `fix/circuit-breaker-bug` for bug fixes
- `docs/update-adr-guide` for documentation
- `refactor/extract-risk-logic` for refactoring

**Never:**
- Push directly to main/master
- Merge PRs automatically (user reviews first)
- Force push without explicit request
- Skip tests before creating PR
