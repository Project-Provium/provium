from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from provium_pipeline.sqlite_execution_store import (
    SCHEMA_VERSION,
    SQLiteExecutionStore,
    UnsupportedSchemaVersionError,
)

_EXPECTED_TABLES = {
    "schema_migrations",
    "runs",
    "tasks",
    "task_dependencies",
    "dispatches",
    "attempts",
    "computation_cache",
    "computation_reservations",
    "events",
}


def _tables(database: Path) -> set[str]:
    with sqlite3.connect(database) as connection:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }


def test_sqlite_store_bootstraps_forward_schema_and_local_pragmas(
    tmp_path: Path,
) -> None:
    database = tmp_path / "execution.sqlite3"

    store = SQLiteExecutionStore(database, busy_timeout_ms=2_500)

    assert _EXPECTED_TABLES <= _tables(database)
    assert store.schema_version == SCHEMA_VERSION
    settings = store.database_settings()
    assert settings.foreign_keys
    assert settings.busy_timeout_ms == 2_500
    assert settings.journal_mode in {"wal", "memory", "delete"}


def test_sqlite_store_reopens_existing_schema_without_duplicate_migration(
    tmp_path: Path,
) -> None:
    database = tmp_path / "execution.sqlite3"
    SQLiteExecutionStore(database)

    reopened = SQLiteExecutionStore(database)

    assert reopened.schema_version == SCHEMA_VERSION
    with sqlite3.connect(database) as connection:
        count = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()
    assert count == (SCHEMA_VERSION,)


def test_sqlite_store_rejects_database_from_newer_schema(tmp_path: Path) -> None:
    database = tmp_path / "execution.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY)"
        )
        connection.execute(
            "INSERT INTO schema_migrations(version) VALUES (?)",
            (SCHEMA_VERSION + 1,),
        )

    with pytest.raises(UnsupportedSchemaVersionError, match="newer"):
        SQLiteExecutionStore(database)


@pytest.mark.parametrize("busy_timeout_ms", [0, -1])
def test_sqlite_store_requires_positive_busy_timeout(
    tmp_path: Path,
    busy_timeout_ms: int,
) -> None:
    with pytest.raises(ValueError, match="positive"):
        SQLiteExecutionStore(
            tmp_path / "execution.sqlite3",
            busy_timeout_ms=busy_timeout_ms,
        )


def test_sqlite_store_persists_and_reopens_run_tasks_atomically(
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime

    from provium_pipeline.execution_store import InMemoryExecutionStore
    from test.test_execution_store import request

    memory = InMemoryExecutionStore(clock=lambda: datetime(2026, 8, 23, tzinfo=UTC))
    run = memory.create_run(request())
    tasks = memory.list_tasks(run.identifier)
    database = tmp_path / "execution.sqlite3"

    SQLiteExecutionStore(database).persist_run(run, tasks)
    reopened = SQLiteExecutionStore(database)

    assert reopened.get_run(run.identifier) == run
    assert reopened.list_runs() == (run,)
    assert reopened.list_tasks(run.identifier) == tasks
    assert reopened.get_task(tasks[0].identifier) == tasks[0]


def test_sqlite_store_transitions_tasks_with_compare_and_set(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from provium_pipeline.execution_store import InMemoryExecutionStore
    from provium_pipeline.run_models import TaskState
    from provium_pipeline.task_transitions import TaskStateConflictError
    from test.test_execution_store import request

    memory = InMemoryExecutionStore(clock=lambda: datetime(2026, 8, 23, tzinfo=UTC))
    run = memory.create_run(request())
    task = memory.list_tasks(run.identifier)[0]
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    store.persist_run(run, memory.list_tasks(run.identifier))

    transitioned = store.transition_task(
        task.identifier,
        expected=task.state,
        target=TaskState.LEASED,
    )
    assert transitioned.state is TaskState.LEASED
    with pytest.raises(TaskStateConflictError):
        store.transition_task(
            task.identifier,
            expected=task.state,
            target=TaskState.SUCCEEDED,
        )


def test_sqlite_store_rejects_mismatched_tasks_and_rolls_back_failures(
    tmp_path: Path,
) -> None:
    from dataclasses import replace
    from datetime import UTC, datetime
    from uuid import UUID

    from provium_pipeline.execution_store import InMemoryExecutionStore
    from provium_pipeline.identifiers import RunId
    from test.test_execution_store import request

    memory = InMemoryExecutionStore(clock=lambda: datetime(2026, 8, 23, tzinfo=UTC))
    run = memory.create_run(request())
    tasks = memory.list_tasks(run.identifier)
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    mismatched = replace(tasks[0], run_identifier=RunId(UUID(int=999)))

    with pytest.raises(ValueError, match="belong"):
        store.persist_run(run, (mismatched,))
    assert store.list_tasks(run.identifier) == ()

    store.persist_run(run, tasks)
    with pytest.raises(sqlite3.IntegrityError):
        store.persist_run(run, tasks)
    assert store.list_tasks(run.identifier) == tasks


def test_sqlite_store_reports_unknown_runs_and_lists_empty_database(
    tmp_path: Path,
) -> None:
    from uuid import uuid4

    from provium_pipeline.identifiers import RunId

    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")

    assert store.list_runs() == ()
    with pytest.raises(KeyError, match="unknown run identifier"):
        store.get_run(RunId(uuid4()))


def test_sqlite_store_rejects_unknown_task_operations(tmp_path: Path) -> None:
    from uuid import UUID

    from provium_pipeline.identifiers import TaskId
    from provium_pipeline.run_models import TaskState

    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    missing = TaskId(UUID(int=999))

    with pytest.raises(KeyError):
        store.get_task(missing)
    with pytest.raises(KeyError):
        store.transition_task(
            missing,
            expected=TaskState.READY,
            target=TaskState.LEASED,
        )
