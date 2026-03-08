"""Unit tests for tools.kb.db — schema creation, PRAGMAs, and retry logic."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tools.kb.db import (
    DEFAULT_DB_PATH,
    MAX_RETRIES,
    _with_busy_retry,
    checkpoint,
    commit_with_retry,
    execute_with_retry,
    get_connection,
    init_schema,
    is_lock_error,
    write_deferred,
)


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    """Return a temporary database path."""
    return tmp_path / "test_kb.db"


@pytest.fixture()
def conn(tmp_db: Path) -> sqlite3.Connection:
    """Return a connection with schema initialized."""
    c = get_connection(tmp_db)
    init_schema(c)
    return c


class TestGetConnection:
    """Tests for get_connection."""

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        """Test that parent directories are created automatically."""
        db_path = tmp_path / "sub" / "dir" / "kb.db"
        c = get_connection(db_path)
        assert db_path.parent.exists()
        c.close()

    def test_returns_connection_with_row_factory(self, tmp_db: Path) -> None:
        """Test that connection uses sqlite3.Row factory."""
        c = get_connection(tmp_db)
        assert c.row_factory is sqlite3.Row
        c.close()

    def test_wal_mode_enabled(self, tmp_db: Path) -> None:
        """Test that WAL journal mode is set."""
        c = get_connection(tmp_db)
        mode = c.execute("PRAGMA journal_mode").fetchone()
        assert mode[0] == "wal"
        c.close()

    def test_foreign_keys_enabled(self, tmp_db: Path) -> None:
        """Test that foreign keys are enforced."""
        c = get_connection(tmp_db)
        fk = c.execute("PRAGMA foreign_keys").fetchone()
        assert fk[0] == 1
        c.close()

    def test_busy_timeout_set(self, tmp_db: Path) -> None:
        """Test that busy_timeout is set to 5000ms."""
        c = get_connection(tmp_db)
        timeout = c.execute("PRAGMA busy_timeout").fetchone()
        assert timeout[0] == 5000
        c.close()

    def test_default_path_used(self) -> None:
        """Test that default path is anchored to repo root."""
        assert DEFAULT_DB_PATH.name == "graph.db"
        assert str(DEFAULT_DB_PATH).endswith(".claude/kb/graph.db")
        assert DEFAULT_DB_PATH.is_absolute()


class TestInitSchema:
    """Tests for init_schema."""

    def test_creates_all_tables(self, conn: sqlite3.Connection) -> None:
        """Test that all 9 tables are created."""
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = {row[0] for row in tables}
        expected = {
            "file_edges",
            "edge_evidence",
            "issue_patterns",
            "review_runs",
            "findings",
            "implementation_sessions",
            "test_runs",
            "test_results",
            "error_fixes",
        }
        assert expected.issubset(table_names)

    def test_idempotent(self, conn: sqlite3.Connection) -> None:
        """Test that calling init_schema twice doesn't error."""
        init_schema(conn)  # Second call should be a no-op
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        assert len(tables) >= 9

    def test_file_edges_primary_key(self, conn: sqlite3.Connection) -> None:
        """Test file_edges composite primary key."""
        conn.execute(
            "INSERT INTO file_edges (src_file, dst_file, relation, weight, support_count) "
            "VALUES ('a.py', 'b.py', 'CO_CHANGE', 1.0, 1)"
        )
        # Same key should conflict
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO file_edges (src_file, dst_file, relation, weight, support_count) "
                "VALUES ('a.py', 'b.py', 'CO_CHANGE', 2.0, 2)"
            )

    def test_upsert_works(self, conn: sqlite3.Connection) -> None:
        """Test INSERT ON CONFLICT DO UPDATE for file_edges."""
        conn.execute(
            "INSERT INTO file_edges (src_file, dst_file, relation, weight, support_count) "
            "VALUES ('a.py', 'b.py', 'CO_CHANGE', 1.0, 1) "
            "ON CONFLICT (src_file, dst_file, relation) "
            "DO UPDATE SET weight = excluded.weight, support_count = excluded.support_count"
        )
        conn.execute(
            "INSERT INTO file_edges (src_file, dst_file, relation, weight, support_count) "
            "VALUES ('a.py', 'b.py', 'CO_CHANGE', 3.0, 5) "
            "ON CONFLICT (src_file, dst_file, relation) "
            "DO UPDATE SET weight = excluded.weight, support_count = excluded.support_count"
        )
        row = conn.execute(
            "SELECT weight, support_count FROM file_edges "
            "WHERE src_file='a.py' AND dst_file='b.py'"
        ).fetchone()
        assert row[0] == 3.0
        assert row[1] == 5


class TestExecuteWithRetry:
    """Tests for execute_with_retry."""

    def test_successful_execution(self, conn: sqlite3.Connection) -> None:
        """Test that a simple query succeeds without retry."""
        cursor = execute_with_retry(conn, "SELECT 1")
        assert cursor.fetchone()[0] == 1

    def test_retries_on_locked(self) -> None:
        """Test retry on SQLITE_BUSY."""
        from unittest.mock import Mock

        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn = Mock()
        mock_conn.execute = Mock(
            side_effect=[
                sqlite3.OperationalError("database is locked"),
                sqlite3.OperationalError("database is locked"),
                mock_cursor,
            ]
        )
        cursor = execute_with_retry(mock_conn, "SELECT 1")
        assert cursor.fetchone()[0] == 1
        assert mock_conn.execute.call_count == 3

    def test_raises_after_max_retries(self) -> None:
        """Test that OperationalError is raised after max retries."""
        from unittest.mock import Mock

        mock_conn = Mock()
        mock_conn.execute = Mock(side_effect=sqlite3.OperationalError("database is locked"))
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            execute_with_retry(mock_conn, "SELECT 1")
        assert mock_conn.execute.call_count == MAX_RETRIES

    def test_non_busy_error_raises_immediately(self) -> None:
        """Test that non-BUSY errors are not retried."""
        from unittest.mock import Mock

        mock_conn = Mock()
        mock_conn.execute = Mock(side_effect=sqlite3.OperationalError("near syntax error"))
        with pytest.raises(sqlite3.OperationalError, match="syntax error"):
            execute_with_retry(mock_conn, "INVALID SQL")
        assert mock_conn.execute.call_count == 1


