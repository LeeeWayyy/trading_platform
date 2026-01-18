"""Focused ETL pipeline tests for CA pass-through and persistence hooks."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest

from libs.data.data_pipeline import etl


def _raw_data(now: datetime) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["AAPL", "MSFT"],
            "date": [date(2024, 1, 10), date(2024, 1, 10)],
            "open": [150.0, 300.0],
            "high": [151.0, 305.0],
            "low": [149.0, 295.0],
            "close": [150.0, 301.0],
            "volume": [1_000_000, 2_000_000],
            "timestamp": [now, now],
        }
    )


def test_run_etl_pipeline_builds_ca_df_for_outlier_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    raw_data = _raw_data(now)

    splits = pl.DataFrame(
        {
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 11)],
            "split_ratio": [4.0],
        }
    )
    dividends = pl.DataFrame(
        {
            "symbol": ["MSFT"],
            "date": [date(2024, 1, 12)],
            "dividend": [2.0],
        }
    )

    captured: dict[str, object] = {}

    def fake_detect_outliers(
        df: pl.DataFrame, ca_df: pl.DataFrame | None, threshold: float
    ) -> tuple[pl.DataFrame, pl.DataFrame]:
        captured["ca_df"] = ca_df
        captured["threshold"] = threshold
        return df, pl.DataFrame()

    def fake_save_results(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(etl, "detect_outliers", fake_detect_outliers)
    monkeypatch.setattr(etl, "_save_results", fake_save_results)

    result = etl.run_etl_pipeline(
        raw_data,
        splits_df=splits,
        dividends_df=dividends,
        outlier_threshold=0.42,
        output_dir="unused",
        run_date=date(2024, 1, 12),
    )

    assert result["stats"]["adjusted_rows"] == len(raw_data)

    ca_df = captured.get("ca_df")
    assert isinstance(ca_df, pl.DataFrame)
    assert set(ca_df.columns) == {"symbol", "date"}
    assert set(tuple(row) for row in ca_df.rows()) == {
        ("AAPL", date(2024, 1, 11)),
        ("MSFT", date(2024, 1, 12)),
    }
    assert captured["threshold"] == pytest.approx(0.42)


def test_run_etl_pipeline_calls_save_results_with_run_date(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    raw_data = _raw_data(now)

    captured: dict[str, object] = {}

    def fake_save_results(
        good_data: pl.DataFrame, quarantine_data: pl.DataFrame, output_dir: object, run_date: date
    ) -> None:
        captured["output_dir"] = output_dir
        captured["run_date"] = run_date
        captured["good_rows"] = len(good_data)
        captured["quarantine_rows"] = len(quarantine_data)

    monkeypatch.setattr(etl, "_save_results", fake_save_results)

    etl.run_etl_pipeline(
        raw_data,
        output_dir="/tmp/etl-output",
        run_date=date(2024, 1, 10),
    )

    assert captured["output_dir"] == etl.Path("/tmp/etl-output")
    assert captured["run_date"] == date(2024, 1, 10)
    assert captured["good_rows"] == len(raw_data)
    assert captured["quarantine_rows"] == 0


def test_load_adjusted_data_symbol_filter_returns_empty(tmp_path: Path) -> None:
    adjusted_dir = tmp_path / "adjusted" / "2024-01-10"
    adjusted_dir.mkdir(parents=True)

    data = pl.DataFrame(
        {
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 10)],
            "close": [150.0],
        }
    )
    data.write_parquet(adjusted_dir / "AAPL.parquet")

    df = etl.load_adjusted_data(symbols=["MSFT"], data_dir=tmp_path / "adjusted")

    assert df.is_empty()
