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

### Example 4: T6 Notional P&L (Simple MVP)
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

### P1: Add Realized P&L
- Query positions table from Execution Gateway (T4)
- Track entry and exit prices
- Calculate profit/loss on closed positions
- Report daily realized P&L

### P1: Add Unrealized P&L
- Fetch current prices from market data
- Calculate mark-to-market value
- Compare to entry values
- Show per-position unrealized P&L

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

- [Position Sizing](./position-sizing.md) - How to determine order quantities
- [Corporate Actions](./corporate-actions.md) - Stock splits affect P&L calculation
- [Slippage](./slippage.md) - Execution costs reduce P&L

## Related ADRs

- ADR-0007: Paper Run Automation - Uses notional P&L for MVP
- ADR-0005: Execution Gateway - Stores position data for P&L calculation
