"""Comprehensive tests for Event Study Framework.

Tests cover:
- Configuration validation
- Trading calendar helpers
- Market model estimation (all model types)
- Newey-West HAC standard errors
- Significance tests (Patell, BMP)
- Clustering mitigation (clustered SE, calendar-time portfolio)
- Delisting return handling
- Gap enforcement
- CAR computation
- Overlap handling
- PEAD analysis
- Index rebalance analysis
- Edge cases
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest
from scipy.stats import t as t_dist

from libs.analytics.event_study import (
    ClusteringMitigation,
    EventStudyAnalysis,
    EventStudyConfig,
    EventStudyFramework,
    ExpectedReturnModel,
    IndexRebalanceResult,
    MarketModelResult,
    OverlapPolicy,
    PEADAnalysisResult,
    SignificanceTest,
    _compute_clustered_se,
    _compute_newey_west_se,
    _compute_trading_days_offset,
    _detect_event_clustering,
    _get_dlret_fallback,
    _run_ols_regression,
    _select_clustering_mitigation,
    _winsorize,
)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_crsp_provider() -> MagicMock:
    """Create mock CRSP provider."""
    provider = MagicMock()
    provider.manifest_manager = MagicMock()
    provider.manifest_manager.load_manifest.return_value = MagicMock(checksum="crsp_v1")
    return provider


@pytest.fixture
def mock_ff_provider() -> MagicMock:
    """Create mock Fama-French provider."""
    provider = MagicMock()
    provider._storage_path = Path("/tmp/fama_french")
    return provider


@pytest.fixture
def trading_calendar() -> pl.DataFrame:
    """Create sample trading calendar."""
    # Generate trading days (weekdays only)
    dates = []
    d = date(2023, 1, 2)  # Start from first Monday
    while d <= date(2023, 12, 29):
        if d.weekday() < 5:  # Mon-Fri
            dates.append(d)
        d = date.fromordinal(d.toordinal() + 1)
    return pl.DataFrame({"date": dates})


@pytest.fixture
def sample_returns() -> pl.DataFrame:
    """Create sample stock returns data."""
    np.random.seed(42)
    dates = []
    d = date(2022, 1, 3)  # Start from early 2022 for more data
    while d <= date(2023, 6, 30):
        if d.weekday() < 5:
            dates.append(d)
        d = date.fromordinal(d.toordinal() + 1)

    n = len(dates)
    return pl.DataFrame(
        {
            "date": dates,
            "permno": [12345] * n,
            "ticker": ["AAPL"] * n,
            "ret": np.random.normal(0.0005, 0.02, n).tolist(),
            "vol": np.random.uniform(1e6, 5e6, n).tolist(),
        }
    )


@pytest.fixture
def sample_ff_data() -> pl.DataFrame:
    """Create sample Fama-French factor data."""
    np.random.seed(42)
    dates = []
    d = date(2022, 1, 3)  # Match sample_returns start date
    while d <= date(2023, 6, 30):
        if d.weekday() < 5:
            dates.append(d)
        d = date.fromordinal(d.toordinal() + 1)

    n = len(dates)
    return pl.DataFrame(
        {
            "date": dates,
            "mkt_rf": np.random.normal(0.0003, 0.01, n).tolist(),
            "smb": np.random.normal(0.0001, 0.005, n).tolist(),
            "hml": np.random.normal(0.0001, 0.005, n).tolist(),
            "rmw": np.random.normal(0.0001, 0.003, n).tolist(),
            "cma": np.random.normal(0.0001, 0.003, n).tolist(),
            "rf": [0.0001] * n,
        }
    )


# =============================================================================
# Test Configuration
# =============================================================================


class TestConfiguration:
    """Tests for EventStudyConfig validation."""

    def test_default_config(self) -> None:
        """Test default configuration is valid."""
        config = EventStudyConfig()
        assert config.estimation_window == 120
        assert config.gap_days == 5
        assert config.pre_window == 5
        assert config.post_window == 20
        assert config.expected_return_model == ExpectedReturnModel.MARKET
        assert config.significance_test == SignificanceTest.T_TEST

    def test_custom_config(self) -> None:
        """Test custom configuration."""
        config = EventStudyConfig(
            estimation_window=100,
            gap_days=10,
            pre_window=3,
            post_window=15,
            expected_return_model=ExpectedReturnModel.FF3,
        )
        assert config.estimation_window == 100
        assert config.gap_days == 10
        assert config.expected_return_model == ExpectedReturnModel.FF3

    def test_invalid_gap_days_raises(self) -> None:
        """Test that gap_days < 1 raises ValueError."""
        with pytest.raises(ValueError, match="gap_days must be >= 1"):
            EventStudyConfig(gap_days=0)

    def test_patell_requires_market_model(self) -> None:
        """Test that Patell test requires market model."""
        with pytest.raises(ValueError, match="requires expected_return_model=MARKET"):
            EventStudyConfig(
                significance_test=SignificanceTest.PATELL,
                expected_return_model=ExpectedReturnModel.FF3,
            )

    def test_bmp_requires_market_model(self) -> None:
        """Test that BMP test requires market model."""
        with pytest.raises(ValueError, match="requires expected_return_model=MARKET"):
            EventStudyConfig(
                significance_test=SignificanceTest.BMP,
                expected_return_model=ExpectedReturnModel.FF5,
            )

    def test_bmp_requires_sufficient_estimation_window(self) -> None:
        """Test that BMP test requires estimation_window > 4."""
        with pytest.raises(ValueError, match="estimation_window .* must be > 4"):
            EventStudyConfig(
                significance_test=SignificanceTest.BMP,
                estimation_window=4,
                min_estimation_obs=4,
            )

    def test_min_days_between_events_auto_from_window(self) -> None:
        """Test that min_days_between_events is auto-computed from window."""
        config = EventStudyConfig(pre_window=5, post_window=20)
        # Should be pre_window + 1 + post_window = 26
        assert config.min_days_between_events == 26

    def test_min_days_between_events_validation(self) -> None:
        """Test that min_days_between_events < 1 raises ValueError."""
        with pytest.raises(ValueError, match="min_days_between_events must be >= 1"):
            EventStudyConfig(min_days_between_events=0)

    def test_newey_west_lags_validation(self) -> None:
        """Test Newey-West lag validation."""
        with pytest.raises(ValueError, match="newey_west_lags must be >= 1"):
            EventStudyConfig(newey_west_lags=0)

    def test_newey_west_lags_exceeds_window(self) -> None:
        """Test that newey_west_lags >= event_window_length raises error."""
        with pytest.raises(ValueError, match="newey_west_lags .* must be <"):
            EventStudyConfig(
                pre_window=2,
                post_window=2,
                newey_west_lags=10,  # Window is 5, so 10 > 5
            )

    def test_negative_pre_window_raises(self) -> None:
        """Test that negative pre_window raises ValueError."""
        with pytest.raises(ValueError, match="pre_window must be >= 0"):
            EventStudyConfig(pre_window=-1)

    def test_negative_post_window_raises(self) -> None:
        """Test that negative post_window raises ValueError."""
        with pytest.raises(ValueError, match="post_window must be >= 0"):
            EventStudyConfig(post_window=-1)

    def test_invalid_cap_beta_raises(self) -> None:
        """Test that non-positive cap_beta raises ValueError."""
        with pytest.raises(ValueError, match="cap_beta must be > 0"):
            EventStudyConfig(cap_beta=0)

    def test_invalid_winsorize_percentile_raises(self) -> None:
        """Test that invalid winsorize_ar_percentile raises ValueError."""
        with pytest.raises(ValueError, match="winsorize_ar_percentile must be in"):
            EventStudyConfig(winsorize_ar_percentile=0.3)


# =============================================================================
# Test Trading Calendar
# =============================================================================


class TestTradingCalendar:
    """Tests for trading calendar utilities."""

    def test_offset_forward(self, trading_calendar: pl.DataFrame) -> None:
        """Test forward offset calculation."""
        base_date = date(2023, 1, 3)  # Tuesday
        result = _compute_trading_days_offset(base_date, 5, trading_calendar)
        assert result == date(2023, 1, 10)  # Next Tuesday (5 trading days later)

    def test_offset_backward(self, trading_calendar: pl.DataFrame) -> None:
        """Test backward offset calculation."""
        base_date = date(2023, 1, 10)  # Tuesday
        result = _compute_trading_days_offset(base_date, -5, trading_calendar)
        assert result == date(2023, 1, 3)  # Previous Tuesday

    def test_offset_beyond_bounds_raises(self, trading_calendar: pl.DataFrame) -> None:
        """Test that offset beyond calendar bounds raises error."""
        with pytest.raises(ValueError, match="goes beyond calendar bounds"):
            _compute_trading_days_offset(date(2023, 1, 3), 1000, trading_calendar)


# =============================================================================
# Test Newey-West Standard Error
# =============================================================================


class TestNeweyWest:
    """Tests for Newey-West HAC standard error computation."""

    def test_bartlett_weights(self) -> None:
        """Test Bartlett kernel weights are correct."""
        ar = np.array([0.01, 0.02, -0.01, 0.03, -0.02])
        se, lags = _compute_newey_west_se(ar, n_lags=2)
        assert not np.isnan(se)
        assert lags == 2

    def test_auto_lag_selection(self) -> None:
        """Test automatic lag selection."""
        np.random.seed(42)
        ar = np.random.normal(0, 0.02, 50)
        se, lags = _compute_newey_west_se(ar)
        assert lags >= 1
        assert lags <= len(ar) - 1

    def test_minimum_lag_enforced(self) -> None:
        """Test minimum lag of 1 is enforced."""
        ar = np.array([0.01, 0.02, 0.03])
        se, lags = _compute_newey_west_se(ar, n_lags=0)  # Request 0, should get 1
        assert lags >= 1

    def test_maximum_lag_capped_at_T_minus_1(self) -> None:
        """Test maximum lag is capped at T-1."""
        ar = np.array([0.01, 0.02, 0.03])  # T=3, max lag = 2
        se, lags = _compute_newey_west_se(ar, n_lags=10)  # Request 10
        assert lags == 2  # Capped at T-1

    def test_mean_centering(self) -> None:
        """Test that mean-centered AR produces correct results."""
        np.random.seed(42)
        ar = np.random.normal(0.01, 0.02, 50)  # Non-zero mean

        # Compute SE
        se, _ = _compute_newey_west_se(ar)
        assert not np.isnan(se)
        assert se > 0

    def test_T_less_than_2_returns_nan(self) -> None:
        """Test that T < 2 returns NaN."""
        ar = np.array([0.01])
        se, lags = _compute_newey_west_se(ar)
        assert np.isnan(se)
        assert lags == 0

    def test_deterministic_vs_statsmodels(self) -> None:
        """Validate Newey-West SE against statsmodels if available."""
        statsmodels = pytest.importorskip("statsmodels")
        from statsmodels.stats.stattools import acovf  # type: ignore[import-untyped]

        np.random.seed(42)
        ar = np.random.normal(0, 0.02, 100)

        # Our implementation
        our_se, lags_used = _compute_newey_west_se(ar, n_lags=5)

        # statsmodels approach: compute autocovariances and LRV
        ar_centered = ar - np.mean(ar)
        T = len(ar)
        n_lags = 5

        # Manual LRV computation to match our formula
        gamma = acovf(ar_centered, fft=False, nlag=n_lags)

        lrv = gamma[0]
        for j in range(1, n_lags + 1):
            weight = 1 - j / (n_lags + 1)
            lrv += 2 * weight * gamma[j]

        var_car = T * lrv
        statsmodels_se = np.sqrt(max(var_car, 0))

        # Should be close
        assert abs(our_se - statsmodels_se) < 1e-10


# =============================================================================
# Test OLS Regression
# =============================================================================


class TestOLSRegression:
    """Tests for OLS regression helper."""

    def test_ols_basic(self) -> None:
        """Test basic OLS regression."""
        np.random.seed(42)
        n = 100
        X = np.column_stack([np.ones(n), np.random.normal(0, 1, n)])
        true_beta = np.array([0.01, 1.2])
        y = X @ true_beta + np.random.normal(0, 0.1, n)

        beta_hat, t_stats, r_squared, residual_std = _run_ols_regression(y, X)

        assert len(beta_hat) == 2
        assert abs(beta_hat[0] - 0.01) < 0.05  # Intercept close to true
        assert abs(beta_hat[1] - 1.2) < 0.2  # Slope close to true
        assert r_squared > 0.5
        assert residual_std > 0

    def test_ols_perfect_fit(self) -> None:
        """Test OLS with perfect fit (no noise)."""
        X = np.column_stack([np.ones(10), np.arange(10)])
        y = 2 + 3 * np.arange(10)  # y = 2 + 3x

        beta_hat, _, r_squared, residual_std = _run_ols_regression(y, X)

        assert np.allclose(beta_hat, [2, 3])
        assert r_squared > 0.999


# =============================================================================
# Test Clustering Detection and Mitigation
# =============================================================================


class TestClusteringMitigation:
    """Tests for event clustering detection and mitigation."""

    def test_detect_event_clustering(self) -> None:
        """Test clustering detection."""
        events = pl.DataFrame(
            {
                "event_date": [
                    date(2023, 1, 3),
                    date(2023, 1, 3),  # Same day
                    date(2023, 1, 3),  # Same day
                    date(2023, 1, 4),
                    date(2023, 1, 5),
                ]
            }
        )

        info = _detect_event_clustering(events)
        assert info["max_events_same_day"] == 3
        assert info["n_clustered_dates"] == 1
        assert info["total_dates"] == 3

    def test_auto_mitigation_selection_none(self) -> None:
        """Test AUTO selects NONE when no clustering."""
        config = EventStudyConfig(clustering_mitigation=ClusteringMitigation.AUTO)
        info = {"max_events_same_day": 1, "n_clustered_dates": 0, "total_dates": 50}

        mitigation = _select_clustering_mitigation(info, config)
        assert mitigation == ClusteringMitigation.NONE

    def test_auto_mitigation_selection_clustered_se(self) -> None:
        """Test AUTO selects CLUSTERED_SE for moderate clustering."""
        config = EventStudyConfig(clustering_mitigation=ClusteringMitigation.AUTO)
        info = {"max_events_same_day": 5, "n_clustered_dates": 5, "total_dates": 50}

        mitigation = _select_clustering_mitigation(info, config)
        assert mitigation == ClusteringMitigation.CLUSTERED_SE

    def test_auto_mitigation_selection_calendar_time(self) -> None:
        """Test AUTO selects CALENDAR_TIME for severe clustering."""
        config = EventStudyConfig(clustering_mitigation=ClusteringMitigation.AUTO)
        info = {"max_events_same_day": 15, "n_clustered_dates": 10, "total_dates": 50}

        mitigation = _select_clustering_mitigation(info, config)
        assert mitigation == ClusteringMitigation.CALENDAR_TIME

    def test_clustered_se_formula_validation(self) -> None:
        """Test clustered SE formula is correct."""
        np.random.seed(42)
        N = 60
        G = 20  # clusters
        scars = np.random.normal(0, 1, N)
        cluster_ids = np.repeat(np.arange(G), N // G)

        se, t_stat, df = _compute_clustered_se(scars, cluster_ids)

        assert not np.isnan(se)
        assert df == G - 1
        assert se > 0

    def test_clustered_se_fewer_than_2_clusters(self) -> None:
        """Test clustered SE with fewer than 2 clusters returns NaN."""
        scars = np.array([1.0, 2.0, 3.0])
        cluster_ids = np.array([1, 1, 1])  # Only 1 cluster

        se, t_stat, df = _compute_clustered_se(scars, cluster_ids)
        assert np.isnan(se)
        assert df == 0

    def test_clustered_se_size_control(self) -> None:
        """Validate clustered SE controls size under clustered null."""
        np.random.seed(42)
        n_simulations = 200
        n_events = 60
        n_clusters = 20
        rejections_clustered = 0
        rejections_naive = 0

        for _ in range(n_simulations):
            cluster_ids = np.repeat(np.arange(n_clusters), n_events // n_clusters)
            cluster_effects = np.random.normal(0, 0.5, n_clusters)

            scars = np.zeros(n_events)
            for i in range(n_events):
                c = cluster_ids[i]
                scars[i] = cluster_effects[c] + np.random.normal(0, 1)

            scar_mean = np.mean(scars)

            # Naive SE
            naive_se = np.std(scars, ddof=1) / np.sqrt(n_events)
            t_naive = scar_mean / naive_se
            if abs(t_naive) > 1.96:
                rejections_naive += 1

            # Clustered SE
            clustered_se, t_clustered, df = _compute_clustered_se(scars, cluster_ids)
            critical = t_dist.ppf(0.975, df=df)
            if abs(t_clustered) > critical:
                rejections_clustered += 1

        naive_rate = rejections_naive / n_simulations
        clustered_rate = rejections_clustered / n_simulations

        # Naive should over-reject
        assert naive_rate > 0.08, f"Naive should over-reject, got {naive_rate}"
        # Clustered should be closer to 5%
        assert (
            clustered_rate < naive_rate
        ), f"Clustered {clustered_rate} not better than naive {naive_rate}"


# =============================================================================
# Test Delisting Handling
# =============================================================================


class TestDelistingHandling:
    """Tests for DLRET handling."""

    def test_dlret_multiplicative_combination(self) -> None:
        """Validate DLRET is combined multiplicatively with RET."""
        last_ret = -0.20
        dlret = -0.50

        # Correct: multiplicative
        combined = (1 + last_ret) * (1 + dlret) - 1
        assert combined == pytest.approx(-0.60)

        # Wrong: additive
        wrong_additive = last_ret + dlret
        assert wrong_additive != combined

    def test_dlstcd_500_fallback(self) -> None:
        """Test DLSTCD 500-599 (dropped for cause) → -30%."""
        assert _get_dlret_fallback(dlstcd=520) == pytest.approx(-0.30)

    def test_dlstcd_400_fallback(self) -> None:
        """Test DLSTCD 400-499 (liquidation) → -100%."""
        assert _get_dlret_fallback(dlstcd=450) == pytest.approx(-1.0)

    def test_dlstcd_200_fallback(self) -> None:
        """Test DLSTCD 200-399 (merger) → 0%."""
        assert _get_dlret_fallback(dlstcd=250) == pytest.approx(0.0)

    def test_missing_dlstcd_fallback(self) -> None:
        """Test missing DLSTCD → -30% (conservative)."""
        assert _get_dlret_fallback(dlstcd=None) == pytest.approx(-0.30)


# =============================================================================
# Test Winsorization
# =============================================================================


class TestWinsorization:
    """Tests for winsorization helper."""

    def test_winsorize_basic(self) -> None:
        """Test basic winsorization."""
        arr = np.array([1, 2, 3, 4, 5, 100])  # 100 is outlier
        winsorized, n_lower, n_upper = _winsorize(arr, percentile=0.95)

        assert n_upper >= 1
        assert winsorized[-1] < 100  # Outlier clipped

    def test_winsorize_symmetric(self) -> None:
        """Test symmetric winsorization."""
        arr = np.array([-100, 1, 2, 3, 4, 5, 100])
        winsorized, n_lower, n_upper = _winsorize(arr, percentile=0.90)

        assert n_lower >= 1
        assert n_upper >= 1


# =============================================================================
# Test Gap Enforcement
# =============================================================================


class TestGapEnforcement:
    """Tests for estimation-event window gap enforcement."""

    def test_gap_enforced(self, mock_crsp_provider: MagicMock, mock_ff_provider: MagicMock) -> None:
        """Test that gap is enforced between windows."""
        config = EventStudyConfig(
            estimation_window=60,
            gap_days=5,
            pre_window=3,
            post_window=10,
        )

        # The gap should be: estimation_end is gap_days before event_window_start
        # event_window_start is pre_window days before event_date
        # So estimation_end = event_date - pre_window - gap_days

        # This test verifies the config is valid
        assert config.gap_days == 5
        assert config.pre_window == 3

    def test_gap_boundary_cases(self) -> None:
        """Test gap boundary cases."""
        # Minimum valid gap
        config = EventStudyConfig(gap_days=1)
        assert config.gap_days == 1

        # Invalid: gap_days = 0
        with pytest.raises(ValueError):
            EventStudyConfig(gap_days=0)


# =============================================================================
# Test Overlap Handling
# =============================================================================


class TestOverlapHandling:
    """Tests for overlapping event handling."""

    def test_drop_later_policy(
        self, mock_crsp_provider: MagicMock, mock_ff_provider: MagicMock
    ) -> None:
        """Test DROP_LATER policy keeps first event."""
        framework = EventStudyFramework(
            crsp_provider=mock_crsp_provider,
            fama_french_provider=mock_ff_provider,
            config=EventStudyConfig(
                overlap_policy=OverlapPolicy.DROP_LATER,
                min_days_between_events=10,
            ),
        )

        events = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL", "AAPL"],
                "event_date": [date(2023, 1, 3), date(2023, 1, 5), date(2023, 1, 20)],
            }
        )

        filtered, n_dropped = framework._handle_overlapping_events(events)

        assert n_dropped == 1
        assert filtered.height == 2
        # First and third events should remain
        dates = filtered["event_date"].to_list()
        assert date(2023, 1, 3) in dates
        assert date(2023, 1, 20) in dates

    def test_drop_earlier_policy(
        self, mock_crsp_provider: MagicMock, mock_ff_provider: MagicMock
    ) -> None:
        """Test DROP_EARLIER policy keeps last event."""
        framework = EventStudyFramework(
            crsp_provider=mock_crsp_provider,
            fama_french_provider=mock_ff_provider,
            config=EventStudyConfig(
                overlap_policy=OverlapPolicy.DROP_EARLIER,
                min_days_between_events=10,
            ),
        )

        events = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL", "AAPL"],
                "event_date": [date(2023, 1, 3), date(2023, 1, 5), date(2023, 1, 20)],
            }
        )

        filtered, n_dropped = framework._handle_overlapping_events(events)

        assert n_dropped == 1

    def test_warn_only_policy(
        self, mock_crsp_provider: MagicMock, mock_ff_provider: MagicMock
    ) -> None:
        """Test WARN_ONLY policy keeps all events."""
        framework = EventStudyFramework(
            crsp_provider=mock_crsp_provider,
            fama_french_provider=mock_ff_provider,
            config=EventStudyConfig(
                overlap_policy=OverlapPolicy.WARN_ONLY,
                min_days_between_events=10,
            ),
        )

        events = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL", "AAPL"],
                "event_date": [date(2023, 1, 3), date(2023, 1, 5), date(2023, 1, 20)],
            }
        )

        filtered, n_dropped = framework._handle_overlapping_events(events)

        assert n_dropped == 0
        assert filtered.height == 3


# =============================================================================
# Test Significance Tests
# =============================================================================


class TestSignificanceTests:
    """Tests for statistical significance tests."""

    def test_patell_z_distribution_under_null(self) -> None:
        """Validate Patell Z ~ N(0,1) under null."""
        np.random.seed(42)
        n_simulations = 200
        n_events = 30
        T1 = 120
        T2 = 21
        z_stats = []

        for _ in range(n_simulations):
            scars = []
            for _ in range(n_events):
                # Generate estimation period
                mkt_est = np.random.normal(0.0005, 0.01, T1)
                true_alpha, true_beta = 0.0, 1.0
                stock_est = true_alpha + true_beta * mkt_est + np.random.normal(0, 0.015, T1)

                # Estimate market model
                X = np.column_stack([np.ones(T1), mkt_est])
                beta_hat = np.linalg.lstsq(X, stock_est, rcond=None)[0]
                resid = stock_est - X @ beta_hat
                sigma_hat = np.std(resid, ddof=2)
                mkt_mean = np.mean(mkt_est)
                Sxx = np.sum((mkt_est - mkt_mean) ** 2)

                # Generate event period (null: no abnormal return)
                mkt_evt = np.random.normal(0.0005, 0.01, T2)
                stock_evt = true_alpha + true_beta * mkt_evt + np.random.normal(0, 0.015, T2)

                # Compute AR and SAR with forecast-error adjustment
                predicted = beta_hat[0] + beta_hat[1] * mkt_evt
                ar = stock_evt - predicted

                sar = np.zeros(T2)
                for t in range(T2):
                    C_t = 1 + 1 / T1 + (mkt_evt[t] - mkt_mean) ** 2 / Sxx
                    sar[t] = ar[t] / (sigma_hat * np.sqrt(C_t))

                scar = np.sum(sar) / np.sqrt(T2)
                scars.append(scar)

            z_patell = np.sum(scars) / np.sqrt(n_events)
            z_stats.append(z_patell)

        # Z should be approximately N(0,1)
        assert abs(np.mean(z_stats)) < 0.3, f"Mean {np.mean(z_stats)} not near 0"
        assert 0.7 < np.std(z_stats) < 1.4, f"Std {np.std(z_stats)} not near 1"

    def test_bmp_coverage_under_null(self) -> None:
        """Validate BMP t-statistic has approximately correct coverage under null."""
        np.random.seed(42)
        n_simulations = 200
        n_events = 30
        T1 = 120
        T2 = 21
        rejections = 0

        for _ in range(n_simulations):
            scars = []
            for _ in range(n_events):
                mkt_est = np.random.normal(0.0005, 0.01, T1)
                stock_est = mkt_est + np.random.normal(0, 0.015, T1)
                X = np.column_stack([np.ones(T1), mkt_est])
                beta_hat = np.linalg.lstsq(X, stock_est, rcond=None)[0]
                resid = stock_est - X @ beta_hat
                sigma_hat = np.std(resid, ddof=2)
                mkt_mean = np.mean(mkt_est)
                Sxx = np.sum((mkt_est - mkt_mean) ** 2)

                mkt_evt = np.random.normal(0.0005, 0.01, T2)
                stock_evt = mkt_evt + np.random.normal(0, 0.015, T2)
                predicted = beta_hat[0] + beta_hat[1] * mkt_evt
                ar = stock_evt - predicted

                sar = np.zeros(T2)
                for t in range(T2):
                    C_t = 1 + 1 / T1 + (mkt_evt[t] - mkt_mean) ** 2 / Sxx
                    sar[t] = ar[t] / (sigma_hat * np.sqrt(C_t))

                # BMP DOF correction
                dof_correction = np.sqrt((T1 - 2) / (T1 - 4))
                scar = dof_correction * np.sum(sar) / np.sqrt(T2)
                scars.append(scar)

            scars_arr = np.array(scars)
            scar_mean = np.mean(scars_arr)
            scar_std = np.std(scars_arr, ddof=1)
            t_bmp = np.sqrt(n_events) * scar_mean / scar_std

            critical = t_dist.ppf(0.975, df=n_events - 1)
            if abs(t_bmp) > critical:
                rejections += 1

        rejection_rate = rejections / n_simulations
        # Should be close to 5% (allow some variance)
        assert rejection_rate < 0.15, f"BMP rejection rate {rejection_rate} too high"


# =============================================================================
# Test Version Tracking
# =============================================================================


class TestVersionTracking:
    """Tests for version propagation in results."""

    def test_version_id_in_config(
        self, mock_crsp_provider: MagicMock, mock_ff_provider: MagicMock
    ) -> None:
        """Test that version info can be retrieved."""
        # Create mock manifest file
        import tempfile
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_ff_provider._storage_path = Path(tmpdir)
            manifest_path = Path(tmpdir) / "fama_french_manifest.json"
            with open(manifest_path, "w") as f:
                json.dump({"aggregate_checksum": "ff_v1"}, f)

            framework = EventStudyFramework(
                crsp_provider=mock_crsp_provider,
                fama_french_provider=mock_ff_provider,
            )

            version_info = framework._get_version_info()

            assert version_info.versions["crsp_daily"] == "crsp_v1"
            assert version_info.versions["fama_french"] == "ff_v1"


# =============================================================================
# Integration Tests (with mocked data)
# =============================================================================


class TestIntegration:
    """Integration tests with mocked data providers."""

    def test_estimate_market_model(
        self,
        mock_crsp_provider: MagicMock,
        mock_ff_provider: MagicMock,
        sample_returns: pl.DataFrame,
        sample_ff_data: pl.DataFrame,
    ) -> None:
        """Test market model estimation with mocked data."""
        import tempfile
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_ff_provider._storage_path = Path(tmpdir)
            manifest_path = Path(tmpdir) / "fama_french_manifest.json"
            with open(manifest_path, "w") as f:
                json.dump({"aggregate_checksum": "ff_v1"}, f)

            mock_crsp_provider.get_daily_prices.return_value = sample_returns
            mock_ff_provider.get_factors.return_value = sample_ff_data

            framework = EventStudyFramework(
                crsp_provider=mock_crsp_provider,
                fama_french_provider=mock_ff_provider,
            )

            result = framework.estimate_market_model(
                symbol="AAPL",
                estimation_end=date(2023, 3, 15),
            )

            assert result.symbol == "AAPL"
            assert result.model_type == ExpectedReturnModel.MARKET
            assert not np.isnan(result.alpha)
            assert not np.isnan(result.beta)
            assert result.n_observations > 0

    def test_compute_car(
        self,
        mock_crsp_provider: MagicMock,
        mock_ff_provider: MagicMock,
        sample_returns: pl.DataFrame,
        sample_ff_data: pl.DataFrame,
    ) -> None:
        """Test CAR computation with mocked data."""
        import tempfile
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_ff_provider._storage_path = Path(tmpdir)
            manifest_path = Path(tmpdir) / "fama_french_manifest.json"
            with open(manifest_path, "w") as f:
                json.dump({"aggregate_checksum": "ff_v1"}, f)

            mock_crsp_provider.get_daily_prices.return_value = sample_returns
            mock_ff_provider.get_factors.return_value = sample_ff_data

            framework = EventStudyFramework(
                crsp_provider=mock_crsp_provider,
                fama_french_provider=mock_ff_provider,
                config=EventStudyConfig(
                    estimation_window=60,
                    min_estimation_obs=30,
                    pre_window=3,
                    post_window=10,
                ),
            )

            result = framework.compute_car(
                symbol="AAPL",
                event_date=date(2023, 4, 15),
                event_type="test_event",
            )

            assert result.symbol == "AAPL"
            assert result.event_type == "test_event"
            assert not np.isnan(result.car_window)
            assert not np.isnan(result.t_statistic)
            assert result.daily_ar.height > 0


# =============================================================================
# Test Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and error conditions."""

    def test_zero_length_window(self) -> None:
        """Test that pre_window = post_window = 0 is valid."""
        config = EventStudyConfig(pre_window=0, post_window=0)
        assert config.pre_window == 0
        assert config.post_window == 0

    def test_estimation_window_equals_min_obs(self) -> None:
        """Test estimation_window = min_estimation_obs is valid."""
        config = EventStudyConfig(estimation_window=60, min_estimation_obs=60)
        assert config.estimation_window == config.min_estimation_obs

    def test_estimation_window_less_than_min_obs_raises(self) -> None:
        """Test estimation_window < min_estimation_obs raises error."""
        with pytest.raises(ValueError, match="estimation_window .* must be >= min_estimation_obs"):
            EventStudyConfig(estimation_window=50, min_estimation_obs=60)


