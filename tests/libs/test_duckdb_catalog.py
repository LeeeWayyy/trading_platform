"""Unit tests for libs.duckdb_catalog with mocked DuckDB connections."""

from __future__ import annotations

from unittest.mock import Mock

import pandas as pd
import polars as pl
import pytest

from libs.duckdb_catalog import (
    DuckDBCatalog,
    calculate_returns,
    calculate_sma,
)


@pytest.fixture()
def mock_conn() -> Mock:
    conn = Mock()
    conn.execute = Mock()
    conn.close = Mock()
    return conn


@pytest.fixture()
def mock_connect(monkeypatch: pytest.MonkeyPatch, mock_conn: Mock) -> Mock:
    connect_mock = Mock(return_value=mock_conn)
    monkeypatch.setattr("libs.duckdb_catalog.duckdb.connect", connect_mock)
    return connect_mock


@pytest.fixture()
def catalog(mock_connect: Mock) -> DuckDBCatalog:
    return DuckDBCatalog()


@pytest.fixture()
def polars_df() -> pl.DataFrame:
    return pl.DataFrame({"symbol": ["AAPL"], "date": ["2024-01-01"]})


@pytest.fixture()
def pandas_df() -> pd.DataFrame:
    return pd.DataFrame({"symbol": ["AAPL"], "date": ["2024-01-01"]})


@pytest.fixture()
def mock_result(polars_df: pl.DataFrame, pandas_df: pd.DataFrame) -> Mock:
    result = Mock()
    result.pl.return_value = polars_df
    result.df.return_value = pandas_df
    return result


@pytest.fixture()
def catalog_with_result(
    catalog: DuckDBCatalog, mock_conn: Mock, mock_result: Mock
) -> DuckDBCatalog:
    mock_conn.execute.return_value = mock_result
    return catalog


class TestDuckDBCatalogInit:
    def test_init_creates_in_memory_connection(self, mock_connect: Mock, mock_conn: Mock) -> None:
        catalog = DuckDBCatalog()

        mock_connect.assert_called_once_with(":memory:", read_only=False)
        assert catalog.conn is mock_conn
        assert catalog._registered_tables == {}

    def test_init_rejects_read_only_in_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        connect_mock = Mock()
        monkeypatch.setattr("libs.duckdb_catalog.duckdb.connect", connect_mock)

        with pytest.raises(ValueError, match="In-memory DuckDB databases cannot be opened"):
            DuckDBCatalog(read_only=True)

        connect_mock.assert_not_called()


class TestDuckDBCatalogRegisterTable:
    @pytest.mark.parametrize("table_name", ["", "123bad", "bad-name"])
    def test_register_table_invalid_name_raises(
        self, catalog: DuckDBCatalog, table_name: str
    ) -> None:
        with pytest.raises(ValueError, match="Invalid table name"):
            catalog.register_table(table_name, "data/market.parquet")

    def test_register_table_single_path(self, catalog: DuckDBCatalog, mock_conn: Mock) -> None:
        catalog.register_table("market_data", "data/market.parquet")

        sql = mock_conn.execute.call_args[0][0]
        assert "CREATE OR REPLACE VIEW market_data" in sql
        assert "read_parquet('data/market.parquet')" in sql
        assert catalog._registered_tables["market_data"] == "data/market.parquet"

    def test_register_table_multiple_paths(self, catalog: DuckDBCatalog, mock_conn: Mock) -> None:
        catalog.register_table("market_data", ["data/a.parquet", "data/b.parquet"])

        sql = mock_conn.execute.call_args[0][0]
        assert "read_parquet(['data/a.parquet', 'data/b.parquet'])" in sql
        assert catalog._registered_tables["market_data"] == "data/a.parquet, data/b.parquet"


class TestDuckDBCatalogQuery:
    def test_query_with_params_polars(
        self,
        catalog_with_result: DuckDBCatalog,
        mock_conn: Mock,
        mock_result: Mock,
        polars_df: pl.DataFrame,
    ) -> None:
        result = catalog_with_result.query("SELECT * FROM table WHERE id = ?", params=[1])

        mock_conn.execute.assert_called_once_with("SELECT * FROM table WHERE id = ?", [1])
        assert result is polars_df
        mock_result.pl.assert_called_once_with()

    def test_query_without_params_pandas(
        self,
        catalog_with_result: DuckDBCatalog,
        mock_conn: Mock,
        mock_result: Mock,
        pandas_df: pd.DataFrame,
    ) -> None:
        result = catalog_with_result.query("SELECT * FROM table", return_format="pandas")

        mock_conn.execute.assert_called_once_with("SELECT * FROM table")
        assert result is pandas_df
        mock_result.df.assert_called_once_with()

    def test_query_invalid_return_format(self, catalog_with_result: DuckDBCatalog) -> None:
        with pytest.raises(ValueError, match="Invalid return_format"):
            catalog_with_result.query("SELECT 1", return_format="arrow")


