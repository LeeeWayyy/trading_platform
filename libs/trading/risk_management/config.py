"""
Risk management configuration models.

This module defines Pydantic models for all risk limits and configuration.
Configuration is loaded from environment variables with sensible defaults.

Example:
    >>> from libs.trading.risk_management.config import RiskConfig
    >>> config = RiskConfig()
    >>> config.position_limits.max_position_size
    1000
    >>> config.loss_limits.daily_loss_limit
    Decimal('5000.00')

See Also:
    - docs/CONCEPTS/risk-management.md for educational overview
    - docs/ADRs/0011-risk-management-system.md for architecture details
"""

from decimal import Decimal

from pydantic import BaseModel, Field


class PositionLimits(BaseModel):
    """
    Per-symbol position limits.

    Prevents concentration risk by limiting how much of a single symbol
    can be held in the portfolio.

    Attributes:
        max_position_size: Maximum shares per symbol (absolute value).
            Example: 1000 means max 1000 shares long or 1000 shares short.
        max_position_pct: Maximum position as % of portfolio value.
            Example: 0.20 means single symbol can be max 20% of portfolio.

    Example:
        >>> limits = PositionLimits(max_position_size=500, max_position_pct=Decimal("0.15"))
        >>> # Position check:
        >>> if abs(new_position) > limits.max_position_size:
        ...     raise RiskViolation("Position too large")

    Notes:
        - Both limits are enforced (AND condition)
        - Applies to absolute position value (long or short)
        - Configured via environment variables RISK_MAX_POSITION_SIZE and RISK_MAX_POSITION_PCT
    """

    max_position_size: int = Field(
        default=1000,
        description="Maximum shares per symbol (absolute value)",
        ge=1,  # Must be at least 1
    )
    max_position_pct: Decimal = Field(
        default=Decimal("0.20"),
        description="Maximum position as % of portfolio (0.20 = 20%)",
        ge=Decimal("0.01"),  # At least 1%
        le=Decimal("1.00"),  # At most 100%
    )


class PortfolioLimits(BaseModel):
    """
    Portfolio-level exposure limits.

    Controls total notional exposure to prevent over-leverage.

    Attributes:
        max_total_notional: Maximum total notional exposure ($).
            Calculated as sum of abs(position_value) for all positions.
        max_long_exposure: Maximum long exposure ($).
            Calculated as sum of position_value for long positions only.
        max_short_exposure: Maximum short exposure ($).
            Calculated as sum of abs(position_value) for short positions only.

    Example:
        >>> limits = PortfolioLimits(
        ...     max_total_notional=Decimal("100000.00"),
        ...     max_long_exposure=Decimal("80000.00"),
        ...     max_short_exposure=Decimal("20000.00")
        ... )
        >>> # Portfolio has $75k long + $15k short = $90k total
        >>> # New $20k long order would violate max_long_exposure ($95k > $80k)

    Notes:
        - Notional = shares * price
        - Long exposure: positions with qty > 0
        - Short exposure: positions with qty < 0 (absolute value)
        - Configured via environment variables RISK_MAX_TOTAL_NOTIONAL, etc.

    See Also:
        - docs/CONCEPTS/risk-management.md#exposure-management
    """

    max_total_notional: Decimal = Field(
        default=Decimal("100000.00"),
        description="Maximum total notional exposure ($)",
        ge=Decimal("1000.00"),  # At least $1k
    )
    max_long_exposure: Decimal = Field(
        default=Decimal("80000.00"),
        description="Maximum long exposure ($)",
        ge=Decimal("0.00"),
    )
    max_short_exposure: Decimal = Field(
        default=Decimal("20000.00"),
        description="Maximum short exposure ($)",
        ge=Decimal("0.00"),
    )


class LossLimits(BaseModel):
    """
    Loss limit configuration.

    Defines maximum acceptable losses before circuit breaker trips.

    Attributes:
        daily_loss_limit: Maximum daily loss before trading stops ($).
            Stored as a positive value. A trip occurs when the day's P&L
            (a negative number) falls below the negative of this limit.
            Example: 5000.00 means trip when today_pnl < -5000.00.
        max_drawdown_pct: Maximum drawdown from peak equity.
            Example: 0.10 = 10% max drawdown from all-time high.

    Example:
        >>> limits = LossLimits(
        ...     daily_loss_limit=Decimal("5000.00"),
        ...     max_drawdown_pct=Decimal("0.10")
        ... )
        >>> # Check daily loss (PnL is negative for losses)
        >>> today_pnl = Decimal("-5200.00")  # Example: $5200 loss
        >>> if today_pnl < -limits.daily_loss_limit:
        ...     circuit_breaker.trip("DAILY_LOSS_EXCEEDED")
        >>> # Check drawdown
        >>> peak_equity = Decimal("100000.00")
        >>> current_equity = Decimal("89000.00")
        >>> drawdown_pct = (peak_equity - current_equity) / peak_equity
        >>> if drawdown_pct > limits.max_drawdown_pct:
        ...     circuit_breaker.trip("MAX_DRAWDOWN")

    Notes:
        - daily_loss_limit is stored as positive value (represents loss threshold)
        - max_drawdown_pct is decimal (0.10 = 10%)
        - Both are enforced continuously by risk monitor
        - Configured via environment variables RISK_DAILY_LOSS_LIMIT, RISK_MAX_DRAWDOWN_PCT

    See Also:
        - docs/CONCEPTS/risk-management.md#loss-limits
        - docs/CONCEPTS/risk-management.md#drawdown
    """

    daily_loss_limit: Decimal = Field(
        default=Decimal("5000.00"),
        description="Maximum daily loss before circuit breaker trips ($)",
        ge=Decimal("0.00"),  # Stored as positive value
    )
    max_drawdown_pct: Decimal = Field(
        default=Decimal("0.10"),
        description="Maximum drawdown from peak equity (0.10 = 10%)",
        ge=Decimal("0.01"),  # At least 1%
        le=Decimal("0.50"),  # At most 50%
    )


class RiskConfig(BaseModel):
    """
    Complete risk management configuration.

    Aggregates all risk limits into a single config object.

    Attributes:
        position_limits: Per-symbol position limits
        portfolio_limits: Portfolio-level exposure limits
        loss_limits: Daily loss and drawdown limits
        blacklist: List of symbols forbidden from trading

    Example:
        >>> config = RiskConfig()
        >>> config.position_limits.max_position_size
        1000
        >>> config.blacklist
        []
        >>>
        >>> # With custom limits
        >>> config = RiskConfig(
        ...     position_limits=PositionLimits(max_position_size=500),
        ...     blacklist=["GME", "AMC"]
        ... )

    Notes:
        - Load from environment for production
        - Use defaults for testing
        - Blacklist prevents trading specific symbols (e.g., meme stocks, penny stocks)

    See Also:
        - config/settings.py for environment variable mapping
    """

    position_limits: PositionLimits = Field(default_factory=PositionLimits)
    portfolio_limits: PortfolioLimits = Field(default_factory=PortfolioLimits)
    loss_limits: LossLimits = Field(default_factory=LossLimits)
    blacklist: list[str] = Field(
        default_factory=list,
        description="Symbols forbidden from trading (e.g., ['GME', 'AMC'])",
    )
