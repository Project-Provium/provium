from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from provium_pipeline.attempts import RetryPolicy
from provium_pipeline.dispatch_models import (
    DependencyPolicy,
    Dispatch,
    DispatchState,
    TaskSelection,
)
from provium_pipeline.identifiers import DispatchId, RunId, TaskId
from provium_pipeline.sqlite_dispatch_store import SQLiteDispatchStore

RUN = RunId.parse("00000000-0000-0000-0000-000000000100")
OTHER_RUN = RunId.parse("00000000-0000-0000-0000-000000000200")


def _dispatch(
    number: int,
    *,
    run_identifier: RunId = RUN,
    key: str | None = None,
    selection: TaskSelection = TaskSelection(all=True),
) -> Dispatch:
    return Dispatch(
        identifier=DispatchId.parse(f"00000000-0000-0000-0000-{number:012d}"),
        run_identifier=run_identifier,
        selection=selection,
        task_identifiers=(
            TaskId.parse("00000000-0000-0000-0000-000000000300"),
        ),
        dependency_policy=DependencyPolicy.INCLUDE_MISSING_UPSTREAM,
        retry_policy=RetryPolicy(
            max_attempts=4,
            initial_backoff=timedelta(milliseconds=500),
        ),
        state=DispatchState.CREATED,
        created_at=datetime(2026, 1, 1, 0, 0, number, tzinfo=UTC),
        idempotency_key=key,
    )


def test_sqlite_dispatch_store_persists_reopens_and_lists_stably(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.sqlite3"
    store = SQLiteDispatchStore(database)
    later = _dispatch(2)
    earlier = _dispatch(1)
    other_run = _dispatch(3, run_identifier=OTHER_RUN)

    assert store.create(later) == later
    assert store.create(earlier) == earlier
    assert store.create(other_run) == other_run

    reopened = SQLiteDispatchStore(database)
    assert reopened.get(earlier.identifier) == earlier
    assert reopened.list_for_run(RUN) == (earlier, later)
    assert reopened.list_for_run(OTHER_RUN) == (other_run,)


def test_sqlite_dispatch_store_orders_different_offsets_by_absolute_time(
    tmp_path: Path,
) -> None:
    store = SQLiteDispatchStore(tmp_path / "state.sqlite3")
    first = replace(
        _dispatch(1),
        created_at=datetime.fromisoformat("2026-01-01T02:00:00+02:00"),
    )
    later = replace(
        _dispatch(2),
        created_at=datetime.fromisoformat("2025-12-31T20:00:00-05:00"),
    )

    store.create(later)
    store.create(first)

    assert store.list_for_run(RUN) == (first, later)


def test_sqlite_dispatch_store_replays_identity_and_rejects_redefinition(
    tmp_path: Path,
) -> None:
    store = SQLiteDispatchStore(tmp_path / "state.sqlite3")
    dispatch = _dispatch(1)

    assert store.create(dispatch) == dispatch
    assert store.create(dispatch) == dispatch
    with pytest.raises(ValueError, match="identifier"):
        store.create(replace(dispatch, selection=TaskSelection(nodes=("changed",))))


def test_sqlite_dispatch_store_enforces_run_scoped_idempotency(
    tmp_path: Path,
) -> None:
    store = SQLiteDispatchStore(tmp_path / "state.sqlite3")
    first = _dispatch(1, key="once")
    replay = replace(first, identifier=_dispatch(2).identifier)

    assert store.create(first) == first
    assert store.create(replay) == first
    with pytest.raises(ValueError, match="idempotency"):
        store.create(
            replace(
                replay,
                identifier=_dispatch(3).identifier,
                selection=TaskSelection(nodes=("changed",)),
            )
        )

    other_run = replace(
        replay,
        identifier=_dispatch(4).identifier,
        run_identifier=OTHER_RUN,
    )
    assert store.create(other_run) == other_run


def test_sqlite_dispatch_store_serializes_concurrent_idempotent_writers(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.sqlite3"
    first = _dispatch(1, key="once")
    second = replace(first, identifier=_dispatch(2).identifier)

    def create(value: Dispatch) -> Dispatch:
        return SQLiteDispatchStore(database).create(value)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(create, (first, second)))

    assert results[0] == results[1]
    assert SQLiteDispatchStore(database).list_for_run(RUN) == (results[0],)


def test_sqlite_dispatch_store_allows_multiple_keyless_dispatches(
    tmp_path: Path,
) -> None:
    store = SQLiteDispatchStore(tmp_path / "state.sqlite3")
    first = _dispatch(1)
    second = _dispatch(2)

    store.create(first)
    store.create(second)

    assert store.list_for_run(RUN) == (first, second)


def test_sqlite_dispatch_store_reports_unknown_and_empty_run(tmp_path: Path) -> None:
    store = SQLiteDispatchStore(tmp_path / "state.sqlite3")

    with pytest.raises(KeyError, match="unknown dispatch"):
        store.get(_dispatch(9).identifier)
    assert store.list_for_run(RUN) == ()
