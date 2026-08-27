from __future__ import annotations

import os
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from multiprocessing import get_context
from pathlib import Path

from provium_pipeline.identifiers import TaskId
from provium_pipeline.run.models import TaskState
from provium_pipeline.sqlite_execution_store import SQLiteExecutionStore
from provium_pipeline.task_transitions import TaskStateConflictError
from test.test_execution_store import request


def _lease_task(database: str, identifier: str) -> str:
    store = SQLiteExecutionStore(Path(database))
    try:
        store.transition_task(
            TaskId.parse(identifier),
            expected=TaskState.READY,
            target=TaskState.LEASED,
        )
    except TaskStateConflictError:
        return "conflict"
    return "leased"


def _lease_available_tasks(
    database: str,
    identifiers: tuple[str, ...],
) -> tuple[int, tuple[str, ...]]:
    time.sleep(0.05)
    leased: list[str] = []
    for identifier in identifiers:
        if _lease_task(database, identifier) == "leased":
            leased.append(identifier)
    return os.getpid(), tuple(leased)


def test_four_spawn_workers_accept_each_sqlite_task_once(tmp_path: Path) -> None:
    database = tmp_path / "four-workers.sqlite3"
    store = SQLiteExecutionStore(database)
    identifiers: list[str] = []
    for _index in range(16):
        run = store.create_run(request(key=None))
        identifiers.extend(
            str(task.identifier) for task in store.list_tasks(run.identifier)
        )
    frozen_identifiers = tuple(identifiers)

    with ProcessPoolExecutor(
        max_workers=4,
        mp_context=get_context("spawn"),
    ) as executor:
        outcomes = tuple(
            executor.map(
                _lease_available_tasks,
                (str(database),) * 4,
                (frozen_identifiers,) * 4,
            )
        )

    assert len({pid for pid, _accepted in outcomes}) == 4
    accepted = [identifier for _pid, task_ids in outcomes for identifier in task_ids]
    assert sorted(accepted) == sorted(frozen_identifiers)
    assert len(accepted) == len(set(accepted))


def test_sqlite_task_claim_is_atomic_across_processes(tmp_path: Path) -> None:
    database = tmp_path / "execution.sqlite3"
    store = SQLiteExecutionStore(
        database,
        clock=lambda: datetime(2026, 8, 23, tzinfo=UTC),
    )
    run = store.create_run(request(key=None))
    task = store.list_tasks(run.identifier)[0]

    with ProcessPoolExecutor(
        max_workers=2,
        mp_context=get_context("spawn"),
    ) as executor:
        outcomes = tuple(
            executor.map(
                _lease_task,
                (str(database), str(database)),
                (str(task.identifier), str(task.identifier)),
            )
        )

    assert sorted(outcomes) == ["conflict", "leased"]
    assert store.get_task(task.identifier).state is TaskState.LEASED
