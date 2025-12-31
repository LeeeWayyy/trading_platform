"""Protocol definitions for tax module database access.

Defines AsyncConnectionPool Protocol to decouple from specific database
implementations (psycopg3, asyncpg, etc.).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from types import TracebackType


class AsyncCursor(Protocol):
    """Protocol for async database cursor."""

    async def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        """Execute a query."""
        ...

    async def fetchone(self) -> dict[str, Any] | None:
        """Fetch one row as dict."""
        ...

    async def fetchall(self) -> list[dict[str, Any]]:
        """Fetch all rows as dicts."""
        ...


class AsyncCursorContextManager(Protocol):
    """Protocol for cursor context manager."""

    async def __aenter__(self) -> AsyncCursor:
        """Enter cursor context."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit cursor context."""
        ...


class AsyncConnection(Protocol):
    """Protocol for async database connection."""

    def cursor(
        self,
        *,
        row_factory: Any | None = None,
    ) -> AsyncCursorContextManager:
        """Get a cursor context manager."""
        ...

    async def commit(self) -> None:
        """Commit the transaction."""
        ...


class AsyncConnectionContextManager(Protocol):
    """Protocol for connection context manager."""

    async def __aenter__(self) -> AsyncConnection:
        """Enter connection context."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit connection context."""
        ...


@runtime_checkable
class AsyncConnectionPool(Protocol):
    """Protocol for async connection pool.

    Compatible with psycopg3 AsyncConnectionPool, asyncpg Pool,
    and any similar async database pool implementation.

    Example:
        >>> async with pool.connection() as conn:
        ...     async with conn.cursor(row_factory=dict_row) as cur:
        ...         await cur.execute("SELECT * FROM tax_lots WHERE user_id = %s", (user_id,))
        ...         rows = await cur.fetchall()
    """

    def connection(self) -> AsyncConnectionContextManager:
        """Get a connection context manager."""
        ...


__all__ = [
    "AsyncConnection",
    "AsyncConnectionPool",
    "AsyncCursor",
]