class TestWriteDeferred:
    """Tests for write_deferred."""

    def test_writes_payload(self, tmp_path: Path) -> None:
        """Test that payload is written as JSONL."""
        queue = tmp_path / "deferred.jsonl"
        write_deferred({"action": "commit", "sha": "abc123"}, queue)
        assert queue.exists()
        import json

        data = json.loads(queue.read_text().strip())
        assert data["action"] == "commit"
        assert data["sha"] == "abc123"

    def test_appends_multiple(self, tmp_path: Path) -> None:
        """Test that multiple payloads are appended."""
        queue = tmp_path / "deferred.jsonl"
        write_deferred({"a": 1}, queue)
        write_deferred({"b": 2}, queue)
        lines = queue.read_text().strip().splitlines()
        assert len(lines) == 2


class TestCommitWithRetry:
    """Tests for commit_with_retry."""

    def test_successful_commit(self, conn: sqlite3.Connection) -> None:
        """Test that a normal commit succeeds."""
        conn.execute(
            "INSERT INTO file_edges (src_file, dst_file, relation, weight, support_count) "
            "VALUES ('a.py', 'b.py', 'CO_CHANGE', 1.0, 1)"
        )
        commit_with_retry(conn)
        row = conn.execute("SELECT COUNT(*) FROM file_edges").fetchone()
        assert row[0] == 1

    def test_retries_on_locked(self) -> None:
        """Test retry on SQLITE_BUSY during commit."""
        from unittest.mock import Mock

        mock_conn = Mock()
        mock_conn.commit = Mock(
            side_effect=[
                sqlite3.OperationalError("database is locked"),
                None,
            ]
        )
        commit_with_retry(mock_conn)
        assert mock_conn.commit.call_count == 2

    def test_raises_after_max_retries(self) -> None:
        """Test that OperationalError is raised after max retries."""
        from unittest.mock import Mock

        mock_conn = Mock()
        mock_conn.commit = Mock(side_effect=sqlite3.OperationalError("database is locked"))
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            commit_with_retry(mock_conn)
        assert mock_conn.commit.call_count == MAX_RETRIES


class TestIsLockError:
    """Tests for is_lock_error helper."""

    def test_database_is_locked(self) -> None:
        """Test that SQLITE_BUSY message is detected."""
        assert is_lock_error(sqlite3.OperationalError("database is locked"))

    def test_database_table_is_locked(self) -> None:
        """Test that SQLITE_LOCKED message is detected."""
        assert is_lock_error(sqlite3.OperationalError("database table is locked"))

    def test_database_schema_is_locked(self) -> None:
        """Test that schema-lock variant is detected as transient."""
        assert is_lock_error(sqlite3.OperationalError("database schema is locked: main"))

    def test_non_lock_error(self) -> None:
        """Test that non-lock errors return False."""
        assert not is_lock_error(sqlite3.OperationalError("near syntax error"))


class TestWithBusyRetry:
    """Tests for the shared _with_busy_retry helper."""

    def test_returns_value_on_success(self) -> None:
        """Test that _with_busy_retry returns the function result."""
        result = _with_busy_retry(lambda: 42, "test")
        assert result == 42

    def test_retries_on_busy(self) -> None:
        """Test that _with_busy_retry retries on SQLITE_BUSY."""
        call_count = 0

        def flaky() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise sqlite3.OperationalError("database is locked")
            return "ok"

        result = _with_busy_retry(flaky, "test")
        assert result == "ok"
        assert call_count == 3

    def test_retries_on_table_locked(self) -> None:
        """Test that _with_busy_retry also retries on SQLITE_LOCKED (table locked)."""
        call_count = 0

        def flaky_table() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise sqlite3.OperationalError("database table is locked")
            return "ok"

        result = _with_busy_retry(flaky_table, "test")
        assert result == "ok"
        assert call_count == 2

    def test_raises_non_busy_immediately(self) -> None:
        """Test that non-BUSY errors are not retried."""
        call_count = 0

        def bad() -> str:
            nonlocal call_count
            call_count += 1
            raise sqlite3.OperationalError("near syntax error")

        with pytest.raises(sqlite3.OperationalError, match="syntax error"):
            _with_busy_retry(bad, "test")
        assert call_count == 1

    def test_raises_after_max_retries(self) -> None:
        """Test that OperationalError is raised after max retries."""
        call_count = 0

        def always_locked() -> str:
            nonlocal call_count
            call_count += 1
            raise sqlite3.OperationalError("database is locked")

        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            _with_busy_retry(always_locked, "test")
        assert call_count == MAX_RETRIES


class TestCheckpoint:
    """Tests for checkpoint."""

    def test_checkpoint_runs(self, conn: sqlite3.Connection) -> None:
        """Test that checkpoint executes without error."""
        checkpoint(conn)
