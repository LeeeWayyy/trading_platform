"""Tests for pit_lookup NiceGUI component (P6T13)."""

from __future__ import annotations

import datetime
from typing import Any

import pytest

from apps.web_console_ng.components import pit_lookup as pit_module
from libs.data.data_quality.pit_inspector import PITDataPoint, PITLookupResult

# ============================================================================
# Mocks
# ============================================================================


class DummyFigure:
    """Mock plotly Figure to avoid add_vline crash on string x values."""

    def add_trace(self, *a: Any, **kw: Any) -> None:
        pass

    def add_vline(self, **kw: Any) -> None:
        pass

    def update_layout(self, **kw: Any) -> None:
        pass


class DummyGo:
    """Mock plotly.graph_objects module."""

    class Scatter:  # noqa: D106
        def __init__(self, **kw: Any) -> None:
            pass

    @staticmethod
    def Figure() -> DummyFigure:
        return DummyFigure()


class DummyElement:
    def __init__(self, text: str = "", value: Any = None) -> None:
        self.text = text
        self.value = value
        self._classes: list[str] = []
        self.on_click_cb: Any = None
        self.on_value_change_cb: Any = None

    def classes(self, c: str) -> DummyElement:
        self._classes.append(c)
        return self

    def props(self, _p: str) -> DummyElement:
        return self

    def __enter__(self) -> DummyElement:
        return self

    def __exit__(self, *a: Any) -> bool:
        return False

    def on_value_change(self, cb: Any) -> DummyElement:
        self.on_value_change_cb = cb
        return self


class DummyUI:
    def __init__(self) -> None:
        self.labels: list[DummyElement] = []
        self.selects: list[DummyElement] = []
        self.inputs: list[DummyElement] = []
        self.sliders: list[DummyElement] = []
        self.buttons: list[DummyElement] = []
        self.tables: list[Any] = []
        self.cards: list[DummyElement] = []
        self.rows: list[DummyElement] = []
        self.columns: list[DummyElement] = []
        self.plotlies: list[Any] = []

    def label(self, text: str = "") -> DummyElement:
        el = DummyElement(text=text)
        self.labels.append(el)
        return el

    def select(self, *, label: str = "", options: Any = None, value: Any = None) -> DummyElement:
        el = DummyElement(value=value)
        self.selects.append(el)
        return el

    def input(self, *, label: str = "", value: str = "", placeholder: str = "") -> DummyElement:
        el = DummyElement(value=value)
        self.inputs.append(el)
        return el

    def slider(self, *, min: int = 0, max: int = 100, value: int = 50) -> DummyElement:
        el = DummyElement(value=value)
        self.sliders.append(el)
        return el

    def button(self, text: str = "", **kwargs: Any) -> DummyElement:
        el = DummyElement(text=text)
        if "on_click" in kwargs:
            el.on_click_cb = kwargs["on_click"]
        self.buttons.append(el)
        return el

    def table(self, *, columns: Any = None, rows: Any = None) -> DummyElement:
        self.tables.append({"columns": columns, "rows": rows})
        return DummyElement()

    def card(self) -> DummyElement:
        el = DummyElement()
        self.cards.append(el)
        return el

    def row(self) -> DummyElement:
        el = DummyElement()
        self.rows.append(el)
        return el

    def column(self) -> DummyElement:
        el = DummyElement()
        self.columns.append(el)
        return el

    def plotly(self, fig: Any) -> DummyElement:
        self.plotlies.append(fig)
        return DummyElement()

    def add_vline(self, **kwargs: Any) -> None:
        pass


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    ui = DummyUI()
    monkeypatch.setattr(pit_module, "ui", ui)
    monkeypatch.setattr(pit_module, "go", DummyGo)
    return ui


# ============================================================================
# render_pit_lookup_form
# ============================================================================


