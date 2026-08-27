"""One-task local execution, publication, and acceptance orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..run.models import PipelineTask
from .attempts import TaskAttemptLease
from .outputs import TaskOutputSet
from .task_executor import AttemptMaterializations, PreparedInvocation


class InvocationOutputMismatchError(RuntimeError):
    """Invocation result fields differ from the frozen task contract."""


class InvocationOutput(Protocol):
    @property
    def path(self) -> Path: ...

    @property
    def produced(self) -> bool: ...


class InvocationResult(Protocol):
    @property
    def output_results(self) -> Mapping[str, InvocationOutput]: ...


@dataclass(frozen=True, slots=True)
class BuiltTaskInvocation:
    """A prepared request and its attempt-owned materializations."""

    request: PreparedInvocation
    materializations: AttemptMaterializations


class TaskInvocationBuilder(Protocol):
    def build(
        self,
        task: PipelineTask,
        lease: TaskAttemptLease,
    ) -> BuiltTaskInvocation: ...


class OutputImporter(Protocol):
    def import_artifact(self, path: Path) -> str: ...


class TaskOutputRecorder(Protocol):
    def record(self, outputs: TaskOutputSet) -> TaskOutputSet: ...


class LocalTaskAttemptExecutor:
    """Execute one built invocation and accept its fully published outputs."""

    def __init__(
        self,
        *,
        builder: TaskInvocationBuilder,
        invoke: Callable[
            [PreparedInvocation, AttemptMaterializations], InvocationResult
        ],
        importer: OutputImporter,
        outputs: TaskOutputRecorder,
    ) -> None:
        self._builder = builder
        self._invoke = invoke
        self._importer = importer
        self._outputs = outputs

    def execute(
        self,
        task: PipelineTask,
        lease: TaskAttemptLease,
    ) -> TaskOutputSet:
        built = self._builder.build(task, lease)
        result = self._invoke(built.request, built.materializations)
        if set(result.output_results) != set(task.expected_output_fields):
            raise InvocationOutputMismatchError(
                "invocation output fields do not match the frozen task contract"
            )
        accepted: dict[str, str | None] = {}
        for field in task.expected_output_fields:
            output = result.output_results[field]
            accepted[field] = (
                self._importer.import_artifact(output.path) if output.produced else None
            )
        return self._outputs.record(
            TaskOutputSet(
                task_identifier=task.identifier,
                run_identifier=task.run_identifier,
                record_key=task.record_key,
                node_identifier=task.node_identifier,
                outputs=accepted,
            )
        )


__all__ = [
    "BuiltTaskInvocation",
    "InvocationOutputMismatchError",
    "LocalTaskAttemptExecutor",
]
