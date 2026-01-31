"""Tests for TCA chart in apps/web_console_ng/components/tca_chart.py.

Tests the TCA visualization components including summary cards,
decomposition charts, and benchmark comparison charts.
"""

from __future__ import annotations

import os

os.environ["WEB_CONSOLE_NG_DEBUG"] = "true"
os.environ.setdefault("NICEGUI_STORAGE_SECRET", "test-secret")

from unittest.mock import MagicMock, patch

import pytest

from apps.web_console_ng.components.tca_chart import (
    TCA_COLORS,
    create_benchmark_comparison_chart,
    create_metric_card,
    create_shortfall_decomposition_chart,
    create_summary_cards,
)


class TestTCAColors:
    """Tests for TCA color palette."""

    def test_colors_defined(self) -> None:
        """Color palette has expected keys."""
        expected_keys = [
            "price_shortfall",
            "fee_cost",
            "opportunity_cost",
            "market_impact",
            "timing_cost",
            "vwap_slippage",
            "positive",
            "negative",
            "neutral",
        ]
        for key in expected_keys:
            assert key in TCA_COLORS
            assert TCA_COLORS[key].startswith("#")


class TestCreateMetricCard:
    """Tests for create_metric_card function."""

    def test_create_card_basic(self) -> None:
        """Create metric card with valid data."""
        with patch("apps.web_console_ng.components.tca_chart.ui") as mock_ui:
            mock_card = MagicMock()
            mock_card.__enter__ = MagicMock(return_value=mock_card)
            mock_card.__exit__ = MagicMock(return_value=None)
            mock_card.classes.return_value = mock_card
            mock_ui.card.return_value = mock_card
            mock_ui.label.return_value = MagicMock()
            mock_ui.label.return_value.classes.return_value = MagicMock()

            result = create_metric_card("Test", 2.5, "bps")

            mock_ui.card.assert_called_once()
            assert result is not None

    def test_create_card_negative_cost(self) -> None:
        """Negative cost shows green (favorable)."""
        with patch("apps.web_console_ng.components.tca_chart.ui") as mock_ui:
            mock_card = MagicMock()
            mock_card.__enter__ = MagicMock(return_value=mock_card)
            mock_card.__exit__ = MagicMock(return_value=None)
            mock_card.classes.return_value = mock_card
            mock_ui.card.return_value = mock_card
            label_mock = MagicMock()
            label_mock.classes.return_value = label_mock
            mock_ui.label.return_value = label_mock

            create_metric_card("Test", -1.5, "bps", is_cost=True)

            # Check that green color was used
            calls = mock_ui.label.return_value.classes.call_args_list
            assert any("green" in str(call) for call in calls)

    def test_create_card_with_description(self) -> None:
        """Card with description has tooltip."""
        with patch("apps.web_console_ng.components.tca_chart.ui") as mock_ui:
            mock_card = MagicMock()
            mock_card.__enter__ = MagicMock(return_value=mock_card)
            mock_card.__exit__ = MagicMock(return_value=None)
            mock_card.classes.return_value = mock_card
            mock_ui.card.return_value = mock_card
            mock_ui.label.return_value = MagicMock()
            mock_ui.label.return_value.classes.return_value = MagicMock()

            create_metric_card("Test", 1.0, "bps", description="Test description")

            mock_card.tooltip.assert_called_once_with("Test description")


