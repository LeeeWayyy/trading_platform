"""Focused tests for helper functions in apps/signal_service/main.py."""

import pytest

from apps.signal_service.config import Settings
from apps.signal_service import main


class TestFormatDatabaseUrlForLogging:
    """Tests for _format_database_url_for_logging."""

    def test_returns_unknown_for_empty(self) -> None:
        assert main._format_database_url_for_logging("") == "unknown"

    def test_strips_credentials(self) -> None:
        url = "postgresql://user:secret@db.example.com:5432/trader"
        assert main._format_database_url_for_logging(url) == "db.example.com:5432/trader"

    def test_preserves_host_when_no_user(self) -> None:
        url = "postgresql://db.example.com:5432/trader"
        assert main._format_database_url_for_logging(url) == "db.example.com:5432/trader"


class TestGetSettings:
    """Tests for get_settings access control."""

    def test_raises_before_lifespan_init(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(main, "settings", None)
        with pytest.raises(RuntimeError, match="Settings not initialized"):
            main.get_settings()

    def test_returns_settings_after_init(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = Settings(_env_file=None)
        monkeypatch.setattr(main, "settings", settings)
        assert main.get_settings() is settings


def test_root_smoke(client) -> None:
    """Basic smoke check for app root using TestClient fixture."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "Signal Service"
