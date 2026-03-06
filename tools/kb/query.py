"""Knowledge Base query engine — 3 integration modes for development.

Modes:
  implementation-brief: Pre-coding context (impacted files, tests, pitfalls)
  troubleshoot: Error resolution (fix files, past fixes)
  pre-commit-check: Co-change advisory (missing coupled files)
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from tools.kb.db import get_connection, init_schema
from tools.kb.decay import compute_freshness
from tools.kb.models import (
    ImpactedFile,
    ImplementationBrief,
    KnownPitfall,
    PreCommitCheckResult,
    RecommendedTest,
    TroubleshootResult,
)

DEFAULT_TOP_FILES = 8
DEFAULT_TOP_TESTS = 6
DEFAULT_TOP_PITFALLS = 5
MIN_SUPPORT_COUNT = 2


def _escape_like(s: str) -> str:
    """Escape SQL LIKE metacharacters (%, _, \\) for safe use in LIKE patterns."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _get_related_files(
    conn: sqlite3.Connection,
    changed_files: list[str],
    top_n: int = DEFAULT_TOP_FILES,
) -> list[ImpactedFile]:
    """Query file_edges for files related to changed_files, ranked by freshness-weighted score."""
    if not changed_files:
        return []

    placeholders = ",".join("?" for _ in changed_files)
    # Include the edge source (the changed file) so we can look up exact evidence pairs
    rows = conn.execute(
        f"SELECT src_file AS edge_src, dst_file AS path, relation, weight, support_count "
        f"FROM file_edges "
        f"WHERE src_file IN ({placeholders}) AND support_count >= ? "
        f"AND relation IN ('CO_CHANGE', 'REFERENCES') AND weight > 0 "
        f"UNION ALL "
        f"SELECT dst_file AS edge_src, src_file AS path, relation, weight, support_count "
        f"FROM file_edges "
        f"WHERE dst_file IN ({placeholders}) AND support_count >= ? "
        f"AND relation IN ('CO_CHANGE', 'REFERENCES') AND weight > 0 "
        f"ORDER BY weight DESC",
        (*changed_files, MIN_SUPPORT_COUNT, *changed_files, MIN_SUPPORT_COUNT),
    ).fetchall()

    # Batch-fetch latest evidence timestamps for all edge pairs (avoids N+1 queries).
    # Uses a temp table to avoid SQLite's compound-SELECT term limit (500).
    freshness_lookup: dict[tuple[str, str, str], str] = {}
    if rows:
        triples: set[tuple[str, str, str]] = set()
        for row in rows:
            triples.add((row["edge_src"], row["path"], row["relation"]))
        if triples:
            conn.execute(
                "CREATE TEMP TABLE IF NOT EXISTS _q_triples "
                "(q_src TEXT, q_dst TEXT, q_rel TEXT)"
            )
            conn.execute("DELETE FROM _q_triples")
            conn.executemany(
                "INSERT INTO _q_triples (q_src, q_dst, q_rel) VALUES (?, ?, ?)",
                list(triples),
            )
            for ev_row in conn.execute(
                "SELECT q.q_src, q.q_dst, q.q_rel, MAX(ee.observed_at) AS latest "
                "FROM _q_triples q "
                "LEFT JOIN edge_evidence ee "
                "ON ((ee.src_file = q.q_src AND ee.dst_file = q.q_dst) "
                "    OR (ee.src_file = q.q_dst AND ee.dst_file = q.q_src)) "
                "AND ee.relation = q.q_rel "
                "GROUP BY q.q_src, q.q_dst, q.q_rel"
            ).fetchall():
                if ev_row["latest"]:
                    freshness_lookup[
                        (ev_row["q_src"], ev_row["q_dst"], ev_row["q_rel"])
                    ] = ev_row["latest"]
            conn.execute("DROP TABLE IF EXISTS _q_triples")

    # Score each edge using pre-fetched freshness data
    seen: dict[str, ImpactedFile] = {}
    changed_set = set(changed_files)
    for row in rows:
        path = row["path"]
        if path in changed_set:
            continue  # Skip files already in the change set

        edge_src = row["edge_src"]
        relation = row["relation"]
        latest_at = freshness_lookup.get((edge_src, path, relation))
        freshness = compute_freshness(latest_at) if latest_at else 0.5
        score = row["weight"] * freshness

        reason = f"{relation} (support={row['support_count']}, weight={row['weight']:.1f})"
        candidate = ImpactedFile(path=path, score=round(score, 3), reason=reason)

        # Keep the higher-scored entry if path already seen
        if path not in seen or candidate.score > seen[path].score:
            seen[path] = candidate

    # Sort by score descending, cap at top_n
    ranked = sorted(seen.values(), key=lambda f: f.score, reverse=True)
    return ranked[:top_n]


