from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

import pytest

from provium_pipeline.execution_codec import (
    ExecutionEncodingError,
    pipeline_task_document,
    pipeline_task_json,
    to_json_value,
)
from provium_pipeline.identifiers import InputRecordKey, RunId, TaskId
from provium_pipeline.run_models import PipelineTask, TaskState


@dataclass(frozen=True)
class _Nested:
    name: str
    values: tuple[int, ...]


class _State(StrEnum):
    READY = "ready"


def _task() -> PipelineTask:
    return PipelineTask(
        identifier=TaskId(UUID("00000000-0000-0000-0000-000000000001")),
        run_identifier=RunId(UUID("00000000-0000-0000-0000-000000000002")),
        node_identifier="node",
        record_key=InputRecordKey("record"),
        procedure_identifier="example.process/v1",
        procedure_contract_digest="a" * 64,
        configuration_snapshot=None,
        setup_bindings=(),
        input_bindings=(),
        expected_output_fields=("required", "optional"),
        dependencies=(
            TaskId(UUID("00000000-0000-0000-0000-000000000003")),
        ),
        state=TaskState.READY,
    )


def test_json_domain_encoder_handles_nested_immutable_runtime_values() -> None:
    value = {
        "nested": _Nested("example", (1, 2)),
        "mapping": MappingProxyType({"enabled": True}),
        "identifier": TaskId(
            UUID("00000000-0000-0000-0000-000000000004")
        ),
        "time": datetime(2026, 8, 23, tzinfo=UTC),
        "state": _State.READY,
        "target": _Nested,
    }

    assert to_json_value(value) == {
        "identifier": "00000000-0000-0000-0000-000000000004",
        "mapping": {"enabled": True},
        "nested": {"name": "example", "values": [1, 2]},
        "state": "ready",
        "target": f"{__name__}:_Nested",
        "time": "2026-08-23T00:00:00+00:00",
    }


def test_json_domain_encoder_rejects_unsupported_or_nonstring_mapping_values() -> None:
    with pytest.raises(ExecutionEncodingError, match="unsupported"):
        to_json_value({1, 2})
    with pytest.raises(ExecutionEncodingError, match="string"):
        to_json_value({1: "value"})


def test_pipeline_task_document_is_versioned_and_semantically_complete() -> None:
    task = _task()

    assert pipeline_task_document(task) == {
        "schema": "provium.pipeline-task/v1",
        "identifier": str(task.identifier),
        "run_identifier": str(task.run_identifier),
        "node_identifier": "node",
        "record_key": "record",
        "procedure_identifier": "example.process/v1",
        "procedure_contract_digest": "a" * 64,
        "configuration_snapshot": None,
        "setup_bindings": [],
        "input_bindings": [],
        "expected_output_fields": ["required", "optional"],
        "dependencies": [str(task.dependencies[0])],
        "state": "ready",
    }


def test_pipeline_task_json_is_canonical_and_roundtrippable_json() -> None:
    document = pipeline_task_document(_task())

    encoded = pipeline_task_json(_task())

    assert json.loads(encoded) == document
    assert encoded == pipeline_task_json(_task())
