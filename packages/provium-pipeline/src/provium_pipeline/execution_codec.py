"""Safe deterministic JSON documents for durable execution models."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any, cast

from provium import JsonValue, canonical_json
from provium_pipeline.compiler.models import CompiledBindingPlan, ConfigurationSnapshot
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


class ExecutionDecodingError(ValueError):
    """Raised when durable execution JSON does not match its declared schema."""


def _strict_object(value: object, fields: set[str], context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ExecutionDecodingError(f"{context} must be a JSON object")
    document = cast(dict[str, object], value)
    actual = set(document)
    if actual != fields:
        raise ExecutionDecodingError(
            f"{context} fields do not match schema; "
            f"missing={sorted(fields - actual)}, extra={sorted(actual - fields)}"
        )
    return document


def _string(value: object, context: str) -> str:
    if not isinstance(value, str):
        raise ExecutionDecodingError(f"{context} must be a string")
    return value


def _string_tuple(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ExecutionDecodingError(f"{context} must be an array of strings")
    items = cast(list[object], value)
    if not all(isinstance(item, str) for item in items):
        raise ExecutionDecodingError(f"{context} must be an array of strings")
    return tuple(cast(list[str], items))


def _configuration_snapshot(value: object) -> ConfigurationSnapshot | None:

    if value is None:
        return None
    document = _strict_object(
        value,
        {"model_target", "schema_digest", "value", "value_digest"},
        "configuration_snapshot",
    )
    return ConfigurationSnapshot(
        model_target=_string(
            document["model_target"], "configuration_snapshot.model_target"
        ),
        schema_digest=_string(
            document["schema_digest"], "configuration_snapshot.schema_digest"
        ),
        value=cast(JsonValue, document["value"]),
        value_digest=_string(
            document["value_digest"], "configuration_snapshot.value_digest"
        ),
    )


def _binding_plans(value: object, context: str) -> tuple[CompiledBindingPlan, ...]:
    if not isinstance(value, list):
        raise ExecutionDecodingError(f"{context} must be an array")
    plans: list[CompiledBindingPlan] = []
    fields = {"field", "artifact_identifier", "minimum", "maximum", "references"}
    for index, item in enumerate(cast(list[object], value)):
        document = _strict_object(item, fields, f"{context}[{index}]")
        minimum = document["minimum"]
        maximum = document["maximum"]
        if isinstance(minimum, bool) or not isinstance(minimum, int):
            raise ExecutionDecodingError(
                f"{context}[{index}].minimum must be an integer"
            )
        if maximum is not None and (
            isinstance(maximum, bool) or not isinstance(maximum, int)
        ):
            raise ExecutionDecodingError(
                f"{context}[{index}].maximum must be an integer or null"
            )
        plans.append(
            CompiledBindingPlan(
                field=_string(document["field"], f"{context}[{index}].field"),
                artifact_identifier=_string(
                    document["artifact_identifier"],
                    f"{context}[{index}].artifact_identifier",
                ),
                minimum=minimum,
                maximum=maximum,
                references=_string_tuple(
                    document["references"], f"{context}[{index}].references"
                ),
            )
        )
    return tuple(plans)


def pipeline_task_from_json(payload: str) -> PipelineTask:
    """Decode a versioned task snapshot without importing persisted model targets."""
    from uuid import UUID

    from provium_pipeline.run_models import TaskState

    try:
        decoded = json.loads(payload)
    except (json.JSONDecodeError, TypeError) as error:
        raise ExecutionDecodingError("pipeline task must be a JSON object") from error

    document = _strict_object(
        decoded,
        {
            "schema",
            "identifier",
            "run_identifier",
            "node_identifier",
            "record_key",
            "procedure_identifier",
            "procedure_contract_digest",
            "configuration_snapshot",
            "setup_bindings",
            "input_bindings",
            "expected_output_fields",
            "dependencies",
            "state",
        },
        "pipeline task",
    )
    if document["schema"] != "provium.pipeline-task/v1":
        raise ExecutionDecodingError("unsupported pipeline task schema")
    configuration_snapshot = _configuration_snapshot(document["configuration_snapshot"])
    setup_bindings = _binding_plans(document["setup_bindings"], "setup_bindings")
    input_bindings = _binding_plans(document["input_bindings"], "input_bindings")

    try:
        return PipelineTask(
            identifier=TaskId(UUID(_string(document["identifier"], "identifier"))),
            run_identifier=RunId(
                UUID(_string(document["run_identifier"], "run_identifier"))
            ),
            node_identifier=_string(document["node_identifier"], "node_identifier"),
            record_key=InputRecordKey(_string(document["record_key"], "record_key")),
            procedure_identifier=_string(
                document["procedure_identifier"], "procedure_identifier"
            ),
            procedure_contract_digest=_string(
                document["procedure_contract_digest"], "procedure_contract_digest"
            ),
            configuration_snapshot=configuration_snapshot,
            setup_bindings=setup_bindings,
            input_bindings=input_bindings,
            expected_output_fields=_string_tuple(
                document["expected_output_fields"], "expected_output_fields"
            ),
            dependencies=tuple(
                TaskId(UUID(value))
                for value in _string_tuple(document["dependencies"], "dependencies")
            ),
            state=TaskState(_string(document["state"], "state")),
        )
    except (TypeError, ValueError) as error:
        raise ExecutionDecodingError(
            f"invalid pipeline task identifier, dependency, or state: {error}"
        ) from error
