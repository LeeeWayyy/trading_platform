"""Tests for SQL validator security rules."""

from __future__ import annotations

import pytest

sql_validator_module = pytest.importorskip(
    "libs.web_console_services.sql_validator",
    reason="SQL validator module not available yet",
)

SQLValidator = sql_validator_module.SQLValidator


@pytest.fixture()
def validator() -> SQLValidator:
    return SQLValidator()


class TestBasicValidation:
    """Test basic validation functionality."""

    def test_empty_query_rejected(self, validator: SQLValidator) -> None:
        valid, error = validator.validate("", "crsp")
        assert not valid
        assert error is not None
        assert "empty" in error.lower()

    def test_multi_statement_rejected(self, validator: SQLValidator) -> None:
        valid, error = validator.validate("SELECT 1; SELECT 2", "crsp")
        assert not valid
        assert "multi-statement" in error.lower()

    def test_unknown_dataset_rejected(self, validator: SQLValidator) -> None:
        valid, error = validator.validate("SELECT * FROM foo", "unknown_dataset")
        assert not valid
        assert "unknown dataset" in error.lower()

    def test_valid_select_accepted(self, validator: SQLValidator) -> None:
        valid, error = validator.validate("SELECT * FROM crsp_daily", "crsp")
        assert valid
        assert error is None


class TestTableAccess:
    """Test dataset-level table access control."""

    def test_cross_dataset_table_rejected(self, validator: SQLValidator) -> None:
        """Query for CRSP dataset should not allow TAQ tables."""
        valid, error = validator.validate("SELECT * FROM taq_trades", "crsp")
        assert not valid
        assert "taq_trades" in error
        assert "not allowed" in error.lower()

    def test_allowed_table_accepted(self, validator: SQLValidator) -> None:
        """Query for CRSP dataset should allow CRSP tables."""
        valid, error = validator.validate("SELECT * FROM crsp_daily", "crsp")
        assert valid

    def test_schema_qualified_table_rejected(self, validator: SQLValidator) -> None:
        """Schema-qualified tables (schema.table) should be rejected."""
        valid, error = validator.validate("SELECT * FROM other_schema.crsp_daily", "crsp")
        assert not valid
        assert "schema-qualified" in error.lower()

    def test_catalog_qualified_table_rejected(self, validator: SQLValidator) -> None:
        """Catalog-qualified tables (catalog.schema.table) should be rejected."""
        valid, error = validator.validate(
            "SELECT * FROM other_catalog.other_schema.crsp_daily", "crsp"
        )
        assert not valid
        assert "schema-qualified" in error.lower()

    def test_cte_names_not_rejected(self, validator: SQLValidator) -> None:
        """CTEs should not be confused with table references."""
        query = """
        WITH temp_data AS (SELECT * FROM crsp_daily)
        SELECT * FROM temp_data
        """
        valid, error = validator.validate(query, "crsp")
        assert valid


class TestBlockedFunctions:
    """Test blocked function detection."""

    @pytest.mark.parametrize(
        "func",
        [
            "read_parquet",
            "read_csv",
            "read_csv_auto",
            "read_json",
            "read_json_auto",
            "read_text",
            "read_xlsx",
            "read_blob",
            "parquet_scan",
            "csv_scan",
            "json_scan",
            "sqlite_scan",
            "sqlite_attach",
            "excel_scan",
            "iceberg_scan",
            "delta_scan",
            "glob",
            "list_files",
        ],
    )
    def test_file_access_functions_blocked(self, validator: SQLValidator, func: str) -> None:
        """File access functions should be blocked."""
        query = f"SELECT * FROM {func}('/path/to/file')"
        valid, error = validator.validate(query, "crsp")
        assert not valid
        assert "blocked function" in error.lower()

    @pytest.mark.parametrize(
        "func",
        ["httpfs_open", "s3_list", "http_get", "azure_blob", "gcs_read"],
    )
    def test_remote_access_functions_blocked(self, validator: SQLValidator, func: str) -> None:
        """Remote access functions should be blocked."""
        query = f"SELECT * FROM {func}('https://example.com')"
        valid, error = validator.validate(query, "crsp")
        assert not valid
        assert "blocked function" in error.lower()


class TestDisallowedOperations:
    """Test that DML/DDL operations are rejected."""

    def test_insert_rejected(self, validator: SQLValidator) -> None:
        valid, error = validator.validate("INSERT INTO crsp_daily VALUES (1)", "crsp")
        assert not valid

    def test_update_rejected(self, validator: SQLValidator) -> None:
        valid, error = validator.validate("UPDATE crsp_daily SET x = 1", "crsp")
        assert not valid

    def test_delete_rejected(self, validator: SQLValidator) -> None:
        valid, error = validator.validate("DELETE FROM crsp_daily", "crsp")
        assert not valid

    def test_create_rejected(self, validator: SQLValidator) -> None:
        valid, error = validator.validate("CREATE TABLE foo (x INT)", "crsp")
        assert not valid

    def test_drop_rejected(self, validator: SQLValidator) -> None:
        valid, error = validator.validate("DROP TABLE crsp_daily", "crsp")
        assert not valid


class TestRowLimit:
    """Test row limit enforcement."""

    def test_adds_limit_when_missing(self, validator: SQLValidator) -> None:
        result = validator.enforce_row_limit("SELECT * FROM crsp_daily", max_rows=100)
        assert "LIMIT 100" in result.upper()

    def test_clamps_existing_limit(self, validator: SQLValidator) -> None:
        result = validator.enforce_row_limit("SELECT * FROM crsp_daily LIMIT 99999", max_rows=100)
        assert "LIMIT 100" in result.upper()

    def test_preserves_smaller_limit(self, validator: SQLValidator) -> None:
        result = validator.enforce_row_limit("SELECT * FROM crsp_daily LIMIT 50", max_rows=100)
        assert "LIMIT 50" in result.upper()

    def test_preserves_offset_when_clamping(self, validator: SQLValidator) -> None:
        """OFFSET should be preserved when LIMIT is clamped."""
        result = validator.enforce_row_limit(
            "SELECT * FROM crsp_daily LIMIT 20000 OFFSET 1000", max_rows=100
        )
        assert "LIMIT 100" in result.upper()
        assert "OFFSET 1000" in result.upper()

    def test_invalid_max_rows_raises(self, validator: SQLValidator) -> None:
        with pytest.raises(ValueError, match="positive"):
            validator.enforce_row_limit("SELECT 1", max_rows=0)


class TestExtractTables:
    """Test table extraction functionality."""

    def test_extracts_single_table(self, validator: SQLValidator) -> None:
        tables = validator.extract_tables("SELECT * FROM crsp_daily")
        assert tables == ["crsp_daily"]

    def test_extracts_multiple_tables(self, validator: SQLValidator) -> None:
        tables = validator.extract_tables("SELECT * FROM crsp_daily JOIN crsp_monthly ON 1=1")
        assert set(tables) == {"crsp_daily", "crsp_monthly"}

    def test_excludes_cte_names(self, validator: SQLValidator) -> None:
        query = """
        WITH temp AS (SELECT * FROM crsp_daily)
        SELECT * FROM temp
        """
        tables = validator.extract_tables(query)
        assert tables == ["crsp_daily"]
