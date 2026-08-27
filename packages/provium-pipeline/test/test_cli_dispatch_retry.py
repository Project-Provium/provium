from __future__ import annotations

import argparse
from datetime import UTC, datetime
from typing import Any, cast

from provium_pipeline.cli import LocalCLIBackend
from provium_pipeline.dispatch.models import (
    DependencyPolicy,
    Dispatch,
    DispatchState,
    RetryPolicy,
    TaskSelection,
)
from provium_pipeline.identifiers import DispatchId, RunId
from provium_pipeline.run.query import RunLookup


def test_dispatch_retry_executes_only_failed_tasks_from_original_run() -> None:
    run_identifier = RunId.new()
    original = _dispatch(
        run_identifier,
        state=DispatchState.FAILED,
        selection=TaskSelection(nodes=("normalize",), records=("record-1",)),
    )

    class Dispatches:
        def get(self, identifier: DispatchId) -> Dispatch:
            assert identifier == original.identifier
            return original

    class Executor:
        call: tuple[RunId, TaskSelection, DependencyPolicy] | None = None

        def execute(
            self,
            run_identifier: RunId,
            *,
            selection: TaskSelection,
            dependency_policy: DependencyPolicy,
        ) -> Dispatch:
            self.call = (run_identifier, selection, dependency_policy)
            return _dispatch(run_identifier, state=DispatchState.SUCCEEDED)

    executor = Executor()
    backend = LocalCLIBackend(
        cast(RunLookup, object()),
        dispatches=cast(Any, Dispatches()),
        run_executor=executor,
    )

    result = backend.execute(
        "dispatch",
        "retry",
        argparse.Namespace(dispatch_id=str(original.identifier)),
    )

    assert executor.call is not None
    retried_run, selection, dependency_policy = executor.call
    assert retried_run == run_identifier
    assert selection.only_failed
    assert selection.only_incomplete
    assert selection.nodes == ("normalize",)
    assert selection.records == ("record-1",)
    assert dependency_policy is DependencyPolicy.INCLUDE_MISSING_UPSTREAM
    dispatch = cast(dict[str, object], result.data["dispatch"])
    assert dispatch["state"] == "succeeded"


def _dispatch(
    run_identifier: RunId,
    *,
    state: DispatchState,
    selection: TaskSelection = TaskSelection(all=True),
) -> Dispatch:
    return Dispatch(
        identifier=DispatchId.new(),
        run_identifier=run_identifier,
        selection=selection,
        task_identifiers=(),
        dependency_policy=DependencyPolicy.INCLUDE_MISSING_UPSTREAM,
        retry_policy=RetryPolicy(max_attempts=3),
        state=state,
        created_at=datetime(2026, 8, 27, tzinfo=UTC),
        terminal_at=datetime(2026, 8, 27, tzinfo=UTC),
    )
