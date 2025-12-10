# T3.1: Microstructure Analytics - Implementation Plan

**Task:** T3.1 Microstructure Analytics
**Effort:** 4-5 days
**Dependencies:** T1.7 (TAQ Storage) + T1.8 (TAQ Query) - COMPLETE
**Branch:** feature/P4T2 (continuing)
**Version:** 11.3 (Fixes: sigma=0 guard before Z, ex-ante sigma window)

---

## Overview

Implement market microstructure analytics using TAQ tick data:
1. Realized volatility calculation (5-min sampling)
2. VPIN (Volume-synchronized PIN) using Bulk Volume Classification (BVC)
3. Intraday volatility patterns (U-shape analysis)
4. HAR volatility forecasting model
5. **Spread AND depth statistics** (both required)

---

## Architecture

### Module Structure

```
libs/analytics/
    __init__.py
    microstructure.py     # MicrostructureAnalyzer
    volatility.py         # HARVolatilityModel

tests/libs/analytics/
    __init__.py
    test_microstructure.py
    test_volatility.py

docs/CONCEPTS/
    microstructure.md
    realized-volatility.md
```

### Constructor Design (SIMPLIFIED - Single Source of Truth)

```python
class MicrostructureAnalyzer:
    """Analyze market microstructure from TAQ data.

    Parameters:
        taq_provider: TAQLocalProvider - data access AND version resolution
            - taq_provider.manifest_manager is used for current data version IDs
            - taq_provider.version_manager is used for PIT queries (as_of)

    Note: This class uses taq_provider as the SINGLE SOURCE for both data
    and version resolution to avoid manifest/version divergence.
    """

    def __init__(self, taq_provider: TAQLocalProvider) -> None:
        self.taq = taq_provider
```

### Dependencies

- `libs.data_providers.taq_query_provider.TAQLocalProvider`
- `polars`, `numpy`, `scipy.stats`

---

## PIT/Versioning Design (COMPREHENSIVE)

### Single-Dataset Version Resolution

```python
def _get_version_id(self, dataset: str, as_of: date | None = None) -> str:
    """Get dataset version ID from taq_provider.

    Two paths:
    1. as_of provided: Use taq_provider.version_manager.query_as_of()
       - Raises DataNotFoundError if no snapshot available
       - Raises ValueError if version_manager not configured
    2. as_of is None: Use taq_provider.manifest_manager.get_manifest()
       - Returns manifest.checksum as version ID
       - Returns "unknown" if manifest not found
    """
    if as_of:
        if self.taq.version_manager is None:
            raise ValueError("version_manager required for PIT queries")
        path, snapshot = self.taq.version_manager.query_as_of(dataset, as_of)
        # Explicit check to raise DataNotFoundError instead of KeyError
        if dataset not in snapshot.datasets:
            raise DataNotFoundError(f"Dataset '{dataset}' not found in snapshot at {as_of}")
        return str(snapshot.datasets[dataset].sync_manifest_version)
    else:
        manifest = self.taq.manifest_manager.get_manifest(dataset)
        return manifest.checksum if manifest else "unknown"
```

### Multi-Dataset Version Resolution (SINGLE SNAPSHOT ENFORCEMENT)

```python
@dataclass
class CompositeVersionInfo:
    """Version info for methods that use multiple datasets."""
    versions: dict[str, str]  # dataset -> version_id
    snapshot_id: str | None   # Snapshot aggregate_checksum (PIT only)
    is_pit: bool              # True if from PIT snapshot

    @property
    def composite_version_id(self) -> str:
        """Deterministic composite version ID.

        Construction:
        - Sort datasets alphabetically
        - Join as "ds1:v1|ds2:v2|..."
        - If PIT, append "|snapshot:<snapshot_id>"
        - Hash with SHA256, take first 32 chars (128 bits, negligible collision risk)

        Example: "taq_samples_20240102:abc123|taq_spread_stats:def456|snapshot:xyz789"
        -> SHA256 -> first 32 chars
        """
        parts = [f"{ds}:{v}" for ds, v in sorted(self.versions.items())]
        if self.snapshot_id:
            parts.append(f"snapshot:{self.snapshot_id}")
        combined = "|".join(parts)
        return hashlib.sha256(combined.encode()).hexdigest()[:32]

def _get_multi_version_id(
    self,
    datasets: list[str],
    as_of: date | None = None,
) -> CompositeVersionInfo:
    """Get version IDs for multiple datasets from SINGLE SNAPSHOT.

    For PIT queries (as_of provided):
    - Perform ONE snapshot lookup for first dataset
    - Verify ALL datasets exist in that snapshot
    - Raise DataNotFoundError if ANY dataset missing from snapshot
    - Return snapshot_id for reproducibility

    For current data (as_of is None):
    - Each dataset uses its own manifest
    - snapshot_id is None
    - is_pit is False
    """
    if as_of:
        if self.taq.version_manager is None:
            raise ValueError("version_manager required for PIT queries")

        # Single snapshot lookup
        path, snapshot = self.taq.version_manager.query_as_of(datasets[0], as_of)

        # Verify ALL datasets in same snapshot
        versions = {}
        for ds in datasets:
            if ds not in snapshot.datasets:
                raise DataNotFoundError(
                    f"Dataset '{ds}' not found in snapshot at {as_of}. "
                    f"Available: {list(snapshot.datasets.keys())}"
                )
            versions[ds] = str(snapshot.datasets[ds].sync_manifest_version)

        return CompositeVersionInfo(
            versions=versions,
            snapshot_id=snapshot.aggregate_checksum,
            is_pit=True,
        )
    else:
        # Current data - each dataset from its manifest
        versions = {}
        for ds in datasets:
            manifest = self.taq.manifest_manager.get_manifest(ds)
            versions[ds] = manifest.checksum if manifest else "unknown"

        return CompositeVersionInfo(
            versions=versions,
            snapshot_id=None,
            is_pit=False,
        )
```

