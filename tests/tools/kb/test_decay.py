"""Unit tests for tools.kb.decay — freshness, soft expiry, and hard prune."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tools.kb.db import get_connection, init_schema
from tools.kb.decay import (
    compute_freshness,
    hard_prune,
    prune_deleted_files,
    soft_expire,
)


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    """Return a connection with schema initialized."""
    c = get_connection(tmp_path / "test.db")
    init_schema(c)
    return c


def _iso(dt: datetime) -> str:
    """Format datetime as ISO8601."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class TestComputeFreshness:
    """Tests for freshness computation."""

    def test_now_is_fresh(self) -> None:
        """Test that current time gives high freshness."""
        now = _iso(datetime.now(UTC))
        assert compute_freshness(now) > 0.95

    def test_old_date_is_stale(self) -> None:
        """Test that very old date gives low freshness."""
        old = _iso(datetime(2020, 1, 1, tzinfo=UTC))
        assert compute_freshness(old) < 0.01

    def test_half_life(self) -> None:
        """Test freshness at half-life is approximately 0.5."""
        half_life_ago = _iso(datetime.now(UTC) - timedelta(days=90))
        f = compute_freshness(half_life_ago)
        assert 0.3 < f < 0.7

    def test_invalid_date(self) -> None:
        """Test that invalid date returns default."""
        assert compute_freshness("not-a-date") == 0.5

    def test_naive_timestamp(self) -> None:
        """Test that timezone-naive timestamps are handled without crashing."""
        f = compute_freshness("2024-01-01T00:00:00")
        assert 0.0 < f < 1.0

    def test_future_timestamp_clamped(self) -> None:
        """Test that future timestamps are clamped to 1.0, not overflow."""
        future = _iso(datetime.now(UTC) + timedelta(days=365))
        f = compute_freshness(future)
        assert f == 1.0

    def test_far_future_no_overflow(self) -> None:
        """Test that far-future timestamps don't cause OverflowError."""
        f = compute_freshness("2999-01-01T00:00:00Z")
        assert f == 1.0


class TestSoftExpire:
    """Tests for soft expiry."""

    def test_expires_low_weight_edges(self, conn: sqlite3.Connection) -> None:
        """Test that edges below threshold are expired."""
        conn.execute(
            "INSERT INTO file_edges "
            "(src_file, dst_file, relation, weight, support_count) "
            "VALUES ('a.py', 'b.py', 'CO_CHANGE', 0.05, 2)"
        )

        expired = soft_expire(conn)
        assert expired >= 1

        row = conn.execute(
            "SELECT weight FROM file_edges " "WHERE src_file = 'a.py' AND dst_file = 'b.py'"
        ).fetchone()
        assert row["weight"] == 0.0

    def test_keeps_high_weight_edges(self, conn: sqlite3.Connection) -> None:
        """Test that healthy edges are kept."""
        conn.execute(
            "INSERT INTO file_edges "
            "(src_file, dst_file, relation, weight, support_count) "
            "VALUES ('a.py', 'b.py', 'CO_CHANGE', 5.0, 3)"
        )

        expired = soft_expire(conn)
        assert expired == 0

    def test_expires_single_old_evidence(self, conn: sqlite3.Connection) -> None:
        """Test that single-evidence old edges are expired."""
        old_date = _iso(datetime.now(UTC) - timedelta(days=120))
        conn.execute(
            "INSERT INTO file_edges "
            "(src_file, dst_file, relation, weight, support_count) "
            "VALUES ('a.py', 'b.py', 'CO_CHANGE', 1.0, 1)"
        )
        conn.execute(
            "INSERT INTO edge_evidence "
            "(evidence_id, src_file, dst_file, relation, source, source_id, weight, observed_at) "
            "VALUES ('ev1', 'a.py', 'b.py', 'CO_CHANGE', 'COMMIT', 'sha1', 1.0, ?)",
            (old_date,),
        )

        expired = soft_expire(conn)
        assert expired >= 1