class TestCreateSummaryCards:
    """Tests for create_summary_cards function."""

    def test_create_summary_cards_basic(self) -> None:
        """Create summary cards with valid data."""
        with patch("apps.web_console_ng.components.tca_chart.ui") as mock_ui:
            mock_row = MagicMock()
            mock_row.__enter__ = MagicMock(return_value=mock_row)
            mock_row.__exit__ = MagicMock(return_value=None)
            mock_row.classes.return_value = mock_row
            mock_ui.row.return_value = mock_row

            mock_card = MagicMock()
            mock_card.__enter__ = MagicMock(return_value=mock_card)
            mock_card.__exit__ = MagicMock(return_value=None)
            mock_card.classes.return_value = mock_card
            mock_ui.card.return_value = mock_card

            mock_ui.label.return_value = MagicMock()
            mock_ui.label.return_value.classes.return_value = MagicMock()

            result = create_summary_cards(
                avg_is_bps=2.5,
                avg_vwap_bps=1.5,
                avg_impact_bps=1.0,
                fill_rate=0.95,
                total_notional=5000000.0,
                total_orders=100,
            )

            mock_ui.row.assert_called()
            # Should create multiple cards (at least 4)
            assert mock_ui.card.call_count >= 4
            assert result is not None

    def test_create_summary_cards_zero_values(self) -> None:
        """Summary cards handle zero values."""
        with patch("apps.web_console_ng.components.tca_chart.ui") as mock_ui:
            mock_row = MagicMock()
            mock_row.__enter__ = MagicMock(return_value=mock_row)
            mock_row.__exit__ = MagicMock(return_value=None)
            mock_row.classes.return_value = mock_row
            mock_ui.row.return_value = mock_row

            mock_card = MagicMock()
            mock_card.__enter__ = MagicMock(return_value=mock_card)
            mock_card.__exit__ = MagicMock(return_value=None)
            mock_card.classes.return_value = mock_card
            mock_ui.card.return_value = mock_card

            mock_ui.label.return_value = MagicMock()
            mock_ui.label.return_value.classes.return_value = MagicMock()

            # Should not raise
            create_summary_cards(
                avg_is_bps=0,
                avg_vwap_bps=0,
                avg_impact_bps=0,
                fill_rate=0,
                total_notional=0,
                total_orders=0,
            )

    def test_create_summary_cards_large_notional(self) -> None:
        """Summary cards format large notional correctly."""
        with patch("apps.web_console_ng.components.tca_chart.ui") as mock_ui:
            mock_row = MagicMock()
            mock_row.__enter__ = MagicMock(return_value=mock_row)
            mock_row.__exit__ = MagicMock(return_value=None)
            mock_row.classes.return_value = mock_row
            mock_ui.row.return_value = mock_row

            mock_card = MagicMock()
            mock_card.__enter__ = MagicMock(return_value=mock_card)
            mock_card.__exit__ = MagicMock(return_value=None)
            mock_card.classes.return_value = mock_card
            mock_ui.card.return_value = mock_card

            labels_created = []

            def capture_label(text: str) -> MagicMock:
                labels_created.append(text)
                mock = MagicMock()
                mock.classes.return_value = mock
                return mock

            mock_ui.label.side_effect = capture_label

            create_summary_cards(
                avg_is_bps=2.5,
                avg_vwap_bps=1.5,
                avg_impact_bps=1.0,
                fill_rate=0.95,
                total_notional=5000000.0,  # 5M
                total_orders=100,
            )

            # Should format as $5.0M
            assert any("5.0M" in str(label) or "$5" in str(label) for label in labels_created)


class TestCreateShortfallDecompositionChart:
    """Tests for create_shortfall_decomposition_chart function."""

    def test_create_chart_basic(self) -> None:
        """Create decomposition chart with valid data."""
        with patch("apps.web_console_ng.components.tca_chart.ui") as mock_ui:
            mock_echart = MagicMock()
            mock_echart.classes.return_value = mock_echart
            mock_ui.echart.return_value = mock_echart

            result = create_shortfall_decomposition_chart(
                labels=["2024-01-01", "2024-01-02", "2024-01-03"],
                price_shortfall=[1.5, 2.0, 1.8],
                fee_cost=[0.5, 0.5, 0.5],
                opportunity_cost=[0.3, 0.4, 0.2],
                timing_cost=[0.7, 0.8, 0.6],
            )

            mock_ui.echart.assert_called_once()
            call_args = mock_ui.echart.call_args
            options = call_args[0][0]

            assert "series" in options
            assert len(options["series"]) == 4  # 4 cost components
            assert "xAxis" in options
            assert "yAxis" in options
            assert result is not None

    def test_create_chart_empty_data(self) -> None:
        """Chart handles empty data."""
        with patch("apps.web_console_ng.components.tca_chart.ui") as mock_ui:
            mock_echart = MagicMock()
            mock_echart.classes.return_value = mock_echart
            mock_ui.echart.return_value = mock_echart

            # Should not raise
            create_shortfall_decomposition_chart(
                labels=[],
                price_shortfall=[],
                fee_cost=[],
                opportunity_cost=[],
                timing_cost=[],
            )

    def test_create_chart_negative_values(self) -> None:
        """Chart handles negative values (favorable execution)."""
        with patch("apps.web_console_ng.components.tca_chart.ui") as mock_ui:
            mock_echart = MagicMock()
            mock_echart.classes.return_value = mock_echart
            mock_ui.echart.return_value = mock_echart

            # Should not raise
            create_shortfall_decomposition_chart(
                labels=["2024-01-01"],
                price_shortfall=[-1.5],  # Price improvement
                fee_cost=[0.5],
                opportunity_cost=[0],
                timing_cost=[-0.2],
            )

    def test_create_chart_uses_colors(self) -> None:
        """Chart uses TCA color palette."""
        with patch("apps.web_console_ng.components.tca_chart.ui") as mock_ui:
            mock_echart = MagicMock()
            mock_echart.classes.return_value = mock_echart
            mock_ui.echart.return_value = mock_echart

            create_shortfall_decomposition_chart(
                labels=["2024-01-01"],
                price_shortfall=[1.5],
                fee_cost=[0.5],
                opportunity_cost=[0.3],
                timing_cost=[0.7],
            )

            call_args = mock_ui.echart.call_args
            options = call_args[0][0]

            # Verify colors from TCA_COLORS are used
            colors_used = [s["itemStyle"]["color"] for s in options["series"]]
            assert TCA_COLORS["price_shortfall"] in colors_used


