from datetime import UTC, datetime
from pathlib import Path

import pytest

from provium_pipeline.execution_store import InMemoryExecutionStore
from provium_pipeline.run_models import RunState
from provium_pipeline.run_transitions import (
    InvalidRunTransitionError,
    RunStateConflictError,
)
from provium_pipeline.sqlite_execution_store import SQLiteExecutionStore
from test.test_execution_store import request


def _store() -> InMemoryExecutionStore:
    return InMemoryExecutionStore(
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_in_memory_store_persists_compare_and_set_run_transition() -> None:
    store = _store()
    run = store.create_run(request())

    running = store.transition_run(
        run.identifier,
        expected=RunState.PLANNED,
        target=RunState.RUNNING,
    )

    assert running.state is RunState.RUNNING
    assert store.get_run(run.identifier) == running
    assert (
        store.transition_run(
            run.identifier,
            expected=RunState.RUNNING,
            target=RunState.RUNNING,
        )
        is running
    )


def test_in_memory_store_rejects_conflicting_and_illegal_run_transitions() -> None:
    store = _store()
    run = store.create_run(request())

    with pytest.raises(RunStateConflictError, match="expected running, found planned"):
        store.transition_run(
            run.identifier,
            expected=RunState.RUNNING,
            target=RunState.CANCELLED,
        )

    with pytest.raises(InvalidRunTransitionError, match="planned -> succeeded"):
        store.transition_run(
            run.identifier,
            expected=RunState.PLANNED,
            target=RunState.SUCCEEDED,
        )


def test_in_memory_store_rejects_unknown_run_identifier() -> None:
    store = _store()

    with pytest.raises(KeyError):
        store.transition_run(
            "run-missing",  # type: ignore[arg-type]
            expected=RunState.PLANNED,
            target=RunState.CANCELLED,
        )


def test_sqlite_store_persists_compare_and_set_run_transition(
    tmp_path: Path,
) -> None:
    database = tmp_path / "pipeline.sqlite3"
    store = SQLiteExecutionStore(database)
    run = store.create_run(request())

    transitioned = store.transition_run(
        run.identifier,
        expected=RunState.PLANNED,
        target=RunState.RUNNING,
    )

    assert transitioned.state is RunState.RUNNING
    assert SQLiteExecutionStore(database).get_run(run.identifier) == transitioned

    with pytest.raises(RunStateConflictError, match="expected planned, found running"):
        store.transition_run(
            run.identifier,
            expected=RunState.PLANNED,
            target=RunState.CANCELLED,
        )
    assert store.get_run(run.identifier) == transitioned


def test_sqlite_store_rejects_unknown_run_identifier(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "pipeline.sqlite3")

    with pytest.raises(KeyError, match="unknown run identifier"):
        store.transition_run(
            "run-missing",  # type: ignore[arg-type]
            expected=RunState.PLANNED,
            target=RunState.CANCELLED,
        )
