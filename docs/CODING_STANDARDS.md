# Coding Standards
- Python 3.11, type hints required, mypy passing.
- Logging: structured, include `strategy_id`, `client_order_id` when relevant.
- Errors: never swallow; raise domain errors with context. Use `tenacity` for retries.
- Config: pydantic settings, no `os.getenv` scattered.
- Time: always timezone-aware UTC; use market calendar utilities.
- DB: use parameterized queries/ORM, migrations for schema changes.
- Concurrency: prefer async FastAPI + httpx; guard shared state with DB not memory.
