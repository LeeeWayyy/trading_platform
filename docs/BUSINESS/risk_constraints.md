# Risk Constraints

## Position Limits
- Max position per symbol: $100,000 notional
- Max total portfolio notional: $500,000
- Max symbols held simultaneously: 20

## Drawdown Limits
- Max daily drawdown: -2% of portfolio
- Max weekly drawdown: -5% of portfolio
- Circuit breaker triggers at -3% intraday

## Execution Constraints
- No market orders during first/last 5 minutes of trading
- Maximum order size: 5% of ADV (Average Daily Volume)
- Minimum time between orders for same symbol: 30 seconds
