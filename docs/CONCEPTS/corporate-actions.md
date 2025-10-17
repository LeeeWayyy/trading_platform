# Corporate Actions

## Plain English Explanation

A **corporate action** is when a company does something that changes the value or quantity of its stock. The two most common types are:

1. **Stock Splits**: One share becomes multiple shares
   - Example: In a 4-for-1 split, your 100 shares become 400 shares
   - The total value stays the same (price divides by 4)

2. **Dividends**: Company pays cash to shareholders
   - Example: $2 dividend per share
   - On the ex-dividend date, stock price typically drops by the dividend amount

## Why It Matters

If you don't adjust for corporate actions in your backtests, you'll see **fake price movements** that never actually happened:

### Real-World Example: Apple's 4-for-1 Split

**What Really Happened:**
- **Aug 30, 2020** (before split): AAPL traded at ~$500/share
- **Aug 31, 2020** (after split): AAPL opened at ~$125/share
- **Shareholders**: If you owned 100 shares worth $50,000, you now owned 400 shares worth $50,000

**Without Adjustment (WRONG):**
```
Date         Price    Daily Change
2020-08-28   $499.23   +2.4%
2020-08-31   $124.81   -75.0%  ← FAKE CRASH!
2020-09-01   $134.18   +7.5%
```

Your backtest would think AAPL crashed 75% overnight, triggering stop-losses, panic selling signals, etc.

**With Adjustment (CORRECT):**
```
Date         Adj Price  Daily Change
2020-08-28   $124.81     +2.4%
2020-08-31   $124.81      0.0%   ← No change!
2020-09-01   $134.18     +7.5%
```

Now the data reflects actual investor experience: no sudden loss.

## Common Pitfalls

### Pitfall #1: Using Unadjusted Prices for Backtesting
**Problem**: Backtests show fake losses/gains at every split.

**Solution**: Always use adjusted prices for strategy research.

**When to use unadjusted**: Only for intraday trading where you care about the actual live price.

### Pitfall #2: Missing Ex-Dividend Dates
**Problem**: Your backtest doesn't account for the cash you'd receive.

**Example**:
- Stock trades at $100
- $2 dividend declared
- Ex-dividend date: tomorrow
- Tomorrow stock opens at $98 (market adjusts automatically)
- **You received $2 cash** but your backtest only sees a $2 "loss"

**Solution**: Subtract dividends from historical close prices AND track the cash received.

### Pitfall #3: Forward-Looking Bias
**Problem**: Using post-split prices to make pre-split decisions.

**Example**:
```python
# WRONG: Uses future knowledge
if today == "2020-08-30":
    # This uses post-split adjusted prices retroactively
    signals = compute_momentum(all_historical_data_adjusted)
```

**Solution**: Only adjust history UP TO the date you're trading. Don't adjust data after that date.

### Pitfall #4: Forgetting Volume Adjustment
**Problem**: After a split, volume looks abnormally low if not adjusted.

**Rule**:
- Prices: divide by split ratio
- Volume: multiply by split ratio

**Example** (4-for-1 split):
```python
pre_split_volume = 1_000_000
post_split_volume = 4_000_000  # 4x more shares traded
# Adjusted: both should normalize to same level
```

## Examples

### Example 1: Stock Split Adjustment

**Given**: 4-for-1 split on 2020-08-31

**Before Adjustment (raw data)**:
| Date       | Open | High | Low  | Close | Volume    |
|------------|------|------|------|-------|-----------|
| 2020-08-28 | 495  | 500  | 490  | 499   | 1,000,000 |
| 2020-08-31 | 120  | 130  | 118  | 125   | 4,500,000 |
| 2020-09-01 | 126  | 137  | 124  | 134   | 3,800,000 |

**After Adjustment** (divide prices by 4, multiply volume by 4):
| Date       | Adj Open | Adj High | Adj Low | Adj Close | Adj Volume |
|------------|----------|----------|---------|-----------|------------|
| 2020-08-28 | 123.75   | 125.00   | 122.50  | 124.75    | 4,000,000  |
| 2020-08-31 | 120.00   | 130.00   | 118.00  | 125.00    | 4,500,000  |
| 2020-09-01 | 126.00   | 137.00   | 124.00  | 134.00    | 3,800,000  |

Now you can see the real price movement: slight dip then rally.

### Example 2: Dividend Adjustment

**Given**: $2 dividend, ex-date 2024-01-15

**Before Adjustment**:
| Date       | Close |
|------------|-------|
| 2024-01-12 | 150   |
| 2024-01-15 | 148   | ← Ex-dividend date
| 2024-01-16 | 149   |

**After Adjustment** (subtract $2 from all pre-ex-date closes):
| Date       | Adj Close | Note                        |
|------------|-----------|------------------------------|
| 2024-01-12 | 148       | Adjusted down by $2          |
| 2024-01-15 | 148       | No change (is ex-date)       |
| 2024-01-16 | 149       | No adjustment (post-ex-date) |

This shows the true price movement independent of the dividend payout.

## Our Implementation

### Adjustment Strategy

We use the **backwards adjustment** method:
1. Start from the most recent date (today)
2. Work backwards through history
3. For each corporate action:
   - **Split**: Divide all prior prices by split ratio, multiply volume
   - **Dividend**: Subtract cumulative dividend from all prior closes

This ensures current prices match live market prices.

### Quality Gate Integration

Our system detects abnormal price movements and checks for corporate actions:

```python
# Pseudo-code
for each trading day:
    daily_return = (close - prev_close) / prev_close

    if abs(daily_return) > 0.30:  # 30% threshold
        if has_corporate_action(symbol, date):
            # OK: large move explained by split/dividend
            pass
        else:
            # ERROR: suspicious move, quarantine this data
            quarantine(symbol, date, daily_return)
```

This prevents bad data (errors, halts, glitches) from corrupting our models.

## Further Reading

### Official Sources
- [Investopedia: Corporate Actions](https://www.investopedia.com/terms/c/corporateaction.asp)
- [Investopedia: Stock Splits](https://www.investopedia.com/terms/s/stocksplit.asp)
- [Investopedia: Dividends](https://www.investopedia.com/terms/d/dividend.asp)

### Data Provider Docs
- [CRSP Data Adjustment Methodology](https://www.crsp.org/)
- [Nasdaq Corporate Actions Calendar](https://www.nasdaq.com/market-activity/corporate-actions)

### Academic Papers
- Fama, E. F., & French, K. R. (1992). "The Cross-Section of Expected Stock Returns" - discusses adjustment methodology
- Liu, W. (2006). "A liquidity-augmented capital asset pricing model" - importance of volume adjustment

### Industry Standards
- [ISO 15022](https://www.iso.org/standard/45634.html) - Corporate actions messaging standard
- [FINRA Corporate Actions](https://www.finra.org/rules-guidance/key-topics/corporate-actions)

## Glossary

- **Ex-Dividend Date**: First day stock trades without dividend included; sellers keep the dividend
- **Record Date**: Date you must own stock to receive dividend (usually 2 days after ex-date)
- **Split Ratio**: How many new shares per old share (4:1 = four new shares for each old one)
- **Reverse Split**: Opposite of split; reduces share count, increases price (1:4 = one new share for four old ones)
- **Special Dividend**: One-time large dividend (vs. regular quarterly dividend)
- **Cumulative Adjustment**: Adjusting for all corporate actions from present back to start of data

## Testing Our Implementation

See `/docs/IMPLEMENTATION_GUIDES/t1-data-etl.md` for:
- Test data with known splits
- Expected adjustment calculations
- Validation procedures
