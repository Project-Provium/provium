"""Safe deterministic JSON documents for durable execution models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any, cast

from provium import JsonValue, canonical_json
from provium_pipeline.identifiers import InputRecordKey, RunId, TaskId
from provium_pipeline.run_models import PipelineTask


class ExecutionEncodingError(TypeError):
    """A runtime value cannot be represented by the execution JSON schema."""


_IDENTIFIER_TYPES = (RunId, TaskId, InputRecordKey)


def to_json_value(value: object) -> JsonValue:
    """Convert supported immutable runtime values into the JSON domain."""
    if isinstance(value, _IDENTIFIER_TYPES):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return to_json_value(value.value)
    if isinstance(value, type):
        return f"{value.__module__}:{value.__qualname__}"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        runtime_value = cast(Any, value)
        return {
            field.name: to_json_value(getattr(runtime_value, field.name))
            for field in fields(runtime_value)
        }
    if isinstance(value, Mapping):
        document: dict[str, JsonValue] = {}
        mapping = cast(Mapping[object, object], value)
        for key, item in mapping.items():
            if not isinstance(key, str):
                raise ExecutionEncodingError("JSON mapping keys must be strings")
            document[key] = to_json_value(item)
        return document
    if isinstance(value, (tuple, list)):
        sequence = cast(tuple[object, ...] | list[object], value)
        return [to_json_value(item) for item in sequence]
    raise ExecutionEncodingError(
        f"unsupported execution JSON value: {type(value).__name__}"
    )


def pipeline_task_document(task: PipelineTask) -> dict[str, JsonValue]:
    """Return the versioned complete JSON-domain task snapshot."""
    return {
        "schema": "provium.pipeline-task/v1",
        "identifier": str(task.identifier),
        "run_identifier": str(task.run_identifier),
        "node_identifier": task.node_identifier,
        "record_key": str(task.record_key),
        "procedure_identifier": task.procedure_identifier,
        "procedure_contract_digest": task.procedure_contract_digest,
        "configuration_snapshot": to_json_value(task.configuration_snapshot),
        "setup_bindings": to_json_value(task.setup_bindings),
        "input_bindings": to_json_value(task.input_bindings),
        "expected_output_fields": list(task.expected_output_fields),
        "dependencies": [str(identifier) for identifier in task.dependencies],
        "state": task.state.value,
    }


def pipeline_task_json(task: PipelineTask) -> str:
    """Return canonical JSON text for a durable task snapshot."""
    return canonical_json(pipeline_task_document(task))
