# Trading Platform

A production-grade algorithmic trading platform integrating Qlib for signal generation and Alpaca for order execution.

## Quick Start

### Prerequisites
- Python 3.11+
- Docker & Docker Compose
- Poetry (optional) or pip + venv

### Installation

**See [docs/SETUP.md](./docs/SETUP.md) for detailed environment setup instructions.**

#### Quick Setup (venv)

```bash
# Create virtual environment with Python 3.11
python3.11 -m venv .venv

# Activate venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run tests
pytest tests/ -v
```

#### Using Poetry

```bash
# Install dependencies
poetry install

# Run tests
poetry run pytest tests/ -v
```

#### Using Make

```bash
# Install dependencies
make install

# Start infrastructure
make up

# Run tests
make test
```

### Development Commands

```bash
make help           # Show all available commands
make fmt            # Format code
make lint           # Run linters
make test           # Run tests
make test-cov       # Run tests with coverage
make clean          # Clean generated files
```

## Project Structure

See [CLAUDE.md](./CLAUDE.md) for comprehensive documentation.

## Documentation

- **[docs/SETUP.md](./docs/SETUP.md)** - Environment setup and installation guide
- [CLAUDE.md](./CLAUDE.md) - Main guide for Claude Code
- [docs/ADR_GUIDE.md](./docs/ADR_GUIDE.md) - Architecture Decision Records guide
- [docs/DOCUMENTATION_STANDARDS.md](./docs/DOCUMENTATION_STANDARDS.md) - Code documentation standards
- [docs/GIT_WORKFLOW.md](./docs/GIT_WORKFLOW.md) - Git workflow and PR automation
- [docs/IMPLEMENTATION_GUIDES/t1-data-etl.md](./docs/IMPLEMENTATION_GUIDES/t1-data-etl.md) - T1 implementation guide

## Current Status

**Phase:** P0 (MVP Core, Days 0-45)

**Implemented:**
- ✅ Project infrastructure and tooling

**In Progress:**
- 🔄 T1: Data ETL with Corporate Actions, Freshness, Quality Gate

**Upcoming:**
- ⏳ T2: Baseline Strategy + MLflow
- ⏳ T3: Signal Service
- ⏳ T4: Execution Gateway
- ⏳ T5: Position Tracker
- ⏳ T6: Paper Run Orchestrator

## License

Private project.
