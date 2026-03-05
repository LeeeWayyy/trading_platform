"""Unit tests for tools.kb.ingest — all 7 ingest subcommands."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.kb.db import get_connection, init_schema
from tools.kb.ingest import (
    MAX_FILES_FOR_COMMIT,
    _evidence_id,
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
from tools.kb.ingest_cli import main

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    """Return a connection with schema initialized."""
    c = get_connection(tmp_path / "test.db")
    init_schema(c)
    return c


class TestNowIso:
    """Tests for _now_iso timestamp format."""

    def test_z_suffix_format(self) -> None:
        """Test that _now_iso produces Z-suffix timestamps for consistent ordering."""
        ts = _now_iso()
        assert ts.endswith("Z"), f"Expected Z suffix, got: {ts}"
        assert "+00:00" not in ts, f"Should not contain +00:00: {ts}"

    def test_includes_microseconds(self) -> None:
        """Test that _now_iso includes microsecond precision for sub-second ordering."""
        ts = _now_iso()
        # Format: 2024-01-01T00:00:00.123456Z — the dot indicates microseconds
        assert "." in ts, f"Expected microsecond precision, got: {ts}"

    def test_sortable_with_git_commit_date(self) -> None:
        """Test that _now_iso timestamps sort correctly against git commit timestamps."""
        # Both _now_iso and parse_git_commit_date now use microsecond format
        git_ts = "2024-01-01T00:00:00.000000Z"
        now_ts = _now_iso()
        # Both share the same fixed-width format so lexicographic ordering works
        assert now_ts > git_ts  # Current time is always after 2024-01-01


class TestEvidenceTimestampStability:
    """Tests for evidence observed_at stability on re-ingest."""

    def test_observed_at_not_updated_on_reingest(self, conn: sqlite3.Connection) -> None:
        """Test that re-ingesting same commit doesn't update observed_at."""
        import time

        with patch("tools.kb.ingest.parse_git_show", return_value=["a.py", "b.py"]):
            conn.execute("BEGIN IMMEDIATE")
            _ingest_commit(conn, "abc123")
            conn.commit()

        original_ts = conn.execute(
            "SELECT observed_at FROM edge_evidence LIMIT 1"
        ).fetchone()[0]

        time.sleep(0.01)  # Ensure different timestamp on re-ingest

        with patch("tools.kb.ingest.parse_git_show", return_value=["a.py", "b.py"]):
            conn.execute("BEGIN IMMEDIATE")
            _ingest_commit(conn, "abc123")
            conn.commit()

        new_ts = conn.execute(
            "SELECT observed_at FROM edge_evidence LIMIT 1"
        ).fetchone()[0]

        # observed_at should remain the original value, not be updated
        assert new_ts == original_ts


class TestEvidenceId:
    """Tests for evidence ID computation."""

    def test_deterministic(self) -> None:
        """Test same inputs produce same ID."""
        id1 = _evidence_id("a.py", "b.py", "CO_CHANGE", "COMMIT", "sha1")
        id2 = _evidence_id("a.py", "b.py", "CO_CHANGE", "COMMIT", "sha1")
        assert id1 == id2

    def test_different_inputs_different_ids(self) -> None:
        """Test different inputs produce different IDs."""
        id1 = _evidence_id("a.py", "b.py", "CO_CHANGE", "COMMIT", "sha1")
        id2 = _evidence_id("a.py", "c.py", "CO_CHANGE", "COMMIT", "sha1")
        assert id1 != id2

    def test_length(self) -> None:
        """Test ID is 16 characters."""
        eid = _evidence_id("a.py", "b.py", "CO_CHANGE", "COMMIT", "sha1")
        assert len(eid) == 16


