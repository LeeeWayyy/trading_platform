# AI Assistant Documentation

**Centralized location for all AI-assisted development content.**

This directory consolidates all AI workflows, prompts, research, examples, and archives in one place.

## Primary AI Guide

**[AI_GUIDE.md](./AI_GUIDE.md)** - **START HERE** - Comprehensive guidance for all AI coding assistants

This is the single source of truth for:
- Claude Code (main development agent)
- Codex (code review agent via direct CLI)
- Gemini (code review agent via direct CLI)
- All other AI assistants

**Quick access:** Symlinked from project root as `CLAUDE.md`, `GEMINI.md`, and `AGENTS.md`

## Cross-Platform Architecture

### Context Files
All three CLI tools share the same guide via symlinks:
- `CLAUDE.md` → `docs/AI/AI_GUIDE.md`
- `GEMINI.md` → `docs/AI/AI_GUIDE.md`
- `AGENTS.md` → `docs/AI/AI_GUIDE.md`

### Shared Skills (`skills/`)
Platform-agnostic skill definitions in `docs/AI/skills/`, symlinked to each CLI:
- `.claude/skills/<name>/SKILL.md` → `docs/AI/skills/<name>/SKILL.md`
- `.gemini/skills/<name>/SKILL.md` → `docs/AI/skills/<name>/SKILL.md`

Note: `.agents/skills/` is NOT used — Gemini CLI reads it and conflicts with `.gemini/skills/`.

**Available skills:** analyze, pr-fix, review, architecture-overview, operational-guardrails, trading-glossary

### Nested Context (`nested/`)
Directory-scoped context files for subdirectory awareness:
- `apps/CLAUDE.md`, `libs/CLAUDE.md`, `tests/CLAUDE.md`, `research/CLAUDE.md`
- Same for `GEMINI.md` and `AGENTS.md` (all symlinked to `docs/AI/nested/`)

### Custom Subagents
- `.claude/agents/` — Claude-specific agent definitions
- `.gemini/agents/` — Gemini-specific agent definitions

## Contents

### Active Documentation
- [Workflows/](./Workflows/) - Step-by-step AI development workflows (MUST follow)
- [Prompts/](./Prompts/) - Reusable AI prompts and templates
- [Examples/](./Examples/) - Example interactions, PR guidelines, and use cases
- [Research/](./Research/) - Research findings on AI capabilities and patterns
- [Implementation/](./Implementation/) - Implementation guides and plans
- [Audits/](./Audits/) - Code audit findings and reviews
- [Analysis/](./Analysis/) - Code analysis reports and checklists

### Archives
- `INDEX-archive-2025-11-21.md` - Previous documentation index (pre-consolidation)

## Quick Start

1. **New to the project?** Read [AI_GUIDE.md](./AI_GUIDE.md) (also accessible via `CLAUDE.md`, `GEMINI.md`, or `AGENTS.md`)
2. **Ready to code?** Follow [Workflows/README.md](./Workflows/README.md)
3. **Need examples?** Check [Examples/](./Examples/)

## Adding a New Skill

Use the scaffolding script:
```bash
./scripts/dev/add_ai_skill.sh <skill-name>
```
This creates the skill in `docs/AI/skills/` and symlinks it for all three CLIs.

## Philosophy

All AI-related content lives here to:
- **Avoid fragmentation** - Single source of truth for AI guidance
- **Easy discovery** - All workflows, prompts, and examples in one place
- **Clear ownership** - AI team maintains this directory
- **Version control** - Track evolution of AI practices

## Navigation

Return to [main documentation index](../INDEX.md)
