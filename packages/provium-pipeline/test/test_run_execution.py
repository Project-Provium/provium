from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest

from provium_pipeline.attempts import RetryPolicy
from provium_pipeline.dispatch_models import (
    CreateDispatchRequest,
    DependencyPolicy,
    Dispatch,
    DispatchState,
    TaskSelection,
)
from provium_pipeline.dispatch_store import InMemoryDispatchStore
from provium_pipeline.dispatch_transitions import DispatchStateConflictError
from provium_pipeline.identifiers import DispatchId, RunId
from provium_pipeline.run_execution import LocalRunExecutor
from provium_pipeline.run_models import RunState
from test.test_dispatch_transitions import dispatch_for_state


class _CancelBeforeFailureStore(InMemoryDispatchStore):
    def transition(
        self,
        identifier: DispatchId,
        *,
        expected: DispatchState,
        target: DispatchState,
    ) -> Dispatch:
        if target is DispatchState.FAILED:
            self.cancel(identifier)
        return super().transition(
            identifier,
            expected=expected,
            target=target,
        )


class _CancelBeforeSuccessStore(InMemoryDispatchStore):
    def transition(
        self,
        identifier: DispatchId,
        *,
        expected: DispatchState,
        target: DispatchState,
    ) -> Dispatch:
        if target is DispatchState.SUCCEEDED:
            self.cancel(identifier)
        return super().transition(
            identifier,
            expected=expected,
            target=target,
        )


class _NonterminalConflictStore(InMemoryDispatchStore):
    def __init__(self, conflict_target: DispatchState) -> None:
        super().__init__()
        self._conflict_target = conflict_target

    def transition(
        self,
        identifier: DispatchId,
        *,
        expected: DispatchState,
        target: DispatchState,
    ) -> Dispatch:
        if target is self._conflict_target:
            raise DispatchStateConflictError("injected nonterminal conflict")
        return super().transition(
            identifier,
            expected=expected,
            target=target,
        )


class _Runs:
    def __init__(self) -> None:
        self.state = RunState.PLANNED
        self.transitions: list[tuple[RunState, RunState]] = []

    def transition_run(
        self,
        identifier: RunId,
        *,
        expected: RunState,
        target: RunState,
    ) -> Any:
        assert self.state is expected
        self.state = target
        self.transitions.append((expected, target))
        return object()


class _Creator:
    def __init__(
        self,
        dispatches: InMemoryDispatchStore,
        dispatch: Dispatch,
    ) -> None:
        self._dispatches = dispatches
        self._dispatch = dispatch
        self.requests: list[CreateDispatchRequest] = []

    def create(self, request: CreateDispatchRequest) -> Dispatch:
        self.requests.append(request)
        return self._dispatches.create(self._dispatch)


def _executor(
    dispatch: Dispatch,
    *,
    run_dispatch: Callable[[Dispatch], None],
    retry_policy: RetryPolicy,
) -> tuple[LocalRunExecutor, InMemoryDispatchStore, _Creator]:
    dispatches = InMemoryDispatchStore()
    creator = _Creator(dispatches, dispatch)
    return (
        LocalRunExecutor(
            creator=creator,
            dispatches=dispatches,
            run_dispatch=run_dispatch,
            retry_policy=retry_policy,
        ),
        dispatches,
        creator,
    )


def test_local_run_executor_creates_runs_and_completes_a_dispatch() -> None:
    dispatch = dispatch_for_state(DispatchState.CREATED)
    retry_policy = RetryPolicy(
        max_attempts=3,
        initial_backoff=timedelta(milliseconds=50),
    )
    observed: list[Dispatch] = []
    executor, dispatches, creator = _executor(
        dispatch,
        run_dispatch=observed.append,
        retry_policy=retry_policy,
    )
    selection = TaskSelection(all_remaining=True)

    completed = executor.execute(
        dispatch.run_identifier,
        selection=selection,
        dependency_policy=DependencyPolicy.INCLUDE_MISSING_UPSTREAM,
    )

    assert creator.requests == [
        CreateDispatchRequest(
            run_identifier=dispatch.run_identifier,
            selection=selection,
            dependency_policy=DependencyPolicy.INCLUDE_MISSING_UPSTREAM,
            retry_policy=retry_policy,
        )
    ]
    assert [value.state for value in observed] == [DispatchState.RUNNING]
    assert completed.state is DispatchState.SUCCEEDED
    assert dispatches.get(dispatch.identifier) == completed


def test_local_run_executor_transitions_the_durable_run_to_success() -> None:
    dispatch = dispatch_for_state(DispatchState.CREATED)
    dispatches = InMemoryDispatchStore()
    runs = _Runs()
    executor = LocalRunExecutor(
        creator=_Creator(dispatches, dispatch),
        dispatches=dispatches,
        runs=runs,
        run_dispatch=lambda _: None,
        retry_policy=dispatch.retry_policy,
    )

    executor.execute(
        dispatch.run_identifier,
        selection=dispatch.selection,
        dependency_policy=dispatch.dependency_policy,
    )

    assert runs.transitions == [
        (RunState.PLANNED, RunState.RUNNING),
        (RunState.RUNNING, RunState.SUCCEEDED),
    ]


def test_local_run_executor_skips_an_already_terminal_dispatch() -> None:
    dispatch = dispatch_for_state(DispatchState.SUCCEEDED)
    dispatches = InMemoryDispatchStore()
    runs = _Runs()
    executor = LocalRunExecutor(
        creator=_Creator(dispatches, dispatch),
        dispatches=dispatches,
        runs=runs,
        run_dispatch=lambda value: pytest.fail(f"unexpected dispatch: {value}"),
        retry_policy=RetryPolicy(max_attempts=1),
    )

    completed = executor.execute(
        dispatch.run_identifier,
        selection=dispatch.selection,
        dependency_policy=dispatch.dependency_policy,
    )

    assert completed is dispatch
    assert runs.state is RunState.SUCCEEDED


