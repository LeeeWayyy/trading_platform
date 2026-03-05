"""Unit tests for tools.kb.promote — pattern promotion and hint generation."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.kb.db import get_connection, init_schema
from tools.kb.promote import (
    HINTS_DIR,
    MIN_CONFIRMATIONS,
    check_and_promote,
    generate_hint_file,
)


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    """Return a connection with schema initialized."""
    c = get_connection(tmp_path / "test.db")
    init_schema(c)
    return c


class TestCheckAndPromote:
    """Tests for check_and_promote."""

    def test_promotes_patterns_above_threshold(
        self, conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Test that patterns with count >= 3 are promoted."""
        conn.execute(
            "INSERT INTO issue_patterns (rule_id, scope_path, count, examples_json) "
            "VALUES ('UTC_NAIVE_DATETIME', 'apps/', 5, ?)",
            (json.dumps(["apps/main.py:42"]),),
        )

        with patch("tools.kb.promote.HINTS_DIR", tmp_path / "hints"):
            generated = check_and_promote(conn)

        assert len(generated) >= 1
        assert any("utc_naive_datetime" in g for g in generated)

    def test_skips_below_threshold(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        """Test that patterns below threshold are not promoted."""
        conn.execute(
            "INSERT INTO issue_patterns (rule_id, scope_path, count) "
            "VALUES ('MISSING_CB_CHECK', 'libs/', 2)"
        )

        with patch("tools.kb.promote.HINTS_DIR", tmp_path / "hints"):
            generated = check_and_promote(conn)

        assert len(generated) == 0

    def test_empty_db(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        """Test graceful handling of empty patterns table."""
        with patch("tools.kb.promote.HINTS_DIR", tmp_path / "hints"):
            generated = check_and_promote(conn)
        assert generated == []


class TestGenerateHintFile:
    """Tests for hint file generation."""

    def test_creates_file(self, tmp_path: Path) -> None:
        """Test that a hint markdown file is created."""
        with patch("tools.kb.promote.HINTS_DIR", tmp_path / "hints"):
            path = generate_hint_file(
                "UTC_NAIVE_DATETIME",
                "apps/",
                5,
                json.dumps(["apps/main.py:42", "apps/service.py:10"]),
            )
        assert path is not None
        assert path.exists()

    def test_file_content(self, tmp_path: Path) -> None:
        """Test that hint file has expected content."""
        with patch("tools.kb.promote.HINTS_DIR", tmp_path / "hints"):
            path = generate_hint_file(
                "UTC_NAIVE_DATETIME",
                "apps/",
                5,
                json.dumps(["apps/main.py:42"]),
            )
        assert path is not None
        content = path.read_text()
        assert "UTC_NAIVE_DATETIME" in content or "Utc Naive Datetime" in content
        assert "apps/" in content
        assert "5 times" in content

    def test_filename_includes_scope(self, tmp_path: Path) -> None:
        """Test that hint filename uses lowercase rule_id with scope slug."""
        with patch("tools.kb.promote.HINTS_DIR", tmp_path / "hints"):
            path = generate_hint_file("MISSING_CB_CHECK", "libs/", 3)
        assert path is not None
        assert path.name == "missing_cb_check_libs.md"

    def test_creates_directory(self, tmp_path: Path) -> None:
        """Test that hints directory is created if it doesn't exist."""
        hints_dir = tmp_path / "new" / "hints"
        with patch("tools.kb.promote.HINTS_DIR", hints_dir):
            path = generate_hint_file("SWALLOWED_EXCEPTION", "apps/", 4)
        assert path is not None
        assert hints_dir.exists()

    def test_min_confirmations(self) -> None:
        """Test that minimum confirmations is 3."""
        assert MIN_CONFIRMATIONS == 3

    def test_hints_dir_is_absolute(self) -> None:
        """Test that HINTS_DIR is anchored to repo root, not cwd-relative."""
        assert HINTS_DIR.is_absolute()
        assert str(HINTS_DIR).endswith(".claude/kb/hints")
