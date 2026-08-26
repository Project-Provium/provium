from __future__ import annotations

from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from threading import Thread
from time import sleep
from typing import cast

from provium_pipeline.attempts import RetryPolicy
from provium_pipeline.cli import LocalCLIBackend, RunLookup
from provium_pipeline.dispatch_codec import dispatch_document
from provium_pipeline.dispatch_models import (
    DependencyPolicy,
    Dispatch,
    DispatchState,
    TaskSelection,
)
from provium_pipeline.dispatch_store import InMemoryDispatchStore
from provium_pipeline.identifiers import DispatchId, RunId
from provium_pipeline.sqlite_dispatch_store import SQLiteDispatchStore


def _dispatch(*, state: DispatchState) -> Dispatch:
    now = datetime(2026, 8, 26, tzinfo=UTC)
    return Dispatch(
        identifier=DispatchId.new(),
        run_identifier=RunId.new(),
        selection=TaskSelection(all_remaining=True),
        task_identifiers=(),
        dependency_policy=DependencyPolicy.INCLUDE_MISSING_UPSTREAM,
        retry_policy=RetryPolicy(max_attempts=1),
        state=state,
        created_at=now,
        terminal_at=now if state.terminal else None,
    )


def _backend(dispatches: InMemoryDispatchStore) -> LocalCLIBackend:
    return LocalCLIBackend(cast(RunLookup, object()), dispatches=dispatches)


def test_dispatch_wait_returns_an_already_terminal_dispatch() -> None:
    dispatches = InMemoryDispatchStore()
    dispatch = dispatches.create(_dispatch(state=DispatchState.SUCCEEDED))

    result = _backend(dispatches).execute(
        "dispatch",
        "wait",
        Namespace(dispatch_id=str(dispatch.identifier)),
    )

    assert result.exit_code == 0
    assert result.data == {"dispatch": dispatch_document(dispatch)["dispatch"]}


def test_dispatch_wait_polls_until_the_dispatch_becomes_terminal() -> None:
    dispatches = InMemoryDispatchStore()
    dispatch = dispatches.create(_dispatch(state=DispatchState.CREATED))

    def complete_dispatch() -> None:
        sleep(0.01)
        dispatches.transition(
            dispatch.identifier,
            expected=DispatchState.CREATED,
            target=DispatchState.RUNNING,
        )
        dispatches.transition(
            dispatch.identifier,
            expected=DispatchState.RUNNING,
            target=DispatchState.SUCCEEDED,
        )

    worker = Thread(target=complete_dispatch)
    worker.start()
    try:
        result = _backend(dispatches).execute(
            "dispatch",
            "wait",
            Namespace(dispatch_id=str(dispatch.identifier)),
        )
    finally:
        worker.join()

    completed = dispatches.get(dispatch.identifier)
    assert completed is not None
    assert result.exit_code == 0
    assert result.data == {"dispatch": dispatch_document(completed)["dispatch"]}


def test_dispatch_wait_observes_a_transition_from_another_sqlite_store(
    tmp_path: Path,
) -> None:
    database = tmp_path / "pipeline.db"
    reader = SQLiteDispatchStore(database)
    writer = SQLiteDispatchStore(database)
    dispatch = reader.create(_dispatch(state=DispatchState.CREATED))

    def complete_dispatch() -> None:
        sleep(0.01)
        writer.transition(
            dispatch.identifier,
            expected=DispatchState.CREATED,
            target=DispatchState.RUNNING,
        )
        writer.transition(
            dispatch.identifier,
            expected=DispatchState.RUNNING,
            target=DispatchState.SUCCEEDED,
        )

    worker = Thread(target=complete_dispatch)
    worker.start()
    try:
        result = LocalCLIBackend(
            cast(RunLookup, object()),
            dispatches=reader,
        ).execute(
            "dispatch",
            "wait",
            Namespace(dispatch_id=str(dispatch.identifier)),
        )
    finally:
        worker.join()

    completed = reader.get(dispatch.identifier)
    assert completed.state is DispatchState.SUCCEEDED
    assert result.exit_code == 0
    assert result.data == {"dispatch": dispatch_document(completed)["dispatch"]}