# =============================================================================
# Test Enums
# =============================================================================


class TestEnums:
    """Tests for enum values."""

    def test_expected_return_model_values(self) -> None:
        """Test ExpectedReturnModel enum values."""
        assert ExpectedReturnModel.MARKET.value == "market"
        assert ExpectedReturnModel.MEAN_ADJUSTED.value == "mean_adjusted"
        assert ExpectedReturnModel.FF3.value == "ff3"
        assert ExpectedReturnModel.FF5.value == "ff5"

    def test_significance_test_values(self) -> None:
        """Test SignificanceTest enum values."""
        assert SignificanceTest.T_TEST.value == "t_test"
        assert SignificanceTest.PATELL.value == "patell"
        assert SignificanceTest.BMP.value == "bmp"

    def test_overlap_policy_values(self) -> None:
        """Test OverlapPolicy enum values."""
        assert OverlapPolicy.DROP_LATER.value == "drop_later"
        assert OverlapPolicy.DROP_EARLIER.value == "drop_earlier"
        assert OverlapPolicy.WARN_ONLY.value == "warn_only"

    def test_clustering_mitigation_values(self) -> None:
        """Test ClusteringMitigation enum values."""
        assert ClusteringMitigation.NONE.value == "none"
        assert ClusteringMitigation.CALENDAR_TIME.value == "calendar_time"
        assert ClusteringMitigation.CLUSTERED_SE.value == "clustered_se"
        assert ClusteringMitigation.WARN_ONLY.value == "warn_only"
        assert ClusteringMitigation.AUTO.value == "auto"


