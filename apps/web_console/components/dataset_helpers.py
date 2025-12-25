"""Shared dataset loading helpers."""

from __future__ import annotations

from typing import Any

import streamlit as st

from apps.web_console.services.data_explorer_service import DataExplorerService
from libs.common.async_utils import run_async

_FETCH_TIMEOUT_SECONDS = 10.0


def load_user_datasets(service: DataExplorerService, user: Any) -> list[str]:
    """Load datasets the user has access to."""

    try:
        with st.spinner("Loading datasets..."):
            datasets = run_async(service.list_datasets(user), timeout=_FETCH_TIMEOUT_SECONDS)
    except Exception as exc:
        st.error(f"Failed to load datasets: {exc}")
        return []
    return [ds.name for ds in datasets]


__all__ = ["load_user_datasets"]
