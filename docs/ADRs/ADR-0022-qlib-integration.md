# ADR 0022: Hybrid Qlib Integration Architecture

**Status:** Accepted
**Date:** 2025-12-08
**Context:** P4 Phase 2 (Analytics Infrastructure)

## Context
We are building a production-grade trading platform that requires:
1.  **Strict Point-in-Time (PIT) Correctness:** No look-ahead bias, full reproducibility of past states.
2.  **High-Performance Research:** Fast backtesting, standard alpha metrics (IC, ICIR), and factor evaluation.
3.  **Modern Stack:** Polars/DuckDB for data processing.

[Qlib](https://github.com/microsoft/qlib) is a popular open-source AI-oriented quantitative investment platform. We analyzed whether to adopt it fully, partially, or not at all (see `docs/CONCEPTS/qlib-comparison.md`).

## Decision
We will adopt a **Hybrid Approach**, leveraging Qlib for specific research metrics while maintaining our own data infrastructure for production safety.

### 1. Data Infrastructure: KEEP OURS
*   **Decision:** We will use our custom `DatasetVersionManager` (Parquet/DuckDB) instead of Qlib's binary format/data loader.
*   **Rationale:**
    *   **PIT Correctness:** Our snapshot-based versioning provides stronger guarantees for reproducing exact historical states than Qlib's loader.
    *   **Tech Stack:** Polars/DuckDB integrates better with our existing ETL pipeline than Qlib's custom binary format.
    *   **Atomic Writes:** We require crash-safe data ingestion, which our `SyncManager` provides.

### 2. Alpha Research: ADOPT Qlib Metrics
*   **Decision:** We will wrap `qlib.contrib.evaluate` in an `AlphaMetricsAdapter` (T2.5).
*   **Rationale:** Qlib provides battle-tested implementations of complex metrics (Rank IC, Grouped IC by sector, turnover analysis). Re-implementing these correctly is non-trivial and prone to subtle errors.
*   **Implementation:** The adapter will convert our Polars DataFrames to the format Qlib expects, run the analysis, and return results. Qlib will be an **optional** dependency (`research` group).

### 3. Factor Definitions: HYBRID (Staged)
*   **Phase 2:** Use **Static Python Classes** (e.g., `class Momentum(FactorDefinition)`).
    *   **Rationale:** Type safety, easier debugging, and explicit control over PIT lookback windows.
*   **Phase 3:** Introduce `FormulaicFactor` (Qlib expression adapter) as an enhancement.
    *   **Rationale:** Enables rapid prototyping for researchers using Qlib's DSL strings (e.g., `Ref($close, 5) / $close - 1`).

### 4. Caching: ADOPT Pattern
*   **Decision:** Implement `DiskExpressionCache` (T2.8) inspired by Qlib.
*   **Rationale:** Caching computed factors to disk is essential for iterative research speed. We will adapt Qlib's pattern to use our `DatasetVersionManager` IDs as cache keys to ensure cache validity.

## Consequences
*   **Positive:** Best of both worlds—production safety of our data layer + rich metrics from Qlib.
*   **Positive:** No heavy dependency on Qlib for the core execution engine (it remains optional).
*   **Negative:** Maintenance overhead of the `AlphaMetricsAdapter` layer (converting data formats).
*   **Negative:** Two ways to define factors (Static vs Formulaic) in Phase 3, requiring clear guidelines.
