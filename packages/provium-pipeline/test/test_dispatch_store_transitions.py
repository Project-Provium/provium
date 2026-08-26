from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from provium_pipeline.dispatch_models import DispatchState
from provium_pipeline.dispatch_store import InMemoryDispatchStore
from provium_pipeline.dispatch_transitions import (
    DispatchStateConflictError,
    InvalidDispatchTransitionError,
)
from provium_pipeline.identifiers import DispatchId
from provium_pipeline.sqlite_dispatch_store import SQLiteDispatchStore
from test.test_dispatch_transitions import dispatch_for_state

NOW = datetime(2026, 2, 1, tzinfo=UTC)


def test_in_memory_dispatch_store_transitions_atomically() -> None:
    store = InMemoryDispatchStore(clock=lambda: NOW)
    dispatch = store.create(dispatch_for_state(DispatchState.CREATED))

    running = store.transition(
        dispatch.identifier,
        expected=DispatchState.CREATED,
        target=DispatchState.RUNNING,
    )
    succeeded = store.transition(
        dispatch.identifier,
        expected=DispatchState.RUNNING,
        target=DispatchState.SUCCEEDED,
    )

    assert running.terminal_at is None
    assert succeeded.terminal_at == NOW
    assert store.get(dispatch.identifier) == succeeded
    assert store.transition(
        dispatch.identifier,
        expected=DispatchState.SUCCEEDED,
        target=DispatchState.SUCCEEDED,
    ) is succeeded


def test_in_memory_dispatch_store_preserves_value_after_transition_failures() -> None:
    store = InMemoryDispatchStore(clock=lambda: NOW)
    dispatch = store.create(dispatch_for_state(DispatchState.CREATED))

    with pytest.raises(DispatchStateConflictError):
        store.transition(
            dispatch.identifier,
            expected=DispatchState.RUNNING,
            target=DispatchState.SUCCEEDED,
        )
    with pytest.raises(InvalidDispatchTransitionError):
        store.transition(
            dispatch.identifier,
            expected=DispatchState.CREATED,
            target=DispatchState.FAILED,
        )
    assert store.get(dispatch.identifier) is dispatch


