from __future__ import annotations

import pytest

from apps.web_console_ng.core.workspace_tabs import BACKTEST_TAB_RESULTS, normalize_backtest_tab


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("new", "new"),
        ("running", "running"),
        ("results", "results"),
        (" compare ", BACKTEST_TAB_RESULTS),
        ("COMPARE", BACKTEST_TAB_RESULTS),
    ],
)
def test_normalize_backtest_tab_accepts_valid_and_alias_values(
    value: str,
    expected: str,
) -> None:
    assert normalize_backtest_tab(value) == expected


@pytest.mark.parametrize("value", ["", "unknown", "foo", "comparex"])
def test_normalize_backtest_tab_returns_default_for_invalid_values(value: str) -> None:
    assert normalize_backtest_tab(value, default="new") == "new"


def test_normalize_backtest_tab_returns_none_for_none_without_default() -> None:
    assert normalize_backtest_tab(None) is None

