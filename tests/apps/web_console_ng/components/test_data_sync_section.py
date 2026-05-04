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


def test_bind_page_globals_updates_patchable_dependencies() -> None:
    original_ui = data_sync_section.ui
    original_logger = data_sync_section.logger
    ui_module = MagicMock()
    logger_obj = MagicMock()

    try:
        data_sync_section.bind_page_globals(ui_module=ui_module, logger_obj=logger_obj)

        assert data_sync_section.ui is ui_module
        assert data_sync_section.logger is logger_obj
    finally:
        data_sync_section.bind_page_globals(
            ui_module=original_ui,
            logger_obj=original_logger,
        )
