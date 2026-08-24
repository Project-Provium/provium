from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal, Never

import pytest

from provium_pipeline.execution_store import (
    ExecutionStore,
    InMemoryExecutionStore,
    RunIdempotencyConflictError,
)
from provium_pipeline.identifiers import RunId, TaskId
from provium_pipeline.run_models import CreateRunRequest, RunPlan, TaskState, plan_run
from provium_pipeline.sqlite_execution_store import SQLiteExecutionStore
from provium_pipeline.task_transitions import TaskStateConflictError
from test.test_execution_store import NOW, request

Adapter = Literal["memory", "sqlite"]
Planner = Callable[..., RunPlan]


def _store(
    adapter: Adapter,
    tmp_path: Path,
    *,
    planner: Planner = plan_run,
) -> ExecutionStore:
    if adapter == "memory":
        return InMemoryExecutionStore(clock=lambda: NOW, planner=planner)
    return SQLiteExecutionStore(
        tmp_path / "execution.sqlite3",
        clock=lambda: NOW,
        planner=planner,
    )


@pytest.mark.parametrize("adapter", ["memory", "sqlite"])
def test_execution_store_conformance_covers_run_and_task_lifecycle(
    adapter: Adapter,
    tmp_path: Path,
) -> None:
    store = _store(adapter, tmp_path)

    run = store.create_run(request(key=None))
    tasks = store.list_tasks(run.identifier)

    assert store.get_run(run.identifier) == run
    assert {item.identifier for item in store.list_runs()} == {run.identifier}
    assert tasks
    assert all(task.run_identifier == run.identifier for task in tasks)
    assert store.get_task(tasks[0].identifier) == tasks[0]

    unknown_run = RunId.new()
    unknown_task = TaskId.new()
    with pytest.raises(KeyError):
        store.get_run(unknown_run)
    assert store.list_tasks(unknown_run) == ()
    with pytest.raises(KeyError):
        store.get_task(unknown_task)


@pytest.mark.parametrize("adapter", ["memory", "sqlite"])
def test_execution_store_conformance_covers_idempotency(
    adapter: Adapter,
    tmp_path: Path,
) -> None:
    store = _store(adapter, tmp_path)
    original = store.create_run(request())

    assert store.create_run(request()) == original
    with pytest.raises(RunIdempotencyConflictError, match="test/same"):
        store.create_run(request("different"))
    assert store.create_run(request(key=None)).identifier != original.identifier


@pytest.mark.parametrize("adapter", ["memory", "sqlite"])
def test_execution_store_conformance_rolls_back_planner_failures(
    adapter: Adapter,
    tmp_path: Path,
) -> None:
    def fail(
        request: CreateRunRequest,
        *,
        now: object,
    ) -> Never:
        del request, now
        raise RuntimeError("planning failed")

    store = _store(adapter, tmp_path, planner=fail)

    with pytest.raises(RuntimeError, match="planning failed"):
        store.create_run(request())
    assert store.list_runs() == ()


@pytest.mark.parametrize("adapter", ["memory", "sqlite"])
def test_execution_store_conformance_covers_atomic_task_transitions(
    adapter: Adapter,
    tmp_path: Path,
) -> None:
    store = _store(adapter, tmp_path)
    run = store.create_run(request(key=None))
    task = store.list_tasks(run.identifier)[0]

    transitioned = store.transition_task(
        task.identifier,
        expected=TaskState.READY,
        target=TaskState.LEASED,
    )

    assert transitioned.state is TaskState.LEASED
    assert store.get_task(task.identifier) == transitioned
    with pytest.raises(TaskStateConflictError):
        store.transition_task(
            task.identifier,
            expected=TaskState.READY,
            target=TaskState.LEASED,
        )
