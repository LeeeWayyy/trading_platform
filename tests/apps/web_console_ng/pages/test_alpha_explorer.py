"""Unit tests for alpha_explorer page."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

os.environ["WEB_CONSOLE_NG_DEBUG"] = "true"
os.environ.setdefault("NICEGUI_STORAGE_SECRET", "test-secret")

from apps.web_console_ng.pages import alpha_explorer as alpha_module
from tests.apps.web_console_ng.pages.ui_test_utils import DummyUI


class DummyService:
    def __init__(self) -> None:
        self.compute_calls: list[list[str]] = []

    def get_signal_metrics(self, signal_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            signal_id=signal_id,
            name="signal-name",
            version="1",
            mean_ic=0.12,
            icir=0.9,
            hit_rate=0.52,
            coverage=0.98,
            average_turnover=0.12,
            decay_half_life=3.0,
            n_days=30,
            start_date=pd.Timestamp("2024-01-01").date(),
            end_date=pd.Timestamp("2024-01-31").date(),
        )

    def get_ic_timeseries(self, _signal_id: str):
        return [{"date": "2024-01-01", "ic": 0.1}]

    def get_decay_curve(self, _signal_id: str):
        return [{"lag": 1, "ic": 0.05}]

    def compute_correlation(self, signal_ids: list[str]):
        self.compute_calls.append(signal_ids)
        return pd.DataFrame([[1.0, 0.2], [0.2, 1.0]], columns=signal_ids, index=signal_ids)


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    ui = DummyUI()
    monkeypatch.setattr(alpha_module, "ui", ui)
    return ui


@pytest.fixture()
def io_bound_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _io_bound(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(alpha_module.run, "io_bound", _io_bound)


def test_get_alpha_service_returns_none_on_registry_error(monkeypatch: pytest.MonkeyPatch) -> None:
    alpha_module._get_alpha_service.cache_clear()

    class DummyRegistry:
        def __init__(self, *args, **kwargs):
            raise FileNotFoundError("missing registry")

    dummy_registry_module = SimpleNamespace(ModelRegistry=DummyRegistry)
    dummy_metrics_module = SimpleNamespace(AlphaMetricsAdapter=lambda: object())
    dummy_service_module = SimpleNamespace(AlphaExplorerService=lambda *_args, **_kwargs: object())

    monkeypatch.setitem(sys.modules, "libs.models.models.registry", dummy_registry_module)
    monkeypatch.setitem(sys.modules, "libs.trading.alpha.metrics", dummy_metrics_module)
    monkeypatch.setitem(
        sys.modules, "libs.web_console_services.alpha_explorer_service", dummy_service_module
    )

    assert alpha_module._get_alpha_service() is None


@pytest.mark.asyncio()
async def test_render_signal_details_handles_missing_metrics(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ErrorService:
        def get_signal_metrics(self, _signal_id: str):
            raise FileNotFoundError("missing metrics")

    monkeypatch.setattr(alpha_module, "render_ic_chart", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(alpha_module, "render_decay_curve", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(alpha_module, "_render_correlation_section", lambda *_args, **_kwargs: None)

    signal = SimpleNamespace(signal_id="sig-1", display_name="Signal 1")
    await alpha_module._render_signal_details(ErrorService(), signal, [signal])

    assert any("Metrics not found" in msg for msg, _type in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_export_metrics_button_triggers_download(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = DummyService()
    signal = SimpleNamespace(signal_id="sig-1", display_name="Signal 1")

    monkeypatch.setattr(alpha_module, "render_ic_chart", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(alpha_module, "render_decay_curve", lambda *_args, **_kwargs: None)

    async def fake_corr_section(*_args, **_kwargs):
        return None

    monkeypatch.setattr(alpha_module, "_render_correlation_section", fake_corr_section)

    await alpha_module._render_signal_details(service, signal, [signal])

    export_buttons = [b for b in dummy_ui.buttons if b.text == "Export Metrics"]
    assert export_buttons

    export_btn = export_buttons[0]
    assert export_btn.on_click_cb is not None
    export_btn.on_click_cb()

    assert dummy_ui.downloads
    _, filename = dummy_ui.downloads[-1]
    assert filename.startswith("alpha_signal_sig-1_metrics")


@pytest.mark.asyncio()
async def test_correlation_section_requires_two_signals(
    dummy_ui: DummyUI, io_bound_passthrough: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = DummyService()
    calls: list[pd.DataFrame] = []

    def fake_render_correlation(matrix: pd.DataFrame) -> None:
        calls.append(matrix)

    monkeypatch.setattr(alpha_module, "render_correlation_matrix", fake_render_correlation)

    signals = [
        SimpleNamespace(signal_id="s1", display_name="Signal 1"),
        SimpleNamespace(signal_id="s2", display_name="Signal 2"),
    ]

    await alpha_module._render_correlation_section(service, signals[0], signals)

    assert any("Select at least two" in text for text, _ in dummy_ui.labels)

    select = dummy_ui.selects[0]
    select.value = [0, 1]
    assert select.on_value_change_cb is not None
    await select.on_value_change_cb(None)

    assert calls
    assert service.compute_calls