class TestRenderPitLookupForm:
    def test_no_tickers_shows_message(self, dummy_ui: DummyUI) -> None:
        pit_module.render_pit_lookup_form(
            available_tickers=[],
            min_date=None,
            max_date=None,
            on_submit=lambda *a: None,
        )
        assert any("No adjusted data" in el.text for el in dummy_ui.labels)
        assert dummy_ui.buttons == []

    def test_creates_form_elements(self, dummy_ui: DummyUI) -> None:
        pit_module.render_pit_lookup_form(
            available_tickers=["AAPL", "MSFT"],
            min_date="2024-01-01",
            max_date="2024-12-31",
            on_submit=lambda *a: None,
        )
        assert len(dummy_ui.selects) == 1  # ticker dropdown
        assert len(dummy_ui.inputs) == 1  # date input
        assert len(dummy_ui.sliders) == 1  # lookback slider
        assert len(dummy_ui.buttons) == 1  # submit button

    def test_date_range_label_shown(self, dummy_ui: DummyUI) -> None:
        pit_module.render_pit_lookup_form(
            available_tickers=["AAPL"],
            min_date="2024-01-01",
            max_date="2024-06-30",
            on_submit=lambda *a: None,
        )
        assert any("2024-01-01" in el.text and "2024-06-30" in el.text for el in dummy_ui.labels)

    def test_no_date_range_when_missing(self, dummy_ui: DummyUI) -> None:
        pit_module.render_pit_lookup_form(
            available_tickers=["AAPL"],
            min_date=None,
            max_date=None,
            on_submit=lambda *a: None,
        )
        # No "Data range:" label when dates are None
        assert not any("Data range" in el.text for el in dummy_ui.labels)


# ============================================================================
# render_pit_results
# ============================================================================


def _make_result(
    *,
    has_risk: bool = False,
    has_contaminated: bool = False,
    future_count: int = 0,
    data_points: int = 3,
    future_points: int = 0,
) -> PITLookupResult:
    base_date = datetime.date(2024, 1, 1)
    available = [
        PITDataPoint(
            market_date=base_date + datetime.timedelta(days=i),
            run_date=datetime.date(2024, 1, 15),
            open=150.0 + i,
            high=151.0 + i,
            low=149.0 + i,
            close=150.5 + i,
            volume=1000,
            source="adjusted",
        )
        for i in range(data_points)
    ]
    future = [
        PITDataPoint(
            market_date=datetime.date(2024, 2, 1 + i),
            run_date=datetime.date(2024, 2, 5),
            open=160.0 + i,
            high=161.0 + i,
            low=159.0 + i,
            close=160.5 + i,
            volume=500,
            source="adjusted",
        )
        for i in range(future_points)
    ]
    return PITLookupResult(
        ticker="AAPL",
        knowledge_date=datetime.date(2024, 1, 20),
        data_available=available,
        data_future=future,
        has_look_ahead_risk=has_risk,
        has_contaminated_historical=has_contaminated,
        latest_available_date=available[0].market_date if available else None,
        days_stale=2 if available else None,
        total_rows_available=len(available),
        future_partition_count=future_count,
    )


class TestRenderPitResults:
    def test_no_risk_shows_green_badge(self, dummy_ui: DummyUI) -> None:
        pit_module.render_pit_results(_make_result())
        assert any("No look-ahead bias" in el.text for el in dummy_ui.labels)

    def test_contaminated_risk_shows_contamination_badge(self, dummy_ui: DummyUI) -> None:
        pit_module.render_pit_results(
            _make_result(has_risk=True, has_contaminated=True, future_count=0)
        )
        assert any("Contaminated" in el.text for el in dummy_ui.labels)

    def test_future_partitions_shows_amber_badge(self, dummy_ui: DummyUI) -> None:
        pit_module.render_pit_results(
            _make_result(has_risk=True, future_count=3)
        )
        assert any("3 future partition" in el.text for el in dummy_ui.labels)

    def test_available_data_table_created(self, dummy_ui: DummyUI) -> None:
        pit_module.render_pit_results(_make_result(data_points=5))
        assert len(dummy_ui.tables) >= 1

    def test_no_data_shows_message(self, dummy_ui: DummyUI) -> None:
        pit_module.render_pit_results(_make_result(data_points=0))
        assert any("No data available" in el.text for el in dummy_ui.labels)

    def test_future_data_table_shown_with_risk(self, dummy_ui: DummyUI) -> None:
        pit_module.render_pit_results(
            _make_result(has_risk=True, future_count=1, future_points=3)
        )
        # Should have available table + future table
        assert len(dummy_ui.tables) >= 2

    def test_timeline_chart_created(self, dummy_ui: DummyUI) -> None:
        pit_module.render_pit_results(_make_result(data_points=5))
        assert len(dummy_ui.plotlies) == 1

    def test_staleness_shown(self, dummy_ui: DummyUI) -> None:
        pit_module.render_pit_results(_make_result())
        assert any("trading days stale" in el.text for el in dummy_ui.labels)

    def test_row_truncation_message(self, dummy_ui: DummyUI) -> None:
        pit_module.render_pit_results(_make_result(data_points=60))
        assert any("Showing 50 of 60" in el.text for el in dummy_ui.labels)


__all__: list[str] = []