def test_local_run_executor_preserves_cancellation_during_execution() -> None:
    dispatch = dispatch_for_state(DispatchState.CREATED)
    dispatches = InMemoryDispatchStore()
    creator = _Creator(dispatches, dispatch)

    def cancel(running: Dispatch) -> None:
        dispatches.cancel(running.identifier)

    executor = LocalRunExecutor(
        creator=creator,
        dispatches=dispatches,
        run_dispatch=cancel,
        retry_policy=dispatch.retry_policy,
    )

    completed = executor.execute(
        dispatch.run_identifier,
        selection=dispatch.selection,
        dependency_policy=dispatch.dependency_policy,
    )

    assert completed.state is DispatchState.CANCELLED


def test_local_run_executor_records_failure_and_reraises() -> None:
    dispatch = dispatch_for_state(DispatchState.CREATED)

    def fail(_: Dispatch) -> None:
        raise RuntimeError("runner failed")

    executor, dispatches, _ = _executor(
        dispatch,
        run_dispatch=fail,
        retry_policy=dispatch.retry_policy,
    )

    with pytest.raises(RuntimeError, match="runner failed"):
        executor.execute(
            dispatch.run_identifier,
            selection=dispatch.selection,
            dependency_policy=dispatch.dependency_policy,
        )

    assert dispatches.get(dispatch.identifier).state is DispatchState.FAILED


def test_local_run_executor_preserves_original_error_when_cancellation_wins() -> None:
    dispatch = dispatch_for_state(DispatchState.CREATED)
    dispatches = _CancelBeforeFailureStore()
    creator = _Creator(dispatches, dispatch)

    def fail(_: Dispatch) -> None:
        raise RuntimeError("original runner failure")

    executor = LocalRunExecutor(
        creator=creator,
        dispatches=dispatches,
        run_dispatch=fail,
        retry_policy=dispatch.retry_policy,
    )

    with pytest.raises(RuntimeError, match="original runner failure"):
        executor.execute(
            dispatch.run_identifier,
            selection=dispatch.selection,
            dependency_policy=dispatch.dependency_policy,
        )

    assert dispatches.get(dispatch.identifier).state is DispatchState.CANCELLED


def test_failure_record_preserves_terminal_cancellation() -> None:
    dispatch = dispatch_for_state(DispatchState.CREATED)
    dispatches = InMemoryDispatchStore()
    runs = _Runs()

    def cancel_and_fail(running: Dispatch) -> None:
        dispatches.cancel(running.identifier)
        raise RuntimeError("runner failed after cancellation")

    executor = LocalRunExecutor(
        creator=_Creator(dispatches, dispatch),
        dispatches=dispatches,
        runs=runs,
        run_dispatch=cancel_and_fail,
        retry_policy=dispatch.retry_policy,
    )

    with pytest.raises(RuntimeError, match="runner failed after cancellation"):
        executor.execute(
            dispatch.run_identifier,
            selection=dispatch.selection,
            dependency_policy=dispatch.dependency_policy,
        )

    assert dispatches.get(dispatch.identifier).state is DispatchState.CANCELLED
    assert runs.state is RunState.CANCELLED


def test_local_run_executor_surfaces_nonterminal_failure_conflict() -> None:
    dispatch = dispatch_for_state(DispatchState.CREATED)
    dispatches = _NonterminalConflictStore(DispatchState.FAILED)

    def fail(_: Dispatch) -> None:
        raise RuntimeError("runner failed")

    executor = LocalRunExecutor(
        creator=_Creator(dispatches, dispatch),
        dispatches=dispatches,
        run_dispatch=fail,
        retry_policy=dispatch.retry_policy,
    )

    with pytest.raises(DispatchStateConflictError, match="injected"):
        executor.execute(
            dispatch.run_identifier,
            selection=dispatch.selection,
            dependency_policy=dispatch.dependency_policy,
        )

    assert dispatches.get(dispatch.identifier).state is DispatchState.RUNNING


def test_local_run_executor_surfaces_nonterminal_success_conflict() -> None:
    dispatch = dispatch_for_state(DispatchState.CREATED)
    dispatches = _NonterminalConflictStore(DispatchState.SUCCEEDED)
    executor = LocalRunExecutor(
        creator=_Creator(dispatches, dispatch),
        dispatches=dispatches,
        run_dispatch=lambda _: None,
        retry_policy=dispatch.retry_policy,
    )

    with pytest.raises(DispatchStateConflictError, match="injected"):
        executor.execute(
            dispatch.run_identifier,
            selection=dispatch.selection,
            dependency_policy=dispatch.dependency_policy,
        )

    assert dispatches.get(dispatch.identifier).state is DispatchState.RUNNING


def test_local_run_executor_returns_cancellation_that_wins_after_success() -> None:
    dispatch = dispatch_for_state(DispatchState.CREATED)
    dispatches = _CancelBeforeSuccessStore()
    runs = _Runs()
    executor = LocalRunExecutor(
        creator=_Creator(dispatches, dispatch),
        dispatches=dispatches,
        runs=runs,
        run_dispatch=lambda _: None,
        retry_policy=dispatch.retry_policy,
    )

    completed = executor.execute(
        dispatch.run_identifier,
        selection=dispatch.selection,
        dependency_policy=dispatch.dependency_policy,
    )

    assert completed.state is DispatchState.CANCELLED
