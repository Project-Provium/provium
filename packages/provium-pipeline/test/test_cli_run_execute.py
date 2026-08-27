from __future__ import annotations

from argparse import Namespace
from datetime import UTC, datetime
from typing import cast

from provium_pipeline.cli import LocalCLIBackend, RunExecutor, RunLookup
from provium_pipeline.dispatch.codec import dispatch_document
from provium_pipeline.dispatch.models import (
    DependencyPolicy,
    Dispatch,
    DispatchState,
    TaskSelection,
)
from provium_pipeline.execution.attempts import RetryPolicy
from provium_pipeline.identifiers import DispatchId, RunId


class _RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[RunId, TaskSelection, DependencyPolicy]] = []
        self.dispatches: list[Dispatch] = []

    def execute(
        self,
        run_identifier: RunId,
        *,
        selection: TaskSelection,
        dependency_policy: DependencyPolicy,
    ) -> Dispatch:
        self.calls.append((run_identifier, selection, dependency_policy))
        dispatch = Dispatch(
            identifier=DispatchId.new(),
            run_identifier=run_identifier,
            selection=selection,
            task_identifiers=(),
            dependency_policy=dependency_policy,
            retry_policy=RetryPolicy(max_attempts=1),
            state=DispatchState.SUCCEEDED,
            created_at=datetime(2026, 8, 26, tzinfo=UTC),
            terminal_at=datetime(2026, 8, 26, tzinfo=UTC),
        )
        self.dispatches.append(dispatch)
        return dispatch


def test_local_cli_backend_executes_a_run_with_canonical_selection() -> None:
    run_identifier = RunId.new()
    executor = _RecordingExecutor()
    backend = LocalCLIBackend(
        cast(RunLookup, object()),
        run_executor=cast(RunExecutor, executor),
    )

    result = backend.execute(
        "run",
        "execute",
        Namespace(
            run_id=str(run_identifier),
            all=False,
            all_remaining=True,
            node=["transform", "transform"],
            procedure=["procedure-b", "procedure-a"],
            record=["record-b", "record-a"],
            label=["important", "important"],
            only_failed=True,
            include_missing_upstream=True,
        ),
    )

    selection = TaskSelection(
        all_remaining=True,
        labels=("important",),
        nodes=("transform",),
        only_failed=True,
        procedures=("procedure-a", "procedure-b"),
        records=("record-a", "record-b"),
    )
    assert executor.calls == [
        (
            run_identifier,
            selection,
            DependencyPolicy.INCLUDE_MISSING_UPSTREAM,
        )
    ]
    assert len(executor.dispatches) == 1
    assert result.exit_code == 0
    assert result.data == {
        "dispatch": dispatch_document(executor.dispatches[0])["dispatch"]
    }


def test_run_execute_all_includes_already_complete_tasks() -> None:
    run_identifier = RunId.new()
    executor = _RecordingExecutor()
    backend = LocalCLIBackend(
        cast(RunLookup, object()),
        run_executor=cast(RunExecutor, executor),
    )

    backend.execute(
        "run",
        "execute",
        Namespace(
            run_id=str(run_identifier),
            all=True,
            all_remaining=False,
            node=[],
            procedure=[],
            record=[],
            label=[],
            only_failed=False,
            include_missing_upstream=False,
        ),
    )

    assert executor.calls[0][1] == TaskSelection(all=True, only_incomplete=False)
    assert executor.calls[0][2] is DependencyPolicy.SELECTED_ONLY
