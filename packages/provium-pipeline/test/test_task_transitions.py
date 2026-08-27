from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest

from provium_pipeline.execution_store import InMemoryExecutionStore
from provium_pipeline.identifiers import InputRecordKey, RunId, TaskId
from provium_pipeline.run.models import PipelineTask, TaskState
from provium_pipeline.task_transitions import (
    InvalidTaskTransitionError,
    TaskStateConflictError,
    transition_task,
)


def _task(state: TaskState) -> PipelineTask:
    return PipelineTask(
        identifier=TaskId(UUID("00000000-0000-0000-0000-000000000001")),
        run_identifier=RunId(UUID("00000000-0000-0000-0000-000000000002")),
        node_identifier="node",
        record_key=InputRecordKey("record"),
        procedure_identifier="example.process/v1",
        procedure_contract_digest="a" * 64,
        configuration_snapshot=None,
        setup_bindings=(),
        input_bindings=(),
        expected_output_fields=(),
        dependencies=(),
        state=state,
    )


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (TaskState.BLOCKED, TaskState.READY),
        (TaskState.BLOCKED, TaskState.CANCELLED),
        (TaskState.READY, TaskState.WAITING_ON_COMPUTATION),
        (TaskState.READY, TaskState.LEASED),
        (TaskState.READY, TaskState.CANCELLED),
        (TaskState.WAITING_ON_COMPUTATION, TaskState.READY),
        (TaskState.WAITING_ON_COMPUTATION, TaskState.REUSED),
        (TaskState.WAITING_ON_COMPUTATION, TaskState.CANCELLED),
        (TaskState.LEASED, TaskState.SUCCEEDED),
        (TaskState.LEASED, TaskState.RETRY_WAIT),
        (TaskState.LEASED, TaskState.FAILED),
        (TaskState.LEASED, TaskState.CANCELLED),
        (TaskState.RETRY_WAIT, TaskState.READY),
        (TaskState.RETRY_WAIT, TaskState.FAILED),
        (TaskState.RETRY_WAIT, TaskState.CANCELLED),
    ],
)
def test_legal_task_transitions_return_a_new_immutable_task(
    source: TaskState,
    target: TaskState,
) -> None:
    original = _task(source)

    transitioned = transition_task(original, expected=source, target=target)

    assert transitioned.state is target
    assert original.state is source
    assert transitioned.identifier == original.identifier


def test_transition_is_idempotent_when_expected_and_target_match() -> None:
    task = _task(TaskState.READY)

    assert (
        transition_task(task, expected=TaskState.READY, target=TaskState.READY) is task
    )


def test_in_memory_store_persists_atomic_compare_and_set_transition() -> None:
    task = _task(TaskState.READY)
    store = InMemoryExecutionStore(clock=lambda: datetime(2026, 8, 23, tzinfo=UTC))
    storage = cast(
        dict[RunId, tuple[PipelineTask, ...]],
        getattr(store, "_tasks"),
    )
    storage[task.run_identifier] = (task,)

    transitioned = store.transition_task(
        task.identifier,
        expected=TaskState.READY,
        target=TaskState.LEASED,
    )

    assert transitioned.state is TaskState.LEASED
    assert store.get_task(task.identifier) == transitioned
    assert store.list_tasks(task.run_identifier) == (transitioned,)
    assert (
        store.transition_task(
            task.identifier,
            expected=TaskState.LEASED,
            target=TaskState.LEASED,
        )
        is transitioned
    )

    with pytest.raises(TaskStateConflictError):
        store.transition_task(
            task.identifier,
            expected=TaskState.READY,
            target=TaskState.CANCELLED,
        )
    assert store.get_task(task.identifier) == transitioned


def test_in_memory_store_rejects_unknown_task_identifier() -> None:
    store = InMemoryExecutionStore(clock=lambda: datetime(2026, 8, 23, tzinfo=UTC))
    existing = _task(TaskState.READY)
    storage = cast(
        dict[RunId, tuple[PipelineTask, ...]],
        getattr(store, "_tasks"),
    )
    storage[existing.run_identifier] = (existing,)
    missing = TaskId(UUID("00000000-0000-0000-0000-000000000099"))

    with pytest.raises(KeyError):
        store.get_task(missing)
    with pytest.raises(KeyError):
        store.transition_task(
            missing,
            expected=TaskState.READY,
            target=TaskState.LEASED,
        )


def test_compare_and_set_rejects_stale_expected_state() -> None:
    with pytest.raises(TaskStateConflictError, match="expected ready"):
        transition_task(
            _task(TaskState.LEASED),
            expected=TaskState.READY,
            target=TaskState.CANCELLED,
        )


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (TaskState.BLOCKED, TaskState.SUCCEEDED),
        (TaskState.READY, TaskState.SUCCEEDED),
        (TaskState.SUCCEEDED, TaskState.READY),
        (TaskState.REUSED, TaskState.READY),
        (TaskState.FAILED, TaskState.READY),
        (TaskState.CANCELLED, TaskState.READY),
    ],
)
def test_illegal_or_terminal_transitions_are_rejected(
    source: TaskState,
    target: TaskState,
) -> None:
    with pytest.raises(InvalidTaskTransitionError):
        transition_task(_task(source), expected=source, target=target)
