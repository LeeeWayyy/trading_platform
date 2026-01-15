#!/usr/bin/env python3
"""File utility helpers shared across tooling and tests."""

from __future__ import annotations

import hashlib
from pathlib import Path


def hash_file_sha256(path: Path, chunk_size: int = 8192) -> str:
    """Compute SHA256 hash of a file."""
    hasher = hashlib.sha256()
    with open(path, "rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(chunk_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
