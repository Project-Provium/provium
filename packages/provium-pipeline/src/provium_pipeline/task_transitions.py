"""Compare-and-set validation for durable pipeline task states."""

from __future__ import annotations

from dataclasses import replace

from provium_pipeline.run_models import PipelineTask, TaskState


class TaskStateConflictError(RuntimeError):
    """A task changed after the caller read its expected state."""


class InvalidTaskTransitionError(ValueError):
    """A requested task lifecycle edge is not legal."""


_LEGAL_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.BLOCKED: frozenset({TaskState.READY, TaskState.CANCELLED}),
    TaskState.READY: frozenset(
        {
            TaskState.WAITING_ON_COMPUTATION,
            TaskState.LEASED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.WAITING_ON_COMPUTATION: frozenset(
        {TaskState.READY, TaskState.REUSED, TaskState.CANCELLED}
    ),
    TaskState.LEASED: frozenset(
        {
            TaskState.SUCCEEDED,
            TaskState.RETRY_WAIT,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.RETRY_WAIT: frozenset(
        {TaskState.READY, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.SUCCEEDED: frozenset(),
    TaskState.REUSED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


def transition_task(
    task: PipelineTask,
    *,
    expected: TaskState,
    target: TaskState,
) -> PipelineTask:
    """Return the immutable CAS transition or raise a typed conflict."""
    if task.state is not expected:
        raise TaskStateConflictError(
            f"expected {expected.value}, found {task.state.value}"
        )
    if target is expected:
        return task
    if target not in _LEGAL_TRANSITIONS[expected]:
        raise InvalidTaskTransitionError(
            f"illegal task transition: {expected.value} -> {target.value}"
        )
    return replace(task, state=target)
