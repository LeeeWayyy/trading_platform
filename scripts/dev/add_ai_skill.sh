#!/usr/bin/env bash
# add_ai_skill.sh — Scaffold a new shared AI skill with symlinks for all CLIs
# Usage: ./scripts/dev/add_ai_skill.sh <skill-name>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ $# -lt 1 ]; then
  echo "Usage: $0 <skill-name>"
  echo "Example: $0 my-new-skill"
  exit 1
fi

SKILL_NAME="$1"

# Validate skill name (lowercase, hyphens, no spaces)
if ! echo "$SKILL_NAME" | grep -qE '^[a-z][a-z0-9-]*$'; then
  echo "Error: Skill name must be lowercase alphanumeric with hyphens (e.g., 'my-skill')"
  exit 1
fi

SHARED_DIR="$PROJECT_ROOT/docs/AI/skills/$SKILL_NAME"

if [ -d "$SHARED_DIR" ]; then
  echo "Error: Skill '$SKILL_NAME' already exists at $SHARED_DIR"
  exit 1
fi

echo "Creating shared skill: $SKILL_NAME"

# Create shared source
mkdir -p "$SHARED_DIR"
cat > "$SHARED_DIR/SKILL.md" << SKILLTEMPLATE
---
name: $SKILL_NAME
description: TODO — describe what this skill does and when to trigger it.
---

# ${SKILL_NAME//-/ } — TODO Title

TODO: Describe the skill purpose and behavior.
SKILLTEMPLATE

echo "  Created: docs/AI/skills/$SKILL_NAME/SKILL.md"

# Create symlinks for Claude and Gemini CLIs
# Note: .agents/skills/ is NOT created — Gemini CLI reads it and conflicts with .gemini/skills/
# Codex uses .codex/ for its config, not .agents/
for CLI_DIR in .claude .gemini; do
  LINK_DIR="$PROJECT_ROOT/$CLI_DIR/skills/$SKILL_NAME"
  mkdir -p "$LINK_DIR"
  ln -sfn "../../../docs/AI/skills/$SKILL_NAME/SKILL.md" "$LINK_DIR/SKILL.md"
  echo "  Symlink: $CLI_DIR/skills/$SKILL_NAME/SKILL.md"
done

echo ""
echo "Done! Edit docs/AI/skills/$SKILL_NAME/SKILL.md to define the skill."
echo "Claude and Gemini CLIs will pick it up automatically via symlinks."
echo "Note: Codex uses .codex/agents/*.toml (different format). Create manually if needed."