### Dataset Naming Convention (ALIGNED WITH TAQLocalProvider)

The following dataset names are used consistently with TAQLocalProvider:
- `taq_1min_bars` - 1-minute OHLCV bars
- `taq_daily_rv` - Precomputed daily realized volatility
- `taq_spread_stats` - Precomputed spread statistics
- `taq_samples_YYYYMMDD` - Tick samples for specific date (e.g., `taq_samples_20240102`)

**Important:** For taq_samples, the date is embedded in the dataset name. Methods must construct the correct dataset name before querying.

### PIT Support Per Method (DETAILED)

| Method | Dataset(s) | PIT Behavior |
|--------|------------|--------------|
| compute_realized_volatility | `taq_1min_bars` OR `taq_daily_rv` | Single dataset, single version |
| compute_vpin | `taq_samples_YYYYMMDD` | Single dataset per date; construct name from date param |
| analyze_intraday_pattern | `taq_1min_bars` | If as_of: use PIT snapshot. Else: use current manifest. |
| compute_spread_depth_stats | `taq_spread_stats` + `taq_samples_YYYYMMDD` | Multi-dataset; single snapshot; CompositeVersionInfo |

### Result Base Class

```python
@dataclass
class MicrostructureResult:
    """Base result with versioning metadata."""
    dataset_version_id: str              # Primary version (or composite_version_id for multi)
    dataset_versions: dict[str, str] | None  # Per-dataset versions (multi-dataset methods)
    computation_timestamp: datetime
    as_of_date: date | None
```

---

## Component 1: MicrostructureAnalyzer

### 1.1 Realized Volatility

**Formula:**
```
Daily RV = sqrt(sum(r_i^2))  where r_i = log(P_i / P_{i-1})
  - P_i are INTRADAY prices sampled at sampling_freq_minutes intervals
  - Returns are intraday log returns (e.g., 5-minute), NOT daily close-to-close
  - Sum is over all intraday intervals within [market_open, market_close]

Annualized RV = Daily RV * sqrt(252)
  - Assumes 252 trading days per year
  - This converts daily RV to annualized volatility
```

**Result:**
```python
@dataclass
class RealizedVolatilityResult(MicrostructureResult):
    symbol: str
    date: date
    rv_daily: float
    rv_annualized: float
    sampling_freq_minutes: int
    num_observations: int
```

**Method:**
```python
def compute_realized_volatility(
    self,
    symbol: str,
    date: date,
    sampling_freq_minutes: int = 5,
    as_of: date | None = None,
) -> RealizedVolatilityResult:
```

**Optimization:** Use precomputed RV for 5/30 min frequencies from taq_daily_rv.

**Edge cases:**
- <10 observations: return NaN with warning
- Missing bars: continue with available data
- PIT failure: raise DataNotFoundError

### 1.2 VPIN (Volume-synchronized PIN) - FULLY DETERMINISTIC ALGORITHM

**Formula (BVC per Easley et al. 2012):**
```
r_i = log(P_i / P_{i-1})   # Log return for trade i
Z = r_i / sigma            # Standardized return (dimensionally consistent)
V_buy = V * Phi(Z)
V_sell = V - V_buy
VPIN = |sum(V_buy) - sum(V_sell)| / total_volume over window
```
**Note:** Both Z numerator (log return) and sigma (std of log returns) are dimensionless,
ensuring scale-invariant classification regardless of stock price level.

**Algorithm Specification (FULLY DETERMINISTIC):**

1. **Data Source:** Use `taq_provider.fetch_ticks()` for tick-level data
   - Columns required: ts, trade_px, trade_size
   - Sort by ts ascending

