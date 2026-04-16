"""Tests for research workspace read-model helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from libs.web_console_services.research_workspace_service import (
    LIFECYCLE_ARCHIVED,
    LIFECYCLE_CANDIDATE,
    LIFECYCLE_FAILED,
    LIFECYCLE_LIVE,
    LIFECYCLE_SHADOW,
    LIFECYCLE_UNLINKED,
    OpsModelRow,
    ResearchSignalRow,
    ResearchWorkspaceService,
    derive_lifecycle_label,
)


def test_derive_lifecycle_label_failed_precedence() -> None:
    assert (
        derive_lifecycle_label(
            ops_status="active",
            research_status="failed",
            linked=True,
        )
        == LIFECYCLE_FAILED
    )


def test_derive_lifecycle_label_live() -> None:
    assert (
        derive_lifecycle_label(
            ops_status="active",
            research_status="production",
            linked=True,
        )
        == LIFECYCLE_LIVE
    )


def test_derive_lifecycle_label_shadow() -> None:
    assert (
        derive_lifecycle_label(
            ops_status="testing",
            research_status="production",
            linked=True,
        )
        == LIFECYCLE_SHADOW
    )


def test_derive_lifecycle_label_candidate() -> None:
    assert (
        derive_lifecycle_label(
            ops_status="inactive",
            research_status="staged",
            linked=True,
        )
        == LIFECYCLE_CANDIDATE
    )


def test_derive_lifecycle_label_archived() -> None:
    assert (
        derive_lifecycle_label(
            ops_status="inactive",
            research_status="archived",
            linked=True,
        )
        == LIFECYCLE_ARCHIVED
    )


def test_derive_lifecycle_label_unlinked() -> None:
    assert (
        derive_lifecycle_label(
            ops_status="inactive",
            research_status="production",
            linked=False,
        )
        == LIFECYCLE_UNLINKED
    )


@pytest.mark.asyncio()
async def test_list_lifecycle_rows_links_by_primary_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Primary (strategy_name, version) key should link deterministically."""
    service = ResearchWorkspaceService(registry_dir=Path("data/models"))
    research_rows = [
        ResearchSignalRow(
            signal_id="sig-1",
            display_name="alpha-a",
            strategy_name="alpha_a",
            version="v1",
            research_status="production",
            backtest_job_id=None,
            snapshot_id="snap-1",
            dataset_version_ids={},
            config_hash="cfg-1",
            mean_ic=0.1,
            icir=1.0,
        )
    ]
    ops_rows = [
        OpsModelRow(
            strategy_name="alpha_a",
            version="v1",
            ops_status="active",
            model_path=None,
            performance_metrics=None,
            backtest_job_id=None,
        )
    ]

    monkeypatch.setattr(
        service,
        "list_research_signals",
        lambda *, limit=500: research_rows,
    )

    async def _fake_list_ops_models(*, user: dict[str, Any], model_service: Any) -> list[OpsModelRow]:
        return ops_rows

    monkeypatch.setattr(service, "list_ops_models", _fake_list_ops_models)

    rows = await service.list_lifecycle_rows(user={}, model_service=object())

    assert len(rows) == 1
    assert rows[0].linked is True
    assert rows[0].linkage_key == "primary:alpha_a:v1"
    assert rows[0].lifecycle_label == LIFECYCLE_LIVE


@pytest.mark.asyncio()
async def test_list_lifecycle_rows_links_by_secondary_backtest_job_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Secondary backtest_job_id link applies when primary key does not match."""
    service = ResearchWorkspaceService(registry_dir=Path("data/models"))
    research_rows = [
        ResearchSignalRow(
            signal_id="sig-2",
            display_name="alpha-b",
            strategy_name="alpha_b",
            version="v2",
            research_status="staged",
            backtest_job_id="bt-200",
            snapshot_id=None,
            dataset_version_ids={},
            config_hash="cfg-2",
            mean_ic=None,
            icir=None,
        )
    ]
    ops_rows = [
        OpsModelRow(
            strategy_name="alpha_other",
            version="v9",
            ops_status="testing",
            model_path=None,
            performance_metrics=None,
            backtest_job_id="bt-200",
        )
    ]

    monkeypatch.setattr(
        service,
        "list_research_signals",
        lambda *, limit=500: research_rows,
    )

    async def _fake_list_ops_models(*, user: dict[str, Any], model_service: Any) -> list[OpsModelRow]:
        return ops_rows

    monkeypatch.setattr(service, "list_ops_models", _fake_list_ops_models)

    rows = await service.list_lifecycle_rows(user={}, model_service=object())

    assert len(rows) == 1
    assert rows[0].linked is True
    assert rows[0].linkage_key == "secondary:bt-200"
    assert rows[0].lifecycle_label == LIFECYCLE_SHADOW


@pytest.mark.asyncio()
async def test_list_lifecycle_rows_unlinked_when_keys_missing_or_mismatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rows remain unlinked/non-actionable when neither linkage key resolves."""
    service = ResearchWorkspaceService(registry_dir=Path("data/models"))
    research_rows = [
        ResearchSignalRow(
            signal_id="sig-3",
            display_name="alpha-c",
            strategy_name="alpha_c",
            version="v1",
            research_status="production",
            backtest_job_id=None,
            snapshot_id=None,
            dataset_version_ids={},
            config_hash="cfg-3",
            mean_ic=None,
            icir=None,
        )
    ]
    ops_rows = [
        OpsModelRow(
            strategy_name="alpha_x",
            version="v9",
            ops_status="inactive",
            model_path=None,
            performance_metrics=None,
            backtest_job_id=None,
        )
    ]

    monkeypatch.setattr(
        service,
        "list_research_signals",
        lambda *, limit=500: research_rows,
    )

    async def _fake_list_ops_models(*, user: dict[str, Any], model_service: Any) -> list[OpsModelRow]:
        return ops_rows

    monkeypatch.setattr(service, "list_ops_models", _fake_list_ops_models)

    rows = await service.list_lifecycle_rows(user={}, model_service=object())

    assert len(rows) == 2
    ops_row = next(row for row in rows if row.strategy_name == "alpha_x")
    assert ops_row.linked is False
    assert ops_row.lifecycle_label == LIFECYCLE_UNLINKED
    assert ops_row.linkage_key == "unlinked:alpha_x:v9"
