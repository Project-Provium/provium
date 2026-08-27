from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import pytest

from provium_pipeline.dispatch.selection import DispatchLimits, select_ready_tasks
from provium_pipeline.identifiers import InputRecordKey, TaskId
from provium_pipeline.run.models import TaskState


@dataclass(frozen=True)
class _Task:
    identifier: TaskId
    node_identifier: str
    record_key: InputRecordKey
    procedure_identifier: str
    state: TaskState


def _task(
    number: int,
    node: str,
    record: str,
    procedure: str,
    state: TaskState = TaskState.READY,
) -> _Task:
    return _Task(
        TaskId(UUID(f"00000000-0000-0000-0000-{number:012d}")),
        node,
        InputRecordKey(record),
        procedure,
        state,
    )


def test_selection_is_stable_by_node_order_then_record_key() -> None:
    tasks = (
        _task(1, "second", "b", "p2"),
        _task(2, "first", "b", "p1"),
        _task(3, "first", "a", "p1"),
        _task(4, "second", "a", "p2", TaskState.BLOCKED),
        _task(5, "second", "c", "p2"),
    )

    selected = select_ready_tasks(
        tasks,
        active_tasks=(),
        node_order={"first": 0, "second": 1},
        limits=DispatchLimits(max_workers=3),
    )

    assert tuple(task.identifier for task in selected) == (
        tasks[2].identifier,
        tasks[1].identifier,
        tasks[0].identifier,
    )


def test_selection_obeys_global_procedure_and_group_capacity() -> None:
    tasks = (
        _task(1, "a", "1", "extract"),
        _task(2, "a", "2", "extract"),
        _task(3, "b", "1", "transform"),
        _task(4, "c", "1", "publish"),
    )
    active = (_task(5, "active", "1", "transform", TaskState.READY),)
    limits = DispatchLimits(
        max_workers=4,
        procedure_limits={"extract": 1},
        procedure_groups={"transform": "io", "publish": "io"},
        group_limits={"io": 2},
    )

    selected = select_ready_tasks(
        tasks,
        active_tasks=active,
        node_order={"a": 0, "b": 1, "c": 2},
        limits=limits,
    )

    assert selected == (tasks[0], tasks[2])


def test_empty_or_full_selection_is_empty() -> None:
    limits = DispatchLimits(max_workers=1)
    active = (_task(1, "active", "1", "p", TaskState.READY),)

    assert select_ready_tasks((), active_tasks=(), node_order={}, limits=limits) == ()
    assert (
        select_ready_tasks(
            (_task(2, "node", "1", "p"),),
            active_tasks=active,
            node_order={"node": 0},
            limits=limits,
        )
        == ()
    )


@pytest.mark.parametrize("limit", [0, -1])
def test_dispatch_limits_must_be_positive(limit: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        DispatchLimits(max_workers=limit)
