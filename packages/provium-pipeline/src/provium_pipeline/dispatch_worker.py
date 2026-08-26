"""Serial execution adapter for durable dispatch tasks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from .attempts import TaskAttemptLease
from .dispatch_models import Dispatch
from .identifiers import TaskId
from .run_models import PipelineTask, TaskState
from .serial import SerialRunner

_TERMINAL_TASK_STATES = frozenset(
    {
        TaskState.SUCCEEDED,
        TaskState.REUSED,
        TaskState.FAILED,
        TaskState.CANCELLED,
    }
)


class ExecutionTasks(Protocol):
    """Durable task state required by a dispatch worker."""

    def get_task(self, identifier: TaskId) -> PipelineTask: ...

    def transition_task(
        self,
        identifier: TaskId,
        *,
        expected: TaskState,
        target: TaskState,
    ) -> PipelineTask: ...


class AttemptLeases(Protocol):
    """Attempt lease operations required by a serial worker."""

    def claim(
        self,
        task: TaskId,
        *,
        state: TaskState,
        worker_identity: str,
        token: str,
        now: datetime,
        ttl: timedelta,
    ) -> TaskAttemptLease: ...

    def release(
        self,
        task: TaskId,
        *,
        token: str,
        ended_at: datetime,
    ) -> TaskAttemptLease: ...


@dataclass(frozen=True, slots=True)
class _ClaimedTask:
    task: PipelineTask
    lease: TaskAttemptLease


class SerialDispatchWorker:
    """Claim and execute selected dispatch tasks one at a time."""

    def __init__(
        self,
        *,
        executions: ExecutionTasks,
        attempts: AttemptLeases,
        execute_task: Callable[[PipelineTask, TaskAttemptLease], None],
        clock: Callable[[], datetime],
        token_factory: Callable[[], str],
        worker_identity: str,
        lease_ttl: timedelta,
        wait_for_work: Callable[[], None],
    ) -> None:
        if lease_ttl <= timedelta(0):
            raise ValueError("lease ttl must be positive")
        self._executions = executions
        self._attempts = attempts
        self._execute_task = execute_task
        self._clock = clock
        self._token_factory = token_factory
        self._worker_identity = worker_identity
        self._lease_ttl = lease_ttl
        self._wait_for_work = wait_for_work

    def run(self, dispatch: Dispatch) -> None:
        SerialRunner(
            claim=lambda: self._claim(dispatch),
            execute=self._execute,
            is_terminal=lambda: self._is_terminal(dispatch),
            wait_for_work=self._wait_for_work,
        ).run()

    def _claim(self, dispatch: Dispatch) -> _ClaimedTask | None:
        for identifier in dispatch.task_identifiers:
            task = self._executions.get_task(identifier)
            if task.state not in {TaskState.READY, TaskState.RETRY_WAIT}:
                continue
            now = self._clock()
            token = self._token_factory()
            lease = self._attempts.claim(
                identifier,
                state=task.state,
                worker_identity=self._worker_identity,
                token=token,
                now=now,
                ttl=self._lease_ttl,
            )
            try:
                leased = self._executions.transition_task(
                    identifier,
                    expected=task.state,
                    target=TaskState.LEASED,
                )
            except BaseException:
                self._attempts.release(identifier, token=token, ended_at=self._clock())
                raise
            return _ClaimedTask(leased, lease)
        return None

    def _execute(self, claimed: _ClaimedTask) -> None:
        try:
            self._execute_task(claimed.task, claimed.lease)
        except Exception:
            self._executions.transition_task(
                claimed.task.identifier,
                expected=TaskState.LEASED,
                target=TaskState.FAILED,
            )
            raise
        else:
            self._executions.transition_task(
                claimed.task.identifier,
                expected=TaskState.LEASED,
                target=TaskState.SUCCEEDED,
            )
        finally:
            self._attempts.release(
                claimed.task.identifier,
                token=claimed.lease.token,
                ended_at=self._clock(),
            )

    def _is_terminal(self, dispatch: Dispatch) -> bool:
        return all(
            self._executions.get_task(identifier).state in _TERMINAL_TASK_STATES
            for identifier in dispatch.task_identifiers
        )
