# duckdb_catalog

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `DuckDBCatalog` | read_only | client | In-memory DuckDB catalog for Parquet queries. |
| `DuckDBCatalog.register_table` | name, parquet_path | None | Register Parquet files as a SQL view. |
| `DuckDBCatalog.query` | sql, params, return_format | DataFrame | Execute SQL and return Polars/Pandas. |
| `DuckDBCatalog.get_symbols` | table_name | list[str] | Unique symbols in table. |
| `DuckDBCatalog.get_date_range` | table_name | tuple[str, str] | Min/max dates in table. |
| `DuckDBCatalog.get_stats` | table_name | DataFrame | Row counts, date range, trading days. |
| `calculate_returns` | catalog, symbol, start_date?, end_date?, table_name? | DataFrame | Daily returns (lag-based). |
| `calculate_sma` | catalog, symbol, window?, start_date?, end_date?, table_name? | DataFrame | Simple moving average. |

## Behavioral Contracts
### DuckDBCatalog.register_table(table_name, parquet_path)
**Purpose:** Register Parquet data as a DuckDB view for SQL queries.

**Preconditions:**
- `table_name` is a valid identifier.
- Parquet path(s) resolve to files.

**Postconditions:**
- Table is registered and stored in `_registered_tables`.

**Behavior:**
1. Normalize paths into list.
2. Build DuckDB `read_parquet` SQL.
3. Create or replace view.

**Raises:**
- `ValueError` for invalid table names.
- `FileNotFoundError` if no files match.

### DuckDBCatalog.query(sql, params, return_format)
**Purpose:** Execute SQL safely with optional parameters.

**Preconditions:**
- SQL references registered tables.

**Postconditions:**
- Returns Polars DataFrame by default.

**Behavior:**
1. Execute SQL with parameter binding when `params` provided.
2. Convert result to Polars or Pandas.

**Raises:**
- `duckdb.Error` on invalid SQL.
- `ValueError` for invalid return_format.

### calculate_returns(catalog, symbol, start_date=None, end_date=None, table_name="market_data")
**Purpose:** Compute daily returns for a symbol over an optional date range.

**Preconditions:**
- `table_name` is registered in the catalog.
- `start_date`/`end_date` (if provided) are ISO `YYYY-MM-DD`.

**Postconditions:**
- Returns Polars DataFrame with columns: `symbol`, `date`, `close`, `daily_return`.

**Behavior:**
1. Validate `table_name`.
2. Build parameterized WHERE clause for symbol and optional dates.
3. Use SQL window function to compute returns.

**Raises:**
- `ValueError` if `table_name` is not registered.

### calculate_sma(catalog, symbol, window=20, start_date=None, end_date=None, table_name="market_data")
**Purpose:** Compute simple moving average for a symbol over an optional date range.

**Preconditions:**
- `table_name` is registered in the catalog.
- `window` is a positive integer.
- `start_date`/`end_date` (if provided) are ISO `YYYY-MM-DD`.

**Postconditions:**
- Returns Polars DataFrame with columns: `symbol`, `date`, `close`, `sma_{window}`.

**Behavior:**
1. Validate `table_name`.
2. Build parameterized WHERE clause for symbol and optional dates.
3. Use SQL window function to compute SMA.

**Raises:**
- `ValueError` if `table_name` is not registered.

### Invariants
- Table names are validated to prevent SQL injection.
- Registered tables are tracked in-memory.

### State Machine (if stateful)
```
[Initialized] --> [Table Registered] --> [Query Executed]
      |                   |
      +-------------------+ (register more)
```
- **States:** initialized, table registered, query executed.
- **Transitions:** register additional tables, execute queries.

## Data Flow
```
Parquet files -> DuckDB view -> SQL query -> DataFrame
```
- **Input format:** Parquet file paths and SQL.
- **Output format:** Polars/Pandas DataFrame.
- **Side effects:** In-memory DuckDB views created.

## Usage Examples
### Example 1: Register and query
```python
from libs.duckdb_catalog import DuckDBCatalog

catalog = DuckDBCatalog()
catalog.register_table("market_data", "data/adjusted/*/*.parquet")
result = catalog.query("SELECT symbol, date, close FROM market_data LIMIT 10")
```

### Example 2: Compute returns
```python
from libs.duckdb_catalog import calculate_returns

returns = calculate_returns(catalog, "AAPL", "2024-01-01", "2024-12-31")
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Invalid table name | name with spaces | `ValueError` |
| Empty parquet glob | no files | `FileNotFoundError` |
| Invalid SQL | syntax error | `duckdb.Error` |

## Dependencies
- **Internal:** N/A
- **External:** DuckDB, Polars, Pandas

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `read_only` | No | False | Read-only mode (unsupported for in-memory). |

## Error Handling
- Raises `ValueError` for invalid inputs.
- DuckDB errors propagate for SQL issues.

## Observability (Services only)
### Health Check
- **Endpoint:** N/A
- **Checks:** N/A

### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| N/A | - | - | Library has no metrics. |

## Security
- Validates table names and uses parameterized queries to reduce SQL injection risk.

## Testing
- **Test Files:** `tests/test_duckdb_catalog.py`
- **Run Tests:** `pytest tests/test_duckdb_catalog.py -v`
- **Coverage:** N/A

## Related Specs
- `data_pipeline.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `libs/duckdb_catalog.py`
- **ADRs:** N/A
