---
id: P6T9
title: "Professional Trading Terminal - Cost Model & Capacity"
phase: P6
task: T9
priority: P1
owner: "@development-team"
state: IMPLEMENTATION
created: 2026-01-13
updated: 2026-01-29
dependencies: [P5]
related_adrs: [ADR-0031-nicegui-migration, ADR-0034-cost-model-architecture]
related_docs: [P6_PLANNING.md]
features: [T9.1, T9.2, T9.3, T9.4]
---

# P6T9: Cost Model & Capacity

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** PLANNING
**Priority:** P1 (Research Platform)
**Owner:** @development-team
**Created:** 2026-01-13
**Updated:** 2026-01-29
**Track:** Track 9 of 18
**Dependency:** P5 complete

---

## Objective

Add realistic cost simulation to backtests and capacity analysis for strategy sizing.

**Success looks like:**
- Transaction costs in backtests (gross vs net P&L)
- Cost model configurable via UI
- Capacity analysis for strategy sizing
- Backtest data export for external verification

---

## Tasks (4 in scope)

### T9.1: Transaction Cost Model - HIGH PRIORITY

#### AUM and Trade Sizing Definition

**Portfolio Value (AUM) Input:**
- New top-level config field: `portfolio_value_usd: float = 1_000_000.0`
- Stored in dedicated DB column (NOT in `extra_params` to avoid duplication)
- Default: $1,000,000 (typical backtest assumption)
- Range: $10,000 to $1,000,000,000

**Backtest AUM Convention: CONSTANT NOTIONAL**

This cost model assumes **constant-notional** backtesting:
- Portfolio value is fixed at `portfolio_value_usd` throughout the backtest
- Weights represent fractions of the fixed AUM (not compounding)
- Trade sizes and cost drag are computed against the same fixed AUM
- This matches the typical research backtest convention (returns are analyzed, not dollar values)

**Note:** If the backtester uses compounding returns, cost drag will be slightly understated
for positive returns and overstated for negative returns. For most research purposes
(sub-20% annual returns), this approximation error is <2% of costs.

**Trade Size Calculation (Constant Notional):**
```
trade_value_usd[symbol, date] = |weight_change[symbol, date]| × portfolio_value_usd
```

Where:
- `weight_change` = current weight - previous weight
- **First day (D=0)**: Previous weight is assumed to be 0 (starting from cash)
  - This means initial portfolio build trades ARE costed
  - Example: If w[AAPL, D=0] = 0.10, then Δw = 0.10 - 0 = 0.10, costing 10% of AUM
- Absolute value because costs apply to both buys and sells
- `portfolio_value_usd` = fixed AUM (constant throughout backtest)

**Cost Application to Returns:**
```
cost_drag[date] = total_cost_usd[date] / portfolio_value_usd  # ALWAYS POSITIVE
net_return[date] = gross_return[date] - cost_drag[date]       # Subtraction reduces return
```

**Sign Convention:** `cost_drag` is ALWAYS stored as a positive number representing the
drag on returns. It is subtracted from gross return to get net return. In exports,
`cost_drag` is shown as positive (the amount by which returns are reduced).

**Net Metrics Computation:**

Net metrics are computed from the net return series, not by subtracting aggregate costs:

```python
# net_total_return: Compounded product of daily net returns
# Guard: Return None if no valid returns (empty series or all NaN)
valid_returns = [r for r in net_return if math.isfinite(r)]
if len(valid_returns) == 0:
    net_total_return = None
else:
    net_total_return = Π(1 + r) - 1  # for all r in valid_returns

# net_sharpe: Sharpe ratio computed on net return series (same formula as gross)
# Guard: Return None if insufficient data (n < 2) or zero/near-zero std
# Note: n >= 2 is the mathematical minimum for std calculation. For meaningful
# Sharpe ratios, longer periods are recommended (e.g., n >= 20 trading days).
# Short backtests may produce unstable Sharpe ratios; users should interpret
# with appropriate caution. We use the minimal threshold to allow the formula
# to compute when mathematically valid, leaving interpretation to the user.
# IMPORTANT: Use valid_returns (finite-filtered) to avoid NaN propagation
MIN_STD_FOR_SHARPE = 1e-10  # Prevent division by zero/inf
if len(valid_returns) < 2 or std(valid_returns) < MIN_STD_FOR_SHARPE:
    net_sharpe = None
else:
    net_sharpe = mean(valid_returns) / std(valid_returns) * sqrt(252)

# net_max_drawdown: Maximum drawdown computed on cumulative net returns
# Guard: Return None if no valid returns (empty series or all NaN)
if len(valid_returns) == 0:
    net_max_drawdown = None
else:
    net_cumulative = cumprod(1 + r for r in valid_returns)
    net_max_drawdown = min(net_cumulative / running_max(net_cumulative) - 1)
```

**Important:** Do NOT compute net_total_return as `gross_total_return - total_cost_drag`,
as this ignores the compounding interaction between daily costs and returns.

#### Trade Timing and Execution Model

**Weight Convention (Target Weights at Close):**

Weights `w[symbol, D]` represent TARGET portfolio weights at Day D close.
- `w[symbol, D-1]` = position held at D-1 close (start of period)
- `w[symbol, D]` = target position at D close (end of period)
- Trade = change in position to reach target

**Close-to-Close Execution Pipeline:**

**IMPORTANT: Alignment with SimpleBacktester Forward-Return Convention**

SimpleBacktester computes *forward returns* for an `as_of_date`:
- `forward_return[D]` = return from D close to D+1 close
- This return is labeled with date D in the output

Cost application must align with this convention:
- Weights `w[D]` represent target portfolio at D close (after signal generation)
- Trade is executed to reach `w[D]` sometime during D or at D close
- The forward return `D → D+1` is the return earned on position `w[D]`
- Costs for reaching `w[D]` are applied to the same row (date D)

```
Day D-1 Close: Position = w[D-1]
Day D:         Signal generated, trade executed to reach w[D]
Day D Close:   Position = w[D]
Day D+1 Close: Position valued; forward_return[D] = (D+1 close / D close) - 1

forward_return[D]:  Return from D close to D+1 close (backtester computes this)
cost_drag[D]:       Cost of trades to reach w[D] (applied same row as forward_return[D])
net_return[D]:      forward_return[D] - cost_drag[D]

ADV/Vol for D trade: Use data from D-1 (LAGGED to avoid lookahead)
```

**Date Alignment Rule:** Costs are applied to the same date row as the forward return.
Both represent the position `w[D]` held from D close to D+1 close.

**Terminal Liquidation:** The final day's rebalance (to exit positions) is NOT costed
because there is no D+1 return. This is standard for research backtests which assume
continuous operation. For "round trip" backtests requiring terminal liquidation costs,
users should manually add exit costs or extend the backtest period by one day.

**Cost Computation Date Alignment:**

Costs MUST be computed only on dates with valid gross returns (`backtest_dates`):

```python
# backtest_dates = dates where gross_return[D] is not null
# This typically excludes the last day (no D+1 return) and any gaps

# Cost computation is aligned to these dates:
# - Weight changes are computed only for dates in backtest_dates
# - ADV/vol are loaded for these dates (with D-1 lag)
# - Net returns are produced only for backtest_dates

# This ensures:
# 1. Net return series aligns exactly with gross return series
# 2. No skew from computing costs on dates without corresponding returns
```

**Weight Change Gap Policy:**

When computing `Δw[D] = w[D] - w[D-1]`, gaps in the date series must be handled:

```python
# RULE: Use the most recent available weight for w[D-1]
# - If D-1 is in backtest_dates: use w[D-1] directly
# - If D-1 is NOT in backtest_dates (gap): use w[most_recent_valid_date]
# - If no prior date exists (D is first date): use 0 (cash position)

# Implementation: Reindex weights to backtest_dates with forward-fill
weights_aligned = weights.reindex(backtest_dates).ffill()
# Fill ANY remaining NaNs to 0 (symbols appearing later, or missing early data)
# This ensures symbols not yet in portfolio have weight=0 (cash)
weights_aligned = weights_aligned.fillna(0)
delta_weights = weights_aligned.diff()
delta_weights.iloc[0] = weights_aligned.iloc[0]  # First day: full weight is the trade

# Example with gap:
# backtest_dates = [D1, D2, D5, D6]  (D3, D4 missing)
# w[D5] uses w[D2] as prior (forward-fill across gap)
# Δw[D5] = w[D5] - w[D2]  (captures accumulated change over gap)
```

This policy ensures that:
1. All weight changes are captured even across date gaps
2. Costs for rebalancing after a gap reflect the full position change
3. The sum of Δw across all dates equals final weight minus initial (cash)

**Cost Application Step-by-Step:**

1. **Input**: Gross portfolio returns from PITBacktester/SimpleBacktester
2. **Compute Weight Changes**: `Δw[symbol,D] = w[symbol,D] - w[symbol,D-1]`
3. **Compute Trade Values (USD)**: `Q[symbol,D] = |Δw[symbol,D]| × AUM`
4. **Load Lagged Data**: ADV and Vol are already lagged in the loader (see `load_pit_adv_volatility`)
   - **IMPORTANT: Do NOT apply additional lag in cost application pipeline**
   - The loader outputs `adv_usd` and `volatility` columns that are ALREADY shifted by 1 day
   - Cost pipeline receives pre-lagged data keyed by trade date D
5. **Per-Symbol Costs** (for each symbol with |Δw| > 0):
   - Commission (USD): `max(Q × commission_bps / 10000, min_commission_usd)`
   - Spread (USD): `Q × (spread_bps / 2) / 10000`  ← half-spread per side
   - Impact (USD): `Q × eta × sigma × sqrt(Q / ADV)`
6. **Sum Daily Costs (USD)**: `total_cost_usd[D] = Σ costs[symbol,D]`
7. **Convert to Return Drag**: `cost_drag[D] = total_cost_usd[D] / AUM`
8. **Apply to Returns with Guard**:
   ```python
   net_return[D] = gross_return[D] - cost_drag[D]

   # GUARD: Prevent net_return <= -1 which breaks compounding
   # This can happen with extreme costs (e.g., low ADV fallback + high impact)
   MIN_NET_RETURN = -0.9999  # Cap at 99.99% daily loss

   if net_return[D] <= -1.0:
       logger.warning(
           "extreme_cost_capped",
           date=D,
           gross_return=gross_return[D],
           cost_drag=cost_drag[D],
           capped_net_return=MIN_NET_RETURN,
       )
       net_return[D] = MIN_NET_RETURN  # Clamp to allow compounding
   ```

**Cost Totals vs Clamped Net Returns:**

