"""Unit tests for libs.web_console_services.alpha_explorer_service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from unittest.mock import Mock, patch

import polars as pl
import pytest

from libs.models.models.types import EnvironmentMetadata, ModelMetadata, ModelStatus, ModelType
from libs.web_console_services.alpha_explorer_service import AlphaExplorerService


@dataclass
class DummyBacktestResult:
    mean_ic: float
    icir: float
    hit_rate: float
    coverage: float
    average_turnover: float
    decay_half_life: float | None
    n_days: int
    start_date: date
    end_date: date
    daily_ic: pl.DataFrame
    decay_curve: pl.DataFrame
    daily_signals: pl.DataFrame


def _build_env() -> EnvironmentMetadata:
    return EnvironmentMetadata(
        python_version="3.11.6",
        dependencies_hash="deadbeef",
        platform="darwin",
        created_by="tester",
        numpy_version="1.26.4",
        polars_version="0.20.31",
        sklearn_version=None,
        cvxpy_version=None,
    )


def _build_metadata(
    model_id: str,
    *,
    metrics: dict[str, float] | None = None,
    parameters: dict[str, object] | None = None,
    created_at: datetime | None = None,
) -> ModelMetadata:
    return ModelMetadata(
        model_id=model_id,
        model_type=ModelType.alpha_weights,
        version="v1.0.0",
        created_at=created_at or datetime(2025, 1, 1, tzinfo=UTC),
        dataset_version_ids={"crsp": "v1.0.0"},
        snapshot_id="snapshot-1",
        factor_list=[],
        parameters=parameters or {},
        checksum_sha256="abc123",
        metrics=metrics or {},
        env=_build_env(),
        config={},
        config_hash="hash",
        feature_formulas=None,
    )


@pytest.fixture()
def registry() -> Mock:
    return Mock()


def test_list_signals_filters_and_paginates(registry: Mock) -> None:
    models = [
        _build_metadata("alpha-1", metrics={"mean_ic": 0.12}),
        _build_metadata("alpha-2", metrics={"mean_ic": 0.35}),
        _build_metadata("alpha-3", metrics={"mean_ic": 0.62}),
    ]
    registry.list_models.return_value = models

    service = AlphaExplorerService(registry)

    summaries, total = service.list_signals(
        status=ModelStatus.staged,
        min_ic=0.2,
        max_ic=0.6,
        limit=1,
        offset=0,
    )

    registry.list_models.assert_called_once_with(
        model_type=ModelType.alpha_weights, status=ModelStatus.staged
    )
    assert total == 1
    assert len(summaries) == 1
    assert summaries[0].signal_id == "alpha-2"


def test_list_signals_uses_display_name(registry: Mock) -> None:
    model = _build_metadata(
        "alpha-1",
        metrics={"mean_ic": 0.12},
        parameters={"name": "Momentum 12-1", "backtest_job_id": "job-1"},
    )
    registry.list_models.return_value = [model]

    service = AlphaExplorerService(registry)
    summaries, total = service.list_signals()

    assert total == 1
    assert summaries[0].display_name == "Momentum 12-1"
    assert summaries[0].backtest_job_id == "job-1"


def test_get_signal_metrics_missing_metadata_raises(registry: Mock) -> None:
    registry.get_model_by_id.return_value = None
    service = AlphaExplorerService(registry)

    with pytest.raises(ValueError, match="Signal not found"):
        service.get_signal_metrics("missing")


def test_get_signal_metrics_with_backtest_result(registry: Mock) -> None:
    metadata = _build_metadata("alpha-1", parameters={"name": "Alpha One"})
    registry.get_model_by_id.return_value = metadata

    backtest = DummyBacktestResult(
        mean_ic=0.12,
        icir=1.5,
        hit_rate=0.58,
        coverage=0.9,
        average_turnover=0.2,
        decay_half_life=5.0,
        n_days=250,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        daily_ic=pl.DataFrame({"date": [date(2024, 1, 1)], "ic": [0.1], "rank_ic": [0.2]}),
        decay_curve=pl.DataFrame({"horizon": [1], "ic": [0.1], "rank_ic": [0.2]}),
        daily_signals=pl.DataFrame({"date": [date(2024, 1, 1)], "signal": [0.01]}),
    )

    service = AlphaExplorerService(registry)
    with patch.object(service, "_load_backtest_result", return_value=backtest):
        metrics = service.get_signal_metrics("alpha-1")

    assert metrics.name == "Alpha One"
    assert metrics.mean_ic == 0.12
    assert metrics.decay_half_life == 5.0
    assert metrics.start_date == date(2024, 1, 1)
    assert metrics.end_date == date(2024, 12, 31)


def test_get_signal_metrics_without_backtest_defaults(registry: Mock) -> None:
    metadata = _build_metadata("alpha-1", parameters={"name": "Alpha One"})
    registry.get_model_by_id.return_value = metadata

    service = AlphaExplorerService(registry)
    with (
        patch.object(service, "_load_backtest_result", return_value=None),
        patch("libs.web_console_services.alpha_explorer_service.date") as date_mock,
    ):
        date_mock.today.return_value = date(2025, 1, 15)
        metrics = service.get_signal_metrics("alpha-1")

    assert metrics.mean_ic == 0.0
    assert metrics.icir == 0.0
    assert metrics.start_date == date(2025, 1, 15)
    assert metrics.end_date == date(2025, 1, 15)


def test_get_ic_timeseries_returns_empty_schema(registry: Mock) -> None:
    registry.get_model_by_id.return_value = None
    service = AlphaExplorerService(registry)

    df = service.get_ic_timeseries("missing")

    assert df.columns == ["date", "ic", "rank_ic", "rolling_ic_20d"]
    assert df.is_empty()


def test_get_ic_timeseries_adds_rolling_column(registry: Mock) -> None:
    metadata = _build_metadata("alpha-1")
    registry.get_model_by_id.return_value = metadata

    daily_ic = pl.DataFrame(
        {
            "date": [date(2024, 1, 1), date(2024, 1, 2)],
            "ic": [0.1, 0.2],
            "rank_ic": [0.05, 0.06],
        }
    )
    backtest = DummyBacktestResult(
        mean_ic=0.1,
        icir=1.0,
        hit_rate=0.5,
        coverage=1.0,
        average_turnover=0.1,
        decay_half_life=None,
        n_days=2,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 2),
        daily_ic=daily_ic,
        decay_curve=pl.DataFrame({"horizon": [1], "ic": [0.1], "rank_ic": [0.05]}),
        daily_signals=pl.DataFrame({"date": [date(2024, 1, 1)], "signal": [0.01]}),
    )

    service = AlphaExplorerService(registry)
    with patch.object(service, "_load_backtest_result", return_value=backtest):
        df = service.get_ic_timeseries("alpha-1")

    assert "rolling_ic_20d" in df.columns
    assert df.height == 2


def test_get_decay_curve_returns_empty_schema(registry: Mock) -> None:
    registry.get_model_by_id.return_value = None
    service = AlphaExplorerService(registry)

    df = service.get_decay_curve("missing")

    assert df.columns == ["horizon", "ic", "rank_ic"]
    assert df.is_empty()


def test_compute_correlation_insufficient_data_returns_empty(registry: Mock) -> None:
    registry.get_model_by_id.return_value = None
    service = AlphaExplorerService(registry)

    df = service.compute_correlation(["alpha-1"])

    assert df.columns == ["signal"]
    assert df.is_empty()


def test_compute_correlation_returns_matrix(registry: Mock) -> None:
    metadata_one = _build_metadata("alpha-1", parameters={"name": "Signal A"})
    metadata_two = _build_metadata("alpha-2", parameters={"name": "Signal B"})

    def get_model_by_id(model_id: str) -> ModelMetadata | None:
        return {"alpha-1": metadata_one, "alpha-2": metadata_two}.get(model_id)

    registry.get_model_by_id.side_effect = get_model_by_id

    backtest_one = DummyBacktestResult(
        mean_ic=0.1,
        icir=1.0,
        hit_rate=0.5,
        coverage=1.0,
        average_turnover=0.1,
        decay_half_life=None,
        n_days=2,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 2),
        daily_ic=pl.DataFrame({"date": [date(2024, 1, 1)], "ic": [0.1], "rank_ic": [0.1]}),
        decay_curve=pl.DataFrame({"horizon": [1], "ic": [0.1], "rank_ic": [0.1]}),
        daily_signals=pl.DataFrame(
            {"date": [date(2024, 1, 1), date(2024, 1, 2)], "signal": [0.1, 0.2]}
        ),
    )
    backtest_two = DummyBacktestResult(
        mean_ic=0.2,
        icir=1.2,
        hit_rate=0.6,
        coverage=1.0,
        average_turnover=0.1,
        decay_half_life=None,
        n_days=2,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 2),
        daily_ic=pl.DataFrame({"date": [date(2024, 1, 1)], "ic": [0.2], "rank_ic": [0.2]}),
        decay_curve=pl.DataFrame({"horizon": [1], "ic": [0.2], "rank_ic": [0.2]}),
        daily_signals=pl.DataFrame(
            {"date": [date(2024, 1, 1), date(2024, 1, 2)], "signal": [0.3, 0.4]}
        ),
    )

    service = AlphaExplorerService(registry)
    with patch.object(service, "_load_backtest_result", side_effect=[backtest_one, backtest_two]):
        corr = service.compute_correlation(["alpha-1", "alpha-2"])

    assert corr.columns[0] == "signal"
    assert corr.width == 3
    assert corr.height == 2


def test_load_backtest_result_missing_job_id_returns_none(registry: Mock) -> None:
    metadata = _build_metadata("alpha-1", parameters={})
    service = AlphaExplorerService(registry)

    assert service._load_backtest_result(metadata) is None


def test_load_backtest_result_storage_error_returns_none(registry: Mock) -> None:
    metadata = _build_metadata("alpha-1", parameters={"backtest_job_id": "job-1"})
    service = AlphaExplorerService(registry)

    storage_instance = Mock()
    storage_instance.get_result.side_effect = OSError("db down")

    with (
        patch("libs.core.common.sync_db_pool.get_sync_db_pool", return_value=Mock()),
        patch(
            "libs.trading.backtest.result_storage.BacktestResultStorage",
            return_value=storage_instance,
        ),
    ):
        assert service._load_backtest_result(metadata) is None
