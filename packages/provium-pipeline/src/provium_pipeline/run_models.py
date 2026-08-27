from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

from provium import ConfigurationSnapshot, JsonValue, canonical_digest

from .compiler import CompiledBindingPlan, CompiledPipeline
from .identifiers import InputRecordKey, RunId, TaskId
from .input.models import RunInputSnapshot


class TaskState(StrEnum):
    """Durable lifecycle state for one planned pipeline task."""

    BLOCKED = "blocked"
    READY = "ready"
    WAITING_ON_COMPUTATION = "waiting-on-computation"
    LEASED = "leased"
    RETRY_WAIT = "retry-wait"
    SUCCEEDED = "succeeded"
    REUSED = "reused"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def satisfied(self) -> bool:
        return self in {TaskState.SUCCEEDED, TaskState.REUSED}


def run_fingerprint(
    pipeline: CompiledPipeline,
    inputs: RunInputSnapshot,
) -> str:
    """Return the stable identity of an intended run."""
    return canonical_digest(
        {
            "pipeline": pipeline.semantic_digest,
            "configuration": pipeline.resolved_configuration.document_digest,
            "inputs": inputs.digest,
        }
    )


class RunState(StrEnum):
    """Durable lifecycle state for one pipeline run."""

    PLANNED = "planned"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class RunOutputExpectation:
    record_key: InputRecordKey
    name: str
    node: str
    field: str


@dataclass(frozen=True, slots=True, init=False)
class CreateRunRequest:
    pipeline: CompiledPipeline
    inputs: RunInputSnapshot
    idempotency_namespace: str | None
    idempotency_key: str | None
    metadata: Mapping[str, JsonValue]

    def __init__(
        self,
        *,
        pipeline: CompiledPipeline,
        inputs: RunInputSnapshot,
        idempotency_namespace: str | None = None,
        idempotency_key: str | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> None:
        object.__setattr__(self, "pipeline", pipeline)
        object.__setattr__(self, "inputs", inputs)
        object.__setattr__(self, "idempotency_namespace", idempotency_namespace)
        object.__setattr__(self, "idempotency_key", idempotency_key)
        object.__setattr__(
            self, "metadata", MappingProxyType(dict(metadata or {}))
        )

    @property
    def digest(self) -> str:
        return canonical_digest(
            {
                "pipeline": self.pipeline.definition_digest,
                "configuration": (
                    self.pipeline.resolved_configuration.document_digest
                ),
                "inputs": self.inputs.digest,
                "metadata": dict(self.metadata),
            }
        )


@dataclass(frozen=True, slots=True)
class PipelineRun:
    identifier: RunId
    idempotency_namespace: str | None
    idempotency_key: str | None
    request_digest: str
    pipeline: CompiledPipeline
    inputs: RunInputSnapshot
    fingerprint: str
    created_at: datetime
    metadata: Mapping[str, JsonValue]
    state: RunState
    expected_outputs: tuple[RunOutputExpectation, ...]


@dataclass(frozen=True, slots=True)
class PipelineTask:
    identifier: TaskId
    run_identifier: RunId
    node_identifier: str
    record_key: InputRecordKey
    procedure_identifier: str
    procedure_contract_digest: str
    configuration_snapshot: ConfigurationSnapshot | None
    setup_bindings: tuple[CompiledBindingPlan, ...]
    input_bindings: tuple[CompiledBindingPlan, ...]
    expected_output_fields: tuple[str, ...]
    dependencies: tuple[TaskId, ...]
    state: TaskState


@dataclass(frozen=True, slots=True)
class RunPlan:
    run: PipelineRun
    tasks: tuple[PipelineTask, ...]


def plan_run(
    request: CreateRunRequest,
    *,
    now: datetime,
    run_id_factory: Callable[[], RunId] = RunId.new,
    task_id_factory: Callable[[], TaskId] = TaskId.new,
) -> RunPlan:
    """Expand one compiled pipeline into a complete immutable run plan."""
    run_id = run_id_factory()
    task_ids = {
        (node.identifier, record.key): task_id_factory()
        for node in request.pipeline.nodes
        for record in request.inputs.records
    }
    tasks: list[PipelineTask] = []
    for node in request.pipeline.nodes:
        upstream_nodes = _upstream_nodes(node.setup_bindings + node.input_bindings)
        for record in request.inputs.records:
            dependencies = tuple(
                task_ids[(upstream, record.key)] for upstream in upstream_nodes
            )
            tasks.append(
                PipelineTask(
                    identifier=task_ids[(node.identifier, record.key)],
                    run_identifier=run_id,
                    node_identifier=node.identifier,
                    record_key=record.key,
                    procedure_identifier=node.procedure_identifier,
                    procedure_contract_digest=node.procedure_contract_digest,
                    configuration_snapshot=node.configuration_snapshot,
                    setup_bindings=node.setup_bindings,
                    input_bindings=node.input_bindings,
                    expected_output_fields=tuple(
                        output.field for output in node.output_contracts
                    ),
                    dependencies=dependencies,
                    state=(TaskState.BLOCKED if dependencies else TaskState.READY),
                )
            )
    expected_outputs = tuple(
        RunOutputExpectation(record.key, output.name, output.node, output.field)
        for record in request.inputs.records
        for output in request.pipeline.outputs
    )
    run = PipelineRun(
        identifier=run_id,
        idempotency_namespace=request.idempotency_namespace,
        idempotency_key=request.idempotency_key,
        request_digest=request.digest,
        pipeline=request.pipeline,
        inputs=request.inputs,
        fingerprint=run_fingerprint(request.pipeline, request.inputs),
        created_at=now,
        metadata=request.metadata,
        state=RunState.PLANNED,
        expected_outputs=expected_outputs,
    )
    return RunPlan(run=run, tasks=tuple(tasks))


def _upstream_nodes(bindings: tuple[CompiledBindingPlan, ...]) -> tuple[str, ...]:
    nodes: list[str] = []
    for binding in bindings:
        for reference in binding.references:
            if reference.startswith("$nodes."):
                node = reference.split(".", 3)[1]
                if node not in nodes:
                    nodes.append(node)
    return tuple(nodes)


__all__ = [
    "CreateRunRequest",
    "PipelineRun",
    "PipelineTask",
    "RunOutputExpectation",
    "RunPlan",
    "RunState",
    "TaskState",
    "plan_run",
    "run_fingerprint",
]