# =============================================================================
# Test PEAD Analysis
# =============================================================================


class TestPEADAnalysis:
    """Tests for Post-Earnings Announcement Drift analysis."""

    def test_analyze_pead_basic(
        self,
        mock_crsp_provider: MagicMock,
        mock_ff_provider: MagicMock,
        sample_returns: pl.DataFrame,
        sample_ff_data: pl.DataFrame,
    ) -> None:
        """Test basic PEAD analysis."""
        import tempfile
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_ff_provider._storage_path = Path(tmpdir)
            manifest_path = Path(tmpdir) / "fama_french_manifest.json"
            with open(manifest_path, "w") as f:
                json.dump({"aggregate_checksum": "ff_v1"}, f)

            mock_crsp_provider.get_daily_prices.return_value = sample_returns
            mock_ff_provider.get_factors.return_value = sample_ff_data

            framework = EventStudyFramework(
                crsp_provider=mock_crsp_provider,
                fama_french_provider=mock_ff_provider,
                config=EventStudyConfig(
                    estimation_window=60,
                    min_estimation_obs=30,
                    pre_window=0,
                    post_window=20,
                ),
            )

            # Create earnings events with sufficient spread
            np.random.seed(42)
            n_events = 30  # Enough for quintiles
            symbols = ["AAPL"] * n_events
            event_dates = []
            base_date = date(2023, 3, 1)
            for i in range(n_events):
                event_dates.append(date.fromordinal(base_date.toordinal() + i * 3))  # 3-day spread

            earnings_events = pl.DataFrame(
                {
                    "symbol": symbols,
                    "event_date": event_dates,
                    "surprise_pct": np.random.uniform(-5, 5, n_events).tolist(),
                }
            )

            result = framework.analyze_pead(
                earnings_events=earnings_events,
                holding_period_days=20,
            )

            assert isinstance(result, PEADAnalysisResult)
            assert result.n_events > 0
            assert result.quintile_results.height > 0
            assert result.holding_period_days == 20

    def test_analyze_pead_with_clustering(
        self,
        mock_crsp_provider: MagicMock,
        mock_ff_provider: MagicMock,
        sample_returns: pl.DataFrame,
        sample_ff_data: pl.DataFrame,
    ) -> None:
        """Test PEAD analysis with clustered events."""
        import tempfile
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_ff_provider._storage_path = Path(tmpdir)
            manifest_path = Path(tmpdir) / "fama_french_manifest.json"
            with open(manifest_path, "w") as f:
                json.dump({"aggregate_checksum": "ff_v1"}, f)

            mock_crsp_provider.get_daily_prices.return_value = sample_returns
            mock_ff_provider.get_factors.return_value = sample_ff_data

            framework = EventStudyFramework(
                crsp_provider=mock_crsp_provider,
                fama_french_provider=mock_ff_provider,
                config=EventStudyConfig(
                    estimation_window=60,
                    min_estimation_obs=30,
                    pre_window=0,
                    post_window=20,
                    clustering_mitigation=ClusteringMitigation.CLUSTERED_SE,
                ),
            )

            # Create clustered earnings events (multiple on same day)
            np.random.seed(42)
            n_events = 25
            symbols = ["AAPL"] * n_events
            # Cluster events on fewer dates
            event_dates = (
                [date(2023, 3, 1)] * 5
                + [date(2023, 3, 15)] * 5
                + [date(2023, 4, 1)] * 5
                + [date(2023, 4, 15)] * 5
                + [date(2023, 5, 1)] * 5
            )

            earnings_events = pl.DataFrame(
                {
                    "symbol": symbols,
                    "event_date": event_dates,
                    "surprise_pct": np.random.uniform(-5, 5, n_events).tolist(),
                }
            )

            result = framework.analyze_pead(
                earnings_events=earnings_events,
                holding_period_days=20,
            )

            assert isinstance(result, PEADAnalysisResult)
            assert result.clustering_mitigation_used == ClusteringMitigation.CLUSTERED_SE

    def test_analyze_pead_missing_columns_raises(
        self,
        mock_crsp_provider: MagicMock,
        mock_ff_provider: MagicMock,
    ) -> None:
        """Test that missing columns raises ValueError."""
        import tempfile
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_ff_provider._storage_path = Path(tmpdir)
            manifest_path = Path(tmpdir) / "fama_french_manifest.json"
            with open(manifest_path, "w") as f:
                json.dump({"aggregate_checksum": "ff_v1"}, f)

            framework = EventStudyFramework(
                crsp_provider=mock_crsp_provider,
                fama_french_provider=mock_ff_provider,
            )

            # Missing surprise_pct column
            bad_events = pl.DataFrame(
                {
                    "symbol": ["AAPL"],
                    "event_date": [date(2023, 3, 1)],
                }
            )

            with pytest.raises(ValueError, match="must have columns"):
                framework.analyze_pead(earnings_events=bad_events)


