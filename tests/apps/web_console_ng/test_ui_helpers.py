"""Tests for UI helper utilities."""

from __future__ import annotations

import pytest

from apps.web_console_ng.ui import helpers


class _DummyElement:
    def __init__(self) -> None:
        self.calls: list[dict[str, str | None]] = []

    def classes(
        self,
        *,
        add: str | None = None,
        remove: str | None = None,
        toggle: str | None = None,
        replace: str | None = None,
    ) -> None:
        self.calls.append({"add": add, "remove": remove, "toggle": toggle, "replace": replace})


def test_safe_classes_calls_element_classes() -> None:
    element = _DummyElement()

    helpers.safe_classes(element, add="a", remove="b", toggle="c", replace="d")

    assert element.calls == [
        {"add": "a", "remove": "b", "toggle": "c", "replace": "d"}
    ]


@pytest.mark.parametrize("replace", [True, False])
def test_safe_classes_rejects_replace_bool(replace: bool) -> None:
    element = _DummyElement()

    with pytest.raises(TypeError, match="expects a string, not bool"):
        helpers.safe_classes(element, replace=replace)


@pytest.mark.parametrize("arg_name", ["add", "remove", "toggle"])
def test_safe_classes_rejects_other_bool_args(arg_name: str) -> None:
    element = _DummyElement()

    kwargs = {arg_name: True}

    with pytest.raises(TypeError, match="expects strings, not bools"):
        helpers.safe_classes(element, **kwargs)
