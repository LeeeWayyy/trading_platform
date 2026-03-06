"""Multi-source knowledge base ingest pipeline.

Core ingest logic for 7 signal types. CLI handlers live in ingest_cli.py.
Each ingest function creates edge_evidence records and reaggregates file_edges.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import os
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools.kb.db import (
    DEFAULT_DB_PATH,
    DEFERRED_QUEUE_PATH,
    _get_repo_root,
    get_connection,
    init_schema,
    is_lock_error,
)
from tools.kb.models import EvidenceSource, Relation, SessionOutcome
from tools.kb.parsers import (
    parse_git_commit_date,
    parse_git_log_range,
    parse_git_show,
    parse_junit_xml,
    parse_review_artifact,
)

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]  # Windows: file locking unavailable

logger = logging.getLogger(__name__)

# Retry settings for BEGIN IMMEDIATE
_BEGIN_MAX_RETRIES = 3
_BEGIN_BACKOFFS = [0.5, 1.0, 2.0]


def _begin_immediate_with_retry(conn: sqlite3.Connection) -> None:
    """Execute BEGIN IMMEDIATE with retries before giving up.

    Retries with exponential backoff on SQLITE_BUSY before falling back
    to the deferred queue mechanism.
    """
    import time

    for attempt in range(_BEGIN_MAX_RETRIES):
        try:
            conn.execute("BEGIN IMMEDIATE")
            return
        except sqlite3.OperationalError as exc:
            if not is_lock_error(exc):
                raise
            if attempt < _BEGIN_MAX_RETRIES - 1:
                time.sleep(_BEGIN_BACKOFFS[attempt])
            else:
                raise


# Artifact size guard — prevent OOM on unexpectedly large files
MAX_ARTIFACT_BYTES = 50 * 1024 * 1024  # 50 MB

# Noise filter: skip commits with fewer than MIN or more than MAX files.
# MAX is overridable via env for repos with larger typical changesets.
MIN_FILES_FOR_COMMIT = 2
MAX_FILES_FOR_COMMIT = int(os.environ.get("KB_MAX_COMMIT_FILES", "15"))


def _evidence_id(src: str, dst: str, relation: str, source: str, source_id: str) -> str:
    """Compute deterministic evidence ID for idempotent upserts."""
    key = f"{src}:{dst}:{relation}:{source}:{source_id}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _now_iso() -> str:
    """Return current UTC time in ISO8601 Z-suffix format with microsecond precision.

    Uses Z-suffix (matching parse_git_commit_date) so text-based ORDER BY
    on observed_at is lexicographically correct. Microseconds are included
    to disambiguate rapid successive ingests within the same second.
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _resolve_sha(sha: str) -> str:
    """Resolve symbolic refs (HEAD, branch names) to concrete SHA hashes."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", sha],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return result.stdout.strip() or sha
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return sha


def _is_merge_commit(sha: str) -> bool:
    """Check if a commit is a merge commit (has more than one parent)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"{sha}^2"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return bool(result.stdout.strip())
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _insert_evidence(
    conn: sqlite3.Connection,
    src: str,
    dst: str,
    relation: str,
    source: str,
    source_id: str,
    weight: float,
    observed_at: str,
) -> None:
    """Insert a single edge_evidence record (idempotent)."""
    eid = _evidence_id(src, dst, relation, source, source_id)
    conn.execute(
        "INSERT INTO edge_evidence "
        "(evidence_id, src_file, dst_file, relation, source, source_id, weight, observed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (evidence_id) DO UPDATE SET "
        "weight = excluded.weight",
        (eid, src, dst, relation, source, source_id, weight, observed_at),
    )


def _reaggregate(conn: sqlite3.Connection, src: str, dst: str, relation: str) -> None:
    """Reaggregate file_edges from edge_evidence for a specific edge."""
    row = conn.execute(
        "SELECT SUM(weight) AS total_weight, COUNT(*) AS cnt "
        "FROM edge_evidence WHERE src_file = ? AND dst_file = ? AND relation = ?",
        (src, dst, relation),
    ).fetchone()

    if row is None or row["cnt"] == 0:
        return

    latest = conn.execute(
        "SELECT source_id FROM edge_evidence "
        "WHERE src_file = ? AND dst_file = ? AND relation = ? "
        "ORDER BY observed_at DESC LIMIT 1",
        (src, dst, relation),
    ).fetchone()
    last_seen = latest["source_id"] if latest else None

    conn.execute(
        "INSERT INTO file_edges (src_file, dst_file, relation, weight, support_count, last_seen_sha) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (src_file, dst_file, relation) "
        "DO UPDATE SET weight = excluded.weight, "
        "support_count = excluded.support_count, "
        "last_seen_sha = excluded.last_seen_sha",
        (src, dst, relation, row["total_weight"], row["cnt"], last_seen),
    )