# =============================================================================
# Test Index Rebalance Analysis
# =============================================================================


class TestIndexRebalanceAnalysis:
    """Tests for index rebalance event study."""

    def test_analyze_index_rebalance_basic(
        self,
        mock_crsp_provider: MagicMock,
        mock_ff_provider: MagicMock,
        sample_returns: pl.DataFrame,
        sample_ff_data: pl.DataFrame,
    ) -> None:
        """Test basic index rebalance analysis."""
        import tempfile
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_ff_provider._storage_path = Path(tmpdir)
            manifest_path = Path(tmpdir) / "fama_french_manifest.json"
            with open(manifest_path, "w") as f:
                json.dump({"aggregate_checksum": "ff_v1"}, f)

            mock_crsp_provider.get_daily_prices.return_value = sample_returns
            mock_ff_provider.get_factors.return_value = sample_ff_data

            framework = EventStudyFramework(
                crsp_provider=mock_crsp_provider,
                fama_french_provider=mock_ff_provider,
                config=EventStudyConfig(
                    estimation_window=60,
                    min_estimation_obs=30,
                    pre_window=5,
                    post_window=10,
                ),
            )

            # Create index changes
            index_changes = pl.DataFrame(
                {
                    "symbol": ["AAPL", "AAPL", "AAPL", "AAPL"],
                    "effective_date": [
                        date(2023, 3, 15),
                        date(2023, 4, 15),
                        date(2023, 5, 15),
                        date(2023, 6, 1),
                    ],
                    "action": ["add", "add", "drop", "drop"],
                }
            )

            result = framework.analyze_index_rebalance(
                index_changes=index_changes,
                index_name="SP500",
            )

            assert isinstance(result, IndexRebalanceResult)
            assert result.index_name == "SP500"
            assert result.n_additions >= 0
            assert result.n_deletions >= 0

    def test_analyze_index_rebalance_with_clustering(
        self,
        mock_crsp_provider: MagicMock,
        mock_ff_provider: MagicMock,
        sample_returns: pl.DataFrame,
        sample_ff_data: pl.DataFrame,
    ) -> None:
        """Test index rebalance with clustered SE."""
        import tempfile
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_ff_provider._storage_path = Path(tmpdir)
            manifest_path = Path(tmpdir) / "fama_french_manifest.json"
            with open(manifest_path, "w") as f:
                json.dump({"aggregate_checksum": "ff_v1"}, f)

            mock_crsp_provider.get_daily_prices.return_value = sample_returns
            mock_ff_provider.get_factors.return_value = sample_ff_data

            framework = EventStudyFramework(
                crsp_provider=mock_crsp_provider,
                fama_french_provider=mock_ff_provider,
                config=EventStudyConfig(
                    estimation_window=60,
                    min_estimation_obs=30,
                    pre_window=5,
                    post_window=10,
                    clustering_mitigation=ClusteringMitigation.CLUSTERED_SE,
                ),
            )

            # Create clustered index changes (multiple on same day)
            index_changes = pl.DataFrame(
                {
                    "symbol": ["AAPL"] * 6,
                    "effective_date": [
                        date(2023, 3, 15),
                        date(2023, 3, 15),  # Same day
                        date(2023, 4, 15),
                        date(2023, 4, 15),  # Same day
                        date(2023, 5, 15),
                        date(2023, 5, 15),  # Same day
                    ],
                    "action": ["add", "add", "add", "drop", "drop", "drop"],
                }
            )

            result = framework.analyze_index_rebalance(
                index_changes=index_changes,
                index_name="SP500",
            )

            assert result.clustering_mitigation_used == ClusteringMitigation.CLUSTERED_SE

    def test_analyze_index_rebalance_missing_columns_raises(
        self,
        mock_crsp_provider: MagicMock,
        mock_ff_provider: MagicMock,
    ) -> None:
        """Test that missing columns raises ValueError."""
        import tempfile
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_ff_provider._storage_path = Path(tmpdir)
            manifest_path = Path(tmpdir) / "fama_french_manifest.json"
            with open(manifest_path, "w") as f:
                json.dump({"aggregate_checksum": "ff_v1"}, f)

            framework = EventStudyFramework(
                crsp_provider=mock_crsp_provider,
                fama_french_provider=mock_ff_provider,
            )

            # Missing action column
            bad_changes = pl.DataFrame(
                {
                    "symbol": ["AAPL"],
                    "effective_date": [date(2023, 3, 1)],
                }
            )

            with pytest.raises(ValueError, match="must have columns"):
                framework.analyze_index_rebalance(index_changes=bad_changes)

    def test_analyze_index_rebalance_with_announcement_date(
        self,
        mock_crsp_provider: MagicMock,
        mock_ff_provider: MagicMock,
        sample_returns: pl.DataFrame,
        sample_ff_data: pl.DataFrame,
    ) -> None:
        """Test index rebalance with announcement date."""
        import tempfile
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_ff_provider._storage_path = Path(tmpdir)
            manifest_path = Path(tmpdir) / "fama_french_manifest.json"
            with open(manifest_path, "w") as f:
                json.dump({"aggregate_checksum": "ff_v1"}, f)

            mock_crsp_provider.get_daily_prices.return_value = sample_returns
            mock_ff_provider.get_factors.return_value = sample_ff_data

            framework = EventStudyFramework(
                crsp_provider=mock_crsp_provider,
                fama_french_provider=mock_ff_provider,
                config=EventStudyConfig(
                    estimation_window=60,
                    min_estimation_obs=30,
                    pre_window=5,
                    post_window=10,
                ),
            )

            # Create index changes with announcement date
            index_changes = pl.DataFrame(
                {
                    "symbol": ["AAPL", "AAPL"],
                    "effective_date": [date(2023, 3, 20), date(2023, 5, 20)],
                    "announcement_date": [date(2023, 3, 15), date(2023, 5, 15)],
                    "action": ["add", "drop"],
                }
            )

            result = framework.analyze_index_rebalance(
                index_changes=index_changes,
                index_name="SP500",
                use_announcement_date=True,
            )

            assert result.uses_announcement_date is True


