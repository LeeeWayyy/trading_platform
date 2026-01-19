"""Unit tests for HeaderMetrics component."""

from __future__ import annotations

import types
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from apps.web_console_ng.components import header_metrics as metrics_module
from apps.web_console_ng.components.header_metrics import (
    ET_TIMEZONE,
    LEVERAGE_GREEN_MAX,
    LEVERAGE_YELLOW_MAX,
    HeaderMetrics,
    _format_currency,
    _format_day_change,
    _get_trading_date_key,
    _safe_float,
)


class DummyLabel:
    """Mock NiceGUI label for testing."""

    def __init__(self, text: str = "") -> None:
        self.text = text
        self._classes: set[str] = set()

    def set_text(self, text: str) -> None:
        self.text = text

    def classes(self, add: str | None = None, remove: str | None = None) -> DummyLabel:
        if remove:
            for cls in remove.split():
                self._classes.discard(cls)
        if add:
            for cls in add.split():
                self._classes.add(cls)
        return self


class DummyRow:
    """Mock NiceGUI row for testing."""

    def __init__(self) -> None:
        self._classes: set[str] = set()

    def classes(self, add: str | None = None) -> DummyRow:
        if add:
            for cls in add.split():
                self._classes.add(cls)
        return self

    def __enter__(self) -> DummyRow:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        pass


class TestSafeFloat:
    """Tests for _safe_float helper function."""

    def test_none_returns_default(self) -> None:
        assert _safe_float(None) == 0.0
        assert _safe_float(None, 42.0) == 42.0

    def test_valid_float(self) -> None:
        assert _safe_float(123.45) == 123.45

    def test_valid_int(self) -> None:
        assert _safe_float(100) == 100.0

    def test_string_number(self) -> None:
        assert _safe_float("123.45") == 123.45

    def test_invalid_string_returns_default(self) -> None:
        assert _safe_float("not a number") == 0.0
        assert _safe_float("not a number", 99.0) == 99.0

    def test_empty_string_returns_default(self) -> None:
        assert _safe_float("") == 0.0


class TestFormatCurrency:
    """Tests for _format_currency helper function."""

    def test_billions(self) -> None:
        assert _format_currency(1_500_000_000) == "$1.50B"
        assert _format_currency(-2_300_000_000) == "-$2.30B"

    def test_millions(self) -> None:
        assert _format_currency(1_234_567) == "$1.23M"
        assert _format_currency(-5_678_901) == "-$5.68M"

    def test_thousands(self) -> None:
        assert _format_currency(12_345) == "$12.3K"
        assert _format_currency(-9_876) == "-$9.9K"

    def test_small_amounts(self) -> None:
        assert _format_currency(123) == "$123"
        assert _format_currency(-50) == "-$50"

    def test_zero(self) -> None:
        assert _format_currency(0) == "$0"


class TestFormatDayChange:
    """Tests for _format_day_change helper function."""

    def test_positive_change_with_pct(self) -> None:
        result = _format_day_change(12345, 2.5)
        assert result == "+$12.3K (+2.5%)"

    def test_negative_change_with_pct(self) -> None:
        result = _format_day_change(-5000, -1.8)
        assert result == "-$5.0K (-1.8%)"

    def test_zero_change(self) -> None:
        result = _format_day_change(0, 0.0)
        assert result == "+$0 (+0.0%)"

    def test_change_without_pct(self) -> None:
        result = _format_day_change(1000, None)
        assert result == "+$1.0K"