When net returns are clamped, the stored `total_cost_usd` and `cost_drag` remain at their
actual computed values (not clamped). This means:
- `cost_summary.total_cost_usd`: Actual total cost computed (may exceed what's reflected in net returns)
- `net_return[D]`: Clamped value used for compounding and metrics
- `cost_breakdown.parquet`: Contains raw (unclamped) cost values for transparency

This ensures cost data is accurate for verification while preventing invalid compounding.
The `extreme_cost_capped` log entry identifies days where clamping occurred.

**Cost Formulas (All in USD, Single-Trip per Trade):**

```python
# Commission: minimum fee or percentage, whichever is greater
# TRADE GRANULARITY: One "trade" per symbol per day, derived from net Δw
# - trade_value = |w[D] - w[D-1]| × AUM (net position change)
# - min_commission_usd applies per (symbol, day) pair
# - This is a simplification: real execution may split into multiple orders
# - For capacity analysis, this one-trade-per-symbol-day model is sufficient
commission_usd = max(trade_value * commission_bps / 10000, min_commission_usd)

# Spread: half bid-ask spread (one-way crossing cost)
# Note: spread_bps is the FULL spread; we pay half on each trade
spread_usd = trade_value * (spread_bps / 2) / 10000

# Impact: Almgren-Chriss square root model
# eta = permanent impact coefficient (default 0.1)
# sigma = daily volatility (decimal, e.g., 0.02 for 2%)
# Q = trade value in USD, ADV = average daily volume in USD
impact_usd = trade_value * eta * sigma * sqrt(trade_value / ADV)
```

**Spread Convention Clarified:**
- `spread_bps` = FULL bid-ask spread (e.g., 5 bps for 0.05% full spread)
- Each trade crosses half the spread: `spread_bps / 2` per side
- Round-trip cost = full `spread_bps` (half on entry + half on exit)

**Impact Convention Clarified:**
- Impact is applied once per trade direction (buy OR sell)
- Impact is NOT doubled for the same trade

#### PIT Compliance for ADV/Volatility

**Window Definitions (EXACT):**

| Metric | Window | Definition | Lag |
|--------|--------|------------|-----|
| ADV (Average Daily Volume) | 20 trading days | `mean(price × volume)` over window | D-1 (use yesterday's ADV for today's trade) |
| Volatility (σ) | 20 trading days | `std(daily_returns, ddof=1)` over window | D-1 (use yesterday's vol for today's trade) |

**Rolling Statistics Parameters:**
- `ddof=1` (sample standard deviation, consistent with pandas default)
- `min_periods=20` (require 20 valid observations for valid result)
- **NaN handling in Polars rolling functions:**
  - Polars `rolling_mean`/`rolling_std` count non-null values toward `min_periods`
  - A 20-row window with 15 nulls and 5 valid values → result is null (need 20 valid)
  - A 20-row window with 0 nulls and 20 valid values → result is computed
  - This means: windows require 20 *valid* observations within 20 consecutive rows
  - If data has >20% null rate, most windows will hit fallback
  - This is intentional: sparse data should use conservative fallback values

**Trading Days vs Calendar Days Note:**
The rolling window operates on **rows** (trading days in the data), not calendar days.
For most liquid symbols, this correctly gives 20 consecutive trading days.
For sparse symbols (e.g., thinly traded), the 20-row window may span more calendar time.
This is acceptable because:
1. The fallback policy handles symbols with insufficient data
2. The 40-calendar-day lookback provides buffer for most cases
3. Symbols with >20% missing data rate will use fallback values anyway
For stricter enforcement, implementers may optionally filter to symbols with
sufficient trading day coverage before rolling, but this is not required.

**Calendar Lookback:**
- Use 40 calendar days for 20 trading days (accounts for weekends, holidays)
- Formula: `lookback_calendar_days = trading_days_needed * 2`
- **Sparse Data Handling:** If fewer than 20 valid observations exist after dropping nulls,
  the rolling window will produce null (triggering fallback). This is acceptable because:
  - Illiquid symbols with sparse data should use conservative fallback values
  - The fallback counts are tracked and surfaced to users for transparency
  - Extending lookback further risks using stale data that doesn't reflect current liquidity

**Example:**
- For a trade on Day D (executed during Day D session):
  - ADV[D] = mean(price × volume) for Days [D-20, D-1] inclusive (20 trading days ending D-1)
  - Vol[D] = std(returns) for Days [D-20, D-1] inclusive (20 trading days ending D-1)

**Snapshot Integration:**

ADV and volatility are derived from the same price/volume data used for returns.
For PIT backtests, this data comes from snapshot-locked CRSP provider.

```python
ADV_WINDOW_DAYS = 20  # 20 trading days
VOL_WINDOW_DAYS = 20  # 20 trading days

def load_pit_adv_volatility(
    snapshot: SnapshotManifest,
    crsp_provider: CRSPLocalProvider,
    permnos: list[int],
    permno_to_symbol: dict[int, str],  # Mapping from permno to symbol (ticker)
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Load ADV/volatility from snapshot-locked CRSP data.

    Uses same PIT-compliant data path as returns, ensuring reproducibility.

    Args:
        permno_to_symbol: Mapping from CRSP permno to symbol (ticker).
            This should come from the same snapshot's symbol mapping to ensure consistency.

    Returns:
        DataFrame with columns [permno, symbol, date, adv_usd, volatility]
        Both metrics are LAGGED by 1 day to avoid lookahead bias.

    IMPORTANT - Join Key Precedence:
        - For PIT backtests: Use `permno` as the primary join key (weights/returns are permno-keyed)
        - `symbol` is included for display/export purposes only
        - This ensures correct cost alignment even when symbols change during the backtest period
    """
    # Extra lookback for rolling window + lag (use 2× calendar days for safety)
    lookback_calendar_days = ADV_WINDOW_DAYS * 2  # 40 days for 20 trading days

    pit_data = crsp_provider.get_daily_data_pit(
        snapshot_id=snapshot.version_tag,
        start_date=start_date - timedelta(days=lookback_calendar_days),
        end_date=end_date,
        permnos=permnos,
        columns=["permno", "date", "prc", "vol", "ret"],
    )

    # CRITICAL: Sort by date within each permno for correct rolling computation
    pit_data = pit_data.sort(["permno", "date"])

    # Compute dollar volume for ADV (handle null prices/volumes)
    pit_data = pit_data.with_columns(
        pl.when(pl.col("prc").is_not_null() & pl.col("vol").is_not_null())
        .then(pl.col("prc").abs() * pl.col("vol"))
        .otherwise(None)
        .alias("dollar_vol")
    )

    # EXPLICIT NULL HANDLING: Filter out rows with null returns before volatility calc
    # Create separate column for volatility computation with nulls dropped
    pit_data = pit_data.with_columns(
        pl.when(pl.col("ret").is_not_null())
        .then(pl.col("ret"))
        .otherwise(None)
        .alias("ret_clean")
    )

    # Compute rolling ADV and volatility per permno
    #
    # ═══════════════════════════════════════════════════════════════════════════
    # IMPLEMENTATION NOTE - POLARS VERSION COMPATIBILITY
    # ═══════════════════════════════════════════════════════════════════════════
    # The project uses Polars ^1.0.0. This pseudocode shows REQUIRED BEHAVIOR.
    # The implementer MUST consult Polars documentation for correct syntax:
    # - Polars 1.0+ docs: https://docs.pola.rs/api/python/stable/reference/
    #   (Note: "stable" redirects to latest; check pyproject.toml for pinned version)
    # - Key methods to verify: rolling(), group_by_dynamic(), over()
    #
    # REQUIRED BEHAVIOR (implement using correct Polars 1.0+ syntax):
    # 1. Sort by (permno, date) before rolling
    # 2. For each permno: 20-row rolling mean of dollar_vol -> adv_usd_raw
    # 3. For each permno: 20-row rolling std of ret_clean (ddof=1) -> volatility_raw
    # 4. min_periods=20 (null if insufficient data)
    # 5. Lag by 1 day (shift) to get D-1 values for D trades
    #
    # EXAMPLE APPROACHES (verify syntax against Polars docs):
    # - Approach A: .rolling(window_size=20, min_periods=20).over("permno")
    # - Approach B: group_by("permno").map_groups() with custom rolling
    # - Approach C: Use pl.rolling_corr patterns from Polars examples
    # ═══════════════════════════════════════════════════════════════════════════
    #
    # PSEUDOCODE - LOGIC ONLY (implementer adapts to actual API):
    result = (
        pit_data
        .sort(["permno", "date"])
        # Step 1: Compute rolling statistics per permno
        # [Implementer: Consult Polars 1.0+ docs for per-group rolling operations]
        # Recommended approaches to investigate:
        #   1. pl.col("dollar_vol").rolling_mean(window_size=20, min_periods=20).over("permno")
        #   2. .group_by("permno").agg(pl.col("dollar_vol").rolling_mean(...))
        #   3. .with_columns(pl.col("dollar_vol").rolling_mean(...).over("permno"))
        # Key requirements: 20-row window, min_periods=20, per-permno partitioning
        # Result columns: adv_usd_raw (rolling mean), volatility_raw (rolling std, ddof=1)
        #
        # Step 2: Lag by 1 day (this part is valid Polars syntax)
        .with_columns([
            pl.col("adv_usd_raw").shift(1).over("permno").alias("adv_usd"),
            pl.col("volatility_raw").shift(1).over("permno").alias("volatility"),
        ])
        .select(["permno", "date", "adv_usd", "volatility"])
    )

    # Add symbol column by joining with permno_to_symbol mapping
    symbol_df = pl.DataFrame({
        "permno": list(permno_to_symbol.keys()),
        "symbol": list(permno_to_symbol.values()),
    })
    result = result.join(symbol_df, on="permno", how="left")

    return result.select(["permno", "symbol", "date", "adv_usd", "volatility"])
```

**Dataset Version Recording:**

Extend `dataset_version_ids` in BacktestResult with standardized cost data keys:

```python
# For PIT backtests using CRSP:
dataset_version_ids = {
    "returns_provider": "crsp",           # Provider for returns data
    "returns_version": "v2025.01.15",     # Version of returns data
    "fundamentals_provider": "compustat", # Provider for fundamentals (if used)
    "fundamentals_version": "v2025.01.15",
    # Cost data fields (always present when cost model enabled)
    "cost_data_source": "crsp",           # Provider for ADV/volatility
    "cost_data_version": "v2025.01.15",   # Version of cost data
}

# For non-PIT backtests using Yahoo Finance:
# VERSION FORMAT: YYYY-MM-DD of the run date (when data was fetched)
# This ensures reproducibility by recording when the live fetch occurred.
# If using cached files, use the cache file's creation timestamp instead.
dataset_version_ids = {
    "returns_provider": "yfinance",       # Provider for returns data
    "returns_version": "2026-01-29",      # Run date (YYYY-MM-DD format)
    # Cost data fields (same structure)
    "cost_data_source": "yfinance",
    "cost_data_version": "2026-01-29",    # Run date (YYYY-MM-DD format)
}
```

**Standardized Key Structure:**
- `*_provider`: Data source name (lowercase)
- `*_version`: Version tag or cache date
- All providers use the same key pattern for consistency
- `cost_data_source` and `cost_data_version` are only present when cost model is enabled

#### Fallback Policy for Missing ADV/Volatility

**Deterministic Fallback Behavior:**

```python
import math

# Fallback floor values (conservative to avoid understating costs)
# $100K ADV floor: ~10th percentile of S&P 500 daily dollar volume
# 1% vol floor: typical annualized vol of ~16% for stable large-caps
ADV_FLOOR_USD = 100_000  # $100K minimum ADV
VOL_FLOOR = 0.01  # 1% daily volatility minimum

def get_adv_with_fallback(adv_raw: float | None, symbol: str) -> tuple[float, bool]:
    """Get ADV with deterministic fallback.

    Handles None, non-positive, NaN, and inf values.

    Returns:
        (adv_value, used_fallback)
    """
    if adv_raw is None or not math.isfinite(adv_raw) or adv_raw <= 0:
        logger.warning("adv_fallback_used", symbol=symbol, fallback=ADV_FLOOR_USD)
        return ADV_FLOOR_USD, True
    return adv_raw, False

def get_volatility_with_fallback(vol_raw: float | None, symbol: str) -> tuple[float, bool]:
    """Get volatility with deterministic fallback.

    Handles None, non-positive, NaN, and inf values.
    """
    if vol_raw is None or not math.isfinite(vol_raw) or vol_raw <= 0:
        logger.warning("volatility_fallback_used", symbol=symbol, fallback=VOL_FLOOR)
        return VOL_FLOOR, True
    return vol_raw, False
```

**Fallback Statistics in Result:**
```python
@dataclass
class CostSummaryDB:
    ...
    adv_fallback_count: int = 0
    volatility_fallback_count: int = 0
```

**Fallback Application Order:**

Fallback counting MUST be applied to the **lagged** values used for cost computation:

```
1. Raw data: rolling ADV/vol computed (may have nulls)
2. Lagging: shift by 1 day (D-1 values for D trades)
3. Fallback: apply floor values to null/zero lagged values ← COUNT HERE
4. Cost computation: use fallback-applied lagged values
```

The fallback counts represent the number of (symbol, date) pairs where the lagged
ADV/volatility used for cost computation was unavailable and replaced with a floor value.
This matches what actually affects costs and participation violations.

**Participation Violations Definition:**

A participation violation occurs when a trade exceeds the ADV participation limit:

```python
def count_participation_violations(
    trades: pl.DataFrame,  # columns: [date, symbol, trade_value, adv_usd]
    adv_participation_limit: float,  # e.g., 0.05 for 5%
) -> int:
    """Count trades that exceed ADV participation limit.

    A violation is counted per-symbol per-day when:
        trade_value / adv_usd > adv_participation_limit

    Returns:
        Total count of (symbol, date) pairs with violations.

    Note:
        This function expects fallback values to have been applied BEFORE calling.
        The `adv_usd` column should contain either actual ADV or the fallback floor.
        Trades using fallback ADV ARE counted in violations (conservative approach).
        Null/zero ADV after fallback application indicates a data error and is skipped.
    """
    # Filter out any remaining null/zero ADV (should be rare after fallback)
    valid_trades = trades.filter(
        (pl.col("adv_usd").is_not_null()) & (pl.col("adv_usd") > 0)
    )
    violations = valid_trades.filter(
        (pl.col("trade_value") / pl.col("adv_usd")) > adv_participation_limit
    )
    return violations.height  # Number of rows = number of violations
```

**Stored in cost_summary:**
```json
{
  "participation_violations": 3  // Count of (symbol, date) pairs exceeding limit
}
```

#### Job Idempotency and Config Storage

**Cost config in job_id hash:**

```python
@dataclass
class BacktestJobConfig:
    ...
    cost_model: CostModelConfig | None = None
    portfolio_value_usd: float = 1_000_000.0

    def compute_job_id(self, created_by: str) -> str:
        content = json.dumps({
            "alpha": self.alpha_name,
            "start": str(self.start_date),
            "end": str(self.end_date),
            "weight": self.weight_method.value,
            "provider": self.provider.value,
            "params": self.extra_params,
            "created_by": created_by,
            # NEW: Include cost config in hash
            "cost_model": self.cost_model.to_dict() if self.cost_model else None,
            # ONLY include portfolio_value_usd when cost model is enabled
            # This ensures disabled runs produce same job_id as no-cost runs
            # NOTE: When cost model is disabled, AUM is irrelevant to results.
            # The validation still runs but the value is not included in hash.
            "portfolio_value_usd": self.portfolio_value_usd if self.cost_model else None,
        }, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:32]
```

**Config Storage Precedence:**
- `cost_model_config` column: Canonical cost config (JSONB)
- `config_json["cost_model"]`: Not used (avoid duplication)
- Worker reads from `cost_model_config` column exclusively

**Enabled/Disabled Semantics:**

**CANONICAL RULE: Presence = Enabled. Always.**

The cost model uses a **presence-only** rule to determine if costs are computed:

```python
# RULE: cost_model_config presence determines behavior
# - cost_model_config = None  →  costs disabled
# - cost_model_config != None →  costs enabled

# DO NOT use enabled=false; if present, config MUST have enabled=true
# Server-side validation MUST reject configs with enabled=false

# UI submit logic:
def prepare_job_config(form_data: dict) -> BacktestJobConfig:
    if not form_data.get("cost_enabled", False):
        cost_model = None  # Disabled = null (canonical)
    else:
        cost_model = CostModelConfig(enabled=True, ...)  # enabled=True always
    return BacktestJobConfig(..., cost_model=cost_model)

# Server-side validation (in worker):
def validate_cost_config(config: CostModelConfig | None) -> None:
    if config is not None and not config.enabled:
        raise ValueError("cost_model_config.enabled must be True when config is present")

# Worker logic:
def should_compute_costs(job: BacktestJob) -> bool:
    return job.cost_model_config is not None  # Presence check only

# UI display logic:
if result.cost_model_config:  # Presence check, NOT .enabled
    show_cost_section()
```

**Why this rule:** Eliminates ambiguity between "config with enabled=false" and "no config".
The `enabled` field exists for schema versioning but is always True when config is present.

#### Result Storage and Reconstruction

**Canonical Source: summary.json (File Storage)**

The summary.json file is the **canonical source** for cost results:
- Written by worker after backtest completion
- Read by result_storage.py for reconstruction
- DB columns (`gross_total_return`, `net_total_return`, etc.) are **derived copies** for query convenience

**Sync Direction:** summary.json → DB (write once, no bidirectional sync needed)

**summary.json Extension:**

```json
{
  "job_id": "abc123def456789012345678901234ab",
  "created_by": "user_uuid_12345678",  // User ID (matches auth system's user_id field)
  "mean_ic": 0.045,
  "icir": 2.1,
  "hit_rate": 0.58,
  ...
  "portfolio_value_usd": 1000000.0,
  "cost_model_config": {
    "_schema_version": 1,
    "enabled": true,
    "commission_bps": 1.0,
    ...
  },
  "cost_summary": {
    "total_cost_usd": 25000.0,
    "commission_total_usd": 5000.0,
    "spread_total_usd": 10000.0,
    "impact_total_usd": 10000.0,
    "total_traded_value_usd": 10000000.0,
    "avg_cost_bps": 2.5,
    "participation_violations": 3,
    "adv_fallback_count": 12,
    "volatility_fallback_count": 5
  },
  "capacity_analysis": {
    "avg_daily_turnover": 0.05,
    "portfolio_adv": 50000000.0,
    "portfolio_sigma": 0.02,
    "gross_alpha_annualized": 0.15,
    "capacity_at_impact_limit": 75000000.0,
    "capacity_at_participation_limit": 50000000.0,
    "capacity_at_breakeven": 100000000.0,
    "breakeven_status": "found",
    "implied_capacity": 50000000.0,
    "binding_constraint": "participation",
    "max_impact_bps": 10.0,
    "max_participation": 0.05
  },
  "total_return": 0.152,
  "gross_total_return": 0.152,
  "net_total_return": 0.127,
  "net_sharpe": 1.85,
  "net_max_drawdown": -0.08,
  "net_portfolio_returns_path": "net_portfolio_returns.parquet",
  "cost_breakdown_path": "cost_breakdown.parquet"
}
```

**Note:** `gross_total_return` and `net_total_return` are stored at the top level of summary.json
(not inside `cost_summary`) for consistency with existing metrics. The DB columns mirror these
top-level fields for query convenience.

**BacktestResult Reconstruction (result_storage.py):**

```python
def get_result(self, job_id: str) -> BacktestResult:
    ...
    # Load summary.json
    summary = json.loads((result_dir / "summary.json").read_text())

    # Load net returns if present
    # SECURITY: Validate filename is in allowlist to prevent path traversal
    ALLOWED_NET_RETURNS_FILES = {"net_portfolio_returns.parquet"}
    ALLOWED_COST_BREAKDOWN_FILES = {"cost_breakdown.parquet"}

    net_returns_filename = summary.get("net_portfolio_returns_path", "net_portfolio_returns.parquet")
    # Sanitize: extract basename only, reject if not in allowlist
    net_returns_basename = Path(net_returns_filename).name
    if net_returns_basename not in ALLOWED_NET_RETURNS_FILES:
        logger.warning("invalid_net_returns_path", path=net_returns_filename)
        net_returns_basename = "net_portfolio_returns.parquet"  # Fallback to default

    net_returns_path = result_dir / net_returns_basename
    # Verify path is within result_dir (defense in depth)
    if not net_returns_path.resolve().is_relative_to(result_dir.resolve()):
        raise ValueError("Path traversal detected in net_returns_path")

    net_portfolio_returns = (
        pl.read_parquet(net_returns_path)
        if net_returns_path.exists()
        else None
    )

    # Load cost breakdown path if present
    cost_breakdown_filename = summary.get("cost_breakdown_path", "cost_breakdown.parquet")
    cost_breakdown_basename = Path(cost_breakdown_filename).name
    if cost_breakdown_basename not in ALLOWED_COST_BREAKDOWN_FILES:
        logger.warning("invalid_cost_breakdown_path", path=cost_breakdown_filename)
        cost_breakdown_basename = "cost_breakdown.parquet"

    cost_breakdown_path = result_dir / cost_breakdown_basename
    if not cost_breakdown_path.resolve().is_relative_to(result_dir.resolve()):
        raise ValueError("Path traversal detected in cost_breakdown_path")

    # SECURITY: Use DB as source of truth for authorization
    # summary.json created_by is for display/logging only; DB is authoritative
    db_created_by = job_row.get("created_by", "unknown")
    summary_created_by = summary.get("created_by")
    if summary_created_by and summary_created_by != db_created_by:
        logger.warning(
            "created_by_mismatch",
            db_value=db_created_by,
            summary_value=summary_created_by,
            job_id=job_id,
        )

    return BacktestResult(
        ...
        # Metadata for security checks - always use DB value for authorization
        created_by=db_created_by,
        # IMPORTANT: Reconstruct portfolio_value_usd from summary.json (not default)
        portfolio_value_usd=summary.get("portfolio_value_usd", 1_000_000.0),
        cost_model_config=(
            CostModelConfig.from_dict(summary["cost_model_config"])
            if summary.get("cost_model_config")
            else None
        ),
        cost_summary_db=(
            CostSummaryDB.from_dict(summary["cost_summary"])
            if summary.get("cost_summary")
            else None
        ),
        capacity_analysis=(
            CapacityAnalysis.from_dict(summary["capacity_analysis"])
            if summary.get("capacity_analysis")
            else None
        ),
        net_portfolio_returns=net_portfolio_returns,
        # Expose paths for export functionality
        net_portfolio_returns_path=str(net_returns_path) if net_returns_path.exists() else None,
        cost_breakdown_path=str(cost_breakdown_path) if cost_breakdown_path.exists() else None,
        net_sharpe=summary.get("net_sharpe"),
        net_max_drawdown=summary.get("net_max_drawdown"),
    )
```

**BacktestResult Fields for Export:**
```python
@dataclass
class BacktestResult:
    ...
    # Metadata fields (required for security checks)
    created_by: str  # User ID who created the backtest (for export ownership check)

    # Cost-related fields
    portfolio_value_usd: float = 1_000_000.0  # AUM used for cost calculations
    net_portfolio_returns: pl.DataFrame | None = None
    net_portfolio_returns_path: str | None = None  # Path for Parquet export
    cost_breakdown_path: str | None = None  # Path for cost breakdown export
    cost_model_config: CostModelConfig | None = None
    cost_summary_db: CostSummaryDB | None = None
    capacity_analysis: CapacityAnalysis | None = None  # Capacity analysis results
    net_sharpe: float | None = None
    net_max_drawdown: float | None = None
```

#### Validation Enforcement

**Server-side validation in worker (not just UI):**

```python
# Server-side validation bounds (match DB constraints and UI limits)
COST_PARAM_BOUNDS = {
    "portfolio_value_usd": (10_000.0, 1_000_000_000.0),  # $10K to $1B
    "commission_bps": (0.0, 10.0),  # 0 to 10 bps
    "min_commission_usd": (0.0, 100.0),  # $0 to $100
    "spread_bps": (0.0, 50.0),  # 0 to 50 bps
    "eta": (0.01, 1.0),  # 0.01 to 1.0 (dimensionless)
    "adv_participation_limit": (0.01, 0.20),  # 1% to 20% (decimal)
    "max_impact_bps": (1.0, 50.0),  # 1 to 50 bps
}

def validate_cost_param(name: str, value: float | None) -> str | None:
    """Validate a cost parameter against its bounds. Returns error message or None.

    Note: Unknown parameters are silently skipped. This is intentional to allow
    forward compatibility with new optional fields. Required fields are validated
    by the model-level validation (CostModelConfig.validate()).
    """
    # Check for None first (before calling math.isfinite which would raise TypeError)
    if value is None:
        return f"{name} must not be None"

    # Check for NaN/inf (comparisons with NaN return False, bypassing bounds)
    if not math.isfinite(value):
        return f"{name} must be a finite number, got {value}"

    if name not in COST_PARAM_BOUNDS:
        return None  # Unknown param, skip validation (forward compatibility)
    min_val, max_val = COST_PARAM_BOUNDS[name]
    if value < min_val or value > max_val:
        return f"{name} must be between {min_val} and {max_val}, got {value}"
    return None

def run_backtest(config: dict[str, Any], created_by: str) -> dict[str, Any]:
    job_config = BacktestJobConfig.from_dict(config)

    # Validate portfolio value (always required)
    err = validate_cost_param("portfolio_value_usd", job_config.portfolio_value_usd)
    if err:
        raise ValueError(err)

    # Validate cost config server-side (when enabled)
    if job_config.cost_model:
        # 1. Validate enabled flag (presence rule: if present, must be enabled=True)
        validate_cost_config(job_config.cost_model)  # Raises if enabled=False

        # 2. Validate schema version
        if job_config.cost_model._schema_version != 1:
            raise ValueError(f"Unsupported cost_model_config schema version: {job_config.cost_model._schema_version}")

        # 3. Validate config size limit
        size_err = validate_config_size(job_config.cost_model)
        if size_err:
            raise ValueError(size_err)

        # 4. Run model-level validation (required fields, types)
        errors = job_config.cost_model.validate()
        if errors:
            raise ValueError(f"Invalid cost model config: {errors}")

        # 5. Validate individual cost parameters against bounds
        cost_params = {
            "commission_bps": job_config.cost_model.commission_bps,
            "min_commission_usd": job_config.cost_model.min_commission_usd,
            "spread_bps": job_config.cost_model.spread_bps,
            "eta": job_config.cost_model.eta,
            "adv_participation_limit": job_config.cost_model.adv_participation_limit,
            "max_impact_bps": job_config.cost_model.max_impact_bps,
        }
        for param_name, param_value in cost_params.items():
            err = validate_cost_param(param_name, param_value)
            if err:
                raise ValueError(err)
    ...
```

**Config size limit:**
```python
MAX_COST_CONFIG_SIZE = 4096  # 4KB max for cost_model_config JSON (UTF-8 bytes)

def validate_config_size(config: CostModelConfig) -> str | None:
    """Validate config size doesn't exceed limit. Returns error message or None."""
    serialized = json.dumps(config.to_dict(), ensure_ascii=False).encode('utf-8')
    if len(serialized) > MAX_COST_CONFIG_SIZE:
        return f"cost_model_config exceeds {MAX_COST_CONFIG_SIZE} bytes ({len(serialized)} bytes)"
    return None
```

**Enforcement location:** Worker validation (before DB insert). The API layer may also
enforce this limit for early rejection, but the worker is the authoritative check.

#### Database Schema Changes

**Migration: `db/migrations/00XX_add_cost_model_fields.sql`**

```sql
-- Add cost model fields to backtest_jobs table
ALTER TABLE backtest_jobs ADD COLUMN IF NOT EXISTS
    portfolio_value_usd NUMERIC(18, 2) DEFAULT 1000000.0 NOT NULL;

ALTER TABLE backtest_jobs ADD COLUMN IF NOT EXISTS
    cost_model_config JSONB DEFAULT NULL;

ALTER TABLE backtest_jobs ADD COLUMN IF NOT EXISTS
    cost_summary JSONB DEFAULT NULL;

ALTER TABLE backtest_jobs ADD COLUMN IF NOT EXISTS
    gross_total_return NUMERIC(12, 6) DEFAULT NULL;

ALTER TABLE backtest_jobs ADD COLUMN IF NOT EXISTS
    net_total_return NUMERIC(12, 6) DEFAULT NULL;

ALTER TABLE backtest_jobs ADD COLUMN IF NOT EXISTS
    net_sharpe NUMERIC(10, 4) DEFAULT NULL;

ALTER TABLE backtest_jobs ADD COLUMN IF NOT EXISTS
    net_max_drawdown NUMERIC(10, 6) DEFAULT NULL;

-- Partial index for querying jobs with cost model (presence-based, not enabled flag)
-- Note: Per spec, enabled=false means cost_model_config=NULL, so presence check is sufficient
-- Index on created_at for efficient time-based queries on cost-enabled jobs
CREATE INDEX IF NOT EXISTS idx_backtest_jobs_has_cost_model
    ON backtest_jobs (created_at DESC)
    WHERE cost_model_config IS NOT NULL;

-- Constraint: portfolio_value_usd must be within valid range
ALTER TABLE backtest_jobs ADD CONSTRAINT chk_portfolio_value_range
    CHECK (portfolio_value_usd >= 10000 AND portfolio_value_usd <= 1000000000);

COMMENT ON COLUMN backtest_jobs.portfolio_value_usd IS
    'Portfolio AUM for cost calculations (USD, $10K-$1B)';
COMMENT ON COLUMN backtest_jobs.cost_model_config IS
    'Cost model configuration JSONB with schema_version';
COMMENT ON COLUMN backtest_jobs.cost_summary IS
    'Computed cost summary JSONB (total, breakdown, fallback counts)';
COMMENT ON COLUMN backtest_jobs.gross_total_return IS
    'Cumulative gross return before costs';
COMMENT ON COLUMN backtest_jobs.net_total_return IS
    'Cumulative net return after costs';
COMMENT ON COLUMN backtest_jobs.net_sharpe IS
    'Sharpe ratio computed on net returns';
COMMENT ON COLUMN backtest_jobs.net_max_drawdown IS
    'Maximum drawdown on net returns';
```

**Column Specifications:**

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `portfolio_value_usd` | NUMERIC(18,2) | NO | 1000000.0 | AUM in USD |
| `cost_model_config` | JSONB | YES | NULL | Cost config with `_schema_version` |
| `cost_summary` | JSONB | YES | NULL | Computed summary stats |
| `gross_total_return` | NUMERIC(12,6) | YES | NULL | Cumulative gross return |
| `net_total_return` | NUMERIC(12,6) | YES | NULL | Cumulative net return |
| `net_sharpe` | NUMERIC(10,4) | YES | NULL | Net Sharpe ratio |
| `net_max_drawdown` | NUMERIC(10,6) | YES | NULL | Net max drawdown |

**cost_model_config JSONB Schema:**
```json
{
  "_schema_version": 1,
  "enabled": true,
  "commission_bps": 1.0,
  "min_commission_usd": 1.0,
  "spread_bps": 5.0,
  "eta": 0.1,
  "adv_participation_limit": 0.05,
  "max_impact_bps": 10.0
}
```

**Field Units (stored as specified, NOT uniformly decimal):**
| Field | Internal Unit | UI Display | Conversion |
|-------|---------------|------------|------------|
| `commission_bps` | bps (1.0 = 1 bps) | bps | None |
| `spread_bps` | bps (5.0 = 5 bps) | bps | None |
| `adv_participation_limit` | **decimal** (0.05 = 5%) | percent | UI × 0.01 → internal |
| `max_impact_bps` | bps (10.0 = 10 bps) | bps | None |
| `eta` | dimensionless (0.1) | decimal | None |

**Note:** Only `adv_participation_limit` requires conversion (percent UI → decimal internal).
Fields ending in `_bps` are stored and displayed in basis points. In formulas, bps fields
are divided by 10000 to convert to decimal (e.g., `commission_bps / 10000`).

**`max_impact_bps` Scope:** This parameter is used ONLY for capacity analysis (T9.3).
It is NOT applied as a cap during cost simulation - actual impact costs are computed
without limitation. This parameter defines the threshold for capacity constraint analysis.

**cost_summary JSONB Schema:**
```json
{
  "total_cost_usd": 25000.0,
  "commission_total_usd": 5000.0,
  "spread_total_usd": 10000.0,
  "impact_total_usd": 10000.0,
  "total_traded_value_usd": 10000000.0,
  "avg_cost_bps": 2.5,
  "participation_violations": 3,
  "adv_fallback_count": 12,
  "volatility_fallback_count": 5
}
```

**`total_traded_value_usd` Definition:**
```python
# Sum of all trade values (absolute) across all symbols and dates
total_traded_value_usd = Σ |trade_value_usd[symbol, D]| for all symbols, all D
                       = Σ |Δw[symbol, D]| × AUM for all symbols, all D
```

**`avg_cost_bps` Formula:**
```python
# Average cost per dollar traded, in basis points
if total_traded_value_usd > 0:
    avg_cost_bps = (total_cost_usd / total_traded_value_usd) * 10000
else:
    avg_cost_bps = 0.0  # No trades, no cost per trade
```

**Note:** `gross_total_return` and `net_total_return` are stored in dedicated DB columns,
not in the `cost_summary` JSONB, to enable efficient queries and sorting.

**Backward Compatibility with `total_return`:**

Existing consumers (e.g., compare UI, metrics.py) use a single `total_return` field.
For backward compatibility:

```python
# total_return = gross_total_return (always, for consistency)
# Rationale: Historical runs had no cost model, so their total_return is gross
# New runs with cost model should display gross for comparison purposes
# Net return is a NEW metric, not a replacement for total_return

@property
def total_return(self) -> float | None:
    """Backward-compatible total return (always gross)."""
    return self.gross_total_return

# UI/export should use:
# - total_return (or gross_total_return): For comparison with historical runs
# - net_total_return: For cost-adjusted performance (new, when cost model enabled)
```

**Summary.json includes both:**
```json
{
  "total_return": 0.152,           // Backward-compatible (= gross)
  "gross_total_return": 0.152,     // Explicit gross
  "net_total_return": 0.127        // Net (only when cost model enabled)
}
```

**Per-Symbol Cost Breakdown Storage:**

For export functionality, per-symbol/per-day cost details are stored in a separate Parquet file:

```
{result_dir}/cost_breakdown.parquet
```

**Schema:**
| Column | Type | Description |
|--------|------|-------------|
| `date` | date | Trading date |
| `permno` | int \| null | CRSP permno (nullable: set for PIT backtests, null for non-PIT/Yahoo) |
| `symbol` | string | Symbol/ticker (for display and non-PIT joins) |
| `weight_change` | float | Signed weight change (Δw) |
| `trade_value_usd` | float | Absolute trade value |
| `commission_usd` | float | Commission cost |
| `spread_usd` | float | Spread cost |
| `impact_usd` | float | Market impact cost |
| `total_cost_usd` | float | Sum of all costs |
| `adv_usd` | float | ADV used (lagged) |
| `volatility` | float | Volatility used (lagged) |
| `participation_pct` | float | trade_value / adv |
| `used_adv_fallback` | bool | True if ADV fallback applied |
| `used_vol_fallback` | bool | True if volatility fallback applied |

**summary.json includes path:**
```json
{
  ...
  "cost_breakdown_path": "cost_breakdown.parquet"
}
```

**Net Portfolio Returns Parquet Schema:**

The `net_portfolio_returns.parquet` file contains daily return data with cost breakdowns:

```
{result_dir}/net_portfolio_returns.parquet
```

**Schema:**
| Column | Type | Description |
|--------|------|-------------|
| `date` | date | Trading date |
| `gross_return` | float | Daily gross portfolio return |
| `cost_drag` | float | Daily cost as fraction of AUM |
| `net_return` | float | Daily net return (gross - cost_drag) |
| `turnover` | float | Daily gross turnover (sum of abs weight changes) |
| `total_cost_usd` | float | Total cost in USD for the day |
| `commission_usd` | float | Commission cost for the day |
| `spread_usd` | float | Spread cost for the day |
| `impact_usd` | float | Impact cost for the day |

**Note:** This schema provides all fields needed for CSV export and external verification.
The `cost_breakdown.parquet` provides per-symbol detail, while this file provides portfolio-level daily aggregates.

#### Files to Modify

1. **`libs/trading/backtest/job_queue.py`**
   - Add `cost_model`, `portfolio_value_usd` to `BacktestJobConfig`
   - Include in `compute_job_id` hash
   - Add `cost_model_config` to INSERT/UPDATE SQL
   - Add `get_job_metadata(job_id: str) -> dict | None` for export auth

2. **`libs/trading/backtest/worker.py`**
   - Add `cost_model_config`, `cost_summary`, `gross_total_return`, `net_total_return` to `_ALLOWED_UPDATE_COLUMNS`
   - Add server-side validation
   - Implement cost application pipeline
   - Save net returns Parquet

3. **`libs/trading/backtest/models.py`**
   - Add fields to `BacktestJob` dataclass
   - Update `row_to_backtest_job` mapper

4. **`libs/trading/backtest/result_storage.py`**
   - Update `_write_summary_json` to include cost fields
   - Update `get_result` to reconstruct cost data

5. **`libs/trading/alpha/research_platform.py`**
   - Add `cost_model_config`, `cost_summary_db`, `net_portfolio_returns`, `net_sharpe`, `net_max_drawdown` to `BacktestResult`

6. **`libs/data/data_providers/crsp_local_provider.py`**
   - Implement `get_daily_data_pit` (or update `get_daily_prices` to support snapshot path)

7. **Create: `libs/trading/backtest/cost_model.py`**
   - `CostModel`, `CostModelConfig`, `CostSummaryDB` classes
   - `apply_costs_to_backtest` function
   - Fallback policies

8. **Create: `libs/trading/backtest/cost_data_loader.py`**
   - `load_pit_adv_volatility` for CRSP
   - `load_adv_volatility_yfinance` for Yahoo (see specification below)
   - Lagging logic
   - **DEPENDENCY:** Add `yfinance = "^0.2.0"` to pyproject.toml

**Yahoo ADV/Volatility Loader Specification:**

The Yahoo loader MUST use the same window/lag rules as the PIT loader for consistency:

```python
def load_adv_volatility_yfinance(
    symbols: list[str],
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    # NOTE: Consider reusing YFinanceProvider for caching and retries.
    # This implementation uses yfinance directly for simplicity but
    # production code should leverage the existing provider infrastructure.
    """Load ADV/volatility from Yahoo Finance with same rules as PIT loader.

    Window/Lag Rules (MUST match PIT loader):
    - ADV: 20 trading days rolling mean of (close × volume)
    - Volatility: 20 trading days rolling std of returns (ddof=1)
    - Lag: D-1 (use yesterday's ADV/vol for today's trade)
    - min_periods: 20 (require full window for valid result)
    - Calendar lookback: 40 days (20 trading days × 2)

    Returns:
        DataFrame with columns [symbol, date, adv_usd, volatility]
        Both metrics are LAGGED by 1 day to avoid lookahead bias.
    """
    # Fetch OHLCV data with lookback
    lookback_calendar_days = 40  # Same as PIT loader
    fetch_start = start_date - timedelta(days=lookback_calendar_days)

    # BATCH DOWNLOAD: Fetch all symbols in one request for efficiency
    # Returns MultiIndex DataFrame with (Date, Symbol) structure
    raw_df = yfinance.download(
        tickers=symbols,
        start=fetch_start,
        end=end_date + timedelta(days=1),
        group_by="ticker",
        threads=True,  # Enable parallel fetching
    )

    all_data = []
    for symbol in symbols:
        try:
            # Extract single-symbol data from MultiIndex result
            if len(symbols) == 1:
                df = raw_df.copy()  # Single symbol: no MultiIndex
            else:
                df = raw_df[symbol].copy()  # Multi-symbol: select by ticker
        except KeyError:
            logger.warning("yfinance_symbol_not_found", symbol=symbol)
            continue
        # IMPORTANT: Use Adj Close to match SimpleBacktester return calculation
        # This ensures cost inputs are consistent with the returns they're applied to
        #
        # ADV Rationale: Dollar volume uses raw Close × Volume intentionally.
        # ADV represents actual traded dollar volume on that day (liquidity available).
        # Using adjusted prices would distort historical liquidity for split-adjusted
        # shares (e.g., a 2:1 split would halve historical ADV if using adj close).
        # For market impact calculations, we need actual liquidity, not adjusted values.
        df["dollar_vol"] = df["Close"] * df["Volume"]
        # Returns use Adj Close (same as SimpleBacktester for consistency)
        df["ret"] = df["Adj Close"].pct_change()

        # Rolling ADV (20 trading days, min_periods=20)
        df["adv_usd_raw"] = df["dollar_vol"].rolling(window=20, min_periods=20).mean()
        # Rolling volatility (20 trading days, min_periods=20, ddof=1)
        df["volatility_raw"] = df["ret"].rolling(window=20, min_periods=20).std(ddof=1)

        # LAG by 1 day (critical for consistency with PIT)
        df["adv_usd"] = df["adv_usd_raw"].shift(1)
        df["volatility"] = df["volatility_raw"].shift(1)

        df["symbol"] = symbol
        # Reset index and rename Date -> date for consistency with PIT loader
        df = df.reset_index().rename(columns={"Date": "date"})
        all_data.append(df[["symbol", "date", "adv_usd", "volatility"]])

    # Handle empty result (no symbols found)
    if not all_data:
        logger.warning("yfinance_no_data", symbols=symbols)
        # Return empty DataFrame with correct schema
        return pl.DataFrame(schema={
            "symbol": pl.Utf8,
            "date": pl.Date,
            "adv_usd": pl.Float64,
            "volatility": pl.Float64,
        })

    # Concatenate and cast date column to pl.Date for consistent joins
    combined = pl.concat([pl.from_pandas(d) for d in all_data])
    return combined.with_columns(pl.col("date").cast(pl.Date))
```

**Consistency Guarantee:** Both CRSP and Yahoo loaders produce the same column structure
and apply identical lag/window rules. The only difference is the data source.

9. **Create: `db/migrations/00XX_add_cost_model_fields.sql`**
   - Add columns per "Database Schema Changes" section above
   - Add constraint for portfolio_value_usd range
   - Add index for cost_model_config presence

10. **`apps/web_console_ng/pages/backtest.py`**
   - Add cost config form (T9.2)
   - Add portfolio value input (T9.2)
   - Display cost summary and net metrics (T9.2)
   - Add export button and download handler (T9.4)

---

### T9.3: Capacity Analysis - HIGH PRIORITY

#### Capacity Enablement Rule

**Capacity analysis is ONLY computed when `cost_model_config` is present (not None).**

When cost model is disabled (i.e., `cost_model_config = None`):
- `capacity_analysis` = `None` in `BacktestResult`
- UI shows "Cost model not enabled" message (no capacity section displayed)
- No capacity computation is attempted

When cost model is enabled:
- Capacity is computed after cost model runs
- All three constraints are evaluated
- Results stored in `capacity_analysis` field

#### Capacity Definition and Objective

**Capacity is defined as the AUM where one of these constraints binds:**

1. **Impact Constraint**: Average daily impact ≤ `max_impact_bps` (default: 10 bps)
2. **Participation Constraint**: Average participation ≤ `adv_participation_limit` (default: 5%)
3. **Net Alpha Constraint**: Net alpha > 0 (breakeven)

#### Input Metric Definitions (Trade-Weighted)

**Turnover (daily, gross, portfolio-level):**

**`backtest_dates` Definition:**
```python
# backtest_dates = dates with valid gross portfolio returns
# This ensures turnover and traded_symbols are computed over the same date set as returns
backtest_dates = [D for D in date_range(start, end) if gross_return[D] is not None]
```

```python
# Daily gross turnover = sum of absolute weight changes
turnover_daily[D] = Σ |w[symbol,D] - w[symbol,D-1]| for all symbols

# Average daily turnover over backtest period
# IMPORTANT: D=0 (initial build) trades ARE included in turnover average
# This is consistent with the cost model which costs D=0 trades
# For short backtests, this may significantly increase avg_daily_turnover
# and reduce capacity estimates (which is conservative and appropriate)
avg_daily_turnover = mean(turnover_daily[D]) for D in backtest_dates
```

**ADV (trade-weighted average):**
```python
# PRE-CHECK: If no trades occurred, skip trade-weighted aggregation
# NOTE: total_traded_weight is UNITLESS (sum of absolute weight changes)
# This is distinct from total_traded_value_usd (in USD) used in cost_summary
total_traded_weight = Σ_D Σ_s |Δw[s,D]|  # unitless (weight units)
if total_traded_weight == 0:
    # No trades means no capacity constraint from ADV/volatility
    # Set to None and let the zero-turnover guard in capacity computation handle it
    portfolio_adv = None
    portfolio_sigma = None
    # Skip to capacity guard (will return implied_capacity = inf, binding_constraint = "none")

# Step 1: Compute mean ADV per symbol over backtest period (time aggregation first)
# IMPORTANT: Apply per-day fallback BEFORE computing means to match cost model behavior
# This ensures capacity uses the same effective ADV values as cost calculation
for each (symbol, D) in backtest:
    if ADV[symbol, D] is null or ADV[symbol, D] <= 0:
        ADV[symbol, D] = ADV_FLOOR_USD  # Apply fallback per-day

mean_adv[symbol] = mean(ADV[symbol, D]) for D in backtest_dates
# Note: After per-day fallback, all values are valid, so mean is always defined

# Step 2: Compute trade weights per symbol (trade weight per symbol / total trade weight)
# Explicit time aggregation: sum over all days first, then divide
# NOTE: total_traded_weight > 0 is guaranteed by the pre-check above
trade_weight[symbol] = (Σ_D |Δw[symbol,D]|) / total_traded_weight  # unitless fraction
# where D ranges over all backtest dates, s ranges over all symbols

# Step 3: Compute portfolio ADV as trade-weighted average of per-symbol mean ADVs
portfolio_adv = Σ (trade_weight[symbol] × mean_adv[symbol]) for all traded symbols
```

**Volatility (trade-weighted average):**
```python
# Step 1: Compute mean volatility per symbol over backtest period (time aggregation first)
# IMPORTANT: Apply per-day fallback BEFORE computing means to match cost model behavior
for each (symbol, D) in backtest:
    if sigma[symbol, D] is null or sigma[symbol, D] <= 0:
        sigma[symbol, D] = VOL_FLOOR  # Apply fallback per-day

mean_sigma[symbol] = mean(sigma[symbol, D]) for D in backtest_dates
# Note: After per-day fallback, all values are valid, so mean is always defined

# Step 2: Use same trade weights as ADV
# Step 3: Compute portfolio volatility as trade-weighted average
portfolio_sigma = Σ (trade_weight[symbol] × mean_sigma[symbol]) for all traded symbols
# Note: Ignores correlation (conservative estimate for impact)
```

**Per-Symbol Mean Computation (After Per-Day Fallback):**
```python
def compute_per_symbol_means(
    adv_vol_df: pl.DataFrame,  # columns: [permno, symbol, date, adv_usd, volatility]
    traded_keys: list[int | str],  # permnos for PIT, symbols for non-PIT
    key_column: str = "symbol",  # "permno" for PIT backtests, "symbol" for non-PIT
) -> tuple[dict[int | str, float], dict[int | str, float]]:
    """Compute mean ADV and volatility per entity (permno or symbol).

    IMPORTANT: This function expects per-day fallbacks to have been applied BEFORE calling.
    The adv_usd and volatility columns should have no nulls (all nulls replaced with floor values).
    This ensures consistency with the cost model which also applies per-day fallbacks.

    For PIT backtests, use key_column="permno" to correctly aggregate by security
    even when symbols change over time. For non-PIT, use key_column="symbol".

    Returns:
        (mean_adv_by_key, mean_sigma_by_key)
    """
    mean_adv: dict[int | str, float] = {}
    mean_sigma: dict[int | str, float] = {}

    for key in traded_keys:
        key_data = adv_vol_df.filter(pl.col(key_column) == key)

        # After per-day fallback, all values should be valid
        # If any nulls remain, it's a data error - use floor as safety
        adv_values = key_data["adv_usd"].drop_nulls()
        mean_adv[key] = adv_values.mean() if len(adv_values) > 0 else ADV_FLOOR_USD

        vol_values = key_data["volatility"].drop_nulls()
        mean_sigma[key] = vol_values.mean() if len(vol_values) > 0 else VOL_FLOOR

    return mean_adv, mean_sigma

# NOTE: Fallback counts for capacity analysis come from the cost model's per-day fallback
# application, which tracks how many (symbol, date) pairs used fallback values.
# These counts are already in cost_summary (adv_fallback_count, volatility_fallback_count).
```

**Temporal Aggregation Order:** First average ADV/sigma over time per symbol, then apply trade weights.
This prevents high-ADV days from dominating when we happen to trade more on those days.

**Approximation Note:** Using trade-weighted portfolio averages is an approximation since
market impact is nonlinear (sqrt of Q/ADV). This approach overstates capacity slightly
compared to computing per-symbol capacity and taking the minimum. This simplification
is acceptable for capacity estimation purposes and documented here for transparency.

**Eta (impact coefficient):**
```python
# Use strategy-level eta from cost config (default 0.1)
# Same value used in cost model for consistency
portfolio_eta = cost_config.eta  # Typically 0.1
```

**`num_trading_days` Definition:**
```python
# Count of trading days in the backtest period with valid portfolio returns
# This excludes weekends, holidays, and days with missing data
num_trading_days = count(gross_portfolio_returns where return is not null)
```

**Gross Alpha (annualized, compounded):**
```python
# PRE-CHECK: If no trading days, capacity cannot be computed
if num_trading_days <= 0:
    return CapacityAnalysis(
        ...
        implied_capacity=None,
        binding_constraint="no_return_data",
        breakeven_status="no_return_data",
    )

# IMPORTANT: gross_total_return is COMPOUNDED (product of 1+daily_return - 1)
# This is consistent with how BacktestResult stores it (from metrics.py)
# gross_total_return = Π(1 + daily_gross_return[d]) - 1 for all days d

# GUARD: Validate gross_total_return before annualization
# - Must be finite (not NaN or inf)
# - Must be > -1 (otherwise annualization formula is invalid: negative base to fractional power)
if not math.isfinite(gross_total_return) or gross_total_return <= -1.0:
    return CapacityAnalysis(
        ...
        implied_capacity=None,
        binding_constraint="invalid_input",
        breakeven_status="invalid_input",
    )

# Annualized gross return using compounded formula (matches metrics.py)
# NOT linear scaling: use geometric annualization
gross_alpha_annualized = (1 + gross_total_return) ** (252 / num_trading_days) - 1
# This is the target return before costs

# For daily return extraction (used in net alpha calculation):
# Since gross_total_return is compounded, we use geometric mean
gross_daily_mean = (1 + gross_total_return) ** (1 / num_trading_days) - 1
```

#### Explicit Capacity Formulas

**Impact-based Capacity:**
```
From cost model:
  impact_usd = Q × eta × sigma × sqrt(Q / ADV)
  where Q = trade_value = turnover × AUM

Substituting Q:
  impact_usd = turnover × AUM × eta × sigma × sqrt(turnover × AUM / ADV)

Converting to bps:
  impact_bps = (impact_usd / AUM) × 10000
             = turnover × eta × sigma × sqrt(turnover × AUM / ADV) × 10000

Solving for AUM at max_impact_bps:
  Let k = turnover × eta × sigma × 10000
  max_impact_bps = k × sqrt(turnover × AUM / ADV)
  sqrt(turnover × AUM / ADV) = max_impact_bps / k
  turnover × AUM / ADV = (max_impact_bps / k)²
  AUM = (max_impact_bps / k)² × ADV / turnover
  AUM_impact = (max_impact_bps / (turnover × eta × sigma × 10000))² × ADV / turnover

Where:
  - eta = portfolio_eta (from cost config)
  - sigma = portfolio_sigma (weighted average daily vol)
  - turnover = avg_daily_turnover (gross)
  - ADV = portfolio_adv (weighted average)
```

**Participation-based Capacity:**

**Note:** This is an *average* participation constraint (ratio of averages), NOT a maximum
or percentile constraint. It answers: "At what AUM does average daily participation equal
the limit?" Individual days/symbols may exceed the limit while average stays below.
For strict per-trade limits, use the `participation_violations` count from T9.1.

```
Given:
  avg_participation = avg_daily_turnover × AUM / portfolio_adv

Solving for AUM at max_participation:
  AUM_participation = max_participation × portfolio_adv / avg_daily_turnover

Where:
  - avg_daily_turnover = mean(daily gross turnover)
  - portfolio_adv = trade-weighted average ADV
  - max_participation = adv_participation_limit (default 5%)
```

**Net Alpha Computation (Compounded Basis):**
```python
def compute_net_alpha(
    aum: float,
    gross_daily_return: float,  # Daily gross return (e.g., 0.0004 for 4 bps/day)
    avg_daily_turnover: float,
    portfolio_adv: float,
    portfolio_sigma: float,
    eta: float,
    commission_bps: float,
    spread_bps: float,
) -> float | None:
    """Compute annualized net alpha at given AUM using compounded basis.

    Both gross and cost are annualized using geometric compounding for consistency.

    Net alpha = (1 + gross_daily - daily_cost_rate)^252 - 1

    Impact scales with sqrt(trade_value / ADV), so:
    - At 2× AUM: trade values 2×, impact ~1.41× per trade, total impact ~2.83×

    Returns:
        Annualized net alpha, or None if costs exceed gross return to the point
        where daily_net_return <= -1 (which would break compounding math).
        None indicates the computation is mathematically invalid, not a valid result.
    """
    # Daily trade value at this AUM
    daily_trade_value = avg_daily_turnover * aum

    # Daily costs (per the cost model formulas)
    commission_daily = daily_trade_value * commission_bps / 10000
    spread_daily = daily_trade_value * (spread_bps / 2) / 10000
    # Impact: Q × eta × sigma × sqrt(Q / ADV)
    impact_daily = daily_trade_value * eta * portfolio_sigma * sqrt(daily_trade_value / portfolio_adv)

    total_daily_cost = commission_daily + spread_daily + impact_daily
    daily_cost_rate = total_daily_cost / aum

    # Compound both gross and net for consistent annualization
    daily_net_return = gross_daily_return - daily_cost_rate

    # GUARD: If daily_net_return <= -1, compounding is invalid (would go negative/NaN)
    # This means costs exceed 100% + gross return, which is a total loss scenario
    if daily_net_return <= -1.0:
        return None  # Computation invalid - costs exceed returns

    annualized_net_alpha = (1 + daily_net_return) ** 252 - 1

    return annualized_net_alpha


def gross_daily_from_annualized(gross_alpha_annualized: float) -> float:
    """Convert annualized gross alpha to daily return."""
    return (1 + gross_alpha_annualized) ** (1/252) - 1
```

**Breakeven Capacity (Binary Search):**

**Pre-check for Non-Positive Gross Alpha:**

If gross alpha ≤ 0, there is no breakeven AUM (strategy loses money even at zero AUM).
The search must handle this case explicitly:

```python
MIN_AUM_FOR_BREAKEVEN = 10_000  # $10K minimum for valid breakeven search
MAX_AUM = 100_000_000_000  # $100B upper bound for search

def find_breakeven_aum(
    gross_alpha_annualized: float,
    avg_daily_turnover: float,
    avg_traded_symbols: int,  # Average number of symbols traded per day
    portfolio_adv: float,
    portfolio_sigma: float,
    cost_config: CostModelConfig,
) -> tuple[float | None, str]:
    """Find AUM where net_alpha = 0 via binary search.

    Returns:
        (breakeven_aum, status) where breakeven_aum is None for non-computable cases.
        Status is one of:
        - "found": Valid breakeven AUM found (breakeven_aum is float)
        - "no_positive_alpha": Gross alpha ≤ 0, no breakeven exists (returns None)
        - "net_negative_at_min": Net alpha negative even at minimum AUM (returns search_low)
        - "min_commission_dominated": Min commission floors dominate, unreliable (returns None)
        - "always_positive": Net alpha positive even at max AUM, rare (returns MAX_AUM)
        - "adv_unavailable": No ADV data available (returns None)
        - "volatility_unavailable": No volatility data available (returns None)
    """
    # PRE-CHECK: If gross alpha is non-positive, no breakeven exists
    # Return None to exclude from capacity min() calculation
    if gross_alpha_annualized <= 0:
        return None, "no_positive_alpha"

    # PRE-CHECK: If turnover is zero/invalid, capacity is infinite (no constraint)
    if avg_daily_turnover is None or avg_daily_turnover <= 0:
        return float('inf'), "always_positive"

    # PRE-CHECK: If liquidity data is invalid, breakeven cannot be computed
    # Return None to exclude from capacity min() calculation
    # Check for None, non-positive, and non-finite (NaN/inf)
    if portfolio_adv is None or portfolio_adv <= 0 or not math.isfinite(portfolio_adv):
        return None, "adv_unavailable"
    if portfolio_sigma is None or portfolio_sigma <= 0 or not math.isfinite(portfolio_sigma):
        return None, "volatility_unavailable"

    # Compute gross_daily BEFORE any use (needed for all net_alpha computations)
    gross_daily = gross_daily_from_annualized(gross_alpha_annualized)

    # PRE-CHECK: If min commission dominates at minimum AUM, start search above threshold
    # This ensures we still find valid breakeven when it exists at higher AUM
    if not is_breakeven_valid(
        aum=MIN_AUM_FOR_BREAKEVEN,
        avg_daily_turnover=avg_daily_turnover,
        avg_traded_symbols=avg_traded_symbols,
        commission_bps=cost_config.commission_bps,
        min_commission_usd=cost_config.min_commission_usd,
    ):
        # Compute the AUM threshold where bps costs begin to dominate
        threshold = compute_bps_dominance_threshold(
            cost_config.commission_bps,
            cost_config.min_commission_usd,
        )
        # Start search above threshold (where bps model is valid)
        min_aum_for_search = max(MIN_AUM_FOR_BREAKEVEN, threshold * avg_traded_symbols / avg_daily_turnover)

        # If threshold is too high (above MAX_AUM), report min_commission_dominated
        # Return None to exclude from capacity min() calculation
        if min_aum_for_search >= MAX_AUM:
            return None, "min_commission_dominated"

        # Check if net alpha is already negative at the adjusted minimum
        net_at_adjusted_min = compute_net_alpha(
            aum=min_aum_for_search,
            gross_daily_return=gross_daily,
            avg_daily_turnover=avg_daily_turnover,
            portfolio_adv=portfolio_adv,
            portfolio_sigma=portfolio_sigma,
            eta=cost_config.eta,
            commission_bps=cost_config.commission_bps,
            spread_bps=cost_config.spread_bps,
        )
        # Handle None (invalid computation) same as non-positive
        if net_at_adjusted_min is None or net_at_adjusted_min <= 0:
            # Breakeven is unreliable due to min commission dominance; return None
            return None, "min_commission_dominated"

        # Update search bounds to start above threshold
        search_low = min_aum_for_search
    else:
        search_low = MIN_AUM_FOR_BREAKEVEN

    # Check if net alpha at search minimum is already negative
    net_at_min = compute_net_alpha(
        aum=search_low,
        gross_daily_return=gross_daily,
        avg_daily_turnover=avg_daily_turnover,
        portfolio_adv=portfolio_adv,
        portfolio_sigma=portfolio_sigma,
        eta=cost_config.eta,
        commission_bps=cost_config.commission_bps,
        spread_bps=cost_config.spread_bps,
    )
    # Handle None (invalid computation) same as non-positive
    if net_at_min is None or net_at_min <= 0:
        return search_low, "net_negative_at_min"

    # Check if net alpha is positive at upper bound (rare but possible for low-cost strategies)
    net_at_max = compute_net_alpha(
        aum=MAX_AUM,
        gross_daily_return=gross_daily,
        avg_daily_turnover=avg_daily_turnover,
        portfolio_adv=portfolio_adv,
        portfolio_sigma=portfolio_sigma,
        eta=cost_config.eta,
        commission_bps=cost_config.commission_bps,
        spread_bps=cost_config.spread_bps,
    )
    # Note: net_at_max being None means extreme costs at max AUM - treat as not positive
    if net_at_max is not None and net_at_max > 0:
        return MAX_AUM, "always_positive"  # Net alpha positive even at max AUM

    # Binary search
    low, high = search_low, MAX_AUM
    while high - low > 1000:  # $1K precision
        mid = (low + high) / 2
        net_alpha = compute_net_alpha(
            aum=mid,
            gross_daily_return=gross_daily,
            avg_daily_turnover=avg_daily_turnover,
            portfolio_adv=portfolio_adv,
            portfolio_sigma=portfolio_sigma,
            eta=cost_config.eta,
            commission_bps=cost_config.commission_bps,
            spread_bps=cost_config.spread_bps,
        )
        # Treat None as non-positive (search lower for valid breakeven)
        if net_alpha is not None and net_alpha > 0:
            low = mid
        else:
            high = mid

    return low, "found"  # Conservative: highest AUM with positive alpha
```

**Commission Approximation in Capacity Analysis:**

The breakeven and net-alpha capacity calculations use a **bps-only approximation** for commission
costs. This simplification has the following implications:

1. **`compute_net_alpha` uses bps commission only**: The formula `commission_daily = daily_trade_value * commission_bps / 10000`
   does not incorporate `min_commission_usd`. This is intentional because:
   - Per-symbol trade counts are not available at the portfolio aggregate level
   - The approximation is accurate when trade values exceed the bps dominance threshold

2. **Accuracy bounds**: The approximation is accurate when:
   - `daily_trade_value_per_symbol > min_commission_usd * 10000 / commission_bps`
   - For typical configs (1 bps commission, $1 min), this means trades > $100K per symbol

3. **Capacity may be overstated**: When trading many small positions (e.g., 100 symbols × $10K each),
   actual min-commission costs are higher than bps-based estimates. The `is_breakeven_valid` guard
   warns when this approximation is unreliable.

4. **Interpreting results**: If `binding_constraint = "breakeven"` and the strategy trades many
   small positions, actual breakeven AUM may be lower than reported.

**min_commission_usd Guard:**

The breakeven calculation uses bps-based commission costs for simplicity. At low AUM/turnover,
min commission floors dominate and can break the monotonicity assumption of binary search.

```python
def compute_bps_dominance_threshold(
    commission_bps: float,
    min_commission_usd: float,
) -> float:
    """Compute trade value threshold where bps costs dominate min commission.

    bps costs dominate when: trade_value * commission_bps / 10000 > min_commission_usd
    Solving: trade_value > min_commission_usd * 10000 / commission_bps

    Special cases:
    - If both commission_bps AND min_commission_usd are zero/None: no commission at all,
      return 0.0 (bps always "dominates" since there's nothing to dominate)
    - If only commission_bps is zero but min_commission_usd > 0: bps never dominates,
      return inf (min commission always applies)
    """
    # Special case: no commission at all (both zero)
    if (commission_bps is None or commission_bps <= 0) and (min_commission_usd is None or min_commission_usd <= 0):
        return 0.0  # No commission, threshold is 0 (always valid for breakeven search)

    # If only bps is zero but min commission exists, bps never dominates
    if commission_bps is None or commission_bps <= 0:
        return float('inf')  # bps never dominates if commission_bps is zero

    return min_commission_usd * 10000 / commission_bps

def is_breakeven_valid(
    aum: float,
    avg_daily_turnover: float,
    avg_traded_symbols: int,  # Average number of symbols traded per day
    commission_bps: float,
    min_commission_usd: float,
) -> bool:
    """Check if breakeven search assumptions are valid using actual config values.

    Uses a conservative per-symbol estimate: if the average per-symbol trade value
    is below the min-commission threshold, bps costs are dominated and capacity
    will be overstated.
    """
    daily_trade_value = aum * avg_daily_turnover

    # Conservative per-symbol estimate (assumes uniform distribution)
    if avg_traded_symbols > 0:
        per_symbol_trade_value = daily_trade_value / avg_traded_symbols
    else:
        per_symbol_trade_value = daily_trade_value

    threshold = compute_bps_dominance_threshold(commission_bps, min_commission_usd)
    return per_symbol_trade_value >= threshold
```

If `is_breakeven_valid` returns False at `MIN_AUM_FOR_BREAKEVEN`, the breakeven search
is skipped and `capacity_at_breakeven` is set to `None` with status
`"min_commission_dominated"`. The `None` value ensures breakeven is excluded from the
capacity minimum calculation, preventing an artificial low bound from dominating.

**avg_traded_symbols Computation:**
```python
# Average number of unique symbols traded per day
# Derived from weight changes during cost computation
avg_traded_symbols = mean(count(symbols where |Δw[symbol,D]| > 0) for D in backtest_dates)
```

**Edge Case Handling (Zero Turnover / Zero ADV):**
```python
def _is_valid_numeric(value: float | None) -> bool:
    """Check if a numeric value is valid (not None, NaN, or inf)."""
    return value is not None and math.isfinite(value)

def compute_capacity_with_guards(
    avg_daily_turnover: float | None,
    portfolio_adv: float | None,
    portfolio_sigma: float | None,
    gross_alpha_annualized: float | None,
    cost_config: CostModelConfig,
) -> CapacityAnalysis:
    """Compute capacity with explicit handling for edge cases."""

    # Guard: NaN/inf/None inputs are invalid and cannot produce valid capacity
    # Check all inputs that will be used in capacity formulas
    if not _is_valid_numeric(avg_daily_turnover) or not _is_valid_numeric(gross_alpha_annualized):
        return CapacityAnalysis(
            avg_daily_turnover=avg_daily_turnover,
            portfolio_adv=portfolio_adv,
            portfolio_sigma=portfolio_sigma,
            gross_alpha_annualized=gross_alpha_annualized,
            capacity_at_impact_limit=None,
            capacity_at_participation_limit=None,
            capacity_at_breakeven=None,
            breakeven_status="invalid_input",
            implied_capacity=None,
            binding_constraint="invalid_input",
            max_impact_bps=cost_config.max_impact_bps,
            max_participation=cost_config.adv_participation_limit,
        )

    # Also validate cost_config values used in formulas
    if not _is_valid_numeric(cost_config.max_impact_bps) or not _is_valid_numeric(cost_config.adv_participation_limit):
        return CapacityAnalysis(
            avg_daily_turnover=avg_daily_turnover,
            portfolio_adv=portfolio_adv,
            portfolio_sigma=portfolio_sigma,
            gross_alpha_annualized=gross_alpha_annualized,
            capacity_at_impact_limit=None,
            capacity_at_participation_limit=None,
            capacity_at_breakeven=None,
            breakeven_status="invalid_input",
            implied_capacity=None,
            binding_constraint="invalid_input",
            max_impact_bps=cost_config.max_impact_bps,
            max_participation=cost_config.adv_participation_limit,
        )

    # Guard: Zero turnover means no trading, capacity is infinite
    if avg_daily_turnover <= 0 or avg_daily_turnover < 1e-10:
        return CapacityAnalysis(
            avg_daily_turnover=avg_daily_turnover,
            portfolio_adv=portfolio_adv,
            portfolio_sigma=portfolio_sigma,
            gross_alpha_annualized=gross_alpha_annualized,
            capacity_at_impact_limit=float('inf'),
            capacity_at_participation_limit=float('inf'),
            capacity_at_breakeven=float('inf'),
            breakeven_status="no_turnover",  # No trading, no breakeven constraint
            implied_capacity=float('inf'),
            binding_constraint="none",  # No constraint binds
            max_impact_bps=cost_config.max_impact_bps,
            max_participation=cost_config.adv_participation_limit,
        )

    # Guard: Invalid ADV (None, non-positive, NaN/inf) means we can't compute capacity
    if portfolio_adv is None or portfolio_adv <= 0 or not math.isfinite(portfolio_adv):
        return CapacityAnalysis(
            avg_daily_turnover=avg_daily_turnover,
            portfolio_adv=portfolio_adv,
            portfolio_sigma=portfolio_sigma,
            gross_alpha_annualized=gross_alpha_annualized,
            capacity_at_impact_limit=0.0,
            capacity_at_participation_limit=0.0,
            capacity_at_breakeven=0.0,
            breakeven_status="adv_unavailable",  # Cannot compute breakeven without ADV
            implied_capacity=0.0,  # Conservative: assume zero capacity
            binding_constraint="adv_unavailable",
            max_impact_bps=cost_config.max_impact_bps,
            max_participation=cost_config.adv_participation_limit,
        )

    # Guard: Invalid volatility (None, non-positive, NaN/inf) means we can't compute
    # impact or breakeven, but participation constraint is still computable
    if portfolio_sigma is None or portfolio_sigma <= 0 or not math.isfinite(portfolio_sigma):
        # Compute participation-based capacity (doesn't need volatility)
        capacity_participation = (
            cost_config.adv_participation_limit * portfolio_adv / avg_daily_turnover
            if avg_daily_turnover > 0 else float('inf')
        )
        return CapacityAnalysis(
            avg_daily_turnover=avg_daily_turnover,
            portfolio_adv=portfolio_adv,
            portfolio_sigma=portfolio_sigma,
            gross_alpha_annualized=gross_alpha_annualized,
            capacity_at_impact_limit=None,  # Cannot compute without volatility
            capacity_at_participation_limit=capacity_participation,
            capacity_at_breakeven=None,  # Cannot compute without volatility
            breakeven_status="volatility_unavailable",  # Cannot compute breakeven without volatility
            implied_capacity=capacity_participation,  # Use participation as the only constraint
            binding_constraint="volatility_unavailable",  # Indicates partial computation
            max_impact_bps=cost_config.max_impact_bps,
            max_participation=cost_config.adv_participation_limit,
        )

    # Normal computation (both ADV and volatility available)...
```

**Final Capacity:**
```python
# SPECIAL CASE: If gross alpha <= 0, strategy loses money at ANY AUM
# No capacity analysis is meaningful; report capacity=0 immediately
if gross_alpha_annualized <= 0:
    return CapacityAnalysis(
        avg_daily_turnover=avg_daily_turnover,
        portfolio_adv=portfolio_adv,
        portfolio_sigma=portfolio_sigma,
        gross_alpha_annualized=gross_alpha_annualized,
        capacity_at_impact_limit=None,  # Not computed (gross alpha binds first)
        capacity_at_participation_limit=None,
        capacity_at_breakeven=None,
        breakeven_status="no_positive_alpha",
        implied_capacity=0.0,  # Zero capacity - strategy unprofitable
        binding_constraint="no_positive_alpha",
        max_impact_bps=cost_config.max_impact_bps,
        max_participation=cost_config.adv_participation_limit,
    )

# Compute minimum of available constraints (None values are excluded)
valid_capacities = [
    (AUM_impact, "impact"),
    (AUM_participation, "participation"),
    (AUM_breakeven, "breakeven"),
]
# Filter out None values (unreliable or unavailable constraints)
computable = [(c, name) for c, name in valid_capacities if c is not None]

if not computable:
    capacity = None
    binding_constraint = "all_unavailable"
else:
    capacity, binding_constraint = min(computable, key=lambda x: x[0])

# Edge cases:
# - turnover = 0: capacity = inf, binding_constraint = "none"
# - ADV = 0/None: capacity = 0, binding_constraint = "adv_unavailable"
# - gross_alpha <= 0: capacity = 0, binding_constraint = "no_positive_alpha"
# - min_commission_dominated: breakeven = None (excluded from min)
```

**Capacity Output:**
```python
@dataclass
class CapacityAnalysis:
    # Input metrics (portfolio-level) - all optional to handle guard paths
    avg_daily_turnover: float | None  # Gross daily turnover, None if invalid
    portfolio_adv: float | None  # Weighted average ADV (USD), None if unavailable
    portfolio_sigma: float | None  # Weighted average daily volatility, None if unavailable
    gross_alpha_annualized: float | None  # Gross alpha (annualized %), None if invalid

    # Capacity at each constraint (None if cannot be computed)
    capacity_at_impact_limit: float | None  # AUM at max_impact_bps (needs volatility)
    capacity_at_participation_limit: float | None  # AUM at adv_participation_limit (needs ADV)
    capacity_at_breakeven: float | None  # AUM where net_alpha = 0 (needs both)
    breakeven_status: str | None  # Status from breakeven search:
    # - "found": Valid breakeven AUM found
    # - "no_positive_alpha": Gross alpha ≤ 0 (no breakeven possible)
    # - "net_negative_at_min": Net alpha negative even at minimum AUM
    # - "min_commission_dominated": Min commission floors dominate (result unreliable)
    # - "always_positive": Net alpha positive even at max AUM ($100B)
    # - "adv_unavailable": No ADV data
    # - "volatility_unavailable": No volatility data
    # - "no_return_data": No trading days with valid returns

    # Final capacity (minimum of computable constraints, None if all unavailable)
    implied_capacity: float | None
    binding_constraint: str  # One of: "impact", "participation", "breakeven", "none", "adv_unavailable", "volatility_unavailable", "no_return_data", "invalid_input", "all_unavailable"
    # - "none": Zero turnover, no constraint binds (capacity = inf)
    # - "adv_unavailable": No ADV data available (capacity = 0, all constraints = None)
    # - "volatility_unavailable": No volatility data, participation computed but impact/breakeven = None
    # - "no_return_data": No trading days with valid returns (capacity = None)

    # User-defined constraints used
    max_impact_bps: float  # Default: 10 bps
    max_participation: float  # Default: 5%

    @classmethod
    def from_dict(cls, data: dict) -> "CapacityAnalysis":
        """Reconstruct from summary.json with null/missing field handling.

        All optional fields (float | None) accept null values from JSON.
        Missing keys use None as default for backward compatibility.
        """
        return cls(
            avg_daily_turnover=data.get("avg_daily_turnover"),  # None if missing
            portfolio_adv=data.get("portfolio_adv"),
            portfolio_sigma=data.get("portfolio_sigma"),
            gross_alpha_annualized=data.get("gross_alpha_annualized"),
            capacity_at_impact_limit=data.get("capacity_at_impact_limit"),
            capacity_at_participation_limit=data.get("capacity_at_participation_limit"),
            capacity_at_breakeven=data.get("capacity_at_breakeven"),
            breakeven_status=data.get("breakeven_status"),
            implied_capacity=data.get("implied_capacity"),
            binding_constraint=data.get("binding_constraint", "unknown"),
            max_impact_bps=data.get("max_impact_bps", 10.0),
            max_participation=data.get("max_participation", 0.05),
        )
```

---

### T9.2: UI Configuration - MEDIUM PRIORITY

#### Cost Model Configuration Form

Add a collapsible "Cost Model" section to the backtest configuration form:

```python
# apps/web_console_ng/pages/backtest.py

with ui.expansion("Cost Model Settings", icon="attach_money").classes("w-full"):
    cost_enabled = ui.switch("Enable Cost Model", value=False)

    with ui.column().classes("w-full gap-2").bind_visibility_from(cost_enabled, "value"):
        portfolio_value = ui.number(
            "Portfolio Value (USD)",
            value=1_000_000,
            min=10_000,
            max=1_000_000_000,
            # Note: NiceGUI's ui.number doesn't support format; use prefix/suffix or
            # validation for display formatting. Consider ui.input with mask for $formatting.
        ).props("prefix=$")
        commission_bps = ui.number("Commission (bps)", value=1.0, min=0, max=10, step=0.1)
        min_commission = ui.number("Min Commission (USD)", value=1.0, min=0, max=100)
        spread_bps = ui.number("Spread (bps, full)", value=5.0, min=0, max=50, step=0.5)
        eta = ui.number("Impact Coefficient (eta)", value=0.1, min=0.01, max=1.0, step=0.01)
        # Note: UI shows percent, internally stored as decimal fraction (÷100)
        adv_limit_pct = ui.number("ADV Participation Limit (%)", value=5.0, min=1, max=20)
        max_impact = ui.number("Max Impact (bps)", value=10.0, min=1, max=50, step=1)

# On form submit (example handler):
def build_cost_config() -> CostModelConfig | None:
    if not cost_enabled.value:
        return None  # Disabled = null config (canonical)
    return CostModelConfig(
        enabled=True,  # REQUIRED: Per spec, presence = enabled=True
        commission_bps=commission_bps.value,
        min_commission_usd=min_commission.value,
        spread_bps=spread_bps.value,
        eta=eta.value,
        adv_participation_limit=adv_limit_pct.value / 100,  # Convert % → decimal
        max_impact_bps=max_impact.value,
    )
```

#### Results Display

Display cost summary alongside existing metrics:

```python
# Cost Summary Card - only shown when cost model is present (presence = enabled per spec)
# Note: Per the "disabled = null config" rule, if cost_model_config is present, it is enabled
if result.cost_model_config and result.cost_summary_db:
    with ui.card().classes("w-full"):
        ui.label("Cost Analysis").classes("text-h6")

        with ui.row().classes("w-full gap-4"):
            # Gross vs Net comparison (guard against None values)
            if result.gross_total_return is not None:
                ui.label(f"Gross Return: {result.gross_total_return:.2%}")
            else:
                ui.label("Gross Return: N/A")
            if result.net_total_return is not None:
                ui.label(f"Net Return: {result.net_total_return:.2%}")
            else:
                ui.label("Net Return: N/A")
            # Total cost as % of AUM (cumulative over entire backtest period)
            # Note: This is NOT annualized - it's the total cost divided by starting AUM
            cost_pct_of_aum = result.cost_summary_db.total_cost_usd / result.portfolio_value_usd
            ui.label(f"Total Cost (% of AUM): {cost_pct_of_aum:.2%}")

        with ui.row().classes("w-full gap-4"):
            # Cost breakdown
            ui.label(f"Total Cost: ${result.cost_summary_db.total_cost_usd:,.0f}")
            ui.label(f"Avg Cost: {result.cost_summary_db.avg_cost_bps:.1f} bps of traded value")

        # Net risk metrics (when available)
        if result.net_sharpe is not None:
            ui.label(f"Net Sharpe: {result.net_sharpe:.2f}")
        if result.net_max_drawdown is not None:
            ui.label(f"Net Max Drawdown: {result.net_max_drawdown:.1%}")

        # Warnings for fallbacks/violations
        if result.cost_summary_db.adv_fallback_count > 0:
            ui.label(f"⚠️ ADV fallback used: {result.cost_summary_db.adv_fallback_count} times")
        if result.cost_summary_db.volatility_fallback_count > 0:
            ui.label(f"⚠️ Volatility fallback used: {result.cost_summary_db.volatility_fallback_count} times")
        if result.cost_summary_db.participation_violations > 0:
            ui.label(f"⚠️ Participation violations: {result.cost_summary_db.participation_violations}")
elif result.cost_model_config and not result.cost_summary_db:
    # Edge case: Config exists but summary missing (legacy run or partial write)
    with ui.card().classes("w-full"):
        ui.label("Cost Analysis").classes("text-h6")
        ui.label("⚠️ Cost data unavailable for this backtest.").classes("text-amber-500")
else:
    # Empty state when cost model is disabled (no config present)
    with ui.card().classes("w-full"):
        ui.label("Cost Analysis").classes("text-h6")
        ui.label("Cost model not enabled for this backtest.").classes("text-gray-500")
```

#### Capacity Display

Add capacity analysis section when cost model is enabled:

```python
def format_capacity(value: float | None) -> str:
    """Format capacity value, handling None/inf edge cases."""
    if value is None:
        return "N/A"
    if math.isinf(value):
        return "∞ (unlimited)"
    return f"${value:,.0f}"

# Capacity Analysis Card - only shown when cost model is enabled and capacity computed
if result.capacity_analysis:
    with ui.card().classes("w-full"):
        ui.label("Capacity Analysis").classes("text-h6")

        ui.label(f"Implied Capacity: {format_capacity(result.capacity_analysis.implied_capacity)}")
        ui.label(f"Binding Constraint: {result.capacity_analysis.binding_constraint}")

        with ui.expansion("Constraint Details"):
            ui.label(f"Impact Limit ({result.capacity_analysis.max_impact_bps} bps): {format_capacity(result.capacity_analysis.capacity_at_impact_limit)}")
            ui.label(f"Participation Limit ({result.capacity_analysis.max_participation:.0%}): {format_capacity(result.capacity_analysis.capacity_at_participation_limit)}")
            ui.label(f"Breakeven: {format_capacity(result.capacity_analysis.capacity_at_breakeven)}")
```

---

### T9.4: Backtest Data Export - MEDIUM PRIORITY

#### Export Formats

Support multiple export formats for external verification:

1. **CSV** - Simple tabular format for spreadsheet analysis
2. **Parquet** - Efficient columnar format for large datasets
3. **JSON** - Human-readable summary with metadata

#### Export Contents

**Daily Returns Export (`{job_id}_returns.csv`):**
```csv
date,gross_return,cost_drag,net_return,turnover,total_cost_usd,commission_usd,spread_usd,impact_usd
2024-01-02,0.0012,0.0002,0.0010,0.05,500.0,100.0,150.0,250.0
2024-01-03,-0.0008,0.0003,-0.0011,0.08,800.0,160.0,240.0,400.0
...
```

**Note:** The returns CSV includes cost component columns to enable complete external verification
without requiring the Parquet file. All fields match the `net_portfolio_returns.parquet` schema.

**Cost Breakdown Export (`{job_id}_costs.csv`):**
```csv
date,permno,symbol,weight_change,trade_value_usd,commission_usd,spread_usd,impact_usd,total_cost_usd,adv_usd,volatility,participation_pct,used_adv_fallback,used_vol_fallback
2024-01-02,14593,AAPL,0.02,20000,2.0,5.0,12.5,19.5,50000000,0.025,0.04,false,false
2024-01-02,,GOOG,0.01,10000,1.0,2.5,5.0,8.5,100000000,0.02,0.01,false,false
...
```

**Note:** `permno` is included for PIT backtests; empty for non-PIT/Yahoo backtests (matches Parquet schema).

**Note:** The CSV export includes all columns from the `cost_breakdown.parquet` schema to enable
full external verification. The `volatility` column contains the lagged daily volatility (decimal),
and the fallback flags indicate whether ADV/volatility used the floor fallback values.

**Fallback Field Naming Convention:**
- `used_adv_fallback` / `used_vol_fallback`: Per-trade boolean flags (in cost_breakdown)
- `adv_fallback_count` / `volatility_fallback_count`: Aggregate counts (in cost_summary)
- The counts are the sum of the per-trade flags: `adv_fallback_count = Σ used_adv_fallback`

**Summary Export (`{job_id}_summary.json`):**

**Note:** This is a **separate export schema** from the canonical `summary.json` stored in result directories.
The export schema is restructured for external consumers with grouped sections, while the canonical
`summary.json` uses flat top-level fields for efficient worker writes and result reconstruction.

```json
{
  "job_id": "abc123def456789012345678901234ab",
  "created_by": "user_uuid_12345678",
  "backtest_period": {"start": "2024-01-01", "end": "2024-12-31"},
  "portfolio_value_usd": 1000000,
  "cost_model_config": {...},
  "results": {
    "gross_total_return": 0.152,
    "net_total_return": 0.127,
    "net_sharpe": 1.85,
    "net_max_drawdown": -0.08,
    "total_cost_usd": 25000,
    "avg_daily_turnover": 0.05,
    "participation_violations": 3
  },
  "capacity_analysis": {
    "implied_capacity": 50000000,
    "binding_constraint": "impact",
    "breakeven_status": "found"
  },
  "dataset_version_ids": {...}
}
```

**Schema Distinction:**
| File | Purpose | Structure |
|------|---------|-----------|
| `summary.json` (canonical) | Worker output, result reconstruction | Flat top-level fields |
| `{job_id}_summary.json` (export) | External verification | Grouped under `results`, `capacity_analysis` |

#### UI Export Button

**Security Requirements:**
- Export requires `EXPORT_DATA` permission (similar to existing data access patterns)
- Use NiceGUI's `ui.download` for client-side download triggers
- Implement as FastAPI endpoint for proper authentication/authorization

```python
# apps/web_console_ng/routes/backtest_export.py

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from starlette.responses import JSONResponse
from libs.common.api_auth_dependency import require_permission
from libs.trading.backtest.result_storage import get_result_storage

router = APIRouter(prefix="/api/v1/backtest", tags=["backtest-export"])

import re

# Job ID format: 32 hex characters (SHA256 hash prefix)
JOB_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")

@router.get("/{job_id}/export/{format}")
async def export_backtest(
    job_id: str,
    format: str,
    user: dict = Depends(require_permission("EXPORT_DATA")),  # Permission check
):
    """Export backtest results in specified format.

    Requires EXPORT_DATA permission.
    """
    # Validate job_id format to prevent path traversal or invalid lookups
    if not JOB_ID_PATTERN.match(job_id):
        raise HTTPException(status_code=400, detail="Invalid job_id format")

    # AUTHORIZE BEFORE LOADING: Check ownership from DB (lightweight metadata call)
    # This prevents unnecessary file I/O for unauthorized requests
    from libs.trading.backtest.job_queue import get_job_metadata

    # NOTE: get_job_metadata must be added to job_queue.py (see Files to Modify)
    # Signature: def get_job_metadata(job_id: str) -> dict | None
    # Returns: {"created_by": str, "status": str} or None if not found
    # Implementation: Simple SELECT created_by, status FROM backtest_jobs WHERE id = ?
    job_metadata = get_job_metadata(job_id)
    if job_metadata is None:
        raise HTTPException(status_code=404, detail="Backtest job not found")

    # AUTHORIZE FIRST: Check ownership before exposing job status
    # This prevents leaking job status to unauthorized users
    db_created_by = job_metadata.get("created_by", "unknown")

    # Legacy backtest handling: If created_by is "unknown", only admins can export
    # NOTE: A migration should backfill created_by where possible (e.g., from API
    # request logs if available). For truly orphaned backtests where no creator
    # can be determined, admin-only access is the permanent policy.
    if db_created_by == "unknown":
        if not user.get("is_admin", False):
            logger.info("legacy_export_blocked", job_id=job_id, user_id=user.get("user_id"))
            raise HTTPException(status_code=403, detail="Legacy backtest - admin access required")
    elif db_created_by != user.get("user_id") and not user.get("is_admin", False):
        raise HTTPException(status_code=403, detail="Access denied to this backtest")

    # Check job status AFTER authorization - only export completed jobs
    job_status = job_metadata.get("status", "")
    if job_status != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot export: job status is '{job_status}', must be 'completed'"
        )

    # NOW load the full result (after authorization passes)
    result_storage = get_result_storage()

    try:
        result = result_storage.get_result(job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Backtest result not found")

    # Import export helpers from dedicated module
    from libs.trading.backtest.exporter import (
        generate_csv_export,
        generate_parquet_export,
        generate_json_summary,
    )

    if format == "csv":
        zip_buffer = generate_csv_export(result)
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{job_id}_export.zip"'},
        )
    elif format == "parquet":
        zip_buffer = generate_parquet_export(result)
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{job_id}_parquet.zip"'},
        )
    elif format == "json":
        return JSONResponse(generate_json_summary(result))
    else:
        raise HTTPException(status_code=400, detail=f"Invalid format: {format}")

# NOTE: generate_parquet_export is defined in libs/trading/backtest/exporter.py
# The route imports it from there. See exporter.py for implementation details.
# Below is the reference implementation for exporter.py:

def _generate_parquet_export_impl(result: BacktestResult) -> BytesIO:
    """Generate zip containing all Parquet files for external verification.

    PRODUCTION NOTE: BytesIO approach works for files <50MB (typical backtest size).
    If backtests generate >50MB Parquet files (e.g., multi-year high-frequency data):
    1. Write zip to temp file (not BytesIO) to avoid memory spikes
    2. Stream with FastAPI FileResponse
    3. Use FastAPI background tasks to delete temp file after response completes
    4. Monitor memory usage; implement streaming if OOM errors occur in production
    The 50MB threshold is conservative; adjust based on observed memory patterns.
    """
    import zipfile
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Add net portfolio returns
        if result.net_portfolio_returns_path:
            zf.write(result.net_portfolio_returns_path, "net_portfolio_returns.parquet")
        # Add cost breakdown
        if result.cost_breakdown_path:
            zf.write(result.cost_breakdown_path, "cost_breakdown.parquet")
    zip_buffer.seek(0)
    return zip_buffer


# apps/web_console_ng/pages/backtest.py (UI integration)

def handle_export(job_id: str, format: str):
    """Trigger download via NiceGUI's ui.download (sync function).

    Note: This is intentionally sync because ui.download is sync and
    we're just triggering a browser download, not awaiting anything.
    """
    # ui.download triggers browser download from the authenticated endpoint
    # The session cookie is automatically included, ensuring auth is checked
    ui.download(f"/api/v1/backtest/{job_id}/export/{format}")

# Use functools.partial to bind arguments (avoids lambda coroutine issue)
# NOTE: job_id comes from the result view context (e.g., result.job_id or route param)
from functools import partial

# Example usage in result view (job_id is available from the loaded result):
# job_id = result.job_id  # or from route parameter
with ui.row().classes("gap-2"):
    ui.button("Export CSV", on_click=partial(handle_export, job_id, "csv"))
    ui.button("Export Parquet", on_click=partial(handle_export, job_id, "parquet"))
    ui.button("Export JSON", on_click=partial(handle_export, job_id, "json"))

# Note: Parquet export returns a zip containing both:
# - net_portfolio_returns.parquet (daily aggregates)
# - cost_breakdown.parquet (per-symbol per-day detail)
```

**Security Checks Summary:**
1. **Permission check**: `require_permission("EXPORT_DATA")` ensures user has export rights
2. **Ownership check**: `db_created_by == user.user_id` or admin override
   - **CRITICAL**: Use `get_job_metadata(job_id)` to get `created_by` from DB
   - Do NOT use `result.created_by` from summary.json (user-controlled, untrusted)
   - The `summary.json` `created_by` is for display only, not authorization
3. **Auth integration**: Uses existing `api_auth_dependency` infrastructure
4. **NiceGUI pattern**: `ui.download` properly includes session credentials

#### Files to Create/Modify

1. **Create: `libs/trading/backtest/exporter.py`**
   - `generate_csv_export(result: BacktestResult) -> BytesIO`
   - `generate_json_summary(result: BacktestResult) -> dict`
   - `generate_cost_breakdown_df(result: BacktestResult) -> pl.DataFrame`
   - **CSV Injection Mitigation**: Sanitize ALL string-typed columns (not just symbol) that may
     start with `=`, `+`, `-`, `@` by prefixing with a single quote `'` or tab-prefix escaping
     to prevent formula injection when opened in Excel/Sheets. Apply generically via a
     `sanitize_csv_string(value: str) -> str` helper used for all string columns.

2. **`apps/web_console_ng/pages/backtest.py`**
   - Add export buttons to results view
   - Add download endpoint handlers

3. **`apps/web_console_ng/main.py`**
   - Register `backtest_export` router: `app.include_router(backtest_export_router)`
   - Import: `from apps.web_console_ng.routes.backtest_export import router as backtest_export_router`

---

## Testing Strategy

### Unit Tests

```
tests/libs/trading/backtest/test_cost_model.py
├── test_trade_size_from_weight_change
├── test_cost_application_pipeline_step_by_step
├── test_spread_convention_half_spread
├── test_impact_per_side
├── test_adv_fallback_used
├── test_volatility_fallback_used
├── test_lagged_data_alignment
├── test_config_validation_server_side
├── test_config_size_limit

tests/libs/trading/backtest/test_job_queue_cost.py
├── test_job_id_includes_cost_config
├── test_job_id_changes_with_portfolio_value
├── test_cost_config_persisted_to_db

tests/libs/trading/backtest/test_result_storage_cost.py
├── test_summary_json_includes_cost_fields
├── test_result_reconstruction_with_cost
├── test_net_returns_parquet_loaded
├── test_reconstruction_without_cost_model
```

### Integration Tests

```
tests/libs/trading/backtest/test_worker_cost_integration.py
├── test_pit_adv_volatility_from_snapshot
├── test_dataset_version_ids_includes_cost_source
├── test_net_metrics_computed_server_side
├── test_fallback_counts_in_summary

tests/apps/web_console_ng/test_backtest_cost_ui.py
├── test_cost_config_form_renders
├── test_cost_config_validation
├── test_cost_summary_display
├── test_capacity_display
├── test_fallback_warnings_shown

tests/libs/trading/backtest/test_exporter.py
├── test_csv_export_returns
├── test_csv_export_cost_breakdown
├── test_json_summary_export
├── test_parquet_export
├── test_export_with_missing_cost_data

tests/apps/web_console_ng/test_backtest_export_security.py
├── test_export_requires_permission
├── test_export_ownership_check_blocks_other_users
├── test_export_admin_can_access_any_backtest
├── test_export_incomplete_job_returns_409  # Status != completed guard
├── test_export_legacy_unknown_requires_admin
├── test_export_invalid_job_id_format_rejected
├── test_export_nonexistent_job_returns_404
├── test_export_invalid_format_rejected  # Format must be csv/parquet/json
├── test_result_storage_rejects_unlisted_paths  # Path traversal defense

tests/libs/trading/backtest/test_capacity_analysis.py
├── test_compute_capacity_basic
├── test_capacity_impact_formula_derivation
├── test_capacity_participation_formula
├── test_compute_net_alpha_basic
├── test_compute_net_alpha_guard_negative_return
├── test_compute_net_alpha_high_costs_clamp
├── test_find_breakeven_aum_positive_alpha
├── test_find_breakeven_aum_no_positive_alpha
├── test_find_breakeven_aum_min_commission_dominated
├── test_find_breakeven_aum_adv_unavailable
├── test_find_breakeven_aum_volatility_unavailable
├── test_find_breakeven_aum_none_at_adjusted_min  # None at min_commission threshold
├── test_find_breakeven_aum_none_at_search_min    # None at search minimum
├── test_find_breakeven_aum_none_at_max           # None at MAX_AUM (doesn't return always_positive)
├── test_find_breakeven_aum_none_in_binary_search # None during binary search iteration
├── test_capacity_guards_zero_turnover
├── test_capacity_guards_missing_adv
├── test_capacity_guards_missing_volatility
├── test_trade_weighted_aggregation
├── test_per_day_fallback_before_means
├── test_avg_traded_symbols_computation
```

---

## Definition of Done

**Pre-Implementation (BLOCKING - Complete BEFORE any code):**
- [x] **Create ADR for cost-model-architecture** (required per repo policy)
  - Created: `docs/ADRs/ADR-0034-cost-model-architecture.md`
  - Content: Database schema decisions, cost model architecture, export API design
  - Updated `related_adrs` with ADR-0034

**T9.1 (Transaction Cost Model):**
- [x] AUM/portfolio_value_usd configurable in job config (CostModelConfig.portfolio_value_usd)
- [x] Trade size calculation from weight changes documented and implemented (compute_daily_costs)
- [ ] PIT-compliant ADV/volatility loading with 20-day windows
- [ ] dataset_version_ids extended for cost data source
- [x] Deterministic fallback for missing ADV/volatility with logging (returns 0 impact)
- [ ] Cost config included in job_id hash
- [x] summary.json extended with cost fields (canonical source)
- [ ] BacktestResult reconstruction includes cost data
- [ ] Server-side validation with size limits
- [x] Unit tests for cost model core (46 tests in test_cost_model.py)

**T9.2 (UI Configuration):**
- [ ] Cost model configuration form in backtest.py
- [ ] Portfolio value input with validation
- [ ] Cost summary display in results view
- [ ] Capacity analysis display with constraint details
- [ ] Warning indicators for fallbacks and violations

**T9.3 (Capacity Analysis):**
- [ ] Capacity defined with explicit constraints and formulas
- [ ] Trade-weighted ADV/volatility aggregation
- [ ] Net alpha computation implemented (compounded basis)
- [ ] Binary search for breakeven AUM with pre-checks

**T9.4 (Backtest Export):**
- [ ] CSV export with daily returns and cost breakdown
- [ ] Parquet export for net returns
- [ ] JSON summary export with metadata
- [ ] Export buttons in UI with download handlers

- [ ] Code reviewed and approved

---

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| PIT data not available for all permnos | Medium | Fallback with logging, warn in UI |
| Large cost_model_config payloads | Low | 4KB size limit, server validation |
| Snapshot versioning complexity | Medium | Reuse existing snapshot infrastructure |

---

**Last Updated:** 2026-01-29
**Status:** PLANNING
**Review Iteration:** 47 (fixed net_sharpe to use valid_returns, moved ownership check before status check in export, added CapacityAnalysis.from_dict with null handling, expanded CSV injection mitigation to all string columns)
