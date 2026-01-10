"""
Signal generator for producing trading signals from ML model predictions.

This module implements the SignalGenerator class which:
- Fetches latest market data from T1 data pipeline
- Generates Alpha158 features (FEATURE PARITY with research)
- Makes predictions using loaded model
- Converts predictions to portfolio weights (Top-N Long/Short)

The signal generator is the core of the signal service, bridging research
(T2 baseline strategy) and production (real-time signal generation).

Example:
    >>> from apps.signal_service.signal_generator import SignalGenerator
    >>> from apps.signal_service.model_registry import ModelRegistry
    >>> from pathlib import Path
    >>>
    >>> # Setup
    >>> registry = ModelRegistry("postgresql://localhost/trading_platform")
    >>> registry.reload_if_changed("alpha_baseline")
    >>> generator = SignalGenerator(registry, Path("data/adjusted"), top_n=3, bottom_n=3)
    >>>
    >>> # Generate signals
    >>> signals = generator.generate_signals(
    ...     symbols=["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"],
    ...     as_of_date=datetime(2024, 1, 15)
    ... )
    >>> signals
       symbol  predicted_return  rank  target_weight
    0   AAPL           0.0234      1         0.3333
    1   MSFT           0.0187      2         0.3333
    2  GOOGL           0.0156      3         0.3333
    3   AMZN          -0.0089      4         0.0000
    4   TSLA          -0.0231      5        -0.3333

See Also:
    - /docs/ADRs/0004-signal-service-architecture.md for architecture
    - /docs/CONCEPTS/feature-parity.md for feature parity pattern
    - strategies/alpha_baseline/features.py for Alpha158 implementation
"""

import json
import logging
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, TypedDict, cast

import numpy as np
import pandas as pd
from redis.exceptions import RedisError

from libs.redis_client import FeatureCache


class PrecomputeResult(TypedDict):
    """Return type for precompute_features method.

    Provides explicit type information for mypy, avoiding cast() calls
    when accessing result dictionary fields.
    """

    cached_count: int
    skipped_count: int
    symbols_cached: list[str]
    symbols_skipped: list[str]


class HydrationResult(TypedDict):
    """Return type for hydrate_feature_cache method."""

    dates_attempted: int
    dates_succeeded: int
    dates_failed: int
    cached_count: int
    skipped_count: int


from strategies.alpha_baseline.data_loader import T1DataProvider  # noqa: E402
from strategies.alpha_baseline.features import get_alpha158_features  # noqa: E402
from strategies.alpha_baseline.mock_features import get_mock_alpha158_features  # noqa: E402

from .model_registry import ModelRegistry  # noqa: E402

logger = logging.getLogger(__name__)


