"""Unit tests for libs.platform.web_console_auth.db."""

from __future__ import annotations

from libs.core.common import db as core_db
from libs.platform.web_console_auth import db as web_console_db


def test_acquire_connection_reexport() -> None:
    assert web_console_db.acquire_connection is core_db.acquire_connection
