"""Tests for database URL logging utilities."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apps.signal_service.main import _format_database_url_for_logging


def test_format_database_url_with_credentials():
    """Credentials should be stripped from logged database URLs."""
    url = "postgresql://user:secret@db.example.com:5432/trading"
    assert (
        _format_database_url_for_logging(url)
        == "db.example.com:5432/trading"
    )


def test_format_database_url_without_credentials():
    """URLs without credentials should remain readable without crashing."""
    url = "postgresql://db.example.com:5432/trading"
    assert (
        _format_database_url_for_logging(url)
        == "db.example.com:5432/trading"
    )


def test_format_database_url_sqlite():
    """Handle SQLite URLs that do not contain network segments."""
    url = "sqlite:///data/app.db"
    assert _format_database_url_for_logging(url) == "/data/app.db"


def test_format_database_url_empty():
    """Empty URLs should not raise errors."""
    assert _format_database_url_for_logging("") == "unknown"
