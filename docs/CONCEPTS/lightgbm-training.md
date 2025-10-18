# LightGBM Training for Stock Return Prediction

## Plain English Explanation

LightGBM (Light Gradient Boosting Machine) is a machine learning algorithm that learns patterns from historical data to predict future stock returns. Think of it like training a very smart assistant to recognize which market conditions tend to lead to positive or negative returns.

**Simple analogy:** Imagine you're teaching someone to predict tomorrow's weather. You show them thousands of days of historical data (temperature, humidity, wind, etc.) and what the weather was the next day. After seeing enough examples, they learn patterns like "when humidity is high and temperature drops, it often rains tomorrow." LightGBM does the same thing for stock returns, but with 158 technical indicators instead of weather features.

## Why LightGBM?

We chose LightGBM for the baseline strategy because it:

1. **Fast training** - Trains 10-100x faster than traditional gradient boosting
2. **Handles tabular data well** - Perfect for Alpha158's 158 features
3. **Built-in regularization** - Prevents overfitting automatically
4. **Feature importance** - Tells us which indicators matter most
5. **Battle-tested** - Used by winners of Kaggle competitions and in production at major quant firms
6. **Works with Qlib** - Microsoft designed Qlib with LightGBM in mind

## How LightGBM Works

### The Big Picture

LightGBM builds an ensemble of decision trees. Each tree corrects the mistakes of the previous trees.

```
Iteration 1: Build Tree 1 → Predict returns → Calculate errors
Iteration 2: Build Tree 2 to fix Tree 1's errors → Combine predictions
Iteration 3: Build Tree 3 to fix remaining errors → Combine predictions
...
Iteration 100: Final model = Tree 1 + Tree 2 + ... + Tree 100
```

### Decision Trees for Returns

A single decision tree might look like this:

```
                    RSI < 50?
                   /          \
                 YES           NO
                 /              \
         MACD < 0?          Volume high?
         /      \             /        \
       YES      NO          YES        NO
       /         \          /           \
   Predict    Predict   Predict      Predict
   -0.5%      +0.2%     +0.8%        +0.3%
```

**Example prediction:**
- If RSI = 45 (< 50) and MACD = -0.5 (< 0): Predict -0.5% return
- If RSI = 65 (≥ 50) and Volume = high: Predict +0.8% return

### Gradient Boosting

Instead of building one big tree, we build many small trees sequentially:

1. **Tree 1** predicts returns (might be inaccurate)
2. **Tree 2** predicts the errors Tree 1 made
3. **Tree 3** predicts the errors Tree 1 + Tree 2 made
4. Repeat 100 times

**Final prediction = Tree 1 + Tree 2 + Tree 3 + ... + Tree 100**

This "boosting" process gradually reduces errors.

### Why "Light"?

Traditional gradient boosting is slow because it checks every possible split for every feature. LightGBM is "light" (fast) because it:

1. **Leaf-wise growth** - Grows trees by expanding the leaf that reduces error most (not level-by-level)
2. **Histogram binning** - Groups feature values into bins (faster than checking every value)
3. **Gradient-based sampling** - Focuses on hard examples (samples with large gradients)

**Speed comparison:**
- XGBoost (traditional): 10 minutes to train
- LightGBM: 1 minute to train (same accuracy!)

## Our Configuration

### Hyperparameters Explained

Here's our baseline configuration with plain English explanations:

```python
{
    "objective": "regression",           # Predict continuous returns (not classification)
    "metric": "mae",                    # Mean Absolute Error (robust to outliers)
    "boosting_type": "gbdt",            # Gradient Boosting Decision Tree

    # Boosting parameters
    "num_boost_round": 100,             # Build 100 trees
    "learning_rate": 0.05,              # Small steps = more stable learning
    "early_stopping_rounds": 20,        # Stop if no improvement for 20 rounds

    # Tree structure
    "max_depth": 6,                     # Limit tree depth (prevents overfitting)
    "num_leaves": 31,                   # Max leaves per tree (2^6 - 1 = 31)

    # Feature/sample sampling
    "feature_fraction": 0.8,            # Use 80% of features per tree
    "bagging_fraction": 0.8,            # Use 80% of samples per tree
    "bagging_freq": 5,                  # Resample every 5 iterations

    # Regularization
    "min_data_in_leaf": 20,             # At least 20 samples per leaf
    "lambda_l1": 0.1,                   # L1 regularization (feature selection)
    "lambda_l2": 0.1,                   # L2 regularization (weight smoothing)

    # Other
    "seed": 42,                         # Random seed (reproducibility)
    "num_threads": 4                    # Parallel training
}
```

