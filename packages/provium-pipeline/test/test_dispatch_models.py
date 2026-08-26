from datetime import UTC, datetime

import pytest

from provium_pipeline.attempts import RetryPolicy
from provium_pipeline.dispatch_models import (
    DependencyPolicy,
    Dispatch,
    DispatchState,
    TaskSelection,
)
from provium_pipeline.identifiers import DispatchId, RunId, TaskId

NOW = datetime(2026, 1, 1, tzinfo=UTC)
DISPATCH_ID = DispatchId.parse("00000000-0000-0000-0000-000000000001")
RUN_ID = RunId.parse("00000000-0000-0000-0000-000000000002")
TASK_A = TaskId.parse("00000000-0000-0000-0000-000000000003")
TASK_B = TaskId.parse("00000000-0000-0000-0000-000000000004")


def test_task_selection_is_immutable_canonical_and_matches_cli_filters() -> None:
    selection = TaskSelection(
        nodes=("b", "a", "a"),
        procedures=("procedure-b", "procedure-a"),
        labels=("nightly", "nightly"),
        records=("record-b", "record-a"),
        only_failed=True,
        only_incomplete=True,
    )

    assert selection.nodes == ("a", "b")
    assert selection.procedures == ("procedure-a", "procedure-b")
    assert selection.labels == ("nightly",)
    assert selection.records == ("record-a", "record-b")
    assert selection.only_failed is True
    assert selection.only_incomplete is True

    with pytest.raises(AttributeError):
        selection.nodes = ()  # type: ignore[misc]


def test_task_selection_rejects_conflicting_full_run_modes() -> None:
    with pytest.raises(
        ValueError,
        match="all and all_remaining are mutually exclusive",
    ):
        TaskSelection(all=True, all_remaining=True)


def test_dispatch_freezes_expanded_tasks_and_lifecycle_policy() -> None:
    dispatch = Dispatch(
        identifier=DISPATCH_ID,
        run_identifier=RUN_ID,
        selection=TaskSelection(nodes=("node",)),
        task_identifiers=(TASK_B, TASK_A, TASK_A),
        dependency_policy=DependencyPolicy.INCLUDE_MISSING_UPSTREAM,
        retry_policy=RetryPolicy(max_attempts=3),
        state=DispatchState.CREATED,
        created_at=NOW,
        idempotency_key="dispatch-once",
    )

    assert dispatch.task_identifiers == (TASK_A, TASK_B)
    assert dispatch.terminal_at is None
    assert dispatch.idempotency_key == "dispatch-once"


def test_dispatch_requires_consistent_aware_terminal_state() -> None:
    with pytest.raises(
        ValueError,
        match="terminal dispatch requires terminal_at",
    ):
        Dispatch(
            identifier=DISPATCH_ID,
            run_identifier=RUN_ID,
            selection=TaskSelection(all=True),
            task_identifiers=(),
            dependency_policy=DependencyPolicy.SELECTED_ONLY,
            retry_policy=RetryPolicy(max_attempts=1),
            state=DispatchState.SUCCEEDED,
            created_at=NOW,
        )
    with pytest.raises(
        ValueError,
        match="non-terminal dispatch cannot have terminal_at",
    ):
        Dispatch(
            identifier=DISPATCH_ID,
            run_identifier=RUN_ID,
            selection=TaskSelection(all=True),
            task_identifiers=(),
            dependency_policy=DependencyPolicy.SELECTED_ONLY,
            retry_policy=RetryPolicy(max_attempts=1),
            state=DispatchState.RUNNING,
            created_at=NOW,
            terminal_at=NOW,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        Dispatch(
            identifier=DISPATCH_ID,
            run_identifier=RUN_ID,
            selection=TaskSelection(all=True),
            task_identifiers=(),
            dependency_policy=DependencyPolicy.SELECTED_ONLY,
            retry_policy=RetryPolicy(max_attempts=1),
            state=DispatchState.CREATED,
            created_at=datetime(2026, 1, 1),
        )
    with pytest.raises(ValueError, match="terminal_at must be timezone-aware"):
        Dispatch(
            identifier=DISPATCH_ID,
            run_identifier=RUN_ID,
            selection=TaskSelection(all=True),
            task_identifiers=(),
            dependency_policy=DependencyPolicy.SELECTED_ONLY,
            retry_policy=RetryPolicy(max_attempts=1),
            state=DispatchState.SUCCEEDED,
            created_at=NOW,
            terminal_at=datetime(2026, 1, 1),
        )
    with pytest.raises(ValueError, match="idempotency_key cannot be empty"):
        Dispatch(
            identifier=DISPATCH_ID,
            run_identifier=RUN_ID,
            selection=TaskSelection(all=True),
            task_identifiers=(),
            dependency_policy=DependencyPolicy.SELECTED_ONLY,
            retry_policy=RetryPolicy(max_attempts=1),
            state=DispatchState.CREATED,
            created_at=NOW,
            idempotency_key="",
        )
