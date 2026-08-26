from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from provium_pipeline.attempts import RetryPolicy
from provium_pipeline.dispatch_models import (
    DependencyPolicy,
    Dispatch,
    DispatchState,
    TaskSelection,
)
from provium_pipeline.dispatch_store import (
    DispatchRedefinitionError,
    InMemoryDispatchStore,
)
from provium_pipeline.identifiers import DispatchId, RunId, TaskId

NOW = datetime(2026, 1, 1, tzinfo=UTC)
RUN_ID = RunId.parse("00000000-0000-0000-0000-000000000001")
OTHER_RUN_ID = RunId.parse("00000000-0000-0000-0000-000000000002")


def _dispatch(identifier: str, *, run_identifier: RunId = RUN_ID) -> Dispatch:
    return Dispatch(
        identifier=DispatchId.parse(identifier),
        run_identifier=run_identifier,
        selection=TaskSelection(all=True),
        task_identifiers=(
            TaskId.parse("00000000-0000-0000-0000-000000000010"),
        ),
        dependency_policy=DependencyPolicy.INCLUDE_MISSING_UPSTREAM,
        retry_policy=RetryPolicy(max_attempts=3),
        state=DispatchState.CREATED,
        created_at=NOW,
    )


def test_in_memory_dispatch_store_creates_gets_and_lists_stably() -> None:
    store = InMemoryDispatchStore()
    later = _dispatch("00000000-0000-0000-0000-000000000020")
    earlier = _dispatch("00000000-0000-0000-0000-000000000019")
    other = _dispatch(
        "00000000-0000-0000-0000-000000000018",
        run_identifier=OTHER_RUN_ID,
    )

    assert store.create(later) is later
    assert store.create(earlier) is earlier
    assert store.create(other) is other
    assert store.get(later.identifier) is later
    assert store.list_for_run(RUN_ID) == (earlier, later)
    assert store.list_for_run(OTHER_RUN_ID) == (other,)


def test_in_memory_dispatch_store_replays_and_rejects_redefinition() -> None:
    store = InMemoryDispatchStore()
    dispatch = _dispatch("00000000-0000-0000-0000-000000000020")
    store.create(dispatch)

    assert store.create(dispatch) is dispatch
    with pytest.raises(DispatchRedefinitionError, match="already identifies"):
        store.create(replace(dispatch, selection=TaskSelection(nodes=("other",))))


def test_in_memory_dispatch_store_serializes_concurrent_redefinition() -> None:
    store = InMemoryDispatchStore()
    dispatch = _dispatch("00000000-0000-0000-0000-000000000020")
    changed = replace(dispatch, selection=TaskSelection(nodes=("other",)))

    def create(value: Dispatch) -> Dispatch | DispatchRedefinitionError:
        try:
            return store.create(value)
        except DispatchRedefinitionError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(create, (dispatch, changed)))

    assert sum(isinstance(value, Dispatch) for value in results) == 1
    assert sum(isinstance(value, DispatchRedefinitionError) for value in results) == 1
    assert store.get(dispatch.identifier) in {dispatch, changed}


def test_in_memory_dispatch_store_reports_unknown_identifier_and_empty_run() -> None:
    store = InMemoryDispatchStore()
    missing = DispatchId.parse("00000000-0000-0000-0000-000000000099")

    with pytest.raises(KeyError, match="unknown dispatch identifier"):
        store.get(missing)
    assert store.list_for_run(RUN_ID) == ()
