from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from provium_pipeline.attempts import TaskAttemptLease
from provium_pipeline.dispatch_models import Dispatch, DispatchState
from provium_pipeline.dispatch_worker import SerialDispatchWorker
from provium_pipeline.identifiers import InputRecordKey, RunId, TaskId
from provium_pipeline.run_models import PipelineTask, TaskState
from test.test_dispatch_transitions import dispatch_for_state

NOW = datetime(2026, 8, 26, tzinfo=UTC)


def _task(
    *,
    state: TaskState = TaskState.READY,
    run_identifier: RunId | None = None,
    dependencies: tuple[TaskId, ...] = (),
) -> PipelineTask:
    return PipelineTask(
        identifier=TaskId.new(),
        run_identifier=run_identifier or RunId.new(),
        node_identifier="node",
        record_key=InputRecordKey("record"),
        procedure_identifier="procedure",
        procedure_contract_digest="contract",
        configuration_snapshot=None,
        setup_bindings=(),
        input_bindings=(),
        expected_output_fields=(),
        dependencies=dependencies,
        state=state,
    )


class _Executions:
    def __init__(self, *tasks: PipelineTask) -> None:
        self.tasks = {task.identifier: task for task in tasks}
        self.transitions: list[tuple[TaskId, TaskState, TaskState]] = []

    def get_task(self, identifier: TaskId) -> PipelineTask:
        return self.tasks[identifier]

    def transition_task(
        self,
        identifier: TaskId,
        *,
        expected: TaskState,
        target: TaskState,
    ) -> PipelineTask:
        task = self.tasks[identifier]
        assert task.state is expected
        transitioned = replace(task, state=target)
        self.tasks[identifier] = transitioned
        self.transitions.append((identifier, expected, target))
        return transitioned


class _LeaseConflictExecutions(_Executions):
    def transition_task(
        self,
        identifier: TaskId,
        *,
        expected: TaskState,
        target: TaskState,
    ) -> PipelineTask:
        if target is TaskState.LEASED:
            raise RuntimeError("lease transition conflict")
        return super().transition_task(
            identifier,
            expected=expected,
            target=target,
        )


class _Attempts:
    def __init__(self) -> None:
        self.claims: list[tuple[TaskId, TaskState, str, str, datetime, timedelta]] = []
        self.releases: list[tuple[TaskId, str, datetime]] = []

    def claim(
        self,
        task: TaskId,
        *,
        state: TaskState,
        worker_identity: str,
        token: str,
        now: datetime,
        ttl: timedelta,
    ) -> TaskAttemptLease:
        self.claims.append((task, state, worker_identity, token, now, ttl))
        return TaskAttemptLease(
            task_identifier=task,
            attempt_number=1,
            worker_identity=worker_identity,
            token=token,
            started_at=now,
            heartbeat_at=now,
            expires_at=now + ttl,
        )

    def release(
        self,
        task: TaskId,
        *,
        token: str,
        ended_at: datetime,
    ) -> TaskAttemptLease:
        self.releases.append((task, token, ended_at))
        return TaskAttemptLease(
            task_identifier=task,
            attempt_number=1,
            worker_identity="serial-1",
            token=token,
            started_at=NOW,
            heartbeat_at=NOW,
            expires_at=NOW + timedelta(seconds=30),
            ended_at=ended_at,
        )


def _dispatch(first: PipelineTask, *rest: PipelineTask) -> Dispatch:
    tasks = (first, *rest)
    return replace(
        dispatch_for_state(DispatchState.RUNNING),
        run_identifier=first.run_identifier,
        task_identifiers=tuple(task.identifier for task in tasks),
    )


def _succeed(_: PipelineTask, __: TaskAttemptLease) -> None:
    pass


def _worker(
    executions: _Executions,
    attempts: _Attempts,
    execute_task: Callable[[PipelineTask, TaskAttemptLease], None],
) -> SerialDispatchWorker:
    return SerialDispatchWorker(
        executions=executions,
        attempts=attempts,
        execute_task=execute_task,
        clock=lambda: NOW,
        token_factory=lambda: "token",
        worker_identity="serial-1",
        lease_ttl=timedelta(seconds=30),
        wait_for_work=lambda: pytest.fail("unexpected wait"),
    )


def test_serial_dispatch_worker_executes_only_selected_ready_task() -> None:
    selected = _task()
    unselected = _task()
    executions = _Executions(selected, unselected)
    attempts = _Attempts()
    observed: list[tuple[PipelineTask, TaskAttemptLease]] = []

    def observe(task: PipelineTask, lease: TaskAttemptLease) -> None:
        observed.append((task, lease))

    _worker(executions, attempts, observe).run(_dispatch(selected))

    assert [task.identifier for task, _ in observed] == [selected.identifier]
    assert executions.get_task(selected.identifier).state is TaskState.SUCCEEDED
    assert executions.get_task(unselected.identifier).state is TaskState.READY
    assert executions.transitions == [
        (selected.identifier, TaskState.READY, TaskState.LEASED),
        (selected.identifier, TaskState.LEASED, TaskState.SUCCEEDED),
    ]
    assert len(attempts.claims) == 1
    assert attempts.releases == [(selected.identifier, "token", NOW)]


