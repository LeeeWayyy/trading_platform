# ADR-0036: AI Context Architecture — Shared Skills & Nested Context

- Status: Accepted
- Date: 2026-03-02

## Context

The current `AI_GUIDE.md` (symlinked as `CLAUDE.md` and `AGENTS.md`) is 402 lines — nearly 3x the recommended max of ~150 lines. This causes instruction dilution (important rules get lost), wasted context (reference material loads every session even when irrelevant), and no cross-platform skill sharing (skills exist only in `.claude/skills/`).

Key problems:
- **No GEMINI.md** — Gemini CLI doesn't pick up project instructions
- **No shared skills** — architecture, guardrails, glossary content is baked into the monolithic guide
- **No directory-scoped context** — all 402 lines load even for simple tasks in a single subdirectory
- **No custom subagents** — missing context isolation for expensive exploration (Claude Code-specific)
- **Docs-only policy conflict** — the anti-pattern "NEVER create documents outside of `docs` folder" prevents AI context files in subdirectories

### Cross-Platform Compatibility

All three AI CLIs (Claude Code, Gemini CLI, Codex CLI) support:
- SKILL.md format with YAML frontmatter (`name`, `description`) + markdown body
- Nested/hierarchical context files in subdirectories
- Symlinked skill directories

## Decision

### 1. Centralize skills in `docs/AI/skills/` as single source of truth

All SKILL.md files live in `docs/AI/skills/<name>/SKILL.md` and are symlinked to each CLI's discovery path (`.claude/skills/`, `.gemini/skills/`, `.agents/skills/`).

### 2. Create nested context files via `docs/AI/nested/`

Directory-scoped context sources live in `docs/AI/nested/<dir>.md` and are symlinked to `<dir>/CLAUDE.md`, `<dir>/GEMINI.md`, `<dir>/AGENTS.md` for cross-platform discovery.

### 3. Add GEMINI.md root symlink

`GEMINI.md -> docs/AI/AI_GUIDE.md` (matching existing CLAUDE.md and AGENTS.md pattern).

### 4. Create custom subagents (Claude Code-specific)

`.claude/agents/*.md` files use `@path` imports to reference shared skills, staying in sync with the single source of truth.

### 5. Docs-only policy exception

AI context files are exempt from the "NEVER create documents outside of `docs` folder" rule:
- Root context files: `CLAUDE.md`, `GEMINI.md`, `AGENTS.md`
- Nested context symlinks: `<dir>/CLAUDE.md`, `<dir>/GEMINI.md`, `<dir>/AGENTS.md`
- Custom subagents: `.claude/agents/*.md`

Source-of-truth files remain in `docs/` (consistent with docs-centric policy); only symlinks and CLI-specific configuration live outside `docs/`.

### 6. Slim the guide in future phases

Phase 4 will reduce `AI_GUIDE.md` from 402 to ~150-170 lines. Reference material moves to on-demand skills. This ADR covers the foundational architecture; the slimming is a separate, non-breaking change.

### 7. Update commit hook

`scripts/hooks/zen_commit_msg.sh` must recognize skill files, context files, and subagent files as non-docs-only (requiring zen review trailers).

### 8. Windows development

`git config core.symlinks true` must be set for symlinks to work. If not feasible, fall back to generated copies with CI drift check (see C8g in the task plan).

## Consequences

### Positive
- Skills shared across Claude, Gemini, and Codex via single source of truth
- Context usage per session drops (directory-scoped context loads only relevant info)
- Custom subagents isolate expensive exploration from main context window
- All three CLIs discover project instructions via their respective context files
- Additive changes — no existing functionality is removed in this phase

### Negative
- Symlink management adds complexity (mitigated by scaffolding script in C8d)
- Windows users need `core.symlinks` git config (mitigated by generated-copy fallback)

### Risks and Mitigations
- **Symlink breakage**: `find . -type l ! -exec test -e {} \; -print` in CI catches broken symlinks
- **Skill drift between CLIs**: Single source of truth in `docs/AI/skills/` eliminates drift by design
- **Nested context duplication**: Each nested file contains ONLY directory-specific info; no overlap with root guide
