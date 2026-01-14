"""Shared session/client ID utilities for NiceGUI components.

This module provides a single source of truth for retrieving or generating
client/session IDs across the application, ensuring consistent behavior.
"""

from __future__ import annotations

import logging

from nicegui import ui

from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager

logger = logging.getLogger(__name__)


def get_or_create_client_id() -> str:
    """Return a stable client ID, generating and storing if missing.

    This function provides consistent client ID retrieval across the application.
    It attempts to:
    1. Return existing client_id from storage if present
    2. Generate and store a new client_id if not present
    3. Fall back to ui.context.client.id if storage is unavailable

    Returns:
        Client ID string, or empty string if unavailable.
    """
    try:
        client = ui.context.client
    except Exception:
        return ""

    if client is None:
        return ""

    storage = getattr(client, "storage", None)
    client_id: str | None = None

    if storage is not None:
        storage_get = getattr(storage, "get", None)
        if callable(storage_get):
            existing = storage_get("client_id")
            if isinstance(existing, str) and existing:
                return existing

            # Generate and store new client_id
            lifecycle = ClientLifecycleManager.get()
            client_id = lifecycle.generate_client_id()
            try:
                storage["client_id"] = client_id
            except (TypeError, AttributeError):
                client_id = None
            else:
                logger.debug(
                    "session_generated_client_id",
                    extra={"client_id": client_id},
                )

    # Fallback to ui.context.client.id if storage-based ID unavailable
    if not client_id:
        fallback_id = getattr(client, "id", None)
        client_id = str(fallback_id) if fallback_id else None

    return client_id or ""


__all__ = ["get_or_create_client_id"]