def test_serial_dispatch_worker_records_failure_and_releases_lease() -> None:
    selected = _task()
    executions = _Executions(selected)
    attempts = _Attempts()

    def fail(_: PipelineTask, __: TaskAttemptLease) -> None:
        raise RuntimeError("task failed")

    with pytest.raises(RuntimeError, match="task failed"):
        _worker(executions, attempts, fail).run(_dispatch(selected))

    assert executions.get_task(selected.identifier).state is TaskState.FAILED
    assert attempts.releases == [(selected.identifier, "token", NOW)]


def test_serial_dispatch_worker_claims_retry_wait_task() -> None:
    selected = _task(state=TaskState.RETRY_WAIT)
    executions = _Executions(selected)
    attempts = _Attempts()

    _worker(executions, attempts, _succeed).run(_dispatch(selected))

    assert attempts.claims[0][1] is TaskState.RETRY_WAIT
    assert executions.get_task(selected.identifier).state is TaskState.SUCCEEDED


def test_serial_dispatch_worker_releases_lease_after_transition_conflict() -> None:
    selected = _task()
    executions = _LeaseConflictExecutions(selected)
    attempts = _Attempts()

    with pytest.raises(RuntimeError, match="lease transition conflict"):
        _worker(executions, attempts, _succeed).run(_dispatch(selected))

    assert attempts.releases == [(selected.identifier, "token", NOW)]
    assert executions.get_task(selected.identifier).state is TaskState.READY


def test_serial_dispatch_worker_waits_and_rescans_nonclaimable_tasks() -> None:
    dependency = _task()
    selected = _task(
        state=TaskState.BLOCKED,
        run_identifier=dependency.run_identifier,
        dependencies=(dependency.identifier,),
    )
    executions = _Executions(dependency, selected)
    attempts = _Attempts()
    waits: list[None] = []

    def make_ready() -> None:
        waits.append(None)
        executions.transition_task(
            dependency.identifier,
            expected=TaskState.READY,
            target=TaskState.SUCCEEDED,
        )

    SerialDispatchWorker(
        executions=executions,
        attempts=attempts,
        execute_task=_succeed,
        clock=lambda: NOW,
        token_factory=lambda: "token",
        worker_identity="serial-1",
        lease_ttl=timedelta(seconds=30),
        wait_for_work=make_ready,
    ).run(_dispatch(selected))

    assert waits == [None]
    assert executions.get_task(selected.identifier).state is TaskState.SUCCEEDED


def test_serial_dispatch_worker_promotes_and_executes_dependency_chain() -> None:
    upstream = _task()
    downstream = _task(
        state=TaskState.BLOCKED,
        run_identifier=upstream.run_identifier,
        dependencies=(upstream.identifier,),
    )
    executions = _Executions(upstream, downstream)
    attempts = _Attempts()
    observed: list[TaskId] = []

    def observe(task: PipelineTask, _: TaskAttemptLease) -> None:
        observed.append(task.identifier)

    _worker(executions, attempts, observe).run(_dispatch(upstream, downstream))

    assert observed == [upstream.identifier, downstream.identifier]
    assert (
        downstream.identifier,
        TaskState.BLOCKED,
        TaskState.READY,
    ) in executions.transitions
    assert executions.get_task(downstream.identifier).state is TaskState.SUCCEEDED


def test_serial_dispatch_worker_cancels_task_with_failed_dependency() -> None:
    upstream = _task(state=TaskState.FAILED)
    downstream = _task(
        state=TaskState.BLOCKED,
        run_identifier=upstream.run_identifier,
        dependencies=(upstream.identifier,),
    )
    executions = _Executions(upstream, downstream)
    waits: list[None] = []
    worker = SerialDispatchWorker(
        executions=executions,
        attempts=_Attempts(),
        execute_task=_succeed,
        clock=lambda: NOW,
        token_factory=lambda: "token",
        worker_identity="serial-1",
        lease_ttl=timedelta(seconds=30),
        wait_for_work=lambda: waits.append(None),
    )

    worker.run(_dispatch(upstream, downstream))

    assert executions.get_task(downstream.identifier).state is TaskState.CANCELLED
    assert waits == [None]


def test_serial_dispatch_worker_requires_positive_lease_ttl() -> None:
    with pytest.raises(ValueError, match="lease ttl must be positive"):
        SerialDispatchWorker(
            executions=_Executions(),
            attempts=_Attempts(),
            execute_task=_succeed,
            clock=lambda: NOW,
            token_factory=lambda: "token",
            worker_identity="serial-1",
            lease_ttl=timedelta(0),
            wait_for_work=lambda: None,
        )
