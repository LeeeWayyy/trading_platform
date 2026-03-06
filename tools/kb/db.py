"""SQLite knowledge base database — connection, schema, and retry logic."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

_T = TypeVar("_T")

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]  # Windows: file locking unavailable

logger = logging.getLogger(__name__)


def _get_repo_root() -> Path:
    """Get the git repository root directory for stable default paths.

    Anchoring defaults to the repo root ensures the same DB is used
    regardless of the working directory when invoking KB commands.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return Path.cwd()


def _resolve_default_db_path() -> Path:
    """Resolve the default DB path, anchoring relative paths to the repo root."""
    if "KB_DB_PATH" in os.environ:
        p = Path(os.environ["KB_DB_PATH"])
        return p if p.is_absolute() else _get_repo_root() / p
    return _get_repo_root() / ".claude/kb/graph.db"


DEFAULT_DB_PATH = _resolve_default_db_path()
DEFERRED_QUEUE_PATH = DEFAULT_DB_PATH.parent / "deferred_ingest.jsonl"

SCHEMA_SQL = """
-- Core aggregated graph (query target)
CREATE TABLE IF NOT EXISTS file_edges (
    src_file TEXT NOT NULL,
    dst_file TEXT NOT NULL,
    relation TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 0.0,
    support_count INTEGER NOT NULL DEFAULT 0,
    last_seen_sha TEXT,
    PRIMARY KEY (src_file, dst_file, relation)
);

-- Evidence layer (raw facts, feeds into file_edges via aggregation)
CREATE TABLE IF NOT EXISTS edge_evidence (
    evidence_id TEXT PRIMARY KEY,
    src_file TEXT NOT NULL,
    dst_file TEXT NOT NULL,
    relation TEXT NOT NULL,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    weight REAL NOT NULL,
    observed_at TEXT NOT NULL
);

-- Recurring issue patterns
CREATE TABLE IF NOT EXISTS issue_patterns (
    rule_id TEXT NOT NULL,
    scope_path TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    last_seen_sha TEXT,
    examples_json TEXT,
    PRIMARY KEY (rule_id, scope_path)
);

-- Review run metadata
CREATE TABLE IF NOT EXISTS review_runs (
    run_id TEXT PRIMARY KEY,
    reviewer TEXT,
    commit_sha TEXT,
    reviewed_at TEXT,
    artifact_path TEXT
);

-- Review findings
CREATE TABLE IF NOT EXISTS findings (
    finding_id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES review_runs(run_id),
    severity TEXT,
    file_path TEXT,
    line INTEGER,
    rule_id TEXT,
    summary TEXT,
    fixed_in_sha TEXT,
    confidence REAL
);

-- Implementation session tracking
CREATE TABLE IF NOT EXISTS implementation_sessions (
    session_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    branch TEXT,
    base_sha TEXT,
    head_sha TEXT,
    outcome TEXT NOT NULL
);

-- Test execution runs
CREATE TABLE IF NOT EXISTS test_runs (
    run_id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES implementation_sessions(session_id),
    command TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    git_sha TEXT,
    changed_files_json TEXT
);

-- Individual test results
CREATE TABLE IF NOT EXISTS test_results (
    run_id TEXT NOT NULL REFERENCES test_runs(run_id),
    test_nodeid TEXT NOT NULL,
    status TEXT NOT NULL,
    error_signature TEXT,
    duration_ms INTEGER,
    PRIMARY KEY (run_id, test_nodeid)
);

-- Error-fix mappings
CREATE TABLE IF NOT EXISTS error_fixes (
    fix_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES implementation_sessions(session_id),
    error_signature TEXT NOT NULL,
    failing_run_id TEXT REFERENCES test_runs(run_id),
    passing_run_id TEXT REFERENCES test_runs(run_id),
    fixed_files_json TEXT NOT NULL,
    confidence REAL NOT NULL
);

-- Secondary indices for common query patterns
CREATE INDEX IF NOT EXISTS idx_file_edges_dst ON file_edges (dst_file);
CREATE INDEX IF NOT EXISTS idx_edge_evidence_triple ON edge_evidence (src_file, dst_file, relation);
CREATE INDEX IF NOT EXISTS idx_test_runs_session ON test_runs (session_id);
CREATE INDEX IF NOT EXISTS idx_findings_run ON findings (run_id);
CREATE INDEX IF NOT EXISTS idx_error_fixes_signature ON error_fixes (error_signature);
"""

FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS findings_fts USING fts5(
    summary,
    content=findings,
    content_rowid=rowid,
    tokenize='trigram'
);
"""

PRAGMAS = [
    "PRAGMA journal_mode=WAL",
    "PRAGMA busy_timeout=5000",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA mmap_size=67108864",  # 64MB
]

MAX_RETRIES = 3
RETRY_BACKOFFS = [1.0, 2.0, 4.0]


def is_lock_error(exc: sqlite3.OperationalError) -> bool:
    """Check if an OperationalError is a transient SQLite lock (BUSY or LOCKED)."""
    msg = str(exc).lower()
    return (
        "database is locked" in msg
        or "database table is locked" in msg
        or "database schema is locked" in msg
    )


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with KB pragmas applied.

    Relative paths are anchored to the repository root for consistency
    with deferred queue path resolution in write_deferred/replay_deferred.
    """
    if db_path:
        p = Path(db_path)
        path = p if p.is_absolute() else _get_repo_root() / p
    else:
        path = DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    for pragma in PRAGMAS:
        conn.execute(pragma)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all 9 tables idempotently. FTS5 is optional."""
    _executescript_with_retry(conn, SCHEMA_SQL)
    try:
        _executescript_with_retry(conn, FTS_SQL)
    except sqlite3.OperationalError:
        logger.warning("FTS5 unavailable; full-text search on findings disabled")


def _with_busy_retry(fn: Callable[[], _T], label: str = "") -> _T:
    """Execute *fn* with exponential backoff retry on SQLITE_BUSY.

    Shared retry core used by execute_with_retry, commit_with_retry,
    and _executescript_with_retry to eliminate code duplication.
    """
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if not is_lock_error(exc):
                raise
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFFS[attempt]
                logger.warning(
                    "SQLITE_BUSY%s, retry %d/%d after %.1fs",
                    f" on {label}" if label else "",
                    attempt + 1,
                    MAX_RETRIES,
                    wait,
                )
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


def execute_with_retry(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...] = (),
) -> sqlite3.Cursor:
    """Execute SQL with exponential backoff retry on SQLITE_BUSY."""
    return _with_busy_retry(lambda: conn.execute(sql, params), "execute")


def commit_with_retry(conn: sqlite3.Connection) -> None:
    """Commit with exponential backoff retry on SQLITE_BUSY."""
    _with_busy_retry(conn.commit, "commit")


def _executescript_with_retry(conn: sqlite3.Connection, sql: str) -> None:
    """Execute a SQL script with retry on SQLITE_BUSY."""
    _with_busy_retry(lambda: conn.executescript(sql), "executescript")


def write_deferred(
    payload: dict[str, Any],
    queue_path: Path | str | None = None,
    db_path: Path | str | None = None,
) -> None:
    """Append a failed ingest payload to the deferred queue for later retry.

    Stores the originating db_path so entries are only replayed against
    the same database that produced them.
    """
    path = Path(queue_path) if queue_path else DEFERRED_QUEUE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    # Scope to originating database (anchor relative paths to repo root for stable
    # identity across invocations from different working directories)
    resolved_db = (
        str((_get_repo_root() / db_path).resolve()) if db_path else str(DEFAULT_DB_PATH.resolve())
    )
    payload = {**payload, "db": resolved_db}
    if fcntl is not None:
        lock_path = path.parent / "deferred_ingest.lock"
        lock_fd = open(lock_path, "w")  # noqa: SIM115
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            with open(path, "a") as f:
                f.write(json.dumps(payload) + "\n")
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
    else:
        # Windows fallback: use msvcrt for file locking via separate lock file
        # (matching the fcntl approach — locking the data file directly causes
        # byte-offset mismatches between lock and unlock in append mode)
        import msvcrt

        lock_path = path.parent / "deferred_ingest.lock"
        lock_fd = open(lock_path, "w")  # noqa: SIM115
        try:
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_LOCK, 1)
            with open(path, "a") as f:
                f.write(json.dumps(payload) + "\n")
        finally:
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
            lock_fd.close()
    logger.info("Wrote deferred ingest payload to %s", path)


def checkpoint(conn: sqlite3.Connection) -> None:
    """Run WAL checkpoint to prevent unbounded WAL file growth."""
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
