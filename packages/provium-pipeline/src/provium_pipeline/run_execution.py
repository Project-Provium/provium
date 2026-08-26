"""Local orchestration for executing a selected run dispatch."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .attempts import RetryPolicy
from .dispatch_models import (
    CreateDispatchRequest,
    DependencyPolicy,
    Dispatch,
    DispatchState,
    TaskSelection,
)
from .dispatch_store import DispatchStore
from .dispatch_transitions import DispatchStateConflictError
from .identifiers import DispatchId, RunId


class DispatchCreator(Protocol):
    """Create and persist a dispatch from a canonical request."""

    def create(self, request: CreateDispatchRequest) -> Dispatch: ...


class LocalRunExecutor:
    """Create a dispatch and drive its local runner lifecycle."""

    def __init__(
        self,
        *,
        creator: DispatchCreator,
        dispatches: DispatchStore,
        run_dispatch: Callable[[Dispatch], None],
        retry_policy: RetryPolicy,
    ) -> None:
        self._creator = creator
        self._dispatches = dispatches
        self._run_dispatch = run_dispatch
        self._retry_policy = retry_policy

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
        if dispatch.state.terminal:
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
            raise
        return self._record_success(dispatch.identifier)

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
