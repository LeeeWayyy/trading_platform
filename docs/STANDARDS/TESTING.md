# Testing Strategy

## Pyramid
- Unit: functions (features, allocators, idempotent ID generation)
- Contract: API endpoints vs OpenAPI (schemathesis)
- Integration: end-to-end paper run in DRY_RUN then real paper API

## Must-Haves
- Reproducible `paper_run` for yesterday (backtest replay parity)
- Circuit breaker tests: trip on DD, untrip via recovery policy
- Stale-order cleanup test (>15m → cancel)

## PR Checklist

**Zen-MCP Review (MANDATORY):**
- [ ] **Progressive commit reviews completed**
  - [ ] All commits reviewed by zen-mcp before committing
  - [ ] All HIGH/CRITICAL issues fixed
  - [ ] MEDIUM issues fixed or deferred with justification
- [ ] **Deep review before PR completed**
  - [ ] Comprehensive zen-mcp review of all branch changes
  - [ ] Architecture reviewed and approved
  - [ ] Test coverage verified
  - [ ] Edge cases identified and handled
- [ ] **Zen review documented in PR description**
  - [ ] Continuation ID included
  - [ ] Issues caught and fixed summary
  - [ ] Review approval confirmation

**Code Quality:**
- [ ] Tests added/updated (unit, integration, edge cases)
- [ ] All tests passing (`make test`)
- [ ] Linting passing (`make lint`)
- [ ] Code coverage ≥80% for new code

**Documentation:**
- [ ] OpenAPI updated if API changed
- [ ] Migrations included if DB changed
- [ ] Docs updated (REPO_MAP / ADR / TASKS / CONCEPTS)
- [ ] ADR created if architectural change
- [ ] Implementation guide updated/created

**GitHub Reviews:**
- [ ] GitHub App reviews requested (@codex @gemini-code-assist)
