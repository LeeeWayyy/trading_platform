"""Legacy compatibility tests for data management page (P5T7 -> P6T13).

These tests verify the basic structure and permission gating remain intact
after the P6T13 service wiring refactor.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.web_console_ng.pages import data_management as dm_module


@pytest.mark.asyncio()
@patch("apps.web_console_ng.pages.data_management.ui")
async def test_data_sync_no_view_shows_placeholder(
    mock_ui: MagicMock,
) -> None:
    """Sync status placeholder when VIEW_DATA_SYNC is missing."""
    mock_ui.label.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))

    sync_service = MagicMock()
    sync_service.get_sync_status = AsyncMock(return_value=[])

    await dm_module._render_sync_status(
        {"role": "viewer"}, sync_service, has_view=False, has_trigger=False
    )
    sync_service.get_sync_status.assert_not_awaited()


@pytest.mark.asyncio()
@patch("apps.web_console_ng.pages.data_management.ui")
async def test_data_quality_section_creates_tabs(
    mock_ui: MagicMock,
) -> None:
    """Quality section creates all four sub-tabs including Quarantine Inspector."""
    mock_ui.tabs.return_value = MagicMock()
    mock_ui.tabs.return_value.__enter__ = MagicMock()
    mock_ui.tabs.return_value.__exit__ = MagicMock(return_value=False)
    mock_ui.tab.return_value = MagicMock()
    mock_ui.tab_panels.return_value = MagicMock()
    mock_ui.tab_panels.return_value.__enter__ = MagicMock()
    mock_ui.tab_panels.return_value.__exit__ = MagicMock(return_value=False)
    mock_ui.tab_panel.return_value = MagicMock()
    mock_ui.tab_panel.return_value.__enter__ = MagicMock()
    mock_ui.tab_panel.return_value.__exit__ = MagicMock(return_value=False)
    mock_ui.label.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))
    col_mock = MagicMock()
    col_mock.__enter__ = MagicMock()
    col_mock.__exit__ = MagicMock(return_value=False)
    mock_ui.column.return_value = col_mock
    select_mock = MagicMock()
    select_mock.value = "all"
    select_mock.classes.return_value = select_mock
    select_mock.on_value_change = MagicMock()
    mock_ui.select.return_value = select_mock
    mock_ui.row.return_value = MagicMock()
    mock_ui.row.return_value.__enter__ = MagicMock()
    mock_ui.row.return_value.__exit__ = MagicMock(return_value=False)
    mock_ui.table.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))
    mock_ui.card.return_value = MagicMock()
    mock_ui.card.return_value.__enter__ = MagicMock()
    mock_ui.card.return_value.__exit__ = MagicMock(return_value=False)
    mock_ui.button.return_value = MagicMock(
        props=MagicMock(return_value=MagicMock(classes=MagicMock(return_value=MagicMock())))
    )
    mock_ui.plotly = MagicMock(return_value=MagicMock(classes=MagicMock(return_value=MagicMock())))
    mock_ui.expansion.return_value = MagicMock()
    mock_ui.expansion.return_value.__enter__ = MagicMock()
    mock_ui.expansion.return_value.__exit__ = MagicMock(return_value=False)

    quality_service = MagicMock()
    quality_service.get_validation_results = AsyncMock(return_value=[])
    quality_service.get_anomaly_alerts = AsyncMock(return_value=[])
    quality_service.get_quality_trends = AsyncMock(
        return_value=MagicMock(data_points=[], dataset="crsp", period_days=30)
    )
    quality_service.get_quarantine_status = AsyncMock(return_value=[])

    result = await dm_module._render_data_quality_section(
        {"role": "admin"}, quality_service
    )

    # Returns tuple of (alerts_container, scores_container, load_alerts_fn)
    assert isinstance(result, tuple)
    assert len(result) == 3
    assert callable(result[2])

    # Should create tabs
    tab_calls = [str(c) for c in mock_ui.tab.call_args_list]
    assert len(tab_calls) >= 4


@pytest.mark.asyncio()
@patch("apps.web_console_ng.pages.data_management.ui")
async def test_module_exports(mock_ui: MagicMock) -> None:
    """Module exports data_management_page."""
    assert "data_management_page" in dm_module.__all__