class TestDuckDBCatalogHelpers:
    def test_get_symbols_returns_sorted_list(self, catalog: DuckDBCatalog, mock_conn: Mock) -> None:
        catalog._registered_tables["market_data"] = "data/market.parquet"
        symbols_df = pl.DataFrame({"symbol": ["AAPL", "MSFT"]})
        catalog.query = Mock(return_value=symbols_df)

        symbols = catalog.get_symbols()

        catalog.query.assert_called_once_with(
            "SELECT DISTINCT symbol FROM market_data ORDER BY symbol"
        )
        assert symbols == ["AAPL", "MSFT"]

    def test_get_symbols_unregistered_raises(self, catalog: DuckDBCatalog) -> None:
        with pytest.raises(ValueError, match="not registered"):
            catalog.get_symbols("missing")

    def test_get_date_range(self, catalog: DuckDBCatalog) -> None:
        catalog._registered_tables["market_data"] = "data/market.parquet"
        dates_df = pl.DataFrame({"min_date": ["2024-01-01"], "max_date": ["2024-12-31"]})
        catalog.query = Mock(return_value=dates_df)

        result = catalog.get_date_range()

        catalog.query.assert_called_once_with(
            "SELECT MIN(date) AS min_date, MAX(date) AS max_date FROM market_data"
        )
        assert result == ("2024-01-01", "2024-12-31")

    def test_get_date_range_unregistered_raises(self, catalog: DuckDBCatalog) -> None:
        with pytest.raises(ValueError, match="not registered"):
            catalog.get_date_range("missing")

    def test_get_stats_returns_polars_df(self, catalog: DuckDBCatalog) -> None:
        catalog._registered_tables["market_data"] = "data/market.parquet"
        stats_df = pl.DataFrame({"row_count": [1], "n_symbols": [1]})
        catalog.query = Mock(return_value=stats_df)

        result = catalog.get_stats()

        catalog.query.assert_called_once()
        sql_arg = catalog.query.call_args[0][0]
        assert "COUNT(*) AS row_count" in sql_arg
        assert "COUNT(DISTINCT symbol) AS n_symbols" in sql_arg
        assert catalog.query.call_args.kwargs["return_format"] == "polars"
        assert result is stats_df


class TestDuckDBCatalogLifecycle:
    def test_close_calls_connection_close(self, catalog: DuckDBCatalog, mock_conn: Mock) -> None:
        catalog.close()
        mock_conn.close.assert_called_once_with()

    def test_context_manager_closes(self, catalog: DuckDBCatalog) -> None:
        catalog.close = Mock()
        with catalog as ctx:
            assert ctx is catalog
        catalog.close.assert_called_once_with()

    def test_repr(self, catalog: DuckDBCatalog) -> None:
        assert repr(catalog) == "DuckDBCatalog(no tables registered)"

        catalog._registered_tables["market_data"] = "data/market.parquet"
        assert repr(catalog) == "DuckDBCatalog(tables: market_data)"


class TestDuckDBCatalogAnalytics:
    def test_calculate_returns_executes_parameterized_query(
        self, catalog: DuckDBCatalog, mock_conn: Mock, mock_result: Mock, polars_df: pl.DataFrame
    ) -> None:
        catalog._registered_tables["market_data"] = "data/market.parquet"
        mock_conn.execute.return_value = mock_result

        result = calculate_returns(
            catalog,
            "AAPL",
            start_date="2024-01-01",
            end_date="2024-01-31",
            table_name="market_data",
        )

        sql, params = mock_conn.execute.call_args[0]
        assert "FROM market_data" in sql
        assert "LAG(close)" in sql
        assert "symbol = ?" in sql
        assert "date >= ?" in sql
        assert "date <= ?" in sql
        assert params == ["AAPL", "2024-01-01", "2024-01-31"]
        assert result is polars_df

    def test_calculate_returns_unregistered_raises(self, catalog: DuckDBCatalog) -> None:
        with pytest.raises(ValueError, match="not registered"):
            calculate_returns(catalog, "AAPL", table_name="missing")

    def test_calculate_sma_executes_parameterized_query(
        self, catalog: DuckDBCatalog, mock_conn: Mock, mock_result: Mock, polars_df: pl.DataFrame
    ) -> None:
        catalog._registered_tables["market_data"] = "data/market.parquet"
        mock_conn.execute.return_value = mock_result

        result = calculate_sma(
            catalog,
            "AAPL",
            window=5,
            start_date="2024-01-01",
            end_date="2024-01-31",
            table_name="market_data",
        )

        sql, params = mock_conn.execute.call_args[0]
        assert "FROM market_data" in sql
        assert "ROWS BETWEEN 4 PRECEDING" in sql
        assert "sma_5" in sql
        assert "symbol = ?" in sql
        assert "date >= ?" in sql
        assert "date <= ?" in sql
        assert params == ["AAPL", "2024-01-01", "2024-01-31"]
        assert result is polars_df

    def test_calculate_sma_unregistered_raises(self, catalog: DuckDBCatalog) -> None:
        with pytest.raises(ValueError, match="not registered"):
            calculate_sma(catalog, "AAPL", table_name="missing")