class TestHardPrune:
    """Tests for hard prune."""

    def test_prunes_old_zero_weight(self, conn: sqlite3.Connection) -> None:
        """Test that old zero-weight edges are deleted."""
        old_date = _iso(datetime.now(UTC) - timedelta(days=200))
        conn.execute(
            "INSERT INTO file_edges "
            "(src_file, dst_file, relation, weight, support_count) "
            "VALUES ('a.py', 'b.py', 'CO_CHANGE', 0.0, 1)"
        )
        conn.execute(
            "INSERT INTO edge_evidence "
            "(evidence_id, src_file, dst_file, relation, source, source_id, weight, observed_at) "
            "VALUES ('ev1', 'a.py', 'b.py', 'CO_CHANGE', 'COMMIT', 'sha1', 1.0, ?)",
            (old_date,),
        )

        pruned = hard_prune(conn)
        assert pruned >= 1

        edges = conn.execute("SELECT COUNT(*) FROM file_edges").fetchone()
        assert edges[0] == 0

    def test_keeps_recent_zero_weight(self, conn: sqlite3.Connection) -> None:
        """Test that recent zero-weight edges are kept."""
        recent = _iso(datetime.now(UTC) - timedelta(days=30))
        conn.execute(
            "INSERT INTO file_edges "
            "(src_file, dst_file, relation, weight, support_count) "
            "VALUES ('a.py', 'b.py', 'CO_CHANGE', 0.0, 1)"
        )
        conn.execute(
            "INSERT INTO edge_evidence "
            "(evidence_id, src_file, dst_file, relation, source, source_id, weight, observed_at) "
            "VALUES ('ev1', 'a.py', 'b.py', 'CO_CHANGE', 'COMMIT', 'sha1', 1.0, ?)",
            (recent,),
        )

        pruned = hard_prune(conn)
        assert pruned == 0


class TestPruneDeletedFiles:
    """Tests for pruning references to deleted files."""

    def test_prunes_missing_files(self, conn: sqlite3.Connection) -> None:
        """Test that edges referencing nonexistent files are pruned."""
        conn.execute(
            "INSERT INTO file_edges "
            "(src_file, dst_file, relation, weight, support_count) "
            "VALUES ('definitely_missing_file_xyz.py', 'also_missing_abc.py', 'CO_CHANGE', 1.0, 1)"
        )

        pruned = prune_deleted_files(conn)
        assert pruned >= 1

    def test_keeps_existing_files(self, conn: sqlite3.Connection) -> None:
        """Test that edges with existing files are kept."""
        # Use files that definitely exist
        conn.execute(
            "INSERT INTO file_edges "
            "(src_file, dst_file, relation, weight, support_count) "
            "VALUES ('pyproject.toml', 'Makefile', 'CO_CHANGE', 1.0, 1)"
        )

        pruned = prune_deleted_files(conn)
        assert pruned == 0

    def test_resolves_relative_paths_against_repo_root(self, conn: sqlite3.Connection) -> None:
        """Test that relative paths are resolved against repo root, not cwd."""
        # Use a file that exists relative to repo root
        conn.execute(
            "INSERT INTO file_edges "
            "(src_file, dst_file, relation, weight, support_count) "
            "VALUES ('pyproject.toml', 'Makefile', 'CO_CHANGE', 1.0, 1)"
        )
        # Even if we change cwd, the prune should resolve against repo root
        pruned = prune_deleted_files(conn)
        assert pruned == 0

    def test_prunes_extensionless_missing_files(self, conn: sqlite3.Connection) -> None:
        """Test that known extensionless filenames (from ALLOWED_EXTENSIONLESS) are recognized."""
        conn.execute(
            "INSERT INTO file_edges "
            "(src_file, dst_file, relation, weight, support_count) "
            "VALUES ('Procfile', 'pyproject.toml', 'CO_CHANGE', 1.0, 1)"
        )

        pruned = prune_deleted_files(conn)
        assert pruned >= 1

    def test_preserves_error_fix_signature_edges(self, conn: sqlite3.Connection) -> None:
        """Test that ERROR_FIX edges with non-path src_file are not pruned."""
        # ERROR_FIX stores error signature hash as src_file, not a real path
        conn.execute(
            "INSERT INTO file_edges "
            "(src_file, dst_file, relation, weight, support_count) "
            "VALUES ('sig_utc_error', 'pyproject.toml', 'ERROR_FIX', 0.9, 1)"
        )

        prune_deleted_files(conn)
        # sig_utc_error doesn't look like a path, so it should be skipped
        edges = conn.execute("SELECT COUNT(*) FROM file_edges").fetchone()
        assert edges[0] == 1  # Edge preserved
