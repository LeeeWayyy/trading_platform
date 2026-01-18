"""Tests for reconciliation context and configuration."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import Mock, patch

from apps.execution_gateway.reconciliation.context import (
    ReconciliationConfig,
    ReconciliationContext,
)


def test_reconciliation_context_default_now_is_utc() -> None:
    """Default now() should return timezone-aware UTC timestamps."""
    ctx = ReconciliationContext(
        db_client=Mock(),
        alpaca_client=Mock(),
        redis_client=Mock(),
    )

    now = ctx.now()

    assert now.tzinfo is UTC


def test_reconciliation_context_injected_now() -> None:
    """Injected now() should be used verbatim."""
    frozen = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    ctx = ReconciliationContext(
        db_client=Mock(),
        alpaca_client=Mock(),
        redis_client=None,
        now=lambda: frozen,
    )

    assert ctx.now() is frozen


def test_reconciliation_config_from_env_defaults() -> None:
    """from_env should use defaults when no variables are set."""
    with patch.dict("os.environ", {}, clear=True):
        config = ReconciliationConfig.from_env()

    assert config.poll_interval_seconds == 300
    assert config.timeout_seconds == 300
    assert config.max_individual_lookups == 100
    assert config.overlap_seconds == 60
    assert config.submitted_unconfirmed_grace_seconds == 300
    assert config.fills_backfill_enabled is False
    assert config.fills_backfill_initial_lookback_hours == 24
    assert config.fills_backfill_page_size == 100
    assert config.fills_backfill_max_pages == 5
    assert config.dry_run is True


def test_reconciliation_config_from_env_custom_values() -> None:
    """from_env should parse integer and boolean values correctly."""
    env_vars = {
        "RECONCILIATION_INTERVAL_SECONDS": "600",
        "RECONCILIATION_TIMEOUT_SECONDS": "120",
        "RECONCILIATION_MAX_LOOKUPS": "50",
        "RECONCILIATION_OVERLAP_SECONDS": "30",
        "RECONCILIATION_SUBMITTED_UNCONFIRMED_GRACE_SECONDS": "600",
        "ALPACA_FILLS_BACKFILL_ENABLED": "yes",
        "ALPACA_FILLS_BACKFILL_INITIAL_LOOKBACK_HOURS": "48",
        "ALPACA_FILLS_BACKFILL_PAGE_SIZE": "200",
        "ALPACA_FILLS_BACKFILL_MAX_PAGES": "10",
        "DRY_RUN": "0",
    }
    with patch.dict("os.environ", env_vars, clear=True):
        config = ReconciliationConfig.from_env()

    assert config.poll_interval_seconds == 600
    assert config.timeout_seconds == 120
    assert config.max_individual_lookups == 50
    assert config.overlap_seconds == 30
    assert config.submitted_unconfirmed_grace_seconds == 600
    assert config.fills_backfill_enabled is True
    assert config.fills_backfill_initial_lookback_hours == 48
    assert config.fills_backfill_page_size == 200
    assert config.fills_backfill_max_pages == 10
    assert config.dry_run is False


def test_reconciliation_config_bool_parsing_variations() -> None:
    """Boolean env parsing should be case-insensitive across truthy values."""
    for value in ["1", "true", "yes", "on", "TRUE", "Yes"]:
        with patch.dict("os.environ", {"ALPACA_FILLS_BACKFILL_ENABLED": value}, clear=True):
            config = ReconciliationConfig.from_env()
            assert config.fills_backfill_enabled is True
