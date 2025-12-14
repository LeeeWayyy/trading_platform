# Models and result_storage have lighter dependencies - always import
from .models import BacktestJob, JobNotFound, ResultPathMissing, row_to_backtest_job
from .result_storage import BacktestResultStorage

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