class TestIngestReview:
    """Tests for review ingest subcommand."""

    def test_ingests_findings(self, conn: sqlite3.Connection) -> None:
        """Test that findings are inserted into DB."""
        conn.execute("BEGIN IMMEDIATE")
        count = _ingest_review(conn, str(FIXTURES_DIR / "sample_review.json"), "gemini", "run1")
        conn.commit()
        assert count == 3

        findings = conn.execute("SELECT COUNT(*) FROM findings").fetchone()
        assert findings[0] == 3

    def test_creates_review_run(self, conn: sqlite3.Connection) -> None:
        """Test that review_run record is created."""
        conn.execute("BEGIN IMMEDIATE")
        _ingest_review(conn, str(FIXTURES_DIR / "sample_review.json"), "gemini", "run1")
        conn.commit()

        run = conn.execute("SELECT * FROM review_runs WHERE run_id = 'run1'").fetchone()
        assert run is not None
        assert run["reviewer"] == "gemini"

    def test_creates_co_change_edges(self, conn: sqlite3.Connection) -> None:
        """Test that CO_CHANGE edges are created between finding files."""
        conn.execute("BEGIN IMMEDIATE")
        _ingest_review(conn, str(FIXTURES_DIR / "sample_review.json"), "gemini", "run1")
        conn.commit()

        edges = conn.execute("SELECT COUNT(*) FROM file_edges").fetchone()
        assert edges[0] > 0

    def test_classifies_issue_patterns(self, conn: sqlite3.Connection) -> None:
        """Test that issue patterns are upserted."""
        conn.execute("BEGIN IMMEDIATE")
        _ingest_review(conn, str(FIXTURES_DIR / "sample_review.json"), "gemini", "run1")
        conn.commit()

        patterns = conn.execute("SELECT COUNT(*) FROM issue_patterns").fetchone()
        assert patterns[0] > 0

    def test_idempotent(self, conn: sqlite3.Connection) -> None:
        """Test that ingesting same artifact twice doesn't duplicate findings."""
        conn.execute("BEGIN IMMEDIATE")
        _ingest_review(conn, str(FIXTURES_DIR / "sample_review.json"), "gemini", "run1")
        conn.commit()

        conn.execute("BEGIN IMMEDIATE")
        _ingest_review(conn, str(FIXTURES_DIR / "sample_review.json"), "gemini", "run1")
        conn.commit()

        findings = conn.execute("SELECT COUNT(*) FROM findings").fetchone()
        assert findings[0] == 3  # Not duplicated

    def test_idempotent_issue_patterns(self, conn: sqlite3.Connection) -> None:
        """Test that re-ingesting same review doesn't inflate issue_patterns count."""
        conn.execute("BEGIN IMMEDIATE")
        _ingest_review(conn, str(FIXTURES_DIR / "sample_review.json"), "gemini", "run1")
        conn.commit()

        # Record counts after first ingest
        patterns = conn.execute(
            "SELECT rule_id, count FROM issue_patterns ORDER BY rule_id"
        ).fetchall()
        first_counts = {row["rule_id"]: row["count"] for row in patterns}

        # Re-ingest same artifact
        conn.execute("BEGIN IMMEDIATE")
        _ingest_review(conn, str(FIXTURES_DIR / "sample_review.json"), "gemini", "run1")
        conn.commit()

        # Counts should not have changed
        patterns = conn.execute(
            "SELECT rule_id, count FROM issue_patterns ORDER BY rule_id"
        ).fetchall()
        second_counts = {row["rule_id"]: row["count"] for row in patterns}
        assert first_counts == second_counts

    def test_distinct_run_ids_for_same_content_at_different_times(
        self, conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Test that re-reviewing with identical content at different times gets distinct run_ids."""
        import os

        data = [{"file_path": "a.py", "summary": "datetime.now() used"}]
        artifact = tmp_path / "review.json"
        artifact.write_text(json.dumps(data))

        # First ingest
        conn.execute("BEGIN IMMEDIATE")
        count1 = _ingest_review(conn, str(artifact), "gemini")
        conn.commit()
        assert count1 == 1

        # Touch the file to change mtime
        os.utime(artifact, (artifact.stat().st_atime + 10, artifact.stat().st_mtime + 10))

        # Second ingest — same content but different mtime should get a different run_id
        conn.execute("BEGIN IMMEDIATE")
        count2 = _ingest_review(conn, str(artifact), "gemini")
        conn.commit()
        assert count2 == 1

        # Two distinct runs should exist
        runs = conn.execute("SELECT COUNT(*) FROM review_runs").fetchone()
        assert runs[0] == 2

    def test_accumulates_examples_across_runs(
        self, conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Test that issue pattern examples accumulate across different runs."""
        # First review with one datetime.now() finding
        data1 = [
            {"file_path": "a.py", "line": 10, "severity": "HIGH", "summary": "datetime.now() used"}
        ]
        artifact1 = tmp_path / "review1.json"
        artifact1.write_text(json.dumps(data1))
        conn.execute("BEGIN IMMEDIATE")
        _ingest_review(conn, str(artifact1), "gemini", "run1")
        conn.commit()

        # Second review with a different file but same rule_id
        data2 = [
            {"file_path": "b.py", "line": 20, "severity": "HIGH", "summary": "datetime.now() used"}
        ]
        artifact2 = tmp_path / "review2.json"
        artifact2.write_text(json.dumps(data2))
        conn.execute("BEGIN IMMEDIATE")
        _ingest_review(conn, str(artifact2), "gemini", "run2")
        conn.commit()

        # Both examples should be in examples_json
        row = conn.execute(
            "SELECT examples_json FROM issue_patterns WHERE rule_id = 'UTC_NAIVE_DATETIME'"
        ).fetchone()
        examples = json.loads(row["examples_json"])
        assert "a.py:10" in examples
        assert "b.py:20" in examples

    def test_deduplicates_pattern_count_per_run(
        self, conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Test that multiple findings with same rule_id in one run increment count only once."""
        data = [
            {"file_path": "a.py", "line": 10, "summary": "datetime.now() used"},
            {"file_path": "b.py", "line": 20, "summary": "datetime.now() called"},
            {"file_path": "c.py", "line": 30, "summary": "datetime.now() found"},
        ]
        artifact = tmp_path / "review.json"
        artifact.write_text(json.dumps(data))
        conn.execute("BEGIN IMMEDIATE")
        _ingest_review(conn, str(artifact), "gemini", "run1")
        conn.commit()

        # All 3 findings share rule_id UTC_NAIVE_DATETIME but count should be 1 (one run)
        rows = conn.execute(
            "SELECT count FROM issue_patterns WHERE rule_id = 'UTC_NAIVE_DATETIME'"
        ).fetchall()
        _total_count = sum(row["count"] for row in rows)
        # With multiple scopes, each scope gets count=1, but no scope gets count>1
        for row in rows:
            assert row["count"] == 1, f"Expected count=1 per scope per run, got {row['count']}"

    def test_empty_artifact(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        """Test handling of empty artifact."""
        conn.execute("BEGIN IMMEDIATE")
        count = _ingest_review(conn, str(tmp_path / "nonexistent.json"), "gemini")
        conn.commit()
        assert count == 0


class TestIngestCommit:
    """Tests for commit ingest subcommand."""

    def test_creates_edges(self, conn: sqlite3.Connection) -> None:
        """Test that CO_CHANGE edges are created for commit files."""
        with patch("tools.kb.ingest.parse_git_show", return_value=["a.py", "b.py", "c.py"]):
            conn.execute("BEGIN IMMEDIATE")
            count = _ingest_commit(conn, "abc123")
            conn.commit()
        # 3 files → C(3,2) = 3 edges
        assert count == 3

    def test_filters_small_commits(self, conn: sqlite3.Connection) -> None:
        """Test that commits with < MIN files are skipped."""
        with patch("tools.kb.ingest.parse_git_show", return_value=["a.py"]):
            conn.execute("BEGIN IMMEDIATE")
            count = _ingest_commit(conn, "abc123")
            conn.commit()
        assert count == 0

    def test_filters_large_commits(self, conn: sqlite3.Connection) -> None:
        """Test that commits with > MAX files are skipped."""
        files = [f"file{i}.py" for i in range(MAX_FILES_FOR_COMMIT + 1)]
        with patch("tools.kb.ingest.parse_git_show", return_value=files):
            conn.execute("BEGIN IMMEDIATE")
            count = _ingest_commit(conn, "abc123")
            conn.commit()
        assert count == 0

    def test_idempotent_edges(self, conn: sqlite3.Connection) -> None:
        """Test that ingesting same commit twice doesn't duplicate evidence."""
        with patch("tools.kb.ingest.parse_git_show", return_value=["a.py", "b.py"]):
            conn.execute("BEGIN IMMEDIATE")
            _ingest_commit(conn, "abc123")
            conn.commit()

            conn.execute("BEGIN IMMEDIATE")
            _ingest_commit(conn, "abc123")
            conn.commit()

        evidence = conn.execute("SELECT COUNT(*) FROM edge_evidence").fetchone()
        assert evidence[0] == 1  # Not duplicated

    def test_resolves_symbolic_sha(self, conn: sqlite3.Connection) -> None:
        """Test that symbolic refs like HEAD are resolved to concrete SHAs."""
        concrete_sha = "abc123def456"
        with (
            patch("tools.kb.ingest._resolve_sha", return_value=concrete_sha) as mock_resolve,
            patch("tools.kb.ingest.parse_git_show", return_value=["a.py", "b.py"]),
        ):
            conn.execute("BEGIN IMMEDIATE")
            _ingest_commit(conn, "HEAD")
            conn.commit()

        mock_resolve.assert_called_once_with("HEAD")
        evidence = conn.execute("SELECT source_id FROM edge_evidence").fetchone()
        assert evidence[0] == concrete_sha

    def test_skips_merge_commits(self, conn: sqlite3.Connection) -> None:
        """Test that merge commits are skipped."""
        with patch("tools.kb.ingest._is_merge_commit", return_value=True):
            conn.execute("BEGIN IMMEDIATE")
            count = _ingest_commit(conn, "merge_sha", files=["a.py", "b.py", "c.py"])
            conn.commit()
        assert count == 0
        edges = conn.execute("SELECT COUNT(*) FROM file_edges").fetchone()
        assert edges[0] == 0

    def test_skip_git_checks_bypasses_resolve_and_merge(
        self, conn: sqlite3.Connection
    ) -> None:
        """Test that _skip_git_checks=True skips _resolve_sha and _is_merge_commit."""
        with (
            patch("tools.kb.ingest._resolve_sha") as mock_resolve,
            patch("tools.kb.ingest._is_merge_commit") as mock_merge,
            patch("tools.kb.ingest.parse_git_show", return_value=["a.py", "b.py"]),
        ):
            conn.execute("BEGIN IMMEDIATE")
            count = _ingest_commit(conn, "abc123", _skip_git_checks=True)
            conn.commit()
        mock_resolve.assert_not_called()
        mock_merge.assert_not_called()
        assert count == 1  # 2 files → C(2,2) = 1 edge


class TestResolveSha:
    """Tests for SHA resolution."""

    def test_resolves_head(self) -> None:
        """Test that symbolic ref is resolved via git rev-parse."""
        with patch("tools.kb.ingest.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "abc123def456\n"
            mock_run.return_value.returncode = 0
            result = _resolve_sha("HEAD")
        assert result == "abc123def456"

    def test_returns_original_on_failure(self) -> None:
        """Test that failure returns the original SHA string."""
        import subprocess

        with patch(
            "tools.kb.ingest.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            result = _resolve_sha("HEAD")
        assert result == "HEAD"


class TestIngestBackfill:
    """Tests for backfill subcommand."""

    def test_processes_multiple_commits(self, conn: sqlite3.Connection) -> None:
        """Test that backfill processes multiple commits with pre-fetched files."""
        mock_commits = [
            ("sha1", ["a.py", "b.py"]),
            ("sha2", ["c.py", "d.py", "e.py"]),
        ]
        with patch("tools.kb.ingest.parse_git_log_range", return_value=mock_commits):
            conn.execute("BEGIN IMMEDIATE")
            count = _ingest_backfill(conn, "6 months ago")
            conn.commit()
        # sha1: C(2,2)=1, sha2: C(3,2)=3
        assert count == 4


class TestIngestTest:
    """Tests for test ingest subcommand."""

    def test_ingests_results(self, conn: sqlite3.Connection) -> None:
        """Test that test results are inserted."""
        conn.execute("BEGIN IMMEDIATE")
        count = _ingest_test(
            conn,
            str(FIXTURES_DIR / "sample_junit.xml"),
            ["apps/signal_service/main.py"],
        )
        conn.commit()
        assert count == 4

    def test_creates_test_edges_for_failures(self, conn: sqlite3.Connection) -> None:
        """Test that TESTS edges are created for failed tests."""
        conn.execute("BEGIN IMMEDIATE")
        _ingest_test(
            conn,
            str(FIXTURES_DIR / "sample_junit.xml"),
            ["apps/signal_service/main.py"],
        )
        conn.commit()

        edges = conn.execute("SELECT COUNT(*) FROM file_edges WHERE relation = 'TESTS'").fetchone()
        assert edges[0] >= 1

    def test_creates_test_run(self, conn: sqlite3.Connection) -> None:
        """Test that test_run record is created."""
        conn.execute("BEGIN IMMEDIATE")
        _ingest_test(
            conn,
            str(FIXTURES_DIR / "sample_junit.xml"),
            ["apps/signal_service/main.py"],
        )
        conn.commit()

        runs = conn.execute("SELECT COUNT(*) FROM test_runs").fetchone()
        assert runs[0] == 1

    def test_auto_creates_session_for_fk(self, conn: sqlite3.Connection) -> None:
        """Test that session_id auto-creates implementation_sessions record if missing."""
        conn.execute("BEGIN IMMEDIATE")
        _ingest_test(
            conn,
            str(FIXTURES_DIR / "sample_junit.xml"),
            ["apps/signal_service/main.py"],
            session_id="new_session",
        )
        conn.commit()

        session = conn.execute(
            "SELECT * FROM implementation_sessions WHERE session_id = 'new_session'"
        ).fetchone()
        assert session is not None
        assert session["outcome"] == "WIP"
        assert session["ended_at"] is None


class TestIngestTestFilePath:
    """Tests for test file path derivation from JUnit classnames."""

    def test_class_based_classname(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        """Test that class-based JUnit classnames produce correct file paths."""
        # Create a JUnit XML with a class-based test
        xml_content = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="1" failures="1">
    <testcase classname="tests.test_orders.TestRisk" name="test_limit" time="0.1">
        <failure message="AssertionError">assert False</failure>
    </testcase>
</testsuite>"""
        xml_file = tmp_path / "junit.xml"
        xml_file.write_text(xml_content)

        conn.execute("BEGIN IMMEDIATE")
        _ingest_test(conn, str(xml_file), ["apps/orders.py"])
        conn.commit()

        # The TESTS edge src_file should be tests/test_orders.py, NOT tests/test_orders/TestRisk.py
        edges = conn.execute("SELECT src_file FROM file_edges WHERE relation = 'TESTS'").fetchall()
        assert len(edges) == 1
        assert edges[0]["src_file"] == "tests/test_orders.py"

    def test_path_style_nodeid(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        """Test that path-style nodeids (with /) are used directly without dot-splitting."""
        xml_content = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="1" failures="1">
    <testcase classname="tests/test_orders.py" name="test_limit" time="0.1">
        <failure message="AssertionError">assert False</failure>
    </testcase>
</testsuite>"""
        xml_file = tmp_path / "junit.xml"
        xml_file.write_text(xml_content)

        conn.execute("BEGIN IMMEDIATE")
        _ingest_test(conn, str(xml_file), ["apps/orders.py"])
        conn.commit()

        edges = conn.execute("SELECT src_file FROM file_edges WHERE relation = 'TESTS'").fetchall()
        assert len(edges) == 1
        # Should be "tests/test_orders.py", NOT "tests/test_orders/py.py"
        assert edges[0]["src_file"] == "tests/test_orders.py"


class TestIngestErrorFix:
    """Tests for error-fix ingest subcommand."""

    def test_links_failing_to_passing(self, conn: sqlite3.Connection) -> None:
        """Test that error fixes are linked between failing and passing runs."""
        # Setup: create session, failing run, passing run
        conn.execute(
            "INSERT INTO implementation_sessions "
            "(session_id, started_at, outcome) VALUES ('s1', '2024-01-01T00:00:00Z', 'COMMITTED')"
        )
        conn.execute(
            "INSERT INTO test_runs "
            "(run_id, session_id, command, status, started_at, finished_at, changed_files_json) "
            "VALUES ('fail1', 's1', 'pytest', 'FAIL', '2024-01-01T00:00:00Z', "
            "'2024-01-01T00:01:00Z', '[\"a.py\"]')"
        )
        conn.execute(
            "INSERT INTO test_results "
            "(run_id, test_nodeid, status, error_signature) "
            "VALUES ('fail1', 'test_foo', 'FAIL', 'sig123')"
        )
        conn.execute(
            "INSERT INTO test_runs "
            "(run_id, session_id, command, status, started_at, finished_at, changed_files_json) "
            "VALUES ('pass1', 's1', 'pytest', 'PASS', '2024-01-01T00:02:00Z', "
            "'2024-01-01T00:03:00Z', '[\"a.py\", \"b.py\"]')"
        )
        conn.execute(
            "INSERT INTO test_results "
            "(run_id, test_nodeid, status) VALUES ('pass1', 'test_foo', 'PASS')"
        )
        conn.commit()

        conn.execute("BEGIN IMMEDIATE")
        count = _ingest_error_fix(conn, "s1")
        conn.commit()
        assert count >= 1

        fixes = conn.execute("SELECT COUNT(*) FROM error_fixes").fetchone()
        assert fixes[0] >= 1

    def test_records_fix_when_same_files_changed(self, conn: sqlite3.Connection) -> None:
        """Test that fix is recorded using common files when failing/passing have same file set."""
        conn.execute(
            "INSERT INTO implementation_sessions "
            "(session_id, started_at, outcome) VALUES ('s1', '2024-01-01T00:00:00Z', 'COMMITTED')"
        )
        conn.execute(
            "INSERT INTO test_runs "
            "(run_id, session_id, command, status, started_at, finished_at, changed_files_json) "
            "VALUES ('fail1', 's1', 'pytest', 'FAIL', '2024-01-01T00:00:00Z', "
            "'2024-01-01T00:01:00Z', '[\"a.py\"]')"
        )
        conn.execute(
            "INSERT INTO test_results "
            "(run_id, test_nodeid, status, error_signature) "
            "VALUES ('fail1', 'test_foo', 'FAIL', 'sig123')"
        )
        conn.execute(
            "INSERT INTO test_runs "
            "(run_id, session_id, command, status, started_at, finished_at, changed_files_json) "
            "VALUES ('pass1', 's1', 'pytest', 'PASS', '2024-01-01T00:02:00Z', "
            "'2024-01-01T00:03:00Z', '[\"a.py\"]')"
        )
        conn.execute(
            "INSERT INTO test_results "
            "(run_id, test_nodeid, status) VALUES ('pass1', 'test_foo', 'PASS')"
        )
        conn.commit()

        conn.execute("BEGIN IMMEDIATE")
        count = _ingest_error_fix(conn, "s1")
        conn.commit()

        # Same file set → fix recorded using intersection (common files)
        assert count >= 1
        fix = conn.execute("SELECT fixed_files_json FROM error_fixes LIMIT 1").fetchone()
        assert "a.py" in json.loads(fix["fixed_files_json"])

    def test_skips_fix_with_no_file_overlap(self, conn: sqlite3.Connection) -> None:
        """Test that no fix is created when failing and passing runs have no file overlap."""
        conn.execute(
            "INSERT INTO implementation_sessions "
            "(session_id, started_at, outcome) VALUES ('s1', '2024-01-01T00:00:00Z', 'COMMITTED')"
        )
        conn.execute(
            "INSERT INTO test_runs "
            "(run_id, session_id, command, status, started_at, finished_at, changed_files_json) "
            "VALUES ('fail1', 's1', 'pytest', 'FAIL', '2024-01-01T00:00:00Z', "
            "'2024-01-01T00:01:00Z', '[]')"
        )
        conn.execute(
            "INSERT INTO test_results "
            "(run_id, test_nodeid, status, error_signature) "
            "VALUES ('fail1', 'test_foo', 'FAIL', 'sig123')"
        )
        conn.execute(
            "INSERT INTO test_runs "
            "(run_id, session_id, command, status, started_at, finished_at, changed_files_json) "
            "VALUES ('pass1', 's1', 'pytest', 'PASS', '2024-01-01T00:02:00Z', "
            "'2024-01-01T00:03:00Z', '[]')"
        )
        conn.execute(
            "INSERT INTO test_results "
            "(run_id, test_nodeid, status) VALUES ('pass1', 'test_foo', 'PASS')"
        )
        conn.commit()

        conn.execute("BEGIN IMMEDIATE")
        count = _ingest_error_fix(conn, "s1")
        conn.commit()

        # No file overlap → no fix
        assert count == 0

    def test_idempotent_on_repeated_runs(self, conn: sqlite3.Connection) -> None:
        """Test that re-running _ingest_error_fix does not create duplicate evidence."""
        conn.execute(
            "INSERT INTO implementation_sessions "
            "(session_id, started_at, outcome) VALUES ('s1', '2024-01-01T00:00:00Z', 'COMMITTED')"
        )
        conn.execute(
            "INSERT INTO test_runs "
            "(run_id, session_id, command, status, started_at, finished_at, changed_files_json) "
            "VALUES ('fail1', 's1', 'pytest', 'FAIL', '2024-01-01T00:00:00Z', "
            "'2024-01-01T00:01:00Z', '[\"x.py\"]')"
        )
        conn.execute(
            "INSERT INTO test_results "
            "(run_id, test_nodeid, status, error_signature) "
            "VALUES ('fail1', 'test_idem', 'FAIL', 'sig_idem')"
        )
        conn.execute(
            "INSERT INTO test_runs "
            "(run_id, session_id, command, status, started_at, finished_at, changed_files_json) "
            "VALUES ('pass1', 's1', 'pytest', 'PASS', '2024-01-01T00:02:00Z', "
            "'2024-01-01T00:03:00Z', '[\"x.py\", \"y.py\"]')"
        )
        conn.execute(
            "INSERT INTO test_results "
            "(run_id, test_nodeid, status) VALUES ('pass1', 'test_idem', 'PASS')"
        )
        conn.commit()

        # First run — should create fix + evidence
        conn.execute("BEGIN IMMEDIATE")
        count1 = _ingest_error_fix(conn, "s1")
        conn.commit()
        assert count1 == 1

        evidence_count_1 = conn.execute(
            "SELECT COUNT(*) FROM edge_evidence WHERE relation = 'ERROR_FIX'"
        ).fetchone()[0]
        fix_count_1 = conn.execute("SELECT COUNT(*) FROM error_fixes").fetchone()[0]
        assert fix_count_1 == 1
        assert evidence_count_1 >= 1

        # Second run — should be a no-op (idempotent)
        conn.execute("BEGIN IMMEDIATE")
        count2 = _ingest_error_fix(conn, "s1")
        conn.commit()
        assert count2 == 0

        evidence_count_2 = conn.execute(
            "SELECT COUNT(*) FROM edge_evidence WHERE relation = 'ERROR_FIX'"
        ).fetchone()[0]
        fix_count_2 = conn.execute("SELECT COUNT(*) FROM error_fixes").fetchone()[0]
        # No new evidence or fixes
        assert fix_count_2 == fix_count_1
        assert evidence_count_2 == evidence_count_1

    def test_no_fixes_for_no_failures(self, conn: sqlite3.Connection) -> None:
        """Test that no fixes are created when there are no failures."""
        conn.execute(
            "INSERT INTO implementation_sessions "
            "(session_id, started_at, outcome) VALUES ('s1', '2024-01-01T00:00:00Z', 'COMMITTED')"
        )
        conn.commit()

        conn.execute("BEGIN IMMEDIATE")
        count = _ingest_error_fix(conn, "s1")
        conn.commit()
        assert count == 0

    def test_only_first_passing_run_matched(self, conn: sqlite3.Connection) -> None:
        """Test that only the earliest passing run creates a fix, not later reruns."""
        conn.execute(
            "INSERT INTO implementation_sessions "
            "(session_id, started_at, outcome) VALUES ('s1', '2024-01-01T00:00:00Z', 'COMMITTED')"
        )
        conn.execute(
            "INSERT INTO test_runs "
            "(run_id, session_id, command, status, started_at, finished_at, changed_files_json) "
            "VALUES ('fail1', 's1', 'pytest', 'FAIL', '2024-01-01T00:00:00Z', "
            "'2024-01-01T00:01:00Z', '[\"a.py\"]')"
        )
        conn.execute(
            "INSERT INTO test_results "
            "(run_id, test_nodeid, status, error_signature) "
            "VALUES ('fail1', 'test_multi', 'FAIL', 'sig_multi')"
        )
        # Two later passing runs for the same test
        conn.execute(
            "INSERT INTO test_runs "
            "(run_id, session_id, command, status, started_at, finished_at, changed_files_json) "
            "VALUES ('pass1', 's1', 'pytest', 'PASS', '2024-01-01T00:02:00Z', "
            "'2024-01-01T00:03:00Z', '[\"a.py\", \"b.py\"]')"
        )
        conn.execute(
            "INSERT INTO test_results "
            "(run_id, test_nodeid, status) VALUES ('pass1', 'test_multi', 'PASS')"
        )
        conn.execute(
            "INSERT INTO test_runs "
            "(run_id, session_id, command, status, started_at, finished_at, changed_files_json) "
            "VALUES ('pass2', 's1', 'pytest', 'PASS', '2024-01-01T00:04:00Z', "
            "'2024-01-01T00:05:00Z', '[\"a.py\", \"b.py\"]')"
        )
        conn.execute(
            "INSERT INTO test_results "
            "(run_id, test_nodeid, status) VALUES ('pass2', 'test_multi', 'PASS')"
        )
        conn.commit()

        conn.execute("BEGIN IMMEDIATE")
        count = _ingest_error_fix(conn, "s1")
        conn.commit()

        # Only 1 fix should be created (earliest passing run), not 2
        assert count == 1
        fix_count = conn.execute("SELECT COUNT(*) FROM error_fixes").fetchone()[0]
        assert fix_count == 1

    def test_same_timestamp_pass_not_linked(self, conn: sqlite3.Connection) -> None:
        """Test that a passing run with the same timestamp as a failure is not linked as a fix."""
        conn.execute(
            "INSERT INTO implementation_sessions "
            "(session_id, started_at, outcome) VALUES ('s1', '2024-01-01T00:00:00Z', 'COMMITTED')"
        )
        conn.execute(
            "INSERT INTO test_runs "
            "(run_id, session_id, command, status, started_at, finished_at, changed_files_json) "
            "VALUES ('fail1', 's1', 'pytest', 'FAIL', '2024-01-01T00:00:00Z', "
            "'2024-01-01T00:00:01Z', '[\"a.py\"]')"
        )
        conn.execute(
            "INSERT INTO test_results "
            "(run_id, test_nodeid, status, error_signature) "
            "VALUES ('fail1', 'test_ts', 'FAIL', 'sig_ts')"
        )
        # Pass run with exact same started_at
        conn.execute(
            "INSERT INTO test_runs "
            "(run_id, session_id, command, status, started_at, finished_at, changed_files_json) "
            "VALUES ('pass1', 's1', 'pytest', 'PASS', '2024-01-01T00:00:00Z', "
            "'2024-01-01T00:00:01Z', '[\"a.py\"]')"
        )
        conn.execute(
            "INSERT INTO test_results "
            "(run_id, test_nodeid, status) VALUES ('pass1', 'test_ts', 'PASS')"
        )
        conn.commit()

        conn.execute("BEGIN IMMEDIATE")
        count = _ingest_error_fix(conn, "s1")
        conn.commit()
        # Same timestamp → not a valid fix
        assert count == 0

    def test_dedup_deferred_until_fix_created(self, conn: sqlite3.Connection) -> None:
        """Test that dedup mark is deferred so later failures can find fixes if first fails."""
        conn.execute(
            "INSERT INTO implementation_sessions "
            "(session_id, started_at, outcome) VALUES ('s1', '2024-01-01T00:00:00Z', 'COMMITTED')"
        )
        # First failure: no overlapping changed_files with its pass run
        conn.execute(
            "INSERT INTO test_runs "
            "(run_id, session_id, command, status, started_at, finished_at, changed_files_json) "
            "VALUES ('fail1', 's1', 'pytest', 'FAIL', '2024-01-01T00:00:00Z', "
            "'2024-01-01T00:00:01Z', '[\"x.py\"]')"
        )
        conn.execute(
            "INSERT INTO test_results "
            "(run_id, test_nodeid, status, error_signature) "
            "VALUES ('fail1', 'test_dedup', 'FAIL', 'sig_dedup')"
        )
        conn.execute(
            "INSERT INTO test_runs "
            "(run_id, session_id, command, status, started_at, finished_at, changed_files_json) "
            "VALUES ('pass1', 's1', 'pytest', 'PASS', '2024-01-01T00:00:02Z', "
            "'2024-01-01T00:00:03Z', '[]')"
        )
        conn.execute(
            "INSERT INTO test_results "
            "(run_id, test_nodeid, status) VALUES ('pass1', 'test_dedup', 'PASS')"
        )
        # Second failure: same error, but this time with overlapping changed_files
        conn.execute(
            "INSERT INTO test_runs "
            "(run_id, session_id, command, status, started_at, finished_at, changed_files_json) "
            "VALUES ('fail2', 's1', 'pytest', 'FAIL', '2024-01-01T00:00:04Z', "
            "'2024-01-01T00:00:05Z', '[\"a.py\"]')"
        )
        conn.execute(
            "INSERT INTO test_results "
            "(run_id, test_nodeid, status, error_signature) "
            "VALUES ('fail2', 'test_dedup', 'FAIL', 'sig_dedup')"
        )
        conn.execute(
            "INSERT INTO test_runs "
            "(run_id, session_id, command, status, started_at, finished_at, changed_files_json) "
            "VALUES ('pass2', 's1', 'pytest', 'PASS', '2024-01-01T00:00:06Z', "
            "'2024-01-01T00:00:07Z', '[\"a.py\"]')"
        )
        conn.execute(
            "INSERT INTO test_results "
            "(run_id, test_nodeid, status) VALUES ('pass2', 'test_dedup', 'PASS')"
        )
        conn.commit()

        conn.execute("BEGIN IMMEDIATE")
        count = _ingest_error_fix(conn, "s1")
        conn.commit()
        # First failure has no file overlap → skipped, but second should succeed
        assert count >= 1

    def test_skips_first_passing_run_no_overlap_uses_second(
        self, conn: sqlite3.Connection
    ) -> None:
        """Test that if earliest passing run has no file overlap, later passing run is used."""
        conn.execute(
            "INSERT INTO implementation_sessions "
            "(session_id, started_at, outcome) VALUES ('s1', '2024-01-01T00:00:00Z', 'COMMITTED')"
        )
        conn.execute(
            "INSERT INTO test_runs "
            "(run_id, session_id, command, status, started_at, finished_at, changed_files_json) "
            "VALUES ('fail1', 's1', 'pytest', 'FAIL', '2024-01-01T00:00:00Z', "
            "'2024-01-01T00:01:00Z', '[\"a.py\"]')"
        )
        conn.execute(
            "INSERT INTO test_results "
            "(run_id, test_nodeid, status, error_signature) "
            "VALUES ('fail1', 'test_skip', 'FAIL', 'sig_skip')"
        )
        # First passing run: no file overlap at all
        conn.execute(
            "INSERT INTO test_runs "
            "(run_id, session_id, command, status, started_at, finished_at, changed_files_json) "
            "VALUES ('pass1', 's1', 'pytest', 'PASS', '2024-01-01T00:02:00Z', "
            "'2024-01-01T00:03:00Z', '[]')"
        )
        conn.execute(
            "INSERT INTO test_results "
            "(run_id, test_nodeid, status) VALUES ('pass1', 'test_skip', 'PASS')"
        )
        # Second passing run: has file overlap
        conn.execute(
            "INSERT INTO test_runs "
            "(run_id, session_id, command, status, started_at, finished_at, changed_files_json) "
            "VALUES ('pass2', 's1', 'pytest', 'PASS', '2024-01-01T00:04:00Z', "
            "'2024-01-01T00:05:00Z', '[\"a.py\", \"b.py\"]')"
        )
        conn.execute(
            "INSERT INTO test_results "
            "(run_id, test_nodeid, status) VALUES ('pass2', 'test_skip', 'PASS')"
        )
        conn.commit()

        conn.execute("BEGIN IMMEDIATE")
        count = _ingest_error_fix(conn, "s1")
        conn.commit()

        # Fix should be created from second passing run (pass2), not skipped
        assert count == 1
        fix = conn.execute(
            "SELECT passing_run_id FROM error_fixes LIMIT 1"
        ).fetchone()
        assert fix["passing_run_id"] == "pass2"

    def test_idempotent_marks_seen_even_when_fix_exists(
        self, conn: sqlite3.Connection
    ) -> None:
        """Test that rerun marks sig_key as seen even when fix already exists."""
        conn.execute(
            "INSERT INTO implementation_sessions "
            "(session_id, started_at, outcome) VALUES ('s1', '2024-01-01T00:00:00Z', 'COMMITTED')"
        )
        # Two separate failure rows with the same (error_signature, test_nodeid)
        conn.execute(
            "INSERT INTO test_runs "
            "(run_id, session_id, command, status, started_at, finished_at, changed_files_json) "
            "VALUES ('fail1', 's1', 'pytest', 'FAIL', '2024-01-01T00:00:00Z', "
            "'2024-01-01T00:01:00Z', '[\"a.py\"]')"
        )
        conn.execute(
            "INSERT INTO test_results "
            "(run_id, test_nodeid, status, error_signature) "
            "VALUES ('fail1', 'test_seen', 'FAIL', 'sig_seen')"
        )
        conn.execute(
            "INSERT INTO test_runs "
            "(run_id, session_id, command, status, started_at, finished_at, changed_files_json) "
            "VALUES ('fail2', 's1', 'pytest', 'FAIL', '2024-01-01T00:01:00Z', "
            "'2024-01-01T00:01:30Z', '[\"a.py\"]')"
        )
        conn.execute(
            "INSERT INTO test_results "
            "(run_id, test_nodeid, status, error_signature) "
            "VALUES ('fail2', 'test_seen', 'FAIL', 'sig_seen')"
        )
        conn.execute(
            "INSERT INTO test_runs "
            "(run_id, session_id, command, status, started_at, finished_at, changed_files_json) "
            "VALUES ('pass1', 's1', 'pytest', 'PASS', '2024-01-01T00:02:00Z', "
            "'2024-01-01T00:03:00Z', '[\"a.py\", \"b.py\"]')"
        )
        conn.execute(
            "INSERT INTO test_results "
            "(run_id, test_nodeid, status) VALUES ('pass1', 'test_seen', 'PASS')"
        )
        conn.commit()

        # First run: creates 1 fix (from fail1), skips fail2 (same sig_key)
        conn.execute("BEGIN IMMEDIATE")
        count1 = _ingest_error_fix(conn, "s1")
        conn.commit()
        assert count1 == 1

        # Second run: should create 0 fixes (sig_key already seen → fix already exists)
        conn.execute("BEGIN IMMEDIATE")
        count2 = _ingest_error_fix(conn, "s1")
        conn.commit()
        assert count2 == 0

        # Total: exactly 1 fix, no inflation
        fix_count = conn.execute("SELECT COUNT(*) FROM error_fixes").fetchone()[0]
        assert fix_count == 1


class TestIngestAnalyze:
    """Tests for analyze ingest subcommand."""

    def test_ingests_analyze_artifact(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        """Test that analyze output is ingested."""
        artifact = tmp_path / "analyze.json"
        artifact.write_text(
            json.dumps(
                {
                    "impacted_files": [
                        {"path": "a.py"},
                        {"path": "b.py"},
                        {"path": "c.py"},
                    ]
                }
            )
        )

        conn.execute("BEGIN IMMEDIATE")
        count = _ingest_analyze(conn, str(artifact))
        conn.commit()
        # C(3,2) = 3 edges
        assert count == 3

    def test_deduplicates_file_paths(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        """Test that duplicate file paths in analyze artifact don't create self-edges."""
        artifact = tmp_path / "analyze.json"
        artifact.write_text(
            json.dumps(
                {
                    "impacted_files": [
                        {"path": "a.py"},
                        {"path": "a.py"},
                        {"path": "b.py"},
                    ]
                }
            )
        )

        conn.execute("BEGIN IMMEDIATE")
        count = _ingest_analyze(conn, str(artifact))
        conn.commit()
        # Only C(2,2) = 1 unique edge (a.py, b.py), not 3 with self-edge
        assert count == 1
        # No self-edges
        self_edges = conn.execute(
            "SELECT COUNT(*) FROM file_edges WHERE src_file = dst_file"
        ).fetchone()
        assert self_edges[0] == 0

    def test_string_list_file_entries(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        """Test that analyze handles plain string file entries (not just dicts)."""
        artifact = tmp_path / "analyze.json"
        artifact.write_text(json.dumps({"files": ["a.py", "b.py", "c.py"]}))

        conn.execute("BEGIN IMMEDIATE")
        count = _ingest_analyze(conn, str(artifact))
        conn.commit()
        # C(3,2) = 3 edges
        assert count == 3

    def test_missing_artifact(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        """Test handling of missing artifact."""
        conn.execute("BEGIN IMMEDIATE")
        count = _ingest_analyze(conn, str(tmp_path / "nonexistent.json"))
        conn.commit()
        assert count == 0

    def test_non_json_artifact_returns_zero(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        """Test that non-JSON analyze artifacts are handled gracefully (not crash)."""
        artifact = tmp_path / "analyze.md"
        artifact.write_text("# This is a markdown report, not JSON\nSome analysis text.")
        conn.execute("BEGIN IMMEDIATE")
        count = _ingest_analyze(conn, str(artifact))
        conn.commit()
        assert count == 0

    def test_array_json_artifact_returns_zero(
        self, conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Test that a JSON array (not object) artifact returns 0 without crash."""
        artifact = tmp_path / "analyze.json"
        artifact.write_text(json.dumps(["a.py", "b.py"]))
        conn.execute("BEGIN IMMEDIATE")
        count = _ingest_analyze(conn, str(artifact))
        conn.commit()
        assert count == 0

    def test_non_string_path_in_dict_skipped(
        self, conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Test that non-string path values in analyze dicts are safely skipped."""
        artifact = tmp_path / "analyze.json"
        artifact.write_text(
            json.dumps(
                {
                    "impacted_files": [
                        {"path": {"nested": "object"}},
                        {"path": 12345},
                        {"path": "valid.py"},
                    ]
                }
            )
        )
        conn.execute("BEGIN IMMEDIATE")
        count = _ingest_analyze(conn, str(artifact))
        conn.commit()
        # Only "valid.py" is a valid path, but need >=2 files for edges
        assert count == 0


class TestIngestSessionFinalize:
    """Tests for session-finalize subcommand."""

    def test_committed_session_creates_edges(self, conn: sqlite3.Connection) -> None:
        """Test that COMMITTED sessions create CO_CHANGE edges."""
        conn.execute("BEGIN IMMEDIATE")
        count = _ingest_session_finalize(
            conn,
            "s1",
            outcome="COMMITTED",
            edited_files=["a.py", "b.py", "c.py"],
        )
        conn.commit()
        assert count == 3  # C(3,2) = 3

    def test_abandoned_session_no_edges(self, conn: sqlite3.Connection) -> None:
        """Test that ABANDONED sessions don't create edges."""
        conn.execute("BEGIN IMMEDIATE")
        count = _ingest_session_finalize(
            conn,
            "s1",
            outcome="ABANDONED",
            edited_files=["a.py", "b.py"],
        )
        conn.commit()
        assert count == 0

    def test_creates_session_record(self, conn: sqlite3.Connection) -> None:
        """Test that session record is upserted."""
        conn.execute("BEGIN IMMEDIATE")
        _ingest_session_finalize(conn, "s1", outcome="COMMITTED")
        conn.commit()

        session = conn.execute(
            "SELECT * FROM implementation_sessions WHERE session_id = 's1'"
        ).fetchone()
        assert session is not None
        assert session["outcome"] == "COMMITTED"

    def test_preserves_branch_base_sha_on_finalize(self, conn: sqlite3.Connection) -> None:
        """Test that branch and base_sha are preserved when finalizing a pre-created session."""
        # Pre-create session (as _ingest_test does)
        conn.execute(
            "INSERT INTO implementation_sessions "
            "(session_id, started_at, outcome) VALUES ('s1', '2024-01-01T00:00:00Z', 'IN_PROGRESS')"
        )
        conn.commit()

        # Finalize with branch and base_sha
        conn.execute("BEGIN IMMEDIATE")
        _ingest_session_finalize(
            conn,
            "s1",
            outcome="COMMITTED",
            branch="feature/test",
            base_sha="base123",
            head_sha="head456",
        )
        conn.commit()

        session = conn.execute(
            "SELECT branch, base_sha, head_sha, outcome FROM implementation_sessions "
            "WHERE session_id = 's1'"
        ).fetchone()
        assert session["branch"] == "feature/test"
        assert session["base_sha"] == "base123"
        assert session["head_sha"] == "head456"
        assert session["outcome"] == "COMMITTED"

    def test_invalid_outcome(self, conn: sqlite3.Connection) -> None:
        """Test that invalid outcome returns 0."""
        conn.execute("BEGIN IMMEDIATE")
        count = _ingest_session_finalize(conn, "s1", outcome="INVALID")
        conn.commit()
        assert count == 0

    def test_deduplicates_edited_files(self, conn: sqlite3.Connection) -> None:
        """Test that duplicate edited files don't create self-edges."""
        conn.execute("BEGIN IMMEDIATE")
        _ingest_session_finalize(
            conn,
            "s1",
            outcome="COMMITTED",
            edited_files=["a.py", "a.py", "b.py"],
        )
        conn.commit()
        # Should have 1 edge (a.py->b.py), no self-edge (a.py->a.py)
        rows = conn.execute(
            "SELECT src_file, dst_file FROM file_edges ORDER BY src_file, dst_file"
        ).fetchall()
        pairs = [(r["src_file"], r["dst_file"]) for r in rows]
        assert ("a.py", "b.py") in pairs
        # No self-edge
        assert ("a.py", "a.py") not in pairs


class TestCLI:
    """Tests for CLI main entry point."""

    def test_review_cli(self, tmp_path: Path) -> None:
        """Test review subcommand via CLI."""
        db_path = str(tmp_path / "test.db")
        main(
            [
                "--db",
                db_path,
                "review",
                "--artifact",
                str(FIXTURES_DIR / "sample_review.json"),
                "--reviewer",
                "gemini",
            ]
        )
        conn = get_connection(db_path)
        findings = conn.execute("SELECT COUNT(*) FROM findings").fetchone()
        assert findings[0] == 3
        conn.close()

    def test_test_cli_newline_delimited_files(self, tmp_path: Path) -> None:
        """Test that --changed-files accepts newline-delimited input (git diff output)."""
        db_path = str(tmp_path / "test.db")
        # Simulate git diff --name-only output with newlines
        main(
            [
                "--db",
                db_path,
                "test",
                "--junit-xml",
                str(FIXTURES_DIR / "sample_junit.xml"),
                "--changed-files",
                "a.py\nb.py\nc.py",
            ]
        )
        conn = get_connection(db_path)
        runs = conn.execute("SELECT COUNT(*) FROM test_runs").fetchone()
        assert runs[0] == 1
        conn.close()

    def test_session_finalize_cli_newline_delimited_files(self, tmp_path: Path) -> None:
        """Test that session-finalize --edited-files accepts newline-delimited input."""
        db_path = str(tmp_path / "test.db")
        main(
            [
                "--db",
                db_path,
                "session-finalize",
                "--session-id",
                "s1",
                "--outcome",
                "COMMITTED",
                "--edited-files",
                "a.py\nb.py\nc.py",
            ]
        )
        conn = get_connection(db_path)
        edges = conn.execute("SELECT COUNT(*) FROM file_edges").fetchone()
        # 3 files → 3 pairs (a-b, a-c, b-c)
        assert edges[0] == 3
        conn.close()

    def test_commit_cli(self, tmp_path: Path) -> None:
        """Test commit subcommand via CLI."""
        db_path = str(tmp_path / "test.db")
        with patch("tools.kb.ingest.parse_git_show", return_value=["a.py", "b.py"]):
            main(["--db", db_path, "commit", "--sha", "abc123"])
        conn = get_connection(db_path)
        edges = conn.execute("SELECT COUNT(*) FROM file_edges").fetchone()
        assert edges[0] == 1
        conn.close()


class TestReplayDeferred:
    """Tests for deferred queue replay."""

    def test_replays_deferred_commit(self, tmp_path: Path) -> None:
        """Test that deferred ingest entries are replayed on next run."""
        queue = tmp_path / "deferred.jsonl"
        queue.write_text(json.dumps({"func": "_ingest_commit", "args": ["abc123"]}) + "\n")
        db_path = str(tmp_path / "test.db")
        conn = get_connection(db_path)
        init_schema(conn)
        conn.close()

        with (
            patch("tools.kb.ingest.DEFERRED_QUEUE_PATH", queue),
            patch("tools.kb.ingest.parse_git_show", return_value=["a.py", "b.py"]),
            patch("tools.kb.ingest._is_merge_commit", return_value=False),
        ):
            count = replay_deferred(db_path)

        assert count == 1
        # Queue file should be removed after successful replay
        assert not queue.exists()

    def test_empty_queue_returns_zero(self, tmp_path: Path) -> None:
        """Test that missing queue file returns 0."""
        with patch("tools.kb.ingest.DEFERRED_QUEUE_PATH", tmp_path / "nonexistent.jsonl"):
            count = replay_deferred()
        assert count == 0

    def test_skips_entries_for_different_db(self, tmp_path: Path) -> None:
        """Test that deferred entries scoped to a different DB are preserved, not replayed."""
        queue = tmp_path / "deferred.jsonl"
        other_db = str((tmp_path / "other.db").resolve())
        queue.write_text(
            json.dumps({"func": "_ingest_commit", "args": ["abc123"], "db": other_db}) + "\n"
        )
        db_path = str(tmp_path / "test.db")
        conn = get_connection(db_path)
        init_schema(conn)
        conn.close()

        with (
            patch("tools.kb.ingest.DEFERRED_QUEUE_PATH", queue),
            patch("tools.kb.ingest.parse_git_show", return_value=["a.py", "b.py"]),
            patch("tools.kb.ingest._is_merge_commit", return_value=False),
        ):
            count = replay_deferred(db_path)

        assert count == 0
        # Entry should remain in the queue for the correct DB to pick up
        assert queue.exists()
        remaining = queue.read_text().strip().splitlines()
        assert len(remaining) == 1
        assert json.loads(remaining[0])["db"] == other_db

    def test_skips_file_based_deferred_with_missing_artifact(self, tmp_path: Path) -> None:
        """Test that file-based deferred entries with missing artifacts are discarded."""
        queue = tmp_path / "deferred.jsonl"
        missing_artifact = str(tmp_path / "deleted_review.json")
        queue.write_text(
            json.dumps({"func": "_ingest_review", "args": [missing_artifact, "gemini", None]})
            + "\n"
        )
        db_path = str(tmp_path / "test.db")
        conn = get_connection(db_path)
        init_schema(conn)
        conn.close()

        with patch("tools.kb.ingest.DEFERRED_QUEUE_PATH", queue):
            count = replay_deferred(db_path)

        assert count == 0
        # Queue file should be cleaned up (entry discarded, not kept)
        assert not queue.exists()

    def test_replays_entries_for_matching_db(self, tmp_path: Path) -> None:
        """Test that deferred entries scoped to the same DB are replayed."""
        queue = tmp_path / "deferred.jsonl"
        db_path = str(tmp_path / "test.db")
        resolved_db = str(Path(db_path).resolve())
        queue.write_text(
            json.dumps({"func": "_ingest_commit", "args": ["abc123"], "db": resolved_db}) + "\n"
        )
        conn = get_connection(db_path)
        init_schema(conn)
        conn.close()

        with (
            patch("tools.kb.ingest.DEFERRED_QUEUE_PATH", queue),
            patch("tools.kb.ingest.parse_git_show", return_value=["a.py", "b.py"]),
            patch("tools.kb.ingest._is_merge_commit", return_value=False),
        ):
            count = replay_deferred(db_path)

        assert count == 1
        assert not queue.exists()

    def test_preserves_malformed_deferred_entries(self, tmp_path: Path) -> None:
        """Test that malformed JSON lines are preserved, not silently discarded."""
        queue = tmp_path / "deferred.jsonl"
        malformed_line = "not valid json {{{]\n"
        valid_entry = json.dumps({"func": "_ingest_commit", "args": ["abc123"]}) + "\n"
        queue.write_text(malformed_line + valid_entry)
        db_path = str(tmp_path / "test.db")
        conn = get_connection(db_path)
        init_schema(conn)
        conn.close()

        with (
            patch("tools.kb.ingest.DEFERRED_QUEUE_PATH", queue),
            patch("tools.kb.ingest.parse_git_show", return_value=["a.py", "b.py"]),
            patch("tools.kb.ingest._is_merge_commit", return_value=False),
        ):
            count = replay_deferred(db_path)

        # Valid entry replayed, malformed entry preserved
        assert count == 1
        assert queue.exists()
        remaining = queue.read_text().strip().splitlines()
        assert len(remaining) == 1
        assert remaining[0] == malformed_line.strip()

    def test_preserves_unknown_func_deferred_entries(self, tmp_path: Path) -> None:
        """Test that entries with unknown func names are preserved, not discarded."""
        queue = tmp_path / "deferred.jsonl"
        unknown_entry = json.dumps({"func": "_future_ingest_func", "args": ["x"]}) + "\n"
        valid_entry = json.dumps({"func": "_ingest_commit", "args": ["abc123"]}) + "\n"
        queue.write_text(unknown_entry + valid_entry)
        db_path = str(tmp_path / "test.db")
        conn = get_connection(db_path)
        init_schema(conn)
        conn.close()

        with (
            patch("tools.kb.ingest.DEFERRED_QUEUE_PATH", queue),
            patch("tools.kb.ingest.parse_git_show", return_value=["a.py", "b.py"]),
            patch("tools.kb.ingest._is_merge_commit", return_value=False),
        ):
            count = replay_deferred(db_path)

        # Valid entry replayed, unknown func entry preserved
        assert count == 1
        assert queue.exists()
        remaining = queue.read_text().strip().splitlines()
        assert len(remaining) == 1
        payload = json.loads(remaining[0])
        assert payload["func"] == "_future_ingest_func"

    def test_replays_deferred_test_with_preserved_timestamp(self, tmp_path: Path) -> None:
        """Test that deferred test ingests use ingested_at instead of replay time."""
        queue = tmp_path / "deferred.jsonl"
        junit_xml = tmp_path / "results.xml"
        junit_xml.write_text(
            '<?xml version="1.0"?>\n<testsuites>'
            '<testsuite name="s"><testcase classname="t" name="t1" time="0.1"/>'
            "</testsuite></testsuites>"
        )
        original_time = "2024-06-15T10:00:00.000000Z"
        queue.write_text(
            json.dumps({
                "func": "_ingest_test",
                "args": [str(junit_xml), ["a.py"], None],
                "kwargs": {"ingested_at": original_time},
            })
            + "\n"
        )
        db_path = str(tmp_path / "test.db")
        conn = get_connection(db_path)
        init_schema(conn)
        conn.close()

        with patch("tools.kb.ingest.DEFERRED_QUEUE_PATH", queue):
            count = replay_deferred(db_path)

        assert count == 1
        conn = get_connection(db_path)
        run = conn.execute("SELECT started_at FROM test_runs LIMIT 1").fetchone()
        assert run["started_at"] == original_time
        conn.close()

    def test_snapshot_cleanup_after_successful_replay(self, tmp_path: Path) -> None:
        """Test that snapshot artifacts are cleaned up after successful replay."""
        queue = tmp_path / "deferred.jsonl"
        # Create a snapshot artifact in the deferred_artifacts dir
        snapshot_dir = tmp_path / "deferred_artifacts"
        snapshot_dir.mkdir()
        snapshot_file = snapshot_dir / "abc123.xml"
        snapshot_file.write_text(
            '<?xml version="1.0"?>\n<testsuites>'
            '<testsuite name="s"><testcase classname="t" name="t1" time="0.1"/>'
            "</testsuite></testsuites>"
        )
        queue.write_text(
            json.dumps({
                "func": "_ingest_test",
                "args": [str(snapshot_file), ["a.py"], None],
                "kwargs": {"ingested_at": "2024-06-15T10:00:00.000000Z"},
            })
            + "\n"
        )
        db_path = str(tmp_path / "test.db")
        conn = get_connection(db_path)
        init_schema(conn)
        conn.close()

        with (
            patch("tools.kb.ingest.DEFERRED_QUEUE_PATH", queue),
            patch("tools.kb.ingest.DEFAULT_DB_PATH", tmp_path / "graph.db"),
        ):
            count = replay_deferred(db_path)

        assert count == 1
        # Snapshot file should be cleaned up
        assert not snapshot_file.exists()

    def test_cmd_review_snapshots_artifact_on_deferral(self, tmp_path: Path) -> None:
        """Test that cmd_review snapshots the artifact before deferring on SQLITE_BUSY."""
        review_artifact = tmp_path / "review.json"
        review_artifact.write_text(json.dumps([{"summary": "issue", "file_path": "a.py"}]))
        queue = tmp_path / "deferred.jsonl"
        db_path = str(tmp_path / "test.db")

        # Force SQLITE_BUSY on BEGIN IMMEDIATE
        with (
            patch("tools.kb.ingest.DEFERRED_QUEUE_PATH", queue),
            patch("tools.kb.db.DEFERRED_QUEUE_PATH", queue),
            patch("tools.kb.ingest.DEFAULT_DB_PATH", tmp_path / "graph.db"),
            patch(
                "tools.kb.ingest_cli._begin_immediate_with_retry",
                side_effect=sqlite3.OperationalError("database is locked"),
            ),
        ):
            main(["--db", db_path, "review", "--artifact", str(review_artifact), "--reviewer", "gemini"])

        # A deferred entry should exist
        assert queue.exists()
        payload = json.loads(queue.read_text().strip())
        deferred_path = payload["args"][0]
        # Should be a snapshot path (not the original), stored in deferred_artifacts
        assert "deferred_artifacts" in deferred_path
        assert Path(deferred_path).exists()
        assert Path(deferred_path).read_text() == review_artifact.read_text()

    def test_cmd_analyze_snapshots_artifact_on_deferral(self, tmp_path: Path) -> None:
        """Test that cmd_analyze snapshots the artifact before deferring on SQLITE_BUSY."""
        analyze_artifact = tmp_path / "analyze.json"
        analyze_artifact.write_text(json.dumps({"impacted_files": [{"path": "a.py"}]}))
        queue = tmp_path / "deferred.jsonl"
        db_path = str(tmp_path / "test.db")

        with (
            patch("tools.kb.ingest.DEFERRED_QUEUE_PATH", queue),
            patch("tools.kb.db.DEFERRED_QUEUE_PATH", queue),
            patch("tools.kb.ingest.DEFAULT_DB_PATH", tmp_path / "graph.db"),
            patch(
                "tools.kb.ingest_cli._begin_immediate_with_retry",
                side_effect=sqlite3.OperationalError("database is locked"),
            ),
        ):
            main(["--db", db_path, "analyze", "--artifact", str(analyze_artifact)])

        assert queue.exists()
        payload = json.loads(queue.read_text().strip())
        deferred_path = payload["args"][0]
        assert "deferred_artifacts" in deferred_path
        assert Path(deferred_path).exists()
        assert Path(deferred_path).read_text() == analyze_artifact.read_text()

    def test_snapshot_cleanup_uses_active_db_path(self, tmp_path: Path) -> None:
        """Test that snapshot cleanup uses the active db_path, not DEFAULT_DB_PATH."""
        queue = tmp_path / "deferred.jsonl"
        # Use a custom DB directory separate from DEFAULT_DB_PATH
        custom_db_dir = tmp_path / "custom"
        custom_db_dir.mkdir()
        db_path = str(custom_db_dir / "test.db")
        snapshot_dir = custom_db_dir / "deferred_artifacts"
        snapshot_dir.mkdir()
        snapshot_file = snapshot_dir / "abc123.xml"
        snapshot_file.write_text(
            '<?xml version="1.0"?>\n<testsuites>'
            '<testsuite name="s"><testcase classname="t" name="t1" time="0.1"/>'
            "</testsuite></testsuites>"
        )
        queue.write_text(
            json.dumps({
                "func": "_ingest_test",
                "args": [str(snapshot_file), ["a.py"], None],
                "kwargs": {"ingested_at": "2024-06-15T10:00:00.000000Z"},
            })
            + "\n"
        )
        conn = get_connection(db_path)
        init_schema(conn)
        conn.close()

        # DEFAULT_DB_PATH points elsewhere — cleanup should still work via db_path
        with (
            patch("tools.kb.ingest.DEFERRED_QUEUE_PATH", queue),
            patch("tools.kb.ingest.DEFAULT_DB_PATH", tmp_path / "other" / "graph.db"),
        ):
            count = replay_deferred(db_path)

        assert count == 1
        # Snapshot should be cleaned up even though DEFAULT_DB_PATH is different
        assert not snapshot_file.exists()

    def test_shared_snapshot_not_deleted_until_all_replayed(self, tmp_path: Path) -> None:
        """Test that a snapshot shared by multiple deferred entries is not deleted prematurely."""
        queue = tmp_path / "deferred.jsonl"
        snapshot_dir = tmp_path / "deferred_artifacts"
        snapshot_dir.mkdir()
        snapshot_file = snapshot_dir / "shared.xml"
        snapshot_file.write_text(
            '<?xml version="1.0"?>\n<testsuites>'
            '<testsuite name="s"><testcase classname="t" name="t1" time="0.1"/>'
            "</testsuite></testsuites>"
        )
        # Two deferred entries referencing the same snapshot but different changed_files
        entry1 = json.dumps({
            "func": "_ingest_test",
            "args": [str(snapshot_file), ["a.py"], None],
            "kwargs": {"ingested_at": "2024-06-15T10:00:00.000000Z"},
        })
        entry2 = json.dumps({
            "func": "_ingest_test",
            "args": [str(snapshot_file), ["b.py"], None],
            "kwargs": {"ingested_at": "2024-06-15T10:01:00.000000Z"},
        })
        queue.write_text(entry1 + "\n" + entry2 + "\n")
        db_path = str(tmp_path / "test.db")
        conn = get_connection(db_path)
        init_schema(conn)
        conn.close()

        with (
            patch("tools.kb.ingest.DEFERRED_QUEUE_PATH", queue),
            patch("tools.kb.ingest.DEFAULT_DB_PATH", tmp_path / "graph.db"),
        ):
            count = replay_deferred(db_path)

        # Both entries should replay successfully
        assert count == 2
        # Snapshot cleaned up only after both entries processed
        assert not snapshot_file.exists()

    def test_snapshot_preserved_when_remaining_entry_references_it(self, tmp_path: Path) -> None:
        """Test that snapshots are NOT deleted if a remaining (requeued) entry still references them."""
        queue = tmp_path / "deferred.jsonl"
        snapshot_dir = tmp_path / "deferred_artifacts"
        snapshot_dir.mkdir()
        snapshot_file = snapshot_dir / "shared.xml"
        snapshot_file.write_text(
            '<?xml version="1.0"?>\n<testsuites>'
            '<testsuite name="s"><testcase classname="t" name="t1" time="0.1"/>'
            "</testsuite></testsuites>"
        )
        # Entry 1: will succeed (normal commit, not file-based)
        # Entry 2: file-based test ingest scoped to a DIFFERENT db — will be requeued
        other_db = str((tmp_path / "other.db").resolve())
        entry1 = json.dumps({
            "func": "_ingest_test",
            "args": [str(snapshot_file), ["a.py"], None],
            "kwargs": {"ingested_at": "2024-06-15T10:00:00.000000Z"},
        })
        entry2 = json.dumps({
            "func": "_ingest_test",
            "args": [str(snapshot_file), ["b.py"], None],
            "kwargs": {"ingested_at": "2024-06-15T10:01:00.000000Z"},
            "db": other_db,
        })
        queue.write_text(entry1 + "\n" + entry2 + "\n")
        db_path = str(tmp_path / "test.db")
        conn = get_connection(db_path)
        init_schema(conn)
        conn.close()

        with (
            patch("tools.kb.ingest.DEFERRED_QUEUE_PATH", queue),
            patch("tools.kb.ingest.DEFAULT_DB_PATH", tmp_path / "graph.db"),
        ):
            count = replay_deferred(db_path)

        # Only entry1 replayed; entry2 remains (different db)
        assert count == 1
        assert queue.exists()
        # Snapshot must still exist because entry2 still references it
        assert snapshot_file.exists()

    def test_cmd_review_preserves_run_id_on_deferral(self, tmp_path: Path) -> None:
        """Test that cmd_review pre-generates and persists run_id in deferred payload."""
        review_artifact = tmp_path / "review.json"
        review_artifact.write_text(json.dumps([{"summary": "issue", "file_path": "a.py"}]))
        queue = tmp_path / "deferred.jsonl"
        db_path = str(tmp_path / "test.db")

        with (
            patch("tools.kb.ingest.DEFERRED_QUEUE_PATH", queue),
            patch("tools.kb.db.DEFERRED_QUEUE_PATH", queue),
            patch("tools.kb.ingest.DEFAULT_DB_PATH", tmp_path / "graph.db"),
            patch(
                "tools.kb.ingest_cli._begin_immediate_with_retry",
                side_effect=sqlite3.OperationalError("database is locked"),
            ),
        ):
            main(["--db", db_path, "review", "--artifact", str(review_artifact), "--reviewer", "gemini"])

        assert queue.exists()
        payload = json.loads(queue.read_text().strip())
        # run_id (third arg) should be a non-None hash string, not None
        run_id = payload["args"][2]
        assert run_id is not None
        assert isinstance(run_id, str)
        assert len(run_id) == 16  # sha256[:16]

    def test_test_run_id_stable_across_snapshot_path(
        self, conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Test that _ingest_test produces the same run_id regardless of file path.

        The same ingested_at must be passed to both calls (as would happen
        during deferred replay, which preserves the original timestamp).
        """
        xml_content = (
            '<?xml version="1.0"?>\n<testsuites>'
            '<testsuite name="s"><testcase classname="t" name="t1" time="0.1"/>'
            "</testsuite></testsuites>"
        )
        original = tmp_path / "results.xml"
        original.write_text(xml_content)
        snapshot = tmp_path / "deferred_artifacts" / "abc123.xml"
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(xml_content)

        fixed_ts = "2024-06-15T10:00:00.000000Z"
        conn.execute("BEGIN IMMEDIATE")
        _ingest_test(conn, str(original), ["a.py"], ingested_at=fixed_ts)
        conn.commit()
        run1 = conn.execute("SELECT run_id FROM test_runs LIMIT 1").fetchone()["run_id"]

        # Clear and re-ingest from snapshot path with same ingested_at
        conn.execute("DELETE FROM test_results")
        conn.execute("DELETE FROM test_runs")
        conn.commit()

        conn.execute("BEGIN IMMEDIATE")
        _ingest_test(conn, str(snapshot), ["a.py"], ingested_at=fixed_ts)
        conn.commit()
        run2 = conn.execute("SELECT run_id FROM test_runs LIMIT 1").fetchone()["run_id"]

        assert run1 == run2


class TestSnapshotArtifact:
    """Tests for _snapshot_artifact helper."""

    def test_creates_snapshot_with_content_hash(self, tmp_path: Path) -> None:
        """Test that _snapshot_artifact creates a unique copy based on content hash."""
        original = tmp_path / "results.xml"
        original.write_text("<xml>test content</xml>")

        with patch("tools.kb.ingest.DEFAULT_DB_PATH", tmp_path / "graph.db"):
            snapshot_path = _snapshot_artifact(str(original))

        snapshot = Path(snapshot_path)
        assert snapshot.exists()
        assert snapshot.parent.name == "deferred_artifacts"
        assert snapshot.read_text() == "<xml>test content</xml>"
        # Snapshot path is different from original
        assert snapshot_path != str(original)

    def test_idempotent_same_content(self, tmp_path: Path) -> None:
        """Test that snapshotting same content returns same path."""
        original = tmp_path / "results.xml"
        original.write_text("<xml>same content</xml>")

        with patch("tools.kb.ingest.DEFAULT_DB_PATH", tmp_path / "graph.db"):
            path1 = _snapshot_artifact(str(original))
            path2 = _snapshot_artifact(str(original))

        assert path1 == path2

    def test_different_content_different_snapshot(self, tmp_path: Path) -> None:
        """Test that different content produces different snapshot paths."""
        original = tmp_path / "results.xml"

        with patch("tools.kb.ingest.DEFAULT_DB_PATH", tmp_path / "graph.db"):
            original.write_text("<xml>content v1</xml>")
            path1 = _snapshot_artifact(str(original))
            original.write_text("<xml>content v2</xml>")
            path2 = _snapshot_artifact(str(original))

        assert path1 != path2

    def test_missing_file_returns_resolved_path(self, tmp_path: Path) -> None:
        """Test that missing file returns resolved path without creating snapshot."""
        missing = tmp_path / "nonexistent.xml"

        with patch("tools.kb.ingest.DEFAULT_DB_PATH", tmp_path / "graph.db"):
            result = _snapshot_artifact(str(missing))

        assert result == str(missing.resolve())
        snapshot_dir = tmp_path / "deferred_artifacts"
        assert not snapshot_dir.exists()

    def test_respects_custom_db_path(self, tmp_path: Path) -> None:
        """Test that snapshot is stored alongside custom db_path, not DEFAULT_DB_PATH."""
        original = tmp_path / "results.xml"
        original.write_text("<xml>test</xml>")
        custom_db = tmp_path / "custom" / "my.db"

        snapshot_path = _snapshot_artifact(str(original), db_path=str(custom_db))

        snapshot = Path(snapshot_path)
        assert snapshot.exists()
        # Should be under custom/deferred_artifacts/, not DEFAULT_DB_PATH
        assert snapshot.parent == tmp_path / "custom" / "deferred_artifacts"


class TestIngestTestTimestamp:
    """Tests for _ingest_test ingested_at parameter."""

    def test_uses_ingested_at_when_provided(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        """Test that _ingest_test uses ingested_at instead of _now_iso when provided."""
        junit_xml = tmp_path / "results.xml"
        junit_xml.write_text(
            '<?xml version="1.0"?>\n<testsuites>'
            '<testsuite name="s"><testcase classname="t" name="t1" time="0.1"/>'
            "</testsuite></testsuites>"
        )
        fixed_time = "2024-06-15T10:00:00.000000Z"
        conn.execute("BEGIN IMMEDIATE")
        _ingest_test(conn, str(junit_xml), ["a.py"], ingested_at=fixed_time)
        conn.commit()

        run = conn.execute("SELECT started_at, finished_at FROM test_runs LIMIT 1").fetchone()
        assert run["started_at"] == fixed_time
        assert run["finished_at"] == fixed_time

    def test_falls_back_to_now_without_ingested_at(
        self, conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Test that _ingest_test uses _now_iso when ingested_at is not provided."""
        junit_xml = tmp_path / "results.xml"
        junit_xml.write_text(
            '<?xml version="1.0"?>\n<testsuites>'
            '<testsuite name="s"><testcase classname="t" name="t1" time="0.1"/>'
            "</testsuite></testsuites>"
        )
        conn.execute("BEGIN IMMEDIATE")
        _ingest_test(conn, str(junit_xml), ["a.py"])
        conn.commit()

        run = conn.execute("SELECT started_at FROM test_runs LIMIT 1").fetchone()
        # Should be a current timestamp, not None
        assert run["started_at"] is not None
        assert run["started_at"].endswith("Z")


class TestSnapshotAbsolutePaths:
    """Tests that snapshot paths are always absolute regardless of db_path."""

    def test_snapshot_returns_absolute_path_with_relative_db(self, tmp_path: Path) -> None:
        """_snapshot_artifact returns absolute path even when --db is relative."""
        artifact = tmp_path / "review.json"
        artifact.write_text('{"findings": []}')
        # Use a relative db_path
        rel_db = "some/dir/graph.db"
        result = _snapshot_artifact(str(artifact), db_path=rel_db)
        assert Path(result).is_absolute(), f"Snapshot path should be absolute, got: {result}"

    def test_snapshot_returns_absolute_path_with_no_db(self, tmp_path: Path) -> None:
        """_snapshot_artifact returns absolute path with default DB."""
        artifact = tmp_path / "results.xml"
        artifact.write_text("<testsuites/>")
        result = _snapshot_artifact(str(artifact))
        assert Path(result).is_absolute(), f"Snapshot path should be absolute, got: {result}"


class TestBackfillLockTiming:
    """Tests that backfill parses git history before acquiring DB lock."""

    def test_backfill_accepts_pre_parsed_commits(self, conn: sqlite3.Connection) -> None:
        """_ingest_backfill uses pre-parsed commits without calling parse_git_log_range."""
        pre_parsed = [("abc123", ["a.py", "b.py"]), ("def456", ["b.py", "c.py"])]
        conn.execute("BEGIN IMMEDIATE")
        with patch("tools.kb.ingest.parse_git_log_range") as mock_parse:
            count = _ingest_backfill(conn, "6 months ago", commits=pre_parsed)
            # parse_git_log_range should NOT be called when commits are provided
            mock_parse.assert_not_called()
        conn.commit()
        assert count > 0

    def test_backfill_uses_pre_computed_dates(self, conn: sqlite3.Connection) -> None:
        """_ingest_backfill uses commit_dates dict without calling parse_git_commit_date."""
        pre_parsed = [("abc123", ["a.py", "b.py"])]
        commit_dates = {"abc123": "2024-06-01T12:00:00Z"}
        conn.execute("BEGIN IMMEDIATE")
        with patch("tools.kb.ingest.parse_git_commit_date") as mock_date:
            _ingest_backfill(
                conn, "6 months ago", commits=pre_parsed, commit_dates=commit_dates
            )
            # parse_git_commit_date should NOT be called when dates are provided
            mock_date.assert_not_called()
        conn.commit()

        # Verify the pre-computed date was used in evidence
        ev = conn.execute(
            "SELECT observed_at FROM edge_evidence WHERE src_file = 'a.py'"
        ).fetchone()
        assert ev is not None
        assert ev["observed_at"] == "2024-06-01T12:00:00Z"


class TestDeferredLockFailOpen:
    """Tests that deferred queue replay fails open on lock file errors."""

    def test_replay_handles_lock_file_permission_error(self, tmp_path: Path) -> None:
        """replay_deferred returns 0 when lock file cannot be opened."""
        from tools.kb.ingest import replay_deferred

        queue = tmp_path / "deferred.jsonl"
        queue.write_text('{"func": "_ingest_commit", "args": ["abc"]}\n')

        db_path = str(tmp_path / "test.db")
        conn = get_connection(db_path)
        init_schema(conn)
        conn.close()

        # Mock open to raise PermissionError for lock file
        original_open = open

        def mock_open(path: object, *a: object, **kw: object) -> object:
            if str(path).endswith(".lock"):
                raise PermissionError("Operation not permitted")
            return original_open(path, *a, **kw)  # type: ignore[arg-type]

        with (
            patch("tools.kb.ingest.DEFERRED_QUEUE_PATH", queue),
            patch("builtins.open", side_effect=mock_open),
        ):
            result = replay_deferred(db_path)

        assert result == 0
        # Queue file should still exist (not lost)
        assert queue.exists()


class TestArtifactSizeLimit:
    """Tests for MAX_ARTIFACT_BYTES size guard."""

    def test_snapshot_skips_oversized_artifact(self, tmp_path: Path) -> None:
        """_snapshot_artifact returns original path for files exceeding size limit."""
        artifact = tmp_path / "huge.json"
        artifact.write_text("x" * 100)
        with patch("tools.kb.ingest.MAX_ARTIFACT_BYTES", 50):
            result = _snapshot_artifact(str(artifact))
        # Should return resolved original path, not create a snapshot
        assert result == str(artifact.resolve())

    def test_snapshot_proceeds_for_small_artifact(self, tmp_path: Path) -> None:
        """_snapshot_artifact creates snapshot for files within size limit."""
        artifact = tmp_path / "small.json"
        artifact.write_text('{"ok": true}')
        result = _snapshot_artifact(str(artifact))
        assert result != str(artifact)  # Should be a snapshot path

    def test_review_uses_mtime_only_for_oversized(
        self, conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """_ingest_review uses mtime-only run_id for oversized artifacts."""
        data = [{"file_path": "a.py", "summary": "issue"}]
        artifact = tmp_path / "review.json"
        artifact.write_text(json.dumps(data))

        conn.execute("BEGIN IMMEDIATE")
        with patch("tools.kb.ingest.MAX_ARTIFACT_BYTES", 5):
            # Should still work — just uses mtime-only run_id
            count = _ingest_review(conn, str(artifact), "gemini")
        conn.commit()
        assert count == 1


class TestDeferredPayloadValidation:
    """Tests for non-dict payload handling in replay_deferred."""

    def test_replay_skips_non_dict_json_entries(self, tmp_path: Path) -> None:
        """replay_deferred skips JSON array and string entries in deferred queue."""
        queue = tmp_path / "deferred.jsonl"
        lines = [
            '[1, 2, 3]',  # JSON array — not a dict
            '"just a string"',  # JSON string — not a dict
            '42',  # JSON number — not a dict
        ]
        queue.write_text("\n".join(lines) + "\n")

        db_path = str(tmp_path / "test.db")
        conn = get_connection(db_path)
        init_schema(conn)
        conn.close()

        with patch("tools.kb.ingest.DEFERRED_QUEUE_PATH", queue):
            result = replay_deferred(db_path)

        # All entries should be skipped (not crash)
        assert result == 0
        # Non-dict entries are discarded, not requeued
        assert not queue.exists() or queue.read_text().strip() == ""


class TestNodeidHeuristic:
    """Tests for test nodeid to file path resolution edge cases."""

    def test_empty_classname_skipped(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        """Test that empty classname in nodeid is handled gracefully.

        When JUnit emits an empty classname, parse_junit_xml produces nodeids
        like 'test_func' (no ::), which _ingest_test now skips entirely to
        avoid creating TESTS edges to nonexistent files.
        """
        xml_content = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<testsuite name="test">'
            '<testcase classname="" name="test_func" time="0.1">'
            '<failure message="oops">error</failure>'
            '</testcase>'
            '</testsuite>'
        )
        xml_path = tmp_path / "junit.xml"
        xml_path.write_text(xml_content)

        conn.execute("BEGIN IMMEDIATE")
        count = _ingest_test(conn, str(xml_path), ["src/app.py"])
        conn.commit()
        # Should not crash — test result is ingested, but no TESTS edges created
        assert count == 1
        edges = conn.execute(
            "SELECT COUNT(*) FROM edge_evidence WHERE relation = 'TESTS'"
        ).fetchone()
        assert edges[0] == 0

    def test_all_uppercase_parts_fallback(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        """Test that all-uppercase classname parts don't produce empty path."""
        xml_content = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<testsuite name="test">'
            '<testcase classname="TestModule.TestClass" name="test_method" time="0.1">'
            '<failure message="fail">error</failure>'
            '</testcase>'
            '</testsuite>'
        )
        xml_path = tmp_path / "junit.xml"
        xml_path.write_text(xml_content)

        conn.execute("BEGIN IMMEDIATE")
        count = _ingest_test(conn, str(xml_path), ["src/app.py"])
        conn.commit()
        # Should create edges using fallback path
        assert count == 1
        edge = conn.execute(
            "SELECT src_file FROM edge_evidence WHERE relation = 'TESTS'"
        ).fetchone()
        assert edge is not None
        assert edge["src_file"] == "TestModule/TestClass.py"
