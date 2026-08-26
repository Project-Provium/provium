from datetime import UTC, datetime

import pytest

from provium_pipeline.attempts import RetryPolicy
from provium_pipeline.dispatch_models import (
    DependencyPolicy,
    Dispatch,
    DispatchState,
    TaskSelection,
)
from provium_pipeline.dispatch_transitions import (
    DispatchStateConflictError,
    InvalidDispatchTransitionError,
    apply_dispatch_transition,
)
from provium_pipeline.identifiers import DispatchId, RunId

NOW = datetime(2026, 1, 2, tzinfo=UTC)


def _dispatch(state: DispatchState) -> Dispatch:
    return Dispatch(
        identifier=DispatchId.parse("00000000-0000-0000-0000-000000000001"),
        run_identifier=RunId.parse("00000000-0000-0000-0000-000000000002"),
        selection=TaskSelection(all=True),
        task_identifiers=(),
        dependency_policy=DependencyPolicy.INCLUDE_MISSING_UPSTREAM,
        retry_policy=RetryPolicy(max_attempts=1),
        state=state,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        terminal_at=NOW if state.terminal else None,
    )


@pytest.mark.parametrize(
    ("expected", "target"),
    (
        (DispatchState.CREATED, DispatchState.RUNNING),
        (DispatchState.CREATED, DispatchState.CANCELLED),
        (DispatchState.RUNNING, DispatchState.SUCCEEDED),
        (DispatchState.RUNNING, DispatchState.FAILED),
        (DispatchState.RUNNING, DispatchState.CANCELLED),
    ),
)
def test_dispatch_transition_applies_each_legal_edge(
    expected: DispatchState,
    target: DispatchState,
) -> None:
    transitioned = apply_dispatch_transition(
        _dispatch(expected),
        expected=expected,
        target=target,
        transitioned_at=NOW,
    )

    assert transitioned.state is target
    assert transitioned.terminal_at == (NOW if target.terminal else None)


@pytest.mark.parametrize("state", tuple(DispatchState))
def test_dispatch_transition_replays_same_state(state: DispatchState) -> None:
    dispatch = _dispatch(state)

    assert apply_dispatch_transition(
        dispatch,
        expected=state,
        target=state,
        transitioned_at=NOW,
    ) is dispatch


def test_dispatch_transition_rejects_stale_expected_state() -> None:
    with pytest.raises(DispatchStateConflictError, match="expected running"):
        apply_dispatch_transition(
            _dispatch(DispatchState.CREATED),
            expected=DispatchState.RUNNING,
            target=DispatchState.SUCCEEDED,
            transitioned_at=NOW,
        )


@pytest.mark.parametrize(
    ("expected", "target"),
    (
        (DispatchState.CREATED, DispatchState.SUCCEEDED),
        (DispatchState.CREATED, DispatchState.FAILED),
        (DispatchState.RUNNING, DispatchState.CREATED),
        (DispatchState.SUCCEEDED, DispatchState.RUNNING),
        (DispatchState.FAILED, DispatchState.RUNNING),
        (DispatchState.CANCELLED, DispatchState.RUNNING),
    ),
)
def test_dispatch_transition_rejects_illegal_edges(
    expected: DispatchState,
    target: DispatchState,
) -> None:
    with pytest.raises(InvalidDispatchTransitionError, match="invalid dispatch"):
        apply_dispatch_transition(
            _dispatch(expected),
            expected=expected,
            target=target,
            transitioned_at=NOW,
        )


def test_dispatch_transition_requires_aware_terminal_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        apply_dispatch_transition(
            _dispatch(DispatchState.RUNNING),
            expected=DispatchState.RUNNING,
            target=DispatchState.SUCCEEDED,
            transitioned_at=datetime(2026, 1, 2),
        )
