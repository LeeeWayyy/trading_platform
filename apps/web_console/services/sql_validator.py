"""SQL validation for safe dataset exploration queries."""

from __future__ import annotations

import sqlglot
from sqlglot import exp

DATASET_TABLES: dict[str, list[str]] = {
    "crsp": ["crsp_daily", "crsp_monthly"],
    "compustat": ["compustat_annual", "compustat_quarterly"],
    "fama_french": ["ff_factors_daily", "ff_factors_monthly"],
    "taq": ["taq_trades", "taq_quotes"],
}

BLOCKED_FUNCTIONS: list[str] = [
    # File reading functions (and auto variants)
    "read_parquet",
    "read_parquet_*",
    "read_csv",
    "read_csv_auto",
    "read_json",
    "read_json_auto",
    "read_text",
    "read_xlsx",
    "read_blob",
    # Scan variants (DuckDB alternatives)
    "parquet_scan",
    "csv_scan",
    "json_scan",
    "sqlite_scan",
    "sqlite_attach",
    "excel_scan",
    "iceberg_scan",
    "delta_scan",
    # File system access
    "glob",
    "list_files",
    # Remote access
    "httpfs_*",
    "s3_*",
    "http_*",
    "azure_*",
    "gcs_*",
]


class SQLValidator:
    """Validates SQL queries for safe read-only execution."""

    def validate(self, query: str, allowed_dataset: str) -> tuple[bool, str | None]:
        """Validate query is safe for execution against specified dataset."""
        if not query or not query.strip():
            return False, "Query cannot be empty"
        if ";" in query:
            return False, "Multi-statement queries are not allowed"
        if allowed_dataset not in DATASET_TABLES:
            return False, f"Unknown dataset: {allowed_dataset}"

        try:
            expressions = sqlglot.parse(query, read="duckdb")
        except sqlglot.errors.ParseError as exc:
            return False, f"Invalid SQL: {exc}"

        if len(expressions) != 1:
            return False, "Only single-statement queries are allowed"

        expression = expressions[0]
        if expression is None:
            return False, "Failed to parse query"

        if not self._is_select_statement(expression):
            return False, "Only SELECT statements are allowed"

        if self._has_disallowed_operations(expression):
            return False, "Only read-only SELECT statements are allowed"

        blocked_function = self._first_blocked_function(expression)
        if blocked_function:
            return False, f"Blocked function usage detected: {blocked_function}"

        # Check for schema-qualified tables (security: prevents cross-schema access)
        tables_with_schema = self._extract_tables_with_schema(expression)
        allowed_tables = set(DATASET_TABLES[allowed_dataset])
        for table_name, schema, catalog in tables_with_schema:
            # Reject schema-qualified tables (e.g., other_schema.table)
            if schema or catalog:
                qualified = (
                    f"{catalog}.{schema}.{table_name}" if catalog else f"{schema}.{table_name}"
                )
                return False, f"Schema-qualified tables not allowed: {qualified}"
            if table_name not in allowed_tables:
                return False, f"Table '{table_name}' is not allowed for dataset '{allowed_dataset}'"

        return True, None

    def extract_tables(self, query: str) -> list[str]:
        """Extract all table references from SQL query using sqlglot."""
        expression = sqlglot.parse_one(query, read="duckdb")
        return list(self._extract_tables_from_expression(expression))

    def enforce_row_limit(self, query: str, max_rows: int) -> str:
        """Add or clamp LIMIT clause to enforce a maximum row count."""
        if max_rows <= 0:
            raise ValueError("max_rows must be positive")

        expression = sqlglot.parse_one(query, read="duckdb")
        if not self._is_select_statement(expression):
            raise ValueError("Only SELECT statements can be limited")

        target = expression.this if isinstance(expression, exp.With) else expression
        limit_node = target.args.get("limit")
        if limit_node is None:
            target.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
        else:
            limit_value = self._limit_value(limit_node)
            if limit_value is None or limit_value > max_rows:
                target.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))

        return str(expression.sql(dialect="duckdb"))

    def _extract_tables_with_schema(
        self, expression: exp.Expression
    ) -> list[tuple[str, str | None, str | None]]:
        """Extract tables with schema/catalog info for security validation.

        Returns list of (table_name, schema, catalog) tuples.
        Schema-qualified tables (e.g., other_schema.table) are security risks.
        """
        cte_names = {cte.alias_or_name for cte in expression.find_all(exp.CTE) if cte.alias_or_name}
        tables: list[tuple[str, str | None, str | None]] = []
        for table in expression.find_all(exp.Table):
            name = table.name
            if not name or name in cte_names:
                continue
            schema = table.db if hasattr(table, "db") else None
            catalog = table.catalog if hasattr(table, "catalog") else None
            tables.append((name, schema, catalog))
        return tables

    def _extract_tables_from_expression(self, expression: exp.Expression) -> list[str]:
        """Extract table names only (for backward compatibility)."""
        return [name for name, _, _ in self._extract_tables_with_schema(expression)]

    def _first_blocked_function(self, expression: exp.Expression) -> str | None:
        for func in expression.find_all(exp.Func):
            # For Anonymous functions (unrecognized by sqlglot), use func.name
            # For known functions, use sql_name()
            if isinstance(func, exp.Anonymous):
                name = str(func.name).lower() if func.name else ""
            else:
                name = str(func.sql_name()).lower()  # type: ignore[no-untyped-call]
            if self._is_blocked_function(name):
                return name
        return None

    @staticmethod
    def _is_blocked_function(name: str) -> bool:
        for blocked in BLOCKED_FUNCTIONS:
            if blocked.endswith("*"):
                if name.startswith(blocked[:-1]):
                    return True
            elif name == blocked:
                return True
        return False

    @staticmethod
    def _is_select_statement(expression: exp.Expression) -> bool:
        if isinstance(expression, exp.Select):
            return True
        if isinstance(expression, exp.With):
            return isinstance(expression.this, exp.Select | exp.SetOperation)
        if isinstance(expression, exp.SetOperation):
            return True
        return False

    @staticmethod
    def _has_disallowed_operations(expression: exp.Expression) -> bool:
        disallowed = (
            # DML operations
            exp.Insert,
            exp.Update,
            exp.Delete,
            exp.Merge,
            # DDL operations
            exp.Create,
            exp.Drop,
            exp.Alter,
            # Admin/privilege operations
            exp.Grant,
            exp.Revoke,
            exp.Transaction,
            exp.Command,
            # DuckDB-specific dangerous operations
            exp.Copy,  # COPY TO/FROM file
            exp.LoadData,  # LOAD DATA
            exp.Attach,  # ATTACH database
            exp.Detach,  # DETACH database
            exp.Pragma,  # PRAGMA statements
            exp.Set,  # SET variable
            exp.Use,  # USE database
        )
        return any(expression.find(node) is not None for node in disallowed)

    @staticmethod
    def _limit_value(limit_node: exp.Expression) -> int | None:
        node = limit_node.args.get("expression") or limit_node.args.get("this")
        if isinstance(node, exp.Literal) and node.is_int:
            try:
                return int(node.this)
            except (TypeError, ValueError):
                return None
        return None


__all__ = ["SQLValidator", "DATASET_TABLES", "BLOCKED_FUNCTIONS"]
