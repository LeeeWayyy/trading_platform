#!/usr/bin/env python3
"""
DelegationRules - Context monitoring and delegation recommendations.

Tracks conversation context usage and recommends delegation to specialized
agents when thresholds are exceeded.

Thresholds:
- < 70%: OK - Continue normal workflow
- 70-84%: WARNING - Delegation RECOMMENDED
- >= 85%: CRITICAL - Delegation MANDATORY
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime

from .constants import (
    CONTEXT_CRITICAL_PCT,
    CONTEXT_WARN_PCT,
    DEFAULT_MAX_TOKENS,
    PROJECT_ROOT,
)


class DelegationRules:
    """
    Context monitoring and delegation recommendations.

    Tracks conversation context usage and recommends delegation to specialized
    agents when thresholds are exceeded. Supports operation-specific cost
    projections and provides user-friendly guidance.
    """

    # Context thresholds (percentages)
    CONTEXT_WARN_PCT = CONTEXT_WARN_PCT
    CONTEXT_CRITICAL_PCT = CONTEXT_CRITICAL_PCT
    DEFAULT_MAX_TOKENS = DEFAULT_MAX_TOKENS

    # Operation cost estimates (tokens)
    OPERATION_COSTS = {
        "full_ci": 50000,
        "deep_review": 30000,
        "multi_file_search": 20000,
        "test_suite": 15000,
        "code_analysis": 10000,
        "simple_fix": 5000,
    }

    def __init__(
        self,
        load_state: Callable[[], dict],
        save_state: Callable[[dict], None],
        locked_modify_state: Callable[[Callable[[dict], None]], dict] | None = None,
    ) -> None:
        """
        Initialize DelegationRules with state management callables.

        Args:
            load_state: Callable that returns current workflow state dict
            save_state: Callable that persists updated state dict
            locked_modify_state: Callable for atomic read-modify-write operations
        """
        self._load_state = load_state
        self._save_state = save_state
        self._locked_modify_state = locked_modify_state

    def get_context_snapshot(self, state: dict | None = None) -> dict:
        """
        Get current context usage snapshot.

        Returns:
            Dictionary with:
            - current_tokens: int - Current token usage
            - max_tokens: int - Maximum tokens available
            - usage_pct: float - Usage percentage (0-100)
            - last_check: str - ISO timestamp of last check
            - error: str | None - Error message if calculation fails
        """
        if state is None:
            try:
                state = self._load_state()
            except (OSError, PermissionError) as e:
                print(
                    f"Warning: Could not load state - I/O error: {e}",
                    file=sys.stderr,
                )
                state = {}
            except json.JSONDecodeError as e:
                print(
                    f"Warning: Could not load state - invalid JSON: {e}",
                    file=sys.stderr,
                )
                state = {}
            except Exception as e:
                print(
                    f"Warning: Could not load state - unexpected error: {e}",
                    file=sys.stderr,
                )
                state = {}

        context = state.get("context", {})
        current_tokens = context.get("current_tokens", 0)
        max_tokens = context.get("max_tokens", self.DEFAULT_MAX_TOKENS)
        last_check = context.get("last_check_timestamp", "never")

        if max_tokens <= 0:
            return {
                "current_tokens": current_tokens,
                "max_tokens": max_tokens,
                "usage_pct": 0.0,
                "last_check": last_check,
                "error": "Invalid max_tokens",
            }

        usage_pct = (current_tokens / max_tokens) * 100.0

        return {
            "current_tokens": current_tokens,
            "max_tokens": max_tokens,
            "usage_pct": usage_pct,
            "last_check": last_check,
            "error": None,
        }

    def record_context(self, tokens: int) -> dict:
        """
        Record current context usage.

        Args:
            tokens: Current token count (clamped to >= 0)

        Returns:
            Updated context snapshot

        Raises:
            TypeError: If locked_modify_state was not provided (required for atomic updates)
        """
        tokens = max(0, tokens)

        # Gemini HIGH fix: Require locked_modify_state for atomic updates
        # Remove fallback to non-atomic operations to prevent race conditions
        if not self._locked_modify_state:
            raise TypeError(
                "DelegationRules must be initialized with a locked_modify_state callable "
                "for atomic updates. Non-atomic fallback has been removed to prevent race conditions."
            )

        def modifier(state: dict) -> None:
            if "context" not in state:
                state["context"] = {}
            state["context"]["current_tokens"] = tokens
            state["context"]["last_check_timestamp"] = datetime.now(UTC).isoformat()
            if "max_tokens" not in state["context"]:
                state["context"]["max_tokens"] = self.DEFAULT_MAX_TOKENS

            # Refresh context cache
            try:
                git_index_hash = subprocess.check_output(
                    ["git", "write-tree"],
                    cwd=PROJECT_ROOT,
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).strip()
            except subprocess.CalledProcessError:
                git_index_hash = "unknown"

            state["context_cache"] = {
                "tokens": tokens,
                "timestamp": time.time(),
                "git_index_hash": git_index_hash,
            }

        try:
            state = self._locked_modify_state(modifier)
            return self.get_context_snapshot(state)
        except (OSError, PermissionError) as e:
            print(
                f"Warning: Could not update state - I/O error: {e}",
                file=sys.stderr,
            )
            return self.get_context_snapshot({})
        except json.JSONDecodeError as e:
            print(
                f"Warning: Could not update state - invalid JSON: {e}",
                file=sys.stderr,
            )
            return self.get_context_snapshot({})
        except Exception as e:
            print(
                f"Warning: Could not update state - unexpected error: {e}",
                file=sys.stderr,
            )
            return self.get_context_snapshot({})

    def check_threshold(self, state: dict | None = None) -> dict:
        """
        Check context usage against thresholds.

        Returns:
            Dictionary with:
            - level: str - "ok", "warning", or "critical"
            - usage_pct: float - Current usage percentage
            - recommendation: str - Action recommendation
        """
        snapshot = self.get_context_snapshot(state)
        usage_pct = snapshot["usage_pct"]

        if usage_pct >= self.CONTEXT_CRITICAL_PCT:
            return {
                "level": "critical",
                "usage_pct": usage_pct,
                "recommendation": "Delegation MANDATORY before commit",
            }
        elif usage_pct >= self.CONTEXT_WARN_PCT:
            return {
                "level": "warning",
                "usage_pct": usage_pct,
                "recommendation": "Consider delegating non-core tasks",
            }
        else:
            return {
                "level": "ok",
                "usage_pct": usage_pct,
                "recommendation": "Continue normal workflow",
            }

    def project_operation_cost(self, operation: str) -> dict:
        """
        Project context cost of an operation.

        Args:
            operation: Operation type (full_ci, deep_review, etc.)

        Returns:
            Dictionary with projection details
        """
        snapshot = self.get_context_snapshot()
        current = snapshot["current_tokens"]
        max_tokens = snapshot["max_tokens"]

        cost = self.OPERATION_COSTS.get(operation, 5000)
        projected = current + cost
        projected_pct = (projected / max_tokens) * 100.0 if max_tokens > 0 else 0

        would_exceed = projected_pct >= self.CONTEXT_CRITICAL_PCT

        return {
            "operation": operation,
            "estimated_cost": cost,
            "current_tokens": current,
            "projected_tokens": projected,
            "projected_pct": projected_pct,
            "would_exceed_critical": would_exceed,
            "recommendation": "Delegate this operation" if would_exceed else "Proceed",
        }

    def record_delegation(self, description: str) -> dict:
        """
        Record a delegation and reset context.

        Args:
            description: Description of delegated task

        Returns:
            Updated context snapshot

        Raises:
            TypeError: If locked_modify_state was not provided (required for atomic updates)
        """
        # Gemini HIGH fix: Require locked_modify_state for atomic updates
        # Remove fallback to non-atomic operations to prevent race conditions
        if not self._locked_modify_state:
            raise TypeError(
                "DelegationRules must be initialized with a locked_modify_state callable "
                "for atomic updates. Non-atomic fallback has been removed to prevent race conditions."
            )

        def modifier(state: dict) -> None:
            # Record delegation
            if "subagent_delegations" not in state:
                state["subagent_delegations"] = []
            state["subagent_delegations"].append(
                {
                    "description": description,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )

            # Reset context
            if "context" not in state:
                state["context"] = {}
            state["context"]["current_tokens"] = 0
            state["context"]["last_check_timestamp"] = datetime.now(UTC).isoformat()

            # Clear cache
            state["context_cache"] = {
                "tokens": 0,
                "timestamp": time.time(),
                "git_index_hash": "",
            }

        try:
            state = self._locked_modify_state(modifier)
            print(f"Delegation recorded: {description}")
            print("Context reset to 0")
            return self.get_context_snapshot(state)
        except (OSError, PermissionError) as e:
            print(
                f"Warning: Could not record delegation - I/O error: {e}",
                file=sys.stderr,
            )
            return self.get_context_snapshot({})
        except json.JSONDecodeError as e:
            print(
                f"Warning: Could not record delegation - invalid JSON: {e}",
                file=sys.stderr,
            )
            return self.get_context_snapshot({})
        except Exception as e:
            print(
                f"Warning: Could not record delegation - unexpected error: {e}",
                file=sys.stderr,
            )
            return self.get_context_snapshot({})

    def suggest_delegation(self) -> None:
        """Print delegation suggestions based on current context."""
        threshold = self.check_threshold()
        snapshot = self.get_context_snapshot()

        print("=" * 50)
        print("Context Status")
        print("=" * 50)
        print(f"Current: {snapshot['current_tokens']:,} / {snapshot['max_tokens']:,} tokens")
        print(f"Usage: {snapshot['usage_pct']:.1f}%")
        print(f"Level: {threshold['level'].upper()}")
        print()

        if threshold["level"] == "ok":
            print("No delegation needed at this time.")
        else:
            print("Suggested delegations:")
            print("  - Multi-file searches (grep, glob)")
            print("  - Test suite runs")
            print("  - Code analysis tasks")
            print("  - Documentation generation")
            print()
            print("See @docs/AI/Workflows/16-subagent-delegation.md for delegation patterns")
            print()
            print("To create subtasks:")
            print(
                "  ./scripts/admin/workflow_gate.py subtask-create --pr <num> --comments-json <file>"
            )
