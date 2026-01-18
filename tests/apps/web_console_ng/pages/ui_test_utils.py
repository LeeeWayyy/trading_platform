"""Test helpers for NiceGUI page unit tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any


class DummyElement:
    """Minimal UI element stub supporting chaining and context manager use."""

    def __init__(self, value: Any = None, text: str | None = None) -> None:
        self.value = value
        self.text = text or ""
        self.visible = True
        self.enabled = True
        self.deleted = False
        self.cleared = False
        self.on_click_cb: Callable[..., Any] | None = None
        self.on_value_change_cb: Callable[..., Any] | None = None
        self.classes_args: list[str] = []
        self.props_args: list[str] = []

    def __enter__(self) -> DummyElement:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def classes(self, classes: str) -> DummyElement:
        self.classes_args.append(classes)
        return self

    def props(self, props: str) -> DummyElement:
        self.props_args.append(props)
        return self

    def on_click(self, cb: Callable[..., Any]) -> DummyElement:
        self.on_click_cb = cb
        return self

    def on_value_change(self, cb: Callable[..., Any]) -> DummyElement:
        self.on_value_change_cb = cb
        return self

    def set_text(self, text: str) -> None:
        self.text = text

    def delete(self) -> None:
        self.deleted = True

    def clear(self) -> None:
        self.cleared = True


class DummyTimer:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class DummyUI:
    """Minimal NiceGUI stub to capture UI construction and callbacks."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.elements: list[DummyElement] = []
        self.buttons: list[DummyElement] = []
        self.selects: list[DummyElement] = []
        self.labels: list[tuple[str, DummyElement]] = []
        self.tables: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []
        self.plotlies: list[Any] = []
        self.notifications: list[tuple[str, str | None]] = []
        self.downloads: list[tuple[bytes, str]] = []
        self.timers: list[tuple[float, Callable[..., Any], DummyTimer]] = []
        self.context = SimpleNamespace(client=SimpleNamespace(storage={}))

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def label(self, text: str = "") -> DummyElement:
        self._record("label", text)
        element = DummyElement(text=text)
        self.labels.append((text, element))
        self.elements.append(element)
        return element

    def card(self) -> DummyElement:
        self._record("card")
        element = DummyElement()
        self.elements.append(element)
        return element

    def row(self) -> DummyElement:
        self._record("row")
        element = DummyElement()
        self.elements.append(element)
        return element

    def column(self) -> DummyElement:
        self._record("column")
        element = DummyElement()
        self.elements.append(element)
        return element

    def select(
        self, *, label: str, options: Any, value: Any = None, multiple: bool = False
    ) -> DummyElement:
        self._record("select", label, options, value, multiple)
        element = DummyElement(value=value)
        element.label = label
        element.multiple = multiple
        element.options = options
        self.selects.append(element)
        self.elements.append(element)
        return element

    def number(
        self, *, label: str, value: Any = None, step: Any = None, min: Any = None, max: Any = None
    ) -> DummyElement:
        self._record("number", label, value, step, min, max)
        element = DummyElement(value=value)
        element.label = label
        self.elements.append(element)
        return element

    def input(self, *, label: str) -> DummyElement:
        self._record("input", label)
        element = DummyElement(value="")
        element.label = label
        self.elements.append(element)
        return element

    def textarea(self, *, label: str, placeholder: str = "") -> DummyElement:
        self._record("textarea", label, placeholder)
        element = DummyElement(value="")
        element.label = label
        self.elements.append(element)
        return element

    def checkbox(self, label: str) -> DummyElement:
        self._record("checkbox", label)
        element = DummyElement(value=False)
        element.label = label
        self.elements.append(element)
        return element

    def date(self, *, value: str = "") -> DummyElement:
        self._record("date", value)
        element = DummyElement(value=value)
        element.label = "date"
        self.elements.append(element)
        return element

    def button(self, text: str, **kwargs: Any) -> DummyElement:
        self._record("button", text, kwargs)
        element = DummyElement()
        element.text = text
        self.buttons.append(element)
        self.elements.append(element)
        on_click = kwargs.get("on_click")
        if on_click:
            element.on_click_cb = on_click
        return element

    def table(self, *, columns: list[dict[str, Any]], rows: list[dict[str, Any]]) -> DummyElement:
        self._record("table", columns, rows)
        self.tables.append((columns, rows))
        element = DummyElement()
        self.elements.append(element)
        return element

    def tabs(self) -> DummyElement:
        self._record("tabs")
        element = DummyElement()
        self.elements.append(element)
        return element

    def tab(self, label: str) -> DummyElement:
        self._record("tab", label)
        element = DummyElement(text=label)
        self.elements.append(element)
        return element

    def tab_panels(self, *_args: Any, **_kwargs: Any) -> DummyElement:
        self._record("tab_panels")
        element = DummyElement()
        self.elements.append(element)
        return element

    def tab_panel(self, *_args: Any, **_kwargs: Any) -> DummyElement:
        self._record("tab_panel")
        element = DummyElement()
        self.elements.append(element)
        return element

    def separator(self) -> DummyElement:
        self._record("separator")
        element = DummyElement()
        self.elements.append(element)
        return element

    def icon(self, name: str, **kwargs: Any) -> DummyElement:
        self._record("icon", name, kwargs)
        element = DummyElement()
        self.elements.append(element)
        return element

    def spinner(self, *_args: Any, **_kwargs: Any) -> DummyElement:
        self._record("spinner")
        element = DummyElement()
        self.elements.append(element)
        return element

    def slider(self, *, min: float, max: float, step: float, value: float) -> DummyElement:
        self._record("slider", min, max, step, value)
        element = DummyElement(value=value)
        self.elements.append(element)
        return element

    def plotly(self, fig: Any) -> DummyElement:
        self._record("plotly", fig)
        self.plotlies.append(fig)
        element = DummyElement()
        self.elements.append(element)
        return element

    def json_editor(self, *_args: Any, **_kwargs: Any) -> DummyElement:
        self._record("json_editor")
        element = DummyElement()
        self.elements.append(element)
        return element

    def expansion(self, *_args: Any, **_kwargs: Any) -> DummyElement:
        self._record("expansion")
        element = DummyElement()
        self.elements.append(element)
        return element

    def notify(self, message: str, *, type: str | None = None) -> None:
        self._record("notify", message, type)
        self.notifications.append((message, type))

    def download(self, data: bytes, filename: str) -> None:
        self._record("download", filename)
        self.downloads.append((data, filename))

    def timer(self, interval: float, callback: Callable[..., Any]) -> DummyTimer:
        self._record("timer", interval)
        timer = DummyTimer()
        self.timers.append((interval, callback, timer))
        return timer

    def refreshable(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        if asyncio.iscoroutinefunction(fn):

            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                return await fn(*args, **kwargs)

            async def refresh() -> Any:
                return await fn()

            wrapper.refresh = refresh  # type: ignore[attr-defined]
            return wrapper

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        def refresh() -> Any:
            return fn()

        wrapper.refresh = refresh  # type: ignore[attr-defined]
        return wrapper
