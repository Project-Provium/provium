from __future__ import annotations

from datetime import UTC, datetime

from provium import canonical_digest
from provium_pipeline import (
    CreateRunRequest,
    InputRecord,
    RunInputSnapshot,
    RunState,
    TaskState,
    plan_run,
    run_fingerprint,
)
from provium_pipeline.definition import NodeOutputReference
from provium_pipeline.identifiers import (
    InputRecordKey,
    PipelineNodeIdentifier,
    PipelineOutputName,
)
from test.compiler.test_compiler_success import PASSTHROUGH, compiler, definition


def input_snapshot(identity: str) -> RunInputSnapshot:
    return RunInputSnapshot.create(
        records=(
            InputRecord(
                key=InputRecordKey("record-1"),
                inputs={"source": (identity,)},
                labels={},
            ),
        ),
        shared_inputs={"model": ("model-1",)},
        sources=(),
    )


def test_run_fingerprint_contains_only_three_semantic_digests() -> None:
    compiled = compiler().compile(definition())
    inputs = input_snapshot("source-1")

    fingerprint = run_fingerprint(compiled, inputs)

    assert fingerprint == canonical_digest(
        {
            "pipeline": compiled.semantic_digest,
            "configuration": compiled.resolved_configuration.document_digest,
            "inputs": inputs.digest,
        }
    )
    assert fingerprint != run_fingerprint(compiled, input_snapshot("source-2"))


def test_task_state_satisfaction_is_explicit() -> None:
    assert TaskState.SUCCEEDED.satisfied
    assert TaskState.REUSED.satisfied
    assert not TaskState.READY.satisfied


def test_plan_run_creates_one_ready_task_and_expected_output_per_record() -> None:
    compiled = compiler().compile(definition())
    inputs = RunInputSnapshot.create(
        records=(
            InputRecord(
                key=InputRecordKey("record-b"),
                inputs={"source": ("source-b",)},
                labels={},
            ),
            InputRecord(
                key=InputRecordKey("record-a"),
                inputs={"source": ("source-a",)},
                labels={},
            ),
        ),
        shared_inputs={"model": ("model-1",)},
        sources=(),
    )
    created_at = datetime(2026, 8, 23, tzinfo=UTC)

    plan = plan_run(
        CreateRunRequest(
            pipeline=compiled,
            inputs=inputs,
            idempotency_namespace="cli",
            idempotency_key="request-1",
            metadata={"actor": "test"},
        ),
        now=created_at,
    )

    assert plan.run.state is RunState.PLANNED
    assert plan.run.created_at == created_at
    assert plan.run.fingerprint == run_fingerprint(compiled, inputs)
    assert len(plan.tasks) == 2
    assert all(task.state is TaskState.READY for task in plan.tasks)
    assert all(task.dependencies == () for task in plan.tasks)
    assert [task.record_key for task in plan.tasks] == [
        InputRecordKey("record-a"),
        InputRecordKey("record-b"),
    ]
    assert len(plan.run.expected_outputs) == 2


def test_plan_run_blocks_downstream_task_on_same_record_dependency() -> None:
    source = definition()
    first = source.nodes["transform"].model_copy(
        update={"uses": PASSTHROUGH.identifier}
    )
    upstream = NodeOutputReference(
        node=PipelineNodeIdentifier("first"),
        output=PipelineOutputName("result"),
    )
    second = first.model_copy(
        update={"setup": {"model": upstream}, "inputs": {"source": upstream}}
    )
    graph = source.model_copy(
        update={
            "nodes": {"second": second, "first": first},
            "outputs": {
                "result": NodeOutputReference(
                    node=PipelineNodeIdentifier("second"),
                    output=PipelineOutputName("result"),
                )
            },
        }
    )
    compiled = compiler().compile(graph)
    inputs = input_snapshot("source-1")

    plan = plan_run(
        CreateRunRequest(pipeline=compiled, inputs=inputs),
        now=datetime(2026, 8, 23, tzinfo=UTC),
    )

    first_task, second_task = plan.tasks
    assert first_task.state is TaskState.READY
    assert second_task.state is TaskState.BLOCKED
    assert second_task.dependencies == (first_task.identifier,)
