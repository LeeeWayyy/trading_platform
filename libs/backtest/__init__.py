# Models and result_storage have lighter dependencies - always import
from .models import BacktestJob, JobNotFound, ResultPathMissing, row_to_backtest_job
from .monte_carlo import (
    ConfidenceInterval,
    MonteCarloConfig,
    MonteCarloResult,
    MonteCarloSimulator,
)

# Walk-forward optimization and parameter search utilities
from .param_search import SearchResult, grid_search, random_search
from .result_storage import BacktestResultStorage
from .walk_forward import (
    WalkForwardConfig,
    WalkForwardOptimizer,
    WalkForwardResult,
    WindowResult,
)

# job_queue and worker require rq - import conditionally to allow tests to run
# in environments without rq installed
try:
    from .job_queue import BacktestJobConfig, BacktestJobQueue, JobPriority
    from .worker import BacktestWorker, record_retry, run_backtest

    _HAS_RQ = True
except ImportError:
    # rq not installed - provide None placeholders for type checkers
    BacktestJobConfig = None  # type: ignore[assignment,misc]
    BacktestJobQueue = None  # type: ignore[assignment,misc]
    JobPriority = None  # type: ignore[assignment,misc]
    BacktestWorker = None  # type: ignore[assignment,misc]
    record_retry = None  # type: ignore[assignment]
    run_backtest = None  # type: ignore[assignment]
    _HAS_RQ = False

__all__ = [
    # Classes
    "BacktestJob",
    "BacktestJobConfig",
    "BacktestJobQueue",
    "BacktestResultStorage",
    "BacktestWorker",
    # Monte Carlo
    "ConfidenceInterval",
    "MonteCarloConfig",
    "MonteCarloResult",
    "MonteCarloSimulator",
    # Walk-forward optimization
    "WalkForwardConfig",
    "WalkForwardOptimizer",
    "WalkForwardResult",
    "WindowResult",
    # Parameter search
    "SearchResult",
    "grid_search",
    "random_search",
    # Exceptions
    "JobNotFound",
    "ResultPathMissing",
    # Enums / Literals
    "JobPriority",
    # Functions
    "record_retry",
    "row_to_backtest_job",
    "run_backtest",
]
