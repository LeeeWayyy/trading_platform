"""
Centralized Redis Key Format Definitions.

This module provides a single source of truth for all Redis key formats used
across the trading platform. Centralizing key formats improves maintainability
and prevents typos or format inconsistencies.

Usage:
    from libs.redis_client.keys import RedisKeys

    # Price cache key
    key = RedisKeys.price(symbol="AAPL")
    # Returns: "price:AAPL"

    # Feature cache key
    key = RedisKeys.feature(symbol="MSFT", date="2025-01-17")
    # Returns: "feature:MSFT:2025-01-17"

Design Principles:
    - All key formats defined in one place
    - Static methods for easy discovery (IDE autocomplete)
    - Type hints for safety
    - Clear naming convention: entity_type:identifier:subtype

See Also:
    - docs/CONCEPTS/redis-patterns.md for Redis patterns and best practices
"""


class RedisKeys:
    """
    Centralized Redis key format definitions.

    All Redis keys in the trading platform should be generated using these
    static methods to ensure consistency and maintainability.
    """

    @staticmethod
    def price(symbol: str) -> str:
        """
        Generate Redis key for real-time price data.

        Format: "price:{symbol}"

        Args:
            symbol: Stock symbol (e.g., "AAPL", "MSFT")

        Returns:
            Redis key string for price cache

        Examples:
            >>> RedisKeys.price("AAPL")
            'price:AAPL'

            >>> RedisKeys.price("MSFT")
            'price:MSFT'

        Used By:
            - Market Data Service (writes real-time prices)
            - Execution Gateway (reads for real-time P&L)
        """
        return f"price:{symbol}"

    @staticmethod
    def feature(symbol: str, date: str) -> str:
        """
        Generate Redis key for feature cache.

        Format: "feature:{symbol}:{date}"

        Args:
            symbol: Stock symbol (e.g., "AAPL", "MSFT")
            date: Date string in ISO format (e.g., "2025-01-17")

        Returns:
            Redis key string for feature cache

        Examples:
            >>> RedisKeys.feature("AAPL", "2025-01-17")
            'feature:AAPL:2025-01-17'

        Used By:
            - Signal Service (caches Alpha158 features)
        """
        return f"feature:{symbol}:{date}"

    @staticmethod
    def circuit_breaker(breaker_id: str) -> str:
        """
        Generate Redis key for circuit breaker state.

        Format: "cb:{breaker_id}"

        Args:
            breaker_id: Circuit breaker identifier (e.g., "global", "AAPL")

        Returns:
            Redis key string for circuit breaker state

        Examples:
            >>> RedisKeys.circuit_breaker("global")
            'cb:global'

        Used By:
            - Risk Manager (sets breaker state)
            - Execution Gateway (checks before orders)
        """
        return f"cb:{breaker_id}"

    @staticmethod
    def model_version(strategy_name: str) -> str:
        """
        Generate Redis key for model version tracking.

        Format: "model:version:{strategy_name}"

        Args:
            strategy_name: Strategy name (e.g., "alpha_baseline")

        Returns:
            Redis key string for model version

        Examples:
            >>> RedisKeys.model_version("alpha_baseline")
            'model:version:alpha_baseline'

        Used By:
            - Signal Service (hot reload mechanism)
        """
        return f"model:version:{strategy_name}"


__all__ = ["RedisKeys"]
