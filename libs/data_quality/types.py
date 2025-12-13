"""
Shared types for the data quality framework.

This module defines:
- LockToken: Token proving exclusive lock ownership
- TradingCalendar: Protocol for trading calendar implementations
- ExchangeCalendarAdapter: Adapter for exchange_calendars library
- DiskSpaceStatus: Result of disk space check
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import pandas as pd


@dataclass
class LockToken:
    """
    Token proving exclusive lock ownership.

    Lock file format (JSON at data/locks/{dataset}.lock):
    {
        "pid": 12345,
        "hostname": "worker-01",
        "writer_id": "sync-job-abc123",
        "acquired_at": "2025-01-15T10:30:00+00:00",
        "expires_at": "2025-01-15T14:30:00+00:00"
    }

    Lifecycle:
    1. Acquire: O_CREAT | O_EXCL | O_WRONLY atomically creates lock file
    2. Validate: Check pid/hostname/writer_id match, not expired, mtime fresh
    3. Refresh: Update mtime periodically (every 60s) during long operations
    4. Release: Delete lock file on completion or crash recovery

    Attributes:
        pid: Process ID that acquired the lock.
        hostname: Hostname of the machine holding the lock.
        writer_id: Unique identifier for the writer (e.g., job ID).
        acquired_at: UTC timestamp when lock was acquired.
        expires_at: UTC timestamp when lock expires (max 4 hours).
        lock_path: Path to the lock file.
    """

    pid: int
    hostname: str
    writer_id: str
    acquired_at: datetime.datetime
    expires_at: datetime.datetime
    lock_path: Path

    def is_expired(self) -> bool:
        """Check if lock has expired (past expires_at)."""
        now = datetime.datetime.now(datetime.UTC)
        return now > self.expires_at

    def to_dict(self) -> dict[str, str | int]:
        """Serialize for lock file."""
        return {
            "pid": self.pid,
            "hostname": self.hostname,
            "writer_id": self.writer_id,
            "acquired_at": self.acquired_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, str | int], lock_path: Path) -> LockToken:
        """Deserialize from lock file data."""
        return cls(
            pid=int(data["pid"]),
            hostname=str(data["hostname"]),
            writer_id=str(data["writer_id"]),
            acquired_at=datetime.datetime.fromisoformat(str(data["acquired_at"])),
            expires_at=datetime.datetime.fromisoformat(str(data["expires_at"])),
            lock_path=lock_path,
        )

    @classmethod
    def from_json(cls, json_str: str, lock_path: Path) -> LockToken:
        """Deserialize from JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data, lock_path)


class TradingCalendar(Protocol):
    """
    Protocol for trading calendar implementations.

    Concrete implementations:
    - Production: exchange_calendars.get_calendar("XNYS") for NYSE
                  Use ExchangeCalendarAdapter for Protocol compliance
    - Testing: libs.data_quality.testing.MockTradingCalendar

    Example:
        from exchange_calendars import get_calendar
        from libs.data_quality.types import ExchangeCalendarAdapter

        # Create adapter for NYSE calendar
        nyse = ExchangeCalendarAdapter("XNYS")

        # Use in validation
        validator.validate_date_continuity(df, "date", calendar=nyse)
    """

    def is_trading_day(self, date: datetime.date) -> bool:
        """Return True if date is a trading day."""
        ...

    def trading_days_between(
        self, start: datetime.date, end: datetime.date
    ) -> list[datetime.date]:
        """Return list of trading days in range (inclusive)."""
        ...


class ExchangeCalendarAdapter:
    """Adapter to make exchange_calendars compatible with TradingCalendar protocol.

    Requires: pip install exchange-calendars

    Attributes:
        exchange_code: Exchange code (e.g., "XNYS" for NYSE, "XNAS" for NASDAQ).
    """

    def __init__(self, exchange_code: str = "XNYS") -> None:
        """Initialize adapter with exchange calendar.

        Args:
            exchange_code: Exchange code. Common values:
                - "XNYS": New York Stock Exchange
                - "XNAS": NASDAQ
                - "XLON": London Stock Exchange
        """
        # Lazy import to avoid requiring exchange_calendars at module load
        from exchange_calendars import get_calendar  # type: ignore[import-not-found]

        self._cal = get_calendar(exchange_code)
        self.exchange_code = exchange_code

    def is_trading_day(self, date: datetime.date) -> bool:
        """Return True if date is a trading day."""
        return bool(self._cal.is_session(pd.Timestamp(date)))

    def trading_days_between(
        self, start: datetime.date, end: datetime.date
    ) -> list[datetime.date]:
        """Return list of trading days in range (inclusive)."""
        sessions = self._cal.sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))
        return [s.date() for s in sessions]


@dataclass
class DiskSpaceStatus:
    """Result of disk space check.

    Attributes:
        level: Status level - "ok", "warning" (80%), or "critical" (90%).
        free_bytes: Available bytes on the filesystem.
        total_bytes: Total bytes on the filesystem.
        used_pct: Percentage of disk space used (0.0 to 1.0).
        message: Human-readable status message.
    """

    level: Literal["ok", "warning", "critical"]
    free_bytes: int
    total_bytes: int
    used_pct: float
    message: str