def test_sqlite_dispatch_store_transitions_and_reopens(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    store = SQLiteDispatchStore(database, clock=lambda: NOW)
    dispatch = store.create(dispatch_for_state(DispatchState.CREATED))

    running = store.transition(
        dispatch.identifier,
        expected=DispatchState.CREATED,
        target=DispatchState.RUNNING,
    )
    cancelled = store.transition(
        dispatch.identifier,
        expected=DispatchState.RUNNING,
        target=DispatchState.CANCELLED,
    )

    assert running.terminal_at is None
    assert cancelled.terminal_at == NOW
    assert SQLiteDispatchStore(database).get(dispatch.identifier) == cancelled


def test_sqlite_dispatch_store_rolls_back_transition_failures(
    tmp_path: Path,
) -> None:
    store = SQLiteDispatchStore(tmp_path / "state.sqlite3", clock=lambda: NOW)
    dispatch = store.create(dispatch_for_state(DispatchState.CREATED))

    with pytest.raises(DispatchStateConflictError):
        store.transition(
            dispatch.identifier,
            expected=DispatchState.RUNNING,
            target=DispatchState.SUCCEEDED,
        )
    with pytest.raises(InvalidDispatchTransitionError):
        store.transition(
            dispatch.identifier,
            expected=DispatchState.CREATED,
            target=DispatchState.FAILED,
        )
    assert store.get(dispatch.identifier) == dispatch


def test_sqlite_dispatch_store_allows_one_competing_transition(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.sqlite3"
    dispatch = SQLiteDispatchStore(database).create(
        dispatch_for_state(DispatchState.CREATED)
    )

    def transition(target: DispatchState) -> DispatchState | type[Exception]:
        try:
            return SQLiteDispatchStore(database, clock=lambda: NOW).transition(
                dispatch.identifier,
                expected=DispatchState.CREATED,
                target=target,
            ).state
        except Exception as error:
            return type(error)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(
                transition,
                (DispatchState.RUNNING, DispatchState.CANCELLED),
            )
        )

    assert results.count(DispatchStateConflictError) == 1
    assert SQLiteDispatchStore(database).get(dispatch.identifier).state in (
        DispatchState.RUNNING,
        DispatchState.CANCELLED,
    )


@pytest.mark.parametrize(
    "store",
    (
        InMemoryDispatchStore(clock=lambda: NOW),
        pytest.param(None, id="sqlite"),
    ),
)
def test_dispatch_stores_reject_unknown_transition(
    store: InMemoryDispatchStore | None,
    tmp_path: Path,
) -> None:
    active_store = store or SQLiteDispatchStore(
        tmp_path / "state.sqlite3", clock=lambda: NOW
    )
    missing = DispatchId.parse("00000000-0000-0000-0000-000000000099")

    with pytest.raises(KeyError):
        active_store.transition(
            missing,
            expected=DispatchState.CREATED,
            target=DispatchState.RUNNING,
        )
    with pytest.raises(KeyError):
        active_store.cancel(missing)


@pytest.mark.parametrize(
    "state",
    (DispatchState.CREATED, DispatchState.RUNNING),
)
def test_in_memory_dispatch_store_cancels_active_dispatches(
    state: DispatchState,
) -> None:
    store = InMemoryDispatchStore(clock=lambda: NOW)
    dispatch = store.create(dispatch_for_state(state))

    cancelled = store.cancel(dispatch.identifier)

    assert cancelled.state is DispatchState.CANCELLED
    assert cancelled.terminal_at == NOW
    assert store.get(dispatch.identifier) is cancelled


def test_in_memory_dispatch_store_cancellation_is_idempotent() -> None:
    store = InMemoryDispatchStore(clock=lambda: NOW)
    dispatch = store.create(dispatch_for_state(DispatchState.CANCELLED))

    assert store.cancel(dispatch.identifier) is dispatch
    assert dispatch.terminal_at != NOW


@pytest.mark.parametrize(
    "state",
    (DispatchState.SUCCEEDED, DispatchState.FAILED),
)
def test_in_memory_dispatch_store_rejects_completed_cancellation(
    state: DispatchState,
) -> None:
    store = InMemoryDispatchStore(clock=lambda: NOW)
    dispatch = store.create(dispatch_for_state(state))

    with pytest.raises(InvalidDispatchTransitionError):
        store.cancel(dispatch.identifier)
    assert store.get(dispatch.identifier) is dispatch


@pytest.mark.parametrize(
    "state",
    (DispatchState.CREATED, DispatchState.RUNNING),
)
def test_sqlite_dispatch_store_cancels_active_dispatches_durably(
    tmp_path: Path,
    state: DispatchState,
) -> None:
    database = tmp_path / "state.sqlite3"
    store = SQLiteDispatchStore(database, clock=lambda: NOW)
    dispatch = store.create(dispatch_for_state(state))

    cancelled = store.cancel(dispatch.identifier)

    assert cancelled.state is DispatchState.CANCELLED
    assert cancelled.terminal_at == NOW
    assert SQLiteDispatchStore(database).get(dispatch.identifier) == cancelled


def test_sqlite_dispatch_store_cancellation_is_idempotent(tmp_path: Path) -> None:
    store = SQLiteDispatchStore(tmp_path / "state.sqlite3", clock=lambda: NOW)
    dispatch = store.create(dispatch_for_state(DispatchState.CANCELLED))

    assert store.cancel(dispatch.identifier) == dispatch
    assert store.get(dispatch.identifier).terminal_at != NOW


@pytest.mark.parametrize(
    "state",
    (DispatchState.SUCCEEDED, DispatchState.FAILED),
)
def test_sqlite_dispatch_store_rejects_completed_cancellation(
    tmp_path: Path,
    state: DispatchState,
) -> None:
    store = SQLiteDispatchStore(tmp_path / "state.sqlite3", clock=lambda: NOW)
    dispatch = store.create(dispatch_for_state(state))

    with pytest.raises(InvalidDispatchTransitionError):
        store.cancel(dispatch.identifier)
    assert store.get(dispatch.identifier) == dispatch


def test_sqlite_dispatch_store_serializes_concurrent_cancellation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.sqlite3"
    dispatch = SQLiteDispatchStore(database).create(
        dispatch_for_state(DispatchState.RUNNING)
    )

    def cancel(_: int):
        return SQLiteDispatchStore(database, clock=lambda: NOW).cancel(
            dispatch.identifier
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(cancel, range(2)))

    assert results[0] == results[1]
    assert results[0].terminal_at == NOW
