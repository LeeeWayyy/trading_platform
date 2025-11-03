#!/bin/bash
# Pre-commit hook - Enforce workflow gates
# CRITICAL: This is a HARD GATE. DO NOT bypass with --no-verify.
#
# This hook ensures that every commit follows the mandatory 4-step workflow:
#   implement → test → review → commit
#
# Prerequisites enforced:
# - Current step must be "review"
# - Zen-MCP review must be APPROVED (clink + gemini → codex)
# - CI must be passing (make ci-local)
#
# Installation:
#   make install-hooks
#
# Author: Claude Code
# Date: 2025-11-02

# Run workflow gate check
python3 scripts/workflow_gate.py check-commit
if [ $? -ne 0 ]; then
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "COMMIT BLOCKED: Workflow prerequisites not met"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "This is a HARD GATE. You must:"
    echo "  1. Request zen review: Follow .claude/workflows/03-zen-review-quick.md"
    echo "  2. Run CI locally: make ci-local"
    echo "  3. Record results:"
    echo "       ./scripts/workflow_gate.py record-review <continuation_id> APPROVED"
    echo "       ./scripts/workflow_gate.py record-ci true"
    echo ""
    echo "WARNING: DO NOT use 'git commit --no-verify' to bypass this gate."
    echo "         Bypassing gates defeats the entire quality system and will"
    echo "         be detected by CI verification (verify_gate_compliance.py)."
    echo ""
    exit 1
fi

# All prerequisites satisfied, allow commit
exit 0