def _snapshot_artifact(original_path: str, db_path: str | None = None) -> str:
    """Copy an artifact to a unique snapshot path for deferred replay.

    Prevents data loss when the original file (e.g., .pytest_results.xml)
    is overwritten by subsequent runs before the deferred queue is replayed.
    Returns the snapshot path (or resolved original path if file doesn't exist).

    Args:
        db_path: Optional database path. When provided, snapshots are stored
            alongside that database rather than under DEFAULT_DB_PATH.
    """
    src = Path(original_path).resolve()
    if not src.exists():
        return str(src)

    try:
        size = src.stat().st_size
    except OSError:
        return str(src)
    if size > MAX_ARTIFACT_BYTES:
        logger.warning(
            "Artifact %s is %.1f MB, exceeding %d MB limit — skipping snapshot",
            src,
            size / 1024 / 1024,
            MAX_ARTIFACT_BYTES // 1024 // 1024,
        )
        return str(src)

    # Hash the file without loading it entirely into memory (stream in chunks)
    hasher = hashlib.sha256()
    with open(src, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            hasher.update(chunk)
    content_hash = hasher.hexdigest()[:12]

    # Resolve to absolute so deferred payloads work from any working directory.
    # Anchor relative db_path to repo root, matching get_connection/write_deferred.
    if db_path:
        p = Path(db_path)
        base_dir = (p if p.is_absolute() else _get_repo_root() / p).parent.resolve()
    else:
        base_dir = DEFAULT_DB_PATH.parent.resolve()
    snapshot_dir = base_dir / "deferred_artifacts"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    ext = src.suffix or ".xml"
    snapshot = snapshot_dir / f"{content_hash}{ext}"
    if not snapshot.exists():
        import shutil

        shutil.copy2(src, snapshot)
    return str(snapshot)


# === Subcommand implementations ===


def _ingest_review(
    conn: sqlite3.Connection,
    artifact_path: str,
    reviewer: str,
    run_id: str | None = None,
) -> int:
    """Ingest review findings into the KB."""
    if run_id is None:
        # Hash reviewer + content + mtime for unique-per-invocation run_id.
        # Including mtime ensures separate review runs with identical findings
        # (e.g., persistent issues across commits) produce distinct run_ids,
        # so issue_patterns.count correctly reflects repeated occurrences.
        try:
            p = Path(artifact_path)
            stat = p.stat()
            if stat.st_size > MAX_ARTIFACT_BYTES:
                logger.warning(
                    "Review artifact %s is %.1f MB — using mtime-only run_id",
                    artifact_path,
                    stat.st_size / 1024 / 1024,
                )
                content = b""
            else:
                content = p.read_bytes()
            mtime = str(stat.st_mtime)
            run_id = hashlib.sha256(
                f"{reviewer}:{mtime}:".encode() + content
            ).hexdigest()[:16]
        except OSError:
            run_id = hashlib.sha256(
                f"{reviewer}:{datetime.now(UTC).isoformat()}".encode()
            ).hexdigest()[:16]

    findings = parse_review_artifact(artifact_path, reviewer, run_id)
    if not findings:
        logger.info("No findings to ingest from %s", artifact_path)
        return 0

    now = _now_iso()

    # Insert review_run
    conn.execute(
        "INSERT INTO review_runs (run_id, reviewer, reviewed_at, artifact_path) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT (run_id) DO NOTHING",
        (run_id, reviewer, now, artifact_path),
    )

    # Insert findings and collect issue_pattern updates (deduped per run)
    pattern_updates: dict[tuple[str, str], list[str]] = {}  # (rule_id, scope) -> examples
    for f in findings:
        cursor = conn.execute(
            "INSERT INTO findings "
            "(finding_id, run_id, severity, file_path, line, rule_id, summary, confidence) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (finding_id) DO NOTHING",
            (
                f.finding_id,
                f.run_id,
                f.severity.value if f.severity else None,
                f.file_path,
                f.line,
                f.rule_id,
                f.summary,
                f.confidence,
            ),
        )
        was_inserted = cursor.rowcount > 0

        # Collect issue_pattern updates, deduped by (rule_id, scope) per run.
        # Skip findings without file_path — empty scope would match all queries.
        if f.rule_id and f.file_path and was_inserted:
            scope = str(Path(f.file_path).parent) + "/"
            example = f"{f.file_path}:{f.line}" if f.file_path and f.line else f.file_path
            key = (f.rule_id, scope)
            if key not in pattern_updates:
                pattern_updates[key] = []
            if example and example not in pattern_updates[key]:
                pattern_updates[key].append(example)

    # Batch-update issue_patterns: increment count once per (rule_id, scope) per run
    for (rule_id, scope), new_examples in pattern_updates.items():
        existing = conn.execute(
            "SELECT examples_json FROM issue_patterns WHERE rule_id = ? AND scope_path = ?",
            (rule_id, scope),
        ).fetchone()
        if existing and existing["examples_json"]:
            examples: list[str] = json.loads(existing["examples_json"])
            for ex in new_examples:
                if ex not in examples:
                    examples.append(ex)
            examples = examples[-10:]  # Cap at 10 most recent
        else:
            examples = new_examples[:10]
        conn.execute(
            "INSERT INTO issue_patterns (rule_id, scope_path, count, last_seen_sha, examples_json) "
            "VALUES (?, ?, 1, ?, ?) "
            "ON CONFLICT (rule_id, scope_path) "
            "DO UPDATE SET count = issue_patterns.count + 1, "
            "last_seen_sha = excluded.last_seen_sha, "
            "examples_json = excluded.examples_json",
            (rule_id, scope, run_id, json.dumps(examples) if examples else None),
        )

    # Create CO_CHANGE edges between files in findings
    # Cap to avoid combinatorial explosion (e.g., 100 files → 4950 edges)
    file_paths = [f.file_path for f in findings if f.file_path]
    unique_files = sorted(set(file_paths))[:MAX_FILES_FOR_COMMIT]
    reaggregate_triples: set[tuple[str, str, str]] = set()
    for src, dst in itertools.combinations(unique_files, 2):
        _insert_evidence(
            conn, src, dst, Relation.CO_CHANGE.value, EvidenceSource.REVIEW.value, run_id, 0.9, now
        )
        reaggregate_triples.add((src, dst, Relation.CO_CHANGE.value))

    # Batch reaggregate after all evidence is inserted
    for src, dst, relation in reaggregate_triples:
        _reaggregate(conn, src, dst, relation)

    logger.info("Ingested %d findings from %s (%s)", len(findings), artifact_path, reviewer)
    return len(findings)


def _ingest_commit(
    conn: sqlite3.Connection,
    sha: str,
    committed_at: str | None = None,
    files: list[str] | None = None,
    *,
    _skip_git_checks: bool = False,
) -> int:
    """Ingest a git commit's co-change signals.

    Args:
        _skip_git_checks: If True, skip _resolve_sha and _is_merge_commit.
            Used by backfill where SHAs are already resolved concrete
            non-merge commits from ``git log --no-merges``.
    """
    if not _skip_git_checks:
        # Resolve symbolic refs (HEAD, branch names) to concrete SHAs
        sha = _resolve_sha(sha)

        # Skip merge commits — they inject branch-integration noise
        if _is_merge_commit(sha):
            logger.debug("Skipping merge commit %s", sha[:8])
            return 0

    if files is None:
        files = parse_git_show(sha)

    # Noise filter
    if len(files) < MIN_FILES_FOR_COMMIT or len(files) > MAX_FILES_FOR_COMMIT:
        logger.debug(
            "Skipping commit %s: %d files (outside %d-%d range)",
            sha,
            len(files),
            MIN_FILES_FOR_COMMIT,
            MAX_FILES_FOR_COMMIT,
        )
        return 0

    observed_at = committed_at or parse_git_commit_date(sha) or _now_iso()
    edge_count = 0
    reaggregate_triples: set[tuple[str, str, str]] = set()

    for src, dst in itertools.combinations(sorted(set(files)), 2):
        _insert_evidence(
            conn,
            src,
            dst,
            Relation.CO_CHANGE.value,
            EvidenceSource.COMMIT.value,
            sha,
            1.0,
            observed_at,
        )
        reaggregate_triples.add((src, dst, Relation.CO_CHANGE.value))
        edge_count += 1

    # Batch reaggregate after all evidence is inserted
    for src, dst, relation in reaggregate_triples:
        _reaggregate(conn, src, dst, relation)

    logger.info("Ingested commit %s: %d files, %d edges", sha[:8], len(files), edge_count)
    return edge_count


def _ingest_backfill(
    conn: sqlite3.Connection,
    since: str,
    *,
    commits: list[tuple[str, list[str]]] | None = None,
    commit_dates: dict[str, str | None] | None = None,
) -> int:
    """Backfill KB from git history.

    Args:
        commits: Pre-parsed commit list. When provided, skips git log parsing
            so that expensive subprocess calls happen outside the DB transaction.
        commit_dates: Pre-fetched {sha: iso_date} map. When provided, avoids
            subprocess calls to ``git show`` inside the DB transaction.
    """
    if commits is None:
        commits = parse_git_log_range(since)
    total_edges = 0
    for i, (sha, files) in enumerate(commits):
        committed_at = (commit_dates or {}).get(sha) or parse_git_commit_date(sha)
        edges = _ingest_commit(
            conn, sha, committed_at=committed_at, files=files, _skip_git_checks=True
        )
        total_edges += edges
        if (i + 1) % 50 == 0:
            logger.info("Backfill progress: %d/%d commits", i + 1, len(commits))
            conn.commit()
            _begin_immediate_with_retry(conn)

    logger.info("Backfill complete: %d commits, %d edges total", len(commits), total_edges)
    return total_edges


def _ingest_test(
    conn: sqlite3.Connection,
    junit_xml: str,
    changed_files: list[str],
    session_id: str | None = None,
    *,
    ingested_at: str | None = None,
) -> int:
    """Ingest test results from JUnit XML.

    Args:
        ingested_at: Optional timestamp override for started_at/finished_at.
            Used by deferred replay to preserve the original ingest time
            instead of using the replay time, which would invert temporal
            ordering in _ingest_error_fix.
    """
    # Hash JUnit XML content + context + ingested_at for deterministic run_id.
    # Excludes the file path so that deferred replay (which uses a snapshot path)
    # produces the same run_id as the original invocation for identical content.
    # Including ingested_at ensures distinct runs of identical test output get
    # unique IDs, while deferred replay preserves the same ingested_at → same ID.
    now = ingested_at or _now_iso()
    context = f"{session_id or ''}:{','.join(sorted(changed_files))}:{now}"
    try:
        p = Path(junit_xml)
        size = p.stat().st_size
        if size > MAX_ARTIFACT_BYTES:
            logger.warning("JUnit XML %s is %.1f MB — using context-only run_id", junit_xml, size / 1024 / 1024)
            content = b""
        else:
            content = p.read_bytes()
        run_id = hashlib.sha256(f"{context}:".encode() + content).hexdigest()[:16]
    except OSError:
        run_id = hashlib.sha256(context.encode()).hexdigest()[:16]
    results = parse_junit_xml(junit_xml, run_id)
    if not results:
        return 0

    has_failures = any(r.status.value == "FAIL" for r in results)

    # Ensure session exists if session_id provided (FK constraint)
    if session_id:
        conn.execute(
            "INSERT INTO implementation_sessions "
            "(session_id, started_at, outcome) "
            "VALUES (?, ?, 'WIP') "
            "ON CONFLICT (session_id) DO NOTHING",
            (session_id, now),
        )

    # Insert test_run
    conn.execute(
        "INSERT INTO test_runs "
        "(run_id, session_id, command, status, started_at, finished_at, git_sha, changed_files_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (run_id) DO NOTHING",
        (
            run_id,
            session_id,
            "pytest",
            "FAIL" if has_failures else "PASS",
            now,
            now,
            None,
            json.dumps(changed_files),
        ),
    )

    # Insert test_results
    for r in results:
        conn.execute(
            "INSERT INTO test_results (run_id, test_nodeid, status, error_signature, duration_ms) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (run_id, test_nodeid) DO NOTHING",
            (r.run_id, r.test_nodeid, r.status.value, r.error_signature, r.duration_ms),
        )

    # For failed tests, create TESTS edges to changed files
    edge_count = 0
    reaggregate_triples: set[tuple[str, str, str]] = set()
    failed_tests = [r for r in results if r.status.value == "FAIL"]
    for r in failed_tests:
        # Extract test file from nodeid (best-effort heuristic).
        # Assumes standard pytest conventions: lowercase modules, uppercase class names.
        # Projects with non-standard naming (e.g., "Test_Order.py") may need adjustment.
        # Path-style: "tests/test_foo.py::test_bar" -> "tests/test_foo.py"
        # Dot-style:  "tests.test_foo::test_bar" -> "tests/test_foo.py"
        # Class:      "tests.test_orders.TestRisk::test_method" -> "tests/test_orders.py"
        if "::" not in r.test_nodeid:
            continue  # Skip nodeids without :: — can't determine test file
        classname_part = r.test_nodeid.split("::")[0]
        if not classname_part:
            continue  # Skip malformed nodeids with no classname
        if "/" in classname_part:
            # Path-style nodeid — use the path directly
            test_file = classname_part
        elif classname_part.endswith(".py"):
            # Already a .py path in dot notation — use as-is
            test_file = classname_part
        else:
            parts = classname_part.split(".")
            # Keep only module parts (class names start with uppercase by convention).
            # Guard: if all parts are uppercase-initial (e.g., "TestModule.TestClass"),
            # fall back to the full dotted path to avoid producing an empty result.
            module_parts = [p for p in parts if p and not p[0].isupper()]
            if module_parts:
                test_file = "/".join(module_parts) + ".py"
            else:
                test_file = "/".join(parts) + ".py"
        for changed_file in changed_files:
            _insert_evidence(
                conn,
                test_file,
                changed_file,
                Relation.TESTS.value,
                EvidenceSource.TEST.value,
                run_id,
                0.9,
                now,
            )
            reaggregate_triples.add((test_file, changed_file, Relation.TESTS.value))
            edge_count += 1

    # Batch reaggregate after all evidence is inserted
    for src, dst, relation in reaggregate_triples:
        _reaggregate(conn, src, dst, relation)

    logger.info("Ingested test run %s: %d results, %d edges", run_id[:8], len(results), edge_count)
    return len(results)


def _ingest_error_fix(conn: sqlite3.Connection, session_id: str) -> int:
    """Link failing test runs to passing runs for the same error."""
    # Find failing runs in this session (include test_nodeid for precise matching)
    failing_runs = conn.execute(
        "SELECT tr.run_id, tres.error_signature, tres.test_nodeid, tr.changed_files_json "
        "FROM test_runs tr "
        "JOIN test_results tres ON tr.run_id = tres.run_id "
        "WHERE tr.session_id = ? AND tres.status = 'FAIL' AND tres.error_signature IS NOT NULL "
        "ORDER BY tr.started_at ASC",
        (session_id,),
    ).fetchall()

    if not failing_runs:
        return 0

    # Find passing test results in this session, matching by test_nodeid
    # Deduplicate: only process first failure per (error_signature, test_nodeid)
    now = _now_iso()
    fix_count = 0
    reaggregate_triples: set[tuple[str, str, str]] = set()
    seen_sig_nodeids: set[tuple[str, str]] = set()

    for failing in failing_runs:
        error_sig = failing["error_signature"]
        failing_nodeid = failing["test_nodeid"]

        # Skip if we already successfully linked this (error_signature, test_nodeid) pair
        sig_key = (error_sig, failing_nodeid)
        if sig_key in seen_sig_nodeids:
            continue

        failing_files = json.loads(failing["changed_files_json"] or "[]")

        # Find passing runs where the same test now passes (strictly after the failure),
        # ordered earliest-first.  We iterate until we find one with file overlap.
        passing_runs = conn.execute(
            "SELECT tr.run_id, tres.test_nodeid, tr.changed_files_json "
            "FROM test_runs tr "
            "JOIN test_results tres ON tr.run_id = tres.run_id "
            "WHERE tr.session_id = ? AND tres.status = 'PASS' "
            "AND tr.run_id != ? "
            "AND tr.started_at > (SELECT tr2.started_at FROM test_runs tr2 WHERE tr2.run_id = ?) "
            "AND tres.test_nodeid = ? "
            "ORDER BY tr.started_at ASC",
            (session_id, failing["run_id"], failing["run_id"], failing_nodeid),
        ).fetchall()

        for passing in passing_runs:
            passing_files = json.loads(passing["changed_files_json"] or "[]")
            # Files new in the passing run are likely fix targets
            fixed_files = list(set(passing_files) - set(failing_files))
            if not fixed_files:
                # Same file set — fix was likely within common files
                fixed_files = list(set(passing_files) & set(failing_files))
            if not fixed_files:
                # No overlap at all — try the next passing run
                continue

            fix_id = hashlib.sha256(
                f"{session_id}:{error_sig}:{failing['run_id']}:{passing['run_id']}".encode()
            ).hexdigest()[:16]

            cursor = conn.execute(
                "INSERT INTO error_fixes "
                "(fix_id, session_id, error_signature, failing_run_id, passing_run_id, "
                "fixed_files_json, confidence) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (fix_id) DO NOTHING",
                (
                    fix_id,
                    session_id,
                    error_sig,
                    failing["run_id"],
                    passing["run_id"],
                    json.dumps(fixed_files),
                    0.8,
                ),
            )

            # Mark this (error_sig, test_nodeid) as processed regardless of whether the
            # fix already existed (idempotent rerun) or was newly inserted.
            seen_sig_nodeids.add(sig_key)

            if cursor.rowcount == 0:
                # Fix already exists from a prior run — no new evidence needed
                break

            # Create ERROR_FIX edges
            for fixed_file in fixed_files:
                _insert_evidence(
                    conn,
                    error_sig,
                    fixed_file,
                    Relation.ERROR_FIX.value,
                    EvidenceSource.ERROR_FIX.value,
                    fix_id,
                    0.9,
                    now,
                )
                reaggregate_triples.add((error_sig, fixed_file, Relation.ERROR_FIX.value))
            fix_count += 1
            # Only use the first passing run with file overlap
            break

    # Batch reaggregate after all evidence is inserted
    for src, dst, relation in reaggregate_triples:
        _reaggregate(conn, src, dst, relation)

    logger.info("Ingested %d error-fix links for session %s", fix_count, session_id)
    return fix_count


def _ingest_analyze(conn: sqlite3.Connection, artifact_path: str) -> int:
    """Ingest /analyze structured output."""
    path = Path(artifact_path)
    if not path.exists():
        logger.warning("Analyze artifact not found: %s", artifact_path)
        return 0

    try:
        data: Any = json.loads(path.read_text())
    except (json.JSONDecodeError, ValueError):
        logger.warning("Analyze artifact is not valid JSON, skipping: %s", artifact_path)
        return 0
    if not isinstance(data, dict):
        logger.warning("Analyze artifact is not a JSON object, skipping: %s", artifact_path)
        return 0
    raw_files_val: Any = data.get("impacted_files")
    if raw_files_val is None:
        raw_files_val = data.get("files", [])
    raw_files: list[Any] = raw_files_val if isinstance(raw_files_val, list) else []
    if not raw_files:
        return 0

    now = _now_iso()
    # Use content hash for idempotent source_id (re-ingesting same artifact is a no-op)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    source_id = content_hash
    edge_count = 0

    # Support both dict entries ({"path": "a.py"}) and plain strings ("a.py").
    # Normalize all paths to repo-relative form so the graph uses consistent
    # identifiers regardless of whether the artifact contains absolute paths.
    repo_root = _get_repo_root().resolve()
    file_paths: list[str] = []
    for f in raw_files:
        raw_path = ""
        if isinstance(f, str):
            raw_path = f
        elif isinstance(f, dict):
            raw_p = f.get("path", f.get("file", ""))
            raw_path = str(raw_p) if isinstance(raw_p, str) else ""
        if raw_path:
            p = Path(raw_path)
            if p.is_absolute():
                try:
                    raw_path = str(p.resolve().relative_to(repo_root))
                except ValueError:
                    pass  # Outside repo — keep as-is
            file_paths.append(raw_path)
    reaggregate_triples: set[tuple[str, str, str]] = set()
    # Cap to avoid combinatorial explosion with large analyze outputs
    for src, dst in itertools.combinations(sorted(set(file_paths))[:MAX_FILES_FOR_COMMIT], 2):
        _insert_evidence(
            conn,
            src,
            dst,
            Relation.CO_CHANGE.value,
            EvidenceSource.ANALYZE.value,
            source_id,
            0.7,
            now,
        )
        reaggregate_triples.add((src, dst, Relation.CO_CHANGE.value))
        edge_count += 1

    # Batch reaggregate after all evidence is inserted
    for src, dst, relation in reaggregate_triples:
        _reaggregate(conn, src, dst, relation)

    logger.info("Ingested analyze artifact: %d files, %d edges", len(file_paths), edge_count)
    return edge_count


def _ingest_session_finalize(
    conn: sqlite3.Connection,
    session_id: str,
    outcome: str = "COMMITTED",
    edited_files: list[str] | None = None,
    searched_files: list[str] | None = None,
    branch: str | None = None,
    base_sha: str | None = None,
    head_sha: str | None = None,
) -> int:
    """Finalize a session — upsert session record and create edges."""
    now = _now_iso()

    # Validate outcome
    try:
        session_outcome = SessionOutcome(outcome)
    except ValueError:
        logger.warning("Invalid session outcome: %s", outcome)
        return 0

    # WIP sessions are still active — don't set ended_at
    ended_at = None if session_outcome == SessionOutcome.WIP else now

    # Upsert implementation_session
    conn.execute(
        "INSERT INTO implementation_sessions "
        "(session_id, started_at, ended_at, branch, base_sha, head_sha, outcome) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (session_id) "
        "DO UPDATE SET ended_at = excluded.ended_at, "
        "branch = COALESCE(excluded.branch, implementation_sessions.branch), "
        "base_sha = COALESCE(excluded.base_sha, implementation_sessions.base_sha), "
        "head_sha = COALESCE(excluded.head_sha, implementation_sessions.head_sha), "
        "outcome = excluded.outcome",
        (session_id, now, ended_at, branch, base_sha, head_sha, session_outcome.value),
    )

    edge_count = 0
    reaggregate_triples: set[tuple[str, str, str]] = set()

    # Edit co-occurrence edges (only if committed)
    # Cap to avoid combinatorial explosion with large sessions
    if edited_files and session_outcome == SessionOutcome.COMMITTED:
        weight = 0.4
        unique_files = sorted(set(edited_files))[:MAX_FILES_FOR_COMMIT]
        for src, dst in itertools.combinations(unique_files, 2):
            _insert_evidence(
                conn,
                src,
                dst,
                Relation.CO_CHANGE.value,
                EvidenceSource.SESSION.value,
                session_id,
                weight,
                now,
            )
            reaggregate_triples.add((src, dst, Relation.CO_CHANGE.value))
            edge_count += 1

    # Search/open patterns (advisory only, weight=0.1)
    if searched_files and session_outcome == SessionOutcome.COMMITTED:
        for src, dst in itertools.combinations(sorted(set(searched_files[:20])), 2):
            _insert_evidence(
                conn,
                src,
                dst,
                Relation.REFERENCES.value,
                EvidenceSource.SESSION.value,
                session_id,
                0.1,
                now,
            )
            reaggregate_triples.add((src, dst, Relation.REFERENCES.value))
            edge_count += 1

    # Batch reaggregate after all evidence is inserted
    for src, dst, relation in reaggregate_triples:
        _reaggregate(conn, src, dst, relation)

    logger.info(
        "Finalized session %s: outcome=%s, %d edges",
        session_id,
        session_outcome.value,
        edge_count,
    )
    return edge_count


# === Deferred queue replay ===


def replay_deferred(db_path: str | None = None) -> int:
    """Replay deferred ingest payloads from the queue file.

    Only replays entries that were deferred for the same database.
    Returns the number of successfully replayed entries.
    """
    queue_path = DEFERRED_QUEUE_PATH
    if not queue_path.exists():
        return 0

    if fcntl is not None:
        # Acquire exclusive lock to prevent concurrent replay/write races
        lock_path = queue_path.parent / "deferred_ingest.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            lock_fd = open(lock_path, "w")  # noqa: SIM115
        except OSError:
            logger.warning("Could not open deferred queue lock file, skipping replay")
            return 0
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        except OSError:
            logger.warning("Could not acquire deferred queue lock, skipping replay")
            lock_fd.close()
            return 0

        try:
            return _replay_deferred_locked(queue_path, db_path)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
    else:
        # Windows fallback: use msvcrt locking (matching write_deferred)
        import msvcrt

        lock_path = queue_path.parent / "deferred_ingest.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            lock_fd = open(lock_path, "w")  # noqa: SIM115
        except OSError:
            logger.warning("Could not open deferred queue lock file, skipping replay")
            return 0
        try:
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_LOCK, 1)
        except OSError:
            logger.warning("Could not acquire deferred queue lock, skipping replay")
            lock_fd.close()
            return 0

        try:
            return _replay_deferred_locked(queue_path, db_path)
        finally:
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
            lock_fd.close()


