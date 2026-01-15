"""
Context-Isolated Subtask System.

KEY DESIGN: Script outputs JSON instructions, agent executes them.

Addresses review feedback:
- C2: Adds response parsing and validation
- C4: Clarifies that script outputs instructions, not execution
"""

import json
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from .config import WorkflowConfig


class SubtaskType(Enum):
    REVIEW_FILES = "review_files"
    FIX_COMMENTS = "fix_comments"
    RUN_TESTS = "run_tests"


class SubtaskStatus(Enum):
    QUEUED = "queued"
    DELEGATED = "delegated"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AgentInstruction:
    """
    Instruction for main agent to execute.

    Script outputs these; agent reads and acts on them.
    """

    id: str
    action: str
    tool: str
    params: dict

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "action": self.action,
            "tool": self.tool,
            "params": self.params,
        }


def validate_file_path(file_path: str, project_root: Path = None) -> str:
    """Validate file path is within project directory.

    Addresses Claude S2 security review: Prevents path traversal attacks.

    Args:
        file_path: Path to validate
        project_root: Project root directory (defaults to cwd)

    Returns:
        Validated absolute path string

    Raises:
        ValueError: If path is outside project directory
    """
    if project_root is None:
        project_root = Path.cwd()

    # Resolve to absolute path
    resolved = Path(file_path).resolve()
    project_resolved = project_root.resolve()

    # Check path is within project
    try:
        resolved.relative_to(project_resolved)
    except ValueError as err:
        raise ValueError(
            f"Security: File path outside project: {file_path}. "
            f"Paths must be within {project_resolved}"
        ) from err

    return str(resolved)


class SubagentPrompts:
    """
    Build prompts for sub-agents.

    Sub-agents fetch their own data and return only summaries.
    """

    @staticmethod
    def fix_comments_prompt(file_path: str, comment_ids: list[int], pr_number: int) -> str:
        """Prompt for sub-agent to fix comments.

        Addresses Claude S2: Validates file_path before use.
        """
        # Validate path before including in prompt (S2 security fix)
        validated_path = validate_file_path(file_path)

        return f"""You are a sub-agent fixing PR review comments.

## Your Task
Fix the review comments in file: `{validated_path}`

## Comment IDs to Address
{comment_ids}

## Instructions
1. Fetch the comment details from GitHub:
   gh api repos/<owner>/<repo>/pulls/{pr_number}/comments
   Filter for IDs: {comment_ids}

2. Read the file `{validated_path}` to understand the context

3. For each comment, make the appropriate code change

4. Return ONLY this JSON summary:

```json
{{
  "task_id": "{Path(validated_path).stem}",
  "file": "{validated_path}",
  "total_comments": {len(comment_ids)},
  "fixed": <number fixed>,
  "failed": <number that couldn't be fixed>,
  "summary": "<one sentence describing what you did>"
}}
```

IMPORTANT: Do NOT include comment text or code in your response.
"""

    @staticmethod
    def review_files_prompt(file_paths: list[str]) -> str:
        """Prompt for sub-agent to review files.

        Addresses Claude S2: Validates all file_paths before use.
        """
        # Validate all paths before including in prompt (S2 security fix)
        validated_paths = [validate_file_path(fp) for fp in file_paths]

        return f"""You are a sub-agent reviewing code files.

## Your Task
Review these files:
{chr(10).join(f"- {f}" for f in validated_paths)}

## Instructions
1. Read each file
2. Look for security issues, logic errors, performance problems

3. Return ONLY this JSON summary:

```json
{{
  "files_reviewed": {len(validated_paths)},
  "status": "APPROVED" or "NEEDS_CHANGES",
  "issues": {{"high": 0, "medium": 0, "low": 0}},
  "summary": "<one sentence overview>"
}}
```

IMPORTANT: Do NOT include file contents in your response.
"""


