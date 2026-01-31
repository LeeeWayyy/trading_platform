"""Tests for grid export toolbar in apps/web_console_ng/components/grid_export_toolbar.py.

Tests the GridExportToolbar component including CSV/Excel/Clipboard export,
formula injection sanitization, and audit logging integration.
"""

from __future__ import annotations

import os

os.environ["WEB_CONSOLE_NG_DEBUG"] = "true"
os.environ.setdefault("NICEGUI_STORAGE_SECRET", "test-secret")

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from apps.web_console_ng.components.grid_export_toolbar import (
    GridExportToolbar,
    sanitize_for_export,
)


class TestSanitizeForExport:
    """Tests for sanitize_for_export function."""

    def test_sanitize_regular_string(self) -> None:
        """Regular strings pass through unchanged."""
        assert sanitize_for_export("hello") == "hello"
        assert sanitize_for_export("AAPL") == "AAPL"
        assert sanitize_for_export("order-123") == "order-123"

    def test_sanitize_numbers_pass_through(self) -> None:
        """Numbers pass through unchanged."""
        assert sanitize_for_export(123) == 123
        assert sanitize_for_export(123.45) == 123.45
        assert sanitize_for_export(-50) == -50

    def test_sanitize_none_pass_through(self) -> None:
        """None values pass through unchanged."""
        assert sanitize_for_export(None) is None

    def test_sanitize_boolean_pass_through(self) -> None:
        """Boolean values pass through unchanged."""
        assert sanitize_for_export(True) is True
        assert sanitize_for_export(False) is False

    def test_sanitize_equals_formula(self) -> None:
        """Strings starting with = are prefixed with quote."""
        assert sanitize_for_export("=IMPORTDATA(url)") == "'=IMPORTDATA(url)"
        assert sanitize_for_export("=SUM(A1:A10)") == "'=SUM(A1:A10)"

    def test_sanitize_plus_formula(self) -> None:
        """Strings starting with + are prefixed with quote."""
        assert sanitize_for_export("+1+1") == "'+1+1"

    def test_sanitize_at_formula(self) -> None:
        """Strings starting with @ are prefixed with quote."""
        assert sanitize_for_export("@SUM(A1)") == "'@SUM(A1)"

    def test_sanitize_tab_formula(self) -> None:
        """Strings starting with tab are prefixed with quote."""
        assert sanitize_for_export("\t=FORMULA") == "'\t=FORMULA"

    def test_sanitize_negative_numeric_passes(self) -> None:
        """Strictly numeric negative values pass through."""
        assert sanitize_for_export("-123") == "-123"
        assert sanitize_for_export("-123.45") == "-123.45"

    def test_sanitize_negative_non_numeric_blocked(self) -> None:
        """Non-numeric negative values are sanitized."""
        assert sanitize_for_export("-1+1") == "'-1+1"
        assert sanitize_for_export("-A1") == "'-A1"

    def test_sanitize_whitespace_prefix_stripped(self) -> None:
        """Leading whitespace is stripped when checking dangerous chars."""
        assert sanitize_for_export(" =FORMULA") == "' =FORMULA"
        assert sanitize_for_export("  =FORMULA") == "'  =FORMULA"

    def test_sanitize_empty_string(self) -> None:
        """Empty string passes through unchanged."""
        assert sanitize_for_export("") == ""

    def test_sanitize_all_whitespace(self) -> None:
        """All-whitespace strings pass through unchanged."""
        assert sanitize_for_export("   ") == "   "


