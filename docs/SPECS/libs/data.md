# libs/data

<!-- Last reviewed: 2026-02-01 - P6T10: PR review fixes - signal key deduplication before join -->

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

**Purpose:** Data quality framework for sync manifests, validation, schema drift detection, and dataset versioning.

**Key Features:**
- Sync manifest management
- Schema validation
- Data versioning
- Quality checks

### libs/data/market_data
See [libs/market_data.md](./market_data.md) for detailed specification.

**Purpose:** Market data fetching and caching for historical and real-time quotes.

**Key Features:**
- Historical data retrieval
- Real-time quote caching
- Symbol universe management
- Alpaca stream integration

## Dependencies
- **Internal:** libs/core/common, libs/core/redis_client
- **External:** Alpaca API, WRDS, yfinance, DuckDB

## Related Specs
- Individual library specs listed above
- [../services/market_data_service.md](../services/market_data_service.md) - Market data service

## Metadata
- **Last Updated:** 2026-02-01 (P6T10 - Added universe.py with ForwardReturnsProvider)
- **Source Files:** `libs/data/` (group index), `libs/data/data_providers/universe.py`
- **ADRs:** N/A
