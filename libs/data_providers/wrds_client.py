"""
WRDS database client with connection pooling and rate limiting.

This module provides:
- WRDSConfig: Connection configuration with secrets integration
- WRDSClient: Database client with pooling, rate limiting, and retry logic

WRDS (Wharton Research Data Services) provides access to academic
financial databases including CRSP, Compustat, and others.
"""

from __future__ import annotations

import datetime
import logging
import threading
import time
from typing import TYPE_CHECKING, Any

import polars as pl
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import QueuePool

from libs.secrets import SecretManager, create_secret_manager

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class WRDSConfig(BaseSettings):
    """WRDS connection configuration.

    Credentials are loaded from secrets manager using paths:
    - wrds/username
    - wrds/password

    Environment variables can override settings:
    - WRDS_HOST, WRDS_PORT, WRDS_DATABASE, etc.
    """

    model_config = SettingsConfigDict(
        env_prefix="WRDS_",
        case_sensitive=False,
    )

    # Connection settings
    host: str = "wrds-pgdata.wharton.upenn.edu"
    port: int = 9737
    database: str = "wrds"

    # Pool settings
    pool_size: int = 3
    max_overflow: int = 2
    pool_timeout: int = 30
    pool_recycle: int = 1800  # Recycle connections every 30 min

    # Query settings
    query_timeout_seconds: int = 300

    # Rate limiting
    rate_limit_queries_per_minute: int = 10

    # Retry settings
    max_retries: int = 3
    retry_backoff_factor: float = 2.0


