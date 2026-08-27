from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from provium_pipeline.dispatch.creation import (
    DispatchCreationService,
    DispatchIdempotencyConflictError,
)
from provium_pipeline.dispatch.models import (
    CreateDispatchRequest,
    DependencyPolicy,
    TaskSelection,
)
from provium_pipeline.dispatch.store import InMemoryDispatchStore
from provium_pipeline.execution.attempts import RetryPolicy
from provium_pipeline.execution_store import InMemoryExecutionStore
from provium_pipeline.identifiers import DispatchId, RunId
from test.test_execution_store import request

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _request(run_identifier: RunId, *, key: str | None = None) -> CreateDispatchRequest:
    return CreateDispatchRequest(
        run_identifier=run_identifier,
        selection=TaskSelection(all=True),
        dependency_policy=DependencyPolicy.INCLUDE_MISSING_UPSTREAM,
        retry_policy=RetryPolicy(max_attempts=3),
        idempotency_key=key,
    )


def _service(
    identifier_factory: Callable[[], DispatchId] = DispatchId.new,
):
    executions = InMemoryExecutionStore(clock=lambda: NOW)
    run = executions.create_run(request())
    dispatches = InMemoryDispatchStore()
    service = DispatchCreationService(
        executions=executions,
        dispatches=dispatches,
        clock=lambda: NOW,
        identifier_factory=identifier_factory,
    )
    return service, dispatches, run


def test_dispatch_creation_plans_and_persists_run_tasks() -> None:
    service, dispatches, run = _service()

    dispatch = service.create(_request(run.identifier))

    assert dispatch.task_identifiers
    assert dispatches.get(dispatch.identifier) is dispatch
    assert dispatches.list_for_run(run.identifier) == (dispatch,)


def test_dispatch_creation_replays_and_rejects_run_scoped_idempotency() -> None:
    identifiers = iter(
        (
            DispatchId.parse("00000000-0000-0000-0000-000000000001"),
            DispatchId.parse("00000000-0000-0000-0000-000000000002"),
        )
    )
    service, dispatches, run = _service(identifiers.__next__)
    creation = _request(run.identifier, key="once")

    other = service.create(_request(run.identifier, key="other"))
    first = service.create(creation)
    assert service.create(creation) is first

    with pytest.raises(DispatchIdempotencyConflictError, match="conflicts"):
        service.create(replace(creation, selection=TaskSelection(nodes=("different",))))
    assert dispatches.list_for_run(run.identifier) == (other, first)


def test_dispatch_creation_serializes_concurrent_idempotent_calls() -> None:
    service, dispatches, run = _service()
    creation = _request(run.identifier, key="once")

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = tuple(executor.map(service.create, (creation,) * 8))

    assert all(dispatch is results[0] for dispatch in results)
    assert dispatches.list_for_run(run.identifier) == (results[0],)


def test_dispatch_creation_without_key_creates_distinct_dispatches() -> None:
    service, dispatches, run = _service()
    creation = _request(run.identifier)

    first = service.create(creation)
    second = service.create(creation)

    assert first.identifier != second.identifier
    assert len(dispatches.list_for_run(run.identifier)) == 2


def test_dispatch_creation_rejects_unknown_run() -> None:
    service, dispatches, _ = _service()
    missing = RunId.parse("00000000-0000-0000-0000-000000000099")

    with pytest.raises(KeyError):
        service.create(_request(missing))
    assert dispatches.list_for_run(missing) == ()
