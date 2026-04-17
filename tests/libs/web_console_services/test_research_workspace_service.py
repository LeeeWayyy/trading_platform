"""Tests for research workspace read-model helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from libs.web_console_services import research_workspace_service as workspace_module
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


def test_resolve_research_strategy_name_uses_defined_key_precedence() -> None:
    parameters = {
        "alpha_name": "alpha_fallback",
        "strategy_id": "strategy_from_id",
        "name": "name_fallback",
    }
    assert (
        workspace_module._resolve_research_strategy_name(parameters, default="unassigned")
        == "strategy_from_id"
    )


def test_resolve_research_strategy_name_uses_alpha_names_list() -> None:
    parameters = {"alpha_names": [" ", "alpha_from_list"]}
    assert (
        workspace_module._resolve_research_strategy_name(parameters, default="unassigned")
        == "alpha_from_list"
    )


def test_list_research_signals_respects_zero_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit zero limit should return zero rows."""
    service = ResearchWorkspaceService(registry_dir=Path("data/models"))
    metadata = SimpleNamespace(
        model_id="alpha-1",
        version="v1",
        parameters={},
        metrics={},
        snapshot_id=None,
        dataset_version_ids={},
        config_hash="cfg-1",
    )

    class _StubRegistry:
        def list_models(self, *, model_type: str) -> list[Any]:
            assert model_type == "alpha_weights"
            return [metadata]

        def get_model_info_bulk(
            self,
            model_type: str,
            versions: list[str],
        ) -> dict[str, dict[str, str]]:
            assert model_type == "alpha_weights"
            assert versions == ["v1"]
            return {"v1": {"status": "staged"}}

    monkeypatch.setattr(service, "_registry", _StubRegistry())

    assert service.list_research_signals(limit=0) == []


def test_list_research_signals_applies_limit_before_bulk_info_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bulk info lookup should only include versions inside the requested limit."""
    service = ResearchWorkspaceService(registry_dir=Path("data/models"))
    metadata_rows = [
        SimpleNamespace(
            model_id="alpha-1",
            version="v1",
            parameters={},
            metrics={},
            snapshot_id=None,
            dataset_version_ids={},
            config_hash="cfg-1",
        ),
        SimpleNamespace(
            model_id="alpha-2",
            version="v2",
            parameters={},
            metrics={},
            snapshot_id=None,
            dataset_version_ids={},
            config_hash="cfg-2",
        ),
    ]

    queried_versions: list[str] = []

    class _StubRegistry:
        def list_models(self, *, model_type: str) -> list[Any]:
            assert model_type == "alpha_weights"
            return metadata_rows

        def get_model_info_bulk(
            self,
            model_type: str,
            versions: list[str],
        ) -> dict[str, dict[str, str]]:
            assert model_type == "alpha_weights"
            queried_versions.extend(versions)
            return {version: {"status": "staged"} for version in versions}

    monkeypatch.setattr(service, "_registry", _StubRegistry())

    rows = service.list_research_signals(limit=1)

    assert len(rows) == 1
    assert rows[0].version == "v1"
    assert queried_versions == ["v1"]


@pytest.mark.asyncio()
async def test_list_ops_models_prefers_bulk_fetch_when_available() -> None:
    """Service should use bulk model fetch to avoid per-strategy N+1 queries."""
    service = ResearchWorkspaceService(registry_dir=Path("data/models"))

    class _BulkModelService:
        def __init__(self) -> None:
            self.bulk_called = False
            self.strategy_list_called = False
            self.per_strategy_called = False

        async def list_models_for_strategies(
            self,
            user: dict[str, Any],
            *,
            strategy_names: list[str] | None = None,
        ) -> dict[str, list[dict[str, Any]]]:
            self.bulk_called = True
            assert strategy_names is None
            return {
                "alpha_main": [
                    {
                        "strategy_name": "alpha_main",
                        "version": "v1",
                        "status": "active",
                        "model_path": "/tmp/model",
                        "performance_metrics": {"ic": 0.1},
                        "config": {"backtest_job_id": "bt-1"},
                    }
                ]
            }

        async def list_strategies_with_models(self, user: dict[str, Any]) -> list[dict[str, Any]]:
            self.strategy_list_called = True
            return []

        async def get_models_for_strategy(
            self,
            strategy_name: str,
            user: dict[str, Any],
        ) -> list[dict[str, Any]]:
            self.per_strategy_called = True
            return []

    model_service = _BulkModelService()
    rows = await service.list_ops_models(user={"user_id": "u-1"}, model_service=model_service)  # type: ignore[arg-type]

    assert model_service.bulk_called is True
    assert model_service.strategy_list_called is False
    assert model_service.per_strategy_called is False
    assert len(rows) == 1
    assert rows[0].strategy_name == "alpha_main"
    assert rows[0].backtest_job_id == "bt-1"


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
