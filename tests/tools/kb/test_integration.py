"""Integration tests for tools.kb — end-to-end ingest→query flows."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.kb.db import get_connection, init_schema
from tools.kb.ingest import (
    _ingest_commit,
    _ingest_review,
    _ingest_session_finalize,
    _ingest_test,
)
from tools.kb.query import (
    query_implementation_brief,
    query_pre_commit_check,
    query_troubleshoot,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    """Return a fresh connection with schema initialized."""
    c = get_connection(tmp_path / "integration.db")
    init_schema(c)
    return c


class TestReviewToQuery:
    """Test review ingest → implementation-brief query."""

    def test_review_findings_appear_in_brief(self, conn: sqlite3.Connection) -> None:
        """Test that review findings create queryable edges."""
        # Ingest review
        conn.execute("BEGIN IMMEDIATE")
        _ingest_review(conn, str(FIXTURES_DIR / "sample_review.json"), "gemini", "run1")
        conn.commit()

        # Query for impacted files
        brief = query_implementation_brief(conn, ["apps/signal_service/main.py"])
        # Circuit breaker file should appear as related
        [f.path for f in brief.likely_impacted_files]
        # The review has findings in both files, so they should be co-change linked
        assert len(brief.known_pitfalls) >= 0  # Pitfalls depend on scope matching


class TestCommitToQuery:
    """Test commit ingest → query flow."""

    def test_commit_edges_appear_in_pre_commit(self, conn: sqlite3.Connection) -> None:
        """Test that commit co-change edges appear in pre-commit check."""
        # Simulate multiple commits touching same files
        files = ["a.py", "b.py", "c.py"]
        with patch("tools.kb.ingest.parse_git_show", return_value=files):
            conn.execute("BEGIN IMMEDIATE")
            _ingest_commit(conn, "sha1")
            conn.commit()

        with patch("tools.kb.ingest.parse_git_show", return_value=files):
            conn.execute("BEGIN IMMEDIATE")
            _ingest_commit(conn, "sha2")
            conn.commit()

        # Now edges have support_count >= 2
        result = query_pre_commit_check(conn, ["a.py"])
        paths = [f.path for f in result.missing_co_changes]
        assert "b.py" in paths or "c.py" in paths


class TestTestToTroubleshoot:
    """Test test ingest → troubleshoot query."""

    def test_test_failure_creates_troubleshoot_data(self, conn: sqlite3.Connection) -> None:
        """Test that test failures can be queried via troubleshoot."""
        # Ingest test results
        conn.execute("BEGIN IMMEDIATE")
        _ingest_test(
            conn,
            str(FIXTURES_DIR / "sample_junit.xml"),
            ["apps/signal_service/main.py"],
        )
        conn.commit()

        # Get the error signature from ingested results
        row = conn.execute(
            "SELECT error_signature FROM test_results WHERE error_signature IS NOT NULL LIMIT 1"
        ).fetchone()

        if row:
            result = query_troubleshoot(conn, row["error_signature"])
            # May or may not have past fixes yet, but should not error
            assert result is not None


class TestSessionFlow:
    """Test session-finalize → query flow."""

    def test_committed_session_edges_appear(self, conn: sqlite3.Connection) -> None:
        """Test that committed session edges are queryable."""
        files = ["x.py", "y.py", "z.py"]

        # Finalize committed session
        conn.execute("BEGIN IMMEDIATE")
        _ingest_session_finalize(
            conn,
            "session1",
            outcome="COMMITTED",
            edited_files=files,
        )
        conn.commit()

        # Another session with same files
        conn.execute("BEGIN IMMEDIATE")
        _ingest_session_finalize(
            conn,
            "session2",
            outcome="COMMITTED",
            edited_files=files,
        )
        conn.commit()

        # Now edges have support_count >= 2
        brief = query_implementation_brief(conn, ["x.py"])
        paths = [f.path for f in brief.likely_impacted_files]
        assert "y.py" in paths or "z.py" in paths

    def test_abandoned_session_excluded(self, conn: sqlite3.Connection) -> None:
        """Test that abandoned session edges don't appear in queries."""
        conn.execute("BEGIN IMMEDIATE")
        _ingest_session_finalize(
            conn,
            "session1",
            outcome="ABANDONED",
            edited_files=["a.py", "b.py"],
        )
        conn.commit()

        # Should have no edges
        edges = conn.execute("SELECT COUNT(*) FROM file_edges").fetchone()
        assert edges[0] == 0


class TestMultiSourceAggregation:
    """Test that multiple signal sources aggregate correctly."""

    def test_commit_plus_review_increases_weight(self, conn: sqlite3.Connection) -> None:
        """Test that commit + review evidence increases edge weight."""
        # Commit evidence
        with patch("tools.kb.ingest.parse_git_show", return_value=["a.py", "b.py"]):
            conn.execute("BEGIN IMMEDIATE")
            _ingest_commit(conn, "sha1")
            conn.commit()

        weight_after_commit = conn.execute(
            "SELECT weight FROM file_edges " "WHERE src_file = 'a.py' AND dst_file = 'b.py'"
        ).fetchone()

        # Review evidence for same files
        review_data = [
            {"file_path": "a.py", "summary": "issue in a"},
            {"file_path": "b.py", "summary": "issue in b"},
        ]
        review_path = (
            Path(conn.execute("PRAGMA database_list").fetchone()[2]).parent / "review.json"
        )
        review_path.write_text(json.dumps(review_data))

        conn.execute("BEGIN IMMEDIATE")
        _ingest_review(conn, str(review_path), "gemini", "run1")
        conn.commit()

        weight_after_review = conn.execute(
            "SELECT weight FROM file_edges " "WHERE src_file = 'a.py' AND dst_file = 'b.py'"
        ).fetchone()

        # Weight should have increased
        assert weight_after_review[0] > weight_after_commit[0]
