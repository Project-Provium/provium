from dataclasses import replace
from datetime import UTC, datetime

import pytest

from provium_pipeline.dispatch.models import (
    CreateDispatchRequest,
    DependencyPolicy,
    DispatchState,
    TaskSelection,
)
from provium_pipeline.dispatch.planning import (
    UnsatisfiedDispatchDependencyError,
    plan_dispatch,
)
from provium_pipeline.execution.attempts import RetryPolicy
from provium_pipeline.execution_store import InMemoryExecutionStore
from provium_pipeline.identifiers import DispatchId, InputRecordKey, TaskId
from provium_pipeline.run.models import PipelineTask, TaskState
from test.test_execution_store import request

NOW = datetime(2026, 1, 1, tzinfo=UTC)
DISPATCH_ID = DispatchId.parse("00000000-0000-0000-0000-000000000099")


def _planned_tasks() -> tuple[PipelineTask, ...]:
    store = InMemoryExecutionStore(clock=lambda: NOW)
    run = store.create_run(request())
    return store.list_tasks(run.identifier)


def _dependency_tasks() -> tuple[PipelineTask, ...]:
    template = _planned_tasks()[0]
    upstream = replace(
        template,
        identifier=TaskId.parse("00000000-0000-0000-0000-000000000010"),
        node_identifier="upstream",
        record_key=InputRecordKey("record"),
        dependencies=(),
    )
    middle = replace(
        template,
        identifier=TaskId.parse("00000000-0000-0000-0000-000000000011"),
        node_identifier="middle",
        record_key=InputRecordKey("record"),
        dependencies=(upstream.identifier,),
    )
    target = replace(
        template,
        identifier=TaskId.parse("00000000-0000-0000-0000-000000000012"),
        node_identifier="target",
        record_key=InputRecordKey("record"),
        dependencies=(middle.identifier,),
    )
    return (upstream, middle, target)


def _plan(
    tasks: tuple[PipelineTask, ...],
    *,
    selection: TaskSelection,
    dependency_policy: DependencyPolicy,
    reusable_tasks: frozenset[TaskId] = frozenset(),
    node_labels: dict[str, frozenset[str]] | None = None,
):
    return plan_dispatch(
        CreateDispatchRequest(
            run_identifier=tasks[0].run_identifier,
            selection=selection,
            dependency_policy=dependency_policy,
            retry_policy=RetryPolicy(max_attempts=3),
        ),
        tasks=tasks,
        created_at=NOW,
        identifier_factory=lambda: DISPATCH_ID,
        reusable_tasks=reusable_tasks,
        node_labels=node_labels or {},
    )


def test_planner_expands_missing_upstream_recursively_in_task_order() -> None:
    tasks = _dependency_tasks()
    target = tasks[-1]

    dispatch = _plan(
        tasks,
        selection=TaskSelection(
            nodes=(target.node_identifier,),
            records=(str(target.record_key),),
        ),
        dependency_policy=DependencyPolicy.INCLUDE_MISSING_UPSTREAM,
    )

    by_identifier = {task.identifier: task for task in tasks}
    expected = {target.identifier}
    pending = list(target.dependencies)
    while pending:
        identifier = pending.pop()
        if identifier in expected:
            continue
        expected.add(identifier)
        pending.extend(by_identifier[identifier].dependencies)
    assert dispatch.task_identifiers == tuple(
        task.identifier for task in tasks if task.identifier in expected
    )
    assert dispatch.state is DispatchState.CREATED


def test_planner_rejects_unsatisfied_selected_only_dependency() -> None:
    tasks = _dependency_tasks()
    target = tasks[-1]
    selection = TaskSelection(
        nodes=(target.node_identifier,),
        records=(str(target.record_key),),
    )

    with pytest.raises(
        UnsatisfiedDispatchDependencyError,
        match="required upstream task is outside the selection",
    ):
        _plan(
            tasks,
            selection=selection,
            dependency_policy=DependencyPolicy.SELECTED_ONLY,
        )

    dispatch = _plan(
        tasks,
        selection=selection,
        dependency_policy=DependencyPolicy.SELECTED_ONLY,
        reusable_tasks=frozenset(target.dependencies),
    )
    assert dispatch.task_identifiers == (target.identifier,)


def test_planner_combines_filters_and_failed_state_deterministically() -> None:
    tasks = _planned_tasks()
    target = tasks[0]
    failed_tasks = tuple(
        replace(task, state=TaskState.FAILED)
        if task.identifier == target.identifier
        else task
        for task in tasks
    )

    dispatch = _plan(
        failed_tasks,
        selection=TaskSelection(
            labels=("chosen",),
            nodes=(target.node_identifier,),
            only_failed=True,
            procedures=(target.procedure_identifier,),
            records=(str(target.record_key),),
        ),
        dependency_policy=DependencyPolicy.INCLUDE_MISSING_UPSTREAM,
        node_labels={target.node_identifier: frozenset({"chosen", "other"})},
    )

    assert dispatch.task_identifiers == (target.identifier,)


@pytest.mark.parametrize(
    "selection",
    (
        TaskSelection(nodes=("missing",)),
        TaskSelection(procedures=("missing",)),
        TaskSelection(records=("missing",)),
        TaskSelection(labels=("missing",)),
        TaskSelection(all_remaining=True, only_failed=True),
    ),
)
def test_planner_excludes_each_nonmatching_filter(selection: TaskSelection) -> None:
    tasks = _planned_tasks()

    dispatch = _plan(
        tasks,
        selection=selection,
        dependency_policy=DependencyPolicy.INCLUDE_MISSING_UPSTREAM,
    )

    assert dispatch.task_identifiers == ()
    assert dispatch.state is DispatchState.SUCCEEDED


def test_planner_does_not_expand_selected_or_satisfied_upstream_tasks() -> None:
    tasks = _dependency_tasks()
    all_selected = _plan(
        tasks,
        selection=TaskSelection(all=True),
        dependency_policy=DependencyPolicy.INCLUDE_MISSING_UPSTREAM,
    )
    succeeded_middle = replace(tasks[1], state=TaskState.SUCCEEDED)
    satisfied = _plan(
        (tasks[0], succeeded_middle, tasks[2]),
        selection=TaskSelection(nodes=("target",)),
        dependency_policy=DependencyPolicy.INCLUDE_MISSING_UPSTREAM,
    )

    assert all_selected.task_identifiers == tuple(task.identifier for task in tasks)
    assert satisfied.task_identifiers == (tasks[2].identifier,)


def test_planner_immediately_succeeds_empty_and_complete_selections() -> None:
    tasks = _planned_tasks()
    empty = _plan(
        tasks,
        selection=TaskSelection(),
        dependency_policy=DependencyPolicy.INCLUDE_MISSING_UPSTREAM,
    )
    complete_tasks = tuple(replace(task, state=TaskState.SUCCEEDED) for task in tasks)
    complete = _plan(
        complete_tasks,
        selection=TaskSelection(all=True, only_incomplete=False),
        dependency_policy=DependencyPolicy.INCLUDE_MISSING_UPSTREAM,
    )

    assert empty.state is DispatchState.SUCCEEDED
    assert empty.task_identifiers == ()
    assert empty.terminal_at == NOW
    assert complete.state is DispatchState.SUCCEEDED
    assert complete.terminal_at == NOW
