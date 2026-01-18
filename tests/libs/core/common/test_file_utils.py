from __future__ import annotations

import hashlib
from pathlib import Path

from libs.core.common.file_utils import hash_file_sha256


def _write_bytes(path: Path, data: bytes) -> None:
    path.write_bytes(data)


def test_hash_file_sha256_matches_expected(tmp_path: Path) -> None:
    payload = b"trading-platform-hash-test"
    file_path = tmp_path / "sample.bin"
    _write_bytes(file_path, payload)

    expected = hashlib.sha256(payload).hexdigest()

    assert hash_file_sha256(file_path) == expected


def test_hash_file_sha256_respects_chunk_size(tmp_path: Path) -> None:
    payload = b"0123456789abcdef" * 8  # 128 bytes to ensure multiple chunks
    file_path = tmp_path / "chunked.bin"
    _write_bytes(file_path, payload)

    expected = hashlib.sha256(payload).hexdigest()

    assert hash_file_sha256(file_path, chunk_size=7) == expected
