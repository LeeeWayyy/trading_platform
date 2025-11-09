# P&L (Profit and Loss) Calculation

## Plain English Explanation

P&L (Profit and Loss) measures how much money you've made or lost on your trades. There are different types:

1. **Notional P&L** - The total dollar value of positions you've entered
2. **Realized P&L** - Actual profit/loss from closed positions (bought and sold)
3. **Unrealized P&L** - Paper profit/loss on open positions (mark-to-market)
4. **Total P&L** - Realized + Unrealized

**Example:**
- You start with $100,000
- Buy 100 shares of AAPL at $150 = $15,000 position (notional)
- AAPL rises to $153 = $300 unrealized profit (you haven't sold yet)
- You sell 100 shares at $153 = $300 realized profit (money in your account)

## Why It Matters

### 1. Performance Tracking
P&L tells you if your strategy is working:
- Positive P&L = Strategy making money ✅
- Negative P&L = Strategy losing money ❌
- Track daily/weekly/monthly to spot trends

### 2. Risk Management
Large unrealized losses can trigger:
- Margin calls (broker demands more cash)
- Circuit breakers (automatic position reduction)
- Portfolio rebalancing

### 3. Tax Reporting
Realized P&L determines:
- Capital gains tax (short-term vs long-term)
- Wash sale rules (re-buying within 30 days)
- Tax loss harvesting opportunities

### 4. Strategy Comparison
Compare different strategies using:
- Sharpe ratio (return per unit of risk)
- Max drawdown (largest peak-to-trough loss)
- Win rate (% of profitable trades)

## Common Pitfalls

### 1. Confusing Notional with Profit
**Pitfall:**
```python
# WRONG - notional value is not profit
total_notional = 100 * $150  # $15,000
profit = total_notional      # ❌ This is position size, not profit!
```

**Correct:**
```python
# RIGHT - profit is change in value
entry_value = 100 * $150     # $15,000
current_value = 100 * $153   # $15,300
unrealized_pnl = current_value - entry_value  # $300 ✅
```

### 2. Ignoring Fees and Slippage
**Pitfall:**
```python
# WRONG - assumes perfect execution
buy_price = $150.00
sell_price = $153.00
gross_pnl = (sell_price - buy_price) * qty  # $300
# ❌ Ignores $0.01/share commission = -$1
# ❌ Ignores $0.02 slippage (bid-ask spread) = -$2
# Actual P&L = $297, not $300
```

**Correct:**
```python
# RIGHT - account for all costs
commission_per_share = 0.01
slippage_per_share = 0.02
total_cost_per_share = commission_per_share + slippage_per_share

net_pnl = (sell_price - buy_price - total_cost_per_share) * qty
# = ($153 - $150 - $0.03) * 100 = $297 ✅
```

### 3. Not Adjusting for Corporate Actions
**Pitfall:**
```python
# WRONG - ignoring stock split
buy_100_shares_at_400 = 100 * $400      # $40,000
after_4_for_1_split = 400 * $100        # $40,000
pnl = after_4_for_1_split - buy_100_shares_at_400
# = $0 (correct value, but misleading if you think you still have 100 shares)
```

**Correct:**
```python
# RIGHT - adjust for split
original_shares = 100
split_ratio = 4
adjusted_shares = original_shares * split_ratio  # 400 shares

original_price = $400
adjusted_price = original_price / split_ratio    # $100

# Value unchanged
value = adjusted_shares * adjusted_price  # 400 * $100 = $40,000 ✅
```

### 4. Double-Counting in Portfolio P&L
**Pitfall:**
```python
# WRONG - counting same position twice
position_pnl = {
    "AAPL": 300,    # Unrealized
    "MSFT": 500,    # Unrealized
}
total_pnl = sum(position_pnl.values())  # $800

# Later, AAPL position closes
realized_pnl = 300  # From closing AAPL
total_pnl += realized_pnl  # ❌ Now $1,100 (double-counted AAPL)
```

**Correct:**
```python
# RIGHT - track realized and unrealized separately
unrealized_pnl = {"MSFT": 500}  # AAPL removed after close
realized_pnl = 300               # From closed AAPL
total_pnl = realized_pnl + sum(unrealized_pnl.values())  # $800 ✅
```

## Examples

### Example 1: Simple Long Trade (Round Trip)
```python
# Day 1: Buy
buy_qty = 100
buy_price = Decimal("150.00")
entry_value = buy_qty * buy_price
# entry_value = $15,000 (notional value)

# Day 5: Stock rises
current_price = Decimal("153.00")
current_value = buy_qty * current_price
unrealized_pnl = current_value - entry_value
# unrealized_pnl = $15,300 - $15,000 = $300 (mark-to-market)

# Day 10: Sell
sell_price = Decimal("155.00")
exit_value = buy_qty * sell_price
realized_pnl = exit_value - entry_value
# realized_pnl = $15,500 - $15,000 = $500 (actual profit)
```

### Example 2: Short Trade
```python
# Day 1: Short sell (borrow and sell)
short_qty = 100
sell_price = Decimal("150.00")
entry_value = short_qty * sell_price
# entry_value = $15,000 (cash received)

# Day 5: Stock falls
current_price = Decimal("145.00")
current_value = short_qty * current_price
unrealized_pnl = entry_value - current_value  # Note: reversed for shorts
# unrealized_pnl = $15,000 - $14,500 = $500 (profit from price drop)

# Day 10: Buy to cover (close short)
buy_price = Decimal("143.00")
exit_value = short_qty * buy_price
realized_pnl = entry_value - exit_value
# realized_pnl = $15,000 - $14,300 = $700 (actual profit)
```

### Example 3: Portfolio P&L
```python
# Portfolio of 3 positions
positions = [
    {
        "symbol": "AAPL",
        "qty": 100,
        "avg_entry": Decimal("150.00"),
        "current_price": Decimal("153.00"),
        "closed": False
    },
    {
        "symbol": "MSFT",
        "qty": 50,
        "avg_entry": Decimal("300.00"),
        "current_price": Decimal("305.00"),
        "closed": False
    },
    {
        "symbol": "GOOGL",
        "qty": 200,
        "avg_entry": Decimal("100.00"),
        "exit_price": Decimal("98.00"),
        "closed": True  # Position closed
    },
]

# Calculate P&L
unrealized_pnl = Decimal("0")
realized_pnl = Decimal("0")

for pos in positions:
    if pos["closed"]:
        # Realized P&L (closed position)
        pnl = (pos["exit_price"] - pos["avg_entry"]) * pos["qty"]
        realized_pnl += pnl
    else:
        # Unrealized P&L (open position)
        pnl = (pos["current_price"] - pos["avg_entry"]) * pos["qty"]
        unrealized_pnl += pnl

# Results:
# AAPL: ($153 - $150) * 100 = $300 unrealized
# MSFT: ($305 - $300) * 50 = $250 unrealized
# GOOGL: ($98 - $100) * 200 = -$400 realized

# unrealized_pnl = $550
# realized_pnl = -$400
# total_pnl = $150
```

### Example 4: T1.1 Enhanced P&L (Real Implementation)
```python
# Scenario: Portfolio with 3 positions after a day of trading

# Position data from T4 Execution Gateway
positions = [
    {
        'symbol': 'AAPL',
        'qty': 100,                    # Long 100 shares
        'avg_entry_price': '150.00',   # Bought at $150
        'realized_pl': '0.00'          # No closes yet
    },
    {
        'symbol': 'MSFT',
        'qty': -50,                    # Short 50 shares
        'avg_entry_price': '300.00',   # Shorted at $300
        'realized_pl': '200.00'        # Partial close profit
    },
    {
        'symbol': 'GOOGL',
        'qty': 0,                      # Closed position
        'avg_entry_price': '140.00',   # Originally bought at $140
        'realized_pl': '-400.00'       # Sold at loss
    }
]

# Current prices from Alpaca Latest Quote API
current_prices = {
    'AAPL': Decimal('152.00'),   # Up $2 from entry
    'MSFT': Decimal('295.00')    # Down $5 from entry (good for short)
}

# Calculate enhanced P&L
pnl = await calculate_enhanced_pnl(positions, current_prices)

# Results:
# ========

# AAPL (long position):
#   Unrealized P&L = (152 - 150) * 100 = +$200
#   Realized P&L   = $0
#   Total P&L      = +$200

# MSFT (short position):
#   Unrealized P&L = (300 - 295) * 50 = +$250  (profit on short)
#   Realized P&L   = $200 (from partial close)
#   Total P&L      = +$450

# GOOGL (closed position):
#   Unrealized P&L = $0 (position closed)
#   Realized P&L   = -$400 (loss locked in)
#   Total P&L      = -$400

# Portfolio totals:
# pnl['unrealized_pnl'] = $200 + $250 = $450
# pnl['realized_pnl'] = $0 + $200 + (-$400) = -$200
# pnl['total_pnl'] = $450 + (-$200) = $250

# Per-symbol breakdown:
# pnl['per_symbol'] = {
#     'AAPL': {
#         'realized': Decimal('0.00'),
#         'unrealized': Decimal('200.00'),
#         'qty': 100,
#         'avg_entry_price': Decimal('150.00'),
#         'current_price': Decimal('152.00'),
#         'status': 'open'
#     },
#     'MSFT': {
#         'realized': Decimal('200.00'),
#         'unrealized': Decimal('250.00'),
#         'qty': -50,
#         'avg_entry_price': Decimal('300.00'),
#         'current_price': Decimal('295.00'),
#         'status': 'open'
#     },
#     'GOOGL': {
#         'realized': Decimal('-400.00'),
#         'unrealized': Decimal('0.00'),
#         'qty': 0,
#         'avg_entry_price': Decimal('140.00'),
#         'current_price': None,
#         'status': 'closed'
#     }
# }

# Console output from paper_run.py:
# [3/5] Calculating enhanced P&L...
#   Positions Fetched:  3 total
#   Prices Updated:     2 symbols
#
#   Realized P&L:       -$200.00
#   Unrealized P&L:     +$450.00
#   Total P&L:          +$250.00
#
#   Open Positions:     2
#   Closed Positions:   1
#
#   Per-Symbol P&L:
#     AAPL   (  100 shares): Realized: +$0.00, Unrealized: +$200.00
#     MSFT   (  -50 shares): Realized: +$200.00, Unrealized: +$250.00
#     GOOGL  (closed): Realized: -$400.00
```

### Example 5: T6 Notional P&L (Simple MVP)
```python
# T6 paper_run.py calculates simple notional P&L

# Orchestration result from T5
result = {
    "orders": [
        {"symbol": "AAPL", "side": "buy", "qty": 133, "price": 150.00, "status": "accepted"},
        {"symbol": "MSFT", "side": "buy", "qty": 66, "price": 300.00, "status": "accepted"},
        {"symbol": "GOOGL", "side": "sell", "qty": 200, "price": 100.00, "status": "accepted"},
    ]
}

# Calculate total notional value
total_notional = Decimal("0")
for order in result["orders"]:
    if order["status"] == "accepted":
        notional = abs(order["qty"] * Decimal(str(order["price"])))
        total_notional += notional

# total_notional = (133*150) + (66*300) + (200*100)
#                = 19,950 + 19,800 + 20,000
#                = $59,750

# This is NOT profit - it's the dollar value of positions entered
# Actual P&L would require tracking exits and price changes
```

## P&L Calculation Methods

### Method 1: Notional Value (T6 MVP)
**Formula:**
```python
notional = abs(quantity * price)
```

**Pros:**
- ✅ Simple to calculate
- ✅ No need for current prices
- ✅ Useful for exposure tracking

**Cons:**
- ❌ Not actual profit/loss
- ❌ Doesn't account for price changes
- ❌ Can't compare strategy performance

**Use Case:** Quick check that orders were placed correctly

### Method 2: Realized P&L (Round Trip)
**Formula:**
```python
# Long trade
realized_pnl = (exit_price - entry_price) * quantity - fees

# Short trade
realized_pnl = (entry_price - exit_price) * quantity - fees
```

**Pros:**
- ✅ Actual cash profit/loss
- ✅ Accurate for closed positions
- ✅ Useful for tax reporting

**Cons:**
- ❌ Only works for closed positions
- ❌ Doesn't show current portfolio value
- ❌ Ignores unrealized gains/losses

**Use Case:** Calculate taxable gains, track realized performance

### Method 3: Unrealized P&L (Mark-to-Market)
**Formula:**
```python
# Long position
unrealized_pnl = (current_price - avg_entry_price) * quantity

# Short position
unrealized_pnl = (avg_entry_price - current_price) * quantity
```

**Pros:**
- ✅ Shows current position value
- ✅ Useful for risk management
- ✅ Triggers stop-losses / circuit breakers

**Cons:**
- ❌ Requires current market prices
- ❌ Fluctuates continuously
- ❌ Not actual profit until realized

**Use Case:** Monitor open positions, risk limits

### Method 4: Total P&L (Complete Picture)
**Formula:**
```python
total_pnl = realized_pnl + sum(unrealized_pnl for all open positions)
```

**Pros:**
- ✅ Complete portfolio view
- ✅ Combines realized and unrealized
- ✅ Best for performance reporting

**Cons:**
- ❌ Most complex to calculate
- ❌ Requires position tracking
- ❌ Requires real-time prices

**Use Case:** Daily performance reports, strategy comparison

## Implementation in Trading Platform

### T6 (MVP): Notional P&L Only
```python
# scripts/paper_run.py
def calculate_simple_pnl(orchestration_result):
    """
    Calculate total notional value of accepted orders.

    This is NOT actual profit - it's the dollar value of positions entered.
    Useful for verifying correct order sizing and execution.

    Args:
        orchestration_result: Result from Orchestrator Service (T5)

    Returns:
        dict with notional_value, num_accepted, success_rate
    """
    total_notional = Decimal("0")
    num_accepted = 0

    for mapping in orchestration_result["mappings"]:
        if mapping["order_status"] == "accepted":
            notional = abs(
                mapping["order_qty"] * Decimal(str(mapping["order_price"]))
            )
            total_notional += notional
            num_accepted += 1

    return {
        "total_notional": total_notional,
        "num_accepted": num_accepted,
        "success_rate": num_accepted / len(orchestration_result["mappings"])
    }
```

### T1.1 (P1): Enhanced P&L with Realized/Unrealized Breakdown
```python
# scripts/paper_run.py - Enhanced P&L calculation

async def calculate_enhanced_pnl(
    positions: List[Dict[str, Any]],
    current_prices: Dict[str, Decimal]
) -> Dict[str, Any]:
    """
    Calculate enhanced P&L with realized/unrealized breakdown.

    This implementation:
    - Fetches positions from T4 Execution Gateway
    - Gets live prices from Alpaca Latest Quote API
    - Calculates realized P&L from closed positions (qty=0)
    - Calculates unrealized P&L from open positions (qty!=0)
    - Provides per-symbol breakdown

    Args:
        positions: List of position dicts from T4 /api/v1/positions
        current_prices: Dict mapping symbol -> current_price from Alpaca

    Returns:
        {
            'realized_pnl': Decimal('500.00'),      # Closed positions
            'unrealized_pnl': Decimal('200.00'),     # Open positions
            'total_pnl': Decimal('700.00'),         # Total
            'per_symbol': {...},
            'num_open_positions': 2,
            'num_closed_positions': 1
        }

    Example:
        >>> positions = [
        ...     {'symbol': 'AAPL', 'qty': 100, 'avg_entry_price': '150.00', 'realized_pl': '0'},
        ...     {'symbol': 'MSFT', 'qty': 0, 'avg_entry_price': '300.00', 'realized_pl': '500'}
        ... ]
        >>> prices = {'AAPL': Decimal('152.00')}
        >>> pnl = await calculate_enhanced_pnl(positions, prices)
        >>> pnl['realized_pnl']
        Decimal('500.00')
        >>> pnl['unrealized_pnl']
        Decimal('200.00')  # (152 - 150) * 100
        >>> pnl['total_pnl']
        Decimal('700.00')
    """
    realized_pnl = Decimal("0")
    unrealized_pnl = Decimal("0")
    per_symbol_pnl = {}

    for position in positions:
        symbol = position['symbol']
        qty = int(position.get('qty', 0))
        avg_entry_price = Decimal(str(position.get('avg_entry_price', 0)))
        position_realized = Decimal(str(position.get('realized_pl', 0)))

        if qty == 0:
            # Closed position - only realized P&L
            realized_pnl += position_realized
            per_symbol_pnl[symbol] = {
                'realized': position_realized,
                'unrealized': Decimal("0"),
                'status': 'closed'
            }
        else:
            # Open position - calculate unrealized P&L
            current_price = current_prices.get(symbol, avg_entry_price)
            position_unrealized = (current_price - avg_entry_price) * qty
            unrealized_pnl += position_unrealized
            realized_pnl += position_realized

            per_symbol_pnl[symbol] = {
                'realized': position_realized,
                'unrealized': position_unrealized,
                'qty': qty,
                'avg_entry_price': avg_entry_price,
                'current_price': current_price,
                'status': 'open'
            }

    return {
        'realized_pnl': realized_pnl,
        'unrealized_pnl': unrealized_pnl,
        'total_pnl': realized_pnl + unrealized_pnl,
        'per_symbol': per_symbol_pnl,
        'num_open_positions': sum(1 for p in positions if p.get('qty', 0) != 0),
        'num_closed_positions': sum(1 for p in positions if p.get('qty', 0) == 0)
    }
```

**Key Features:**
- ✅ Realized P&L: From T4 positions table (closed positions with qty=0)
- ✅ Unrealized P&L: Mark-to-market using live Alpaca prices
- ✅ Per-symbol breakdown: Individual P&L for each symbol
- ✅ Handles both long and short positions
- ✅ Graceful degradation: Falls back to avg_entry_price if prices unavailable

**Price Data Source:**
```python
async def fetch_current_prices(
    symbols: List[str],
    config: Dict[str, Any]
) -> Dict[str, Decimal]:
    """
    Fetch current market prices from Alpaca Latest Quote API.

    Uses mid-quote price: (bid + ask) / 2
    Batch fetching for efficiency (1 API call for all symbols)
    Graceful degradation: Returns empty dict on error

    Returns:
        {'AAPL': Decimal('152.75'), 'MSFT': Decimal('380.50')}
    """
    alpaca_client = AlpacaExecutor(
        api_key=os.getenv('ALPACA_API_KEY'),
        secret_key=os.getenv('ALPACA_SECRET_KEY'),
        base_url=os.getenv('ALPACA_BASE_URL')
    )

    quotes = alpaca_client.get_latest_quotes(symbols)
    prices = {}
    for symbol, quote_data in quotes.items():
        prices[symbol] = quote_data['last_price']  # Mid-quote

    return prices
```

### P2: Complete P&L Dashboard
- Web UI showing total P&L
- Historical P&L chart
- Per-symbol breakdown
- Performance metrics (Sharpe, drawdown)

## Further Reading

- [Investopedia: Realized Profit](https://www.investopedia.com/terms/r/realizedprofit.asp)
- [Investopedia: Unrealized Gain](https://www.investopedia.com/terms/u/unrealizedgain.asp)
- [IRS Publication 550: Investment Income and Expenses](https://www.irs.gov/publications/p550)
- [Mark-to-Market Accounting](https://www.investopedia.com/terms/m/marktomarket.asp)

## Related Concepts

- [Corporate Actions](./corporate-actions.md) - Stock splits affect P&L calculation
- Position Sizing (TODO: concept doc needed) - How to determine order quantities
- Slippage (TODO: concept doc needed) - Execution costs reduce P&L

## Related ADRs

- [ADR-0008](../ADRs/0008-enhanced-pnl-calculation.md): Enhanced P&L Calculation (T1.1) - Architecture for realized/unrealized P&L
- [ADR-0007](../ADRs/0007-paper-run-automation.md): Paper Run Automation - Uses notional P&L for MVP
- [ADR-0014](../ADRs/0014-execution-gateway-architecture.md): Execution Gateway - Stores position data for P&L calculation
