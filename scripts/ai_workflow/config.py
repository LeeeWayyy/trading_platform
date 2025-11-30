"""
Configuration Management for AI Workflow.

Addresses review feedback:
- L2: Single source of truth for reviewer enablement
- Uses only 'enabled' array, removes settings.*.enabled boolean
- Gemini LOW: Atomic config save using temp-file-rename pattern
"""

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import List, Optional

from .constants import WORKFLOW_DIR, CONFIG_FILE  # Import from constants (no duplicate)

DEFAULT_CONFIG = {
    "version": "1.0",
    "reviewers": {
        # Codex MEDIUM fix: Default to dual-review (gemini + codex) with min_required=2
        # This aligns with the project's documented review requirements
        "enabled": ["gemini", "codex"],
        "available": ["claude", "gemini", "codex"],
        "min_required": 2,
        "username_mapping": {}
    },
    "ci": {
        "wait_timeout_seconds": 600,
        "poll_interval_seconds": 30,
        "retry_on_flaky": True
    },
    "git": {
        "push_retry_count": 3,
        "default_base_branch": "master"
    },
    "delegation": {
        "comment_threshold": 10,
        "file_threshold": 20,
    }
}


class WorkflowConfig:
    """
    Configuration manager for workflow settings.

    Addresses L2: Single source of truth for reviewer enablement.
    Uses config["reviewers"]["enabled"] array only.
    """

    def __init__(self):
        self.config = self._load_or_create()

    def _load_or_create(self) -> dict:
        """Load config or create default.

        Addresses:
        - Gemini MEDIUM: Validates loaded JSON is a dict
        - Codex MEDIUM: Handles JSON corruption gracefully
        """
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as f:
                    config = json.load(f)
            except json.JSONDecodeError as e:
                # Codex MEDIUM fix: Handle corrupted JSON gracefully
                import sys
                print(f"Warning: Corrupted config file, using defaults: {e}", file=sys.stderr)
                self._save(DEFAULT_CONFIG)
                return copy.deepcopy(DEFAULT_CONFIG)
            except OSError as e:
                import sys
                print(f"Warning: Could not read config file: {e}", file=sys.stderr)
                return copy.deepcopy(DEFAULT_CONFIG)

            # Validate config is a dict (Gemini MEDIUM fix)
            if not isinstance(config, dict):
                import sys
                print(
                    f"Warning: Invalid config format (expected dict, got {type(config).__name__}). "
                    f"Using defaults.", file=sys.stderr
                )
                self._save(DEFAULT_CONFIG)
                return copy.deepcopy(DEFAULT_CONFIG)

            return self._merge_with_defaults(config)
        else:
            self._save(DEFAULT_CONFIG)
            return copy.deepcopy(DEFAULT_CONFIG)

    def _merge_with_defaults(self, config: dict) -> dict:
        """Merge user config with defaults for missing fields."""
        merged = copy.deepcopy(DEFAULT_CONFIG)
        self._deep_update(merged, config)
        return merged

    def _deep_update(self, base: dict, update: dict) -> None:
        """Recursively update nested dicts."""
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_update(base[key], value)
            else:
                base[key] = value

    def _save(self, config: dict) -> None:
        """Save config to file atomically.

        Gemini LOW fix: Uses temp-file-rename pattern for atomic writes.
        """
        WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)

        # Atomic write: write to temp file then rename
        temp_fd, temp_path = tempfile.mkstemp(
            dir=WORKFLOW_DIR, prefix=".config-", suffix=".tmp"
        )
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
            Path(temp_path).replace(CONFIG_FILE)
        except OSError:
            Path(temp_path).unlink(missing_ok=True)
            raise

    def get_enabled_reviewers(self) -> List[str]:
        """Get list of enabled reviewer names."""
        return self.config["reviewers"]["enabled"]

    def get_min_required_approvals(self) -> int:
        """Get minimum required reviewer approvals."""
        return self.config["reviewers"]["min_required"]

    def get_reviewer_username(self, reviewer_name: str) -> Optional[str]:
        """Map reviewer CLI name to GitHub username if configured."""
        return self.config["reviewers"]["username_mapping"].get(reviewer_name)

    def is_reviewer_enabled(self, reviewer_name: str) -> bool:
        """Check if a specific reviewer is enabled."""
        return reviewer_name in self.config["reviewers"]["enabled"]
