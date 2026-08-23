"""Task attempts, fenced leases, and deterministic retry decisions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from threading import RLock

from provium_pipeline.identifiers import TaskId
from provium_pipeline.run_models import TaskState


class LeaseConflictError(RuntimeError):
    """A lease is missing, owned by another token, or expired."""


@dataclass(frozen=True, slots=True)
class TaskAttemptLease:
    """One fenced task attempt and its current lease timestamps."""

    task_identifier: TaskId
    attempt_number: int
    worker_identity: str
    token: str
    started_at: datetime
    heartbeat_at: datetime
    expires_at: datetime
    ended_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RetryDecision:
    """Durable state and eligibility time after an attempt failure."""

    state: TaskState
    eligible_at: datetime | None

    def is_eligible(self, now: datetime) -> bool:
        """Return whether this decision permits a retry at ``now``."""
        return self.eligible_at is not None and self.eligible_at <= now


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Capped exponential retry policy with deterministic timing."""

    max_attempts: int
    initial_backoff: timedelta = timedelta(seconds=1)
    multiplier: float = 2
    max_backoff: timedelta = timedelta(minutes=5)

    def __post_init__(self) -> None:
        if self.max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        if self.initial_backoff < timedelta(0) or self.max_backoff < timedelta(0):
            raise ValueError("retry backoffs cannot be negative")
        if self.multiplier < 1:
            raise ValueError("retry multiplier must be at least one")

    def after_failure(
        self,
        *,
        attempt_number: int,
        retryable: bool,
        now: datetime,
    ) -> RetryDecision:
        """Choose terminal failure or the next retry eligibility time."""
        if attempt_number <= 0:
            raise ValueError("attempt_number must be positive")
        if not retryable or attempt_number >= self.max_attempts:
            return RetryDecision(TaskState.FAILED, None)
        delay_seconds = self.initial_backoff.total_seconds() * (
            self.multiplier ** (attempt_number - 1)
        )
        delay = min(timedelta(seconds=delay_seconds), self.max_backoff)
        return RetryDecision(TaskState.RETRY_WAIT, now + delay)


class AttemptLeaseManager:
    """Thread-safe reference manager for task attempt lease fencing."""

    def __init__(self) -> None:
        self._active: dict[TaskId, TaskAttemptLease] = {}
        self._attempt_numbers: dict[TaskId, int] = {}
        self._lock = RLock()

    def claim(
        self,
        task: TaskId,
        *,
        state: TaskState,
        worker_identity: str,
        token: str,
        now: datetime,
        ttl: timedelta,
    ) -> TaskAttemptLease:
        """Atomically claim a ready or retry-eligible task."""
        if state not in {TaskState.READY, TaskState.RETRY_WAIT}:
            raise ValueError("task state is not claimable")
        if ttl <= timedelta(0):
            raise ValueError("lease ttl must be positive")
        with self._lock:
            if task in self._active:
                raise LeaseConflictError("task already has an active lease")
            attempt_number = self._attempt_numbers.get(task, 0) + 1
            lease = TaskAttemptLease(
                task_identifier=task,
                attempt_number=attempt_number,
                worker_identity=worker_identity,
                token=token,
                started_at=now,
                heartbeat_at=now,
                expires_at=now + ttl,
            )
            self._active[task] = lease
            self._attempt_numbers[task] = attempt_number
            return lease

    def renew(
        self,
        task: TaskId,
        *,
        token: str,
        now: datetime,
        ttl: timedelta,
    ) -> TaskAttemptLease:
        """Extend an unexpired lease held by the matching fence token."""
        if ttl <= timedelta(0):
            raise ValueError("lease ttl must be positive")
        with self._lock:
            lease = self._require_token(task, token)
            if lease.expires_at <= now:
                raise LeaseConflictError("lease has expired")
            renewed = replace(lease, heartbeat_at=now, expires_at=now + ttl)
            self._active[task] = renewed
            return renewed

    def release(
        self,
        task: TaskId,
        *,
        token: str,
        ended_at: datetime,
    ) -> TaskAttemptLease:
        """End and remove the lease held by the matching fence token."""
        with self._lock:
            lease = self._require_token(task, token)
            del self._active[task]
            return replace(lease, ended_at=ended_at)

    def recover_expired(self, now: datetime) -> tuple[TaskAttemptLease, ...]:
        """Atomically remove and return expired leases in task-ID order."""
        with self._lock:
            expired = tuple(
                sorted(
                    (
                        lease
                        for lease in self._active.values()
                        if lease.expires_at <= now
                    ),
                    key=lambda lease: str(lease.task_identifier),
                )
            )
            for lease in expired:
                del self._active[lease.task_identifier]
            return expired

    def _require_token(self, task: TaskId, token: str) -> TaskAttemptLease:
        lease = self._active.get(task)
        if lease is None or lease.token != token:
            raise LeaseConflictError("lease token does not own task")
        return lease
