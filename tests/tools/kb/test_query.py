"""Unit tests for tools.kb.query — all 3 query modes."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools.kb.db import get_connection, init_schema
from tools.kb.query import (
    _escape_like,
    compute_freshness,
    main,
    query_implementation_brief,
    query_pre_commit_check,
    query_troubleshoot,
)


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    """Return a connection with schema initialized."""
    c = get_connection(tmp_path / "test.db")
    init_schema(c)
    return c


@pytest.fixture()
def seeded_conn(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Return a connection with test data seeded."""
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Seed file_edges
    edges = [
        ("a.py", "b.py", "CO_CHANGE", 3.0, 3, "sha1"),
        ("a.py", "c.py", "CO_CHANGE", 2.0, 2, "sha2"),
        ("a.py", "d.py", "CO_CHANGE", 0.5, 1, "sha3"),  # Low support
        ("tests/test_a.py", "a.py", "TESTS", 1.8, 2, "sha1"),
    ]
    for src, dst, rel, weight, count, sha in edges:
        conn.execute(
            "INSERT INTO file_edges "
            "(src_file, dst_file, relation, weight, support_count, last_seen_sha) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (src, dst, rel, weight, count, sha),
        )

    # Seed edge_evidence for freshness
    conn.execute(
        "INSERT INTO edge_evidence "
        "(evidence_id, src_file, dst_file, relation, source, source_id, weight, observed_at) "
        "VALUES ('ev1', 'a.py', 'b.py', 'CO_CHANGE', 'COMMIT', 'sha1', 1.0, ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO edge_evidence "
        "(evidence_id, src_file, dst_file, relation, source, source_id, weight, observed_at) "
        "VALUES ('ev2', 'a.py', 'c.py', 'CO_CHANGE', 'COMMIT', 'sha2', 1.0, ?)",
        (now,),
    )

    # Seed issue_patterns
    conn.execute(
        "INSERT INTO issue_patterns (rule_id, scope_path, count, examples_json) "
        "VALUES ('UTC_NAIVE_DATETIME', 'apps/', 5, ?)",
        (json.dumps(["apps/main.py:42"]),),
    )

    # Seed error_fixes
    conn.execute(
        "INSERT INTO implementation_sessions "
        "(session_id, started_at, outcome) VALUES ('s1', ?, 'COMMITTED')",
        (now,),
    )
    conn.execute(
        "INSERT INTO error_fixes "
        "(fix_id, session_id, error_signature, fixed_files_json, confidence) "
        "VALUES ('ef1', 's1', 'sig_utc', ?, 0.9)",
        (json.dumps(["a.py", "utils.py"]),),
    )

    conn.commit()
    return conn


