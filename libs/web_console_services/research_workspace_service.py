"""Read-model adapter for consolidated research workspace.

This service aggregates:
- Ops registry rows (Postgres model_registry via ModelRegistryBrowserService)
- Research registry rows (DuckDB/file ModelRegistry)

No storage merge occurs; this is a UI-only adapter.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from libs.models.models.registry import ModelRegistry
from libs.models.models.types import ModelType

if TYPE_CHECKING:
    from libs.web_console_services.model_registry_browser_service import (
        ModelRegistryBrowserService,
    )

LIFECYCLE_FAILED = "FAILED"
LIFECYCLE_LIVE = "LIVE"
LIFECYCLE_SHADOW = "SHADOW"
LIFECYCLE_CANDIDATE = "CANDIDATE"
LIFECYCLE_ARCHIVED = "ARCHIVED"
LIFECYCLE_UNLINKED = "UNLINKED"


class ResearchSignalRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_id: str
    display_name: str
    strategy_name: str
    version: str
    research_status: str
    backtest_job_id: str | None
    snapshot_id: str | None
    dataset_version_ids: dict[str, str] = Field(default_factory=dict)
    config_hash: str
    mean_ic: float | None
    icir: float | None


class OpsModelRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_name: str
    version: str
    ops_status: str
    model_path: str | None
    performance_metrics: dict[str, Any] | None
    backtest_job_id: str | None


class LifecycleRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_name: str
    version: str
    ops_status: str | None
    research_status: str | None
    lifecycle_label: str
    linkage_key: str
    linked: bool
    signal_id: str | None
    backtest_job_id: str | None
    snapshot_id: str | None
    dataset_version_ids: dict[str, str] = Field(default_factory=dict)
    config_hash: str | None


def _to_optional_float(value: Any) -> float | None:
    """Parse optional numeric value without raising on malformed payloads."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def derive_lifecycle_label(
    *,
    ops_status: str | None,
    research_status: str | None,
    linked: bool,
) -> str:
    """Derive lifecycle label with fixed precedence."""
    normalized_ops = (ops_status or "").strip().lower()
    normalized_research = (research_status or "").strip().lower()

    if not linked:
        return LIFECYCLE_UNLINKED
    if normalized_ops == "failed" or normalized_research == "failed":
        return LIFECYCLE_FAILED
    if normalized_ops == "active":
        return LIFECYCLE_LIVE
    if normalized_ops == "testing":
        return LIFECYCLE_SHADOW
    if normalized_ops == "inactive" and normalized_research in {"staged", "production"}:
        return LIFECYCLE_CANDIDATE
    if normalized_ops == "inactive" and normalized_research == "archived":
        return LIFECYCLE_ARCHIVED
    return LIFECYCLE_UNLINKED


def _resolve_research_strategy_name(parameters: dict[str, Any], default: str) -> str:
    """Derive strategy name from research metadata parameters."""
    strategy_like = parameters.get("strategy_name") or parameters.get("strategy_id")
    if isinstance(strategy_like, str) and strategy_like.strip():
        return strategy_like.strip()

    alpha_name = parameters.get("alpha_name")
    if isinstance(alpha_name, str) and alpha_name.strip():
        return alpha_name.strip()

    alpha_names = parameters.get("alpha_names")
    if isinstance(alpha_names, list) and alpha_names:
        first = alpha_names[0]
        if isinstance(first, str) and first.strip():
            return first.strip()

    name = parameters.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()

    return default


