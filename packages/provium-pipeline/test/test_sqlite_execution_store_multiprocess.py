from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from multiprocessing import get_context
from pathlib import Path

from provium_pipeline.identifiers import TaskId
from provium_pipeline.run_models import TaskState
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
