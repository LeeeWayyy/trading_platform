#!/bin/bash
# KB Post-Commit Hook — ingest commit co-change signals into knowledge base.
# Fail-open: never block commits, silently skip on errors.
#
# Called by pre-commit framework with stages: [post-commit]
# Requires: KB database to exist (skip otherwise)

KB_DB="${KB_DB_PATH:-.claude/kb/graph.db}"

# Skip if KB database doesn't exist yet
if [ ! -f "$KB_DB" ]; then
    exit 0
fi

# Use venv python if available, fall back to python3
if [ -x ".venv/bin/python3" ]; then
    PYTHON=".venv/bin/python3"
else
    PYTHON="python3"
fi

# Resolve HEAD to concrete SHA for unique evidence IDs
SHA=$(git rev-parse HEAD 2>/dev/null || echo "")
if [ -z "$SHA" ]; then
    exit 0
fi

# Ingest the commit (fail-open, log errors for debugging)
KB_LOG_DIR="$(dirname "$KB_DB")"
$PYTHON -m tools.kb.ingest_cli --db "$KB_DB" commit --sha "$SHA" 2>>"$KB_LOG_DIR/hook_errors.log" || true
