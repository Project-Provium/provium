from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from types import MappingProxyType

from provium_pipeline.dispatch.models import (
    CreateDispatchRequest,
    DependencyPolicy,
    Dispatch,
    DispatchState,
    TaskSelection,
)
from provium_pipeline.identifiers import DispatchId, TaskId
from provium_pipeline.run.models import PipelineTask


class UnsatisfiedDispatchDependencyError(ValueError):
    """Raised when selected-only dispatches omit a required upstream task."""


def plan_dispatch(
    request: CreateDispatchRequest,
    *,
    tasks: Sequence[PipelineTask],
    created_at: datetime,
    identifier_factory: Callable[[], DispatchId] = DispatchId.new,
    reusable_tasks: frozenset[TaskId] = frozenset(),
    node_labels: Mapping[str, frozenset[str]] = MappingProxyType({}),
) -> Dispatch:
    """Select and expand a deterministic immutable dispatch snapshot."""

    ordered = tuple(tasks)
    by_identifier = {task.identifier: task for task in ordered}
    selected = {
        task.identifier
        for task in ordered
        if _matches(task, selection=request.selection, node_labels=node_labels)
    }
    if request.dependency_policy is DependencyPolicy.SELECTED_ONLY:
        _validate_selected_only(
            selected,
            by_identifier=by_identifier,
            reusable_tasks=reusable_tasks,
        )
    else:
        _include_missing_upstream(
            selected,
            by_identifier=by_identifier,
            reusable_tasks=reusable_tasks,
        )
    selected_tasks = tuple(
        task.identifier for task in ordered if task.identifier in selected
    )
    complete = not selected_tasks or all(
        _is_satisfied(by_identifier[identifier], reusable_tasks)
        for identifier in selected_tasks
    )
    return Dispatch(
        identifier=identifier_factory(),
        run_identifier=request.run_identifier,
        selection=request.selection,
        task_identifiers=selected_tasks,
        dependency_policy=request.dependency_policy,
        retry_policy=request.retry_policy,
        state=DispatchState.SUCCEEDED if complete else DispatchState.CREATED,
        created_at=created_at,
        terminal_at=created_at if complete else None,
        idempotency_key=request.idempotency_key,
    )


def _matches(
    task: PipelineTask,
    *,
    selection: TaskSelection,
    node_labels: Mapping[str, frozenset[str]],
) -> bool:
    has_selector = bool(
        selection.all
        or selection.all_remaining
        or selection.labels
        or selection.nodes
        or selection.procedures
        or selection.records
    )
    if not has_selector:
        return False
    if selection.nodes and task.node_identifier not in selection.nodes:
        return False
    if selection.procedures and task.procedure_identifier not in selection.procedures:
        return False
    if selection.records and str(task.record_key) not in selection.records:
        return False
    if selection.labels and not (
        set(selection.labels) & node_labels.get(task.node_identifier, frozenset())
    ):
        return False
    if selection.only_failed and task.state.value != "failed":
        return False
    return not (
        selection.only_incomplete and task.state.value in {"succeeded", "cancelled"}
    )


def _validate_selected_only(
    selected: set[TaskId],
    *,
    by_identifier: Mapping[TaskId, PipelineTask],
    reusable_tasks: frozenset[TaskId],
) -> None:
    for identifier in tuple(selected):
        for dependency in by_identifier[identifier].dependencies:
            if dependency in selected or _is_satisfied(
                by_identifier[dependency], reusable_tasks
            ):
                continue
            raise UnsatisfiedDispatchDependencyError(
                f"required upstream task is outside the selection: {dependency}"
            )


def _include_missing_upstream(
    selected: set[TaskId],
    *,
    by_identifier: Mapping[TaskId, PipelineTask],
    reusable_tasks: frozenset[TaskId],
) -> None:
    pending = list(selected)
    while pending:
        identifier = pending.pop()
        for dependency in by_identifier[identifier].dependencies:
            if dependency in selected or _is_satisfied(
                by_identifier[dependency], reusable_tasks
            ):
                continue
            selected.add(dependency)
            pending.append(dependency)


def _is_satisfied(
    task: PipelineTask,
    reusable_tasks: frozenset[TaskId],
) -> bool:
    return task.identifier in reusable_tasks or task.state.value == "succeeded"


__all__ = ["UnsatisfiedDispatchDependencyError", "plan_dispatch"]
