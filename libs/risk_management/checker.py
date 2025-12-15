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
from libs.risk_management.kill_switch import KillSwitch
from libs.risk_management.position_reservation import (
    PositionReservation,
    ReservationResult,
)

logger = logging.getLogger(__name__)


class RiskChecker:
    """
    Pre-trade risk validation.

    Validates orders against all risk limits before submission to broker.

    Attributes:
        config: Risk configuration with all limits
        breaker: Circuit breaker instance
        kill_switch: Kill switch instance (optional, but recommended for production)
        position_reservation: Position reservation for atomic limit checking (optional)

    Example:
        >>> checker = RiskChecker(config=config, breaker=breaker, kill_switch=kill_switch)
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
        - Kill switch check is highest priority (absolute trading halt)
        - Circuit breaker check is second priority
        - Blacklist check is third priority
        - Position reservation provides atomic limit checking (prevents race conditions)
    """

    def __init__(
        self,
        config: RiskConfig,
        breaker: CircuitBreaker,
        kill_switch: KillSwitch | None = None,
        position_reservation: PositionReservation | None = None,
    ):
        """
        Initialize risk checker.

        Args:
            config: Risk configuration with all limits
            breaker: Circuit breaker instance
            kill_switch: Kill switch instance (optional for backwards compatibility,
                but strongly recommended for production use)
            position_reservation: Position reservation for atomic limit checking (optional).
                When provided, enables atomic position limit checks that prevent race
                conditions from concurrent orders.

        Example:
            >>> from libs.redis_client import RedisClient
            >>> redis = RedisClient(host="localhost", port=6379)
            >>> config = RiskConfig()
            >>> breaker = CircuitBreaker(redis_client=redis)
            >>> kill_switch = KillSwitch(redis_client=redis)
            >>> position_res = PositionReservation(redis=redis)
            >>> checker = RiskChecker(
            ...     config=config,
            ...     breaker=breaker,
            ...     kill_switch=kill_switch,
            ...     position_reservation=position_res
            ... )
        """
        self.config = config
        self.breaker = breaker
        self.kill_switch = kill_switch
        self.position_reservation = position_reservation

    def validate_order(
        self,
        symbol: str,
        side: str,  # "buy" | "sell"
        qty: int,
        current_position: int = 0,
        current_price: Decimal | None = None,
        portfolio_value: Decimal | None = None,
        *,
        _skip_position_limit: bool = False,  # Internal: skip when using atomic reservation
    ) -> tuple[bool, str]:
        """
        Validate order against all risk limits.

        Checks performed (in order):
        0. Kill switch state (HIGHEST priority - absolute trading halt)
        1. Circuit breaker state (second priority)
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
            - Kill switch check is always first (absolute halt)
            - Circuit breaker check is second (automatic risk-based halt)
        """
        # 0. Kill switch check (HIGHEST priority - absolute trading halt)
        # Kill switch is operator-controlled emergency stop - blocks ALL trading
        if self.kill_switch is not None and self.kill_switch.is_engaged():
            reason = "Kill switch ENGAGED: All trading halted by operator"
            logger.critical(
                f"Order blocked by KILL SWITCH: {symbol} {side} {qty}",
                extra={
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "kill_switch_engaged": True,
                },
            )
            return (False, reason)

        # 1. Circuit breaker check (second priority - automatic risk halt)
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
        # Skip if using atomic reservation (reservation does this check atomically)
        if not _skip_position_limit:
            max_position_size = self.config.position_limits.max_position_size
            if abs(new_position) > max_position_size:
                reason = (
                    f"Position limit exceeded: {abs(new_position)} shares > "
                    f"{max_position_size} max"
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

        for _, qty, price in positions:
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

    def validate_order_with_reservation(
        self,
        symbol: str,
        side: str,
        qty: int,
        current_position: int = 0,
        current_price: Decimal | None = None,
        portfolio_value: Decimal | None = None,
    ) -> tuple[bool, str, ReservationResult | None]:
        """
        Validate order with atomic position reservation.

        This method combines standard risk validation with atomic position reservation
        to prevent race conditions from concurrent orders. Use this for production
        order submission paths where multiple orders might be processed simultaneously.

        Args:
            symbol: Stock symbol (e.g., "AAPL")
            side: Order side ("buy" or "sell")
            qty: Order quantity (positive integer)
            current_position: Current position in symbol (default: 0)
            current_price: Current market price (optional, for % limit check)
            portfolio_value: Total portfolio value (optional, for % limit check)

        Returns:
            (is_valid, reason, reservation_result):
                - (True, "", ReservationResult) if order passes all checks with reservation
                - (False, "reason", None) if blocked by risk checks
                - (False, "reason", ReservationResult) if reservation failed (limit exceeded)

        Example:
            >>> # Atomic validation with reservation
            >>> is_valid, reason, reservation = checker.validate_order_with_reservation(
            ...     "AAPL", "buy", 100, current_position=0
            ... )
            >>> if is_valid and reservation and reservation.success:
            ...     try:
            ...         submit_order_to_broker(...)
            ...         checker.confirm_reservation("AAPL", reservation.token)
            ...     except BrokerError:
            ...         checker.release_reservation("AAPL", reservation.token)

        Notes:
            - If position_reservation is not configured, falls back to standard validate_order
            - Reservation token must be confirmed or released after broker submission
            - Reservation auto-expires after TTL (default 60s) as safety net
        """
        # First run standard validation
        # Skip position limit check if using atomic reservation (avoids redundant stateless check)
        use_atomic_reservation = self.position_reservation is not None
        is_valid, reason = self.validate_order(
            symbol=symbol,
            side=side,
            qty=qty,
            current_position=current_position,
            current_price=current_price,
            portfolio_value=portfolio_value,
            _skip_position_limit=use_atomic_reservation,  # Atomic reservation does this check
        )

        if not is_valid:
            return (False, reason, None)

        # If position_reservation is not configured, return standard result
        if not use_atomic_reservation:
            logger.debug("Position reservation not configured, using standard validation only")
            return (True, "", None)

        # Attempt atomic position reservation
        # Assert for mypy: we know position_reservation is not None at this point
        assert self.position_reservation is not None  # Verified by use_atomic_reservation check
        max_position_size = self.config.position_limits.max_position_size
        reservation_result = self.position_reservation.reserve(
            symbol=symbol,
            side=side,
            qty=qty,
            max_limit=max_position_size,
            current_position=current_position,
        )

        if not reservation_result.success:
            logger.warning(
                f"Position reservation failed: {symbol} {side} {qty}, "
                f"reason={reservation_result.reason}"
            )
            return (False, reservation_result.reason, reservation_result)

        logger.debug(
            f"Position reserved: {symbol} {side} {qty}, " f"token={reservation_result.token}"
        )
        return (True, "", reservation_result)

    def confirm_reservation(self, symbol: str, token: str) -> bool:
        """
        Confirm a position reservation after successful order submission.

        Call this after the broker accepts the order to finalize the reservation.

        Args:
            symbol: Stock symbol
            token: Reservation token from validate_order_with_reservation()

        Returns:
            True if confirmed successfully, False otherwise

        Notes:
            - Safe to call multiple times (idempotent)
            - Requires position_reservation to be configured
        """
        if self.position_reservation is None:
            logger.warning("Cannot confirm reservation: position_reservation not configured")
            return False

        result = self.position_reservation.confirm(symbol, token)
        return result.success

    def release_reservation(self, symbol: str, token: str) -> bool:
        """
        Release a position reservation after order failure.

        Call this when an order fails after reservation to return
        the reserved position back to the pool.

        Args:
            symbol: Stock symbol
            token: Reservation token from validate_order_with_reservation()

        Returns:
            True if released successfully, False otherwise

        Notes:
            - Safe to call multiple times (idempotent)
            - Requires position_reservation to be configured
        """
        if self.position_reservation is None:
            logger.warning("Cannot release reservation: position_reservation not configured")
            return False

        result = self.position_reservation.release(symbol, token)
        return result.success
