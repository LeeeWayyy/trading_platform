# libs/data

<!-- Last reviewed: 2026-02-28 - P6T15: Aligned built-in universe scope (SP500, R1000) -->

## Identity
- **Type:** Library Group (Data Pipeline and Providers)
- **Location:** `libs/data/`

## Overview
Data pipeline and provider libraries for market data ingestion, quality validation, and access:

- **data_pipeline/** - Data ETL, corporate actions, quality gates
- **data_providers/** - WRDS/CRSP/Compustat/Fama-French/yfinance providers
- **data_quality/** - Data quality framework for validation and versioning
- **market_data/** - Market data fetching and caching

## Libraries

### libs/data/data_pipeline
See [libs/data_pipeline.md](./data_pipeline.md) for detailed specification.

**Purpose:** Data ETL, corporate actions adjustments, quality gates, and freshness checks.

**Key Features:**
- Alpaca data fetching
- Split/dividend adjustments
- Data freshness monitoring
- Quality validation gates
- Health monitoring with per-source caching and staleness thresholds (P6T12)
- ETL pipeline heartbeat recording to Redis (P6T12)

### libs/data/data_providers
See [libs/data_providers.md](./data_providers.md) for detailed specification.

**Purpose:** WRDS/CRSP/Compustat/Fama-French/yfinance providers with unified fetcher and sync tooling.

**Key Features:**
- Unified data provider interface
- WRDS client for academic datasets
- TAQ query provider
- yfinance provider for market data

### libs/data/data_quality
See [libs/data_quality.md](./data_quality.md) for detailed specification.

**Purpose:** Data quality framework for sync manifests, validation, schema drift detection, dataset versioning, coverage analysis, and point-in-time inspection.

**Key Features:**
- Sync manifest management
- Schema validation
- Data versioning
- Quality checks
- Coverage analysis with symbol x date matrix (P6T13)
- Point-in-time data inspector for look-ahead bias detection (P6T13)
- Quality scoring (freshness, completeness, consistency, accuracy) (P6T13)

### libs/data/market_data
See [libs/market_data.md](./market_data.md) for detailed specification.

**Purpose:** Market data fetching and caching for historical and real-time quotes.

**Key Features:**
- Historical data retrieval
- Real-time quote caching
- Symbol universe management
- Alpaca stream integration

### libs/data/universe_manager

**Purpose:** Synchronous universe management: list, create, delete, enrich, and filter CRSP-based universes. Manages both built-in (SP500, R1000) and custom JSON-defined universes.

**Key Features:**
- Built-in and custom universe listing with metadata
- Constituent enrichment with market cap and ADV from CRSP
- Filter application (market_cap, adv_20d with gt/lt/gte/lte operators)
- Custom universe CRUD with JSON persistence
- Ticker resolution and unresolved ticker detection
- Generation-gated caching with nanosecond mtime precision
- Ticker format validation and canonicalization
- Corruption detection with graceful error propagation

### libs/data/schemas

**Purpose:** Neutral domain models shared across layers (data and service).

**Key Features:**
- `UniverseMetadata` Pydantic model for universe definitions
- Used by both UniverseManager and UniverseService

### libs/data/feature_metadata

**Purpose:** Feature catalog metadata and statistics computation for the Alpha158 feature set.

**Key Features:**
- Feature catalog with name, category, description, formula, lookback window, and input columns
- Sample value extraction from feature DataFrames
- Descriptive statistics computation (mean, std, quantiles, null percentage)
- Used by Feature Store Browser page (P6T14)

## Dependencies
- **Internal:** libs/core/common, libs/core/redis_client
- **External:** Alpaca API, WRDS, yfinance, DuckDB

## Related Specs
- Individual library specs listed above
- [../services/market_data_service.md](../services/market_data_service.md) - Market data service

## Metadata
- **Last Updated:** 2026-02-28 (P6T15 - Aligned built-in universe scope)
- **Source Files:** `libs/data/` (group index), `libs/data/data_providers/universe.py`, `libs/data/data_pipeline/health_monitor.py`, `libs/data/data_quality/coverage_analyzer.py`, `libs/data/data_quality/pit_inspector.py`, `libs/data/data_quality/quality_scorer.py`, `libs/data/feature_metadata.py`, `libs/data/schemas/__init__.py`, `libs/data/schemas/universe.py`, `libs/data/universe_manager.py`
- **ADRs:** N/A
