# AI Assistant Development Guide

**Comprehensive guidance for all AI coding assistants working with this repository.**

---

## CRITICAL: Review Override Policy

**NEVER use `ZEN_REVIEW_OVERRIDE` without explicit human approval.**

- Skipping reviews caused 7 fix commits and 10-15 hours of wasted time in past incidents
- `ZEN_REVIEW_OVERRIDE` is ONLY for genuine emergencies (server outages, critical hotfixes)
- If a reviewer CLI is unavailable, ask the user: "Wait for server, or use override (requires your approval)?"
- Include user's name in override commit message: `User approved by: [name]`
- NEVER use `git commit --no-verify` (detected by CI)

---

## CRITICAL: CI-Local Single Instance Rule

**NEVER run multiple `make ci-local` instances simultaneously.**

1. **ONLY ONE** `make ci-local` at a time — the Makefile has a lock mechanism
2. Wait for the current CI to complete before starting a new one
3. If CI needs restart: `pkill -f "make ci-local" && rm -f .ci-local.lock`

---

## Project Overview

**Qlib + Alpaca trading platform** for algorithmic trading. Signals via Qlib-based models, execution via Alpaca API.

> **Default branch:** `master` (NOT `main`). All PRs merge to `master`.

**Key principles:** Idempotency first | Feature parity (research = production) | No duplicate orders | Circuit breakers override signals | Every architectural change requires an ADR | Educational, well-documented code

---

## Quick Start — Analyze → Build → Ship

1. **Start:** `git checkout -b feature/<task-id>-description`
2. **Analyze:** `/analyze <task-file>` (skip for docs-only)
3. **Build:** Implement + write tests (TDD preferred)
4. **Review:** `/review` (iterate until zero issues on first try)
5. **Commit:** Conventional format + zen trailers
6. **Repeat** steps 2-5 per component
7. **CI + PR:** `make ci-local` then `gh pr create`
8. **PR Feedback:** `/pr-fix` then `make ci-local` again

---

## AI Agent Roles

| Agent | Role | Access |
|-------|------|--------|
| **Claude Code** | Main development agent | Full codebase, `/review`, `/pr-fix`, `/analyze` |
| **Gemini** | Code reviewer | Direct CLI: `gemini -p` (non-interactive) |
| **Codex** | Code reviewer | Direct CLI: `codex review` (built-in review) |

---

## Code Review

**Default reviewers:** Gemini + Codex via direct CLI
**Skill:** `/review` — one full iteration (review + fix + re-review until approved)

1. Run `/review` — iterate until all reviewers approve
2. If fixes were made → run `/review` again (fresh iteration)
3. Repeat until zero issues on first try
4. Include final continuation_id in commit message

**Fix ALL issues** — even LOW severity. No deferral.

---

## Skills Reference

| When | Skill | What it does |
|------|-------|-------------|
| Before coding non-trivial feature | `/analyze <task-file>` | Parallel subagent analysis |
| Before committing code changes | `/review` | Review iteration (Gemini + Codex) |
| Single reviewer only | `/review --reviewer gemini` or `codex` | Single reviewer |
| PR has review comments or CI failures | `/pr-fix` or `/pr-fix <number>` | Batch-fix PR feedback |

---

## Common Commands

**Always activate venv first:** `source .venv/bin/activate`

```bash
make up / down      # Start/stop infrastructure
make fmt / lint     # Format / lint code
make test           # Run tests
make ci-local       # Full CI suite locally
make paper-run      # Paper trading for today
make status         # Positions, orders, P&L
make kill-switch    # Emergency stop
```

---

## Critical Patterns

| Pattern | Implementation |
|---------|----------------|
| **Idempotency** | `client_order_id = hash(symbol + side + qty + price + strategy + date)[:24]` |
| **Circuit Breaker** | Check `redis.get("cb:state") != b"TRIPPED"` before every order. Risk-reducing exits permitted when TRIPPED. |
| **Risk Check** | `abs(current_pos + order.qty) <= limits.max_pos_per_symbol` |
| **Feature Parity** | Share code between research/production — never duplicate logic |
| **Reconciliation** | Boot-time + periodic: pull broker state → diff DB → heal discrepancies → alert on failures |

---

## Coding Standards

**Language:** Python 3.11, type hints required, `mypy --strict`

- Pydantic for all config/models
- Structured logging (JSON) with `strategy_id`, `client_order_id`
- Never swallow exceptions — catch, log with context, re-raise
- Always UTC timezone-aware timestamps
- Parameterized queries only (no SQL injection)
- Credentials never hardcoded, logged, or committed
- Async FastAPI + httpx for HTTP

**Full details:** [STANDARDS/CODING_STANDARDS.md](../STANDARDS/CODING_STANDARDS.md)

---

## Development Process

- **Analyze before coding** for non-trivial changes
- **TDD:** Write tests before implementation
- **Review before commit:** `/review` until zero issues
- **CI before PR:** `make ci-local` before creating PR and after fixes
- **Conventional commits:** `feat(scope):`, `fix(scope):`, `docs:`, etc.
- **ADR required** for architectural changes

### Commit Message Format

```
feat(scope): description

zen-mcp-review: approved
continuation-id: <uuid>
```

Docs-only commits (no code, config, AI skills/commands/agents, or context files changed) skip zen trailers. See `scripts/hooks/zen_commit_msg.sh` for the full pathspec list.

---

## Environment Modes

- **DRY_RUN=true** (default): Logs orders, doesn't submit to broker
- **Paper Trading:** `DRY_RUN=false` + paper API (real API, fake money)
- **Live Trading:** Live API credentials (graduated rollout required)

---

## Operational Guardrails

**Pre-Trade:** Per-symbol position limits, total notional limits, blacklist enforcement, daily loss limits, circuit breaker state check (MANDATORY)

**Post-Trade:** Drawdown calculation, realized volatility tracking, exposure monitoring, Prometheus metrics

**Circuit Breakers** trip on: drawdown breach, broker errors, data staleness (>30min). When TRIPPED: block new entries, allow risk-reducing exits, recovery requires normalized conditions + manual approval + quiet period.

**Details:** [RUNBOOKS/ops.md](../RUNBOOKS/ops.md) | `/operational-guardrails` skill

---

## Anti-Patterns

**Process:**
- No coding without `/analyze` for non-trivial changes
- NEVER `git commit --no-verify` (detected by CI)
- No skipping `/review` before commit
- No skipping `make ci-local` before PR or after fixes
- NEVER create documents outside of `docs/` (exception: AI context files per ADR-0036)

**Code Quality:**
- No duplicate feature logic (share research/production code)
- No pattern violations (match established retry/error/logging patterns)
- No call site ignorance (analyze ALL call sites before changing signatures)
- No architectural changes without ADR

---

**Last Updated:** 2026-03-02
**Version:** 4.0
