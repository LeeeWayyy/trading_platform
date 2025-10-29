"""
APScheduler-based slice execution scheduler with safety guards.

Manages scheduled submission of TWAP child order slices with:
- Kill switch check before EVERY slice submission
- Circuit breaker check before EVERY slice submission
- Automatic retry on transient failures (3 attempts, exponential backoff)
- Job cancellation support

This scheduler ensures that TWAP child slices are executed at their scheduled
times while respecting critical trading safety guardrails. Slices blocked by
safety checks are silently marked as blocked in the database rather than raising
exceptions, allowing the scheduler to continue operating normally.

Example:
    >>> from datetime import datetime, UTC, timedelta
    >>> scheduler = SliceScheduler(
    ...     kill_switch=kill_switch,
    ...     breaker=breaker,
    ...     db_client=db,
    ...     executor=alpaca_executor,
    ... )
    >>> scheduler.start()
    >>>
    >>> # Schedule slices from a TWAP plan
    >>> job_ids = scheduler.schedule_slices(
    ...     parent_order_id="parent123",
    ...     slices=slicing_plan.slices,
    ...     symbol="AAPL",
    ...     side="buy",
    ...     order_type="market",
    ...     limit_price=None,
    ...     stop_price=None,
    ...     time_in_force="day",
    ... )
    >>>
    >>> # Later: cancel remaining slices
    >>> scheduler_count, db_count = scheduler.cancel_remaining_slices("parent123")

See Also:
    - docs/TASKS/P2T0_PLANNING.md#p2t0-twap-order-slicer
    - docs/CONCEPTS/execution-algorithms.md#twap
    - .claude/workflows/03-zen-review-quick.md
"""

import logging
import time
from decimal import Decimal
from typing import Literal

from apscheduler.jobstores.base import JobLookupError  # type: ignore[import-untyped]
from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore[import-untyped]
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from apps.execution_gateway.alpaca_client import (
    AlpacaConnectionError,
    AlpacaExecutor,
    AlpacaRejectionError,
    AlpacaValidationError,
)
from apps.execution_gateway.database import DatabaseClient
from apps.execution_gateway.schemas import OrderRequest, SliceDetail
from libs.risk_management.breaker import CircuitBreaker
from libs.risk_management.kill_switch import KillSwitch

logger = logging.getLogger(__name__)


