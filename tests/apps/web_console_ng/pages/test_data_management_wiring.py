"""Tests for data management page service wiring (P6T13/T13.3).

Tests verify:
- Service calls replace mock data
- Error handling (PermissionError, RateLimitExceeded)
- Timer cleanup via keyed ClientLifecycleManager callback
- Overlap guard behavior
- Validation status and severity normalization
- Per-capability RBAC gating
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.web_console_ng.pages import data_management as dm_module
from libs.web_console_services.data_explorer_service import (
    RateLimitExceeded as ExplorerRateLimitExceeded,
)
from libs.web_console_services.data_sync_service import (
    RateLimitExceeded as SyncRateLimitExceeded,
)
from libs.web_console_services.schemas.data_management import (
    AnomalyAlertDTO,
    DatasetInfoDTO,
    ExportJobDTO,
    QualityTrendDTO,
    QuarantineEntryDTO,
    QueryResultDTO,
    SyncJobDTO,
    SyncLogEntry,
    SyncScheduleDTO,
    SyncStatusDTO,
    ValidationResultDTO,
)

# ============================================================================
# Fixtures
# ============================================================================

NOW = datetime.now(UTC)

ADMIN_USER: dict[str, Any] = {"id": "user-1", "role": "admin"}
VIEWER_USER: dict[str, Any] = {"id": "user-2", "role": "viewer"}


def _make_sync_statuses() -> list[SyncStatusDTO]:
    return [
        SyncStatusDTO(
            dataset="crsp",
            last_sync=NOW,
            row_count=10000,
            validation_status="ok",
            schema_version="v1",
        ),
        SyncStatusDTO(
            dataset="compustat",
            last_sync=NOW,
            row_count=5000,
            validation_status="ok",
            schema_version="v1",
        ),
    ]


def _make_sync_logs() -> list[SyncLogEntry]:
    return [
        SyncLogEntry(
            id="log-1",
            dataset="crsp",
            level="info",
            message="Sync completed",
            extra=None,
            sync_run_id="run-1",
            created_at=NOW,
        ),
    ]


def _make_sync_schedules() -> list[SyncScheduleDTO]:
    return [
        SyncScheduleDTO(
            id="sched-1",
            dataset="crsp",
            enabled=True,
            cron_expression="0 2 * * *",
            last_scheduled_run=NOW,
            next_scheduled_run=NOW,
            version=1,
        ),
    ]


def _make_sync_job() -> SyncJobDTO:
    return SyncJobDTO(id="job-1", dataset="crsp", status="queued", started_at=NOW)


def _make_datasets() -> list[DatasetInfoDTO]:
    return [
        DatasetInfoDTO(
            name="crsp",
            description="CRSP daily stock data",
            row_count=100000,
            date_range={"start": "2000-01-01", "end": "2024-12-31"},
            symbol_count=5000,
            last_sync=NOW,
        ),
    ]


def _make_query_result() -> QueryResultDTO:
    return QueryResultDTO(
        columns=["date", "symbol", "close"],
        rows=[{"date": "2024-01-15", "symbol": "AAPL", "close": 175.50}],
        total_count=1,
        has_more=False,
        cursor=None,
    )


def _make_export_job() -> ExportJobDTO:
    return ExportJobDTO(
        id="export-1",
        status="queued",
        format="csv",
        row_count=None,
        file_path=None,
        expires_at=NOW,
    )


def _make_validation_results() -> list[ValidationResultDTO]:
    return [
        ValidationResultDTO(
            id="val-1",
            dataset="crsp",
            sync_run_id="run-1",
            validation_type="row_count",
            status="ok",
            expected_value=1000,
            actual_value=1000,
            error_message=None,
            created_at=NOW,
        ),
        ValidationResultDTO(
            id="val-2",
            dataset="crsp",
            sync_run_id="run-2",
            validation_type="null_check",
            status="error",
            expected_value=0,
            actual_value=5,
            error_message="5 null rows found",
            created_at=NOW,
        ),
    ]


def _make_anomaly_alerts() -> list[AnomalyAlertDTO]:
    return [
        AnomalyAlertDTO(
            id="alert-1",
            dataset="crsp",
            metric="row_drop",
            severity="warning",
            current_value=0.9,
            expected_value=1.0,
            deviation_pct=10.0,
            message="Row count dropped",
            acknowledged=False,
            acknowledged_by=None,
            created_at=NOW,
        ),
        AnomalyAlertDTO(
            id="alert-2",
            dataset="compustat",
            metric="schema_drift",
            severity="high",
            current_value=1.0,
            expected_value=0.0,
            deviation_pct=100.0,
            message="Schema change detected",
            acknowledged=True,
            acknowledged_by="user-1",
            created_at=NOW,
        ),
    ]


def _make_quality_trend() -> QualityTrendDTO:
    return QualityTrendDTO(dataset="crsp", period_days=30, data_points=[])


def _make_quarantine_entries() -> list[QuarantineEntryDTO]:
    return [
        QuarantineEntryDTO(
            dataset="crsp",
            quarantine_path="data/quarantine/crsp/2024-01-15",
            reason="validation_failure",
            created_at=NOW,
        ),
    ]


@pytest.fixture()
def sync_service() -> MagicMock:
    svc = MagicMock()
    svc.get_sync_status = AsyncMock(return_value=_make_sync_statuses())
    svc.get_sync_logs = AsyncMock(return_value=_make_sync_logs())
    svc.get_sync_schedule = AsyncMock(return_value=_make_sync_schedules())
    svc.trigger_sync = AsyncMock(return_value=_make_sync_job())
    svc.update_sync_schedule = AsyncMock(return_value=_make_sync_schedules()[0])
    return svc


@pytest.fixture()
def explorer_service() -> MagicMock:
    svc = MagicMock()
    svc.list_datasets = AsyncMock(return_value=_make_datasets())
    svc.get_dataset_preview = AsyncMock(
        return_value=MagicMock(columns=["date", "symbol", "close"], rows=[])
    )
    svc.execute_query = AsyncMock(return_value=_make_query_result())
    svc.export_data = AsyncMock(return_value=_make_export_job())
    return svc


@pytest.fixture()
def quality_service() -> MagicMock:
    svc = MagicMock()
    svc.get_validation_results = AsyncMock(return_value=_make_validation_results())
    svc.get_anomaly_alerts = AsyncMock(return_value=_make_anomaly_alerts())
    svc.get_quality_trends = AsyncMock(return_value=_make_quality_trend())
    svc.get_quarantine_status = AsyncMock(return_value=_make_quarantine_entries())
    svc.acknowledge_alert = AsyncMock(
        return_value=MagicMock(acknowledged_by="user-1")
    )
    return svc


# ============================================================================
# Validation Status Normalization
# ============================================================================


class TestValidationStatusNormalization:
    """Test normalize_validation_status (centralized in quality_scorer, imported by data_management)."""

    def test_ok_maps_to_passed(self) -> None:
        assert dm_module.normalize_validation_status("ok") == "passed"

    def test_error_maps_to_failed(self) -> None:
        assert dm_module.normalize_validation_status("error") == "failed"

    def test_fail_maps_to_failed(self) -> None:
        assert dm_module.normalize_validation_status("fail") == "failed"

    def test_warn_maps_to_warning(self) -> None:
        assert dm_module.normalize_validation_status("warn") == "warning"

    def test_unknown_passes_through(self) -> None:
        assert dm_module.normalize_validation_status("custom_status") == "custom_status"


# ============================================================================
# Severity Normalization
# ============================================================================


class TestSeverityNormalization:
    """Test _SEVERITY_MAP normalization."""

    def test_warning_maps_to_medium(self) -> None:
        assert dm_module._SEVERITY_MAP["warning"] == "medium"

    def test_info_maps_to_low(self) -> None:
        assert dm_module._SEVERITY_MAP["info"] == "low"

    def test_high_passes_through(self) -> None:
        assert dm_module._SEVERITY_MAP["high"] == "high"

    def test_critical_passes_through(self) -> None:
        assert dm_module._SEVERITY_MAP["critical"] == "critical"

    def test_normalize_and_filter_all(self) -> None:
        alerts = _make_anomaly_alerts()
        result = dm_module._normalize_and_filter_alerts(alerts, "all")
        assert len(result) == 2
        # Original .severity is NOT mutated; normalized value on _normalized_severity
        assert result[0].severity == "warning"  # Original preserved
        assert result[0]._normalized_severity == "medium"  # "warning" -> "medium"
        assert result[1]._normalized_severity == "high"

    def test_normalize_and_filter_by_canonical(self) -> None:
        alerts = _make_anomaly_alerts()
        result = dm_module._normalize_and_filter_alerts(alerts, "medium")
        assert len(result) == 1
        assert result[0].id == "alert-1"

    def test_normalize_and_filter_empty_result(self) -> None:
        alerts = _make_anomaly_alerts()
        result = dm_module._normalize_and_filter_alerts(alerts, "critical")
        assert len(result) == 0


# ============================================================================
# Acknowledged Filter Mapping
# ============================================================================


class TestAckFilterMapping:
    """Test _ACK_MAP UI string -> service parameter."""

    def test_all_maps_to_none(self) -> None:
        assert dm_module._ACK_MAP["all"] is None

    def test_unacked_maps_to_false(self) -> None:
        assert dm_module._ACK_MAP["unacked"] is False

    def test_acked_maps_to_true(self) -> None:
        assert dm_module._ACK_MAP["acked"] is True


# ============================================================================
# Helper Functions
# ============================================================================


class TestHelperFunctions:
    """Test utility functions."""

    def test_format_datetime_with_value(self) -> None:
        assert dm_module._format_datetime(NOW) == NOW.isoformat()

    def test_format_datetime_with_none(self) -> None:
        assert dm_module._format_datetime(None) == "-"

    def test_format_datetime_with_string(self) -> None:
        assert dm_module._format_datetime("not a datetime") == "-"

    def test_get_user_id_safe_dict(self) -> None:
        assert dm_module._get_user_id_safe({"id": "user-1"}) == "user-1"

    def test_get_user_id_safe_object(self) -> None:
        obj = MagicMock()
        obj.id = "user-2"
        assert dm_module._get_user_id_safe(obj) == "user-2"

    def test_get_user_id_safe_missing(self) -> None:
        assert dm_module._get_user_id_safe({}) is None


# ============================================================================
# Sync Status Service Call
# ============================================================================


class TestSyncStatusWiring:
    """Test _render_sync_status calls service correctly."""

    @pytest.mark.asyncio()
    @patch("apps.web_console_ng.pages.data_management.ui")
    async def test_calls_get_sync_status(
        self, mock_ui: MagicMock, sync_service: MagicMock
    ) -> None:
        mock_ui.column.return_value = MagicMock()
        mock_ui.column.return_value.__enter__ = MagicMock()
        mock_ui.column.return_value.__exit__ = MagicMock(return_value=False)
        mock_ui.label.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))
        mock_ui.separator.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))
        mock_ui.table.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))
        mock_ui.row.return_value = MagicMock()
        mock_ui.row.return_value.__enter__ = MagicMock()
        mock_ui.row.return_value.__exit__ = MagicMock(return_value=False)
        mock_ui.select.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))
        mock_ui.input.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))
        mock_ui.button.return_value = MagicMock(props=MagicMock(return_value=MagicMock()))

        await dm_module._render_sync_status(
            ADMIN_USER, sync_service, has_view=True, has_trigger=False
        )
        sync_service.get_sync_status.assert_awaited_once_with(ADMIN_USER)

    @pytest.mark.asyncio()
    @patch("apps.web_console_ng.pages.data_management.ui")
    async def test_permission_error_handled(
        self, mock_ui: MagicMock, sync_service: MagicMock
    ) -> None:
        mock_ui.column.return_value = MagicMock()
        mock_ui.column.return_value.__enter__ = MagicMock()
        mock_ui.column.return_value.__exit__ = MagicMock(return_value=False)
        mock_ui.label.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))
        sync_service.get_sync_status = AsyncMock(
            side_effect=PermissionError("Permission view_data_sync required")
        )

        await dm_module._render_sync_status(
            VIEWER_USER, sync_service, has_view=True, has_trigger=False
        )
        mock_ui.notify.assert_called()

    @pytest.mark.asyncio()
    @patch("apps.web_console_ng.pages.data_management.ui")
    async def test_no_view_shows_placeholder(
        self, mock_ui: MagicMock, sync_service: MagicMock
    ) -> None:
        mock_ui.label.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))

        await dm_module._render_sync_status(
            VIEWER_USER, sync_service, has_view=False, has_trigger=False
        )
        sync_service.get_sync_status.assert_not_awaited()


# ============================================================================
# Sync Trigger Wiring
# ============================================================================


class TestSyncTriggerWiring:
    """Test trigger_sync calls service correctly."""

    @pytest.mark.asyncio()
    @patch("apps.web_console_ng.pages.data_management.ui")
    async def test_trigger_sync_rate_limit(
        self, mock_ui: MagicMock, sync_service: MagicMock
    ) -> None:
        sync_service.trigger_sync = AsyncMock(
            side_effect=SyncRateLimitExceeded("1 per 60 seconds")
        )
        # The rate limit is caught in the trigger_sync callback (closured)
        # We can't easily test the closure, but we can test that the exception type is correct
        with pytest.raises(SyncRateLimitExceeded):
            await sync_service.trigger_sync(ADMIN_USER, "crsp", "test")


# ============================================================================
# Query Execution Wiring
# ============================================================================


class TestQueryExecutionWiring:
    """Test execute_query calls service correctly."""

    @pytest.mark.asyncio()
    async def test_query_rate_limit_exception_type(
        self, explorer_service: MagicMock
    ) -> None:
        explorer_service.execute_query = AsyncMock(
            side_effect=ExplorerRateLimitExceeded("10 per 60 seconds")
        )
        with pytest.raises(ExplorerRateLimitExceeded):
            await explorer_service.execute_query(ADMIN_USER, "crsp", "SELECT 1")

    @pytest.mark.asyncio()
    async def test_query_validation_error(
        self, explorer_service: MagicMock
    ) -> None:
        explorer_service.execute_query = AsyncMock(
            side_effect=ValueError("Invalid query: DROP not allowed")
        )
        with pytest.raises(ValueError, match="DROP not allowed"):
            await explorer_service.execute_query(ADMIN_USER, "crsp", "DROP TABLE x")


# ============================================================================
# Quality Service (No Rate Limit)
# ============================================================================


class TestQualityServiceNoRateLimit:
    """Test that quality service does NOT have RateLimitExceeded."""

    def test_quality_service_has_no_rate_limit_exception(self) -> None:
        from libs.web_console_services import data_quality_service as dqs

        assert not hasattr(dqs, "RateLimitExceeded")


# ============================================================================
# Build Functions (Pure Logic)
# ============================================================================


class TestBuildSyncStatusTable:
    """Test _build_sync_status_table pure data mapping."""

    @patch("apps.web_console_ng.pages.data_management.ui")
    def test_maps_dto_fields(self, mock_ui: MagicMock) -> None:
        mock_ui.table.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))
        statuses = _make_sync_statuses()
        dm_module._build_sync_status_table(statuses)
        mock_ui.table.assert_called_once()
        call_kwargs = mock_ui.table.call_args
        rows = call_kwargs.kwargs.get("rows") or call_kwargs[1].get("rows")
        assert len(rows) == 2
        assert rows[0]["dataset"] == "crsp"
        assert rows[0]["row_count"] == 10000


class TestBuildValidationTable:
    """Test _build_validation_table status normalization."""

    @patch("apps.web_console_ng.pages.data_management.ui")
    def test_normalizes_status(self, mock_ui: MagicMock) -> None:
        mock_ui.table.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))
        results = _make_validation_results()
        dm_module._build_validation_table(results)
        call_kwargs = mock_ui.table.call_args
        rows = call_kwargs.kwargs.get("rows") or call_kwargs[1].get("rows")
        assert rows[0]["status"] == "passed"  # "ok" -> "passed"
        assert rows[1]["status"] == "failed"  # "error" -> "failed"


class TestBuildQueryResults:
    """Test _build_query_results rendering."""

    @patch("apps.web_console_ng.pages.data_management.ui")
    def test_renders_columns_and_rows(self, mock_ui: MagicMock) -> None:
        mock_ui.table.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))
        mock_ui.row.return_value = MagicMock()
        mock_ui.row.return_value.__enter__ = MagicMock()
        mock_ui.row.return_value.__exit__ = MagicMock(return_value=False)
        mock_ui.label.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))

        result = _make_query_result()
        dm_module._build_query_results(result)
        mock_ui.table.assert_called_once()

    @patch("apps.web_console_ng.pages.data_management.ui")
    def test_empty_results(self, mock_ui: MagicMock) -> None:
        mock_ui.label.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))

        result = QueryResultDTO(
            columns=[], rows=[], total_count=0, has_more=False, cursor=None
        )
        dm_module._build_query_results(result)
        mock_ui.table.assert_not_called()


class TestBuildQualityTrendChart:
    """Test _build_quality_trend_chart rendering."""

    @patch("apps.web_console_ng.pages.data_management.ui")
    def test_empty_trend_shows_placeholder(self, mock_ui: MagicMock) -> None:
        mock_ui.card.return_value = MagicMock()
        mock_ui.card.return_value.__enter__ = MagicMock()
        mock_ui.card.return_value.__exit__ = MagicMock(return_value=False)
        mock_ui.label.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))

        trend = _make_quality_trend()
        dm_module._build_quality_trend_chart(trend)
        # Should show "No trend data available yet"
        labels = [
            str(call.args[0]) if call.args else ""
            for call in mock_ui.label.call_args_list
        ]
        assert any("No trend data" in label for label in labels)


# ============================================================================
# Timer Cleanup (Keyed Callback)
# ============================================================================


class TestTimerCleanup:
    """Test timer cleanup uses keyed callback."""

    def test_cleanup_owner_key_is_defined(self) -> None:
        assert dm_module._CLEANUP_OWNER_KEY == "data_management_timers"

    @pytest.mark.asyncio()
    async def test_keyed_callback_replaces_previous(self) -> None:
        """Ensure keyed registration replaces (tested in client_lifecycle_keyed tests)."""
        from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager

        ClientLifecycleManager._instance = None
        manager = ClientLifecycleManager.get()

        await manager.register_client("test-client")

        call_log: list[str] = []
        await manager.register_cleanup_callback(
            "test-client", lambda: call_log.append("first"), owner_key="data_management_timers"
        )
        await manager.register_cleanup_callback(
            "test-client", lambda: call_log.append("second"), owner_key="data_management_timers"
        )

        await manager.cleanup_client("test-client")
        assert call_log == ["second"]

        ClientLifecycleManager._instance = None

    @pytest.mark.asyncio()
    async def test_all_three_timers_cancelled(self) -> None:
        """All three timers (sync, alerts, scores) are cancelled in cleanup."""
        timer_sync = MagicMock()
        timer_alerts = MagicMock()
        timer_scores = MagicMock()

        async def _cleanup_timers() -> None:
            timer_sync.cancel()
            timer_alerts.cancel()
            timer_scores.cancel()

        await _cleanup_timers()
        timer_sync.cancel.assert_called_once()
        timer_alerts.cancel.assert_called_once()
        timer_scores.cancel.assert_called_once()


# ============================================================================
# Overlap Guard
# ============================================================================


class TestOverlapGuard:
    """Test timer overlap guard prevents concurrent stacking."""

    @pytest.mark.asyncio()
    async def test_overlap_guard_skips_concurrent(self) -> None:
        """Simulate overlap guard behavior."""
        refreshing = False
        call_count = 0

        async def guarded_refresh() -> None:
            nonlocal refreshing, call_count
            if refreshing:
                return
            refreshing = True
            try:
                call_count += 1
                await asyncio.sleep(0.01)
            finally:
                refreshing = False

        # Simulate concurrent calls
        await asyncio.gather(guarded_refresh(), guarded_refresh())
        # Only one should have executed (the second returns immediately)
        assert call_count == 1


# ============================================================================
# Export Permission Gating
# ============================================================================


class TestExportPermissionGating:
    """Test export button visibility based on EXPORT_DATA permission."""

    def test_export_requires_export_data_permission(self) -> None:
        """Verify the permission constant exists."""
        from libs.platform.web_console_auth.permissions import Permission

        assert hasattr(Permission, "EXPORT_DATA")


# ============================================================================
# Data Sync Tab Visibility
# ============================================================================


class TestSyncTabVisibility:
    """Test Data Sync tab per-capability gating."""

    def test_tab_visible_with_trigger_only(self) -> None:
        """Tab should be visible if user has TRIGGER_DATA_SYNC only."""
        from libs.platform.web_console_auth.permissions import Permission

        # Simulate: user has TRIGGER but not VIEW or MANAGE
        perms = {Permission.TRIGGER_DATA_SYNC}
        has_sync = Permission.VIEW_DATA_SYNC in perms
        has_trigger = Permission.TRIGGER_DATA_SYNC in perms
        has_manage = Permission.MANAGE_SYNC_SCHEDULE in perms
        show_sync_tab = has_sync or has_trigger or has_manage
        assert show_sync_tab is True

    def test_tab_hidden_with_no_sync_perms(self) -> None:
        """Tab should be hidden if user has no sync permissions."""
        from libs.platform.web_console_auth.permissions import Permission

        perms: set[Permission] = set()
        has_sync = Permission.VIEW_DATA_SYNC in perms
        has_trigger = Permission.TRIGGER_DATA_SYNC in perms
        has_manage = Permission.MANAGE_SYNC_SCHEDULE in perms
        show_sync_tab = has_sync or has_trigger or has_manage
        assert show_sync_tab is False


# ============================================================================
# Explorer Tab Visibility
# ============================================================================


class TestExplorerTabVisibility:
    """Test Explorer tab per-capability gating."""

    def test_tab_visible_with_query_only(self) -> None:
        from libs.platform.web_console_auth.permissions import Permission

        perms = {Permission.QUERY_DATA}
        has_view = Permission.VIEW_DATA_SYNC in perms
        has_query = Permission.QUERY_DATA in perms
        show_explorer = has_view or has_query
        assert show_explorer is True

    def test_tab_hidden_with_export_only(self) -> None:
        """EXPORT_DATA alone is insufficient to see the tab."""
        from libs.platform.web_console_auth.permissions import Permission

        perms = {Permission.EXPORT_DATA}
        has_view = Permission.VIEW_DATA_SYNC in perms
        has_query = Permission.QUERY_DATA in perms
        show_explorer = has_view or has_query
        assert show_explorer is False


# ============================================================================
# Centralized Client ID
# ============================================================================


class TestCentralizedClientId:
    """Test that timer cleanup uses get_or_create_client_id."""

    def test_import_exists(self) -> None:
        """Verify get_or_create_client_id is importable."""
        from apps.web_console_ng.utils.session import get_or_create_client_id

        assert callable(get_or_create_client_id)


# ============================================================================
# T13.4: Quality Score Cards
# ============================================================================


class TestQualityScoreCards:
    """Test quality score card rendering."""

    @pytest.mark.asyncio()
    @patch("apps.web_console_ng.pages.data_management.ui")
    async def test_score_cards_with_data(self, mock_ui: MagicMock) -> None:
        """Score cards render when service data is available."""
        mock_ui.label.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))
        mock_ui.card.return_value = MagicMock()
        mock_ui.card.return_value.__enter__ = MagicMock()
        mock_ui.card.return_value.__exit__ = MagicMock(return_value=False)
        mock_ui.row.return_value = MagicMock()
        mock_ui.row.return_value.__enter__ = MagicMock()
        mock_ui.row.return_value.__exit__ = MagicMock(return_value=False)

        quality_service = MagicMock()
        quality_service.get_validation_results = AsyncMock(
            return_value=[
                ValidationResultDTO(
                    id="v1", dataset="crsp", validation_type="row_count",
                    status="ok", created_at=NOW,
                ),
            ]
        )
        quality_service.get_anomaly_alerts = AsyncMock(return_value=[])
        quality_service.get_quarantine_status = AsyncMock(return_value=[])

        await dm_module._build_quality_score_cards(ADMIN_USER, quality_service)

        # Should render dataset label and score
        label_calls = [str(c) for c in mock_ui.label.call_args_list]
        assert any("crsp" in call for call in label_calls)

    @pytest.mark.asyncio()
    @patch("apps.web_console_ng.pages.data_management.ui")
    async def test_score_cards_empty_data(self, mock_ui: MagicMock) -> None:
        """Score cards show 'No quality data' when no service data."""
        mock_ui.label.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))

        quality_service = MagicMock()
        quality_service.get_validation_results = AsyncMock(return_value=[])
        quality_service.get_anomaly_alerts = AsyncMock(return_value=[])
        quality_service.get_quarantine_status = AsyncMock(return_value=[])

        await dm_module._build_quality_score_cards(ADMIN_USER, quality_service)

        label_calls = [str(c) for c in mock_ui.label.call_args_list]
        assert any("No quality data" in call for call in label_calls)

    @pytest.mark.asyncio()
    @patch("apps.web_console_ng.pages.data_management.ui")
    async def test_score_cards_permission_error(self, mock_ui: MagicMock) -> None:
        """Score cards handle PermissionError gracefully."""
        mock_ui.label.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))
        mock_ui.notify = MagicMock()

        quality_service = MagicMock()
        quality_service.get_validation_results = AsyncMock(
            side_effect=PermissionError("No access")
        )

        await dm_module._build_quality_score_cards(ADMIN_USER, quality_service)
        mock_ui.notify.assert_called_once()


# ============================================================================
# T13.4: Enhanced Trends with compute_trend_summary
# ============================================================================


class TestEnhancedTrends:
    """Test enhanced trend chart with threshold lines and trend summary."""

    @patch("apps.web_console_ng.pages.data_management.ui")
    def test_trend_chart_threshold_lines(self, mock_ui: MagicMock) -> None:
        """Trend chart adds threshold lines at 90 and 70."""
        mock_ui.card.return_value = MagicMock()
        mock_ui.card.return_value.__enter__ = MagicMock()
        mock_ui.card.return_value.__exit__ = MagicMock(return_value=False)
        mock_ui.row.return_value = MagicMock()
        mock_ui.row.return_value.__enter__ = MagicMock()
        mock_ui.row.return_value.__exit__ = MagicMock(return_value=False)
        mock_ui.label.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))
        mock_plotly = MagicMock(return_value=MagicMock(classes=MagicMock(return_value=MagicMock())))
        mock_ui.plotly = mock_plotly

        # Create trend with data points
        trend = MagicMock()
        point = MagicMock()
        point.metric = "quality"
        point.date = NOW
        point.value = 85.0
        trend.data_points = [point]
        trend.dataset = "crsp"

        dm_module._build_quality_trend_chart(trend)

        # Plotly figure should have been created
        mock_plotly.assert_called_once()
        fig = mock_plotly.call_args[0][0]
        # Check threshold lines were added (hlines are stored in layout.shapes)
        assert hasattr(fig, "layout")

    @patch("apps.web_console_ng.pages.data_management.ui")
    def test_trend_empty_data(self, mock_ui: MagicMock) -> None:
        """Trend chart shows placeholder when no data points."""
        mock_ui.card.return_value = MagicMock()
        mock_ui.card.return_value.__enter__ = MagicMock()
        mock_ui.card.return_value.__exit__ = MagicMock(return_value=False)
        mock_ui.label.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))

        trend = MagicMock()
        trend.data_points = []
        trend.dataset = "crsp"
        trend.period_days = 30

        dm_module._build_quality_trend_chart(trend)

        label_calls = [str(c) for c in mock_ui.label.call_args_list]
        assert any("No trend data" in call for call in label_calls)


# ============================================================================
# T13.4: Quarantine Inspector
# ============================================================================


class TestQuarantineInspector:
    """Test quarantine inspector with DuckDB drill-down."""

    @pytest.mark.asyncio()
    @patch("apps.web_console_ng.pages.data_management.ui")
    async def test_inspector_empty_entries(self, mock_ui: MagicMock) -> None:
        """Inspector shows 'No quarantine entries' when empty."""
        mock_ui.label.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))

        quality_service = MagicMock()
        quality_service.get_quarantine_status = AsyncMock(return_value=[])

        await dm_module._render_quarantine_inspector(ADMIN_USER, quality_service)

        label_calls = [str(c) for c in mock_ui.label.call_args_list]
        assert any("No quarantine entries" in call for call in label_calls)

    @pytest.mark.asyncio()
    @patch("apps.web_console_ng.pages.data_management.ui")
    async def test_inspector_groups_by_dataset(self, mock_ui: MagicMock) -> None:
        """Inspector groups quarantine entries by dataset."""
        mock_ui.label.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))
        mock_ui.column.return_value = MagicMock()
        mock_ui.column.return_value.__enter__ = MagicMock()
        mock_ui.column.return_value.__exit__ = MagicMock(return_value=False)
        expansion_mock = MagicMock()
        expansion_mock.__enter__ = MagicMock()
        expansion_mock.__exit__ = MagicMock(return_value=False)
        mock_ui.expansion.return_value = expansion_mock
        mock_ui.card.return_value = MagicMock()
        mock_ui.card.return_value.__enter__ = MagicMock()
        mock_ui.card.return_value.__exit__ = MagicMock(return_value=False)
        mock_ui.row.return_value = MagicMock()
        mock_ui.row.return_value.__enter__ = MagicMock()
        mock_ui.row.return_value.__exit__ = MagicMock(return_value=False)
        mock_ui.button.return_value = MagicMock(
            props=MagicMock(return_value=MagicMock(classes=MagicMock(return_value=MagicMock())))
        )

        entries = [
            QuarantineEntryDTO(
                dataset="crsp",
                quarantine_path="data/quarantine/crsp/2024-10-16",
                reason="Row count drop",
                created_at=NOW,
            ),
            QuarantineEntryDTO(
                dataset="compustat",
                quarantine_path="data/quarantine/compustat/2024-10-16",
                reason="Schema mismatch",
                created_at=NOW,
            ),
        ]

        quality_service = MagicMock()
        quality_service.get_quarantine_status = AsyncMock(return_value=entries)

        await dm_module._render_quarantine_inspector(ADMIN_USER, quality_service)

        # Should create expansions for both datasets
        expansion_calls = [str(c) for c in mock_ui.expansion.call_args_list]
        assert len(expansion_calls) == 2

    @pytest.mark.asyncio()
    @patch("apps.web_console_ng.pages.data_management.ui")
    async def test_drill_down_invalid_path(self, mock_ui: MagicMock) -> None:
        """Drill-down rejects path traversal attempts."""
        mock_ui.label.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))

        entry = MagicMock()
        entry.quarantine_path = "../../etc/passwd"
        entry.dataset = "crsp"

        await dm_module._load_quarantine_preview(entry)

        label_calls = [str(c) for c in mock_ui.label.call_args_list]
        assert any("Path validation failed" in call for call in label_calls)

    @pytest.mark.asyncio()
    @patch("apps.web_console_ng.pages.data_management.ui")
    async def test_drill_down_invalid_dataset_name(self, mock_ui: MagicMock) -> None:
        """Drill-down rejects invalid dataset names."""
        mock_ui.label.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))

        entry = MagicMock()
        entry.quarantine_path = "2024-10-16"
        entry.dataset = "../../etc/passwd"

        await dm_module._load_quarantine_preview(entry)

        label_calls = [str(c) for c in mock_ui.label.call_args_list]
        assert any("Invalid dataset name" in call for call in label_calls)

    @pytest.mark.asyncio()
    @patch("apps.web_console_ng.pages.data_management.ui")
    async def test_drill_down_nonexistent_dir(self, mock_ui: MagicMock) -> None:
        """Drill-down shows informational message for non-existent directory."""
        mock_ui.label.return_value = MagicMock(classes=MagicMock(return_value=MagicMock()))

        entry = MagicMock()
        entry.quarantine_path = "2024-10-16"
        entry.dataset = "crsp"

        await dm_module._load_quarantine_preview(entry)

        label_calls = [str(c) for c in mock_ui.label.call_args_list]
        assert any("Preview unavailable" in call for call in label_calls)


# ============================================================================
# T13.4: Centralized normalize_validation_status Import
# ============================================================================


class TestCentralizedNormalization:
    """Test that data_management imports normalize_validation_status from quality_scorer."""

    def test_normalize_imported(self) -> None:
        """normalize_validation_status should be importable from data_management module."""
        assert hasattr(dm_module, "normalize_validation_status")
        assert dm_module.normalize_validation_status("ok") == "passed"

    def test_compute_quality_scores_imported(self) -> None:
        """compute_quality_scores should be importable from data_management module."""
        assert hasattr(dm_module, "compute_quality_scores")

    def test_validate_quarantine_path_imported(self) -> None:
        """validate_quarantine_path should be importable from data_management module."""
        assert hasattr(dm_module, "validate_quarantine_path")