### Why These Values?

- **num_boost_round = 100**: More trees = better fit, but too many = overfitting. 100 is a good starting point.
- **learning_rate = 0.05**: Small learning rate (0.01-0.1) makes training more stable. We use 0.05 as a conservative default.
- **max_depth = 6**: Depth 6 trees can capture complex patterns without overfitting. Depth 8+ often overfits on financial data.
- **feature_fraction = 0.8**: Randomly using 80% of features per tree prevents over-reliance on any single feature.
- **bagging_fraction = 0.8**: Randomly using 80% of samples per tree adds robustness (like random forests).
- **lambda_l1/l2 = 0.1**: Light regularization prevents overfitting. Financial data is noisy, so regularization helps.

## Training Process

### Step-by-Step Pipeline

Our training pipeline (`strategies/alpha_baseline/train.py`):

```
1. Load Data
   ├─ Load OHLCV from T1 Parquet files
   ├─ Compute Alpha158 features (158 features)
   └─ Split: train (2020-2023), valid (2024 H1), test (2024 H2)

2. Prepare Data
   ├─ Normalize features (robust z-score)
   ├─ Create LightGBM Datasets
   └─ Set up validation for early stopping

3. Train Model
   ├─ Iterate: Build tree 1, 2, 3, ..., 100
   ├─ Monitor: Check validation MAE after each tree
   └─ Early Stop: If no improvement for 20 rounds, stop

4. Evaluate
   ├─ Compute: MAE, RMSE, R², IC
   ├─ Compare: train vs valid metrics
   └─ Check: Is model overfitting?

5. Save
   ├─ Save best model (lowest validation MAE)
   ├─ Log to MLflow (params, metrics, model)
   └─ Return trained model
```

### Example Training Output

```
Loading data and computing features...
Symbols: ['AAPL', 'MSFT', 'GOOGL']
Train: 2020-01-01 to 2023-12-31
Valid: 2024-01-01 to 2024-06-30
Test: 2024-07-01 to 2024-12-31

Data loaded successfully:
  Train: 3024 samples, 158 features
  Valid: 378 samples, 158 features
  Test:  378 samples, 158 features

Preparing LightGBM datasets...

Training LightGBM model...
Parameters: {'objective': 'regression', 'metric': 'mae', ...}

Training complete!
Best iteration: 78

==================================================
Evaluation Metrics
==================================================
Metric              Train           Valid
--------------------------------------------------
MAE              0.012345        0.014567
RMSE             0.023456        0.026789
R²               0.234567        0.189012
IC               0.156789        0.052345
==================================================

Model saved to: artifacts/models/alpha_baseline.txt
```

## Evaluation Metrics

### 1. MAE (Mean Absolute Error)

**What it is:** Average absolute difference between predicted and actual returns.

**Formula:** `MAE = mean(|predicted - actual|)`

**Example:**
```
Day 1: Predicted +2%, Actual +3% → Error = 1%
Day 2: Predicted -1%, Actual +1% → Error = 2%
Day 3: Predicted +4%, Actual +2% → Error = 2%
MAE = (1% + 2% + 2%) / 3 = 1.67%
```

**Why it matters:** Lower is better. MAE = 0.015 means predictions are off by 1.5% on average.

### 2. RMSE (Root Mean Squared Error)

**What it is:** Square root of average squared difference.

**Formula:** `RMSE = sqrt(mean((predicted - actual)^2))`

**Why it matters:** Penalizes large errors more than MAE. If RMSE >> MAE, model makes some very bad predictions.

### 3. R² (Coefficient of Determination)

**What it is:** Fraction of variance explained by the model.

**Range:** -∞ to 1 (higher is better)
- R² = 1: Perfect predictions
- R² = 0: No better than predicting the mean
- R² < 0: Worse than predicting the mean

**Example:**
```
Actual returns: [+2%, -1%, +3%, +1%]
Mean: 1.25%

Model A predictions: [+2.1%, -0.9%, +2.9%, +1.1%] → R² = 0.95 (great!)
Model B predictions: [+1.2%, +1.3%, +1.2%, +1.3%] → R² = 0.10 (barely better than mean)
```

**Why it matters:** R² = 0.20 is typical for daily stock returns (markets are noisy!).

### 4. IC (Information Coefficient)

**What it is:** Correlation between predicted and actual returns.

**Range:** -1 to 1 (higher is better)
- IC > 0: Predictions point in right direction
- IC = 0: No predictive power
- IC < 0: Predictions point in wrong direction

