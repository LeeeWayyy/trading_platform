"""DuckDB connection factory for web console data queries."""

from __future__ import annotations

import duckdb


def get_read_only_connection() -> duckdb.DuckDBPyConnection:
    """Create read-only DuckDB connection with security restrictions."""
    conn = duckdb.connect(read_only=True)
    conn.execute("SET enable_external_access = false")
    conn.execute("SET enable_fsst_vectors = false")
    return conn


__all__ = ["get_read_only_connection"]