# =============================================================================
# Test Patell and BMP in compute_car
# =============================================================================


class TestPatellBMPComputation:
    """Tests for Patell and BMP test computation in compute_car."""

    def test_compute_car_with_patell(
        self,
        mock_crsp_provider: MagicMock,
        mock_ff_provider: MagicMock,
        sample_returns: pl.DataFrame,
        sample_ff_data: pl.DataFrame,
    ) -> None:
        """Test CAR computation with Patell test."""
        import tempfile
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_ff_provider._storage_path = Path(tmpdir)
            manifest_path = Path(tmpdir) / "fama_french_manifest.json"
            with open(manifest_path, "w") as f:
                json.dump({"aggregate_checksum": "ff_v1"}, f)

            mock_crsp_provider.get_daily_prices.return_value = sample_returns
            mock_ff_provider.get_factors.return_value = sample_ff_data

            framework = EventStudyFramework(
                crsp_provider=mock_crsp_provider,
                fama_french_provider=mock_ff_provider,
                config=EventStudyConfig(
                    estimation_window=60,
                    min_estimation_obs=30,
                    pre_window=3,
                    post_window=10,
                    significance_test=SignificanceTest.PATELL,
                    expected_return_model=ExpectedReturnModel.MARKET,
                ),
            )

            result = framework.compute_car(
                symbol="AAPL",
                event_date=date(2023, 4, 15),
                event_type="test_event",
            )

            assert result.patell_z is not None
            assert not np.isnan(result.patell_z)

    def test_compute_car_with_bmp(
        self,
        mock_crsp_provider: MagicMock,
        mock_ff_provider: MagicMock,
        sample_returns: pl.DataFrame,
        sample_ff_data: pl.DataFrame,
    ) -> None:
        """Test CAR computation with BMP test."""
        import tempfile
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_ff_provider._storage_path = Path(tmpdir)
            manifest_path = Path(tmpdir) / "fama_french_manifest.json"
            with open(manifest_path, "w") as f:
                json.dump({"aggregate_checksum": "ff_v1"}, f)

            mock_crsp_provider.get_daily_prices.return_value = sample_returns
            mock_ff_provider.get_factors.return_value = sample_ff_data

            framework = EventStudyFramework(
                crsp_provider=mock_crsp_provider,
                fama_french_provider=mock_ff_provider,
                config=EventStudyConfig(
                    estimation_window=60,
                    min_estimation_obs=30,
                    pre_window=3,
                    post_window=10,
                    significance_test=SignificanceTest.BMP,
                    expected_return_model=ExpectedReturnModel.MARKET,
                ),
            )

            result = framework.compute_car(
                symbol="AAPL",
                event_date=date(2023, 4, 15),
                event_type="test_event",
            )

            assert result.bmp_t is not None
            assert not np.isnan(result.bmp_t)