def _get_recommended_tests(
    conn: sqlite3.Connection,
    changed_files: list[str],
    top_n: int = DEFAULT_TOP_TESTS,
) -> list[RecommendedTest]:
    """Query for test files linked to changed files via TESTS relation."""
    if not changed_files:
        return []

    placeholders = ",".join("?" for _ in changed_files)
    rows = conn.execute(
        f"SELECT src_file AS test_path, MAX(weight) AS weight, MAX(support_count) AS support_count "
        f"FROM file_edges "
        f"WHERE dst_file IN ({placeholders}) AND relation = 'TESTS' AND weight > 0 "
        f"GROUP BY src_file "
        f"ORDER BY weight DESC LIMIT ?",
        (*changed_files, top_n),
    ).fetchall()

    return [
        RecommendedTest(path=row["test_path"], confidence=round(min(row["weight"], 1.0), 2))
        for row in rows
    ]


def _get_known_pitfalls(
    conn: sqlite3.Connection,
    changed_files: list[str],
    top_n: int = DEFAULT_TOP_PITFALLS,
) -> list[KnownPitfall]:
    """Query issue_patterns for pitfalls relevant to changed file scopes."""
    if not changed_files:
        return []

    # Extract all ancestor directory scopes from changed files.
    # Root-level files (e.g., pyproject.toml, Makefile) use "./" scope,
    # matching how _ingest_review records root-file findings.
    scopes = set()
    for f in changed_files:
        parts = Path(f).parts
        if len(parts) >= 2:
            # Generate all ancestor directory scopes (not just top 2 levels)
            # e.g., apps/gw/recon/worker.py → apps/, apps/gw/, apps/gw/recon/
            for depth in range(1, len(parts)):
                scopes.add("/".join(parts[:depth]) + "/")
        elif len(parts) == 1:
            scopes.add("./")

    if not scopes:
        return []

    # Use a temp table to avoid SQLite expression-tree depth limits with many scopes.
    # Match pitfalls whose scope_path is an ancestor of (or equal to) the changed-file scope.
    # Uses SUBSTR prefix matching to avoid LIKE metacharacter issues entirely.
    scope_list = sorted(scopes)
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS _q_scopes (scope TEXT)"
    )
    conn.execute("DELETE FROM _q_scopes")
    conn.executemany(
        "INSERT INTO _q_scopes (scope) VALUES (?)",
        [(s,) for s in scope_list],
    )
    rows = conn.execute(
        "SELECT ip.rule_id, ip.scope_path, ip.count, ip.examples_json "
        "FROM issue_patterns ip "
        "WHERE EXISTS ("
        "  SELECT 1 FROM _q_scopes qs "
        "  WHERE SUBSTR(qs.scope, 1, LENGTH(ip.scope_path)) = ip.scope_path"
        ") "
        "ORDER BY ip.count DESC LIMIT ?",
        (top_n,),
    ).fetchall()
    conn.execute("DROP TABLE IF EXISTS _q_scopes")

    pitfalls: list[KnownPitfall] = []
    for row in rows:
        examples = json.loads(row["examples_json"]) if row["examples_json"] else []
        example = examples[0] if examples else None
        pitfalls.append(
            KnownPitfall(
                rule_id=row["rule_id"],
                scope=row["scope_path"],
                count=row["count"],
                example=example,
            )
        )
    return pitfalls


def query_implementation_brief(
    conn: sqlite3.Connection,
    changed_files: list[str],
    top_files: int = DEFAULT_TOP_FILES,
    top_tests: int = DEFAULT_TOP_TESTS,
) -> ImplementationBrief:
    """Build pre-implementation context brief."""
    return ImplementationBrief(
        likely_impacted_files=_get_related_files(conn, changed_files, top_files),
        recommended_tests=_get_recommended_tests(conn, changed_files, top_tests),
        known_pitfalls=_get_known_pitfalls(conn, changed_files),
    )


def query_troubleshoot(
    conn: sqlite3.Connection,
    error_signature: str,
    changed_files: list[str] | None = None,
) -> TroubleshootResult:
    """Query KB for error resolution guidance."""
    # Find past error_fixes matching this signature
    fixes = conn.execute(
        "SELECT fix_id, fixed_files_json, confidence, failing_run_id, passing_run_id "
        "FROM error_fixes WHERE error_signature = ? "
        "ORDER BY confidence DESC LIMIT 5",
        (error_signature,),
    ).fetchall()

    likely_files: list[ImpactedFile] = []
    seen_paths: set[str] = set()
    past_fix_list: list[dict[str, Any]] = []

    for fix in fixes:
        fixed_files = json.loads(fix["fixed_files_json"])
        for f in fixed_files:
            if f not in seen_paths:
                seen_paths.add(f)
                likely_files.append(
                    ImpactedFile(
                        path=f,
                        score=round(fix["confidence"], 2),
                        reason=f"Fixed same error in past (fix_id={fix['fix_id'][:8]})",
                    )
                )
        past_fix_list.append(
            {
                "error": error_signature,
                "files": fixed_files,
            }
        )

    # Also check ERROR_FIX edges
    rows = conn.execute(
        "SELECT dst_file, weight, support_count FROM file_edges "
        "WHERE src_file = ? AND relation = 'ERROR_FIX' AND weight > 0 "
        "ORDER BY weight DESC LIMIT 5",
        (error_signature,),
    ).fetchall()

    for row in rows:
        new_score = round(row["weight"], 2)
        existing = next((f for f in likely_files if f.path == row["dst_file"]), None)
        if existing is None:
            likely_files.append(
                ImpactedFile(
                    path=row["dst_file"],
                    score=new_score,
                    reason=f"ERROR_FIX edge (support={row['support_count']})",
                )
            )
        elif new_score > existing.score:
            existing.score = new_score
            existing.reason = f"ERROR_FIX edge (support={row['support_count']})"

    # Boost scores for files in changed_files (currently being edited)
    if changed_files:
        changed_set = set(changed_files)
        for f in likely_files:
            if f.path in changed_set:
                f.score = round(f.score * 1.5, 2)
                f.reason += " [currently changed]"

    # Sort by score
    likely_files.sort(key=lambda f: f.score, reverse=True)

    return TroubleshootResult(
        likely_fix_files=likely_files[:8],
        past_fixes=past_fix_list[:5],
    )