class ResearchWorkspaceService:
    """Aggregates research + ops rows for /research workspace."""

    def __init__(self, *, registry_dir: Path) -> None:
        self._registry = ModelRegistry(registry_dir=registry_dir)

    def list_research_signals(self, *, limit: int = 500) -> list[ResearchSignalRow]:
        """List research registry alpha rows with status/provenance details."""
        normalized_limit = max(0, limit)
        if normalized_limit == 0:
            return []

        models = self._registry.list_models(model_type=ModelType.alpha_weights.value)
        if not models:
            return []

        limited_models = models[:normalized_limit]
        versions = [metadata.version for metadata in limited_models]
        info_map = self._registry.get_model_info_bulk(ModelType.alpha_weights.value, versions)
        result: list[ResearchSignalRow] = []
        for metadata in limited_models:
            params = metadata.parameters if isinstance(metadata.parameters, dict) else {}
            metrics = metadata.metrics if isinstance(metadata.metrics, dict) else {}
            info = info_map.get(metadata.version, {})
            research_status = str(info.get("status") or "unknown")
            backtest_job_id_raw = params.get("backtest_job_id")
            backtest_job_id = (
                str(backtest_job_id_raw).strip() if backtest_job_id_raw else None
            )
            result.append(
                ResearchSignalRow(
                    signal_id=metadata.model_id,
                    display_name=str(params.get("name") or metadata.model_id),
                    strategy_name=_resolve_research_strategy_name(params, default="unassigned"),
                    version=metadata.version,
                    research_status=research_status,
                    backtest_job_id=backtest_job_id,
                    snapshot_id=metadata.snapshot_id,
                    dataset_version_ids=dict(metadata.dataset_version_ids),
                    config_hash=metadata.config_hash,
                    mean_ic=_to_optional_float(metrics.get("mean_ic")),
                    icir=_to_optional_float(metrics.get("icir")),
                )
            )
        return result

    async def list_ops_models(
        self,
        *,
        user: dict[str, Any],
        model_service: ModelRegistryBrowserService,
    ) -> list[OpsModelRow]:
        """List ops registry model rows visible to the user."""
        bulk_fetch = getattr(model_service, "list_models_for_strategies", None)
        if callable(bulk_fetch):
            models_by_strategy = await bulk_fetch(user)
        else:
            strategy_rows = await model_service.list_strategies_with_models(user)
            models_by_strategy = {}
            for strategy in strategy_rows:
                strategy_name = str(strategy.get("strategy_name") or "").strip()
                if not strategy_name:
                    continue
                models_by_strategy[strategy_name] = await model_service.get_models_for_strategy(
                    strategy_name,
                    user,
                )

        result: list[OpsModelRow] = []
        for strategy_name, models in models_by_strategy.items():
            strategy_name = str(strategy_name).strip()
            if not strategy_name:
                continue
            for model in models:
                config_payload = model.get("config")
                backtest_job_id: str | None = None
                if isinstance(config_payload, dict):
                    raw_backtest_job_id = config_payload.get("backtest_job_id")
                    if raw_backtest_job_id:
                        backtest_job_id = str(raw_backtest_job_id).strip()
                result.append(
                    OpsModelRow(
                        strategy_name=strategy_name,
                        version=str(model.get("version") or "").strip(),
                        ops_status=str(model.get("status") or "unknown"),
                        model_path=(
                            str(model.get("model_path")).strip()
                            if model.get("model_path")
                            else None
                        ),
                        performance_metrics=(
                            model.get("performance_metrics")
                            if isinstance(model.get("performance_metrics"), dict)
                            else None
                        ),
                        backtest_job_id=backtest_job_id,
                    )
                )
        return result

    async def list_lifecycle_rows(
        self,
        *,
        user: dict[str, Any],
        model_service: ModelRegistryBrowserService,
    ) -> list[LifecycleRow]:
        """Join ops + research rows with deterministic linkage and derived lifecycle."""
        research_task = asyncio.to_thread(self.list_research_signals)
        ops_task = self.list_ops_models(user=user, model_service=model_service)
        research_rows, ops_rows = await asyncio.gather(research_task, ops_task)

        research_by_primary: dict[tuple[str, str], ResearchSignalRow] = {}
        research_by_secondary: dict[str, ResearchSignalRow] = {}
        for row in research_rows:
            primary = (row.strategy_name, row.version)
            research_by_primary[primary] = row
            if row.backtest_job_id:
                research_by_secondary[row.backtest_job_id] = row

        result: list[LifecycleRow] = []
        used_signals: set[str] = set()
        for ops in ops_rows:
            linkage_key = f"unlinked:{ops.strategy_name}:{ops.version or 'unknown'}"
            linked_row = research_by_primary.get((ops.strategy_name, ops.version))
            if linked_row is not None:
                linkage_key = f"primary:{ops.strategy_name}:{ops.version}"
            elif ops.backtest_job_id:
                linked_row = research_by_secondary.get(ops.backtest_job_id)
                if linked_row is not None:
                    linkage_key = f"secondary:{ops.backtest_job_id}"

            linked = linked_row is not None
            lifecycle_label = derive_lifecycle_label(
                ops_status=ops.ops_status,
                research_status=(linked_row.research_status if linked_row else None),
                linked=linked,
            )
            result.append(
                LifecycleRow(
                    strategy_name=ops.strategy_name,
                    version=ops.version,
                    ops_status=ops.ops_status,
                    research_status=linked_row.research_status if linked_row else None,
                    lifecycle_label=lifecycle_label,
                    linkage_key=linkage_key,
                    linked=linked,
                    signal_id=linked_row.signal_id if linked_row else None,
                    backtest_job_id=(
                        linked_row.backtest_job_id if linked_row else ops.backtest_job_id
                    ),
                    snapshot_id=linked_row.snapshot_id if linked_row else None,
                    dataset_version_ids=linked_row.dataset_version_ids if linked_row else {},
                    config_hash=linked_row.config_hash if linked_row else None,
                )
            )
            if linked_row:
                used_signals.add(linked_row.signal_id)

        for research in research_rows:
            if research.signal_id in used_signals:
                continue
            result.append(
                LifecycleRow(
                    strategy_name=research.strategy_name,
                    version=research.version,
                    ops_status=None,
                    research_status=research.research_status,
                    lifecycle_label=LIFECYCLE_UNLINKED,
                    linkage_key=f"unlinked:{research.strategy_name}:{research.version}",
                    linked=False,
                    signal_id=research.signal_id,
                    backtest_job_id=research.backtest_job_id,
                    snapshot_id=research.snapshot_id,
                    dataset_version_ids=research.dataset_version_ids,
                    config_hash=research.config_hash,
                )
            )

        return result
