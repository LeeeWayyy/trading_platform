"""Tests for the extracted data sync NiceGUI component."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.web_console_ng.components import data_sync_section
from libs.web_console_services.data_manifest_service import ALPACA_SIP_DAILY_DATASET
from libs.web_console_services.data_sync_service import PreflightRequired, RateLimitExceeded
from libs.web_console_services.schemas.data_management import (
    DataAcquisitionJobDTO,
    DataAcquisitionPreflightDTO,
)


class _FakeElement:
    def __init__(self, value: Any = None) -> None:
        self.value = value
        self.cleared = False
        self.clear_count = 0
        self.on_value_change_cb: Any | None = None

    def classes(self, *_args: Any) -> _FakeElement:
        return self

    def props(self, *_args: Any) -> _FakeElement:
        return self

    def on_value_change(self, cb: Any) -> _FakeElement:
        self.on_value_change_cb = cb
        return self

    def clear(self) -> None:
        self.cleared = True
        self.clear_count += 1

    def __enter__(self) -> _FakeElement:
        return self

    def __exit__(self, *_args: Any) -> Literal[False]:
        return False


class _FakeUI:
    def __init__(self, *, input_values: dict[str, Any] | None = None) -> None:
        self.input_values = input_values or {}
        self.buttons: dict[str, Any] = {}
        self.notifications: list[tuple[str, str | None]] = []
        self.tables: list[dict[str, Any]] = []
        self.elements: dict[str, _FakeElement] = {}
        self.columns: list[_FakeElement] = []
        self.select_options: dict[str, tuple[Any, ...]] = {}

    def label(self, *_args: Any, **_kwargs: Any) -> _FakeElement:
        return _FakeElement()

    def separator(self, *_args: Any, **_kwargs: Any) -> _FakeElement:
        return _FakeElement()

    def row(self, *_args: Any, **_kwargs: Any) -> _FakeElement:
        return _FakeElement()

    def column(self, *_args: Any, **_kwargs: Any) -> _FakeElement:
        element = _FakeElement()
        self.columns.append(element)
        return element

    def select(self, *, label: str, value: Any, **_kwargs: Any) -> _FakeElement:
        element = _FakeElement(self.input_values.get(label, value))
        self.elements[label] = element
        self.select_options[label] = tuple(_kwargs.get("options", ()))
        return element

    def input(self, *, label: str, value: Any = "", **_kwargs: Any) -> _FakeElement:
        element = _FakeElement(self.input_values.get(label, value))
        self.elements[label] = element
        return element

    def switch(self, _label: str, *, value: bool) -> _FakeElement:
        element = _FakeElement(value)
        self.elements[_label] = element
        return element

    def button(self, label: str, *, on_click: Any, **_kwargs: Any) -> _FakeElement:
        self.buttons[label] = on_click
        return _FakeElement()

    def notify(self, message: str, *, type: str | None = None) -> None:
        self.notifications.append((message, type))

    def table(self, **kwargs: Any) -> _FakeElement:
        self.tables.append(kwargs)
        return _FakeElement()


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


@pytest.mark.asyncio()
async def test_render_sync_status_submit_before_preflight_warns() -> None:
    ui_module = _FakeUI()
    sync_service = MagicMock()
    sync_service.submit_acquisition = AsyncMock()

    await data_sync_section.render_sync_status(
        {"user_id": "operator"},
        sync_service,
        has_view=False,
        has_trigger=True,
        ui_module=ui_module,
    )
    await ui_module.buttons["Submit Job"]()

    assert ("Run preflight before submitting", "warning") in ui_module.notifications
    sync_service.submit_acquisition.assert_not_awaited()


@pytest.mark.asyncio()
async def test_render_sync_status_preflight_passes_raw_adjustment() -> None:
    ui_module = _FakeUI(
        input_values={
            "Start Date": "2026-01-03",
            "End Date": "2026-04-30",
            "Symbols": "MSFT,AAPL",
            "Reason": "fill gap",
        }
    )
    sync_service = MagicMock()
    captured: dict[str, Any] = {}

    async def preflight_side_effect(_user: Any, request: Any) -> DataAcquisitionPreflightDTO:
        captured["request"] = request
        return DataAcquisitionPreflightDTO(
            dataset=request.dataset,
            start_date=request.start_date,
            end_date=request.end_date,
            symbol_source=request.symbol_source,
            mode=request.mode,
            dry_run=request.dry_run,
            provider_id="alpaca_sip",
            source_feed="sip",
            canonical_storage_mode="raw",
            read_time_adjustment_mode="unavailable",
            adjustment_mode="raw",
            idempotency_key="acq_abc",
            submit_token="secret-submit-token",
            submit_token_expires_at=datetime(2026, 5, 1, 12, tzinfo=UTC),
            supported_semantics=["alpaca_sip_daily_sync_uses_calendar_year_partitions"],
            warnings=[],
            logs=[],
        )

    sync_service.preflight_acquisition = AsyncMock(side_effect=preflight_side_effect)

    await data_sync_section.render_sync_status(
        {"user_id": "operator"},
        sync_service,
        has_view=False,
        has_trigger=True,
        ui_module=ui_module,
    )
    await ui_module.buttons["Preflight"]()

    assert captured["request"].dataset == ALPACA_SIP_DAILY_DATASET
    assert captured["request"].adjustment_mode == "raw"
    assert ("Acquisition preflight ready", "positive") in ui_module.notifications
    assert ui_module.tables
    assert ui_module.select_options["Mode"] == ("backfill",)


@pytest.mark.asyncio()
async def test_render_sync_status_preserves_manual_sync_trigger() -> None:
    ui_module = _FakeUI(input_values={"Sync Reason": "operator rerun"})
    sync_job = MagicMock(id="sync-1", dataset="crsp")
    sync_service = MagicMock()
    sync_service.trigger_sync = AsyncMock(return_value=sync_job)

    await data_sync_section.render_sync_status(
        {"user_id": "operator"},
        sync_service,
        has_view=False,
        has_trigger=True,
        ui_module=ui_module,
    )
    await ui_module.buttons["Trigger Sync"]()

    assert "crsp" in ui_module.select_options["Sync Dataset"]
    sync_service.trigger_sync.assert_awaited_once_with(
        {"user_id": "operator"},
        "crsp",
        "operator rerun",
    )
    assert ("Sync job sync-1 queued for crsp", "positive") in ui_module.notifications


@pytest.mark.asyncio()
async def test_render_sync_status_successful_submit_clears_preflight_display() -> None:
    ui_module = _FakeUI(
        input_values={
            "Start Date": "2026-01-03",
            "End Date": "2026-04-30",
            "Symbols": "MSFT,AAPL",
            "Reason": "fill gap",
        }
    )
    preflight = DataAcquisitionPreflightDTO(
        dataset=ALPACA_SIP_DAILY_DATASET,
        start_date=date(2026, 1, 3),
        end_date=date(2026, 4, 30),
        symbol_source="AAPL,MSFT",
        mode="backfill",
        dry_run=True,
        provider_id="alpaca_sip",
        source_feed="sip",
        canonical_storage_mode="raw",
        read_time_adjustment_mode="unavailable",
        adjustment_mode="raw",
        idempotency_key="acq_abc",
        submit_token="secret-submit-token",
        submit_token_expires_at=datetime(2026, 5, 1, 12, tzinfo=UTC),
        supported_semantics=["alpaca_sip_daily_sync_uses_calendar_year_partitions"],
        warnings=[],
        logs=[],
    )
    job = DataAcquisitionJobDTO(
        id="job-1",
        dataset=ALPACA_SIP_DAILY_DATASET,
        status="completed",
        idempotency_key="acq_abc",
        mode="backfill",
        dry_run=True,
        provider_id="alpaca_sip",
        source_feed="sip",
        canonical_storage_mode="raw",
        read_time_adjustment_mode="unavailable",
        adjustment_mode="raw",
        started_at=datetime(2026, 5, 1, 12, tzinfo=UTC),
        submit_token_status="consumed",
        adapter="script:scripts/data/alpaca_sip_sync.py",
        produced_manifest_ids=[],
        validation_output=["preflight_passed"],
        logs=["job_queued"],
    )
    sync_service = MagicMock()
    sync_service.preflight_acquisition = AsyncMock(return_value=preflight)
    sync_service.submit_acquisition = AsyncMock(return_value=job)

    await data_sync_section.render_sync_status(
        {"user_id": "operator"},
        sync_service,
        has_view=False,
        has_trigger=True,
        ui_module=ui_module,
    )
    preflight_container = ui_module.columns[0]
    await ui_module.buttons["Preflight"]()
    clear_count_before_submit = preflight_container.clear_count
    await ui_module.buttons["Submit Job"]()

    assert preflight_container.clear_count == clear_count_before_submit + 1
    assert ui_module.elements["Start Date"].value == ""
    assert ui_module.elements["End Date"].value == ""
    assert ui_module.elements["Symbols"].value == ""
    assert ui_module.elements["Reason"].value == ""
    assert ui_module.elements["Mode"].value == "backfill"
    assert ui_module.elements["Dry Run"].value is True
    assert ("Acquisition job job-1 completed", "positive") in ui_module.notifications


@pytest.mark.asyncio()
async def test_render_sync_status_duplicate_submit_uses_scope_message() -> None:
    ui_module = _FakeUI(
        input_values={
            "Start Date": "2026-01-03",
            "End Date": "2026-04-30",
            "Symbols": "MSFT,AAPL",
            "Reason": "fill gap",
        }
    )
    preflight = DataAcquisitionPreflightDTO(
        dataset=ALPACA_SIP_DAILY_DATASET,
        start_date=date(2026, 1, 3),
        end_date=date(2026, 4, 30),
        symbol_source="AAPL,MSFT",
        mode="backfill",
        dry_run=True,
        provider_id="alpaca_sip",
        source_feed="sip",
        canonical_storage_mode="raw",
        read_time_adjustment_mode="unavailable",
        adjustment_mode="raw",
        idempotency_key="acq_abc",
        submit_token="secret-submit-token",
        submit_token_expires_at=datetime(2026, 5, 1, 12, tzinfo=UTC),
        supported_semantics=["alpaca_sip_daily_sync_uses_calendar_year_partitions"],
        warnings=[],
        logs=[],
    )
    job = DataAcquisitionJobDTO(
        id="job-1",
        dataset=ALPACA_SIP_DAILY_DATASET,
        status="completed",
        idempotency_key="acq_abc",
        mode="backfill",
        dry_run=True,
        provider_id="alpaca_sip",
        source_feed="sip",
        canonical_storage_mode="raw",
        read_time_adjustment_mode="unavailable",
        adjustment_mode="raw",
        started_at=datetime(2026, 5, 1, 12, tzinfo=UTC),
        submit_token_status="consumed",
        adapter="script:scripts/data/alpaca_sip_sync.py",
        produced_manifest_ids=[],
        validation_output=["preflight_passed"],
        logs=["job_queued", "duplicate_submission_reused_existing_job"],
    )
    sync_service = MagicMock()
    sync_service.preflight_acquisition = AsyncMock(return_value=preflight)
    sync_service.submit_acquisition = AsyncMock(return_value=job)

    await data_sync_section.render_sync_status(
        {"user_id": "operator"},
        sync_service,
        has_view=False,
        has_trigger=True,
        ui_module=ui_module,
    )
    await ui_module.buttons["Preflight"]()
    await ui_module.buttons["Submit Job"]()

    assert (
        "Acquisition scope already covered by job job-1 (completed)",
        "positive",
    ) in ui_module.notifications


@pytest.mark.asyncio()
async def test_render_sync_status_submit_error_clears_stale_preflight() -> None:
    ui_module = _FakeUI(
        input_values={
            "Start Date": "2026-01-03",
            "End Date": "2026-04-30",
            "Symbols": "MSFT,AAPL",
            "Reason": "fill gap",
        }
    )
    preflight = DataAcquisitionPreflightDTO(
        dataset=ALPACA_SIP_DAILY_DATASET,
        start_date=date(2026, 1, 3),
        end_date=date(2026, 4, 30),
        symbol_source="AAPL,MSFT",
        mode="backfill",
        dry_run=True,
        provider_id="alpaca_sip",
        source_feed="sip",
        canonical_storage_mode="raw",
        read_time_adjustment_mode="unavailable",
        adjustment_mode="raw",
        idempotency_key="acq_abc",
        submit_token="secret-submit-token",
        submit_token_expires_at=datetime(2026, 5, 1, 12, tzinfo=UTC),
        supported_semantics=["alpaca_sip_daily_sync_uses_calendar_year_partitions"],
        warnings=[],
        logs=[],
    )
    sync_service = MagicMock()
    sync_service.preflight_acquisition = AsyncMock(return_value=preflight)
    sync_service.submit_acquisition = AsyncMock(
        side_effect=PreflightRequired("Current acquisition preflight required")
    )

    await data_sync_section.render_sync_status(
        {"user_id": "operator"},
        sync_service,
        has_view=False,
        has_trigger=True,
        ui_module=ui_module,
    )
    preflight_container = ui_module.columns[0]
    await ui_module.buttons["Preflight"]()
    clear_count_before_submit = preflight_container.clear_count
    await ui_module.buttons["Submit Job"]()
    await ui_module.buttons["Submit Job"]()

    assert preflight_container.clear_count == clear_count_before_submit + 1
    assert ("Current acquisition preflight required", "warning") in ui_module.notifications
    assert ("Run preflight before submitting", "warning") in ui_module.notifications
    sync_service.submit_acquisition.assert_awaited_once()


@pytest.mark.asyncio()
async def test_render_sync_status_preflight_rate_limit_notifies() -> None:
    ui_module = _FakeUI(
        input_values={
            "Start Date": "2026-01-03",
            "End Date": "2026-04-30",
            "Reason": "fill gap",
        }
    )
    sync_service = MagicMock()
    sync_service.preflight_acquisition = AsyncMock(side_effect=RateLimitExceeded("blocked"))

    await data_sync_section.render_sync_status(
        {"user_id": "operator"},
        sync_service,
        has_view=False,
        has_trigger=True,
        ui_module=ui_module,
    )
    await ui_module.buttons["Preflight"]()

    assert ("Rate limit reached; wait before retrying", "warning") in ui_module.notifications


@pytest.mark.asyncio()
async def test_render_sync_status_input_change_invalidates_preflight() -> None:
    ui_module = _FakeUI(
        input_values={
            "Start Date": "2026-01-03",
            "End Date": "2026-04-30",
            "Symbols": "MSFT,AAPL",
            "Reason": "fill gap",
        }
    )
    preflight = DataAcquisitionPreflightDTO(
        dataset=ALPACA_SIP_DAILY_DATASET,
        start_date=date(2026, 1, 3),
        end_date=date(2026, 4, 30),
        symbol_source="AAPL,MSFT",
        mode="backfill",
        dry_run=True,
        provider_id="alpaca_sip",
        source_feed="sip",
        canonical_storage_mode="raw",
        read_time_adjustment_mode="unavailable",
        adjustment_mode="raw",
        idempotency_key="acq_abc",
        submit_token="secret-submit-token",
        submit_token_expires_at=datetime(2026, 5, 1, 12, tzinfo=UTC),
        supported_semantics=["alpaca_sip_daily_sync_uses_calendar_year_partitions"],
        warnings=[],
        logs=[],
    )
    sync_service = MagicMock()
    sync_service.preflight_acquisition = AsyncMock(return_value=preflight)
    sync_service.submit_acquisition = AsyncMock()

    await data_sync_section.render_sync_status(
        {"user_id": "operator"},
        sync_service,
        has_view=False,
        has_trigger=True,
        ui_module=ui_module,
    )
    await ui_module.buttons["Preflight"]()
    ui_module.elements["Symbols"].value = "GOOG"
    on_value_change_cb = ui_module.elements["Symbols"].on_value_change_cb
    assert on_value_change_cb is not None
    on_value_change_cb(None)
    await ui_module.buttons["Submit Job"]()

    assert ("Run preflight before submitting", "warning") in ui_module.notifications
    sync_service.submit_acquisition.assert_not_awaited()


@pytest.mark.asyncio()
async def test_render_sync_status_ignores_stale_inflight_preflight_after_edit() -> None:
    ui_module = _FakeUI(
        input_values={
            "Start Date": "2026-01-03",
            "End Date": "2026-04-30",
            "Symbols": "MSFT,AAPL",
            "Reason": "fill gap",
        }
    )
    started = asyncio.Event()
    release = asyncio.Event()
    preflight = DataAcquisitionPreflightDTO(
        dataset=ALPACA_SIP_DAILY_DATASET,
        start_date=date(2026, 1, 3),
        end_date=date(2026, 4, 30),
        symbol_source="AAPL,MSFT",
        mode="backfill",
        dry_run=True,
        provider_id="alpaca_sip",
        source_feed="sip",
        canonical_storage_mode="raw",
        read_time_adjustment_mode="unavailable",
        adjustment_mode="raw",
        idempotency_key="acq_abc",
        submit_token="secret-submit-token",
        submit_token_expires_at=datetime(2026, 5, 1, 12, tzinfo=UTC),
        supported_semantics=["alpaca_sip_daily_sync_uses_calendar_year_partitions"],
        warnings=[],
        logs=[],
    )

    async def preflight_side_effect(_user: Any, _request: Any) -> DataAcquisitionPreflightDTO:
        started.set()
        await release.wait()
        return preflight

    sync_service = MagicMock()
    sync_service.preflight_acquisition = AsyncMock(side_effect=preflight_side_effect)
    sync_service.submit_acquisition = AsyncMock()

    await data_sync_section.render_sync_status(
        {"user_id": "operator"},
        sync_service,
        has_view=False,
        has_trigger=True,
        ui_module=ui_module,
    )
    preflight_task = asyncio.create_task(ui_module.buttons["Preflight"]())
    await asyncio.wait_for(started.wait(), timeout=1)
    ui_module.elements["Symbols"].value = "GOOG"
    on_value_change_cb = ui_module.elements["Symbols"].on_value_change_cb
    assert on_value_change_cb is not None
    on_value_change_cb(None)
    release.set()
    await preflight_task
    await ui_module.buttons["Submit Job"]()

    assert ("Acquisition inputs changed; rerun preflight", "warning") in ui_module.notifications
    assert ("Acquisition preflight ready", "positive") not in ui_module.notifications
    assert not ui_module.tables
    assert ("Run preflight before submitting", "warning") in ui_module.notifications
    sync_service.submit_acquisition.assert_not_awaited()


@pytest.mark.asyncio()
async def test_render_sync_status_failed_preflight_clears_previous_token() -> None:
    ui_module = _FakeUI(
        input_values={
            "Start Date": "2026-01-03",
            "End Date": "2026-04-30",
            "Symbols": "MSFT,AAPL",
            "Reason": "fill gap",
        }
    )
    preflight = DataAcquisitionPreflightDTO(
        dataset=ALPACA_SIP_DAILY_DATASET,
        start_date=date(2026, 1, 3),
        end_date=date(2026, 4, 30),
        symbol_source="AAPL,MSFT",
        mode="backfill",
        dry_run=True,
        provider_id="alpaca_sip",
        source_feed="sip",
        canonical_storage_mode="raw",
        read_time_adjustment_mode="unavailable",
        adjustment_mode="raw",
        idempotency_key="acq_abc",
        submit_token="secret-submit-token",
        submit_token_expires_at=datetime(2026, 5, 1, 12, tzinfo=UTC),
        supported_semantics=["alpaca_sip_daily_sync_uses_calendar_year_partitions"],
        warnings=[],
        logs=[],
    )
    sync_service = MagicMock()
    sync_service.preflight_acquisition = AsyncMock(side_effect=[preflight, ValueError("bad dates")])
    sync_service.submit_acquisition = AsyncMock()

    await data_sync_section.render_sync_status(
        {"user_id": "operator"},
        sync_service,
        has_view=False,
        has_trigger=True,
        ui_module=ui_module,
    )
    await ui_module.buttons["Preflight"]()
    await ui_module.buttons["Preflight"]()
    await ui_module.buttons["Submit Job"]()

    assert ("bad dates", "warning") in ui_module.notifications
    assert ("Run preflight before submitting", "warning") in ui_module.notifications
    sync_service.submit_acquisition.assert_not_awaited()


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


def test_normalize_sync_reason_strips_whitespace() -> None:
    assert data_sync_section._normalize_sync_reason("  backfill gap  ") == "backfill gap"
    assert data_sync_section._normalize_sync_reason("   ") == ""


def test_parse_date_input_validates_iso_dates() -> None:
    assert data_sync_section._parse_date_input("2026-05-01", "start date") == date(2026, 5, 1)

    with pytest.raises(ValueError, match="Invalid start date"):
        data_sync_section._parse_date_input("05/01/2026", "start date")
    with pytest.raises(ValueError, match="Invalid start date"):
        data_sync_section._parse_date_input("20260501", "start date")


def test_normalize_acquisition_mode_rejects_unknown_values() -> None:
    assert data_sync_section._normalize_acquisition_mode("backfill") == "backfill"

    for unsupported in ("incremental", "full"):
        with pytest.raises(ValueError, match="Mode must be backfill"):
            data_sync_section._normalize_acquisition_mode(unsupported)


def test_acquisition_preflight_rows_hide_submit_token() -> None:
    preflight = DataAcquisitionPreflightDTO(
        dataset="alpaca_sip_daily",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        symbol_source="all",
        mode="backfill",
        dry_run=True,
        provider_id="alpaca_sip",
        source_feed="sip",
        canonical_storage_mode="raw",
        read_time_adjustment_mode="unavailable",
        adjustment_mode="raw",
        idempotency_key="acq_abc",
        submit_token="secret-submit-token",
        submit_token_expires_at=datetime(2026, 5, 1, 12, tzinfo=UTC),
        supported_semantics=["alpaca_sip_daily_sync_uses_calendar_year_partitions"],
        warnings=["raw_sip_returns_unavailable"],
        logs=["submit_token_value_hidden_from_ui_logs"],
    )

    rows = data_sync_section.build_acquisition_preflight_rows(preflight)
    values = " ".join(row["value"] for row in rows)
    fields = [row["field"] for row in rows]

    assert "secret-submit-token" not in values
    assert "active" in values
    assert "acq_abc" in values
    assert "Requested date range" in fields
    assert "Effective acquisition scope" in fields


def test_acquisition_job_rows_expose_state_without_token_value() -> None:
    job = DataAcquisitionJobDTO(
        id="job-1",
        dataset="alpaca_sip_daily",
        status="queued",
        idempotency_key="acq_abc",
        mode="backfill",
        dry_run=True,
        provider_id="alpaca_sip",
        source_feed="sip",
        canonical_storage_mode="raw",
        read_time_adjustment_mode="unavailable",
        adjustment_mode="raw",
        started_at=datetime(2026, 5, 1, 12, tzinfo=UTC),
        submit_token_status="consumed",
        adapter="script:scripts/data/alpaca_sip_sync.py",
        produced_manifest_ids=[],
        validation_output=["preflight_passed"],
        logs=["job_queued"],
    )

    rows = data_sync_section.build_acquisition_job_rows(job)
    values = " ".join(row["value"] for row in rows)

    assert "consumed" in values
    assert "true" in values
    assert "script:scripts/data/alpaca_sip_sync.py" in values
    assert "submit-token" not in values