class TestCreateBenchmarkComparisonChart:
    """Tests for create_benchmark_comparison_chart function."""

    def test_create_chart_basic(self) -> None:
        """Create benchmark chart with valid data."""
        with patch("apps.web_console_ng.components.tca_chart.ui") as mock_ui:
            mock_echart = MagicMock()
            mock_echart.classes.return_value = mock_echart
            mock_ui.echart.return_value = mock_echart

            result = create_benchmark_comparison_chart(
                timestamps=["10:00", "10:05", "10:10"],
                execution_prices=[150.00, 150.10, 150.15],
                benchmark_prices=[150.05, 150.08, 150.10],
                benchmark_type="VWAP",
            )

            mock_ui.echart.assert_called_once()
            call_args = mock_ui.echart.call_args
            options = call_args[0][0]

            assert "series" in options
            assert len(options["series"]) == 2  # Execution and benchmark
            assert result is not None

    def test_create_chart_with_symbol(self) -> None:
        """Chart title includes symbol."""
        with patch("apps.web_console_ng.components.tca_chart.ui") as mock_ui:
            mock_echart = MagicMock()
            mock_echart.classes.return_value = mock_echart
            mock_ui.echart.return_value = mock_echart

            create_benchmark_comparison_chart(
                timestamps=["10:00"],
                execution_prices=[150.00],
                benchmark_prices=[150.05],
                benchmark_type="VWAP",
                symbol="AAPL",
            )

            call_args = mock_ui.echart.call_args
            options = call_args[0][0]

            assert "AAPL" in options["title"]["text"]

    def test_create_chart_different_benchmarks(self) -> None:
        """Create charts for different benchmark types."""
        for benchmark_type in ["VWAP", "TWAP", "Arrival"]:
            with patch("apps.web_console_ng.components.tca_chart.ui") as mock_ui:
                mock_echart = MagicMock()
                mock_echart.classes.return_value = mock_echart
                mock_ui.echart.return_value = mock_echart

                create_benchmark_comparison_chart(
                    timestamps=["10:00"],
                    execution_prices=[150.00],
                    benchmark_prices=[150.05],
                    benchmark_type=benchmark_type,
                )

                call_args = mock_ui.echart.call_args
                options = call_args[0][0]

                # Benchmark type should be in legend
                assert benchmark_type in options["legend"]["data"]

    def test_create_chart_empty_data(self) -> None:
        """Chart handles empty data."""
        with patch("apps.web_console_ng.components.tca_chart.ui") as mock_ui:
            mock_echart = MagicMock()
            mock_echart.classes.return_value = mock_echart
            mock_ui.echart.return_value = mock_echart

            # Should not raise
            create_benchmark_comparison_chart(
                timestamps=[],
                execution_prices=[],
                benchmark_prices=[],
            )

    def test_create_chart_single_point(self) -> None:
        """Chart handles single data point."""
        with patch("apps.web_console_ng.components.tca_chart.ui") as mock_ui:
            mock_echart = MagicMock()
            mock_echart.classes.return_value = mock_echart
            mock_ui.echart.return_value = mock_echart

            create_benchmark_comparison_chart(
                timestamps=["10:00"],
                execution_prices=[150.00],
                benchmark_prices=[150.00],
            )

            mock_ui.echart.assert_called_once()

    def test_create_chart_custom_height(self) -> None:
        """Chart respects custom height."""
        with patch("apps.web_console_ng.components.tca_chart.ui") as mock_ui:
            mock_echart = MagicMock()
            mock_echart.classes.return_value = mock_echart
            mock_ui.echart.return_value = mock_echart

            create_benchmark_comparison_chart(
                timestamps=["10:00"],
                execution_prices=[150.00],
                benchmark_prices=[150.05],
                height=500,
            )

            # Should use custom height in classes
            mock_echart.classes.assert_called_once()
            class_call = mock_echart.classes.call_args[0][0]
            assert "500" in class_call
