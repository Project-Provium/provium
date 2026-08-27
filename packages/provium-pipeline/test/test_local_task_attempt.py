from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from provium_pipeline.execution.attempts import TaskAttemptLease
from provium_pipeline.identifiers import InputRecordKey, RunId, TaskId
from provium_pipeline.local_task_attempt import (
    BuiltTaskInvocation,
    InvocationOutput,
    InvocationOutputMismatchError,
    LocalTaskAttemptExecutor,
)
from provium_pipeline.run.models import PipelineTask, TaskState
from provium_pipeline.task_executor import (
    AttemptMaterializations,
    PreparedInvocation,
    PreparedProcedureKey,
)
from provium_pipeline.task_outputs import InMemoryTaskOutputStore

NOW = datetime(2026, 8, 26, tzinfo=UTC)


@dataclass(frozen=True)
class _OutputResult:
    path: Path
    produced: bool


@dataclass(frozen=True)
class _Result:
    output_results: Mapping[str, InvocationOutput]


class _Builder:
    def __init__(self, built: BuiltTaskInvocation) -> None:
        self.built = built
        self.calls: list[tuple[PipelineTask, TaskAttemptLease]] = []

    def build(
        self,
        task: PipelineTask,
        lease: TaskAttemptLease,
    ) -> BuiltTaskInvocation:
        self.calls.append((task, lease))
        return self.built


class _Importer:
    def __init__(self, identities: dict[Path, str]) -> None:
        self.identities = identities
        self.paths: list[Path] = []

    def import_artifact(self, path: Path) -> str:
        self.paths.append(path)
        return self.identities[path]


def _task(*outputs: str) -> PipelineTask:
    return PipelineTask(
        identifier=TaskId.new(),
        run_identifier=RunId.new(),
        node_identifier="decode",
        record_key=InputRecordKey("record-1"),
        procedure_identifier="example/decode@1",
        procedure_contract_digest="contract-digest",
        configuration_snapshot=None,
        setup_bindings=(),
        input_bindings=(),
        expected_output_fields=outputs,
        dependencies=(),
        state=TaskState.LEASED,
    )


def _lease(task: PipelineTask) -> TaskAttemptLease:
    return TaskAttemptLease(
        task_identifier=task.identifier,
        attempt_number=1,
        worker_identity="serial-1",
        token="token",
        started_at=NOW,
        heartbeat_at=NOW,
        expires_at=NOW + timedelta(seconds=30),
    )


def _built() -> BuiltTaskInvocation:
    return BuiltTaskInvocation(
        request=PreparedInvocation(
            key=PreparedProcedureKey("procedure", "configuration", "setup"),
            definition=object(),
            configuration_layers=(),
            setup_inputs=None,
            inputs=None,
            outputs=None,
            cancellation=None,
        ),
        materializations=AttemptMaterializations(),
    )


def test_local_task_attempt_publishes_produced_and_records_absent_outputs(
    tmp_path: Path,
) -> None:
    task = _task("image", "preview")
    lease = _lease(task)
    built = _built()
    builder = _Builder(built)
    image = tmp_path / "image.pa"
    preview = tmp_path / "preview.pa"
    importer = _Importer({image: "artifact-1"})
    outputs = InMemoryTaskOutputStore()
    invocations: list[PreparedInvocation] = []

    def invoke(request: PreparedInvocation, _: AttemptMaterializations) -> _Result:
        invocations.append(request)
        return _Result(
            {
                "image": _OutputResult(image, True),
                "preview": _OutputResult(preview, False),
            }
        )

    accepted = LocalTaskAttemptExecutor(
        builder=builder,
        invoke=invoke,
        importer=importer,
        outputs=outputs,
    ).execute(task, lease)

    assert builder.calls == [(task, lease)]
    assert invocations == [built.request]
    assert importer.paths == [image]
    assert dict(accepted.outputs) == {"image": "artifact-1", "preview": None}
    assert outputs.list_for_run(task.run_identifier) == (accepted,)


def test_local_task_attempt_rejects_result_field_mismatch() -> None:
    task = _task("image")
    lease = _lease(task)
    outputs = InMemoryTaskOutputStore()

    with pytest.raises(InvocationOutputMismatchError, match="frozen task contract"):
        LocalTaskAttemptExecutor(
            builder=_Builder(_built()),
            invoke=lambda _request, _materializations: _Result({}),
            importer=_Importer({}),
            outputs=outputs,
        ).execute(task, lease)

    assert outputs.list_for_run(task.run_identifier) == ()


def test_local_task_attempt_does_not_accept_partial_publication(tmp_path: Path) -> None:
    task = _task("first", "second")
    lease = _lease(task)
    first = tmp_path / "first.pa"
    second = tmp_path / "second.pa"
    importer = _Importer({first: "artifact-1"})
    outputs = InMemoryTaskOutputStore()

    def invoke(
        _: PreparedInvocation,
        __: AttemptMaterializations,
    ) -> _Result:
        return _Result(
            {
                "first": _OutputResult(first, True),
                "second": _OutputResult(second, True),
            }
        )

    with pytest.raises(KeyError):
        LocalTaskAttemptExecutor(
            builder=_Builder(_built()),
            invoke=invoke,
            importer=importer,
            outputs=outputs,
        ).execute(task, lease)

    assert importer.paths == [first, second]
    assert outputs.list_for_run(task.run_identifier) == ()
