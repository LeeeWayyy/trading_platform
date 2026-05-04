"""Tests for the extracted data sync NiceGUI component."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.web_console_ng.components import data_sync_section


@pytest.mark.asyncio()
@patch("apps.web_console_ng.components.data_sync_section.ui")
async def test_render_sync_status_without_view_permission_skips_service(
    mock_ui: MagicMock,
) -> None:
    mock_ui.label.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))
    sync_service = MagicMock()
    sync_service.get_sync_status = AsyncMock(return_value=[])

    result = await data_sync_section.render_sync_status(
        {"role": "viewer"},
        sync_service,
        has_view=False,
        has_trigger=False,
    )

    assert result is None
    sync_service.get_sync_status.assert_not_awaited()


def test_build_sync_status_table_accepts_explicit_ui_module() -> None:
    ui_module = MagicMock()
    status = MagicMock(
        dataset="alpaca_sip",
        last_sync=None,
        row_count=10,
        validation_status="ok",
    )

    data_sync_section.build_sync_status_table([status], ui_module=ui_module)

    ui_module.table.assert_called_once()
