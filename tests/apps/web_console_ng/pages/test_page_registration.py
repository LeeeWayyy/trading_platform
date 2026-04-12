"""Tests for page module registration and optional-dependency skip behavior."""

from __future__ import annotations

import importlib
from types import ModuleType
from unittest.mock import patch

import pytest


class TestPageRegistrationSkipBehavior:
    """Verify that page __init__ correctly skips optional deps and fails on real errors."""

    def _reload_pages_init(self) -> ModuleType:
        """Reload pages.__init__ to re-execute the module-level registration loop."""
        import apps.web_console_ng.pages as pages_mod

        return importlib.reload(pages_mod)

    def test_optional_package_missing_is_skipped(self) -> None:
        """When an optional package (e.g. 'rq') is missing, the page is skipped."""
        original_import = importlib.import_module

        def _mock_import(name: str) -> ModuleType:
            if name == "apps.web_console_ng.pages.backtest":
                raise ModuleNotFoundError(name="rq")
            return original_import(name)

        with patch("importlib.import_module", side_effect=_mock_import):
            mod = self._reload_pages_init()

        skipped = mod.get_skipped_page_modules()
        assert any(
            mod_name == "apps.web_console_ng.pages.backtest" and pkg == "rq"
            for mod_name, pkg in skipped
        ), f"Expected backtest to be skipped due to 'rq', got: {skipped}"

    def test_non_optional_package_missing_raises(self) -> None:
        """When a non-optional internal module is missing, the error is re-raised."""
        original_import = importlib.import_module

        def _mock_import(name: str) -> ModuleType:
            if name == "apps.web_console_ng.pages.dashboard":
                raise ModuleNotFoundError(name="apps.web_console_ng.core.something")
            return original_import(name)

        with (
            patch("importlib.import_module", side_effect=_mock_import),
            pytest.raises(ModuleNotFoundError, match="apps.web_console_ng.core.something"),
        ):
            self._reload_pages_init()

    def test_page_module_itself_missing_raises(self) -> None:
        """If a page module file itself is missing, the error is re-raised."""
        original_import = importlib.import_module

        def _mock_import(name: str) -> ModuleType:
            if name == "apps.web_console_ng.pages.admin":
                raise ModuleNotFoundError(name="apps.web_console_ng.pages.admin")
            return original_import(name)

        with (
            patch("importlib.import_module", side_effect=_mock_import),
            pytest.raises(ModuleNotFoundError),
        ):
            self._reload_pages_init()

    def test_get_skipped_page_modules_returns_list(self) -> None:
        """get_skipped_page_modules returns a list (possibly empty) of tuples."""
        from apps.web_console_ng.pages import get_skipped_page_modules

        result = get_skipped_page_modules()
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, tuple)
            assert len(item) == 2