2. **Sigma Estimation (EX-ANTE - prior returns only):**
   - Use rolling window of `sigma_lookback` log-returns from PRIOR trades only
   - sigma = std(log(P_j / P_{j-1}) for j in [i-sigma_lookback, i-1], ddof=1)
   - **ddof=1** (sample std with Bessel's correction) for reproducibility
   - Window EXCLUDES current trade's return: uses returns from trades [i-sigma_lookback, i-1]
     (requires prices from trades [i-sigma_lookback-1, i-1], i.e., sigma_lookback+1 prior prices)
   - **Rationale:** Ex-ante evaluation - classify trade against volatility known BEFORE the trade
     occurred, avoiding dampening of outlier detection during shocks
   - First valid sigma at trade index sigma_lookback (0-indexed, need sigma_lookback prior returns)
   - Minimum warmup: sigma_lookback trades before sigma is valid

3. **Warmup Period Handling:**
   - Trades 0 to (sigma_lookback - 1): sigma is NaN, skip these trades for bucket volume
   - First valid sigma at trade index sigma_lookback
   - Buckets before window_buckets complete: VPIN = NaN in output, is_warmup = True
   - First valid VPIN at bucket index (window_buckets - 1)

   **Rationale for excluding warmup trades from bucket volume:**
   - Without valid sigma, we cannot compute meaningful V_buy/V_sell classification
   - Including unclassified volume would dilute VPIN accuracy
   - This matches BVC literature where classification requires price history
   - Bucket boundaries start AFTER warmup to ensure all buckets have valid VPIN

   **Alternative approach (NOT implemented - documented for reference):**
   - Include warmup trades in buckets with neutral classification (V_buy = V_sell = 0.5 * volume)
   - First VPIN values would still be NaN until window_buckets complete
   - This preserves bucket timing and avoids dead period at market open

   **Decision:** Exclude warmup trades to ensure all VPIN values use fully-informed
   classifications. Acceptable for backtesting; live trading should pre-warm sigma
   using previous day's close prices.

4. **Bucket Construction:**
   - Fixed volume per bucket: `volume_per_bucket` shares
   - Accumulate trades until cumulative volume >= volume_per_bucket
   - Bucket timestamp = timestamp of last trade in bucket
   - **is_partial flag semantics:**
     - is_partial = True for the LAST bucket if total accumulated volume < volume_per_bucket at EOD
     - All other buckets have is_partial = False
     - Partial buckets are included in output but marked for downstream filtering if needed

5. **Trade Splitting (DETERMINISTIC OVERFLOW HANDLING - ITERATIVE):**
   When a trade causes bucket overflow, handle ITERATIVELY for trades spanning multiple buckets:
   ```python
   remaining_trade_volume = trade_size
   remaining_v_buy = v_buy
   remaining_v_sell = v_sell

   while remaining_trade_volume > 0:
       remaining_capacity = volume_per_bucket - current_bucket_volume

       if remaining_trade_volume <= remaining_capacity:
           # Trade fits in current bucket
           current_bucket_volume += remaining_trade_volume
           current_bucket_v_buy += remaining_v_buy
           current_bucket_v_sell += remaining_v_sell
           remaining_trade_volume = 0
       else:
           # Split trade: fill current bucket, continue with excess
           split_ratio = remaining_capacity / remaining_trade_volume
           current_bucket_volume += remaining_capacity
           current_bucket_v_buy += remaining_v_buy * split_ratio
           current_bucket_v_sell += remaining_v_sell * split_ratio

           # Finalize current bucket
           finalize_bucket()

           # Update remaining for next bucket
           remaining_trade_volume -= remaining_capacity
           remaining_v_buy *= (1 - split_ratio)
           remaining_v_sell *= (1 - split_ratio)

           # Start new bucket
           current_bucket_volume = 0
           current_bucket_v_buy = 0
           current_bucket_v_sell = 0
   ```
   **Note:** This handles trades of ANY size, including those spanning 3+ buckets.

   **Timestamp assignment (DETERMINISTIC):**
   - Each bucket receives the timestamp of the LAST trade that contributed volume to it
   - For a single trade split across multiple buckets, ALL affected buckets receive that trade's timestamp
   - For buckets completed by multiple trades, use the timestamp of the final completing trade
   - This ensures deterministic bucket timestamps independent of trade sizes

6. **Volume Classification (BVC):**
   - For each trade: r_i = log(P_i / P_{i-1}), then Z = r_i / sigma
   - **Sigma=0 guard (MUST check BEFORE computing Z):**
     - If sigma <= 0: set Z = 0, V_buy = V_sell = trade_size / 2 (neutral classification)
     - Mark bucket as sigma_zero_contaminated = True
     - VPIN for windows containing sigma=0 trades = NaN
   - V_buy = trade_size * Phi(Z)  where Phi is standard normal CDF
   - V_sell = trade_size - V_buy
   - Aggregate V_buy and V_sell per bucket
   - Note: Using log returns ensures scale-invariance across different price levels

7. **Zero/Flat Price Handling:**
   - If P_i == P_{i-1}: r_i = 0, Z = 0, V_buy = V_sell = trade_size / 2
   - If sigma == 0 (no price variation over lookback): handled in step 6 above
     (NaN preserves signal that imbalance is indeterminate; 0 would falsely suggest balance)

8. **VPIN Calculation:**
   - Rolling window of `window_buckets` buckets
   - VPIN_n = |sum(V_buy) - sum(V_sell)| / sum(total_volume) over window
   - First valid VPIN at bucket index (window_buckets - 1)

**Parameters:**
- `volume_per_bucket: int = 10000` - Fixed volume per bucket (BVC standard)
- `window_buckets: int = 50` - Rolling window of buckets for VPIN
- `sigma_lookback: int = 20` - Trades for rolling sigma

**Result:**
```python
@dataclass
class VPINResult(MicrostructureResult):
    symbol: str
    date: date
    data: pl.DataFrame  # [bucket_id, vpin, cumulative_volume, imbalance, timestamp, is_partial, is_warmup]
    num_buckets: int
    num_valid_vpin: int      # Buckets with non-NaN VPIN
    avg_vpin: float          # Average over valid buckets only
    warnings: list[str]      # e.g., ["sigma=0 detected", "partial bucket at EOD"]
```

**Method:**
```python
def compute_vpin(
    self,
    symbol: str,
    date: date,
    volume_per_bucket: int = 10000,
    window_buckets: int = 50,
    sigma_lookback: int = 20,
    as_of: date | None = None,
) -> VPINResult:
```

**Edge cases:**
- sigma=0: return VPIN=NaN for affected buckets with warning
- Empty day: return empty DataFrame with warning
- <window_buckets buckets: return partial results with is_warmup=True
- Zero-volume trades: skip with debug log; count and add to warnings if >5% of trades
- PIT failure: raise DataNotFoundError
- **No valid buckets (day ends during warmup):** Return VPINResult with:
  - data: empty DataFrame with correct schema
  - num_buckets: 0
  - num_valid_vpin: 0
  - avg_vpin: NaN
  - warnings: ["Day ended during warmup period - no valid buckets"]

### 1.3 Intraday Pattern

**Result:**
```python
@dataclass
class IntradayPatternResult(MicrostructureResult):
    symbol: str
    start_date: date
    end_date: date
    data: pl.DataFrame  # [time_bucket, avg_volatility, avg_spread, avg_volume, n_days]
```

**Method:**
```python
def analyze_intraday_pattern(
    self,
    symbol: str,
    start_date: date,
    end_date: date,
    bucket_minutes: int = 30,
    as_of: date | None = None,
) -> IntradayPatternResult:
```

**PIT/as_of Precedence (CLARIFIED):**
- If as_of provided: Use PIT snapshot at as_of date for ALL data
- If as_of is None: Use current manifest (no PIT guarantee)
- Warning logged if range spans >5 trading days (informational only)

**Aggregation Logic:**
- For each time bucket (e.g., 09:30-10:00), compute average across all trading days
- Each day contributes equally to the average (NOT weighted by n_bars)
- The 'n_days' column in output shows how many days contributed to each bucket
- Example: 09:30 bucket might have n_days=20, but 15:00 bucket might have n_days=15 (excludes 5 half-days)

**Edge cases:**
- Holidays (0 bars): excluded from average entirely
- Half-days: contribute their available time buckets; missing buckets simply reduce n_days
- PIT failure: raise DataNotFoundError

### 1.4 Spread AND Depth Statistics - PRECISE DEFINITIONS

**Spread Formulas (from taq_spread_stats):**
- **QWAP Spread:** Quote-weighted average percentage spread
  - `qwap = sum(spread_i * size_i) / sum(size_i)` where spread_i = (ask - bid) / midpoint
- **EWAS:** Equal-weighted average spread (simple mean of spreads)

**Depth Formulas (from taq_samples tick data - QUOTE-ONLY FILTER):**

**Required taq_samples schema for depth computation:**
| Column | Type | Description |
|--------|------|-------------|
| ts | datetime | Quote timestamp |
| bid | float | Bid price |
| ask | float | Ask price |
| bid_size | int | Bid depth (shares) |
| ask_size | int | Ask depth (shares) |

Optional: `record_type` (str) - if present, filter to 'quote'

```python
def _compute_depth_from_ticks(self, ticks: pl.DataFrame) -> tuple[float, float]:
    """Compute time-weighted L1 depth from tick data.

    QUOTE-ONLY FILTER (hierarchical logic):
    1. If 'record_type' column exists: Filter to record_type == 'quote'
    2. Otherwise, apply fallback: bid_size > 0 AND ask_size > 0 (both sides valid)
       - Exclude rows where EITHER side is 0 (incomplete/one-sided quotes)
       - This avoids biasing depth toward artificially low values

    Additional validation (applied after filtering):
    - Exclude rows where bid == 0 or ask == 0 (invalid prices)
    - Exclude crossed markets (bid > ask) from depth calculation
    - Count and flag these exclusions in data quality metrics

    Time-weighting:
    - Duration = time to next quote update (or EOD for last quote)
    - avg_bid_depth = sum(bid_size_i * duration_i) / sum(duration_i)
    - avg_ask_depth = sum(ask_size_i * duration_i) / sum(duration_i)

    Returns: (avg_bid_depth, avg_ask_depth)
    """
```

**Data Source Decision:**
- Spread metrics: Use precomputed taq_spread_stats (trust validated data)
- Depth metrics: Compute from taq_samples tick data (quote-only rows)
- Version tracking: CompositeVersionInfo with single snapshot enforcement

**Result:**
```python
@dataclass
class SpreadDepthResult(MicrostructureResult):
    symbol: str
    date: date
    # Spread metrics (from taq_spread_stats - precomputed)
    qwap_spread: float
    ewas: float
    # Depth metrics (computed from taq_samples ticks, quote-only rows)
    avg_bid_depth: float       # Time-weighted L1 bid depth
    avg_ask_depth: float       # Time-weighted L1 ask depth
    avg_total_depth: float     # avg_bid_depth + avg_ask_depth
    depth_imbalance: float     # (avg_bid - avg_ask) / avg_total
    # Counts (SOURCE CLARIFICATION)
    quotes: int                # From taq_spread_stats (precomputed)
    trades: int                # From taq_spread_stats (precomputed)
    # Data quality flags (computed from taq_samples ticks)
    has_locked_markets: bool   # bid >= ask detected in ticks
    has_crossed_markets: bool  # bid > ask detected in ticks
    locked_pct: float          # % of tick quotes that were locked
    crossed_pct: float         # % of tick quotes that were crossed
    stale_quote_pct: float     # % tick quotes unchanged >1min
    # Fallback indicators
    depth_is_estimated: bool   # True if tick data missing, depth = NaN
```

**Data Source Summary:**
| Field | Source | Notes |
|-------|--------|-------|
| qwap_spread, ewas | taq_spread_stats | Precomputed, validated |
| quotes, trades | taq_spread_stats | Precomputed counts |
| avg_*_depth, depth_imbalance | taq_samples (ticks) | Computed from quote-only rows |
| has_locked_markets, has_crossed_markets | taq_samples (ticks) | Computed from quote-only rows |
| locked_pct, crossed_pct, stale_quote_pct | taq_samples (ticks) | Computed from quote-only rows |

**Method:**
```python
def compute_spread_depth_stats(
    self,
    symbol: str,
    date: date,
    stale_threshold_seconds: int = 60,  # Configurable for different liquidity levels
    as_of: date | None = None,
) -> SpreadDepthResult:
```

**Stale Quote Detection Algorithm:**
```python
def _compute_stale_quote_pct(
    self,
    quotes: pl.DataFrame,
    threshold_seconds: int = 60,
) -> float:
    """Compute percentage of stale quotes (unchanged >threshold).

    Default threshold: 60 seconds (suitable for liquid stocks)
    Adjust threshold based on symbol liquidity:
    - High liquidity (e.g., SPY): 5-10 seconds
    - Medium liquidity: 30-60 seconds
    - Low liquidity: 120+ seconds

    Algorithm:
    1. Sort quotes by timestamp
    2. Create columns: prev_bid, prev_ask, prev_bid_size, prev_ask_size, prev_ts
    3. Quote is stale if ALL of:
       - bid == prev_bid AND ask == prev_ask
       - bid_size == prev_bid_size AND ask_size == prev_ask_size (depth also unchanged)
       - (ts - prev_ts) > threshold_seconds
    4. stale_quote_pct = count(stale) / count(total)

    Returns: percentage in [0, 1]
    """
```

**Stale Quote Behavior:**
- If stale_quote_pct > 0.50: Log warning, depth values may be unreliable
- Stale quotes are INCLUDED in depth calculation (represent actual market state)
- Flag is informational only; downstream consumers decide filtering policy

**Edge cases (COMPREHENSIVE):**
- **Locked markets (bid = ask):** Flag in result via tick analysis, exclude from DEPTH calc, count percentage
  (Note: taq_spread_stats already handles locked/crossed in precomputed spreads; flags are for tick-level QA)
- **Crossed markets (bid > ask):** Flag in result via tick analysis, exclude from DEPTH calc, count percentage
- **Stale quotes (>1 min unchanged):** Track percentage, warn if >50%, include in depth calc
- **Zero depth:** return 0.0 with warning
- **Missing tick data (taq_samples):** Return spread metrics only, depth = NaN, depth_is_estimated = True
- **Missing spread stats (taq_spread_stats):** Raise DataNotFoundError (no fallback)
- **PIT failure:** raise DataNotFoundError
- **Multi-dataset from different snapshots (PIT):** Raise DataNotFoundError - enforced by single snapshot lookup

---

## Component 2: HARVolatilityModel - COMPLETE SPECIFICATION

### 2.1 HAR Model Formula (Corsi 2009)

```
RV_{t+h} = c + b_d * RV_t + b_w * RV_t^w + b_m * RV_t^m + e

Where:
- RV_t = daily realized volatility at time t
- RV_t^w = average RV over past 5 days (weekly)
- RV_t^m = average RV over past 22 days (monthly)
- h = forecast horizon (default: 1 day)
```

### 2.2 Feature Construction

```python
def _construct_har_features(self, rv_series: pl.DataFrame) -> pl.DataFrame:
    """Construct HAR features from RV series.

    Input: DataFrame with ['date', 'rv'] columns, sorted by date ascending

    Features constructed (ALL LAGGED to prevent look-ahead bias):
    - rv_d: RV_{t-1} (lag-1 daily RV)
    - rv_w: mean(RV_{t-5}, ..., RV_{t-1}) (5-day average of lags 1-5)
    - rv_m: mean(RV_{t-22}, ..., RV_{t-1}) (22-day average of lags 1-22)

    Note: All lags exclude RV_t to prevent look-ahead bias. The weekly component
    uses lags 1-5 (same data as daily lag plus 4 more), and monthly uses lags 1-22.

    NaN handling:
    - Rolling means use min_periods=1 initially
    - First 22 rows may have partially-populated rv_m

    Returns: DataFrame with ['date', 'rv', 'rv_d', 'rv_w', 'rv_m', 'rv_target']
    where rv_target = RV_{t+h} (shifted by forecast horizon)
    """
```

### 2.3 Estimation Details

```python
class HARVolatilityModel:
    """HAR-RV volatility forecasting model.

    Estimator: Ordinary Least Squares (OLS) via numpy.linalg.lstsq
    - Simple, deterministic, numerically stable
    - No robust SE needed for forecasting (only point estimates)

    Version tracking:
    - dataset_version_id provided at fit() time
    - Stored in model metadata for reproducibility
    """

    def __init__(self, forecast_horizon: int = 1) -> None:
        """Initialize HAR model.

        Args:
            forecast_horizon: Number of days ahead to forecast (default: 1)
        """
        self.horizon = forecast_horizon
        self._fitted = False
        self._coefficients: np.ndarray | None = None  # [c, b_d, b_w, b_m]
        self._r_squared: float | None = None
        self._dataset_version_id: str | None = None
        self._fit_timestamp: datetime | None = None
        self._n_observations: int | None = None

    def fit(
        self,
        realized_vol: pl.DataFrame,  # [date, rv]
        dataset_version_id: str,
    ) -> HARModelResult:
        """Fit HAR model using OLS.

        Args:
            realized_vol: DataFrame with 'date' and 'rv' columns
                - Must be sorted by date ascending
                - Must have at least 60 observations
                - RV values should be non-annualized daily RV
            dataset_version_id: Version ID from source RV data

        Returns:
            HARModelResult with coefficients, R², and metadata

        Raises:
            ValueError: <60 days, non-monotonic dates, or >5 consecutive NaNs
        """
```

### 2.4 Model Result and Forecast

```python
@dataclass
class HARModelResult:
    """Result of HAR model fitting."""
    intercept: float           # c
    coef_daily: float          # b_d
    coef_weekly: float         # b_w
    coef_monthly: float        # b_m
    r_squared: float
    n_observations: int
    dataset_version_id: str
    fit_timestamp: datetime
    forecast_horizon: int

@dataclass
class HARForecastResult:
    """Result of HAR forecast."""
    forecast_date: date
    rv_forecast: float         # Forecasted RV (non-annualized)
    rv_forecast_annualized: float  # * sqrt(252)
    model_r_squared: float
    dataset_version_id: str
```

```python
def forecast(self, current_rv: pl.DataFrame) -> HARForecastResult:
    """Generate h-day ahead forecast.

    Args:
        current_rv: DataFrame with recent RV data
            - Must have at least 22 rows for monthly lag
            - Latest date is t, forecast is for t+h

    Returns:
        HARForecastResult with point forecast

    Raises:
        RuntimeError: If called before fit()
        ValueError: If insufficient data for lags
    """
```

### 2.5 Edge Cases

| Edge Case | Handling |
|-----------|----------|
| < 60 days training data | Raise ValueError("Minimum 60 observations required") |
| NaN in RV | Forward-fill up to 5 consecutive, then raise ValueError |
| Non-monotonic dates | Raise ValueError("Dates must be monotonically increasing") |
| Negative RV values | Raise ValueError("RV must be non-negative") |
| forecast() before fit() | Raise RuntimeError("Model not fitted") |
| forecast() with <22 rows | Raise ValueError("Need 22 rows for monthly lag") |
| All-zero RV | Model fits but warns; forecasts will be near-zero |

---

## Test Plan (EXPANDED - 62 tests)

### test_microstructure.py (47 tests)

**RV Tests (7):**
1. `test_rv_computation` - formula correct → covers: rv_daily/rv_annualized calculation
2. `test_rv_uses_precomputed` - 5/30 optimization → covers: precomputed RV path
3. `test_rv_missing_data` - returns NaN → covers: <10 observations branch
4. `test_rv_includes_version_id` - metadata present → covers: _get_version_id() current path
5. `test_rv_with_as_of` - PIT works → covers: _get_version_id() PIT path
6. `test_rv_pit_failure` - raises DataNotFoundError → covers: PIT error handling
7. `test_rv_missing_manifest` - version_id="unknown" → covers: missing manifest branch

**VPIN Tests (17):**
8. `test_vpin_basic` - known imbalance → covers: BVC formula, bucket aggregation
9. `test_vpin_range` - [0, 1] → covers: VPIN normalization
10. `test_vpin_volume_per_bucket` - bucket construction → covers: bucket volume logic
11. `test_vpin_sigma_zero` - returns VPIN=NaN → covers: sigma=0 branch, warning
12. `test_vpin_flat_prices` - Z=0 case → covers: P_i == P_{i-1} branch
13. `test_vpin_empty_day` - returns empty → covers: empty data branch
14. `test_vpin_partial_bucket` - EOD handling → covers: is_partial=True logic
15. `test_vpin_bucket_overflow` - trade splitting → covers: split_ratio algorithm
16. `test_vpin_multi_bucket_overflow` - large trade spans 3+ buckets → covers: iterative while-loop
17. `test_vpin_warmup_period` - is_warmup=True → covers: warmup flag logic
18. `test_vpin_sigma_warmup` - sigma NaN → covers: sigma warmup trades skip
19. `test_vpin_no_valid_buckets` - day ends in warmup → covers: no-valid-buckets edge case
20. `test_vpin_uses_fetch_ticks` - data source → covers: fetch_ticks integration
21. `test_vpin_includes_version_id` - metadata → covers: _get_version_id() call
22. `test_vpin_pit_failure` - raises DataNotFoundError → covers: PIT error path
23. `test_vpin_insufficient_buckets` - partial results → covers: <window_buckets branch
24. `test_vpin_trade_split_ratio` - v_buy/v_sell ratio → covers: split preserves ratio

**Intraday Tests (7):**
25. `test_intraday_u_shape` - pattern → covers: volatility averaging
26. `test_intraday_timezone` - ET → covers: timezone handling
27. `test_intraday_half_day` - handled → covers: partial day weighting
28. `test_intraday_holidays` - excluded → covers: 0-bar day exclusion
29. `test_intraday_includes_version_id` - metadata → covers: version tracking
30. `test_intraday_pit_failure` - raises DataNotFoundError → covers: PIT error path
31. `test_intraday_asof_precedence` - as_of override → covers: PIT vs current manifest

**Spread/Depth Tests (12):**
32. `test_spread_retrieval` - from precomputed → covers: taq_spread_stats fetch
33. `test_depth_computation` - time-weighted → covers: depth calculation
34. `test_depth_quote_only_filter` - excludes trades → covers: quote filter logic
35. `test_depth_imbalance` - formula → covers: imbalance calculation
36. `test_spread_depth_composite_version` - deterministic → covers: composite_version_id
37. `test_depth_empty_book` - zero → covers: zero depth branch
38. `test_spread_depth_pit` - single snapshot → covers: _get_multi_version_id()
39. `test_spread_depth_pit_missing_dataset` - dataset not in snapshot → covers: DataNotFoundError
40. `test_locked_markets` - flagged → covers: locked detection, locked_pct
41. `test_crossed_markets` - flagged → covers: crossed detection, crossed_pct
42. `test_stale_quotes_high_pct` - >50% stale quotes → covers: stale detection, warning
43. `test_spread_only_fallback` - ticks missing → covers: depth_is_estimated=True

**Determinism Tests (4):**
44. `test_vpin_deterministic_rerun` - identical VPIN on re-execution → covers: split_ratio determinism
45. `test_composite_version_deterministic` - same datasets → same hash → covers: hash consistency
46. `test_har_forecast_deterministic` - same RV series → identical forecast → covers: OLS reproducibility
47. `test_depth_calculation_deterministic` - same quotes → same depth → covers: time-weighting reproducibility

### test_volatility.py (15 tests)

**HAR Fitting (5):**
1. `test_har_fit_basic` - model fits → covers: fit() happy path
2. `test_har_coefficients` - values reasonable → covers: coefficient extraction
3. `test_har_r_squared` - in [0, 1] → covers: R² calculation
4. `test_har_lag_construction` - features correct → covers: _construct_har_features()
5. `test_har_version_id_stored` - metadata persisted → covers: version tracking

**HAR Edge Cases (6):**
6. `test_har_insufficient_data` - ValueError <60 days → covers: minimum data check
7. `test_har_nan_handling` - forward-fill up to 5 → covers: NaN forward-fill logic
8. `test_har_excessive_nan` - ValueError >5 consecutive → covers: excessive NaN check
9. `test_har_non_monotonic` - ValueError → covers: date validation
10. `test_har_forecast_before_fit` - RuntimeError → covers: _fitted check
11. `test_har_negative_rv` - ValueError → covers: RV validation

**HAR Forecast (4):**
12. `test_har_forecast_positive` - non-negative → covers: forecast clipping
13. `test_har_forecast_reasonable` - within range → covers: forecast calculation
14. `test_har_forecast_horizon` - h-day ahead → covers: horizon parameter
15. `test_har_forecast_insufficient_data` - ValueError <22 rows → covers: lag data check

**Total: 62 tests (47 microstructure + 15 volatility)**

### Coverage Target and Measurement

**Target:** >90% line coverage across libs/analytics/

**Measurement:**
```bash
pytest tests/libs/analytics/ --cov=libs/analytics --cov-report=term-missing --cov-fail-under=90
```

**Coverage Mapping Summary:**
| Component | Tests | Key Branches Covered |
|-----------|-------|---------------------|
| _get_version_id | 5 | current path, PIT path, missing manifest, PIT failure, version_manager None |
| _get_multi_version_id | 3 | single snapshot, dataset-not-in-snapshot error, current data |
| compute_realized_volatility | 7 | precomputed, <10 obs, PIT, formula, missing manifest |
| compute_vpin | 17 | sigma=0/NaN, warmup, is_partial, no-valid-buckets, trade-split (1 & multi), empty day |
| analyze_intraday_pattern | 7 | u-shape, holidays, half-day, as_of precedence |
| compute_spread_depth_stats | 12 | quote-only (AND logic), locked, crossed, stale >50%, PIT missing, fallback |
| HARVolatilityModel | 15 | fit, forecast, edge cases, validation |
| Determinism | 4 | VPIN rerun, composite hash, HAR forecast, depth calc |

**Estimated Coverage:** ~95% (all major branches + determinism verified)

---

## Acceptance Criteria

- [ ] Realized volatility with configurable sampling frequency
- [ ] VPIN calculation with deterministic bucket construction and trade splitting (BVC)
- [ ] Intraday pattern analysis
- [ ] HAR volatility model with OLS fitting
- [ ] **Spread AND depth statistics** (both required, quote-only depth)
- [ ] Outputs tagged with dataset_version_id (single or composite)
- [ ] Multi-dataset methods enforce single snapshot for PIT
- [ ] Composite version_id is deterministic (SHA256-based)
- [ ] >90% test coverage (62 tests, measured with pytest-cov)

---

## Implementation Order

1. Create `libs/analytics/__init__.py`
2. Implement `libs/analytics/microstructure.py` (MicrostructureAnalyzer)
   - Result dataclasses first
   - CompositeVersionInfo with deterministic composite_version_id
   - _get_version_id and _get_multi_version_id helpers (single snapshot)
   - compute_realized_volatility
   - compute_vpin (deterministic trade splitting, warmup handling)
   - analyze_intraday_pattern
   - compute_spread_depth_stats (quote-only depth filter)
3. Implement `libs/analytics/volatility.py` (HARVolatilityModel)
4. Create tests (62 tests)
5. Create documentation

---

## Risk Mitigation

1. **TAQ data**: Mocked in tests
2. **Numerical stability**: Edge case handling, NaN propagation
3. **Performance**: Polars vectorization for bucket construction
4. **Type safety**: mypy strict
5. **Reproducibility**: All outputs have deterministic dataset_version_id(s)
6. **Multi-dataset consistency**: Single snapshot enforcement in _get_multi_version_id
7. **Algorithm determinism**: VPIN trade splitting fully specified with split_ratio
8. **Coverage verification**: pytest-cov with --cov-fail-under=90