**Example:**
```
Day 1: Predicted +2%, Actual +3% → Both positive ✓
Day 2: Predicted +1%, Actual -1% → Opposite signs ✗
Day 3: Predicted -2%, Actual -3% → Both negative ✓

IC ≈ 0.05 (weak positive correlation)
```

**Why it matters:** IC is the gold standard for quant trading:
- IC > 0.05: Usable signal
- IC > 0.10: Strong signal
- IC > 0.20: Exceptional signal (rare!)

**Our target:** IC ≥ 0.05 on validation set.

## Common Pitfalls

### 1. Overfitting

**Problem:** Model memorizes training data instead of learning patterns.

**Symptoms:**
```
Train MAE: 0.005 (very low!)
Valid MAE: 0.025 (much higher)
→ Overfitting!
```

**Solutions:**
- Reduce max_depth (try 4 or 5)
- Increase lambda_l1/l2 (try 0.5 or 1.0)
- Reduce num_boost_round (stop earlier)
- Use more regularization (lower feature_fraction/bagging_fraction)

### 2. Underfitting

**Problem:** Model is too simple to capture patterns.

**Symptoms:**
```
Train MAE: 0.020
Valid MAE: 0.022
Both high, similar values → Underfitting!
```

**Solutions:**
- Increase max_depth (try 8)
- Increase num_boost_round (try 200)
- Decrease lambda_l1/l2 (less regularization)
- Check if features are informative

### 3. Look-Ahead Bias

**Problem:** Using future information in features.

**Example (WRONG):**
```python
# This uses tomorrow's close in today's feature!
features['tomorrow_return'] = (df['close'].shift(-1) - df['close']) / df['close']
```

**Solution:**
```python
# Only use past/current information
features['yesterday_return'] = (df['close'] - df['close'].shift(1)) / df['close'].shift(1)
```

**In our pipeline:** Alpha158 features are guaranteed to be look-ahead free.

### 4. Data Snooping

**Problem:** Using test set information during training.

**Example (WRONG):**
```python
# Compute normalization stats from ALL data
mean = all_data.mean()  # Includes test set!
normalized = (test_data - mean) / std
```

**Solution:**
```python
# Only use training set for normalization stats
mean = train_data.mean()  # Training only
normalized = (test_data - mean) / std  # Apply to test
```

**In our pipeline:** `fit_start_date` and `fit_end_date` ensure we only use training period for normalization.

### 5. Ignoring Market Regimes

**Problem:** Markets change over time (2020 bull ≠ 2022 bear).

**Symptoms:**
```
Train (2020-2021): IC = 0.15
Valid (2024): IC = -0.02
→ Model learned bull market patterns that don't work in different regime!
```

**Solutions:**
- Retrain periodically (monthly or quarterly)
- Use cross-sectional features (relative to market)
- Use shorter training windows (1-2 years instead of 4)
- Add regime-detection features

## Feature Importance

After training, we can see which features matter most:

```python
import pandas as pd

# Get feature importance
importance = pd.DataFrame({
    'feature': feature_names,
    'importance': model.feature_importance()
}).sort_values('importance', ascending=False)

print(importance.head(10))
```

**Example output:**
```
              feature  importance
0    ROC_close_20d        1543
1    RSI_12d              1289
2    MACD_histogram       1156
3    BOLL_position_20d    1034
4    KDJ_K_12d             987
5    Volume_MA_ratio       856
6    Daily_return          745
...
```

**Insights:**
- Multi-timeframe momentum (ROC) is most important
- Medium-term indicators (12-20 days) matter more than short-term
- Single-day patterns (KBAR) are least important

## Next Steps

After training the baseline model:

1. **Backtest** - Test on unseen data (2024 H2)
2. **Analyze errors** - When does model fail?
3. **Feature engineering** - Can we add better features?
4. **Hyperparameter tuning** - Optimize max_depth, learning_rate, etc.
5. **Ensemble** - Combine multiple models
6. **Deploy** - Integrate with Signal Service for live trading

## Further Reading

- [LightGBM Documentation](https://lightgbm.readthedocs.io/)
- [LightGBM Paper (KDD 2017)](https://papers.nips.cc/paper/6907-lightgbm-a-highly-efficient-gradient-boosting-decision-tree.pdf)
- [Gradient Boosting Explained](https://explained.ai/gradient-boosting/)
- [IC (Information Coefficient) in Quant Trading](https://www.investopedia.com/terms/i/informationratio.asp)
- See `/docs/IMPLEMENTATION_GUIDES/p0t2-baseline-strategy.md` for full pipeline
- See `/docs/CONCEPTS/alpha158-features.md` for feature details
