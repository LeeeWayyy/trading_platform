"""Tests for SQLValidator."""

from __future__ import annotations

import pytest
import sqlglot

from libs.web_console_services.sql_validator import SQLValidator


@pytest.fixture()
def validator() -> SQLValidator:
    return SQLValidator()


def test_validate_rejects_empty_query(validator: SQLValidator) -> None:
    ok, error = validator.validate("   ", "crsp")
    assert ok is False
    assert error == "Query cannot be empty"


def test_validate_rejects_multi_statement(validator: SQLValidator) -> None:
    ok, error = validator.validate("SELECT 1; SELECT 2", "crsp")
    assert ok is False
    assert error == "Multi-statement queries are not allowed"


def test_validate_rejects_unknown_dataset(validator: SQLValidator) -> None:
    ok, error = validator.validate("SELECT * FROM crsp_daily", "unknown")
    assert ok is False
    assert error == "Unknown dataset: unknown"


def test_validate_rejects_non_select(validator: SQLValidator) -> None:
    ok, error = validator.validate("DELETE FROM crsp_daily", "crsp")
    assert ok is False
    assert error == "Only SELECT statements are allowed"


def test_validate_rejects_blocked_function(validator: SQLValidator) -> None:
    ok, error = validator.validate("SELECT read_csv('file.csv')", "crsp")
    assert ok is False
    assert error is not None
    assert "Blocked function usage detected" in error


def test_validate_rejects_schema_qualified_table(validator: SQLValidator) -> None:
    ok, error = validator.validate("SELECT * FROM other_schema.crsp_daily", "crsp")
    assert ok is False
    assert error == "Schema-qualified tables not allowed: other_schema.crsp_daily"


def test_validate_rejects_table_outside_dataset(validator: SQLValidator) -> None:
    ok, error = validator.validate("SELECT * FROM compustat_annual", "crsp")
    assert ok is False
    assert error == "Table 'compustat_annual' is not allowed for dataset 'crsp'"


def test_validate_accepts_simple_select(validator: SQLValidator) -> None:
    ok, error = validator.validate("SELECT * FROM crsp_daily", "crsp")
    assert ok is True
    assert error is None


def test_extract_tables_ignores_cte(validator: SQLValidator) -> None:
    query = (
        "WITH temp AS (SELECT * FROM crsp_daily) "
        "SELECT * FROM temp JOIN crsp_monthly ON temp.date = crsp_monthly.date"
    )
    tables = validator.extract_tables(query)
    assert set(tables) == {"crsp_daily", "crsp_monthly"}


def test_enforce_row_limit_adds_limit(validator: SQLValidator) -> None:
    query = "SELECT * FROM crsp_daily"
    limited = validator.enforce_row_limit(query, max_rows=100)
    expression = sqlglot.parse_one(limited, read="duckdb")
    limit = expression.args.get("limit")
    assert limit is not None
    assert validator._limit_value(limit) == 100


def test_enforce_row_limit_clamps_and_preserves_offset(validator: SQLValidator) -> None:
    query = "SELECT * FROM crsp_daily LIMIT 1000 OFFSET 5"
    limited = validator.enforce_row_limit(query, max_rows=100)
    expression = sqlglot.parse_one(limited, read="duckdb")
    limit = expression.args.get("limit")
    assert limit is not None
    assert validator._limit_value(limit) == 100
    assert expression.args.get("offset") is not None


def test_enforce_row_limit_keeps_smaller_limit(validator: SQLValidator) -> None:
    query = "SELECT * FROM crsp_daily LIMIT 10"
    limited = validator.enforce_row_limit(query, max_rows=100)
    expression = sqlglot.parse_one(limited, read="duckdb")
    limit = expression.args.get("limit")
    assert limit is not None
    assert validator._limit_value(limit) == 10


def test_enforce_row_limit_rejects_non_select(validator: SQLValidator) -> None:
    with pytest.raises(ValueError, match="Only SELECT statements can be limited"):
        validator.enforce_row_limit("UPDATE crsp_daily SET price = 1", max_rows=10)


def test_enforce_row_limit_rejects_non_positive(validator: SQLValidator) -> None:
    with pytest.raises(ValueError, match="max_rows must be positive"):
        validator.enforce_row_limit("SELECT * FROM crsp_daily", max_rows=0)


def test_has_disallowed_operations_flags_ddl(validator: SQLValidator) -> None:
    expression = sqlglot.parse_one("DROP TABLE foo", read="duckdb")
    assert validator._has_disallowed_operations(expression) is True


def test_has_disallowed_operations_allows_select(validator: SQLValidator) -> None:
    expression = sqlglot.parse_one("SELECT * FROM crsp_daily", read="duckdb")
    assert validator._has_disallowed_operations(expression) is False