def _replay_deferred_locked(queue_path: Path, db_path: str | None) -> int:
    """Replay deferred entries while holding exclusive lock."""
    if not queue_path.exists():
        return 0

    # Read line-by-line to avoid loading the entire queue as a single string,
    # which could cause memory pressure on very large deferred queues.
    with open(queue_path) as f:
        lines = [ln.rstrip("\n") for ln in f if ln.strip()]
    if not lines:
        return 0

    logger.info("Replaying %d deferred ingest entries", len(lines))

    # Resolve current db path for comparison with stored entries
    current_db = (
        str((_get_repo_root() / db_path).resolve()) if db_path else str(DEFAULT_DB_PATH.resolve())
    )

    # Map func names to actual functions
    func_map: dict[str, Any] = {
        "_ingest_review": _ingest_review,
        "_ingest_commit": _ingest_commit,
        "_ingest_test": _ingest_test,
        "_ingest_error_fix": _ingest_error_fix,
        "_ingest_analyze": _ingest_analyze,
        "_ingest_session_finalize": _ingest_session_finalize,
    }

    conn = get_connection(db_path)
    init_schema(conn)
    replayed = 0
    remaining: list[str] = []
    snapshots_to_clean: list[Path] = []
    _file_based_funcs = {"_ingest_review", "_ingest_test", "_ingest_analyze"}

    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Malformed deferred entry, preserving for inspection: %s", line[:200])
            remaining.append(line)
            continue

        if not isinstance(payload, dict):
            logger.warning("Deferred entry is not a JSON object, skipping: %s", line[:200])
            continue

        # Skip entries that belong to a different database
        stored_db = payload.get("db")
        if stored_db and stored_db != current_db:
            remaining.append(line)
            continue

        func_name = payload.get("func", "")
        func_args = payload.get("args", [])
        func_kwargs: dict[str, Any] = payload.get("kwargs", {})

        func = func_map.get(func_name)
        if func is None:
            logger.warning("Unknown deferred func: %s, preserving for future replay", func_name)
            remaining.append(line)
            continue

        # File-based ingests reference an artifact path as their first arg.
        # If the file no longer exists (e.g., temp file cleaned up), the replay
        # would silently produce no data. Log a warning so the signal loss is visible.
        if func_name in _file_based_funcs and func_args:
            artifact = Path(func_args[0])
            if not artifact.exists():
                logger.warning(
                    "Deferred %s references missing file %s — signal permanently lost",
                    func_name,
                    artifact,
                )
                continue

        try:
            _begin_immediate_with_retry(conn)
            func(conn, *func_args, **func_kwargs)
            conn.commit()
            replayed += 1
            # Collect snapshot artifacts for deferred cleanup (after all entries processed)
            if func_name in _file_based_funcs and func_args:
                artifact_path = Path(func_args[0])
                # Anchor relative db_path to repo root, matching _snapshot_artifact.
                if db_path:
                    p = Path(db_path)
                    active_base = (p if p.is_absolute() else _get_repo_root() / p).parent
                else:
                    active_base = DEFAULT_DB_PATH.parent
                snapshot_dir = active_base / "deferred_artifacts"
                try:
                    if artifact_path.parent.resolve() == snapshot_dir.resolve():
                        snapshots_to_clean.append(artifact_path)
                except OSError:
                    pass
        except sqlite3.OperationalError as exc:
            conn.rollback()
            remaining.append(line)
            if is_lock_error(exc):
                logger.debug("DB locked, requeueing deferred %s", func_name)
            else:
                logger.warning("Failed to replay deferred %s: %s", func_name, exc)
        except Exception:
            conn.rollback()
            remaining.append(line)
            logger.warning("Failed to replay deferred %s", func_name, exc_info=True)

    conn.close()

    # Collect artifact paths still referenced by remaining (requeued) entries.
    # Only delete snapshots that no remaining entry points to.
    remaining_artifacts: set[str] = set()
    for line in remaining:
        try:
            payload = json.loads(line)
            r_func = payload.get("func", "")
            r_args = payload.get("args", [])
            if r_func in _file_based_funcs and r_args:
                remaining_artifacts.add(str(Path(r_args[0]).resolve()))
        except (json.JSONDecodeError, TypeError, IndexError):
            pass

    for snap in snapshots_to_clean:
        try:
            if str(snap.resolve()) not in remaining_artifacts:
                snap.unlink(missing_ok=True)
        except OSError:
            pass  # Best-effort cleanup

    # Rewrite queue with only remaining entries
    if remaining:
        queue_path.write_text("\n".join(remaining) + "\n")
    else:
        queue_path.unlink(missing_ok=True)

    logger.info("Replayed %d deferred entries, %d remaining", replayed, len(remaining))
    return replayed
