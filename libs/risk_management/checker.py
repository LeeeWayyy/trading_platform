"""
Pre-trade risk validation.

This module provides the RiskChecker class for validating orders before submission.
All risk limits are checked including circuit breaker state, position limits,
blacklist, and portfolio exposure.

Example:
    >>> from libs.risk_management import RiskConfig, CircuitBreaker, RiskChecker
    >>> from libs.risk_management.exceptions import RiskViolation
    >>>
    >>> config = RiskConfig()
    >>> breaker = CircuitBreaker(redis_client=redis)
    >>> checker = RiskChecker(config=config, breaker=breaker)
    >>>
    >>> # Validate order
    >>> is_valid, reason = checker.validate_order(
    ...     symbol="AAPL",
    ...     side="buy",
    ...     qty=100,
    ...     current_position=0
    ... )
    >>> if not is_valid:
    ...     raise RiskViolation(reason)

See Also:
    - docs/CONCEPTS/risk-management.md#pre-trade-checks
    - docs/ADRs/0011-risk-management-system.md#pre-trade-risk-checks
"""

import logging
from decimal import Decimal

from libs.risk_management.breaker import CircuitBreaker
from libs.risk_management.config import RiskConfig

logger = logging.getLogger(__name__)


class RiskChecker:
    """
    Pre-trade risk validation.

    Validates orders against all risk limits before submission to broker.

    Attributes:
        config: Risk configuration with all limits
        breaker: Circuit breaker instance

    Example:
        >>> checker = RiskChecker(config=config, breaker=breaker)
        >>> is_valid, reason = checker.validate_order(
        ...     symbol="AAPL",
        ...     side="buy",
        ...     qty=100,
        ...     current_position=0
        ... )
        >>> if not is_valid:
        ...     logger.warning(f"Order blocked: {reason}")

    Notes:
        - All checks are synchronous (<5ms total)
        - Returns (is_valid, reason) tuple (never raises)
        - Circuit breaker check is highest priority
        - Blacklist check is second priority
    """

    def __init__(self, config: RiskConfig, breaker: CircuitBreaker):
        """
        Initialize risk checker.

        Args:
            config: Risk configuration with all limits
            breaker: Circuit breaker instance

        Example:
            >>> from libs.redis_client import RedisClient
            >>> redis = RedisClient(host="localhost", port=6379)
            >>> config = RiskConfig()
            >>> breaker = CircuitBreaker(redis_client=redis)
            >>> checker = RiskChecker(config=config, breaker=breaker)
        """
        self.config = config
        self.breaker = breaker

    def validate_order(
        self,
        symbol: str,
        side: str,  # "buy" | "sell"
        qty: int,
        current_position: int = 0,
        current_price: Decimal | None = None,
        portfolio_value: Decimal | None = None,
    ) -> tuple[bool, str]:
        """
        Validate order against all risk limits.

        Checks performed (in order):
        1. Circuit breaker state (highest priority)
        2. Symbol blacklist
        3. Position size limit (shares)
        4. Position size limit (% of portfolio) - if portfolio_value provided
        5. Position direction (no reversals) - future enhancement

        Args:
            symbol: Stock symbol (e.g., "AAPL")
            side: Order side ("buy" or "sell")
            qty: Order quantity (positive integer)
            current_position: Current position in symbol (default: 0)
                Positive = long, negative = short, zero = flat
            current_price: Current market price (optional, for % limit check)
            portfolio_value: Total portfolio value (optional, for % limit check)

        Returns:
            (is_valid, reason):
                - (True, "") if order passes all checks
                - (False, "reason") if blocked

        Example:
            >>> # Simple check (position size only)
            >>> is_valid, reason = checker.validate_order("AAPL", "buy", 100, 0)
            >>> is_valid
            True
            >>>
            >>> # With price and portfolio value (% check)
            >>> is_valid, reason = checker.validate_order(
            ...     "AAPL",
            ...     "buy",
            ...     1000,
            ...     current_position=0,
            ...     current_price=Decimal("200.00"),
            ...     portfolio_value=Decimal("50000.00")
            ... )
            >>> is_valid  # 1000 * $200 = $200k notional, 400% of $50k portfolio
            False
            >>> reason
            'Position would exceed 20.0% of portfolio ($200000.00 > $10000.00)'

        Notes:
            - Returns (True, "") on success (empty reason string)
            - Never raises exceptions (safe to call in critical path)
            - Logs all blocked orders at WARNING level
            - Circuit breaker check is always first (fail fast)
        """
        # 1. Circuit breaker check (highest priority - fail fast)
        if self.breaker.is_tripped():
            reason = f"Circuit breaker TRIPPED: {self.breaker.get_trip_reason()}"
            logger.warning(
                f"Order blocked by circuit breaker: {symbol} {side} {qty}, "
                f"reason={self.breaker.get_trip_reason()}"
            )
            return (False, reason)

        # 2. Blacklist check
        if symbol in self.config.blacklist:
            reason = f"Symbol {symbol} is blacklisted"
            logger.warning(f"Order blocked by blacklist: {symbol}")
            return (False, reason)

        # Calculate new position after order
        new_position = self._calculate_new_position(
            current_position=current_position, side=side, qty=qty
        )

        # 3. Position size limit (absolute shares)
        max_position_size = self.config.position_limits.max_position_size
        if abs(new_position) > max_position_size:
            reason = (
                f"Position limit exceeded: {abs(new_position)} shares > " f"{max_position_size} max"
            )
            logger.warning(
                f"Order blocked by position size limit: {symbol} {side} {qty}, "
                f"new_position={new_position}, limit={max_position_size}"
            )
            return (False, reason)

        # 4. Position size limit (% of portfolio) - if price and portfolio_value provided
        if current_price is not None and portfolio_value is not None:
            position_notional = abs(new_position) * current_price
            max_position_pct = self.config.position_limits.max_position_pct
            max_notional = portfolio_value * max_position_pct

            if position_notional > max_notional:
                reason = (
                    f"Position would exceed {max_position_pct * 100:.1f}% of portfolio "
                    f"(${position_notional:.2f} > ${max_notional:.2f})"
                )
                logger.warning(
                    f"Order blocked by position % limit: {symbol} {side} {qty}, "
                    f"notional=${position_notional:.2f}, "
                    f"max=${max_notional:.2f} ({max_position_pct * 100:.1f}%)"
                )
                return (False, reason)

        # All checks passed
        return (True, "")

    def _calculate_new_position(self, current_position: int, side: str, qty: int) -> int:
        """
        Calculate new position after order execution.

        Args:
            current_position: Current position (long=positive, short=negative)
            side: Order side ("buy" or "sell")
            qty: Order quantity (always positive)

        Returns:
            New position after order execution

        Example:
            >>> checker._calculate_new_position(100, "buy", 50)
            150
            >>> checker._calculate_new_position(100, "sell", 50)
            50
            >>> checker._calculate_new_position(100, "sell", 200)
            -100
        """
        if side == "buy":
            return current_position + qty
        elif side == "sell":
            return current_position - qty
        else:
            raise ValueError(f"Invalid side: {side} (must be 'buy' or 'sell')")

    def check_portfolio_exposure(
        self,
        positions: list[tuple[str, int, Decimal]],  # [(symbol, qty, price), ...]
    ) -> tuple[bool, str]:
        """
        Check total portfolio exposure against limits.

        Validates that total notional, long exposure, and short exposure
        are within configured limits.

        Args:
            positions: List of (symbol, qty, price) tuples
                qty: positive=long, negative=short
                price: current market price

        Returns:
            (is_valid, reason):
                - (True, "") if within limits
                - (False, "reason") if exceeded

        Example:
            >>> positions = [
            ...     ("AAPL", 100, Decimal("200.00")),   # $20k long
            ...     ("MSFT", 200, Decimal("400.00")),   # $80k long
            ...     ("TSLA", -50, Decimal("300.00")),   # $15k short
            ... ]
            >>> is_valid, reason = checker.check_portfolio_exposure(positions)
            >>> # Total: $115k, Long: $100k, Short: $15k
            >>> is_valid
            False  # If max_total_notional = $100k
            >>> reason
            'Total exposure exceeds limit: $115000.00 > $100000.00'

        Notes:
            - Notional = abs(qty) * price
            - Long exposure = sum of long positions
            - Short exposure = sum of abs(short positions)
            - Total exposure = long + short
        """
        total_exposure = Decimal("0.00")
        long_exposure = Decimal("0.00")
        short_exposure = Decimal("0.00")

        for symbol, qty, price in positions:
            notional = abs(qty) * price

            if qty > 0:
                long_exposure += notional
            elif qty < 0:
                short_exposure += notional

            total_exposure += notional

        # Check total notional limit
        max_total = self.config.portfolio_limits.max_total_notional
        if total_exposure > max_total:
            reason = f"Total exposure exceeds limit: ${total_exposure:.2f} > ${max_total:.2f}"
            logger.warning(
                f"Portfolio exposure limit exceeded: total=${total_exposure:.2f}, limit=${max_total:.2f}"
            )
            return (False, reason)

        # Check long exposure limit
        max_long = self.config.portfolio_limits.max_long_exposure
        if long_exposure > max_long:
            reason = f"Long exposure exceeds limit: ${long_exposure:.2f} > ${max_long:.2f}"
            logger.warning(
                f"Long exposure limit exceeded: long=${long_exposure:.2f}, limit=${max_long:.2f}"
            )
            return (False, reason)

        # Check short exposure limit
        max_short = self.config.portfolio_limits.max_short_exposure
        if short_exposure > max_short:
            reason = f"Short exposure exceeds limit: ${short_exposure:.2f} > ${max_short:.2f}"
            logger.warning(
                f"Short exposure limit exceeded: short=${short_exposure:.2f}, limit=${max_short:.2f}"
            )
            return (False, reason)

        # All checks passed
        return (True, "")
