# Automated Coding Research

**Purpose:** Research and design patterns for automating the per-component coding workflow
**Date:** 2025-11-02
**Related:** P1T13-F3 Phase 3 - Automated Coding Workflow

---

> Note: The AI workflow tooling now uses `.ai_workflow/` for state and config files by default; legacy `.claude/` paths remain present in older docs and some scripts. When integrating automation that updates workflow state (e.g., workflow-state.json), prefer `.ai_workflow/workflow-state.json`. See scripts/ai_workflow/constants.py for canonical locations.

## Research Questions

1. How to automate the 4-step pattern (implement → test → review → commit) per component?
2. How to handle review feedback automatically (auto-fix vs. escalate)?
3. How to integrate task state tracking (`.claude/task-state.json`)?
4. What error handling patterns are needed for robust automation?
5. How to ensure quality gates remain MANDATORY while being automated?

...

(remaining content unchanged)
