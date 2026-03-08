"""CLI handlers and argparse entrypoint for the KB ingest pipeline.

Separated from ingest.py (core logic) to keep each module focused and
under ~900 lines, per maintainability guidelines.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from tools.kb.db import checkpoint, get_connection, init_schema, is_lock_error, write_deferred
from tools.kb.ingest import (
    MAX_ARTIFACT_BYTES,
    _begin_immediate_with_retry,
    _ingest_analyze,
    _ingest_backfill,
    _ingest_commit,
    _ingest_error_fix,
    _ingest_review,
    _ingest_session_finalize,
    _ingest_test,
    _now_iso,
    _resolve_sha,
    _snapshot_artifact,
    replay_deferred,
)
from tools.kb.parsers import parse_git_commit_date, parse_git_log_range

logger = logging.getLogger(__name__)


def cmd_review(args: argparse.Namespace) -> None:
    """Handle 'review' subcommand."""
    conn = get_connection(args.db)
    # Pre-generate run_id so it is stable across deferral and replay.
    # If not provided, derive from reviewer + content + mtime (same logic as _ingest_review).
    run_id = getattr(args, "run_id", None)
    if run_id is None:
        try:
            p = Path(args.artifact)
            stat = p.stat()
            if stat.st_size > MAX_ARTIFACT_BYTES:
                artifact_content = b""
            else:
                artifact_content = p.read_bytes()
            mtime = str(stat.st_mtime)
            run_id = hashlib.sha256(
                f"{args.reviewer}:{mtime}:".encode() + artifact_content
            ).hexdigest()[:16]
        except OSError:
            run_id = hashlib.sha256(
                f"{args.reviewer}:{datetime.now(UTC).isoformat()}".encode()
            ).hexdigest()[:16]
    try:
        init_schema(conn)
        _begin_immediate_with_retry(conn)
        count = _ingest_review(conn, args.artifact, args.reviewer, run_id)
        conn.commit()
        print(f"Ingested {count} findings")
    except sqlite3.OperationalError as exc:
        conn.rollback()
        if is_lock_error(exc):
            logger.warning("SQLITE_BUSY during review ingest, deferring: %s", exc)
            snapshot_path = _snapshot_artifact(args.artifact, db_path=args.db)
            write_deferred(
                {
                    "func": "_ingest_review",
                    "args": [snapshot_path, args.reviewer, run_id],
                },
                db_path=args.db,
            )
        else:
            raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cmd_commit(args: argparse.Namespace) -> None:
    """Handle 'commit' subcommand."""
    # Resolve symbolic refs (e.g., HEAD) to concrete SHAs before any deferral
    resolved_sha = _resolve_sha(args.sha)
    conn = get_connection(args.db)
    try:
        init_schema(conn)
        _begin_immediate_with_retry(conn)
        count = _ingest_commit(conn, resolved_sha)
        conn.commit()
        print(f"Ingested {count} edges")
    except sqlite3.OperationalError as exc:
        conn.rollback()
        if is_lock_error(exc):
            logger.warning("SQLITE_BUSY during commit ingest, deferring: %s", exc)
            write_deferred({"func": "_ingest_commit", "args": [resolved_sha]}, db_path=args.db)
        else:
            raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cmd_backfill(args: argparse.Namespace) -> None:
    """Handle 'backfill' subcommand."""
    # Parse git history and fetch commit dates before acquiring DB lock
    # to minimize lock duration (subprocess calls are expensive).
    commits = parse_git_log_range(args.since)
    commit_dates = {sha: parse_git_commit_date(sha) for sha, _ in commits}
    conn = get_connection(args.db)
    try:
        init_schema(conn)
        _begin_immediate_with_retry(conn)
        count = _ingest_backfill(
            conn, args.since, commits=commits, commit_dates=commit_dates
        )
        conn.commit()
        checkpoint(conn)
        print(f"Backfilled {count} edges")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cmd_test(args: argparse.Namespace) -> None:
    """Handle 'test' subcommand."""
    conn = get_connection(args.db)
    changed_files = [f.strip() for f in re.split(r"[,\n]", args.changed_files) if f.strip()]
    ingested_at = _now_iso()
    try:
        init_schema(conn)
        _begin_immediate_with_retry(conn)
        count = _ingest_test(
            conn, args.junit_xml, changed_files,
            getattr(args, "session_id", None),
            ingested_at=ingested_at,
        )
        conn.commit()
        print(f"Ingested {count} test results")
    except sqlite3.OperationalError as exc:
        conn.rollback()
        if is_lock_error(exc):
            logger.warning("SQLITE_BUSY during test ingest, deferring: %s", exc)
            # Snapshot the JUnit XML to prevent data loss if the original
            # file (e.g., .pytest_results.xml) is overwritten before replay
            snapshot_path = _snapshot_artifact(args.junit_xml, db_path=args.db)
            write_deferred(
                {
                    "func": "_ingest_test",
                    "args": [snapshot_path, changed_files, getattr(args, "session_id", None)],
                    "kwargs": {"ingested_at": ingested_at},
                },
                db_path=args.db,
            )
        else:
            raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cmd_error_fix(args: argparse.Namespace) -> None:
    """Handle 'error-fix' subcommand."""
    conn = get_connection(args.db)
    try:
        init_schema(conn)
        _begin_immediate_with_retry(conn)
        count = _ingest_error_fix(conn, args.session_id)
        conn.commit()
        print(f"Linked {count} error-fix pairs")
    except sqlite3.OperationalError as exc:
        conn.rollback()
        if is_lock_error(exc):
            logger.warning("SQLITE_BUSY during error-fix ingest, deferring: %s", exc)
            write_deferred(
                {"func": "_ingest_error_fix", "args": [args.session_id]}, db_path=args.db
            )
        else:
            raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cmd_analyze(args: argparse.Namespace) -> None:
    """Handle 'analyze' subcommand."""
    conn = get_connection(args.db)
    try:
        init_schema(conn)
        _begin_immediate_with_retry(conn)
        count = _ingest_analyze(conn, args.artifact)
        conn.commit()
        print(f"Ingested {count} edges from analyze")
    except sqlite3.OperationalError as exc:
        conn.rollback()
        if is_lock_error(exc):
            logger.warning("SQLITE_BUSY during analyze ingest, deferring: %s", exc)
            snapshot_path = _snapshot_artifact(args.artifact, db_path=args.db)
            write_deferred({"func": "_ingest_analyze", "args": [snapshot_path]}, db_path=args.db)
        else:
            raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cmd_session_finalize(args: argparse.Namespace) -> None:
    """Handle 'session-finalize' subcommand."""
    conn = get_connection(args.db)
    edited = [f.strip() for f in re.split(r"[,\n]", args.edited_files or "") if f.strip()]
    searched = [f.strip() for f in re.split(r"[,\n]", args.searched_files or "") if f.strip()]
    try:
        init_schema(conn)
        _begin_immediate_with_retry(conn)
        count = _ingest_session_finalize(
            conn,
            args.session_id,
            outcome=args.outcome,
            edited_files=edited or None,
            searched_files=searched or None,
            branch=getattr(args, "branch", None),
            base_sha=getattr(args, "base_sha", None),
            head_sha=getattr(args, "head_sha", None),
        )
        conn.commit()
        print(f"Finalized session with {count} edges")
    except sqlite3.OperationalError as exc:
        conn.rollback()
        if is_lock_error(exc):
            logger.warning("SQLITE_BUSY during session-finalize, deferring: %s", exc)
            write_deferred(
                {
                    "func": "_ingest_session_finalize",
                    "args": [args.session_id],
                    "kwargs": {
                        "outcome": args.outcome,
                        "edited_files": edited or None,
                        "searched_files": searched or None,
                        "branch": getattr(args, "branch", None),
                        "base_sha": getattr(args, "base_sha", None),
                        "head_sha": getattr(args, "head_sha", None),
                    },
                },
                db_path=args.db,
            )
        else:
            raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> None:
    """CLI entrypoint for KB ingest pipeline."""
    parser = argparse.ArgumentParser(
        prog="kb-ingest",
        description="Knowledge Base ingest pipeline",
    )
    parser.add_argument(
        "--db", type=str, default=None, help="Database path (default: .claude/kb/graph.db)"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # review
    p_review = subparsers.add_parser("review", help="Ingest review findings")
    p_review.add_argument("--artifact", required=True, help="Path to review artifact JSON/JSONL")
    p_review.add_argument("--reviewer", required=True, help="Reviewer name (gemini, codex)")
    p_review.add_argument("--run-id", dest="run_id", help="Optional run ID")
    p_review.set_defaults(func=cmd_review)

    # commit
    p_commit = subparsers.add_parser("commit", help="Ingest commit co-change signals")
    p_commit.add_argument("--sha", required=True, help="Commit SHA")
    p_commit.set_defaults(func=cmd_commit)

    # backfill
    p_backfill = subparsers.add_parser("backfill", help="Backfill from git history")
    p_backfill.add_argument(
        "--since", required=True, help='Git log --since value (e.g., "6 months ago")'
    )
    p_backfill.set_defaults(func=cmd_backfill)

    # test
    p_test = subparsers.add_parser("test", help="Ingest test results")
    p_test.add_argument("--junit-xml", required=True, help="Path to JUnit XML")
    p_test.add_argument("--changed-files", required=True, help="Comma-separated changed files")
    p_test.add_argument("--session-id", dest="session_id", help="Optional session ID")
    p_test.set_defaults(func=cmd_test)

    # error-fix
    p_errfix = subparsers.add_parser("error-fix", help="Link failing→passing test runs")
    p_errfix.add_argument("--session-id", required=True, help="Session ID")
    p_errfix.set_defaults(func=cmd_error_fix)

    # analyze
    p_analyze = subparsers.add_parser("analyze", help="Ingest /analyze output")
    p_analyze.add_argument("--artifact", required=True, help="Path to analyze JSON artifact")
    p_analyze.set_defaults(func=cmd_analyze)

    # session-finalize
    p_session = subparsers.add_parser("session-finalize", help="Finalize implementation session")
    p_session.add_argument("--session-id", required=True, help="Session ID")
    p_session.add_argument(
        "--outcome", default="COMMITTED", choices=["COMMITTED", "ABANDONED", "WIP"]
    )
    p_session.add_argument("--edited-files", help="Comma-separated edited files")
    p_session.add_argument("--searched-files", help="Comma-separated searched files")
    p_session.add_argument("--branch", help="Branch name")
    p_session.add_argument("--base-sha", help="Base commit SHA")
    p_session.add_argument("--head-sha", help="Head commit SHA")
    p_session.set_defaults(func=cmd_session_finalize)

    args = parser.parse_args(argv)

    # Replay any deferred entries from previous failed ingests
    replay_deferred(args.db)

    args.func(args)


if __name__ == "__main__":
    main()
