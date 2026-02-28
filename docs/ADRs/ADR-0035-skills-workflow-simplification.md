# ADR-0035: Replace workflow_gate.py with Lightweight Skills

- Status: Accepted
- Date: 2026-02-26

## Context

The development workflow evolved into a heavyweight state machine:

- **workflow_gate.py**: ~1,200 lines, 32 commands, JSON state tracking
- **46 workflow documentation files**: 432KB of process documentation
- **10+ tracking commands per component**: None write code or run reviews

This created friction without proportional quality gains. The state machine enforced a rigid 6-step ceremony (plan → plan-review → implement → test → review → commit) even for trivial changes, consuming developer time and AI context windows on bookkeeping rather than coding.

Key observations:
- Reviews (the actual quality gate) are orchestrated via clink, not workflow_gate
- CI enforcement works through GitHub Actions, not local hooks
- The state machine tracked transitions but didn't prevent bad code
- Context window bloat from workflow commands reduced AI effectiveness

## Decision

Replace workflow_gate.py and 46 workflow docs with **3 lightweight skills**:

1. **`/review`** — Orchestrates shared-context code review iterations via Gemini + Codex
2. **`/pr-fix`** — Collects PR comments from GitHub API and batch-fixes them
3. **`/analyze`** — Runs parallel subagent analysis before implementation

Design principles:
- **AI_GUIDE.md is the control plane** — tells the AI when to invoke each skill
- **Skills are helpers, not enforcers** — no state machines, no locks, no hard blocks
- **Git is the state** — branch + commits + staged changes = current state
- **GitHub Branch Protection is the only hard gate** — CI + approval required to merge

### What gets removed
- `.ai_workflow/workflow-state.json` and all state tracking
- `workflow_gate.py` and all 32 commands
- Pre-commit hook dependency on workflow_gate
- 6-step state machine transitions
- All local enforcement mechanisms (except `.ci-local.lock` resource guard)

### What stays
- `make ci-local` (CI runner)
- `gh` CLI (PR operations)
- Commit message conventions (zen markers, continuation-id trailers)
- Code review via clink (now orchestrated by `/review` skill)

## Consequences

### Positive
- Reduces 12+ tracking commands per component to 0
- Skills do actual work (run reviews, collect feedback, analyze impact)
- Fresh context per skill invocation prevents context window bloat
- Lower barrier for quick fixes and documentation changes

### Negative
- No local enforcement of review discipline (relies on AI_GUIDE.md guidance + branch protection)
- Developers could skip `/review` locally (caught at PR merge by branch protection)

### Risks and Mitigations
- **Review discipline degrades**: AI_GUIDE.md triggers + PR template checklist + branch protection
- **Safety gap during transition**: Phase 0 enables branch protection before removing local gates
- **Residual references break flow**: Link consistency check (`rg "workflow_gate"`) in cleanup phase
