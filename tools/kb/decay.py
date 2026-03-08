"""Freshness decay, soft expiry, and hard prune for KB edges."""

from __future__ import annotations

import logging
import math
import os
import sqlite3
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

HALF_LIFE_DAYS = 90.0
SOFT_EXPIRY_THRESHOLD = 0.1
SOFT_EXPIRY_SINGLE_DAYS = 90
HARD_PRUNE_DAYS = 180


def compute_freshness(last_seen_at: str, half_life_days: float = HALF_LIFE_DAYS) -> float:
    """Compute freshness score: exp(-days_since / half_life)."""
    try:
        last_seen = datetime.fromisoformat(last_seen_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 0.5
    # Ensure timezone-aware for subtraction
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=UTC)
    now = datetime.now(UTC)
    days_since = (now - last_seen).total_seconds() / 86400.0
    # Future timestamps (clock skew) → clamp to 1.0.
    if days_since < 0:
        return 1.0
    return math.exp(-days_since / half_life_days)


def soft_expire(conn: sqlite3.Connection) -> int:
    """Mark edges as soft-expired (weight → 0) based on staleness criteria.

    Criteria:
    - weight < SOFT_EXPIRY_THRESHOLD
    - OR support_count == 1 AND oldest evidence > SOFT_EXPIRY_SINGLE_DAYS
    """
    expired_count = 0

    # 1. Expire low-weight edges purely in SQL (no Python loop needed)
    cursor = conn.execute(
        "UPDATE file_edges SET weight = 0.0 "
        "WHERE weight > 0.0 AND weight < ?",
        (SOFT_EXPIRY_THRESHOLD,),
    )
    expired_count += cursor.rowcount

    # 2. For single-evidence edges, check staleness via evidence timestamps.
    # Only fetch the subset that needs date checking (support_count=1, still active).
    single_rows = conn.execute(
        "SELECT fe.src_file, fe.dst_file, fe.relation, "
        "  MIN(ee.observed_at) AS oldest_at "
        "FROM file_edges fe "
        "JOIN edge_evidence ee "
        "  ON ee.src_file = fe.src_file AND ee.dst_file = fe.dst_file "
        "  AND ee.relation = fe.relation "
        "WHERE fe.support_count = 1 AND fe.weight > 0.0 "
        "GROUP BY fe.src_file, fe.dst_file, fe.relation"
    ).fetchall()

    for row in single_rows:
        freshness = compute_freshness(row["oldest_at"])
        days = -HALF_LIFE_DAYS * math.log(max(freshness, 1e-10))
        if days > SOFT_EXPIRY_SINGLE_DAYS:
            conn.execute(
                "UPDATE file_edges SET weight = 0.0 "
                "WHERE src_file = ? AND dst_file = ? AND relation = ?",
                (row["src_file"], row["dst_file"], row["relation"]),
            )
            expired_count += 1

    logger.info("Soft-expired %d edges", expired_count)
    return expired_count


def hard_prune(conn: sqlite3.Connection) -> int:
    """Delete soft-expired edges older than HARD_PRUNE_DAYS."""
    # Find edges with weight=0 whose latest evidence is old
    rows = conn.execute(
        "SELECT fe.src_file, fe.dst_file, fe.relation "
        "FROM file_edges fe "
        "WHERE fe.weight = 0.0"
    ).fetchall()

    pruned = 0

    for row in rows:
        latest = conn.execute(
            "SELECT observed_at FROM edge_evidence "
            "WHERE src_file = ? AND dst_file = ? AND relation = ? "
            "ORDER BY observed_at DESC LIMIT 1",
            (row["src_file"], row["dst_file"], row["relation"]),
        ).fetchone()

        if latest:
            freshness = compute_freshness(latest["observed_at"])
            days = -HALF_LIFE_DAYS * math.log(max(freshness, 1e-10))
            if days <= HARD_PRUNE_DAYS:
                continue

        # Delete evidence and edge
        conn.execute(
            "DELETE FROM edge_evidence " "WHERE src_file = ? AND dst_file = ? AND relation = ?",
            (row["src_file"], row["dst_file"], row["relation"]),
        )
        conn.execute(
            "DELETE FROM file_edges " "WHERE src_file = ? AND dst_file = ? AND relation = ?",
            (row["src_file"], row["dst_file"], row["relation"]),
        )
        pruned += 1

    logger.info("Hard-pruned %d edges", pruned)
    return pruned


def _looks_like_path(value: str) -> bool:
    """Check if a value looks like a file path (not an error signature or hash).

    Recognizes paths with slashes, extensions, or known extensionless filenames
    (shared with parsers.ALLOWED_EXTENSIONLESS for consistency).
    """
    from tools.kb.parsers import ALLOWED_EXTENSIONLESS

    if "/" in value:
        return True
    basename = value.split("/")[-1]
    # Has extension (e.g., foo.py)
    if "." in basename:
        return True
    # Known extensionless file (Makefile, Dockerfile, etc.)
    if basename in ALLOWED_EXTENSIONLESS:
        return True
    return False


def prune_deleted_files(conn: sqlite3.Connection) -> int:
    """Remove edges referencing files that no longer exist on disk.

    Skips non-path entries (e.g., error signatures used as src_file in ERROR_FIX edges).
    Resolves relative paths against the repository root to work from any working directory.
    """
    from tools.kb.db import _get_repo_root

    repo_root = str(_get_repo_root())
    all_files: set[str] = set()
    rows = conn.execute(
        "SELECT DISTINCT src_file FROM file_edges " "UNION SELECT DISTINCT dst_file FROM file_edges"
    ).fetchall()

    for row in rows:
        all_files.add(row[0])

    # Only check entries that look like file paths — skip error signatures, hashes, etc.
    # Resolve relative paths against repo root for correct existence checks
    missing = {
        f
        for f in all_files
        if _looks_like_path(f) and not os.path.exists(os.path.join(repo_root, f))
    }
    if not missing:
        return 0

    pruned = 0
    for f in missing:
        conn.execute("DELETE FROM edge_evidence WHERE src_file = ? OR dst_file = ?", (f, f))
        conn.execute("DELETE FROM file_edges WHERE src_file = ? OR dst_file = ?", (f, f))
        pruned += 1

    logger.info("Pruned %d missing files from KB", pruned)
    return pruned