def query_pre_commit_check(
    conn: sqlite3.Connection,
    staged_files: list[str],
) -> PreCommitCheckResult:
    """Check for missing co-change partners in staged files."""
    if not staged_files:
        return PreCommitCheckResult()

    placeholders = ",".join("?" for _ in staged_files)
    rows = conn.execute(
        f"SELECT dst_file AS path, weight, support_count "
        f"FROM file_edges "
        f"WHERE src_file IN ({placeholders}) AND relation = 'CO_CHANGE' "
        f"AND support_count >= {MIN_SUPPORT_COUNT} AND weight > 0 "
        f"UNION "
        f"SELECT src_file AS path, weight, support_count "
        f"FROM file_edges "
        f"WHERE dst_file IN ({placeholders}) AND relation = 'CO_CHANGE' "
        f"AND support_count >= {MIN_SUPPORT_COUNT} AND weight > 0 "
        f"ORDER BY weight DESC",
        (*staged_files, *staged_files),
    ).fetchall()

    missing: list[ImpactedFile] = []
    staged_set = set(staged_files)
    seen: set[str] = set()

    for row in rows:
        path = row["path"]
        if path in staged_set or path in seen:
            continue
        seen.add(path)
        missing.append(
            ImpactedFile(
                path=path,
                score=round(row["weight"], 2),
                reason=f"Changed together in {row['support_count']} commits",
            )
        )

    return PreCommitCheckResult(missing_co_changes=missing[:8])


# === CLI handlers ===


def cmd_implementation_brief(args: argparse.Namespace) -> None:
    """Handle implementation-brief subcommand."""
    conn = get_connection(args.db)
    init_schema(conn)
    changed = [f.strip() for f in re.split(r"[,\n]", args.changed_files) if f.strip()]
    brief = query_implementation_brief(conn, changed, args.top_files, args.top_tests)
    print(brief.model_dump_json(indent=2))
    conn.close()


def cmd_troubleshoot(args: argparse.Namespace) -> None:
    """Handle troubleshoot subcommand."""
    conn = get_connection(args.db)
    init_schema(conn)
    changed = (
        [f.strip() for f in re.split(r"[,\n]", args.changed_files) if f.strip()]
        if args.changed_files
        else None
    )
    result = query_troubleshoot(conn, args.error_signature, changed)
    print(result.model_dump_json(indent=2))
    conn.close()


def cmd_pre_commit_check(args: argparse.Namespace) -> None:
    """Handle pre-commit-check subcommand."""
    conn = get_connection(args.db)
    init_schema(conn)
    staged = [f.strip() for f in re.split(r"[,\n]", args.staged_files) if f.strip()]
    result = query_pre_commit_check(conn, staged)
    print(result.model_dump_json(indent=2))
    conn.close()


def main(argv: list[str] | None = None) -> None:
    """CLI entrypoint for KB query engine."""
    parser = argparse.ArgumentParser(
        prog="kb-query",
        description="Knowledge Base query engine",
    )
    parser.add_argument("--db", type=str, default=None, help="Database path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # implementation-brief
    p_brief = subparsers.add_parser("implementation-brief", help="Pre-implementation context")
    p_brief.add_argument("--changed-files", required=True, help="Comma-separated file paths")
    p_brief.add_argument("--top-files", type=int, default=DEFAULT_TOP_FILES)
    p_brief.add_argument("--top-tests", type=int, default=DEFAULT_TOP_TESTS)
    p_brief.set_defaults(func=cmd_implementation_brief)

    # troubleshoot
    p_trouble = subparsers.add_parser("troubleshoot", help="Error resolution guidance")
    p_trouble.add_argument("--error-signature", required=True, help="Normalized error signature")
    p_trouble.add_argument("--changed-files", default=None, help="Comma-separated changed files")
    p_trouble.set_defaults(func=cmd_troubleshoot)

    # pre-commit-check
    p_precommit = subparsers.add_parser("pre-commit-check", help="Co-change advisory")
    p_precommit.add_argument("--staged-files", required=True, help="Comma-separated staged files")
    p_precommit.set_defaults(func=cmd_pre_commit_check)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
