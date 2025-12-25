"""Shared dataset loading helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import streamlit as st

from libs.common.async_utils import run_async

if TYPE_CHECKING:
    from apps.web_console.services.data_explorer_service import DataExplorerService
    from apps.web_console.services.data_quality_service import DataQualityService
    from apps.web_console.services.data_sync_service import DataSyncService

_FETCH_TIMEOUT_SECONDS = 10.0


def load_user_datasets(service: DataExplorerService, user: Any) -> list[str]:
    """Load datasets the user has access to via DataExplorerService."""
    try:
        with st.spinner("Loading datasets..."):
            datasets = run_async(service.list_datasets(user), timeout=_FETCH_TIMEOUT_SECONDS)
    except Exception as exc:
        st.error(f"Failed to load datasets: {exc}")
        return []
    return [ds.name for ds in datasets]


def load_sync_datasets(service: DataSyncService, user: Any) -> list[str] | None:
    """Load datasets the user has access to via DataSyncService."""
    try:
        with st.spinner("Loading datasets..."):
            statuses = run_async(service.get_sync_status(user), timeout=_FETCH_TIMEOUT_SECONDS)
    except Exception as exc:
        st.error(f"Failed to load dataset options: {exc}")
        return None

    datasets = sorted({status.dataset for status in statuses})
    if not datasets:
        st.info("No datasets available.")
        return []
    return datasets


def load_quality_datasets(service: DataQualityService, user: Any) -> list[str]:
    """Load datasets with validation results via DataQualityService."""
    try:
        with st.spinner("Loading datasets..."):
            results = run_async(
                service.get_validation_results(user=user, dataset=None, limit=50),
                timeout=_FETCH_TIMEOUT_SECONDS,
            )
    except Exception as exc:
        st.error(f"Failed to load dataset options: {exc}")
        return []
    return sorted({result.dataset for result in results})


__all__ = ["load_user_datasets", "load_sync_datasets", "load_quality_datasets"]
