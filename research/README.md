# Research Directory

**Purpose:** Experimental code, exploratory data analysis, and strategy prototyping

**Last Updated:** 2026-01-14

---

## Overview

The `research/` directory is explicitly separated from production code to enable rapid experimentation without compromising production quality standards. Code here follows **lenient standards** compared to the strict requirements for `apps/`, `libs/`, and production `strategies/`.

**Key Principles:**
- **Experimentation encouraged:** Rapid iteration, trial and error, incomplete implementations
- **No deployment:** Research code never deploys to production
- **Lenient CI:** Excluded from strict mypy/ruff/test coverage requirements
- **Documentation optional:** Comments and docstrings encouraged but not required
- **Migration path:** Promising research graduates to production with full standards

---

## Directory Structure

```
research/
├── README.md               # This file
├── notebooks/              # Jupyter notebooks for EDA and analysis
├── experiments/            # Ad-hoc experiments and one-off analyses
├── strategies/             # Experimental strategy implementations
└── data_exploration/       # Data quality analysis, feature engineering
```

---

## Quality Standards

### Research Code (Lenient)
- ✅ Runs without errors (basic functionality)
- ✅ Basic type hints (encouraged but not required)
- ✅ Minimal comments explaining "why" (for future reference)
- ❌ No strict mypy compliance required
- ❌ No mandatory test coverage
- ❌ No ruff linting enforcement

### Production Code (Strict)
When research code graduates to production (`apps/`, `libs/`, or `strategies/`):
- ✅ Full type hints (mypy --strict compliance)
- ✅ Comprehensive tests (unit + integration)
- ✅ Complete docstrings and documentation
- ✅ Ruff linting compliance
- ✅ Code review and approval required

---

## Experimental Strategies

**Location:** `research/strategies/`

**Current experimental strategies:**
- `momentum/` - Momentum-based trading strategy
- `mean_reversion/` - Mean reversion strategy

**Graduation criteria:**
When an experimental strategy is ready for production:
1. Achieve positive Sharpe ratio >1.5 in backtests
2. Pass risk management checks (max drawdown, position limits)
3. Complete full test coverage (>80%)
4. Add comprehensive documentation
5. Code review and approval
6. Move to `strategies/` (production)

---

## Notebooks

**Location:** `research/notebooks/`

**Guidelines:**
- Use Jupyter notebooks for exploratory data analysis
- Name notebooks with date prefix: `YYYY-MM-DD_description.ipynb`
- Add markdown cells explaining analysis goals and findings
- Export key visualizations to `artifacts/visualizations/`

**Example notebooks:**
- `2026-01-14_alpha_factor_analysis.ipynb` - Factor correlation analysis
- `2026-01-15_backtest_parameter_sweep.ipynb` - Strategy parameter optimization

---

## Experiments

**Location:** `research/experiments/`

**Purpose:** One-off scripts, prototype code, proof-of-concept implementations

**Guidelines:**
- Quick and dirty code is acceptable
- Focus on answering specific questions
- Document findings in notebook or markdown
- Clean up or delete after insights extracted

---

## Data Exploration

**Location:** `research/data_exploration/`

**Purpose:** Data quality analysis, schema exploration, feature engineering

**Guidelines:**
- Analyze data distributions, missing values, outliers
- Test feature engineering ideas
- Document data quality issues
- Graduate useful features to production feature store

---

## CI/CD Behavior

**Research code is excluded from strict CI checks:**
- ✅ Included: Basic syntax checks (Python can import)
- ❌ Excluded: mypy --strict type checking
- ❌ Excluded: ruff linting
- ❌ Excluded: test coverage requirements
- ❌ Excluded: documentation completeness

**Why?**
- Research code evolves rapidly; strict checks slow iteration
- Experimental code may be intentionally incomplete
- Focus is on learning, not production readiness

---

## Migration Path: Research → Production

When research code is ready for production deployment:

### Step 1: Validate Research Findings
- [ ] Backtest results reproducible
- [ ] Risk metrics acceptable (Sharpe >1.5, max drawdown <20%)
- [ ] Strategy logic sound and well-understood

### Step 2: Refactor to Production Standards
- [ ] Add comprehensive type hints (mypy --strict)
- [ ] Write unit tests (>80% coverage)
- [ ] Write integration tests
- [ ] Add docstrings and documentation
- [ ] Pass ruff linting
- [ ] Code review approval

### Step 3: Move to Production
- [ ] Move code from `research/strategies/` → `strategies/`
- [ ] Update imports across codebase
- [ ] Update CI to enforce strict checks
- [ ] Create ADR documenting strategy design
- [ ] Deploy to paper trading for validation

### Step 4: Monitor and Iterate
- [ ] Monitor live performance vs. backtest
- [ ] Adjust parameters if needed
- [ ] Document lessons learned

---

## Best Practices

### Do's
- ✅ Experiment freely and iterate rapidly
- ✅ Document key findings and insights
- ✅ Use version control (git) for all research code
- ✅ Clean up old experiments after insights extracted
- ✅ Graduate successful research to production

### Don'ts
- ❌ Don't deploy research code to production
- ❌ Don't duplicate production logic (import from libs/)
- ❌ Don't let research/ become a dumping ground
- ❌ Don't skip documentation for key findings
- ❌ Don't circumvent production standards by keeping code in research/

---

## Examples

### Good Research Code
```python
# research/experiments/test_new_alpha_factor.py
"""Quick experiment: Does momentum + volume predict returns?"""
import pandas as pd
from libs.data.data_pipeline import load_market_data

# Load data
df = load_market_data("2024-01-01", "2024-12-31")

# Test hypothesis
df['momentum_volume'] = df['returns_20d'] * df['volume_20d_avg']
correlation = df[['momentum_volume', 'forward_returns_5d']].corr()

print(f"Correlation: {correlation.iloc[0, 1]:.3f}")
# Result: 0.23 - weak signal, not worth pursuing
```

### Production-Ready Code
```python
# strategies/momentum_volume/strategy.py
"""Momentum-Volume strategy with comprehensive risk management.

This strategy combines momentum and volume signals to generate alpha.
See ADR-0042 for design rationale.
"""
from typing import Dict
import pandas as pd
from libs.trading.alpha import AlphaModel
from libs.trading.risk_management import PositionSizer

class MomentumVolumeStrategy(AlphaModel):
    """Momentum-volume trading strategy.

    Args:
        lookback_days: Momentum calculation window
        volume_threshold: Minimum volume percentile
    """
    def __init__(self, lookback_days: int = 20, volume_threshold: float = 0.5) -> None:
        self.lookback_days = lookback_days
        self.volume_threshold = volume_threshold

    def generate_signals(self, data: pd.DataFrame) -> Dict[str, float]:
        """Generate trading signals based on momentum and volume.

        Returns:
            Dictionary mapping symbol to target weight [-1, 1]
        """
        # Full implementation with error handling, logging, tests...
```

---

## Git Ignore

Research artifacts are excluded from version control:
- `*.ipynb_checkpoints/` - Jupyter notebook checkpoints
- `research/artifacts/` - Generated artifacts (plots, models)
- `research/tmp/` - Temporary files
- `research/data/` - Local data files (use DVC for large datasets)

---

## Questions?

See:
- [Setup Guide](../docs/GETTING_STARTED/SETUP.md)
- [Coding Standards](../docs/STANDARDS/CODING_STANDARDS.md)
- [Testing Standards](../docs/STANDARDS/TESTING.md)

---

**Last Updated:** 2026-01-14
**Maintained By:** Development Team