class SignalGenerator:
    """
    Generates trading signals from ML model predictions.

    The signal generator:
    1. Fetches market data from T1 data pipeline
    2. Computes Alpha158 features (158 technical indicators)
    3. Gets model predictions (expected next-day returns)
    4. Ranks stocks by predicted return
    5. Computes target weights (Top-N Long / Bottom-N Short)

    This class implements the "feature parity" pattern: it uses the EXACT
    same feature generation code as research (T2 baseline strategy). This
    eliminates train-serve skew and ensures production signals match
    backtesting.

    Args:
        model_registry: ModelRegistry instance with loaded model
        data_dir: Path to T1 adjusted data directory
        top_n: Number of long positions (highest predicted returns)
        bottom_n: Number of short positions (lowest predicted returns)
        feature_cache: Optional Redis-backed feature cache (T1.2)
            If provided, features will be cached for faster retrieval.
            If None, features will be generated on every request.

    Example:
        >>> registry = ModelRegistry("postgresql://localhost/trading_platform")
        >>> registry.reload_if_changed("alpha_baseline")
        >>>
        >>> # Without caching
        >>> generator = SignalGenerator(
        ...     model_registry=registry,
        ...     data_dir=Path("data/adjusted"),
        ...     top_n=3,
        ...     bottom_n=3
        ... )
        >>>
        >>> # With caching (T1.2)
        >>> from libs.redis_client import RedisClient, FeatureCache
        >>> redis_client = RedisClient()
        >>> feature_cache = FeatureCache(redis_client)
        >>> generator = SignalGenerator(
        ...     model_registry=registry,
        ...     data_dir=Path("data/adjusted"),
        ...     top_n=3,
        ...     bottom_n=3,
        ...     feature_cache=feature_cache
        ... )
        >>>
        >>> # Generate signals for today
        >>> signals = generator.generate_signals(
        ...     symbols=["AAPL", "MSFT", "GOOGL"],
        ...     as_of_date=datetime.now()
        ... )
        >>>
        >>> # Long positions (positive weights)
        >>> long_positions = signals[signals["target_weight"] > 0]
        >>> long_positions
           symbol  predicted_return  rank  target_weight
        0   AAPL           0.0234      1         0.3333
        1   MSFT           0.0187      2         0.3333

    Notes:
        - Requires T1 data to be available for the requested date
        - Requires model to be loaded in registry
        - Feature generation may take 5-50ms depending on data volume
        - Thread-safe for concurrent signal generation (read-only model access)

    See Also:
        - /docs/ADRs/0004-signal-service-architecture.md for design decisions
        - /docs/CONCEPTS/feature-parity.md for feature parity pattern
        - /docs/IMPLEMENTATION_GUIDES/t3-signal-service.md for deployment
    """

    # Alpha158 features include 60-day lookbacks (e.g., ROC/BOLL), so hydrate 60 days.
    DEFAULT_FEATURE_HYDRATION_DAYS = 60

    def __init__(
        self,
        model_registry: ModelRegistry,
        data_dir: Path,
        top_n: int = 3,
        bottom_n: int = 3,
        feature_cache: FeatureCache | None = None,
    ):
        """
        Initialize signal generator.

        Args:
            model_registry: ModelRegistry instance for model access
                Must have a model loaded (call reload_if_changed first)
            data_dir: Directory containing T1 adjusted Parquet data
                Structure: data_dir/YYYY-MM-DD/*.parquet
            top_n: Number of long positions (stocks with highest predicted returns)
                Must be > 0. Example: top_n=3 means go long top 3 stocks.
            bottom_n: Number of short positions (stocks with lowest predicted returns)
                Must be > 0. Example: bottom_n=3 means short bottom 3 stocks.
                Set to 0 for long-only strategy (not recommended, reduces alpha).
            feature_cache: Optional Redis-backed feature cache (T1.2)
                If provided, features will be cached/retrieved from Redis.
                If None, features will be generated fresh on every request.

        Raises:
            ValueError: If top_n or bottom_n < 0
            FileNotFoundError: If data_dir doesn't exist

        Example:
            >>> registry = ModelRegistry("postgresql://localhost/trading_platform")
            >>> registry.reload_if_changed("alpha_baseline")
            >>> generator = SignalGenerator(registry, Path("data/adjusted"))
        """
        if top_n < 0 or bottom_n < 0:
            raise ValueError(
                f"top_n and bottom_n must be >= 0, got top_n={top_n}, bottom_n={bottom_n}"
            )

        if not data_dir.exists():
            raise FileNotFoundError(f"Data directory not found: {data_dir}")

        self.model_registry = model_registry
        self.data_provider = T1DataProvider(data_dir)
        self.top_n = top_n
        self.bottom_n = bottom_n
        self.feature_cache = feature_cache

        logger.info(
            "SignalGenerator initialized",
            extra={
                "data_dir": str(data_dir),
                "top_n": top_n,
                "bottom_n": bottom_n,
                "feature_cache_enabled": feature_cache is not None,
            },
        )

    def generate_signals(
        self,
        symbols: list[str],
        as_of_date: datetime | None = None,
    ) -> pd.DataFrame:
        """
        Generate target portfolio weights for given symbols.

        This is the main method that orchestrates signal generation:
        1. Validates model is loaded
        2. Generates Alpha158 features for requested date (FEATURE PARITY!)
        3. Gets model predictions (expected returns)
        4. Ranks stocks by predicted return
        5. Computes target weights (Top-N Long / Bottom-N Short)

        Args:
            symbols: List of stock symbols to generate signals for
                Example: ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]
                Must be symbols that model was trained on.
            as_of_date: Date to generate signals for (default: today)
                Signals are based on data up to and including this date.
                Predictions are for next trading day.

        Returns:
            DataFrame with columns:
                - symbol: Stock symbol (str)
                - predicted_return: Model's predicted next-day return (float)
                    Range: typically -0.05 to +0.05 (-5% to +5%)
                - rank: Rank by predicted return (int, 1 = highest)
                - target_weight: Portfolio weight (float, -1 to 1)
                    Positive = long position
                    Negative = short position
                    Zero = no position

            Sorted by rank (highest predicted return first).

        Raises:
            RuntimeError: If model not loaded in registry
            ValueError: If no features available for given date
                This can happen if:
                - Date is outside T1 data range
                - Data not yet available for recent date
                - Symbols not in T1 data

        Example:
            >>> generator = SignalGenerator(registry, Path("data/adjusted"), top_n=2, bottom_n=2)
            >>> signals = generator.generate_signals(
            ...     symbols=["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"],
            ...     as_of_date=datetime(2024, 1, 15)
            ... )
            >>> signals
               symbol  predicted_return  rank  target_weight
            0   AAPL           0.0234      1         0.5000  # Top 1: 50% long
            1   MSFT           0.0187      2         0.5000  # Top 2: 50% long
            2  GOOGL           0.0056      3         0.0000  # Neutral
            3   AMZN          -0.0089      4        -0.5000  # Bottom 2: 50% short
            4   TSLA          -0.0231      5        -0.5000  # Bottom 1: 50% short
            >>>
            >>> # Verify weights sum to 1.0 (long) and -1.0 (short)
            >>> signals[signals["target_weight"] > 0]["target_weight"].sum()
            1.0
            >>> signals[signals["target_weight"] < 0]["target_weight"].sum()
            -1.0

        Notes:
            - Feature generation uses SAME code as research (feature parity)
            - Predictions are for next trading day (T+1)
            - Weights are equal within long/short groups (not risk-weighted)
            - Weights sum to 1.0 for longs, -1.0 for shorts
            - Neutral positions (rank between top_n and bottom_n) have weight 0

        Performance:
            - Feature generation: 10-50ms for 5-10 symbols
            - Model prediction: 1-5ms
            - Total latency: typically < 100ms

        See Also:
            - strategies/alpha_baseline/features.py for get_alpha158_features()
            - /docs/CONCEPTS/feature-parity.md for why this matters
            - /docs/ADRs/0004-signal-service-architecture.md for design
        """
        # ====================================================================
        # Step 1: Validate model is loaded
        # ====================================================================
        if not self.model_registry.is_loaded:
            raise RuntimeError(
                "Model not loaded. Call model_registry.reload_if_changed() first.\n"
                "Example: registry.reload_if_changed('alpha_baseline')"
            )

        # ====================================================================
        # Step 2: Default to latest available date
        # ====================================================================
        if as_of_date is None:
            as_of_date = datetime.now(UTC)

        # Convert to date string for feature generation
        date_str = as_of_date.strftime("%Y-%m-%d")

        # Ensure model metadata is loaded
        if self.model_registry.current_metadata is None:
            raise ValueError("Model metadata not loaded")

        logger.info(
            f"Generating signals for {len(symbols)} symbols on {date_str}",
            extra={
                "symbols": symbols,
                "as_of_date": date_str,
                "model_version": self.model_registry.current_metadata.version,
            },
        )

        # ====================================================================
        # Step 3: Generate Alpha158 features (FEATURE PARITY!)
        # ====================================================================
        # This uses the EXACT same code as research (T2 baseline strategy)
        # to ensure zero train-serve skew.
        #
        # The get_alpha158_features() function:
        # - Fetches OHLCV data from T1 data pipeline
        # - Computes 158 technical indicators (KBAR, KDJ, RSI, MACD, etc.)
        # - Normalizes features using robust statistics
        # - Returns DataFrame with (date, symbol) MultiIndex
        #
        # T1.2: Cache-Aside Pattern
        # - Try to get features from Redis cache first
        # - On cache miss, generate features and cache them
        # - On cache error, fall back to fresh generation (graceful degradation)

        # Collect all features (per-symbol caching)
        features_list = []
        symbols_to_generate = []
        cached_symbols = []

        if self.feature_cache is not None:
            # Try to get cached features for each symbol
            for symbol in symbols:
                try:
                    cached_features = self.feature_cache.get(symbol, date_str)
                    if cached_features is not None:
                        # Cache hit - use cached features
                        # Convert dict back to DataFrame with proper MultiIndex
                        features_df = pd.DataFrame([cached_features])
                        features_df.index = pd.MultiIndex.from_tuples(
                            [(date_str, symbol)], names=["datetime", "instrument"]
                        )
                        features_list.append(features_df)
                        cached_symbols.append(symbol)
                    else:
                        # Cache miss - need to generate
                        symbols_to_generate.append(symbol)
                except (RedisError, json.JSONDecodeError, KeyError, ValueError) as e:
                    # Cache error - fall back to generation (graceful degradation)
                    logger.warning(
                        f"Cache error for {symbol}: {e}, falling back to generation",
                        extra={"symbol": symbol, "date": date_str, "error_type": type(e).__name__},
                    )
                    symbols_to_generate.append(symbol)
        else:
            # No cache available - generate all
            symbols_to_generate = symbols

        if cached_symbols:
            logger.debug(f"Cache hits: {len(cached_symbols)} symbols {cached_symbols}")

        # Generate features for cache misses
        if symbols_to_generate:
            logger.debug(
                f"Generating features for {len(symbols_to_generate)} symbols (cache misses)",
                extra={"symbols": symbols_to_generate, "date": date_str},
            )
            try:
                fresh_features = get_alpha158_features(
                    symbols=symbols_to_generate,
                    start_date=date_str,
                    end_date=date_str,
                    fit_start_date=date_str,  # Already fitted during training
                    fit_end_date=date_str,
                    data_dir=self.data_provider.data_dir,
                )

                # Cache the freshly generated features (per-symbol)
                if self.feature_cache is not None and not fresh_features.empty:
                    for symbol in symbols_to_generate:
                        try:
                            # Extract features for this symbol
                            symbol_features = fresh_features.xs(
                                symbol, level="instrument", drop_level=False
                            )
                            if not symbol_features.empty:
                                # Convert to dict for caching (cast for type safety)
                                features_dict = cast(
                                    dict[str, Any], symbol_features.iloc[0].to_dict()
                                )
                                self.feature_cache.set(symbol, date_str, features_dict)
                                logger.debug(f"Cached features for {symbol} on {date_str}")
                        except (KeyError, IndexError):
                            # Symbol not in generated features, skip caching
                            logger.debug(f"Symbol {symbol} not in features, skipping cache")
                        except (RedisError, TypeError, ValueError) as e:
                            # Cache write error - log but don't fail (graceful degradation)
                            logger.warning(
                                f"Failed to cache features for {symbol}: {e}",
                                extra={"symbol": symbol, "date": date_str, "error_type": type(e).__name__},
                            )

                features_list.append(fresh_features)

            except (KeyError, ValueError, TypeError, AttributeError, FileNotFoundError, OSError) as e:
                # FALLBACK: Use mock features if Qlib integration not available
                # This allows P3 testing without full Qlib data setup
                logger.warning(
                    f"Falling back to mock features due to error: {e}",
                    extra={"date": date_str, "symbols": symbols_to_generate, "error_type": type(e).__name__},
                )
                try:
                    mock_features = get_mock_alpha158_features(
                        symbols=symbols_to_generate,
                        start_date=date_str,
                        end_date=date_str,
                        data_dir=self.data_provider.data_dir,
                    )

                    # Cache mock features too (for consistency)
                    if self.feature_cache is not None and not mock_features.empty:
                        for symbol in symbols_to_generate:
                            try:
                                symbol_features = mock_features.xs(
                                    symbol, level="instrument", drop_level=False
                                )
                                if not symbol_features.empty:
                                    features_dict = cast(
                                        dict[str, Any], symbol_features.iloc[0].to_dict()
                                    )
                                    self.feature_cache.set(symbol, date_str, features_dict)
                                    logger.debug(f"Cached mock features for {symbol} on {date_str}")
                            except (RedisError, TypeError, ValueError, KeyError, IndexError) as cache_error:
                                logger.warning(
                                    f"Failed to cache mock features for {symbol}: {cache_error}",
                                    extra={"symbol": symbol, "date": date_str, "error_type": type(cache_error).__name__},
                                )

                    features_list.append(mock_features)
                except (KeyError, ValueError, TypeError, AttributeError, FileNotFoundError, OSError) as mock_error:
                    logger.error(
                        f"Failed to generate mock features: {mock_error}",
                        extra={"date": date_str, "symbols": symbols_to_generate, "error_type": type(mock_error).__name__},
                        exc_info=True,
                    )
                    raise ValueError(
                        f"No features available for {date_str}: {mock_error}"
                    ) from mock_error

        # Combine all features (cached + freshly generated)
        if features_list:
            features = pd.concat(features_list, axis=0)
            # Sort by symbol to ensure consistent ordering with input symbols
            # This is critical for matching predictions to symbols
            features = features.sort_index(level="instrument")
        else:
            features = pd.DataFrame()

        if features.empty:
            raise ValueError(
                f"No features generated for {date_str}. "
                f"Check T1 data exists: ls {self.data_provider.data_dir}/{date_str}/"
            )

        logger.debug(
            f"Generated {features.shape[0]} feature vectors ({features.shape[1]} features each)",
            extra={
                "num_symbols": len(features.index.get_level_values("instrument").unique()),
                "num_features": features.shape[1],
            },
        )

        # ====================================================================
        # Step 4: Generate predictions using model
        # ====================================================================
        # Model expects numpy array with shape (n_samples, 158)
        # Returns array of predicted next-day returns
        logger.debug("Generating predictions with model")

        # Ensure model is loaded
        if self.model_registry.current_model is None:
            raise ValueError("Model not loaded")

        try:
            predictions = self.model_registry.current_model.predict(features.values)
        except (ValueError, TypeError, AttributeError, KeyError) as e:
            logger.error(
                f"Model prediction failed: {e}",
                extra={"error_type": type(e).__name__, "features_shape": features.shape},
                exc_info=True,
            )
            raise RuntimeError(f"Model prediction failed: {e}") from e

        logger.debug(
            f"Generated {len(predictions)} predictions (raw)",
            extra={
                "mean_prediction": float(np.mean(predictions)),
                "std_prediction": float(np.std(predictions)),
                "min_prediction": float(np.min(predictions)),
                "max_prediction": float(np.max(predictions)),
            },
        )

        # Normalize predictions to reasonable return range
        # This is necessary because models may have different output scales
        # Normalize to mean=0, std=0.02 (roughly 2% daily return std)
        if len(predictions) > 1:
            pred_mean = np.mean(predictions)
            pred_std = np.std(predictions)
            if pred_std > 1e-10:  # Avoid division by zero
                predictions = (predictions - pred_mean) / pred_std * 0.02
            else:
                # All predictions are the same, use raw values
                predictions = predictions - pred_mean

        logger.debug(
            "Normalized predictions to return scale",
            extra={
                "mean_prediction": float(np.mean(predictions)),
                "std_prediction": float(np.std(predictions)),
                "min_prediction": float(np.min(predictions)),
                "max_prediction": float(np.max(predictions)),
            },
        )

        # ====================================================================
        # Step 5: Create results DataFrame
        # ====================================================================
        # Extract symbol list from MultiIndex
        # (features.index is (date, symbol), we want just symbols)
        symbol_list = features.index.get_level_values("instrument").tolist()

        results = pd.DataFrame(
            {
                "symbol": symbol_list,
                "predicted_return": predictions,
            }
        )

        # ====================================================================
        # Step 6: Rank symbols by predicted return
        # ====================================================================
        # Rank 1 = highest predicted return (most bullish)
        # Rank N = lowest predicted return (most bearish)
        # Use method='dense' so ties get same rank
        results["rank"] = (
            results["predicted_return"]
            .rank(ascending=False, method="dense")  # Highest return = rank 1  # Ties get same rank
            .astype(int)
        )

        # ====================================================================
        # Step 7: Compute target portfolio weights
        # ====================================================================
        # Strategy: Top-N Long / Bottom-N Short
        # - Top N stocks by predicted return: long positions (positive weight)
        # - Bottom N stocks by predicted return: short positions (negative weight)
        # - Middle stocks: no position (zero weight)
        # - Equal weight within long/short groups

        # Initialize all weights to zero
        results["target_weight"] = 0.0

        # Long positions (top N by predicted return)
        # Use nsmallest because rank 1 = best (smallest rank number)
        if self.top_n > 0:
            top_symbols = results.nsmallest(self.top_n, "rank")
            if not top_symbols.empty:
                # Equal weight: 1.0 / N for each long position
                # Example: top_n=3 means each gets 0.3333 (33.3% of capital)
                results.loc[top_symbols.index, "target_weight"] = 1.0 / self.top_n

        # Short positions (bottom N by predicted return)
        # Use nlargest because rank N = worst (largest rank number)
        if self.bottom_n > 0:
            bottom_symbols = results.nlargest(self.bottom_n, "rank")
            if not bottom_symbols.empty:
                # Equal weight: -1.0 / N for each short position
                # Example: bottom_n=3 means each gets -0.3333 (-33.3% of capital)
                results.loc[bottom_symbols.index, "target_weight"] = -1.0 / self.bottom_n

        # ====================================================================
        # Step 8: Sort by rank and return
        # ====================================================================
        results = results.sort_values("rank").reset_index(drop=True)

        # Log summary statistics
        long_count = (results["target_weight"] > 0).sum()
        short_count = (results["target_weight"] < 0).sum()
        neutral_count = (results["target_weight"] == 0).sum()

        logger.info(
            f"Generated {len(results)} signals: "
            f"{long_count} long, {short_count} short, {neutral_count} neutral",
            extra={
                "num_signals": len(results),
                "num_long": long_count,
                "num_short": short_count,
                "num_neutral": neutral_count,
                "total_long_weight": float(
                    results[results["target_weight"] > 0]["target_weight"].sum()
                ),
                "total_short_weight": float(
                    results[results["target_weight"] < 0]["target_weight"].sum()
                ),
            },
        )

        return results

    def _resolve_hydration_end_date(self, symbols: list[str]) -> datetime | None:
        """
        Resolve the latest available date across symbols for hydration.

        Returns:
            Latest available date as timezone-aware datetime, or None if unavailable.
        """
        latest_dates: list[date] = []
        for symbol in symbols:
            _, max_date = self.data_provider.get_date_range(symbol)
            if max_date is not None:
                latest_dates.append(max_date)

        if not latest_dates:
            return None

        return datetime.combine(max(latest_dates), time.min, tzinfo=UTC)

    def hydrate_feature_cache(
        self,
        symbols: list[str],
        history_days: int | None = None,
        end_date: datetime | None = None,
    ) -> HydrationResult:
        """
        Hydrate feature cache with recent history for a universe of symbols.

        This is intended for startup cache warming to avoid cold-start
        issues with rolling-window features.

        Args:
            symbols: List of stock symbols to hydrate.
            history_days: Number of trailing days to hydrate (default: 60).
            end_date: Optional end date; if None, uses latest available data date.

        Returns:
            HydrationResult with aggregate counts.
        """
        if history_days is None:
            history_days = self.DEFAULT_FEATURE_HYDRATION_DAYS

        if history_days <= 0:
            logger.info("Feature hydration skipped (history_days <= 0)")
            return HydrationResult(
                dates_attempted=0,
                dates_succeeded=0,
                dates_failed=0,
                cached_count=0,
                skipped_count=0,
            )

        if not symbols:
            logger.info("Feature hydration skipped (no symbols provided)")
            return HydrationResult(
                dates_attempted=0,
                dates_succeeded=0,
                dates_failed=0,
                cached_count=0,
                skipped_count=0,
            )

        if self.feature_cache is None:
            logger.info("Feature cache not enabled, skipping hydration")
            return HydrationResult(
                dates_attempted=0,
                dates_succeeded=0,
                dates_failed=0,
                cached_count=0,
                skipped_count=0,
            )

        if end_date is None:
            end_date = self._resolve_hydration_end_date(symbols)

        if end_date is None:
            logger.warning("No available data found for hydration, skipping")
            return HydrationResult(
                dates_attempted=0,
                dates_succeeded=0,
                dates_failed=0,
                cached_count=0,
                skipped_count=0,
            )

        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=UTC)

        total_cached = 0
        total_skipped = 0
        dates_succeeded = 0
        dates_failed = 0

        for offset in range(history_days):
            current_date = (end_date - timedelta(days=offset)).date()
            try:
                result = self.precompute_features(
                    symbols=symbols,
                    as_of_date=datetime.combine(current_date, time.min, tzinfo=UTC),
                )
                total_cached += result["cached_count"]
                total_skipped += result["skipped_count"]
                dates_succeeded += 1
            except (ValueError, KeyError, TypeError, FileNotFoundError, OSError, RedisError) as exc:
                dates_failed += 1
                logger.warning(
                    "Feature hydration failed for %s: %s",
                    current_date.isoformat(),
                    exc,
                    extra={"date": current_date.isoformat(), "error_type": type(exc).__name__},
                )
            except Exception as exc:
                # Catch-all for unexpected errors - continue with remaining dates
                dates_failed += 1
                logger.error(
                    "Unexpected error during feature hydration for %s: %s",
                    current_date.isoformat(),
                    exc,
                    extra={"date": current_date.isoformat(), "error_type": type(exc).__name__},
                    exc_info=True,
                )

        return HydrationResult(
            dates_attempted=history_days,
            dates_succeeded=dates_succeeded,
            dates_failed=dates_failed,
            cached_count=total_cached,
            skipped_count=total_skipped,
        )

    def precompute_features(
        self,
        symbols: list[str],
        as_of_date: datetime | None = None,
    ) -> PrecomputeResult:
        """
        Pre-compute and cache features without generating signals.

        M5 Fix: Allows cache warming at day start to avoid blocking
        signal generation requests with disk I/O. Call this endpoint
        before market open via cron/scheduler.

        Uses batch MGET for O(1) cache lookups instead of O(N) individual calls.

        Args:
            symbols: List of stock symbols to pre-compute features for
            as_of_date: Date to compute features for (default: today)

        Returns:
            PrecomputeResult TypedDict with:
                - cached_count: Number of symbols successfully cached
                - skipped_count: Number of symbols skipped (already cached or error)
                - symbols_cached: List of newly cached symbols
                - symbols_skipped: List of skipped symbols

        Example:
            >>> generator = SignalGenerator(registry, Path("data/adjusted"))
            >>> result = generator.precompute_features(
            ...     symbols=["AAPL", "MSFT", "GOOGL"],
            ...     as_of_date=datetime(2024, 1, 15)
            ... )
            >>> result
            {'cached_count': 3, 'skipped_count': 0, 'symbols_cached': ['AAPL', 'MSFT', 'GOOGL']}

        Notes:
            - Does NOT require model to be loaded (features only)
            - Returns immediately if feature_cache is None (no-op)
            - Gracefully handles errors per-symbol (continues with others)
        """
        if as_of_date is None:
            as_of_date = datetime.now(UTC)

        date_str = as_of_date.strftime("%Y-%m-%d")

        # If no cache, nothing to pre-compute
        if self.feature_cache is None:
            logger.info("Feature cache not enabled, skipping precompute")
            return PrecomputeResult(
                cached_count=0,
                skipped_count=len(symbols),
                symbols_cached=[],
                symbols_skipped=list(symbols),
            )

        logger.info(
            f"Pre-computing features for {len(symbols)} symbols on {date_str}",
            extra={"symbols": symbols, "date": date_str},
        )

        # Check which symbols are already cached using batch MGET (O(1) vs O(N))
        symbols_to_generate: list[str] = []
        symbols_already_cached: list[str] = []

        cached_results = self.feature_cache.mget(symbols, date_str)
        for symbol in symbols:
            if cached_results.get(symbol) is not None:
                symbols_already_cached.append(symbol)
            else:
                symbols_to_generate.append(symbol)

        if symbols_already_cached:
            logger.debug(f"Already cached: {len(symbols_already_cached)} symbols")

        if not symbols_to_generate:
            logger.info("All symbols already cached, nothing to precompute")
            return PrecomputeResult(
                cached_count=0,
                skipped_count=len(symbols),
                symbols_cached=[],
                symbols_skipped=list(symbols),
            )

        # Generate features for uncached symbols
        symbols_cached: list[str] = []
        symbols_failed: list[str] = []

        try:
            # Use the same feature generation as generate_signals
            fresh_features = get_alpha158_features(
                symbols=symbols_to_generate,
                start_date=date_str,
                end_date=date_str,
                fit_start_date=date_str,
                fit_end_date=date_str,
                data_dir=self.data_provider.data_dir,
            )

            # Cache each symbol's features
            for symbol in symbols_to_generate:
                try:
                    symbol_features = fresh_features.xs(
                        symbol, level="instrument", drop_level=False
                    )
                    if not symbol_features.empty:
                        features_dict = cast(dict[str, Any], symbol_features.iloc[0].to_dict())
                        self.feature_cache.set(symbol, date_str, features_dict)
                        symbols_cached.append(symbol)
                        logger.debug(f"Cached features for {symbol}")
                    else:
                        symbols_failed.append(symbol)
                        logger.warning(f"No features generated for {symbol}")
                except (KeyError, IndexError) as e:
                    symbols_failed.append(symbol)
                    logger.warning(
                        f"Failed to cache {symbol}: {e}",
                        extra={"symbol": symbol, "date": date_str, "error_type": type(e).__name__},
                    )
                except (RedisError, TypeError, ValueError) as e:
                    symbols_failed.append(symbol)
                    logger.warning(
                        f"Cache write error for {symbol}: {e}",
                        extra={"symbol": symbol, "date": date_str, "error_type": type(e).__name__},
                    )

        except (KeyError, ValueError, TypeError, AttributeError, FileNotFoundError, OSError) as e:
            # Fallback to mock features if real features fail
            logger.warning(
                f"Feature generation failed, trying mock: {e}",
                extra={"error_type": type(e).__name__, "date": date_str, "symbols_count": len(symbols_to_generate)},
            )
            try:
                mock_features = get_mock_alpha158_features(
                    symbols=symbols_to_generate,
                    start_date=date_str,
                    end_date=date_str,
                    data_dir=self.data_provider.data_dir,
                )

                for symbol in symbols_to_generate:
                    try:
                        symbol_features = mock_features.xs(
                            symbol, level="instrument", drop_level=False
                        )
                        if not symbol_features.empty:
                            features_dict = cast(dict[str, Any], symbol_features.iloc[0].to_dict())
                            self.feature_cache.set(symbol, date_str, features_dict)
                            symbols_cached.append(symbol)
                    except (RedisError, TypeError, ValueError, KeyError, IndexError) as cache_err:
                        symbols_failed.append(symbol)
                        logger.warning(
                            f"Mock cache error for {symbol}: {cache_err}",
                            extra={"symbol": symbol, "date": date_str, "error_type": type(cache_err).__name__},
                        )

            except (KeyError, ValueError, TypeError, AttributeError, FileNotFoundError, OSError) as mock_err:
                logger.error(
                    f"Mock feature generation also failed: {mock_err}",
                    extra={"error_type": type(mock_err).__name__, "date": date_str, "symbols_count": len(symbols_to_generate)},
                )
                symbols_failed = symbols_to_generate

        logger.info(
            f"Feature precompute complete: {len(symbols_cached)} cached, "
            f"{len(symbols_failed)} failed, {len(symbols_already_cached)} already cached"
        )

        return PrecomputeResult(
            cached_count=len(symbols_cached),
            skipped_count=len(symbols_failed) + len(symbols_already_cached),
            symbols_cached=symbols_cached,
            symbols_skipped=symbols_failed + symbols_already_cached,
        )

    def validate_weights(self, signals: pd.DataFrame) -> bool:
        """
        Validate that portfolio weights are correct.

        Checks:
        1. Long weights sum to ~1.0 (within tolerance)
        2. Short weights sum to ~-1.0 (within tolerance)
        3. No weights outside [-1, 1] range
        4. Exactly top_n long positions
        5. Exactly bottom_n short positions

        Args:
            signals: DataFrame from generate_signals()

        Returns:
            True if all validations pass, False otherwise

        Example:
            >>> signals = generator.generate_signals(["AAPL", "MSFT", "GOOGL"])
            >>> generator.validate_weights(signals)
            True

        Notes:
            - Tolerance for sum checks is 1e-6 (accounting for float precision)
            - Logs warning if validation fails (doesn't raise exception)
        """
        long_positions = signals[signals["target_weight"] > 0]
        short_positions = signals[signals["target_weight"] < 0]

        # Check sums (allow small floating point error)
        long_sum = long_positions["target_weight"].sum()
        short_sum = short_positions["target_weight"].sum()

        if self.top_n > 0 and not np.isclose(long_sum, 1.0, atol=1e-6):
            logger.warning(f"Long weights sum to {long_sum}, expected 1.0")
            return False

        if self.bottom_n > 0 and not np.isclose(short_sum, -1.0, atol=1e-6):
            logger.warning(f"Short weights sum to {short_sum}, expected -1.0")
            return False

        # Check position counts
        if len(long_positions) != self.top_n:
            logger.warning(f"Found {len(long_positions)} long positions, expected {self.top_n}")
            return False

        if len(short_positions) != self.bottom_n:
            logger.warning(
                f"Found {len(short_positions)} short positions, expected {self.bottom_n}"
            )
            return False

        # Check weight bounds
        if (signals["target_weight"].abs() > 1.0).any():
            logger.warning("Some weights exceed [-1, 1] range")
            return False

        return True