class WRDSClient:
    """WRDS database client with connection pooling and rate limiting.

    Features:
    - Connection pooling via SQLAlchemy QueuePool
    - Token bucket rate limiting (QPM configurable)
    - Exponential backoff retry for transient errors
    - Query timeout with cancellation
    - Credential loading from secrets manager

    Example:
        config = WRDSConfig()
        client = WRDSClient(config)
        client.connect()

        df = client.execute_query("SELECT * FROM crsp.dsf LIMIT 100")
        client.close()

    Thread Safety:
        This client is thread-safe. Multiple threads can execute queries
        concurrently, subject to rate limiting.
    """

    # Transient errors that should trigger retry
    _TRANSIENT_ERRORS = (
        "connection refused",
        "connection reset",
        "timeout",
        "temporary failure",
    )

    def __init__(
        self,
        config: WRDSConfig,
        secret_manager: SecretManager | None = None,
    ) -> None:
        """Initialize WRDS client.

        Args:
            config: Connection configuration.
            secret_manager: Optional secrets manager. Defaults to factory.
        """
        self.config = config
        self._secret_manager = secret_manager or create_secret_manager()
        self._engine: Engine | None = None

        # Rate limiting state
        self._rate_lock = threading.Lock()
        self._query_times: list[float] = []

        # Credential cache
        self._username: str | None = None
        self._password: SecretStr | None = None
        self._credential_expires: datetime.datetime | None = None

    def connect(self) -> None:
        """Establish connection pool to WRDS.

        Creates SQLAlchemy engine with connection pooling.
        Credentials are loaded from secrets manager.
        """
        username, password = self._get_credentials()

        # Use URL.create to properly handle special characters in credentials
        connection_url = URL.create(
            drivername="postgresql",
            username=username,
            password=password.get_secret_value(),
            host=self.config.host,
            port=self.config.port,
            database=self.config.database,
        )

        self._engine = create_engine(
            connection_url,
            poolclass=QueuePool,
            pool_size=self.config.pool_size,
            max_overflow=self.config.max_overflow,
            pool_timeout=self.config.pool_timeout,
            pool_recycle=self.config.pool_recycle,
            connect_args={
                "connect_timeout": 30,
                "options": f"-c statement_timeout={self.config.query_timeout_seconds * 1000}",
                # Enforce SSL to protect credentials in transit
                "sslmode": "require",
            },
        )

        # Test connection
        with self._engine.connect() as conn:
            conn.execute(text("SELECT 1"))

        logger.info(
            "WRDS connection pool established",
            extra={
                "event": "wrds.connected",
                "host": self.config.host,
                "pool_size": self.config.pool_size,
            },
        )

    def close(self) -> None:
        """Close connection pool."""
        if self._engine:
            self._engine.dispose()
            self._engine = None
            logger.info("WRDS connection pool closed")

    def execute_query(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> pl.DataFrame:
        """Execute SQL query and return results as Polars DataFrame.

        Applies rate limiting and retry logic.

        Args:
            sql: SQL query string.
            params: Optional query parameters.

        Returns:
            Query results as Polars DataFrame.

        Raises:
            RuntimeError: If not connected.
            SQLAlchemyError: If query fails after retries.
        """
        if not self._engine:
            raise RuntimeError("Not connected. Call connect() first.")

        # Apply rate limiting
        self._rate_limit()

        # Execute with retry
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries):
            try:
                return self._execute_query_internal(sql, params)
            except OperationalError as e:
                if self._is_transient_error(e):
                    last_error = e
                    backoff = self.config.retry_backoff_factor**attempt
                    logger.warning(
                        f"Transient error, retrying in {backoff}s",
                        extra={
                            "event": "wrds.retry",
                            "attempt": attempt + 1,
                            "error": str(e),
                        },
                    )
                    time.sleep(backoff)
                else:
                    raise

        # All retries exhausted
        raise last_error or RuntimeError("Query failed after retries")

    def get_table_info(self, schema: str, table: str) -> dict[str, Any]:
        """Get table metadata from WRDS.

        Args:
            schema: Schema name (e.g., "crsp").
            table: Table name (e.g., "dsf").

        Returns:
            Dict with table info including columns and row count estimate.
        """
        # Get columns
        col_sql = """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = :schema AND table_name = :table
            ORDER BY ordinal_position
        """
        cols_df = self.execute_query(col_sql, {"schema": schema, "table": table})

        # Get row count estimate
        count_sql = """
            SELECT reltuples::bigint as estimate
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = :schema AND c.relname = :table
        """
        count_df = self.execute_query(count_sql, {"schema": schema, "table": table})

        return {
            "schema": schema,
            "table": table,
            "columns": cols_df.to_dicts(),
            "row_count_estimate": count_df["estimate"][0] if len(count_df) > 0 else 0,
        }

    def check_credential_expiry(self) -> tuple[bool, int | None]:
        """Check if credentials are expiring soon.

        Returns:
            Tuple of (is_expiring_soon, days_until_expiry).
            - is_expiring_soon: Always False (expiry check not implemented)
            - days_until_expiry: None (not available from WRDS API)

        Note:
            WRDS does not expose credential expiry via API. Users must monitor
            their WRDS account page directly for renewal deadlines.
            This method returns sentinel values to indicate "unknown" status.

        Warning:
            Do NOT rely on this method for monitoring. Set up external
            calendar reminders for WRDS subscription renewal dates.
        """
        # WRDS does not expose credential expiry via any public API.
        # Web scraping the account page would be fragile and potentially
        # violate terms of service. Return explicit "unknown" values.
        logger.info(
            "Credential expiry check not available - WRDS API limitation",
            extra={
                "event": "sync.credential.check_unavailable",
                "recommendation": "Monitor WRDS account page directly",
            },
        )

        # Return (False, None) to indicate "not expiring" with "unknown" days
        # Callers should check for None and handle appropriately
        return False, None

    def _get_credentials(self) -> tuple[str, SecretStr]:
        """Load credentials from secrets manager.

        Returns:
            Tuple of (username, password).
        """
        # Check cache
        now = datetime.datetime.now(datetime.UTC)
        if (
            self._username
            and self._password
            and self._credential_expires
            and now < self._credential_expires
        ):
            return self._username, self._password

        # Load from secrets manager
        self._username = self._secret_manager.get_secret("wrds/username")
        password_str = self._secret_manager.get_secret("wrds/password")
        self._password = SecretStr(password_str)

        # Cache for 1 hour (matches SecretCache TTL)
        self._credential_expires = now + datetime.timedelta(hours=1)

        return self._username, self._password

    # PostgreSQL type OID to Polars dtype mapping for empty result schema preservation
    # Use type objects directly - Polars accepts both types and instances for schema
    _PG_TYPE_MAP: dict[int, type[pl.DataType]] = {
        16: pl.Boolean,  # bool
        20: pl.Int64,  # int8
        21: pl.Int16,  # int2
        23: pl.Int32,  # int4
        700: pl.Float32,  # float4
        701: pl.Float64,  # float8
        1082: pl.Date,  # date
        1114: pl.Datetime,  # timestamp
        1184: pl.Datetime,  # timestamptz
        1700: pl.Float64,  # numeric (use Float64 for simplicity)
    }

    # Chunk size for streaming query results to avoid OOM on large partitions
    FETCH_CHUNK_SIZE = 50_000

    def _execute_query_internal(
        self,
        sql: str,
        params: dict[str, Any] | None,
    ) -> pl.DataFrame:
        """Execute query with chunked fetching to avoid memory exhaustion.

        Uses server-side cursors and fetches in chunks to keep memory bounded
        for large result sets (multi-million row WRDS partitions).

        Args:
            sql: SQL query.
            params: Query parameters.

        Returns:
            Results as Polars DataFrame.
        """
        assert self._engine is not None

        # Use server-side cursor with streaming for large result sets
        with self._engine.connect() as conn:
            # Enable server-side cursor via execution options
            result = conn.execution_options(
                stream_results=True,
                yield_per=self.FETCH_CHUNK_SIZE,
            ).execute(text(sql), params or {})

            columns = list(result.keys())
            chunks: list[pl.DataFrame] = []

            # Fetch and process in chunks to avoid loading all rows at once
            while True:
                rows = result.fetchmany(self.FETCH_CHUNK_SIZE)
                if not rows:
                    break
                # Build chunk DataFrame using native row-oriented constructor
                chunk_df = pl.DataFrame(rows, schema=columns, orient="row")
                chunks.append(chunk_df)

            # Handle empty results - preserve proper dtypes from cursor metadata
            if not chunks:
                cursor = result.cursor
                if cursor and hasattr(cursor, "description") and cursor.description:
                    schema = {}
                    for col, desc in zip(columns, cursor.description, strict=True):
                        # desc[1] is type_code from PEP-249; cast to int for OID lookup
                        raw_oid = desc[1] if len(desc) > 1 else None
                        type_oid = int(raw_oid) if raw_oid is not None else None  # type: ignore[call-overload]
                        dtype = self._PG_TYPE_MAP.get(type_oid) if type_oid else None
                        schema[col] = dtype if dtype is not None else pl.Utf8
                    return pl.DataFrame(schema=schema)
                return pl.DataFrame(schema={col: pl.Utf8 for col in columns})

        # Concatenate all chunks into final DataFrame
        # Use rechunk=False to avoid copying data during concat
        if len(chunks) == 1:
            return chunks[0]
        return pl.concat(chunks, rechunk=False)

    def _rate_limit(self) -> None:
        """Apply token bucket rate limiting.

        Blocks if query rate exceeds configured QPM. Releases lock during sleep
        to allow other threads to proceed or check the window.
        """
        while True:
            sleep_time = 0.0
            with self._rate_lock:
                now = time.monotonic()
                window_start = now - 60.0  # 1 minute window

                # Remove queries outside window
                self._query_times = [t for t in self._query_times if t > window_start]

                # Check if at limit
                if len(self._query_times) >= self.config.rate_limit_queries_per_minute:
                    # Calculate wait until oldest query exits window
                    sleep_time = self._query_times[0] - window_start
                else:
                    # Under limit - record this query and proceed
                    self._query_times.append(time.monotonic())
                    return

            # Sleep outside of lock to allow other threads to proceed
            if sleep_time > 0:
                logger.debug(
                    "Rate limited, sleeping %.2fs",
                    sleep_time,
                    extra={"event": "wrds.rate_limited"},
                )
                time.sleep(sleep_time)
                # Loop back to re-check after sleep

    def _is_transient_error(self, error: Exception) -> bool:
        """Check if error is transient and should be retried.

        Args:
            error: The exception that occurred.

        Returns:
            True if error is transient.
        """
        error_str = str(error).lower()
        return any(msg in error_str for msg in self._TRANSIENT_ERRORS)

    def __enter__(self) -> WRDSClient:
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Context manager exit."""
        self.close()
