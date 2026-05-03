"""Shared file-write and checksum utilities for local data-provider syncs."""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Sequence
from pathlib import Path

import polars as pl

from libs.data.data_quality.exceptions import DiskSpaceError

logger = logging.getLogger(__name__)


def atomic_write_parquet(df: pl.DataFrame, target_path: Path) -> str:
    """Write a Parquet file atomically and return the fsynced file checksum."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_suffix(".parquet.tmp")
    try:
        df.write_parquet(temp_path)
        checksum = compute_checksum_and_fsync(temp_path)
        temp_path.replace(target_path)
        fsync_directory(target_path.parent)
        return checksum
    except OSError as exc:
        if exc.errno == 28:
            raise DiskSpaceError(f"Disk full writing {target_path}") from exc
        raise
    finally:
        temp_path.unlink(missing_ok=True)


def compute_checksum(path: Path) -> str:
    """Return a SHA-256 checksum for a file."""
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_checksum_and_fsync(path: Path) -> str:
    """Return a SHA-256 checksum and fsync the same file handle."""
    hasher = hashlib.sha256()
    with open(path, "r+b") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            hasher.update(chunk)
        os.fsync(handle.fileno())
    return hasher.hexdigest()


def compute_combined_checksum_for_paths(paths: Sequence[Path]) -> str:
    """Return a stable checksum over the checksums of existing paths."""
    hasher = hashlib.sha256()
    for path in sorted(paths, key=str):
        if path.exists():
            hasher.update(compute_checksum(path).encode())
    return hasher.hexdigest()


def fsync_directory(path: Path) -> None:
    """Best-effort fsync for a directory after atomic file replacement."""
    fd: int | None = None
    try:
        fd = os.open(path, os.O_RDONLY)
        os.fsync(fd)
    except OSError as exc:
        logger.debug(
            "sync_directory_fsync_skipped",
            extra={"path": str(path), "error": str(exc)},
        )
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError as exc:
                logger.debug(
                    "sync_directory_fsync_close_skipped",
                    extra={"path": str(path), "error": str(exc)},
                )