class SubtaskOrchestrator:
    """
    Creates delegation queue and tracks results.

    NEVER executes MCP tools - outputs instructions for agent.
    """

    def __init__(self, state: dict, config: WorkflowConfig = None):
        self.state = state
        self.config = config or WorkflowConfig()
        self._ensure_subtask_state()

    def _ensure_subtask_state(self) -> None:
        """Ensure subtask state structure exists."""
        if "subtasks" not in self.state:
            self.state["subtasks"] = {
                "queue": [],
                "completed": [],
                "failed": [],
            }

    def _get_preferred_cli(self) -> str:
        """Get preferred CLI for subtask delegation.

        Addresses Gemini review: HARDCODED_CLI_NAME
        Uses first enabled reviewer from config instead of hardcoding "claude".
        """
        enabled = self.config.get_enabled_reviewers()
        if not enabled:
            raise ValueError("No reviewers enabled in config - cannot delegate subtasks")
        return enabled[0]

    def create_agent_instructions(
        self, pr_number: int, comments_by_file: dict[str, list[int]], cli_name: str = None
    ) -> list[AgentInstruction]:
        """
        Create instructions for agent to execute.

        Args:
            pr_number: PR number to fetch comments from
            comments_by_file: Dict mapping file paths to comment IDs
            cli_name: Optional CLI to use (defaults to first enabled reviewer)

        Returns list of AgentInstruction that agent will act on.
        """
        # Use provided CLI or get from config (tool-agnostic design)
        target_cli = cli_name or self._get_preferred_cli()

        instructions = []

        for file_path, comment_ids in comments_by_file.items():
            # Validate and resolve to absolute path (Gemini HIGH + MEDIUM fix)
            validated_path = validate_file_path(file_path)

            task_id = f"fix-{Path(validated_path).stem}-{uuid.uuid4().hex[:6]}"

            prompt = SubagentPrompts.fix_comments_prompt(validated_path, comment_ids, pr_number)

            instruction = AgentInstruction(
                id=task_id,
                action="delegate_to_subagent",
                tool="mcp__zen__clink",
                params={
                    "cli_name": target_cli,
                    "prompt": prompt,
                    "absolute_file_paths": [validated_path],  # Now absolute path
                    "role": "codereviewer",  # Gemini LOW fix: consistent with ReviewerOrchestrator
                },
            )
            instructions.append(instruction)

            # Track in state
            self.state["subtasks"]["queue"].append(
                {
                    "id": task_id,
                    "type": SubtaskType.FIX_COMMENTS.value,
                    "file_path": validated_path,
                    "comment_count": len(comment_ids),
                    "status": SubtaskStatus.QUEUED.value,
                }
            )

        return instructions

    def output_instructions_json(self, instructions: list[AgentInstruction]) -> str:
        """
        Output instructions as JSON for agent to read and execute.

        This is what the script prints - agent parses and acts on it.
        """
        return json.dumps(
            {
                "action": "delegate_subtasks",
                "instruction": "For each task, call mcp__zen__clink with the provided params",
                "tasks": [i.to_dict() for i in instructions],
            },
            indent=2,
        )

    def mark_delegated(self, task_id: str) -> bool:
        """
        Mark task as delegated (started).

        Called when agent confirms it started the subtask.
        """
        for task in self.state["subtasks"]["queue"]:
            if task["id"] == task_id:
                task["status"] = SubtaskStatus.DELEGATED.value
                task["delegated_at"] = datetime.now(UTC).isoformat()
                return True
        return False

    def parse_subagent_response(self, response: str) -> dict:
        """
        Parse sub-agent response and extract summary.

        Addresses C2: Validates response format before accepting.
        """
        # Try to extract JSON from response
        json_match = re.search(r"```json\s*(.*?)\s*```", response, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group(1))
            except json.JSONDecodeError:
                return {
                    "success": False,
                    "error": "Invalid JSON in response",
                    "summary": "Sub-agent returned malformed JSON",
                }
        else:
            # Try parsing entire response as JSON
            try:
                parsed = json.loads(response)
            except json.JSONDecodeError:
                return {
                    "success": False,
                    "error": "No JSON found in response",
                    "summary": response[:200] if response else "Empty response",
                }

        # Validate required fields
        if "summary" not in parsed:
            return {
                "success": False,
                "error": "Missing 'summary' field",
                "summary": str(parsed)[:200],
            }

        # Check for error indicators
        if parsed.get("error") or parsed.get("status") == "error":
            return {
                "success": False,
                "error": parsed.get("error", "Unknown error"),
                "summary": parsed.get("summary", ""),
            }

        return {
            "success": True,
            "summary": parsed["summary"],
            "data": parsed,
        }

    def record_completion(self, task_id: str, response: str) -> bool:
        """
        Record task completion.

        Accepts --summary-file to avoid shell escaping issues (G3 fix).
        """
        parsed = self.parse_subagent_response(response)

        # Update queue status - use enum values for consistency
        for task in self.state["subtasks"]["queue"]:
            if task["id"] == task_id:
                # Validate task was delegated (M5 fix)
                if task.get("status") != SubtaskStatus.DELEGATED.value:
                    print(
                        f"Warning: Task {task_id} completed without being delegated first",
                        file=sys.stderr,
                    )
                    print(
                        "   Use: ./scripts/admin/workflow_gate.py subtask-start <task-id> before completing",
                        file=sys.stderr,
                    )
                task["status"] = (
                    SubtaskStatus.COMPLETED.value
                    if parsed["success"]
                    else SubtaskStatus.FAILED.value
                )
                break

        if parsed["success"]:
            self.state["subtasks"]["completed"].append(
                {
                    "id": task_id,
                    "summary": parsed["summary"],
                    "data": parsed.get("data", {}),
                    "completed_at": datetime.now(UTC).isoformat(),
                }
            )
        else:
            self.state["subtasks"]["failed"].append(
                {
                    "id": task_id,
                    "error": parsed.get("error", "Unknown"),
                    "summary": parsed.get("summary", ""),
                    "failed_at": datetime.now(UTC).isoformat(),
                }
            )

        return parsed["success"]

    def get_status_summary(self) -> dict:
        """Get summary of subtask status."""
        queue = self.state["subtasks"]["queue"]
        return {
            "total": len(queue),
            "queued": len([t for t in queue if t["status"] == SubtaskStatus.QUEUED.value]),
            "delegated": len([t for t in queue if t["status"] == SubtaskStatus.DELEGATED.value]),
            "completed": len(self.state["subtasks"]["completed"]),
            "failed": len(self.state["subtasks"]["failed"]),
        }