class TestGetTradingDateKey:
    """Tests for _get_trading_date_key helper function."""

    def test_format_is_correct(self) -> None:
        key = _get_trading_date_key()
        assert key.startswith("nlv_baseline_")
        # Should be in YYYY-MM-DD format
        date_part = key.replace("nlv_baseline_", "")
        datetime.strptime(date_part, "%Y-%m-%d")

    def test_uses_et_timezone(self) -> None:
        # Mock datetime to a specific time
        with patch.object(metrics_module, "datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.strftime.return_value = "2026-01-15"
            mock_dt.now.return_value = mock_now

            _get_trading_date_key()

            # Verify it was called with ET timezone
            mock_dt.now.assert_called_once_with(ET_TIMEZONE)


class TestLeverageColorThresholds:
    """Tests for leverage color thresholds."""

    @pytest.fixture()
    def dummy_ui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Set up mock UI components."""

        def row() -> DummyRow:
            return DummyRow()

        def label(text: str = "") -> DummyLabel:
            return DummyLabel(text)

        dummy = types.SimpleNamespace(row=row, label=label)
        monkeypatch.setattr(metrics_module, "ui", dummy)

    def test_green_threshold(self, dummy_ui: None) -> None:
        metrics = HeaderMetrics()
        color = metrics._get_leverage_color_class(1.5)
        assert "green" in color.lower()

    def test_yellow_threshold(self, dummy_ui: None) -> None:
        metrics = HeaderMetrics()
        color = metrics._get_leverage_color_class(2.5)
        assert "yellow" in color.lower()

    def test_red_threshold(self, dummy_ui: None) -> None:
        metrics = HeaderMetrics()
        color = metrics._get_leverage_color_class(3.5)
        assert "red" in color.lower()

    def test_boundary_at_green_max(self, dummy_ui: None) -> None:
        metrics = HeaderMetrics()
        # At exactly LEVERAGE_GREEN_MAX (2.0), should be yellow
        color = metrics._get_leverage_color_class(LEVERAGE_GREEN_MAX)
        assert "yellow" in color.lower()

    def test_boundary_at_yellow_max(self, dummy_ui: None) -> None:
        metrics = HeaderMetrics()
        # At exactly LEVERAGE_YELLOW_MAX (3.0), should be red
        color = metrics._get_leverage_color_class(LEVERAGE_YELLOW_MAX)
        assert "red" in color.lower()


class TestLeverageCalculation:
    """Tests for leverage calculation logic."""

    @pytest.fixture()
    def dummy_ui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Set up mock UI components."""

        def row() -> DummyRow:
            return DummyRow()

        def label(text: str = "") -> DummyLabel:
            return DummyLabel(text)

        dummy = types.SimpleNamespace(row=row, label=label)
        monkeypatch.setattr(metrics_module, "ui", dummy)

    def test_zero_nlv_returns_none(self, dummy_ui: None) -> None:
        metrics = HeaderMetrics()
        positions = [{"market_value": 10000}]
        leverage, is_partial = metrics._calculate_leverage(positions, 0)
        assert leverage is None
        assert is_partial is False

    def test_negative_nlv_returns_none(self, dummy_ui: None) -> None:
        metrics = HeaderMetrics()
        positions = [{"market_value": 10000}]
        leverage, is_partial = metrics._calculate_leverage(positions, -1000)
        assert leverage is None
        assert is_partial is False

    def test_uses_market_value_when_available(self, dummy_ui: None) -> None:
        metrics = HeaderMetrics()
        positions = [
            {"market_value": 50000, "qty": 100, "current_price": 100},
            {"market_value": 30000, "qty": 50, "current_price": 200},
        ]
        nlv = 100000
        leverage, is_partial = metrics._calculate_leverage(positions, nlv)
        # Expected: (50000 + 30000) / 100000 = 0.8
        assert leverage == pytest.approx(0.8)
        assert is_partial is False

    def test_falls_back_to_qty_times_price(self, dummy_ui: None) -> None:
        metrics = HeaderMetrics()
        positions = [
            {"qty": 100, "current_price": 50},  # No market_value
        ]
        nlv = 10000
        leverage, is_partial = metrics._calculate_leverage(positions, nlv)
        # Expected: (100 * 50) / 10000 = 0.5
        assert leverage == pytest.approx(0.5)
        assert is_partial is False

    def test_marks_partial_when_missing_data(self, dummy_ui: None) -> None:
        metrics = HeaderMetrics()
        positions = [
            {"market_value": 50000},
            {"qty": 100},  # Missing current_price and market_value=0
            {},  # Completely missing data
        ]
        nlv = 100000
        leverage, is_partial = metrics._calculate_leverage(positions, nlv)
        # Should skip positions without data
        assert leverage == pytest.approx(0.5)  # 50000 / 100000
        assert is_partial is True  # 2 positions were skipped

    def test_handles_negative_positions(self, dummy_ui: None) -> None:
        metrics = HeaderMetrics()
        positions = [
            {"market_value": -30000},  # Short position
            {"market_value": 20000},
        ]
        nlv = 100000
        leverage, is_partial = metrics._calculate_leverage(positions, nlv)
        # Uses absolute values: (30000 + 20000) / 100000 = 0.5
        assert leverage == pytest.approx(0.5)
        assert is_partial is False

    def test_empty_positions(self, dummy_ui: None) -> None:
        metrics = HeaderMetrics()
        positions: list[dict[str, Any]] = []
        nlv = 100000
        leverage, is_partial = metrics._calculate_leverage(positions, nlv)
        assert leverage == 0.0
        assert is_partial is False


class TestStaleIndicator:
    """Tests for stale indicator logic."""

    @pytest.fixture()
    def dummy_ui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Set up mock UI components."""

        def row() -> DummyRow:
            return DummyRow()

        def label(text: str = "") -> DummyLabel:
            return DummyLabel(text)

        dummy = types.SimpleNamespace(row=row, label=label)
        monkeypatch.setattr(metrics_module, "ui", dummy)

    def test_is_stale_returns_false_when_no_update(self, dummy_ui: None) -> None:
        metrics = HeaderMetrics()
        # No update has occurred yet
        assert metrics.is_stale() is False

    def test_is_stale_after_threshold(
        self, dummy_ui: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        metrics = HeaderMetrics()
        # Simulate an update at time 1000
        monkeypatch.setattr(
            "apps.web_console_ng.components.header_metrics.time.monotonic",
            lambda: 1000.0,
        )
        metrics._last_update = 1000.0

        # Check stale at time 1031 (31 seconds later, threshold is 30)
        monkeypatch.setattr(
            "apps.web_console_ng.components.header_metrics.time.monotonic",
            lambda: 1031.0,
        )
        assert metrics.is_stale() is True

    def test_not_stale_within_threshold(
        self, dummy_ui: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        metrics = HeaderMetrics()
        monkeypatch.setattr(
            "apps.web_console_ng.components.header_metrics.time.monotonic",
            lambda: 1000.0,
        )
        metrics._last_update = 1000.0

        # Check stale at time 1029 (29 seconds later, threshold is 30)
        monkeypatch.setattr(
            "apps.web_console_ng.components.header_metrics.time.monotonic",
            lambda: 1029.0,
        )
        assert metrics.is_stale() is False

    def test_mark_stale_adds_opacity(self, dummy_ui: None) -> None:
        metrics = HeaderMetrics()
        metrics.mark_stale()
        # All labels should have opacity-50 class (use DummyLabel for testing)
        assert metrics._nlv_label is not None
        assert metrics._leverage_label is not None
        assert metrics._day_change_label is not None
        assert "opacity-50" in metrics._nlv_label._classes
        assert "opacity-50" in metrics._leverage_label._classes
        assert "opacity-50" in metrics._day_change_label._classes


class TestDayChangeETBoundary:
    """Tests for day change ET timezone boundary handling."""

    def test_et_timezone_is_america_new_york(self) -> None:
        assert ET_TIMEZONE == ZoneInfo("America/New_York")

    def test_date_key_changes_at_midnight_et(self) -> None:
        """Verify date key changes at midnight ET, not UTC."""
        # 11:59 PM ET on Jan 15
        with patch.object(metrics_module, "datetime") as mock_dt:
            mock_now_1159 = MagicMock()
            mock_now_1159.strftime.return_value = "2026-01-15"
            mock_dt.now.return_value = mock_now_1159

            key1 = _get_trading_date_key()

        # 12:01 AM ET on Jan 16
        with patch.object(metrics_module, "datetime") as mock_dt:
            mock_now_0001 = MagicMock()
            mock_now_0001.strftime.return_value = "2026-01-16"
            mock_dt.now.return_value = mock_now_0001

            key2 = _get_trading_date_key()

        # Keys should be different
        assert key1 != key2
        assert "2026-01-15" in key1
        assert "2026-01-16" in key2


class TestExtractPositions:
    """Tests for _extract_positions method."""

    @pytest.fixture()
    def dummy_ui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Set up mock UI components."""

        def row() -> DummyRow:
            return DummyRow()

        def label(text: str = "") -> DummyLabel:
            return DummyLabel(text)

        dummy = types.SimpleNamespace(row=row, label=label)
        monkeypatch.setattr(metrics_module, "ui", dummy)

    def test_extract_positions_from_list(self, dummy_ui: None) -> None:
        """Test extracting positions when API returns a list directly."""
        metrics = HeaderMetrics()
        positions = [{"symbol": "AAPL", "qty": 100}, {"symbol": "GOOGL", "qty": 50}]
        result = metrics._extract_positions(positions)
        assert result == positions

    def test_extract_positions_from_dict_wrapper(self, dummy_ui: None) -> None:
        """Test extracting positions when API returns dict with 'positions' key."""
        metrics = HeaderMetrics()
        positions_list = [{"symbol": "AAPL", "qty": 100}]
        positions_result = {"positions": positions_list, "total": 1}
        result = metrics._extract_positions(positions_result)
        assert result == positions_list

    def test_extract_positions_from_dict_with_non_list_positions(
        self, dummy_ui: None
    ) -> None:
        """Test extracting positions when dict has non-list positions value."""
        metrics = HeaderMetrics()
        positions_result = {"positions": "invalid"}
        result = metrics._extract_positions(positions_result)
        assert result == []

    def test_extract_positions_from_dict_without_positions_key(
        self, dummy_ui: None
    ) -> None:
        """Test extracting positions when dict doesn't have positions key."""
        metrics = HeaderMetrics()
        positions_result = {"data": [{"symbol": "AAPL"}]}
        result = metrics._extract_positions(positions_result)
        assert result == []

    def test_extract_positions_from_none(self, dummy_ui: None) -> None:
        """Test extracting positions from None returns empty list."""
        metrics = HeaderMetrics()
        result = metrics._extract_positions(None)
        assert result == []

    def test_extract_positions_from_invalid_type(self, dummy_ui: None) -> None:
        """Test extracting positions from invalid type returns empty list."""
        metrics = HeaderMetrics()
        result = metrics._extract_positions("invalid")
        assert result == []


class TestGetOrSetBaselineNLV:
    """Tests for _get_or_set_baseline_nlv method."""

    @pytest.fixture()
    def dummy_ui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Set up mock UI components."""

        def row() -> DummyRow:
            return DummyRow()

        def label(text: str = "") -> DummyLabel:
            return DummyLabel(text)

        dummy = types.SimpleNamespace(row=row, label=label)
        monkeypatch.setattr(metrics_module, "ui", dummy)

    def test_get_existing_baseline(
        self, dummy_ui: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test getting existing baseline NLV from storage."""
        metrics = HeaderMetrics()

        # Mock app.storage.user
        mock_app = MagicMock()
        mock_app.storage.user.get.return_value = 100000.0
        monkeypatch.setattr(metrics_module, "app", mock_app)

        # Mock the date key
        monkeypatch.setattr(
            metrics_module, "_get_trading_date_key", lambda: "nlv_baseline_2026-01-15"
        )

        result = metrics._get_or_set_baseline_nlv(105000.0)
        assert result == 100000.0  # Should return existing baseline

    def test_set_new_baseline(
        self, dummy_ui: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test setting new baseline NLV when not present."""
        metrics = HeaderMetrics()

        mock_app = MagicMock()
        mock_app.storage.user.get.return_value = None
        mock_app.storage.user.__setitem__ = MagicMock()
        monkeypatch.setattr(metrics_module, "app", mock_app)

        monkeypatch.setattr(
            metrics_module, "_get_trading_date_key", lambda: "nlv_baseline_2026-01-15"
        )

        result = metrics._get_or_set_baseline_nlv(100000.0)
        assert result == 100000.0  # Should return the new baseline
        # Verify it was set
        mock_app.storage.user.__setitem__.assert_called_once_with(
            "nlv_baseline_2026-01-15", 100000.0
        )

    def test_storage_exception_returns_current_nlv(
        self, dummy_ui: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that storage exception returns current NLV as fallback."""
        metrics = HeaderMetrics()

        mock_app = MagicMock()
        mock_app.storage.user.get.side_effect = RuntimeError("Storage unavailable")
        monkeypatch.setattr(metrics_module, "app", mock_app)

        monkeypatch.setattr(
            metrics_module, "_get_trading_date_key", lambda: "nlv_baseline_2026-01-15"
        )

        result = metrics._get_or_set_baseline_nlv(105000.0)
        assert result == 105000.0  # Should return current NLV as fallback


class TestClearStale:
    """Tests for _clear_stale method."""

    @pytest.fixture()
    def dummy_ui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Set up mock UI components."""

        def row() -> DummyRow:
            return DummyRow()

        def label(text: str = "") -> DummyLabel:
            return DummyLabel(text)

        dummy = types.SimpleNamespace(row=row, label=label)
        monkeypatch.setattr(metrics_module, "ui", dummy)

    def test_clear_stale_removes_opacity(self, dummy_ui: None) -> None:
        """Test that _clear_stale removes opacity-50 class."""
        metrics = HeaderMetrics()

        # First mark as stale
        metrics.mark_stale()
        assert "opacity-50" in metrics._nlv_label._classes
        assert "opacity-50" in metrics._leverage_label._classes
        assert "opacity-50" in metrics._day_change_label._classes

        # Now clear stale
        metrics._clear_stale()
        assert "opacity-50" not in metrics._nlv_label._classes
        assert "opacity-50" not in metrics._leverage_label._classes
        assert "opacity-50" not in metrics._day_change_label._classes

    def test_clear_stale_handles_none_labels(self, dummy_ui: None) -> None:
        """Test that _clear_stale handles None labels gracefully."""
        metrics = HeaderMetrics()
        metrics._nlv_label = None
        metrics._leverage_label = None
        metrics._day_change_label = None

        # Should not raise
        metrics._clear_stale()


class TestUpdateMethod:
    """Tests for the async update method."""

    @pytest.fixture()
    def dummy_ui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Set up mock UI components."""

        def row() -> DummyRow:
            return DummyRow()

        def label(text: str = "") -> DummyLabel:
            return DummyLabel(text)

        dummy = types.SimpleNamespace(row=row, label=label)
        monkeypatch.setattr(metrics_module, "ui", dummy)

    @pytest.fixture()
    def mock_client(self) -> MagicMock:
        """Create a mock trading client."""
        client = MagicMock()
        return client

    @pytest.mark.asyncio()
    async def test_update_successful_with_positive_nlv(
        self, dummy_ui: None, mock_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test successful update with positive NLV and positions."""

        metrics = HeaderMetrics()

        # Mock storage for baseline NLV
        mock_app = MagicMock()
        mock_app.storage.user.get.return_value = 100000.0
        monkeypatch.setattr(metrics_module, "app", mock_app)
        monkeypatch.setattr(
            metrics_module, "_get_trading_date_key", lambda: "nlv_baseline_2026-01-15"
        )

        # Mock client responses
        account_info = {"portfolio_value": 105000.0}
        positions = [{"market_value": 50000}, {"market_value": 30000}]

        async def mock_fetch_account(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return account_info

        async def mock_fetch_positions(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            return positions

        mock_client.fetch_account_info = mock_fetch_account
        mock_client.fetch_positions = mock_fetch_positions

        await metrics.update(mock_client, "user123", "admin", ["strategy1"])

        # Verify NLV was updated
        assert metrics._nlv_label.text == "$105.0K"

        # Verify leverage was calculated and displayed (80000/105000 = 0.76x)
        assert "0.8x" in metrics._leverage_label.text

        # Verify day change was calculated (+5000 from baseline)
        assert "+" in metrics._day_change_label.text

        # Verify last_update was set
        assert metrics._last_update is not None

    @pytest.mark.asyncio()
    async def test_update_with_zero_nlv(
        self, dummy_ui: None, mock_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test update with zero NLV displays placeholder."""
        metrics = HeaderMetrics()

        mock_app = MagicMock()
        mock_app.storage.user.get.return_value = None
        monkeypatch.setattr(metrics_module, "app", mock_app)

        account_info = {"portfolio_value": 0}
        positions: list[dict[str, Any]] = []

        async def mock_fetch_account(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return account_info

        async def mock_fetch_positions(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            return positions

        mock_client.fetch_account_info = mock_fetch_account
        mock_client.fetch_positions = mock_fetch_positions

        await metrics.update(mock_client, "user123")

        assert metrics._nlv_label.text == "--"
        assert metrics._day_change_label.text == "--"

    @pytest.mark.asyncio()
    async def test_update_with_negative_day_change(
        self, dummy_ui: None, mock_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test update with negative day change displays correctly."""
        metrics = HeaderMetrics()

        mock_app = MagicMock()
        mock_app.storage.user.get.return_value = 100000.0
        monkeypatch.setattr(metrics_module, "app", mock_app)
        monkeypatch.setattr(
            metrics_module, "_get_trading_date_key", lambda: "nlv_baseline_2026-01-15"
        )

        account_info = {"portfolio_value": 95000.0}  # Loss of 5000
        positions = [{"market_value": 50000}]

        async def mock_fetch_account(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return account_info

        async def mock_fetch_positions(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            return positions

        mock_client.fetch_account_info = mock_fetch_account
        mock_client.fetch_positions = mock_fetch_positions

        await metrics.update(mock_client, "user123")

        # Verify day change shows negative
        assert "-" in metrics._day_change_label.text
        # Verify negative color class was applied
        from apps.web_console_ng.ui.theme import DAY_CHANGE_NEGATIVE

        assert DAY_CHANGE_NEGATIVE in metrics._day_change_label._classes

    @pytest.mark.asyncio()
    async def test_update_with_leverage_none_displays_placeholder(
        self, dummy_ui: None, mock_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test update with None leverage shows placeholder."""
        metrics = HeaderMetrics()

        mock_app = MagicMock()
        mock_app.storage.user.get.return_value = None
        monkeypatch.setattr(metrics_module, "app", mock_app)

        # Zero NLV causes leverage to be None
        account_info = {"portfolio_value": 0}
        positions = [{"market_value": 50000}]

        async def mock_fetch_account(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return account_info

        async def mock_fetch_positions(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            return positions

        mock_client.fetch_account_info = mock_fetch_account
        mock_client.fetch_positions = mock_fetch_positions

        await metrics.update(mock_client, "user123")

        assert metrics._leverage_label.text == "--"

    @pytest.mark.asyncio()
    async def test_update_with_partial_leverage_shows_asterisk(
        self, dummy_ui: None, mock_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test update with partial leverage data shows asterisk."""
        metrics = HeaderMetrics()

        mock_app = MagicMock()
        mock_app.storage.user.get.return_value = None
        mock_app.storage.user.__setitem__ = MagicMock()
        monkeypatch.setattr(metrics_module, "app", mock_app)
        monkeypatch.setattr(
            metrics_module, "_get_trading_date_key", lambda: "nlv_baseline_2026-01-15"
        )

        account_info = {"portfolio_value": 100000.0}
        # One position missing market_value and qty/price
        positions = [{"market_value": 50000}, {"symbol": "MISSING"}]

        async def mock_fetch_account(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return account_info

        async def mock_fetch_positions(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            return positions

        mock_client.fetch_account_info = mock_fetch_account
        mock_client.fetch_positions = mock_fetch_positions

        await metrics.update(mock_client, "user123")

        # Should show asterisk for partial data
        assert "*" in metrics._leverage_label.text

    @pytest.mark.asyncio()
    async def test_update_timeout_marks_stale(
        self, dummy_ui: None, mock_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that timeout marks metrics as stale."""
        import asyncio

        metrics = HeaderMetrics()

        async def slow_fetch(*args: Any, **kwargs: Any) -> dict[str, Any]:
            await asyncio.sleep(10)  # Sleep longer than timeout
            return {}

        mock_client.fetch_account_info = slow_fetch
        mock_client.fetch_positions = slow_fetch

        # Use a short timeout for test
        monkeypatch.setattr(metrics_module, "METRICS_FETCH_TIMEOUT", 0.01)

        await metrics.update(mock_client, "user123")

        # Should be marked as stale
        assert "opacity-50" in metrics._nlv_label._classes

    @pytest.mark.asyncio()
    async def test_update_cancelled_marks_stale(
        self, dummy_ui: None, mock_client: MagicMock
    ) -> None:
        """Test that cancelled task marks metrics as stale."""
        import asyncio

        metrics = HeaderMetrics()

        async def cancelled_fetch(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise asyncio.CancelledError()

        mock_client.fetch_account_info = cancelled_fetch
        mock_client.fetch_positions = cancelled_fetch

        await metrics.update(mock_client, "user123")

        # Should be marked as stale
        assert "opacity-50" in metrics._nlv_label._classes

    @pytest.mark.asyncio()
    async def test_update_exception_marks_stale(
        self, dummy_ui: None, mock_client: MagicMock
    ) -> None:
        """Test that generic exception marks metrics as stale."""
        metrics = HeaderMetrics()

        async def failing_fetch(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("API Error")

        mock_client.fetch_account_info = failing_fetch
        mock_client.fetch_positions = failing_fetch

        await metrics.update(mock_client, "user123")

        # Should be marked as stale
        assert "opacity-50" in metrics._nlv_label._classes

    @pytest.mark.asyncio()
    async def test_update_account_exception_propagates(
        self, dummy_ui: None, mock_client: MagicMock
    ) -> None:
        """Test that account fetch exception is caught and marks stale."""
        metrics = HeaderMetrics()

        async def account_error(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise ValueError("Account API Error")

        async def positions_success(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            return []

        mock_client.fetch_account_info = account_error
        mock_client.fetch_positions = positions_success

        await metrics.update(mock_client, "user123")

        # Should be marked as stale due to account error
        assert "opacity-50" in metrics._nlv_label._classes

    @pytest.mark.asyncio()
    async def test_update_positions_exception_propagates(
        self, dummy_ui: None, mock_client: MagicMock
    ) -> None:
        """Test that positions fetch exception is caught and marks stale."""
        metrics = HeaderMetrics()

        async def account_success(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"portfolio_value": 100000.0}

        async def positions_error(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            raise ValueError("Positions API Error")

        mock_client.fetch_account_info = account_success
        mock_client.fetch_positions = positions_error

        await metrics.update(mock_client, "user123")

        # Should be marked as stale due to positions error
        assert "opacity-50" in metrics._nlv_label._classes

    @pytest.mark.asyncio()
    async def test_update_with_dict_positions_result(
        self, dummy_ui: None, mock_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test update handles dict wrapper format for positions."""
        metrics = HeaderMetrics()

        mock_app = MagicMock()
        mock_app.storage.user.get.return_value = None
        mock_app.storage.user.__setitem__ = MagicMock()
        monkeypatch.setattr(metrics_module, "app", mock_app)
        monkeypatch.setattr(
            metrics_module, "_get_trading_date_key", lambda: "nlv_baseline_2026-01-15"
        )

        account_info = {"portfolio_value": 100000.0}
        # Positions in dict wrapper format
        positions_result = {"positions": [{"market_value": 50000}], "total": 1}

        async def mock_fetch_account(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return account_info

        async def mock_fetch_positions(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return positions_result

        mock_client.fetch_account_info = mock_fetch_account
        mock_client.fetch_positions = mock_fetch_positions

        await metrics.update(mock_client, "user123")

        # Should have processed positions correctly
        assert metrics._nlv_label.text == "$100.0K"

    @pytest.mark.asyncio()
    async def test_update_leverage_color_transition(
        self, dummy_ui: None, mock_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test leverage color class transition from one color to another."""
        metrics = HeaderMetrics()

        mock_app = MagicMock()
        mock_app.storage.user.get.return_value = None
        mock_app.storage.user.__setitem__ = MagicMock()
        monkeypatch.setattr(metrics_module, "app", mock_app)
        monkeypatch.setattr(
            metrics_module, "_get_trading_date_key", lambda: "nlv_baseline_2026-01-15"
        )

        # First update with low leverage (green)
        account_info = {"portfolio_value": 100000.0}
        positions = [{"market_value": 100000}]  # 1x leverage (green)

        async def mock_fetch_account(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return account_info

        async def mock_fetch_positions(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            return positions

        mock_client.fetch_account_info = mock_fetch_account
        mock_client.fetch_positions = mock_fetch_positions

        await metrics.update(mock_client, "user123")

        from apps.web_console_ng.ui.theme import LEVERAGE_GREEN, LEVERAGE_RED

        # LEVERAGE_GREEN is "bg-green-600 text-white" - check all parts are present
        for cls in LEVERAGE_GREEN.split():
            assert cls in metrics._leverage_label._classes

        # Second update with high leverage (red)
        positions_high = [{"market_value": 400000}]  # 4x leverage (red)

        async def mock_fetch_positions_high(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            return positions_high

        mock_client.fetch_positions = mock_fetch_positions_high

        await metrics.update(mock_client, "user123")

        # LEVERAGE_RED is "bg-red-600 text-white" - check all parts are present
        for cls in LEVERAGE_RED.split():
            assert cls in metrics._leverage_label._classes
        # Green-specific classes should be removed (bg-green-600)
        assert "bg-green-600" not in metrics._leverage_label._classes

    @pytest.mark.asyncio()
    async def test_update_with_none_account_result(
        self, dummy_ui: None, mock_client: MagicMock
    ) -> None:
        """Test update handles None account result."""
        metrics = HeaderMetrics()

        async def mock_fetch_account(*args: Any, **kwargs: Any) -> None:
            return None

        async def mock_fetch_positions(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            return []

        mock_client.fetch_account_info = mock_fetch_account
        mock_client.fetch_positions = mock_fetch_positions

        await metrics.update(mock_client, "user123")

        # Should show placeholder due to 0 NLV
        assert metrics._nlv_label.text == "--"

    @pytest.mark.asyncio()
    async def test_update_clears_stale_on_success(
        self, dummy_ui: None, mock_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that successful update clears stale indicator."""
        metrics = HeaderMetrics()

        # First mark as stale
        metrics.mark_stale()
        assert "opacity-50" in metrics._nlv_label._classes

        mock_app = MagicMock()
        mock_app.storage.user.get.return_value = None
        mock_app.storage.user.__setitem__ = MagicMock()
        monkeypatch.setattr(metrics_module, "app", mock_app)
        monkeypatch.setattr(
            metrics_module, "_get_trading_date_key", lambda: "nlv_baseline_2026-01-15"
        )

        account_info = {"portfolio_value": 100000.0}
        positions = [{"market_value": 50000}]

        async def mock_fetch_account(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return account_info

        async def mock_fetch_positions(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            return positions

        mock_client.fetch_account_info = mock_fetch_account
        mock_client.fetch_positions = mock_fetch_positions

        await metrics.update(mock_client, "user123")

        # Stale indicator should be cleared
        assert "opacity-50" not in metrics._nlv_label._classes
