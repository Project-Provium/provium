from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from threading import RLock
from typing import Protocol

from .cancellation import cancelled_task_state
from .identifiers import RunId, TaskId
from .run_models import (
    CreateRunRequest,
    PipelineRun,
    PipelineTask,
    RunPlan,
    RunState,
    TaskState,
    plan_run,
)
from .run_transitions import apply_run_transition
from .task_transitions import transition_task as apply_task_transition


class RunIdempotencyConflictError(ValueError):
    """A scoped idempotency key was reused for a different run request."""


class ExecutionStore(Protocol):
    """Atomic persistence boundary for pipeline execution state."""

    def create_run(self, request: CreateRunRequest) -> PipelineRun: ...

    def get_run(self, identifier: RunId) -> PipelineRun: ...

    def list_runs(self) -> tuple[PipelineRun, ...]: ...

    def list_tasks(self, run_identifier: RunId) -> tuple[PipelineTask, ...]: ...

    def get_task(self, identifier: TaskId) -> PipelineTask: ...

    def cancel_run(self, identifier: RunId) -> PipelineRun: ...

    def transition_run(
        self,
        identifier: RunId,
        *,
        expected: RunState,
        target: RunState,
    ) -> PipelineRun: ...

    def transition_task(
        self,
        identifier: TaskId,
        *,
        expected: TaskState,
        target: TaskState,
    ) -> PipelineTask: ...


class _RunPlanner(Protocol):
    def __call__(
        self,
        request: CreateRunRequest,
        *,
        now: datetime,
    ) -> RunPlan: ...


class InMemoryExecutionStore:
    """Thread-safe deterministic reference execution-store adapter."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        planner: _RunPlanner = plan_run,
    ) -> None:
        self._clock = clock
        self._planner = planner
        self._lock = RLock()
        self._runs: dict[RunId, PipelineRun] = {}
        self._tasks: dict[RunId, tuple[PipelineTask, ...]] = {}
        self._idempotency: dict[tuple[str, str], RunId] = {}

    def create_run(self, request: CreateRunRequest) -> PipelineRun:
        with self._lock:
            scoped_key = self._scoped_key(request)
            if scoped_key is not None:
                existing_id = self._idempotency.get(scoped_key)
                if existing_id is not None:
                    existing = self._runs[existing_id]
                    if existing.request_digest != request.digest:
                        namespace, key = scoped_key
                        raise RunIdempotencyConflictError(
                            f"idempotency key conflicts: {namespace}/{key}"
                        )
                    return existing
            plan = self._planner(request, now=self._clock())
            self._runs[plan.run.identifier] = plan.run
            self._tasks[plan.run.identifier] = plan.tasks
            if scoped_key is not None:
                self._idempotency[scoped_key] = plan.run.identifier
            return plan.run

    def get_run(self, identifier: RunId) -> PipelineRun:
        with self._lock:
            return self._runs[identifier]

    def list_runs(self) -> tuple[PipelineRun, ...]:
        with self._lock:
            return tuple(self._runs.values())

    def list_tasks(self, run_identifier: RunId) -> tuple[PipelineTask, ...]:
        with self._lock:
            return self._tasks.get(run_identifier, ())

    def get_task(self, identifier: TaskId) -> PipelineTask:
        with self._lock:
            _, _, task = self._locate_task(identifier)
            return task

    def cancel_run(self, identifier: RunId) -> PipelineRun:
        with self._lock:
            run = self._runs[identifier]
            cancelled = (
                run
                if run.state is RunState.CANCELLED
                else apply_run_transition(
                    run,
                    expected=run.state,
                    target=RunState.CANCELLED,
                )
            )
            tasks = tuple(
                replace(task, state=cancelled_task_state(task.state))
                for task in self._tasks[identifier]
            )
            self._runs[identifier] = cancelled
            self._tasks[identifier] = tasks
            return cancelled

    def transition_run(
        self,
        identifier: RunId,
        *,
        expected: RunState,
        target: RunState,
    ) -> PipelineRun:
        with self._lock:
            run = self._runs[identifier]
            transitioned = apply_run_transition(
                run,
                expected=expected,
                target=target,
            )
            if transitioned is not run:
                self._runs[identifier] = transitioned
            return transitioned

    def transition_task(
        self,
        identifier: TaskId,
        *,
        expected: TaskState,
        target: TaskState,
    ) -> PipelineTask:
        with self._lock:
            run_identifier, index, task = self._locate_task(identifier)
            transitioned = apply_task_transition(
                task,
                expected=expected,
                target=target,
            )
            if transitioned is task:
                return task
            tasks = list(self._tasks[run_identifier])
            tasks[index] = transitioned
            self._tasks[run_identifier] = tuple(tasks)
            return transitioned

    def _locate_task(self, identifier: TaskId) -> tuple[RunId, int, PipelineTask]:
        for run_identifier, tasks in self._tasks.items():
            for index, task in enumerate(tasks):
                if task.identifier == identifier:
                    return run_identifier, index, task
        raise KeyError(identifier)

    @staticmethod
    def _scoped_key(request: CreateRunRequest) -> tuple[str, str] | None:
        if request.idempotency_key is None:
            return None
        return (request.idempotency_namespace or "default", request.idempotency_key)


__all__ = [
    "ExecutionStore",
    "InMemoryExecutionStore",
    "RunIdempotencyConflictError",
]
