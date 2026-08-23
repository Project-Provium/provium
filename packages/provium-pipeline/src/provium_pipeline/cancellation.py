"""Fenced cooperative cancellation for pipeline dispatch work."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from provium import CancellationToken
from provium_pipeline.attempts import LeaseConflictError
from provium_pipeline.identifiers import TaskId
from provium_pipeline.run_models import TaskState

_CANCELLABLE_STATES = frozenset(
    {
        TaskState.BLOCKED,
        TaskState.READY,
        TaskState.WAITING_ON_COMPUTATION,
        TaskState.LEASED,
        TaskState.RETRY_WAIT,
    }
)


def cancelled_task_state(state: TaskState) -> TaskState:
    """Return the durable state after cancellation is fully observed."""
    if state in _CANCELLABLE_STATES:
        return TaskState.CANCELLED
    return state


@dataclass(frozen=True, slots=True)
class _ActiveCancellation:
    lease_token: str
    cancellation: CancellationToken


class DispatchCancellationController:
    """Stop new claims and signal currently active fenced attempts."""

    def __init__(self) -> None:
        self._requested = False
        self._active: dict[TaskId, _ActiveCancellation] = {}
        self._lock = RLock()

    @property
    def can_claim(self) -> bool:
        """Return whether the dispatch may claim additional work."""
        with self._lock:
            return not self._requested

    def request_cancellation(self) -> bool:
        """Idempotently request cancellation and signal active attempts."""
        with self._lock:
            first_request = not self._requested
            self._requested = True
            for active in self._active.values():
                active.cancellation.cancel()
            return first_request

    def register(
        self,
        task: TaskId,
        *,
        lease_token: str,
        cancellation: CancellationToken,
    ) -> None:
        """Register the cooperative token for one fenced active attempt."""
        with self._lock:
            if task in self._active:
                raise LeaseConflictError("task already has active cancellation state")
            self._active[task] = _ActiveCancellation(lease_token, cancellation)
            if self._requested:
                cancellation.cancel()

    def unregister(
        self,
        task: TaskId,
        *,
        lease_token: str,
    ) -> CancellationToken:
        """Remove an active attempt only when its fencing token matches."""
        with self._lock:
            active = self._active.get(task)
            if active is None or active.lease_token != lease_token:
                raise LeaseConflictError("lease token does not own cancellation state")
            del self._active[task]
            return active.cancellation
