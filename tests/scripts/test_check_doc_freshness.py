#!/usr/bin/env python3
"""
Test suite for scripts/check_doc_freshness.py.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import scripts.check_doc_freshness as freshness


@pytest.fixture()
def tmp_repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide a temporary repo root and patch module constants."""
    monkeypatch.setattr(freshness, "PROJECT_ROOT", tmp_path)
    return tmp_path


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_normalize_path_cases() -> None:
    assert freshness.normalize_path("./apps") == "apps/"
    assert freshness.normalize_path("apps/signal_service") == "apps/signal_service/"
    assert freshness.normalize_path("/libs/redis_client/") == "libs/redis_client/"


def test_parse_documented_entries_active_and_deprecated(
    tmp_repo_root: Path,
) -> None:
    doc_path = tmp_repo_root / "docs/GETTING_STARTED/REPO_MAP.md"
    _write_file(
        doc_path,
        "- `apps/signal_service/` - Service\n"
        "- `apps/legacy_service/` [DEPRECATED] - Old service\n"
        "- `libs/redis_client/` - Redis helper\n",
    )

    active, deprecated = freshness.parse_documented_entries("docs/GETTING_STARTED/REPO_MAP.md")

    assert active == {"apps/signal_service/", "libs/redis_client/"}
    assert deprecated == {"apps/legacy_service/"}


def test_check_freshness_missing_orphaned_deprecated(
    tmp_repo_root: Path,
) -> None:
    (tmp_repo_root / "apps/foo").mkdir(parents=True)
    (tmp_repo_root / "apps/bar").mkdir(parents=True)

    doc_path = tmp_repo_root / "docs/GETTING_STARTED/REPO_MAP.md"
    _write_file(
        doc_path,
        "- `apps/foo/`\n"
        "- `apps/qux/`\n"
        "- `apps/baz/`\n"
        "- `apps/baz/` [DEPRECATED]\n",
    )

    def fake_run(args: list[str], capture_output: bool, text: bool, cwd: Path) -> MagicMock:
        return MagicMock(stdout="2026-01-02T00:00:00Z")

    with patch("scripts.check_doc_freshness.subprocess.run", side_effect=fake_run):
        report = freshness.check_freshness("docs/GETTING_STARTED/REPO_MAP.md", ["apps/*/"])

    assert report["missing"] == ["apps/bar/"]
    assert report["orphaned"] == ["apps/qux/"]
    assert report["deprecated"] == ["apps/baz/"]


def test_main_exit_code_bitmask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        freshness,
        "get_source_directories",
        lambda: {str(freshness.REPO_MAP_PATH): ["apps/"]},
    )
    monkeypatch.setattr(freshness, "_is_path_dirty", lambda _: False)

    def fake_check(doc_path: str, source_dirs: list[str]) -> freshness.FreshnessReport:
        return {
            "doc_path": doc_path,
            "missing": ["apps/missing/"],
            "orphaned": ["apps/orphaned/"],
            "deprecated": [],
            "stale": False,
            "last_doc_update": "2026-01-01T00:00:00+00:00",
            "last_source_change": "2026-01-15T00:00:00+00:00",
            "missing_specs": [],
        }

    monkeypatch.setattr(freshness, "check_freshness", fake_check)

    specs_dir = tmp_path / "docs/SPECS"
    specs_dir.mkdir(parents=True)
    monkeypatch.setattr(freshness, "SPECS_DIR", specs_dir)
    monkeypatch.setattr(
        freshness,
        "_expected_spec_files",
        lambda: (["docs/SPECS/services/foo.md"], ["docs/SPECS/services/foo.md"]),
    )

    monkeypatch.setattr(sys, "argv", ["check_doc_freshness.py"])

    exit_code = freshness.main()

    assert exit_code == 15


def test_main_exit_code_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        freshness,
        "get_source_directories",
        lambda: {str(freshness.REPO_MAP_PATH): ["apps/"]},
    )
    monkeypatch.setattr(freshness, "_is_path_dirty", lambda _: False)

    def fake_check(doc_path: str, source_dirs: list[str]) -> freshness.FreshnessReport:
        return {
            "doc_path": doc_path,
            "missing": [],
            "orphaned": [],
            "deprecated": [],
            "stale": False,
            "last_doc_update": "2026-01-10T00:00:00+00:00",
            "last_source_change": "2026-01-10T00:00:00+00:00",
            "missing_specs": [],
        }

    monkeypatch.setattr(freshness, "check_freshness", fake_check)

    specs_dir = tmp_path / "docs/SPECS"
    monkeypatch.setattr(freshness, "SPECS_DIR", specs_dir)

    monkeypatch.setattr(sys, "argv", ["check_doc_freshness.py"])

    exit_code = freshness.main()

    assert exit_code == 0
