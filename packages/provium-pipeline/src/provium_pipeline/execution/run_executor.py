"""Local orchestration for executing a selected run dispatch."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ..dispatch.models import (
    CreateDispatchRequest,
    DependencyPolicy,
    Dispatch,
    DispatchState,
    TaskSelection,
)
from ..dispatch.store import DispatchStore
from ..dispatch.transitions import DispatchStateConflictError
from ..identifiers import DispatchId, RunId
from ..run.models import PipelineRun, RunState
from .attempts import RetryPolicy


class DispatchCreator(Protocol):
    """Create and persist a dispatch from a canonical request."""

    def create(self, request: CreateDispatchRequest) -> Dispatch: ...


class RunTransitions(Protocol):
    def transition_run(
        self,
        identifier: RunId,
        *,
        expected: RunState,
        target: RunState,
    ) -> PipelineRun: ...


class LocalRunExecutor:
    """Create a dispatch and drive its local runner lifecycle."""

    def __init__(
        self,
        *,
        creator: DispatchCreator,
        dispatches: DispatchStore,
        run_dispatch: Callable[[Dispatch], None],
        retry_policy: RetryPolicy,
        runs: RunTransitions | None = None,
    ) -> None:
        self._creator = creator
        self._dispatches = dispatches
        self._run_dispatch = run_dispatch
        self._retry_policy = retry_policy
        self._runs = runs

    def execute(
        self,
        run_identifier: RunId,
        *,
        selection: TaskSelection,
        dependency_policy: DependencyPolicy,
    ) -> Dispatch:
        dispatch = self._creator.create(
            CreateDispatchRequest(
                run_identifier=run_identifier,
                selection=selection,
                dependency_policy=dependency_policy,
                retry_policy=self._retry_policy,
            )
        )
        self._transition_run(
            run_identifier,
            expected=RunState.PLANNED,
            target=RunState.RUNNING,
        )
        if dispatch.state.terminal:
            self._transition_run(
                run_identifier,
                expected=RunState.RUNNING,
                target=self._terminal_run_state(dispatch.state),
            )
            return dispatch
        running = self._dispatches.transition(
            dispatch.identifier,
            expected=DispatchState.CREATED,
            target=DispatchState.RUNNING,
        )
        try:
            self._run_dispatch(running)
        except Exception:
            self._record_failure(dispatch.identifier)
            failed = self._dispatches.get(dispatch.identifier)
            self._transition_run(
                run_identifier,
                expected=RunState.RUNNING,
                target=self._terminal_run_state(failed.state),
            )
            raise
        completed = self._record_success(dispatch.identifier)
        self._transition_run(
            run_identifier,
            expected=RunState.RUNNING,
            target=self._terminal_run_state(completed.state),
        )
        return completed

    def _transition_run(
        self,
        identifier: RunId,
        *,
        expected: RunState,
        target: RunState,
    ) -> None:
        if self._runs is not None:
            self._runs.transition_run(identifier, expected=expected, target=target)

    @staticmethod
    def _terminal_run_state(state: DispatchState) -> RunState:
        if state is DispatchState.SUCCEEDED:
            return RunState.SUCCEEDED
        if state is DispatchState.CANCELLED:
            return RunState.CANCELLED
        return RunState.FAILED

    def _record_failure(self, identifier: DispatchId) -> None:
        current = self._dispatches.get(identifier)
        if current.state.terminal:
            return
        try:
            self._dispatches.transition(
                identifier,
                expected=DispatchState.RUNNING,
                target=DispatchState.FAILED,
            )
        except DispatchStateConflictError:
            if not self._dispatches.get(identifier).state.terminal:
                raise

    def _record_success(self, identifier: DispatchId) -> Dispatch:
        current = self._dispatches.get(identifier)
        if current.state.terminal:
            return current
        try:
            return self._dispatches.transition(
                identifier,
                expected=DispatchState.RUNNING,
                target=DispatchState.SUCCEEDED,
            )
        except DispatchStateConflictError:
            current = self._dispatches.get(identifier)
            if current.state.terminal:
                return current
            raise
