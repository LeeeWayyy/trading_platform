"""Tests for DuckDB connection factory."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

from libs.web_console_services.duckdb_connection import get_read_only_connection


@patch("libs.web_console_services.duckdb_connection.duckdb.connect")
def test_get_read_only_connection_sets_restrictions(connect: MagicMock) -> None:
    conn = MagicMock()
    connect.return_value = conn

    result = get_read_only_connection()

    assert result is conn
    connect.assert_called_once_with(read_only=True)
    conn.execute.assert_has_calls(
        [
            call("SET enable_external_access = false"),
            call("SET enable_fsst_vectors = false"),
        ]
    )
