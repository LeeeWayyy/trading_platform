# AI Guide — How to Work in This Repo

**Your role:** implement tickets safely, incrementally, and test-first. Do not invent APIs or folders not defined here.

## Source of Truth (in order)
1. /docs/REPO_MAP.md — directory purpose and owners
2. /docs/API/*.openapi.yaml — API contracts (strict)
3. /docs/DB/*.sql — database schemas (strict)
4. /docs/TASKS/*.md — ticket backlog and acceptance criteria
5. /docs/CODING_STANDARDS.md — style, patterns, error handling
6. /docs/TESTING.md — required tests
7. /docs/GLOSSARY.md — project-specific terms

If something is missing, open an ADR in /docs/ADRs per the template before coding.

## Working Agreement
- **No silent scope changes.** If a ticket lacks detail, propose an ADR or add TODOs with rationale.
- **Idempotency first** for order paths. **Never** produce duplicate orders.
- **Feature parity**: research vs production feature definitions must share code.

## Implementation Workflow
1. Read the ticket in /docs/TASKS.
2. Update or add tests per /docs/TESTING.md (red).
3. Implement code in the specified module only.
4. Run `make test` and `make lint` (green).
5. Update docs and ADRs if anything changed.
6. Open PR with checklist from /docs/TESTING.md.

## Environments
- **DRY_RUN=true** by default in dev.
- Paper trading requires valid Alpaca paper keys.

## Guardrails
- Never commit secrets. Use /docs/CONFIG/.env.example.
- Respect rate limits and backoff policies in execution gateway.
- Circuit breakers override signals. If TRIPPED, do not place new entries.
