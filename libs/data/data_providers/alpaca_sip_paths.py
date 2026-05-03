"""Shared path resolution helpers for local Alpaca SIP manifests."""

from __future__ import annotations

from pathlib import Path


def resolve_alpaca_sip_manifest_path(
    path: Path,
    *,
    data_root: Path,
    storage_root: Path,
) -> Path:
    """Resolve manifest path forms written by Alpaca SIP sync managers."""
    if path.is_absolute():
        return path.resolve()
    if len(path.parts) == 1:
        return (storage_root / path).resolve()
    if path.parts[0] == data_root.name:
        return (data_root.parent / path).resolve()
    if path.parts[0] == "alpaca":
        return (data_root / path).resolve()
    return (storage_root / path).resolve()
