from collections.abc import Callable, Mapping
from datetime import datetime
from threading import RLock
from typing import Protocol

from provium_pipeline.dispatch_models import CreateDispatchRequest, Dispatch
from provium_pipeline.dispatch_planner import plan_dispatch
from provium_pipeline.dispatch_store import DispatchStore
from provium_pipeline.identifiers import DispatchId, RunId, TaskId
from provium_pipeline.run_models import PipelineTask


class DispatchIdempotencyConflictError(ValueError):
    """Raised when a run-scoped key is reused for different dispatch intent."""


class ExecutionTaskLookup(Protocol):
    def get_run(self, identifier: RunId) -> object: ...

    def list_tasks(self, run_identifier: RunId) -> tuple[PipelineTask, ...]: ...


def _no_reusable_tasks(_: RunId) -> frozenset[TaskId]:
    return frozenset()


def _no_node_labels(_: RunId) -> Mapping[str, frozenset[str]]:
    return {}


class DispatchCreationService:
    """Plan and persist dispatches with run-scoped idempotency."""

    def __init__(
        self,
        *,
        executions: ExecutionTaskLookup,
        dispatches: DispatchStore,
        clock: Callable[[], datetime],
        identifier_factory: Callable[[], DispatchId] = DispatchId.new,
        reusable_tasks: Callable[[RunId], frozenset[TaskId]] = _no_reusable_tasks,
        node_labels: Callable[
            [RunId], Mapping[str, frozenset[str]]
        ] = _no_node_labels,
    ) -> None:
        self._executions = executions
        self._dispatches = dispatches
        self._clock = clock
        self._identifier_factory = identifier_factory
        self._reusable_tasks = reusable_tasks
        self._node_labels = node_labels
        self._lock = RLock()

    def create(self, request: CreateDispatchRequest) -> Dispatch:
        with self._lock:
            self._executions.get_run(request.run_identifier)
            existing = self._idempotent_dispatch(request)
            if existing is not None:
                return existing
            dispatch = plan_dispatch(
                request,
                tasks=self._executions.list_tasks(request.run_identifier),
                created_at=self._clock(),
                identifier_factory=self._identifier_factory,
                reusable_tasks=self._reusable_tasks(request.run_identifier),
                node_labels=self._node_labels(request.run_identifier),
            )
            return self._dispatches.create(dispatch)

    def _idempotent_dispatch(
        self,
        request: CreateDispatchRequest,
    ) -> Dispatch | None:
        if request.idempotency_key is None:
            return None
        for dispatch in self._dispatches.list_for_run(request.run_identifier):
            if dispatch.idempotency_key != request.idempotency_key:
                continue
            if not _matches_request(dispatch, request):
                raise DispatchIdempotencyConflictError(
                    f"dispatch idempotency key conflicts: "
                    f"{request.run_identifier}/{request.idempotency_key}"
                )
            return dispatch
        return None


def _matches_request(
    dispatch: Dispatch,
    request: CreateDispatchRequest,
) -> bool:
    return (
        dispatch.run_identifier == request.run_identifier
        and dispatch.selection == request.selection
        and dispatch.dependency_policy is request.dependency_policy
        and dispatch.retry_policy == request.retry_policy
        and dispatch.idempotency_key == request.idempotency_key
    )


__all__ = [
    "DispatchCreationService",
    "DispatchIdempotencyConflictError",
    "ExecutionTaskLookup",
]
