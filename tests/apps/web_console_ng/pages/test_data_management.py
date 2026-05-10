"""Legacy compatibility tests for data management page (P5T7 -> P6T13).

These tests verify the basic structure and permission gating remain intact
after the P6T13 service wiring refactor.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.web_console_ng.pages import data_management as dm_module
from libs.platform.web_console_auth.permissions import Permission
from libs.web_console_services.data_readiness_service import HYBRID_CRSP_SIP_DATASET_KEY


def _setup_data_quality_ui(mock_ui: MagicMock) -> None:
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


def _quality_service_for_section() -> MagicMock:
    quality_service = MagicMock()
    quality_service.get_validation_results = AsyncMock(return_value=[])
    quality_service.get_anomaly_alerts = AsyncMock(return_value=[])
    quality_service.get_quality_trends = AsyncMock(
        return_value=MagicMock(data_points=[], dataset="crsp", period_days=30)
    )
    quality_service.get_quarantine_status = AsyncMock(return_value=[])
    quality_service.get_alpaca_sip_quality_summary = AsyncMock(return_value=MagicMock())
    quality_service.acknowledgments_persistent = False
    return quality_service


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
@patch("apps.web_console_ng.pages.data_management.has_permission", return_value=False)
@patch("apps.web_console_ng.pages.data_management.has_dataset_permission")
async def test_manifest_transparency_requires_sync_view_permission(
    mock_dataset_permission: MagicMock,
    mock_permission: MagicMock,
) -> None:
    manifest_service = MagicMock()
    readiness_service = MagicMock()

    await dm_module._render_manifest_transparency(
        {"role": "viewer"}, manifest_service, readiness_service
    )

    manifest_service.get_alpaca_sip_summary.assert_not_called()
    readiness_service.get_readiness.assert_not_called()
    mock_dataset_permission.assert_not_called()
    mock_permission.assert_called_once()


@pytest.mark.asyncio()
@patch("apps.web_console_ng.pages.data_management.has_permission", return_value=True)
@patch("apps.web_console_ng.pages.data_management.has_dataset_permission", return_value=False)
async def test_manifest_transparency_requires_alpaca_sip_dataset_permission(
    mock_dataset_permission: MagicMock,
    mock_permission: MagicMock,
) -> None:
    manifest_service = MagicMock()
    readiness_service = MagicMock()

    await dm_module._render_manifest_transparency(
        {"role": "viewer"}, manifest_service, readiness_service
    )

    manifest_service.get_alpaca_sip_summary.assert_not_called()
    readiness_service.get_readiness.assert_not_called()
    mock_permission.assert_called_once()
    assert mock_dataset_permission.call_count == 2


@pytest.mark.asyncio()
@patch("apps.web_console_ng.pages.data_management._data_readiness_section")
@patch("apps.web_console_ng.pages.data_management._data_manifest_panel")
@patch("apps.web_console_ng.pages.data_management.has_permission", return_value=True)
@patch("apps.web_console_ng.pages.data_management.has_dataset_permission")
async def test_manifest_transparency_renders_hybrid_readiness_without_direct_sip_access(
    mock_dataset_permission: MagicMock,
    mock_permission: MagicMock,
    mock_panel: MagicMock,
    mock_readiness_section: MagicMock,
) -> None:
    def dataset_access(_user: dict[str, str], dataset: str) -> bool:
        return dataset == HYBRID_CRSP_SIP_DATASET_KEY

    mock_dataset_permission.side_effect = dataset_access
    readiness = MagicMock()
    manifest_service = MagicMock()
    readiness_service = MagicMock()
    readiness_service.get_readiness.return_value = readiness

    await dm_module._render_manifest_transparency(
        {"role": "viewer"}, manifest_service, readiness_service
    )

    manifest_service.get_alpaca_sip_summary.assert_not_called()
    readiness_service.get_readiness.assert_called_once_with(
        {"role": "viewer"},
        HYBRID_CRSP_SIP_DATASET_KEY,
        "hybrid_research_backtest",
        alpaca_sip_summary=None,
    )
    mock_panel.render_manifest_transparency_panel.assert_not_called()
    mock_readiness_section.render_readiness_section.assert_called_once_with([readiness])
    mock_permission.assert_called_once()


@pytest.mark.asyncio()
@patch("apps.web_console_ng.pages.data_management.ui")
@patch("apps.web_console_ng.pages.data_management._data_readiness_section")
@patch("apps.web_console_ng.pages.data_management._data_manifest_panel")
@patch("apps.web_console_ng.pages.data_management.has_permission", return_value=True)
@patch("apps.web_console_ng.pages.data_management.has_dataset_permission", return_value=True)
async def test_manifest_transparency_renders_authorized_summary(
    mock_dataset_permission: MagicMock,
    mock_permission: MagicMock,
    mock_panel: MagicMock,
    mock_readiness_section: MagicMock,
    mock_ui: MagicMock,
) -> None:
    summary = MagicMock()
    manifest_service = MagicMock()
    manifest_service.get_alpaca_sip_summary.return_value = summary
    readiness_service = MagicMock()

    await dm_module._render_manifest_transparency(
        {"role": "operator"}, manifest_service, readiness_service
    )

    manifest_service.get_alpaca_sip_summary.assert_called_once_with()
    assert readiness_service.get_readiness.call_count == 2
    mock_panel.render_manifest_transparency_panel.assert_called_once_with(summary)
    mock_readiness_section.render_readiness_section.assert_called_once()
    mock_permission.assert_called_once()
    assert mock_dataset_permission.call_count == 2
    mock_ui.notify.assert_not_called()


@pytest.mark.asyncio()
@patch("apps.web_console_ng.pages.data_management.ui")
@patch("apps.web_console_ng.pages.data_management._data_readiness_section")
@patch("apps.web_console_ng.pages.data_management.has_permission", return_value=True)
@patch("apps.web_console_ng.pages.data_management.has_dataset_permission", return_value=True)
async def test_manifest_transparency_warns_when_service_unavailable(
    mock_dataset_permission: MagicMock,
    mock_permission: MagicMock,
    mock_readiness_section: MagicMock,
    mock_ui: MagicMock,
) -> None:
    manifest_service = MagicMock()
    manifest_service.get_alpaca_sip_summary.side_effect = RuntimeError("boom")
    readiness_service = MagicMock()
    readiness_service.get_readiness.side_effect = RuntimeError("boom")

    await dm_module._render_manifest_transparency(
        {"role": "operator"}, manifest_service, readiness_service
    )

    assert readiness_service.get_readiness.call_count == 2
    mock_ui.notify.assert_any_call("Manifest status temporarily unavailable", type="warning")
    mock_ui.notify.assert_any_call(
        "Some readiness checks are temporarily unavailable", type="warning"
    )
    assert mock_ui.notify.call_count == 2
    mock_readiness_section.render_readiness_section.assert_not_called()
    mock_permission.assert_called_once()
    assert mock_dataset_permission.call_count == 2


@pytest.mark.asyncio()
@patch("apps.web_console_ng.pages.data_management.ui")
@patch("apps.web_console_ng.pages.data_management._data_readiness_section")
@patch("apps.web_console_ng.pages.data_management._data_manifest_panel")
@patch("apps.web_console_ng.pages.data_management.has_permission", return_value=True)
@patch("apps.web_console_ng.pages.data_management.has_dataset_permission", return_value=True)
async def test_manifest_transparency_preserves_partial_readiness(
    mock_dataset_permission: MagicMock,
    mock_permission: MagicMock,
    mock_panel: MagicMock,
    mock_readiness_section: MagicMock,
    mock_ui: MagicMock,
) -> None:
    summary = MagicMock()
    manifest_service = MagicMock()
    manifest_service.get_alpaca_sip_summary.return_value = summary
    readiness = MagicMock()
    readiness_service = MagicMock()
    readiness_service.get_readiness.side_effect = [readiness, RuntimeError("boom")]

    await dm_module._render_manifest_transparency(
        {"role": "operator"}, manifest_service, readiness_service
    )

    mock_panel.render_manifest_transparency_panel.assert_called_once_with(summary)
    mock_readiness_section.render_readiness_section.assert_called_once_with([readiness])
    mock_ui.notify.assert_called_once_with(
        "Some readiness checks are temporarily unavailable", type="warning"
    )
    mock_permission.assert_called_once()
    assert mock_dataset_permission.call_count == 2


@pytest.mark.asyncio()
@patch("apps.web_console_ng.pages.data_management.ui")
@patch("apps.web_console_ng.pages.data_management._data_readiness_section")
@patch("apps.web_console_ng.pages.data_management._data_manifest_panel")
@patch("apps.web_console_ng.pages.data_management.has_permission", return_value=True)
@patch("apps.web_console_ng.pages.data_management.has_dataset_permission", return_value=True)
async def test_manifest_transparency_warns_for_invalid_readiness_target(
    mock_dataset_permission: MagicMock,
    mock_permission: MagicMock,
    mock_panel: MagicMock,
    mock_readiness_section: MagicMock,
    mock_ui: MagicMock,
) -> None:
    summary = MagicMock()
    manifest_service = MagicMock()
    manifest_service.get_alpaca_sip_summary.return_value = summary
    readiness_service = MagicMock()
    readiness_service.get_readiness.side_effect = ValueError("unsupported")

    await dm_module._render_manifest_transparency(
        {"role": "operator"}, manifest_service, readiness_service
    )

    mock_panel.render_manifest_transparency_panel.assert_called_once_with(summary)
    mock_readiness_section.render_readiness_section.assert_not_called()
    mock_ui.notify.assert_called_once_with(
        "Some readiness checks are temporarily unavailable", type="warning"
    )
    mock_permission.assert_called_once()
    assert mock_dataset_permission.call_count == 2


@pytest.mark.asyncio()
@patch("apps.web_console_ng.pages.data_management.ui")
async def test_data_quality_section_creates_tabs(
    mock_ui: MagicMock,
) -> None:
    """Quality section creates all four sub-tabs including Quarantine Inspector."""
    _setup_data_quality_ui(mock_ui)
    quality_service = _quality_service_for_section()

    result = await dm_module._render_data_quality_section({"role": "admin"}, quality_service)

    # Returns tuple of (alerts_container, scores_container, load_alerts_fn)
    assert isinstance(result, tuple)
    assert len(result) == 3
    assert callable(result[2])

    # Should create tabs
    tab_calls = [str(c) for c in mock_ui.tab.call_args_list]
    assert len(tab_calls) >= 4


@pytest.mark.asyncio()
@patch("apps.web_console_ng.pages.data_management.ui")
@patch("apps.web_console_ng.pages.data_management._data_quality_section")
@patch("apps.web_console_ng.pages.data_management.has_permission", return_value=True)
@patch("apps.web_console_ng.pages.data_management.has_dataset_permission", return_value=True)
async def test_data_quality_section_renders_alpaca_sip_quality_summary(
    mock_dataset_permission: MagicMock,
    mock_permission: MagicMock,
    mock_quality_section: MagicMock,
    mock_ui: MagicMock,
) -> None:
    _setup_data_quality_ui(mock_ui)
    quality_service = _quality_service_for_section()
    summary = MagicMock()
    quality_service.get_alpaca_sip_quality_summary = AsyncMock(return_value=summary)

    await dm_module._render_data_quality_section({"role": "admin"}, quality_service)

    quality_service.get_alpaca_sip_quality_summary.assert_awaited_once_with(
        {"role": "admin"},
        alpaca_sip_summary=None,
    )
    mock_quality_section.render_quality_summary.assert_called_once_with(summary)
    mock_ui.notify.assert_not_called()
    mock_dataset_permission.assert_any_call({"role": "admin"}, "alpaca_sip")
    mock_permission.assert_any_call({"role": "admin"}, Permission.VIEW_DATA_QUALITY)


@pytest.mark.asyncio()
@patch("apps.web_console_ng.pages.data_management.ui")
@patch("apps.web_console_ng.pages.data_management._data_quality_section")
@patch("apps.web_console_ng.pages.data_management.has_permission", return_value=True)
@patch("apps.web_console_ng.pages.data_management.has_dataset_permission", return_value=True)
async def test_data_quality_section_skips_for_quality_permission_error(
    mock_dataset_permission: MagicMock,
    mock_permission: MagicMock,
    mock_quality_section: MagicMock,
    mock_ui: MagicMock,
) -> None:
    _setup_data_quality_ui(mock_ui)
    quality_service = _quality_service_for_section()
    quality_service.get_alpaca_sip_quality_summary = AsyncMock(
        side_effect=PermissionError("denied")
    )

    await dm_module._render_data_quality_section({"role": "admin"}, quality_service)

    mock_quality_section.render_quality_summary.assert_not_called()
    quality_service.get_alpaca_sip_quality_summary.assert_awaited_once_with(
        {"role": "admin"},
        alpaca_sip_summary=None,
    )
    mock_ui.notify.assert_not_called()
    mock_dataset_permission.assert_any_call({"role": "admin"}, "alpaca_sip")
    mock_permission.assert_any_call({"role": "admin"}, Permission.VIEW_DATA_QUALITY)


@pytest.mark.asyncio()
@patch("apps.web_console_ng.pages.data_management.ui")
@patch("apps.web_console_ng.pages.data_management._data_quality_section")
@patch("apps.web_console_ng.pages.data_management.has_permission", return_value=True)
@patch("apps.web_console_ng.pages.data_management.has_dataset_permission", return_value=True)
async def test_data_quality_section_reuses_page_alpaca_sip_summary(
    mock_dataset_permission: MagicMock,
    mock_permission: MagicMock,
    mock_quality_section: MagicMock,
    mock_ui: MagicMock,
) -> None:
    _setup_data_quality_ui(mock_ui)
    quality_service = _quality_service_for_section()
    quality_summary = MagicMock()
    manifest_summary = MagicMock()
    quality_service.get_alpaca_sip_quality_summary = AsyncMock(return_value=quality_summary)

    await dm_module._render_data_quality_section(
        {"role": "admin"},
        quality_service,
        alpaca_sip_summary=manifest_summary,
    )

    quality_service.get_alpaca_sip_quality_summary.assert_awaited_once_with(
        {"role": "admin"},
        alpaca_sip_summary=manifest_summary,
    )
    quality_service.get_validation_results.assert_any_await(
        {"role": "admin"},
        dataset=None,
        alpaca_sip_summary=manifest_summary,
    )
    mock_quality_section.render_quality_summary.assert_called_once_with(quality_summary)
    mock_dataset_permission.assert_any_call({"role": "admin"}, "alpaca_sip")
    mock_permission.assert_any_call({"role": "admin"}, Permission.VIEW_DATA_QUALITY)


@pytest.mark.asyncio()
@patch("apps.web_console_ng.pages.data_management.ui")
@patch("apps.web_console_ng.pages.data_management._data_quality_section")
@patch("apps.web_console_ng.pages.data_management.has_permission", return_value=True)
@patch("apps.web_console_ng.pages.data_management.has_dataset_permission", return_value=True)
async def test_data_quality_section_warns_when_quality_summary_unavailable(
    mock_dataset_permission: MagicMock,
    mock_permission: MagicMock,
    mock_quality_section: MagicMock,
    mock_ui: MagicMock,
) -> None:
    _setup_data_quality_ui(mock_ui)
    quality_service = _quality_service_for_section()
    quality_service.get_alpaca_sip_quality_summary = AsyncMock(side_effect=RuntimeError("boom"))

    await dm_module._render_data_quality_section({"role": "admin"}, quality_service)

    mock_quality_section.render_quality_summary.assert_not_called()
    quality_service.get_alpaca_sip_quality_summary.assert_awaited_once_with(
        {"role": "admin"},
        alpaca_sip_summary=None,
    )
    mock_ui.notify.assert_called_once_with(
        "Alpaca SIP quality inputs temporarily unavailable", type="warning"
    )
    mock_dataset_permission.assert_any_call({"role": "admin"}, "alpaca_sip")
    mock_permission.assert_any_call({"role": "admin"}, Permission.VIEW_DATA_QUALITY)


@pytest.mark.asyncio()
@patch("apps.web_console_ng.pages.data_management.ui")
@patch("apps.web_console_ng.pages.data_management._data_quality_section")
@patch("apps.web_console_ng.pages.data_management.has_permission", return_value=False)
@patch("apps.web_console_ng.pages.data_management.has_dataset_permission", return_value=True)
async def test_data_quality_section_skips_quality_summary_without_view_permission(
    mock_dataset_permission: MagicMock,
    mock_permission: MagicMock,
    mock_quality_section: MagicMock,
    mock_ui: MagicMock,
) -> None:
    _setup_data_quality_ui(mock_ui)
    quality_service = _quality_service_for_section()

    await dm_module._render_data_quality_section({"role": "admin"}, quality_service)

    quality_service.get_alpaca_sip_quality_summary.assert_not_awaited()
    mock_quality_section.render_quality_summary.assert_not_called()
    assert mock_dataset_permission.call_count >= 1
    mock_permission.assert_any_call({"role": "admin"}, Permission.VIEW_DATA_QUALITY)


@pytest.mark.asyncio()
@patch("apps.web_console_ng.pages.data_management.ui")
@patch("apps.web_console_ng.pages.data_management._data_quality_section")
@patch("apps.web_console_ng.pages.data_management.has_permission", return_value=True)
@patch("apps.web_console_ng.pages.data_management.has_dataset_permission", return_value=False)
async def test_data_quality_section_skips_quality_summary_without_sip_access(
    mock_dataset_permission: MagicMock,
    mock_permission: MagicMock,
    mock_quality_section: MagicMock,
    mock_ui: MagicMock,
) -> None:
    _setup_data_quality_ui(mock_ui)
    quality_service = _quality_service_for_section()

    await dm_module._render_data_quality_section({"role": "admin"}, quality_service)

    quality_service.get_alpaca_sip_quality_summary.assert_not_awaited()
    mock_quality_section.render_quality_summary.assert_not_called()
    mock_dataset_permission.assert_any_call({"role": "admin"}, "alpaca_sip")
    mock_permission.assert_any_call({"role": "admin"}, Permission.VIEW_DATA_QUALITY)


@patch("apps.web_console_ng.pages.data_management.ui")
@patch("apps.web_console_ng.pages.data_management.has_permission", return_value=True)
def test_anomaly_alert_cards_disable_acknowledge_without_persistence(
    mock_permission: MagicMock,
    mock_ui: MagicMock,
) -> None:
    """Acknowledgment controls remain unavailable until persistence is durable."""
    label = MagicMock()
    label.classes.return_value = label
    mock_ui.label.return_value = label
    card_context = MagicMock()
    card_context.__enter__.return_value = card_context
    card_context.__exit__.return_value = False
    mock_ui.card.return_value.classes.return_value = card_context
    row_context = MagicMock()
    row_context.__enter__.return_value = row_context
    row_context.__exit__.return_value = False
    mock_ui.row.return_value.classes.return_value = row_context
    button = MagicMock()
    button.props.return_value = button
    button.classes.return_value = button
    mock_ui.button.return_value = button

    alerts = [
        MagicMock(
            id="alert-1",
            severity="warning",
            metric="row_drop",
            created_at=datetime(2026, 5, 1, 12, tzinfo=UTC),
            acknowledged=False,
            message="Placeholder alert",
            deviation_pct=None,
            current_value=1.0,
            expected_value=1.0,
        ),
        MagicMock(
            id="alert-2",
            severity="warning",
            metric="null_spike",
            created_at=datetime(2026, 5, 1, 12, tzinfo=UTC),
            acknowledged=False,
            message="Placeholder alert",
            deviation_pct=None,
            current_value=1.0,
            expected_value=1.0,
        ),
    ]
    quality_service = MagicMock()
    quality_service.acknowledgments_persistent = False

    dm_module._build_anomaly_alert_cards(
        alerts,
        {"role": "operator"},
        quality_service,
        {"alert-1": "medium", "alert-2": "medium"},
    )

    mock_ui.button.assert_not_called()
    label_texts = [call.args[0] for call in mock_ui.label.call_args_list if call.args]
    assert (
        label_texts.count(
            "Acknowledgment controls are unavailable until server-side persistence is enabled"
        )
        == 1
    )
    mock_permission.assert_called_once_with({"role": "operator"}, Permission.ACKNOWLEDGE_ALERTS)


@patch("apps.web_console_ng.pages.data_management.ui")
@patch("apps.web_console_ng.pages.data_management.has_permission", return_value=True)
def test_anomaly_alert_cards_render_acknowledge_action_with_persistence(
    mock_permission: MagicMock,
    mock_ui: MagicMock,
) -> None:
    label = MagicMock()
    label.classes.return_value = label
    mock_ui.label.return_value = label
    card_context = MagicMock()
    card_context.__enter__.return_value = card_context
    card_context.__exit__.return_value = False
    mock_ui.card.return_value.classes.return_value = card_context
    row_context = MagicMock()
    row_context.__enter__.return_value = row_context
    row_context.__exit__.return_value = False
    mock_ui.row.return_value.classes.return_value = row_context
    button = MagicMock()
    button.props.return_value = button
    button.classes.return_value = button
    mock_ui.button.return_value = button

    alert = MagicMock(
        id="alert-1",
        severity="warning",
        metric="row_drop",
        created_at=datetime(2026, 5, 1, 12, tzinfo=UTC),
        acknowledged=False,
        message="Placeholder alert",
        deviation_pct=None,
        current_value=1.0,
        expected_value=1.0,
    )
    quality_service = MagicMock()
    quality_service.acknowledgments_persistent = True

    dm_module._build_anomaly_alert_cards(
        [alert],
        {"role": "operator"},
        quality_service,
        {"alert-1": "medium"},
    )

    mock_ui.button.assert_called_once()
    label_texts = [call.args[0] for call in mock_ui.label.call_args_list if call.args]
    assert (
        "Acknowledgment controls are unavailable until server-side persistence is enabled"
        not in label_texts
    )
    mock_permission.assert_called_once_with({"role": "operator"}, Permission.ACKNOWLEDGE_ALERTS)


@pytest.mark.asyncio()
@patch("apps.web_console_ng.pages.data_management.ui")
async def test_module_exports(mock_ui: MagicMock) -> None:
    """Module exports data_management_page."""
    assert "data_management_page" in dm_module.__all__