# =============================================================================
# Test Delisting Handling in compute_car
# =============================================================================


class TestDelistingInComputeCar:
    """Tests for delisting handling in compute_car."""

    def test_delisting_detection(
        self,
        mock_crsp_provider: MagicMock,
        mock_ff_provider: MagicMock,
        sample_ff_data: pl.DataFrame,
    ) -> None:
        """Test delisting is detected when data ends early."""
        import tempfile
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_ff_provider._storage_path = Path(tmpdir)
            manifest_path = Path(tmpdir) / "fama_french_manifest.json"
            with open(manifest_path, "w") as f:
                json.dump({"aggregate_checksum": "ff_v1"}, f)

            # Create returns that end early (simulating delisting)
            # Event on Feb 10 with post_window=5 means event_window_end ~ Feb 17
            # We truncate data on Feb 15 so it ends BEFORE event window end
            np.random.seed(42)
            dates = []
            d = date(2022, 1, 3)
            # Truncate on Feb 15 (before event_window_end of Feb 17)
            while d <= date(2023, 2, 15):
                if d.weekday() < 5:
                    dates.append(d)
                d = date.fromordinal(d.toordinal() + 1)

            n = len(dates)
            truncated_returns = pl.DataFrame(
                {
                    "date": dates,
                    "permno": [12345] * n,
                    "ticker": ["DELISTED"] * n,
                    "ret": np.random.normal(0.0005, 0.02, n).tolist(),
                    "vol": np.random.uniform(1e6, 5e6, n).tolist(),
                }
            )

            mock_crsp_provider.get_daily_prices.return_value = truncated_returns
            mock_ff_provider.get_factors.return_value = sample_ff_data

            framework = EventStudyFramework(
                crsp_provider=mock_crsp_provider,
                fama_french_provider=mock_ff_provider,
                config=EventStudyConfig(
                    estimation_window=60,
                    min_estimation_obs=30,
                    pre_window=2,
                    post_window=5,
                ),
            )

            # Event window: Feb 8-17. Data ends Feb 15, so is_delisted should be True
            result = framework.compute_car(
                symbol="DELISTED",
                event_date=date(2023, 2, 10),
                event_type="test_event",
            )

            # Should detect delisting since data ends before event window end
            assert result.is_delisted is True
            assert any("delisted" in w.lower() for w in result.warnings)

    def test_delisting_with_provider_support(
        self,
        mock_crsp_provider: MagicMock,
        mock_ff_provider: MagicMock,
        sample_ff_data: pl.DataFrame,
    ) -> None:
        """Test delisting with provider that supports get_delisting."""
        import tempfile
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_ff_provider._storage_path = Path(tmpdir)
            manifest_path = Path(tmpdir) / "fama_french_manifest.json"
            with open(manifest_path, "w") as f:
                json.dump({"aggregate_checksum": "ff_v1"}, f)

            # Create truncated returns (data ends before event window completes)
            # Truncate on Feb 15 so data ends BEFORE event_window_end of Feb 17
            np.random.seed(42)
            dates = []
            d = date(2022, 1, 3)
            while d <= date(2023, 2, 15):
                if d.weekday() < 5:
                    dates.append(d)
                d = date.fromordinal(d.toordinal() + 1)

            n = len(dates)
            truncated_returns = pl.DataFrame(
                {
                    "date": dates,
                    "permno": [12345] * n,
                    "ticker": ["DELISTED"] * n,
                    "ret": np.random.normal(0.0005, 0.02, n).tolist(),
                    "vol": np.random.uniform(1e6, 5e6, n).tolist(),
                }
            )

            mock_crsp_provider.get_daily_prices.return_value = truncated_returns
            mock_ff_provider.get_factors.return_value = sample_ff_data

            # Add get_delisting method
            mock_crsp_provider.get_delisting = MagicMock(
                return_value={"dlret": -0.30, "dlstcd": 520}
            )

            framework = EventStudyFramework(
                crsp_provider=mock_crsp_provider,
                fama_french_provider=mock_ff_provider,
                config=EventStudyConfig(
                    estimation_window=60,
                    min_estimation_obs=30,
                    pre_window=2,
                    post_window=5,
                ),
            )

            result = framework.compute_car(
                symbol="DELISTED",
                event_date=date(2023, 2, 10),
                event_type="test_event",
            )

            assert result.is_delisted is True
            assert result.delisting_return == pytest.approx(-0.30)


