"""Pattern promotion — generate hint files from recurring KB patterns."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import yaml

from tools.kb.db import _get_repo_root

logger = logging.getLogger(__name__)

HINTS_DIR = _get_repo_root() / ".claude/kb/hints"
MIN_CONFIRMATIONS = 3


def check_and_promote(conn: sqlite3.Connection) -> list[str]:
    """Find patterns with count >= MIN_CONFIRMATIONS and generate hint files.

    Returns list of generated hint file paths.
    """
    rows = conn.execute(
        "SELECT rule_id, scope_path, count, examples_json, last_seen_sha "
        "FROM issue_patterns WHERE count >= ?",
        (MIN_CONFIRMATIONS,),
    ).fetchall()

    generated: list[str] = []
    for row in rows:
        rule_id: str = row["rule_id"]
        hint_path = generate_hint_file(
            rule_id=rule_id,
            scope_path=row["scope_path"],
            count=row["count"],
            examples_json=row["examples_json"],
        )
        if hint_path:
            generated.append(str(hint_path))

    if generated:
        logger.info("Promoted %d patterns to hint files", len(generated))
    return generated


def generate_hint_file(
    rule_id: str,
    scope_path: str,
    count: int,
    examples_json: str | None = None,
) -> Path | None:
    """Write a hint markdown file for a promoted pattern.

    Returns the path to the generated file, or None if skipped.
    """
    HINTS_DIR.mkdir(parents=True, exist_ok=True)
    # Include scope in filename to avoid overwriting across different scopes
    scope_slug = scope_path.strip("/").replace("/", "_") if scope_path.strip("/") else "root"
    hint_path = HINTS_DIR / f"{rule_id.lower()}_{scope_slug}.md"

    examples = json.loads(examples_json) if examples_json else []
    hotspots = ", ".join(examples[:3]) if examples else "see KB for details"

    # Load rule description from taxonomy if available
    description = _get_rule_description(rule_id)

    content = f"""---
name: {rule_id.lower().replace('_', '-')}
description: {description}
---
# {rule_id.replace('_', ' ').title()}

**Known issue:** Found {count} times in `{scope_path}`.
**Hotspots:** {hotspots}
**Action:** Review and fix instances matching this pattern.
"""

    hint_path.write_text(content)
    logger.info("Generated hint file: %s", hint_path)
    return hint_path


def _get_rule_description(rule_id: str) -> str:
    """Load rule description from taxonomy.yaml."""
    try:
        taxonomy_path = Path(__file__).parent / "taxonomy.yaml"
        if taxonomy_path.exists():
            with open(taxonomy_path) as f:
                data = yaml.safe_load(f)
            for rule in data.get("rules", []):
                if rule["id"] == rule_id:
                    return str(rule.get("description", rule_id))
    except Exception:
        logger.debug("Failed to load taxonomy for rule %s", rule_id, exc_info=True)
    return rule_id