class SliceScheduler:
    """
    APScheduler-based slice execution scheduler with safety guards.

    Manages scheduled submission of TWAP child order slices with mandatory
    kill switch and circuit breaker checks before every slice, automatic retry
    on transient failures, and job cancellation support.

    Attributes:
        kill_switch: Kill switch instance for critical safety checks
        breaker: Circuit breaker instance for risk management
        db: Database client for order status updates
        executor: Alpaca executor for order submission (None in DRY_RUN mode)
        scheduler: APScheduler BackgroundScheduler instance (UTC timezone)

    Example:
        >>> scheduler = SliceScheduler(
        ...     kill_switch=kill_switch,
        ...     breaker=breaker,
        ...     db_client=db,
        ...     executor=alpaca_executor,
        ... )
        >>> scheduler.start()
        >>> job_ids = scheduler.schedule_slices(
        ...     parent_order_id="parent123",
        ...     slices=[...],
        ...     symbol="AAPL",
        ...     side="buy",
        ...     order_type="market",
        ...     limit_price=None,
        ...     stop_price=None,
        ...     time_in_force="day",
        ... )

    Notes:
        - Scheduler runs in background thread (non-blocking)
        - Kill switch/breaker checks execute in job thread (not at schedule time)
        - Blocked slices update DB status but don't raise exceptions
        - Retry logic only applies to connection errors (transient failures)
        - Validation/rejection errors are non-retryable and update DB immediately
    """

    def __init__(
        self,
        kill_switch: KillSwitch,
        breaker: CircuitBreaker,
        db_client: DatabaseClient,
        executor: AlpacaExecutor | None,
    ):
        """
        Initialize slice scheduler.

        Args:
            kill_switch: Kill switch instance for critical safety checks
            breaker: Circuit breaker instance for risk management
            db_client: Database client for order status updates
            executor: Alpaca executor for order submission (None in DRY_RUN mode)

        Example:
            >>> scheduler = SliceScheduler(
            ...     kill_switch=kill_switch,
            ...     breaker=breaker,
            ...     db_client=db,
            ...     executor=alpaca_executor,  # or None for DRY_RUN
            ... )

        Notes:
            - When executor is None (DRY_RUN mode), slices are logged but not submitted to broker
            - DRY_RUN slices update DB status to 'dry_run' instead of 'submitted'
        """
        self.kill_switch = kill_switch
        self.breaker = breaker
        self.db = db_client
        self.executor = executor
        self.scheduler = BackgroundScheduler(timezone="UTC")

    def start(self) -> None:
        """
        Start the scheduler.

        Starts the APScheduler background thread to begin processing scheduled jobs.
        This method is idempotent - calling it multiple times has no additional effect.

        Example:
            >>> scheduler = SliceScheduler(...)
            >>> scheduler.start()
        """
        self.scheduler.start()
        logger.info("SliceScheduler started")

    def shutdown(self, wait: bool = True) -> None:
        """
        Shutdown the scheduler.

        Stops the APScheduler background thread and optionally waits for running
        jobs to complete before returning.

        Args:
            wait: If True, wait for running jobs to complete (default: True)

        Example:
            >>> scheduler = SliceScheduler(...)
            >>> scheduler.start()
            >>> # ... later ...
            >>> scheduler.shutdown(wait=True)
        """
        self.scheduler.shutdown(wait=wait)
        logger.info("SliceScheduler shutdown complete")

    def schedule_slices(
        self,
        parent_order_id: str,
        slices: list[SliceDetail],
        symbol: str,
        side: Literal["buy", "sell"],
        order_type: Literal["market", "limit", "stop", "stop_limit"],
        limit_price: Decimal | None,
        stop_price: Decimal | None,
        time_in_force: Literal["day", "gtc", "ioc", "fok"],
    ) -> list[str]:
        """
        Schedule all slices for execution at their scheduled times.

        Creates APScheduler jobs for each slice using 'date' trigger (one-time execution).
        Job IDs are deterministic: "{parent_order_id}_slice_{slice_num}".

        Args:
            parent_order_id: Parent order's client_order_id
            slices: List of SliceDetail with scheduled times
            symbol: Stock symbol (e.g., "AAPL")
            side: Order side ("buy" or "sell")
            order_type: Order type ("market", "limit", "stop", "stop_limit")
            limit_price: Limit price for limit/stop_limit orders
            stop_price: Stop price for stop/stop_limit orders
            time_in_force: Time in force ("day", "gtc", "ioc", "fok")

        Returns:
            List of job IDs created (one per slice)

        Example:
            >>> job_ids = scheduler.schedule_slices(
            ...     parent_order_id="parent123",
            ...     slices=plan.slices,
            ...     symbol="AAPL",
            ...     side="buy",
            ...     order_type="market",
            ...     limit_price=None,
            ...     stop_price=None,
            ...     time_in_force="day",
            ... )
            >>> len(job_ids)
            5

        Notes:
            - Each slice gets a unique job ID: "{parent_order_id}_slice_{slice_num}"
            - Jobs are scheduled using 'date' trigger (one-time, not recurring)
            - Slices execute at slice_detail.scheduled_time (UTC)
            - Safety checks (kill switch, breaker) execute at job run time, not schedule time
        """
        job_ids = []
        for slice_detail in slices:
            job_id = f"{parent_order_id}_slice_{slice_detail.slice_num}"
            self.scheduler.add_job(
                func=self._execute_slice_job_wrapper,
                trigger="date",
                run_date=slice_detail.scheduled_time,
                id=job_id,
                kwargs={
                    "parent_order_id": parent_order_id,
                    "slice_detail": slice_detail,
                    "symbol": symbol,
                    "side": side,
                    "order_type": order_type,
                    "limit_price": limit_price,
                    "stop_price": stop_price,
                    "time_in_force": time_in_force,
                },
            )
            job_ids.append(job_id)

        logger.info(
            f"Scheduled {len(job_ids)} slices for parent: {parent_order_id}",
            extra={"parent_order_id": parent_order_id, "slice_count": len(job_ids)},
        )
        return job_ids

    def _execute_slice_job_wrapper(
        self,
        parent_order_id: str,
        slice_detail: SliceDetail,
        symbol: str,
        side: Literal["buy", "sell"],
        order_type: Literal["market", "limit", "stop", "stop_limit"],
        limit_price: Decimal | None,
        stop_price: Decimal | None,
        time_in_force: Literal["day", "gtc", "ioc", "fok"],
    ) -> None:
        """
        Job wrapper for APScheduler that handles final retry exhaustion.

        This wrapper calls the retry-decorated _execute_slice method and catches
        the final exception when all retries are exhausted, marking the order as failed.
        """
        try:
            self._execute_slice(
                parent_order_id=parent_order_id,
                slice_detail=slice_detail,
                symbol=symbol,
                side=side,
                order_type=order_type,
                limit_price=limit_price,
                stop_price=stop_price,
                time_in_force=time_in_force,
            )
        except AlpacaConnectionError as e:
            # All retries exhausted - mark as failed in DB
            self.db.update_order_status(
                client_order_id=slice_detail.client_order_id,
                status="failed",
                error_message=f"Retry exhausted: {e}",
            )
            logger.error(
                f"Slice failed after all retries exhausted: parent={parent_order_id}, "
                f"slice={slice_detail.slice_num}, error={e}",
                extra={
                    "parent_order_id": parent_order_id,
                    "slice_num": slice_detail.slice_num,
                    "client_order_id": slice_detail.client_order_id,
                    "error": str(e),
                    "retries_exhausted": True,
                },
            )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(AlpacaConnectionError),
        reraise=True,
    )
    def _execute_slice(
        self,
        parent_order_id: str,
        slice_detail: SliceDetail,
        symbol: str,
        side: Literal["buy", "sell"],
        order_type: Literal["market", "limit", "stop", "stop_limit"],
        limit_price: Decimal | None,
        stop_price: Decimal | None,
        time_in_force: Literal["day", "gtc", "ioc", "fok"],
    ) -> None:
        """
        Execute single slice with safety guards and retry logic.

        This method runs in the APScheduler background thread. It performs mandatory
        safety checks (kill switch, circuit breaker, DB cancellation status) before
        submitting the slice order. Slices blocked by safety checks update DB status
        but don't raise exceptions.

        Retry policy:
        - Max 3 attempts
        - Exponential backoff: 2s, 4s, 8s
        - Only retries AlpacaConnectionError (transient failures)
        - Validation/rejection errors are non-retryable

        Args:
            parent_order_id: Parent order's client_order_id
            slice_detail: Slice details with qty, scheduled_time, client_order_id
            symbol: Stock symbol (e.g., "AAPL")
            side: Order side ("buy" or "sell")
            order_type: Order type ("market", "limit", "stop", "stop_limit")
            limit_price: Limit price for limit/stop_limit orders
            stop_price: Stop price for stop/stop_limit orders
            time_in_force: Time in force ("day", "gtc", "ioc", "fok")

        Raises:
            AlpacaConnectionError: If all retries exhausted (propagated to wrapper)

        Notes:
            - 🔒 MANDATORY: DB cancellation check (prevents race condition)
            - 🔒 MANDATORY: Kill switch check before submission
            - 🔒 MANDATORY: Circuit breaker check before submission
            - Blocked slices update DB status to 'blocked_kill_switch' or 'blocked_circuit_breaker'
            - Rejected slices update DB status to 'rejected'
            - Connection errors are logged and re-raised for tenacity retry
            - Final failure marking (when all retries exhausted) is handled by _execute_slice_job_wrapper
            - Successful slices update DB status to 'submitted' and clear any prior error_message
        """
        # 🔒 CRITICAL: Wrap pre-submission checks in exception handling to prevent silent job drops
        # If DB/Redis fails during these checks, we need to mark the slice as failed rather than
        # letting APScheduler silently drop the job (the @retry decorator only catches AlpacaConnectionError)
        try:
            # 🔒 MANDATORY: Check if slice was already canceled in DB (prevents race condition)
            current_order = self.db.get_order_by_client_id(slice_detail.client_order_id)
            if current_order and current_order.status == "canceled":
                logger.info(
                    f"Slice execution aborted, already canceled in DB: parent={parent_order_id}, "
                    f"slice={slice_detail.slice_num}",
                    extra={
                        "parent_order_id": parent_order_id,
                        "slice_num": slice_detail.slice_num,
                        "client_order_id": slice_detail.client_order_id,
                        "abort_reason": "already_canceled_in_db",
                    },
                )
                return

            # 🔒 MANDATORY: Kill switch check
            if self.kill_switch.is_engaged():
                logger.warning(
                    f"Slice blocked by kill switch: parent={parent_order_id}, "
                    f"slice={slice_detail.slice_num}",
                    extra={
                        "parent_order_id": parent_order_id,
                        "slice_num": slice_detail.slice_num,
                        "client_order_id": slice_detail.client_order_id,
                        "block_reason": "kill_switch_engaged",
                    },
                )
                # Update DB status to 'blocked_kill_switch' with error message for debuggability
                self.db.update_order_status(
                    client_order_id=slice_detail.client_order_id,
                    status="blocked_kill_switch",
                    error_message="Kill switch is engaged - all new orders blocked",
                )
                # Do NOT raise - job should complete silently
                return

            # 🔒 MANDATORY: Circuit breaker check
            if self.breaker.is_tripped():
                reason = self.breaker.get_trip_reason()
                logger.warning(
                    f"Slice blocked by circuit breaker: parent={parent_order_id}, "
                    f"slice={slice_detail.slice_num}, reason={reason}",
                    extra={
                        "parent_order_id": parent_order_id,
                        "slice_num": slice_detail.slice_num,
                        "client_order_id": slice_detail.client_order_id,
                        "block_reason": "circuit_breaker_tripped",
                        "trip_reason": str(reason),
                    },
                )
                # Update DB status to 'blocked_circuit_breaker' with error message for debuggability
                self.db.update_order_status(
                    client_order_id=slice_detail.client_order_id,
                    status="blocked_circuit_breaker",
                    error_message=f"Circuit breaker is tripped - reason: {reason}",
                )
                return

        except Exception as infra_error:
            # Infrastructure failure (PostgreSQL/Redis outage) during pre-submission checks
            # Mark slice as failed to prevent silent job drops
            logger.error(
                f"Infrastructure failure during pre-submission checks: parent={parent_order_id}, "
                f"slice={slice_detail.slice_num}, error={infra_error}",
                extra={
                    "parent_order_id": parent_order_id,
                    "slice_num": slice_detail.slice_num,
                    "client_order_id": slice_detail.client_order_id,
                    "infrastructure_error": str(infra_error),
                },
            )
            # Try to mark slice as failed in DB (best effort)
            try:
                self.db.update_order_status(
                    client_order_id=slice_detail.client_order_id,
                    status="failed",
                    error_message=f"Infrastructure failure during pre-submission checks: {infra_error}",
                )
            except Exception:
                # Even marking as failed failed - log and re-raise original error
                logger.critical(
                    f"Cannot update DB after infrastructure failure: parent={parent_order_id}, "
                    f"slice={slice_detail.slice_num}",
                    extra={
                        "parent_order_id": parent_order_id,
                        "slice_num": slice_detail.slice_num,
                        "client_order_id": slice_detail.client_order_id,
                    },
                )
            # Re-raise to ensure APScheduler logs the failure
            raise

        # Create order request
        order_request = OrderRequest(
            symbol=symbol,
            side=side,
            qty=slice_detail.qty,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            time_in_force=time_in_force,
        )

        # 🔒 DEFENSE IN DEPTH: Double-check DB status immediately before submission
        # (second guard against race conditions during cancellation)
        # Wrap in try-catch to handle DB failures (same pattern as pre-submission checks above)
        try:
            current_order_pre_submit = self.db.get_order_by_client_id(slice_detail.client_order_id)
            if current_order_pre_submit and current_order_pre_submit.status == "canceled":
                logger.info(
                    f"Slice execution aborted at pre-submit guard, canceled in DB: parent={parent_order_id}, "
                    f"slice={slice_detail.slice_num}",
                    extra={
                        "parent_order_id": parent_order_id,
                        "slice_num": slice_detail.slice_num,
                        "client_order_id": slice_detail.client_order_id,
                        "abort_reason": "canceled_at_pre_submit_guard",
                    },
                )
                return
        except Exception as infra_error:
            # DB failure at pre-submit guard - mark as failed and re-raise
            logger.error(
                f"Infrastructure failure at pre-submit guard: parent={parent_order_id}, "
                f"slice={slice_detail.slice_num}, error={infra_error}",
                extra={
                    "parent_order_id": parent_order_id,
                    "slice_num": slice_detail.slice_num,
                    "client_order_id": slice_detail.client_order_id,
                    "infrastructure_error": str(infra_error),
                },
            )
            try:
                self.db.update_order_status(
                    client_order_id=slice_detail.client_order_id,
                    status="failed",
                    error_message=f"Infrastructure failure at pre-submit guard: {infra_error}",
                )
            except Exception:
                logger.critical(
                    f"Cannot update DB after pre-submit guard failure: parent={parent_order_id}, "
                    f"slice={slice_detail.slice_num}",
                    extra={
                        "parent_order_id": parent_order_id,
                        "slice_num": slice_detail.slice_num,
                        "client_order_id": slice_detail.client_order_id,
                    },
                )
            raise

        # Submit to broker (or log as dry-run if executor is None)
        try:
            if self.executor is None:
                # DRY_RUN mode: Log order without submitting to broker
                logger.info(
                    f"DRY_RUN: Slice would be submitted: parent={parent_order_id}, "
                    f"slice={slice_detail.slice_num}, qty={slice_detail.qty}",
                    extra={
                        "parent_order_id": parent_order_id,
                        "slice_num": slice_detail.slice_num,
                        "client_order_id": slice_detail.client_order_id,
                        "symbol": symbol,
                        "side": side,
                        "qty": str(slice_detail.qty),
                        "order_type": order_type,
                        "dry_run": True,
                    },
                )
                # Update DB status to 'dry_run'
                self.db.update_order_status(
                    client_order_id=slice_detail.client_order_id,
                    status="dry_run",
                )
            else:
                # Production mode: Submit to broker (with automatic retry on connection errors via @retry decorator)
                broker_response = self.executor.submit_order(
                    order=order_request,
                    client_order_id=slice_detail.client_order_id,
                )

                # 🔒 CRITICAL: Handle DB failures after broker submission to prevent inconsistent state
                # If DB update fails after Alpaca submission, the order is placed but DB shows pending_new,
                # creating double execution risk. We catch DB errors and retry with exponential backoff.
                max_db_retries = 3
                for retry_attempt in range(max_db_retries):
                    try:
                        # Update DB status to 'submitted' and clear any error message from failed retries
                        self.db.update_order_status(
                            client_order_id=slice_detail.client_order_id,
                            status="submitted",
                            broker_order_id=broker_response["id"],
                            error_message="",  # Clear any error from previous retry attempts
                        )

                        logger.info(
                            f"Slice submitted successfully: parent={parent_order_id}, "
                            f"slice={slice_detail.slice_num}, broker_id={broker_response['id']}",
                            extra={
                                "parent_order_id": parent_order_id,
                                "slice_num": slice_detail.slice_num,
                                "client_order_id": slice_detail.client_order_id,
                                "broker_order_id": broker_response["id"],
                            },
                        )
                        break  # Success, exit retry loop

                    except Exception as db_error:
                        if retry_attempt < max_db_retries - 1:
                            # Retry with exponential backoff
                            wait_time = 2**retry_attempt  # 1s, 2s, 4s
                            logger.warning(
                                f"DB update failed (attempt {retry_attempt + 1}/{max_db_retries}), "
                                f"retrying in {wait_time}s: {db_error}",
                                extra={
                                    "parent_order_id": parent_order_id,
                                    "slice_num": slice_detail.slice_num,
                                    "client_order_id": slice_detail.client_order_id,
                                    "broker_order_id": broker_response["id"],
                                    "retry_attempt": retry_attempt + 1,
                                },
                            )
                            time.sleep(wait_time)
                        else:
                            # All retries exhausted - mark slice as submitted_unconfirmed to prevent cancellation
                            logger.error(
                                f"CRITICAL: All DB update retries exhausted after broker submission: "
                                f"parent={parent_order_id}, slice={slice_detail.slice_num}, "
                                f"broker_id={broker_response['id']}, error={db_error}. "
                                f"Marking slice as 'submitted_unconfirmed' to prevent double execution.",
                                extra={
                                    "parent_order_id": parent_order_id,
                                    "slice_num": slice_detail.slice_num,
                                    "client_order_id": slice_detail.client_order_id,
                                    "broker_order_id": broker_response["id"],
                                    "db_error": str(db_error),
                                    "inconsistent_state": True,
                                },
                            )
                            # Last-ditch attempt: mark as submitted_unconfirmed to prevent cancellation/retry
                            # Reconciliation will eventually heal this, but we prevent immediate double execution
                            try:
                                self.db.update_order_status(
                                    client_order_id=slice_detail.client_order_id,
                                    status="submitted_unconfirmed",
                                    broker_order_id=broker_response["id"],
                                    error_message=f"DB update failed after broker submission: {db_error}",
                                )
                            except Exception:
                                # Even this failed - log and rely on reconciliation
                                logger.critical(
                                    f"CRITICAL: Cannot update DB at all after broker submission. "
                                    f"Manual intervention required: broker_id={broker_response['id']}",
                                    extra={
                                        "parent_order_id": parent_order_id,
                                        "client_order_id": slice_detail.client_order_id,
                                        "broker_order_id": broker_response["id"],
                                    },
                                )
                            # Re-raise to ensure APScheduler logs the failure
                            raise

        except (AlpacaValidationError, AlpacaRejectionError) as e:
            # Non-retryable errors - update DB and log
            self.db.update_order_status(
                client_order_id=slice_detail.client_order_id,
                status="rejected",
                error_message=str(e),
            )
            logger.error(
                f"Slice rejected: parent={parent_order_id}, "
                f"slice={slice_detail.slice_num}, error={e}",
                extra={
                    "parent_order_id": parent_order_id,
                    "slice_num": slice_detail.slice_num,
                    "client_order_id": slice_detail.client_order_id,
                    "error": str(e),
                },
            )

        except AlpacaConnectionError as e:
            # Transient connection error - log and re-raise for tenacity to retry
            # Do NOT mark as failed here - tenacity will retry up to 3 times
            # Only if ALL retries are exhausted will this exception propagate to APScheduler
            logger.warning(
                f"Slice submission connection error (will retry): parent={parent_order_id}, "
                f"slice={slice_detail.slice_num}, error={e}",
                extra={
                    "parent_order_id": parent_order_id,
                    "slice_num": slice_detail.slice_num,
                    "client_order_id": slice_detail.client_order_id,
                    "error": str(e),
                    "will_retry": True,
                },
            )
            # Re-raise to trigger tenacity retry logic
            # If all retries fail, APScheduler will log the final exception
            raise

    def cancel_remaining_slices(self, parent_order_id: str) -> tuple[int, int]:
        """
        Cancel all pending jobs for parent order and update DB.

        Removes scheduled APScheduler jobs matching the parent order ID pattern
        and updates the database to mark all pending_new slices as canceled.

        Args:
            parent_order_id: Parent order's client_order_id

        Returns:
            Tuple of (scheduler_canceled_count, db_canceled_count)

        Example:
            >>> scheduler.schedule_slices(...)
            >>> # ... later ...
            >>> scheduler_count, db_count = scheduler.cancel_remaining_slices("parent123")
            >>> scheduler_count, db_count
            (3, 3)

        Notes:
            - Job IDs match pattern: "{parent_order_id}_slice_*"
            - DB update happens BEFORE job removal to close race condition
            - Returns tuple: (jobs removed from scheduler, rows updated in DB)
            - Safe to call even if no jobs exist (returns (0, 0))
            - Caller must validate parent order existence (API boundary validation)
        """
        # 🔒 CRITICAL: Update DB FIRST so any jobs that fire during removal see 'canceled' status
        # Note: Caller (main.py cancel_slices endpoint) validates parent order existence
        db_canceled = self.db.cancel_pending_slices(parent_order_id)

        # Now remove scheduled jobs (any that fire during this loop will see 'canceled' in DB)
        # Guard against JobLookupError: APScheduler removes jobs immediately after execution,
        # so a job that fires between get_jobs() and remove_job() will raise JobLookupError.
        # We swallow this specific error to make cancellation idempotent and prevent 500s when
        # users cancel near the scheduled fire time.
        canceled_count = 0
        for job in self.scheduler.get_jobs():
            if job.id.startswith(f"{parent_order_id}_slice_"):
                try:
                    self.scheduler.remove_job(job.id)
                    canceled_count += 1
                except JobLookupError:
                    # Job already executed and removed - this is expected and safe
                    # DB was already marked canceled (line 695), so we can safely ignore
                    logger.debug(
                        f"Job already removed (likely executed): {job.id}",
                        extra={"job_id": job.id, "parent_order_id": parent_order_id},
                    )

        logger.info(
            f"Canceled {canceled_count} scheduler jobs and {db_canceled} DB slices for parent: {parent_order_id}",
            extra={
                "parent_order_id": parent_order_id,
                "scheduler_canceled": canceled_count,
                "db_canceled": db_canceled,
            },
        )

        return (canceled_count, db_canceled)