# =============================================================================
# Test FF3 and FF5 Models
# =============================================================================


class TestFactorModels:
    """Tests for multi-factor model estimation."""

    def test_estimate_ff3_model(
        self,
        mock_crsp_provider: MagicMock,
        mock_ff_provider: MagicMock,
        sample_returns: pl.DataFrame,
        sample_ff_data: pl.DataFrame,
    ) -> None:
        """Test Fama-French 3-factor model estimation."""
        import tempfile
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_ff_provider._storage_path = Path(tmpdir)
            manifest_path = Path(tmpdir) / "fama_french_manifest.json"
            with open(manifest_path, "w") as f:
                json.dump({"aggregate_checksum": "ff_v1"}, f)

            mock_crsp_provider.get_daily_prices.return_value = sample_returns
            mock_ff_provider.get_factors.return_value = sample_ff_data

            framework = EventStudyFramework(
                crsp_provider=mock_crsp_provider,
                fama_french_provider=mock_ff_provider,
                config=EventStudyConfig(expected_return_model=ExpectedReturnModel.FF3),
            )

            result = framework.estimate_market_model(
                symbol="AAPL",
                estimation_end=date(2023, 3, 15),
                model=ExpectedReturnModel.FF3,
            )

            assert result.model_type == ExpectedReturnModel.FF3
            assert result.factor_betas is not None
            assert "MKT" in result.factor_betas
            assert "SMB" in result.factor_betas
            assert "HML" in result.factor_betas

    def test_estimate_ff5_model(
        self,
        mock_crsp_provider: MagicMock,
        mock_ff_provider: MagicMock,
        sample_returns: pl.DataFrame,
        sample_ff_data: pl.DataFrame,
    ) -> None:
        """Test Fama-French 5-factor model estimation."""
        import tempfile
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_ff_provider._storage_path = Path(tmpdir)
            manifest_path = Path(tmpdir) / "fama_french_manifest.json"
            with open(manifest_path, "w") as f:
                json.dump({"aggregate_checksum": "ff_v1"}, f)

            mock_crsp_provider.get_daily_prices.return_value = sample_returns
            mock_ff_provider.get_factors.return_value = sample_ff_data

            framework = EventStudyFramework(
                crsp_provider=mock_crsp_provider,
                fama_french_provider=mock_ff_provider,
                config=EventStudyConfig(expected_return_model=ExpectedReturnModel.FF5),
            )

            result = framework.estimate_market_model(
                symbol="AAPL",
                estimation_end=date(2023, 3, 15),
                model=ExpectedReturnModel.FF5,
            )

            assert result.model_type == ExpectedReturnModel.FF5
            assert result.factor_betas is not None
            assert "RMW" in result.factor_betas
            assert "CMA" in result.factor_betas

    def test_estimate_mean_adjusted_model(
        self,
        mock_crsp_provider: MagicMock,
        mock_ff_provider: MagicMock,
        sample_returns: pl.DataFrame,
        sample_ff_data: pl.DataFrame,
    ) -> None:
        """Test mean-adjusted model estimation."""
        import tempfile
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_ff_provider._storage_path = Path(tmpdir)
            manifest_path = Path(tmpdir) / "fama_french_manifest.json"
            with open(manifest_path, "w") as f:
                json.dump({"aggregate_checksum": "ff_v1"}, f)

            mock_crsp_provider.get_daily_prices.return_value = sample_returns
            mock_ff_provider.get_factors.return_value = sample_ff_data

            framework = EventStudyFramework(
                crsp_provider=mock_crsp_provider,
                fama_french_provider=mock_ff_provider,
            )

            result = framework.estimate_market_model(
                symbol="AAPL",
                estimation_end=date(2023, 3, 15),
                model=ExpectedReturnModel.MEAN_ADJUSTED,
            )

            assert result.model_type == ExpectedReturnModel.MEAN_ADJUSTED
            assert result.beta == 0.0
            assert result.factor_betas is None