class TestComputeFreshness:
    """Tests for freshness decay function."""

    def test_recent_is_high(self) -> None:
        """Test that recent timestamps have high freshness."""
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert compute_freshness(now) > 0.95

    def test_old_is_low(self) -> None:
        """Test that old timestamps have low freshness."""
        assert compute_freshness("2020-01-01T00:00:00Z") < 0.01

    def test_half_life_at_90_days(self) -> None:
        """Test that freshness is ~0.5 at half_life."""
        from datetime import timedelta

        past = (datetime.now(UTC) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
        f = compute_freshness(past)
        assert 0.3 < f < 0.7  # Roughly 0.5

    def test_invalid_date(self) -> None:
        """Test that invalid date returns neutral score."""
        assert compute_freshness("not-a-date") == 0.5

    def test_naive_timestamp(self) -> None:
        """Test that timezone-naive timestamps are handled without crashing."""
        # Naive ISO string (no Z or +00:00)
        f = compute_freshness("2024-01-01T00:00:00")
        assert 0.0 < f < 1.0


class TestImplementationBrief:
    """Tests for implementation-brief query mode."""

    def test_returns_impacted_files(self, seeded_conn: sqlite3.Connection) -> None:
        """Test that impacted files are returned."""
        brief = query_implementation_brief(seeded_conn, ["a.py"])
        assert len(brief.likely_impacted_files) >= 1
        # b.py should be ranked high (weight=3.0, support=3)
        paths = [f.path for f in brief.likely_impacted_files]
        assert "b.py" in paths

    def test_excludes_changed_files(self, seeded_conn: sqlite3.Connection) -> None:
        """Test that changed files are excluded from results."""
        brief = query_implementation_brief(seeded_conn, ["a.py"])
        paths = [f.path for f in brief.likely_impacted_files]
        assert "a.py" not in paths

    def test_filters_low_support(self, seeded_conn: sqlite3.Connection) -> None:
        """Test that edges with support_count < 2 are filtered."""
        brief = query_implementation_brief(seeded_conn, ["a.py"])
        paths = [f.path for f in brief.likely_impacted_files]
        assert "d.py" not in paths  # support_count=1

    def test_returns_recommended_tests(self, seeded_conn: sqlite3.Connection) -> None:
        """Test that test files are recommended."""
        brief = query_implementation_brief(seeded_conn, ["a.py"])
        assert len(brief.recommended_tests) >= 1
        test_paths = [t.path for t in brief.recommended_tests]
        assert "tests/test_a.py" in test_paths

    def test_returns_pitfalls(self, seeded_conn: sqlite3.Connection) -> None:
        """Test that known pitfalls are returned."""
        brief = query_implementation_brief(seeded_conn, ["apps/main.py"])
        assert len(brief.known_pitfalls) >= 1
        assert brief.known_pitfalls[0].rule_id == "UTC_NAIVE_DATETIME"

    def test_like_wildcards_escaped_in_pitfall_scope(
        self, conn: sqlite3.Connection
    ) -> None:
        """Test that _escape_like prevents LIKE metacharacters from acting as wildcards."""
        # Verify the escape function itself
        assert _escape_like("apps/signal_service/") == "apps/signal\\_service/"
        assert _escape_like("path_with%both") == "path\\_with\\%both"
        assert _escape_like("clean/path/") == "clean/path/"

        # Verify escaped pattern works correctly in SQLite LIKE
        conn.execute(
            "INSERT INTO issue_patterns (rule_id, scope_path, count, examples_json) "
            "VALUES ('RULE_A', 'libs/signal_service/sub/', 3, ?)",
            (json.dumps([]),),
        )
        conn.execute(
            "INSERT INTO issue_patterns (rule_id, scope_path, count, examples_json) "
            "VALUES ('RULE_B', 'libs/signalXservice/sub/', 3, ?)",
            (json.dumps([]),),
        )
        conn.commit()

        # With _ escaped, only the literal match should succeed
        escaped = _escape_like("libs/signal_service/sub/")
        rows = conn.execute(
            "SELECT rule_id FROM issue_patterns WHERE scope_path LIKE ? ESCAPE '\\'",
            (f"{escaped}%",),
        ).fetchall()
        rule_ids = [r["rule_id"] for r in rows]
        assert "RULE_A" in rule_ids
        assert "RULE_B" not in rule_ids

    def test_deduplicates_recommended_tests(self, seeded_conn: sqlite3.Connection) -> None:
        """Test that recommended tests are deduplicated across changed files."""
        # Add another TESTS edge from same test to a different file
        seeded_conn.execute(
            "INSERT INTO file_edges "
            "(src_file, dst_file, relation, weight, support_count, last_seen_sha) "
            "VALUES ('tests/test_a.py', 'b.py', 'TESTS', 1.5, 2, 'sha1')"
        )
        seeded_conn.commit()

        brief = query_implementation_brief(seeded_conn, ["a.py", "b.py"])
        test_paths = [t.path for t in brief.recommended_tests]
        # tests/test_a.py should appear only once, not twice
        assert test_paths.count("tests/test_a.py") == 1

    def test_caps_results(self, seeded_conn: sqlite3.Connection) -> None:
        """Test result capping."""
        brief = query_implementation_brief(seeded_conn, ["a.py"], top_files=1)
        assert len(brief.likely_impacted_files) <= 1

    def test_includes_references_edges(self, conn: sqlite3.Connection) -> None:
        """Test that REFERENCES edges are included and freshness uses correct relation."""
        conn.execute(
            "INSERT INTO file_edges "
            "(src_file, dst_file, relation, weight, support_count, last_seen_sha) "
            "VALUES ('x.py', 'y.py', 'REFERENCES', 2.0, 3, 'sha1')"
        )
        conn.execute(
            "INSERT INTO edge_evidence "
            "(evidence_id, src_file, dst_file, relation, source, source_id, weight, observed_at) "
            "VALUES ('ev1', 'x.py', 'y.py', 'REFERENCES', 'SESSION', 's1', 2.0, "
            "datetime('now'))"
        )
        conn.commit()
        brief = query_implementation_brief(conn, ["x.py"])
        paths = [f.path for f in brief.likely_impacted_files]
        assert "y.py" in paths
        # Verify reason uses the actual relation type, not hard-coded CO_CHANGE
        y_file = next(f for f in brief.likely_impacted_files if f.path == "y.py")
        assert "REFERENCES" in y_file.reason

    def test_root_level_file_pitfalls(self, conn: sqlite3.Connection) -> None:
        """Test that root-level files (e.g., pyproject.toml) surface pitfalls under './' scope."""
        conn.execute(
            "INSERT INTO issue_patterns (rule_id, scope_path, count, examples_json) "
            "VALUES ('MISSING_TYPE_HINTS', './', 5, '[]')"
        )
        conn.commit()
        brief = query_implementation_brief(conn, ["pyproject.toml"])
        assert len(brief.known_pitfalls) == 1
        assert brief.known_pitfalls[0].rule_id == "MISSING_TYPE_HINTS"

    def test_pitfall_scope_underscore_not_wildcard(self, conn: sqlite3.Connection) -> None:
        """Test that '_' in scope paths is matched literally, not as SQL wildcard."""
        # Use top-level directory scopes to avoid broader parent scope matching both
        conn.execute(
            "INSERT INTO issue_patterns (rule_id, scope_path, count, examples_json) "
            "VALUES ('RULE_A', 'signal_service/', 3, '[]')"
        )
        conn.execute(
            "INSERT INTO issue_patterns (rule_id, scope_path, count, examples_json) "
            "VALUES ('RULE_B', 'signalXservice/', 3, '[]')"
        )
        conn.commit()
        brief = query_implementation_brief(conn, ["signal_service/foo.py"])
        rule_ids = [p.rule_id for p in brief.known_pitfalls]
        assert "RULE_A" in rule_ids
        assert "RULE_B" not in rule_ids  # '_' should not match 'X'

    def test_pitfall_excludes_sibling_scopes(self, conn: sqlite3.Connection) -> None:
        """Test that pitfalls from sibling directories are excluded.

        Changing apps/signal_service/main.py should surface pitfalls from apps/
        (parent) and apps/signal_service/ (exact), but NOT apps/orders/ (sibling).
        """
        conn.execute(
            "INSERT INTO issue_patterns (rule_id, scope_path, count, examples_json) "
            "VALUES ('PARENT_RULE', 'apps/', 5, '[]')"
        )
        conn.execute(
            "INSERT INTO issue_patterns (rule_id, scope_path, count, examples_json) "
            "VALUES ('EXACT_RULE', 'apps/signal_service/', 3, '[]')"
        )
        conn.execute(
            "INSERT INTO issue_patterns (rule_id, scope_path, count, examples_json) "
            "VALUES ('SIBLING_RULE', 'apps/orders/', 4, '[]')"
        )
        conn.commit()
        brief = query_implementation_brief(conn, ["apps/signal_service/main.py"])
        rule_ids = [p.rule_id for p in brief.known_pitfalls]
        assert "PARENT_RULE" in rule_ids  # Parent scope applies
        assert "EXACT_RULE" in rule_ids  # Exact scope applies
        assert "SIBLING_RULE" not in rule_ids  # Sibling scope excluded

    def test_pitfall_deep_scope_matched(self, conn: sqlite3.Connection) -> None:
        """Test that pitfalls at deep directory scopes are matched for nested files.

        Changing apps/gw/recon/worker.py should match pitfalls at apps/gw/recon/.
        """
        conn.execute(
            "INSERT INTO issue_patterns (rule_id, scope_path, count, examples_json) "
            "VALUES ('DEEP_RULE', 'apps/gw/recon/', 3, '[]')"
        )
        conn.commit()
        brief = query_implementation_brief(conn, ["apps/gw/recon/worker.py"])
        rule_ids = [p.rule_id for p in brief.known_pitfalls]
        assert "DEEP_RULE" in rule_ids

    def test_empty_db(self, conn: sqlite3.Connection) -> None:
        """Test graceful handling of empty database."""
        brief = query_implementation_brief(conn, ["a.py"])
        assert brief.likely_impacted_files == []
        assert brief.recommended_tests == []
        assert brief.known_pitfalls == []

    def test_empty_changed_files(self, seeded_conn: sqlite3.Connection) -> None:
        """Test with empty changed files list."""
        brief = query_implementation_brief(seeded_conn, [])
        assert brief.likely_impacted_files == []

    def test_excludes_soft_expired_co_change_edges(self, conn: sqlite3.Connection) -> None:
        """Test that soft-expired edges (weight=0) are excluded from results."""
        # Insert a soft-expired edge (weight=0.0)
        conn.execute(
            "INSERT INTO file_edges "
            "(src_file, dst_file, relation, weight, support_count, last_seen_sha) "
            "VALUES ('a.py', 'b.py', 'CO_CHANGE', 0.0, 3, 'sha1')"
        )
        conn.execute(
            "INSERT INTO edge_evidence "
            "(evidence_id, src_file, dst_file, relation, source, source_id, weight, observed_at) "
            "VALUES ('e1', 'a.py', 'b.py', 'CO_CHANGE', 'COMMIT', 'sha1', 1.0, '2020-01-01T00:00:00Z')"
        )
        conn.commit()
        brief = query_implementation_brief(conn, ["a.py"])
        paths = [f.path for f in brief.likely_impacted_files]
        assert "b.py" not in paths

    def test_excludes_soft_expired_tests_edges(self, conn: sqlite3.Connection) -> None:
        """Test that soft-expired TESTS edges (weight=0) are excluded from recommendations."""
        conn.execute(
            "INSERT INTO file_edges "
            "(src_file, dst_file, relation, weight, support_count, last_seen_sha) "
            "VALUES ('tests/test_a.py', 'a.py', 'TESTS', 0.0, 2, 'sha1')"
        )
        conn.commit()
        brief = query_implementation_brief(conn, ["a.py"])
        assert brief.recommended_tests == []


class TestTroubleshoot:
    """Tests for troubleshoot query mode."""

    def test_finds_past_fixes(self, seeded_conn: sqlite3.Connection) -> None:
        """Test that past error fixes are found."""
        result = query_troubleshoot(seeded_conn, "sig_utc")
        assert len(result.likely_fix_files) >= 1
        paths = [f.path for f in result.likely_fix_files]
        assert "a.py" in paths

    def test_includes_past_fix_details(self, seeded_conn: sqlite3.Connection) -> None:
        """Test that past fix details are included."""
        result = query_troubleshoot(seeded_conn, "sig_utc")
        assert len(result.past_fixes) >= 1

    def test_past_fixes_files_are_lists(self, seeded_conn: sqlite3.Connection) -> None:
        """Test that past_fixes files are native lists, not JSON strings."""
        result = query_troubleshoot(seeded_conn, "sig_utc")
        assert len(result.past_fixes) >= 1
        files = result.past_fixes[0]["files"]
        assert isinstance(files, list)
        assert "a.py" in files

    def test_unknown_error(self, seeded_conn: sqlite3.Connection) -> None:
        """Test graceful handling of unknown error signature."""
        result = query_troubleshoot(seeded_conn, "unknown_sig")
        assert result.likely_fix_files == []
        assert result.past_fixes == []

    def test_deduplicates_fix_files(self, conn: sqlite3.Connection) -> None:
        """Test that duplicate file paths across fixes are deduplicated."""
        conn.execute(
            "INSERT INTO implementation_sessions (session_id, started_at, outcome) "
            "VALUES (?, ?, ?)",
            ("s1", "2024-01-01T00:00:00Z", "COMMITTED"),
        )
        conn.execute(
            "INSERT INTO error_fixes (fix_id, session_id, error_signature, "
            "fixed_files_json, confidence) VALUES (?, ?, ?, ?, ?)",
            ("f1", "s1", "sig", json.dumps(["a.py"]), 0.9),
        )
        conn.execute(
            "INSERT INTO error_fixes (fix_id, session_id, error_signature, "
            "fixed_files_json, confidence) VALUES (?, ?, ?, ?, ?)",
            ("f2", "s1", "sig", json.dumps(["a.py"]), 0.8),
        )
        result = query_troubleshoot(conn, "sig")
        paths = [f.path for f in result.likely_fix_files]
        assert paths.count("a.py") == 1  # Deduplicated

    def test_changed_files_boosts_score(self, conn: sqlite3.Connection) -> None:
        """Test that changed_files boosts scores of matching fix files."""
        conn.execute(
            "INSERT INTO implementation_sessions (session_id, started_at, outcome) "
            "VALUES (?, ?, ?)",
            ("s1", "2024-01-01T00:00:00Z", "COMMITTED"),
        )
        conn.execute(
            "INSERT INTO error_fixes (fix_id, session_id, error_signature, "
            "fixed_files_json, confidence) VALUES (?, ?, ?, ?, ?)",
            ("f1", "s1", "sig", json.dumps(["a.py", "b.py"]), 0.8),
        )
        result = query_troubleshoot(conn, "sig", changed_files=["a.py"])
        a_file = next(f for f in result.likely_fix_files if f.path == "a.py")
        b_file = next(f for f in result.likely_fix_files if f.path == "b.py")
        assert a_file.score > b_file.score
        assert "[currently changed]" in a_file.reason


    def test_error_fix_edge_upgrades_lower_confidence(self, conn: sqlite3.Connection) -> None:
        """Test that ERROR_FIX edge with higher weight upgrades a lower-confidence fix entry."""
        conn.execute(
            "INSERT INTO implementation_sessions (session_id, started_at, outcome) "
            "VALUES (?, ?, ?)",
            ("s1", "2024-01-01T00:00:00Z", "COMMITTED"),
        )
        # error_fixes gives a.py confidence=0.5
        conn.execute(
            "INSERT INTO error_fixes (fix_id, session_id, error_signature, "
            "fixed_files_json, confidence) VALUES (?, ?, ?, ?, ?)",
            ("f1", "s1", "sig", json.dumps(["a.py"]), 0.5),
        )
        # file_edges gives a.py weight=1.8 (aggregated from multiple fixes)
        conn.execute(
            "INSERT INTO file_edges "
            "(src_file, dst_file, relation, weight, support_count, last_seen_sha) "
            "VALUES ('sig', 'a.py', 'ERROR_FIX', 1.8, 3, 'sha1')"
        )
        conn.commit()
        result = query_troubleshoot(conn, "sig")
        a_file = next(f for f in result.likely_fix_files if f.path == "a.py")
        # Score should be upgraded to 1.8, not stuck at 0.5
        assert a_file.score == 1.8

    def test_excludes_soft_expired_error_fix_edges(self, conn: sqlite3.Connection) -> None:
        """Test that soft-expired ERROR_FIX edges (weight=0) are excluded."""
        conn.execute(
            "INSERT INTO file_edges "
            "(src_file, dst_file, relation, weight, support_count, last_seen_sha) "
            "VALUES ('sig_utc', 'old_fix.py', 'ERROR_FIX', 0.0, 2, 'sha1')"
        )
        conn.commit()
        result = query_troubleshoot(conn, "sig_utc")
        paths = [f.path for f in result.likely_fix_files]
        assert "old_fix.py" not in paths


class TestPreCommitCheck:
    """Tests for pre-commit-check query mode."""

    def test_finds_missing_co_changes(self, seeded_conn: sqlite3.Connection) -> None:
        """Test that missing co-change partners are found."""
        result = query_pre_commit_check(seeded_conn, ["a.py"])
        paths = [f.path for f in result.missing_co_changes]
        # b.py and c.py have support_count >= 2
        assert "b.py" in paths or "c.py" in paths

    def test_excludes_staged_files(self, seeded_conn: sqlite3.Connection) -> None:
        """Test that staged files are excluded."""
        result = query_pre_commit_check(seeded_conn, ["a.py", "b.py"])
        paths = [f.path for f in result.missing_co_changes]
        assert "a.py" not in paths
        assert "b.py" not in paths

    def test_empty_staged(self, seeded_conn: sqlite3.Connection) -> None:
        """Test with empty staged files."""
        result = query_pre_commit_check(seeded_conn, [])
        assert result.missing_co_changes == []

    def test_advisory_text(self, seeded_conn: sqlite3.Connection) -> None:
        """Test that advisory text is present."""
        result = query_pre_commit_check(seeded_conn, ["a.py"])
        assert "historically coupled" in result.advisory

    def test_excludes_soft_expired_edges(self, conn: sqlite3.Connection) -> None:
        """Test that soft-expired edges (weight=0) are excluded from pre-commit check."""
        conn.execute(
            "INSERT INTO file_edges "
            "(src_file, dst_file, relation, weight, support_count, last_seen_sha) "
            "VALUES ('a.py', 'b.py', 'CO_CHANGE', 0.0, 3, 'sha1')"
        )
        conn.commit()
        result = query_pre_commit_check(conn, ["a.py"])
        paths = [f.path for f in result.missing_co_changes]
        assert "b.py" not in paths


class TestCLI:
    """Tests for CLI entry point."""

    def test_implementation_brief_cli(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test implementation-brief via CLI."""
        db_path = str(tmp_path / "test.db")
        main(["--db", db_path, "implementation-brief", "--changed-files", "a.py"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "likely_impacted_files" in data

    def test_troubleshoot_cli(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Test troubleshoot via CLI."""
        db_path = str(tmp_path / "test.db")
        main(["--db", db_path, "troubleshoot", "--error-signature", "sig1"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "likely_fix_files" in data

    def test_pre_commit_check_cli(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Test pre-commit-check via CLI."""
        db_path = str(tmp_path / "test.db")
        main(["--db", db_path, "pre-commit-check", "--staged-files", "a.py,b.py"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "missing_co_changes" in data
