from __future__ import annotations

from datetime import UTC, datetime
from typing import Never

import pytest

from provium_pipeline import CreateRunRequest
from provium_pipeline.execution_store import (
    InMemoryExecutionStore,
    RunIdempotencyConflictError,
)
from test.compiler.test_compiler_success import compiler, definition
from test.test_run_models import input_snapshot

NOW = datetime(2026, 8, 23, tzinfo=UTC)


def request(
    identity: str = "source-1", *, key: str | None = "same"
) -> CreateRunRequest:
    return CreateRunRequest(
        pipeline=compiler().compile(definition()),
        inputs=input_snapshot(identity),
        idempotency_namespace="test" if key is not None else None,
        idempotency_key=key,
    )


def test_in_memory_store_creates_complete_run_and_tasks_atomically() -> None:
    store = InMemoryExecutionStore(clock=lambda: NOW)

    run = store.create_run(request())

    assert store.get_run(run.identifier) == run
    assert len(store.list_tasks(run.identifier)) == 1
    assert store.list_runs() == (run,)


def test_run_creation_is_scoped_and_idempotent() -> None:
    store = InMemoryExecutionStore(clock=lambda: NOW)
    first = store.create_run(request())

    assert store.create_run(request()) == first
    with pytest.raises(RunIdempotencyConflictError, match="test/same"):
        store.create_run(request("different"))

    assert store.create_run(request(key=None)).identifier != first.identifier


def test_planner_failure_leaves_store_unchanged() -> None:
    def fail(
        request: CreateRunRequest,
        *,
        now: datetime,
    ) -> Never:
        del request, now
        raise RuntimeError("planning failed")

    store = InMemoryExecutionStore(clock=lambda: NOW, planner=fail)

    with pytest.raises(RuntimeError, match="planning failed"):
        store.create_run(request())

    assert store.list_runs() == ()
