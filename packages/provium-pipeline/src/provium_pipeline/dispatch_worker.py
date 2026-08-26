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

_SUCCESSFUL_TASK_STATES = frozenset({TaskState.SUCCEEDED, TaskState.REUSED})
_FAILED_TASK_STATES = frozenset({TaskState.FAILED, TaskState.CANCELLED})
_TERMINAL_TASK_STATES = _SUCCESSFUL_TASK_STATES | _FAILED_TASK_STATES


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
        is_retryable: Callable[[Exception], bool],
        wait_until: Callable[[datetime], None],
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
        self._is_retryable = is_retryable
        self._wait_until = wait_until

    def run(self, dispatch: Dispatch) -> None:
        SerialRunner(
            claim=lambda: self._claim(dispatch),
            execute=lambda claimed: self._execute(claimed, dispatch),
            is_terminal=lambda: self._is_terminal(dispatch),
            wait_for_work=self._wait_for_work,
        ).run()

    def _claim(self, dispatch: Dispatch) -> _ClaimedTask | None:
        for identifier in dispatch.task_identifiers:
            task = self._resolve_blocked(self._executions.get_task(identifier))
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

    def _resolve_blocked(self, task: PipelineTask) -> PipelineTask:
        if task.state is not TaskState.BLOCKED:
            return task
        dependency_states = tuple(
            self._executions.get_task(identifier).state
            for identifier in task.dependencies
        )
        if any(state in _FAILED_TASK_STATES for state in dependency_states):
            target = TaskState.CANCELLED
        elif all(state in _SUCCESSFUL_TASK_STATES for state in dependency_states):
            target = TaskState.READY
        else:
            return task
        return self._executions.transition_task(
            task.identifier,
            expected=TaskState.BLOCKED,
            target=target,
        )

    def _execute(self, claimed: _ClaimedTask, dispatch: Dispatch) -> None:
        retry_at: datetime | None = None
        try:
            self._execute_task(claimed.task, claimed.lease)
        except Exception as error:
            decision = dispatch.retry_policy.after_failure(
                attempt_number=claimed.lease.attempt_number,
                retryable=self._is_retryable(error),
                now=self._clock(),
            )
            self._executions.transition_task(
                claimed.task.identifier,
                expected=TaskState.LEASED,
                target=decision.state,
            )
            retry_at = decision.eligible_at
            if decision.state is not TaskState.RETRY_WAIT:
                raise
        else:
            self._executions.transition_task(
                claimed.task.identifier,
                expected=TaskState.LEASED,
                target=TaskState.SUCCEEDED,
            )
            return
        finally:
            self._attempts.release(
                claimed.task.identifier,
                token=claimed.lease.token,
                ended_at=self._clock(),
            )
        assert retry_at is not None
        self._wait_until(retry_at)
        self._executions.transition_task(
            claimed.task.identifier,
            expected=TaskState.RETRY_WAIT,
            target=TaskState.READY,
        )

    def _is_terminal(self, dispatch: Dispatch) -> bool:
        return all(
            self._executions.get_task(identifier).state in _TERMINAL_TASK_STATES
            for identifier in dispatch.task_identifiers
        )