class TestGridExportToolbar:
    """Tests for GridExportToolbar class."""

    @pytest.fixture
    def toolbar(self) -> GridExportToolbar:
        """Create toolbar instance for testing."""
        return GridExportToolbar(
            grid_id="_ordersGridApi",
            grid_name="orders",
            filename_prefix="orders_export",
            api_base_url="http://localhost:8001",
        )

    def test_init_basic(self, toolbar: GridExportToolbar) -> None:
        """Basic initialization."""
        assert toolbar.grid_id == "_ordersGridApi"
        assert toolbar.grid_name == "orders"
        assert toolbar.filename_prefix == "orders_export"

    def test_init_with_all_params(self) -> None:
        """Initialization with all parameters."""
        on_start = MagicMock()
        on_complete = MagicMock()

        toolbar = GridExportToolbar(
            grid_id="_testGridApi",
            grid_name="test",
            filename_prefix="test_export",
            pii_columns=["user_id", "email"],
            exclude_columns=["internal_id"],
            on_export_start=on_start,
            on_export_complete=on_complete,
            api_base_url="http://localhost:8001",
        )
        assert toolbar.pii_columns == ["user_id", "email"]
        assert toolbar.exclude_columns == ["internal_id"]
        assert toolbar.on_export_start == on_start
        assert toolbar.on_export_complete == on_complete

    def test_init_defaults(self) -> None:
        """Default parameter values."""
        toolbar = GridExportToolbar(
            grid_id="_gridApi",
            grid_name="test",
            filename_prefix="test",
        )
        assert toolbar.pii_columns == []
        assert toolbar.exclude_columns == []
        assert toolbar.on_export_start is None
        assert toolbar.on_export_complete is None
        assert toolbar.api_base_url == "/api/v1"

    def test_get_filename_format(self, toolbar: GridExportToolbar) -> None:
        """Filename follows expected format."""
        filename = toolbar._get_filename()
        assert filename.startswith("orders_export_")
        # Format: orders_export_YYYY-MM-DD_HH-MM
        parts = filename.split("_")
        assert len(parts) >= 3

    def test_get_exclude_columns_basic(self, toolbar: GridExportToolbar) -> None:
        """Basic exclude columns without PII."""
        toolbar.exclude_columns = ["col1", "col2"]
        toolbar.pii_columns = []

        # Mock app.storage to return no user
        with patch("apps.web_console_ng.components.grid_export_toolbar.app") as mock_app:
            mock_app.storage.user.get.return_value = None
            exclude = toolbar._get_exclude_columns()
            assert "col1" in exclude
            assert "col2" in exclude

    def test_get_exclude_columns_pii_for_non_admin(self, toolbar: GridExportToolbar) -> None:
        """PII columns added for non-admin users."""
        toolbar.exclude_columns = ["col1"]
        toolbar.pii_columns = ["user_id", "email"]

        # Mock app.storage and is_admin
        mock_user = {"role": "trader"}
        with patch("apps.web_console_ng.components.grid_export_toolbar.app") as mock_app:
            mock_app.storage.user.get.return_value = mock_user
            with patch(
                "libs.platform.web_console_auth.permissions.is_admin",
                return_value=False,
            ):
                exclude = toolbar._get_exclude_columns()
                assert "col1" in exclude
                assert "user_id" in exclude
                assert "email" in exclude

    def test_get_exclude_columns_pii_not_added_for_admin(
        self, toolbar: GridExportToolbar
    ) -> None:
        """PII columns NOT added for admin users."""
        toolbar.exclude_columns = ["col1"]
        toolbar.pii_columns = ["user_id", "email"]

        # Mock app.storage and is_admin
        mock_user = {"role": "admin"}
        with patch("apps.web_console_ng.components.grid_export_toolbar.app") as mock_app:
            mock_app.storage.user.get.return_value = mock_user
            with patch(
                "libs.platform.web_console_auth.permissions.is_admin",
                return_value=True,
            ):
                exclude = toolbar._get_exclude_columns()
                assert "col1" in exclude
                # PII columns should NOT be in exclude for admin
                assert "user_id" not in exclude
                assert "email" not in exclude

    def test_get_exclude_columns_no_user(self, toolbar: GridExportToolbar) -> None:
        """PII columns NOT added when no user available."""
        toolbar.exclude_columns = ["col1"]
        toolbar.pii_columns = ["user_id", "email"]

        # Mock app.storage with no user
        with patch("apps.web_console_ng.components.grid_export_toolbar.app") as mock_app:
            mock_app.storage.user.get.return_value = None
            exclude = toolbar._get_exclude_columns()
            assert "col1" in exclude
            # PII columns should NOT be in exclude when no user
            assert "user_id" not in exclude
            assert "email" not in exclude


class TestGridExportToolbarWithUI:
    """Tests for GridExportToolbar UI creation (mocked)."""

    @pytest.fixture
    def mock_ui(self) -> MagicMock:
        """Mock NiceGUI ui module."""
        with patch("apps.web_console_ng.components.grid_export_toolbar.ui") as mock:
            mock.row.return_value.__enter__ = MagicMock()
            mock.row.return_value.__exit__ = MagicMock()
            mock.button.return_value = MagicMock()
            yield mock

    def test_create_generates_buttons(self, mock_ui: MagicMock) -> None:
        """Create method generates export buttons."""
        toolbar = GridExportToolbar(
            grid_id="_gridApi",
            grid_name="test",
            filename_prefix="test",
        )

        toolbar.create()

        # Should create multiple buttons (CSV, Excel, Clipboard)
        assert mock_ui.button.call_count >= 1
