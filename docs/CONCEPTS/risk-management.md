# Risk Management Concepts

**Audience:** Beginner traders and developers
**Purpose:** Educational guide to risk management in algorithmic trading
**Related ADR:** [ADR-0011: Risk Management System](../ADRs/0011-risk-management-system.md)

---

## Table of Contents

1. [What is Risk Management?](#what-is-risk-management)
2. [Why Risk Management Matters](#why-risk-management-matters)
3. [Position Limits](#position-limits)
4. [Loss Limits](#loss-limits)
5. [Circuit Breakers](#circuit-breakers)
6. [Drawdown](#drawdown)
7. [Exposure Management](#exposure-management)
8. [Common Risk Scenarios](#common-risk-scenarios)
9. [Best Practices](#best-practices)

---

## What is Risk Management?

**Risk management** is the practice of identifying, measuring, and controlling financial risks to protect trading capital.

**Key Principle:** "Don't lose money" comes before "make money"

In algorithmic trading, risk management involves:
- **Setting limits** on position sizes and losses
- **Monitoring** portfolio metrics continuously
- **Automatically stopping** trading when limits are breached
- **Preventing** catastrophic losses from bugs or market events

**Analogy:** Think of risk management as the brakes in a car. You need acceleration (alpha/returns) to get somewhere, but you need brakes (risk limits) to avoid crashing.

---

## Why Risk Management Matters

### Real-World Examples

**Example 1: Knight Capital (2012)**
- **What Happened:** Software bug sent millions of unintended orders
- **Loss:** $440 million in 45 minutes
- **Lesson:** No automated kill switch for runaway trading

**Example 2: Pattern Day Trading Rule**
- **Regulation:** US requires $25k minimum for day traders
- **Why:** Prevents retail traders from over-leveraging
- **Lesson:** Regulatory compliance requires risk controls

**Example 3: Flash Crash (2010)**
- **What Happened:** Market dropped 9% in minutes
- **Cause:** Automated trading without circuit breakers
- **Lesson:** Markets need automatic pause mechanisms

### Why We Need Risk Management

1. **Protect Capital** - Losing 50% requires 100% gain to recover
2. **Regulatory Compliance** - Pattern day trading, margin requirements
3. **Operational Safety** - Bugs can cause runaway trading
4. **Investor Confidence** - Controlled risk = trust from backers
5. **Longevity** - Stay in the game for compound growth

---

## Position Limits

### What is a Position?

A **position** is the quantity of shares you own (long) or owe (short) in a symbol.

**Examples:**
```
AAPL: +500 shares  → Long position (you own 500 shares)
TSLA: -200 shares  → Short position (you owe 200 shares)
MSFT: 0 shares     → No position (flat)
```

### Why Limit Position Sizes?

**Problem:** One symbol dominates portfolio

**Example:**
```
Portfolio: $100,000
AAPL position: $80,000 (80% of portfolio)
Risk: If AAPL drops 10%, portfolio loses $8,000 (8%)
```

**Solution:** Limit maximum position per symbol

```python
MAX_POSITION_PCT = 0.20  # 20% max per symbol

# If AAPL = $200/share
max_aapl_shares = (100_000 * 0.20) / 200 = 100 shares
max_aapl_value = 100 * 200 = $20,000 (20% of portfolio)
```

### Types of Position Limits

#### 1. Absolute Limit (Shares)
```python
MAX_POSITION_SIZE = 1000  # Max 1000 shares per symbol
```
- **Pro:** Simple to implement
- **Con:** Doesn't account for price (1000 shares of $10 stock vs $1000 stock)

#### 2. Percentage Limit (% of Portfolio)
```python
MAX_POSITION_PCT = 0.20  # Max 20% of portfolio per symbol
```
- **Pro:** Scales with portfolio size
- **Con:** Requires real-time portfolio valuation

#### 3. Dollar Limit (Notional Value)
```python
MAX_POSITION_VALUE = Decimal("20000.00")  # Max $20k per symbol
```
- **Pro:** Easy to understand
- **Con:** Fixed regardless of portfolio growth

**Our Implementation:** Combination of #1 and #2

```python
class PositionLimits(BaseModel):
    max_position_size: int = 1000        # Absolute share limit
    max_position_pct: Decimal = Decimal("0.20")  # % of portfolio
```

---

## Loss Limits

### What is a Loss Limit?

A **loss limit** is the maximum acceptable loss before trading stops.

**Types:**
1. **Daily Loss Limit** - Max loss in one trading day
2. **Drawdown Limit** - Max loss from peak equity
3. **Per-Trade Loss Limit** - Max loss on single trade (stop-loss)

### Daily Loss Limit

**Definition:** Maximum loss allowed in a single trading day

**Example:**
```
Starting Equity (today): $100,000
Daily Loss Limit: $5,000 (5%)
Stop Trading If: Equity < $95,000
```

**Why Daily Limits?**
- Prevents catastrophic losses in one day
- Forces re-evaluation when strategy underperforms
- Regulatory compliance (pattern day trading)

**Implementation:**
```python
class LossLimits(BaseModel):
    daily_loss_limit: Decimal = Decimal("5000.00")

# Check before each trade
if today_pnl < -daily_loss_limit:
    circuit_breaker.trip("DAILY_LOSS_EXCEEDED")
```

### Drawdown Limit

**Definition:** Maximum loss from peak equity (all-time high)

**Example:**
```
Peak Equity: $120,000 (reached last week)
Current Equity: $108,000
Drawdown: ($120,000 - $108,000) / $120,000 = 10%

If Max Drawdown = 10% → Circuit breaker trips
```

**Drawdown Formula:**
```python
drawdown_pct = (peak_equity - current_equity) / peak_equity

# Example:
peak = 120_000
current = 108_000
drawdown = (120_000 - 108_000) / 120_000 = 0.10 (10%)
```

**Why Drawdown Limits?**
- Prevents death spiral (losing 50% requires 100% gain to recover)
- Protects long-term capital
- Signals strategy breakdown

**Recovery Difficulty:**

| Loss | Gain Needed to Recover |
|------|----------------------|
| -10% | +11.1% |
| -20% | +25.0% |
| -30% | +42.9% |
| -50% | +100.0% |
| -75% | +300.0% |

**Key Insight:** Small losses are easy to recover, large losses are devastating.

---

## Circuit Breakers

### What is a Circuit Breaker?

A **circuit breaker** is an automatic system that halts trading when risk conditions are violated.

**Analogy:** Like a circuit breaker in your house that cuts power when there's an electrical overload.

### States

```
┌─────────────────────────────────────────────────┐
│  OPEN                                           │
│  ├─ Normal trading allowed                      │
│  ├─ All signals executed                        │
│  └─ Risk checks active                          │
└─────────────────────────────────────────────────┘
                      │
        Violation Detected (automatic)
                      ↓
┌─────────────────────────────────────────────────┐
│  TRIPPED                                        │
│  ├─ New entries BLOCKED                         │
│  ├─ Position-reducing orders allowed            │
│  ├─ Trip reason logged                          │
│  └─ Manual reset required                       │
└─────────────────────────────────────────────────┘
                      │
      Conditions cleared + manual reset
                      ↓
┌─────────────────────────────────────────────────┐
│  QUIET_PERIOD (5 minutes)                       │
│  ├─ Monitoring only                             │
│  ├─ No trading                                  │
│  └─ Auto-transitions to OPEN                    │
└─────────────────────────────────────────────────┘
                      │
            After 5 minutes
                      ↓
              Return to OPEN
```

### Trip Conditions

**Automatic Triggers:**
```python
TRIP_REASONS = {
    "DAILY_LOSS_EXCEEDED": "Daily loss > $5,000",
    "MAX_DRAWDOWN": "Drawdown > 10% from peak",
    "DATA_STALE": "Market data >30 min old",
    "BROKER_ERRORS": "3+ consecutive Alpaca API failures",
    "MANUAL": "Manually tripped by operator"
}
```

**Example Scenario:**
```
09:30 AM - Trading starts, circuit breaker = OPEN
10:15 AM - Position in AAPL loses $3,000
11:00 AM - Position in TSLA loses $2,500
11:05 AM - Total daily loss = -$5,500 (exceeds $5,000 limit)
11:05 AM - Circuit breaker trips automatically
          - Reason: "DAILY_LOSS_EXCEEDED"
          - State: TRIPPED
11:05 AM - All new orders blocked
          - Position-reducing orders still allowed
15:00 PM - Operator reviews, conditions cleared
15:00 PM - Manual reset issued
15:00 PM - Quiet period starts (5 minutes)
15:05 PM - Circuit breaker returns to OPEN
```

### When TRIPPED

**Blocked:**
- ❌ New position entries (increasing abs(position))
- ❌ Signal generation (Orchestrator skips all signals)
- ❌ Automated trading via `paper_run.py`

**Allowed:**
- ✅ Position-reducing orders (closing positions)
- ✅ Query endpoints (health checks, P&L, positions)
- ✅ Manual circuit breaker reset (after verification)

**Example:**
```
Current Position: AAPL +500 shares
Circuit Breaker: TRIPPED

Order 1: Buy 100 AAPL  → BLOCKED (increases position)
Order 2: Sell 200 AAPL → ALLOWED (reduces position to +300)
Order 3: Sell 500 AAPL → ALLOWED (closes position to 0)
```

### Recovery Workflow

**1. Identify Cause**
```bash
make circuit-status

# Output:
# Circuit Breaker Status: TRIPPED
# Tripped At: 2025-10-19 11:05:00 UTC
# Reason: DAILY_LOSS_EXCEEDED
# Daily Loss: -$5,500 (limit: -$5,000)
```

**2. Verify Conditions Cleared**
- Check current P&L
- Verify no ongoing issues
- Review positions and orders

**3. Manual Reset**
```bash
make circuit-reset

# Requires:
# - Conditions normalized
# - Human verification
# - Explicit approval
```

**4. Quiet Period**
- 5 minutes of monitoring
- No trading activity
- Ensures stability before resuming

**5. Return to Normal**
- Circuit breaker automatically transitions to OPEN
- Trading resumes

---

## Drawdown

### What is Drawdown?

**Drawdown** is the peak-to-trough decline in portfolio value.

**Formula:**
```python
drawdown = (peak_equity - current_equity) / peak_equity
```

**Example:**
```
January 1:  Equity = $100,000 (starting)
February 1: Equity = $120,000 (new peak)
March 1:    Equity = $108,000 (decline)

Drawdown = ($120,000 - $108,000) / $120,000 = 10%
```

### Underwater Equity Curve

**Visualization:**
```
Equity ($)
  120k ─────●                  ← Peak (February 1)
            │ \
  115k      │  \
            │   \
  110k      │    \
            │     \
  105k      │      ●          ← Current (March 1)
            │       │
  100k ●────┘       │         ← Starting (January 1)
       │            │
       └────────────┴─ Drawdown: 10%
     Jan 1    Feb 1   Mar 1
```

**Key Metrics:**
- **Peak Equity:** $120,000 (all-time high)
- **Current Equity:** $108,000
- **Drawdown:** 10% (from peak)
- **Underwater Period:** 28 days (time below peak)

### Why Drawdown Matters

**1. Recovery Difficulty**
```python
# Losing 10% requires 11.1% gain to recover
loss = 0.10
required_gain = loss / (1 - loss) = 0.10 / 0.90 = 0.111 (11.1%)

# Losing 50% requires 100% gain to recover
loss = 0.50
required_gain = 0.50 / 0.50 = 1.00 (100%)
```

**2. Psychological Impact**
- Large drawdowns test discipline
- May trigger panic selling
- Can lead to abandoning good strategies

**3. Investor Confidence**
- Professional funds track max drawdown
- Investors evaluate risk-adjusted returns
- Drawdown history affects fundraising

### Maximum Drawdown (MDD)

**Definition:** Largest peak-to-trough decline in history

**Example:**
```
Portfolio History:
Jan: $100k (starting)
Feb: $120k (peak #1)
Mar: $108k (trough #1) → Drawdown = 10%
Apr: $125k (peak #2 - new ATH)
May: $100k (trough #2) → Drawdown = 20% ← Max Drawdown
Jun: $110k (recovering)

Maximum Drawdown = 20% (from $125k to $100k)
```

**Our Implementation:**
```python
class LossLimits(BaseModel):
    max_drawdown_pct: Decimal = Decimal("0.10")  # 10% max

# If drawdown exceeds 10%, circuit breaker trips
if current_drawdown > max_drawdown_pct:
    circuit_breaker.trip("MAX_DRAWDOWN")
```

---

## Exposure Management

### What is Exposure?

**Exposure** is the total notional value of all positions.

**Formula:**
```python
total_exposure = sum(abs(position_value) for all positions)
long_exposure = sum(position_value for long positions)
short_exposure = sum(abs(position_value) for short positions)
```

**Example:**
```
Portfolio:
AAPL: +100 shares @ $200 = +$20,000 (long)
TSLA: +50 shares @ $300 = +$15,000 (long)
MSFT: -200 shares @ $400 = -$80,000 (short)

Long Exposure:  $20,000 + $15,000 = $35,000
Short Exposure: $80,000
Total Exposure: $35,000 + $80,000 = $115,000

Net Exposure: $35,000 - $80,000 = -$45,000 (net short)
```

### Why Manage Exposure?

**Problem 1: Over-Leverage**
```
Portfolio Equity: $100,000
Total Exposure: $500,000 (5x leverage)
Risk: 1% market move = 5% portfolio impact
```

**Problem 2: Concentrated Risk**
```
Portfolio: $100,000
Single Position (AAPL): $80,000 (80%)
Risk: AAPL-specific news causes large loss
```

### Exposure Limits

**Our Implementation:**
```python
class PortfolioLimits(BaseModel):
    max_total_notional: Decimal = Decimal("100000.00")  # Max $100k total
    max_long_exposure: Decimal = Decimal("80000.00")    # Max $80k long
    max_short_exposure: Decimal = Decimal("20000.00")   # Max $20k short
```

**Example Check:**
```python
# Current state:
long_exposure = Decimal("75000.00")
short_exposure = Decimal("15000.00")

# Proposed order: Buy 100 AAPL @ $200 = +$20,000
new_long_exposure = long_exposure + Decimal("20000.00") = $95,000

# Check limit:
if new_long_exposure > max_long_exposure:  # $95k > $80k
    raise RiskViolation("Long exposure limit exceeded")
```

---

## Common Risk Scenarios

### Scenario 1: Position Limit Breach

**Setup:**
```
MAX_POSITION_SIZE = 1000 shares
Current AAPL position: 800 shares
```

**Order Attempt:**
```python
order = {
    "symbol": "AAPL",
    "side": "buy",
    "qty": 300
}

# Pre-trade check:
new_position = 800 + 300 = 1100 shares
if new_position > MAX_POSITION_SIZE:  # 1100 > 1000
    raise RiskViolation("Position limit exceeded: 1100 > 1000")
```

**Resolution:** Order BLOCKED, reduce quantity to 200 shares (max allowed).

---

### Scenario 2: Daily Loss Limit Triggered

**Setup:**
```
Daily Loss Limit: $5,000
Starting Equity (today): $100,000
```

**Timeline:**
```
09:30 AM - Open positions
10:00 AM - AAPL drops, unrealized P&L: -$2,000
11:00 AM - TSLA drops, unrealized P&L: -$3,500
11:15 AM - Total daily P&L: -$5,500

→ Circuit breaker trips automatically
→ All new orders blocked
→ Manual review required
```

**Resolution:**
1. Review positions and losses
2. Close losing positions (allowed while TRIPPED)
3. Wait for market close
4. Reset circuit breaker for next day

---

### Scenario 3: Stale Data Trading

**Setup:**
```
Market Data Staleness Threshold: 30 minutes
```

**Event:**
```
12:00 PM - Last price update received from Alpaca
12:30 PM - Network issue, no new data
12:35 PM - Risk monitor detects staleness

→ Circuit breaker trips ("DATA_STALE")
→ Cannot trade on outdated prices
```

**Resolution:**
1. Check WebSocket connection
2. Verify Alpaca API status
3. Reconnect if needed
4. Wait for fresh data
5. Reset circuit breaker

---

### Scenario 4: Max Drawdown Exceeded

**Setup:**
```
Peak Equity: $120,000
Max Drawdown: 10% ($12,000)
```

**Event:**
```
Current Equity: $107,000
Drawdown: ($120,000 - $107,000) / $120,000 = 10.8%

→ Exceeds 10% limit
→ Circuit breaker trips ("MAX_DRAWDOWN")
```

**Resolution:**
1. Review strategy performance
2. Analyze recent trades
3. Evaluate if conditions temporary or structural
4. Decision: resume trading or pause for re-evaluation
5. Manual reset required

---

## Best Practices

### 1. Conservative Limits for Live Trading

**Paper Trading:**
```python
MAX_POSITION_SIZE = 1000
DAILY_LOSS_LIMIT = Decimal("5000.00")
MAX_DRAWDOWN = Decimal("0.10")  # 10%
```

**Live Trading (start smaller):**
```python
MAX_POSITION_SIZE = 100  # 10x smaller
DAILY_LOSS_LIMIT = Decimal("500.00")  # 10x smaller
MAX_DRAWDOWN = Decimal("0.05")  # 5% (2x stricter)
```

### 2. Test Circuit Breaker Regularly

```bash
# Manually trip to verify workflow
make circuit-trip

# Verify trading blocked
make paper-run  # Should fail with circuit breaker error

# Practice reset procedure
make circuit-reset
```

### 3. Monitor Risk Metrics Daily

**Daily Checklist:**
- [ ] Peak equity updated
- [ ] Current drawdown calculated
- [ ] Daily P&L tracked
- [ ] Position limits verified
- [ ] Circuit breaker status checked

### 4. Log All Risk Violations

**Even if not blocking, log for analysis:**
```python
# Order allowed but close to limit
if new_position > 0.8 * MAX_POSITION_SIZE:
    logger.warning(f"Position near limit: {new_position}/{MAX_POSITION_SIZE}")
    db.log_risk_violation(
        violation_type="position_warning",
        symbol=symbol,
        blocked=False
    )
```

### 5. Gradual Limit Increases

**Never jump limits drastically:**
```python
# BAD: 10x increase overnight
MAX_POSITION_SIZE = 100  # Week 1
MAX_POSITION_SIZE = 1000 # Week 2 (too aggressive!)

# GOOD: Gradual increase with validation
MAX_POSITION_SIZE = 100  # Week 1
MAX_POSITION_SIZE = 200  # Week 4 (after validation)
MAX_POSITION_SIZE = 500  # Week 8 (after validation)
```

### 6. Separate Limits for Paper vs Live

**Use environment variables:**
```bash
# .env.paper
RISK_MAX_POSITION_SIZE=1000
RISK_DAILY_LOSS_LIMIT=5000

# .env.live
RISK_MAX_POSITION_SIZE=100
RISK_DAILY_LOSS_LIMIT=500
```

---

## Summary

**Key Takeaways:**

1. **Risk Management is Critical** - Protects capital and enables longevity
2. **Position Limits** - Prevent concentration risk
3. **Loss Limits** - Stop catastrophic losses early
4. **Circuit Breakers** - Automatic safety mechanism
5. **Drawdown** - Track decline from peak equity
6. **Exposure** - Monitor total notional risk
7. **Test Regularly** - Practice circuit breaker recovery
8. **Start Conservative** - Gradually increase limits with validation

**Remember:** "Rule #1: Don't lose money. Rule #2: Don't forget Rule #1." - Warren Buffett

---

## Further Reading

- [ADR-0011: Risk Management System](../ADRs/0011-risk-management-system.md) - Technical architecture
- [Implementation Guide: P1T7](../TASKS/P1T7_DONE.md) - Step-by-step guide
- [Operational Runbook](../RUNBOOKS/ops.md) - Deployment and troubleshooting procedures
- [CLAUDE.md](../../CLAUDE.md) - Risk management patterns (Circuit Breakers section)

> **Note:** Circuit Breaker Recovery runbook (TODO: create dedicated runbook)

---

**Last Updated:** 2025-10-19
**Related Task:** P1.2T3 - Risk Management System
