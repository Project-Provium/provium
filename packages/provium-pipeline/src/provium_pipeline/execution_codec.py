"""Safe deterministic JSON documents for durable execution models."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal, cast

from pydantic import BaseModel

from provium import JsonValue, canonical_json
from provium_pipeline.compiler.configuration import PipelineConfiguration
from provium_pipeline.compiler.models import (
    CompiledBindingPlan,
    CompiledOutputContract,
    CompiledPipelineInput,
    CompiledPipelineNode,
    CompiledPipelineOutput,
    ConfigurationSnapshot,
)
from provium_pipeline.compiler.resolution import (
    PipelineConfigurationLayer,
    ResolvedNodeConfiguration,
    ResolvedPipelineConfiguration,
)
from provium_pipeline.definition.models import PipelineInputScope
from provium_pipeline.identifiers import InputRecordKey, RunId, TaskId
from provium_pipeline.inputs import (
    InputRecord,
    InputSourceDescriptor,
    InputSourceKind,
    RunInputSnapshot,
)
from provium_pipeline.run_models import PipelineRun, PipelineTask


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
    if isinstance(value, BaseModel):
        return cast(JsonValue, value.model_dump(mode="json", by_alias=True))
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


def pipeline_run_document(run: PipelineRun) -> dict[str, JsonValue]:
    """Return the complete versioned durable representation of a run."""
    encoded = cast(dict[str, JsonValue], to_json_value(run))
    return {"schema": "provium.pipeline-run/v1", **encoded}


def pipeline_run_json(run: PipelineRun) -> str:
    """Return canonical JSON text for a durable run snapshot."""
    return canonical_json(pipeline_run_document(run))


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


def pipeline_run_document_from_json(payload: str) -> dict[str, JsonValue]:
    """Parse and validate the versioned envelope of a durable run snapshot."""
    try:
        decoded = json.loads(payload)
    except (json.JSONDecodeError, TypeError) as error:
        raise ExecutionDecodingError("pipeline run must be a JSON object") from error
    document = _strict_object(
        decoded,
        {
            "schema",
            "identifier",
            "idempotency_namespace",
            "idempotency_key",
            "request_digest",
            "pipeline",
            "inputs",
            "fingerprint",
            "created_at",
            "metadata",
            "state",
            "expected_outputs",
        },
        "pipeline run",
    )
    if document["schema"] != "provium.pipeline-run/v1":
        raise ExecutionDecodingError("unsupported pipeline run schema")
    return cast(dict[str, JsonValue], document)


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


def _string_tuple_mapping(value: object, context: str) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict):
        raise ExecutionDecodingError(f"{context} must be a JSON object")
    mapping = cast(dict[object, object], value)
    result: dict[str, tuple[str, ...]] = {}
    for key, items in mapping.items():
        result[_string(key, f"{context} key")] = _string_tuple(
            items, f"{context}.{key}"
        )
    return result


def _json_mapping(value: object, context: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ExecutionDecodingError(f"{context} must be a JSON object")
    mapping = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in mapping):
        raise ExecutionDecodingError(f"{context} must be a JSON object")
    return cast(dict[str, JsonValue], mapping)


def _object_array(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise ExecutionDecodingError(f"{context} must be an array")
    return cast(list[object], value)


def _integer(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExecutionDecodingError(f"{context} must be an integer")
    return value


def _optional_integer(value: object, context: str) -> int | None:
    if value is None:
        return None
    return _integer(value, context)


def compiled_pipeline_inputs_from_value(
    value: object,
) -> tuple[CompiledPipelineInput, ...]:
    """Decode the pipeline input portion of a compiled run snapshot."""
    result: list[CompiledPipelineInput] = []
    fields = {"name", "artifact_identifier", "scope", "minimum", "maximum"}
    for index, item in enumerate(_object_array(value, "pipeline.inputs")):
        document = _strict_object(item, fields, f"pipeline.inputs[{index}]")
        try:
            scope = PipelineInputScope(
                _string(document["scope"], f"pipeline.inputs[{index}].scope")
            )
        except ValueError as error:
            raise ExecutionDecodingError(
                f"invalid pipeline input scope: {error}"
            ) from error
        result.append(
            CompiledPipelineInput(
                name=_string(document["name"], f"pipeline.inputs[{index}].name"),
                artifact_identifier=_string(
                    document["artifact_identifier"],
                    f"pipeline.inputs[{index}].artifact_identifier",
                ),
                scope=scope,
                minimum=_integer(
                    document["minimum"], f"pipeline.inputs[{index}].minimum"
                ),
                maximum=_optional_integer(
                    document["maximum"], f"pipeline.inputs[{index}].maximum"
                ),
            )
        )
    return tuple(result)


def compiled_pipeline_outputs_from_value(
    value: object,
) -> tuple[CompiledPipelineOutput, ...]:
    """Decode the pipeline output portion of a compiled run snapshot."""
    result: list[CompiledPipelineOutput] = []
    fields = {"name", "node", "field", "artifact_identifier", "contract_digest"}
    for index, item in enumerate(_object_array(value, "pipeline.outputs")):
        document = _strict_object(item, fields, f"pipeline.outputs[{index}]")
        result.append(
            CompiledPipelineOutput(
                name=_string(document["name"], f"pipeline.outputs[{index}].name"),
                node=_string(document["node"], f"pipeline.outputs[{index}].node"),
                field=_string(document["field"], f"pipeline.outputs[{index}].field"),
                artifact_identifier=_string(
                    document["artifact_identifier"],
                    f"pipeline.outputs[{index}].artifact_identifier",
                ),
                contract_digest=_string(
                    document["contract_digest"],
                    f"pipeline.outputs[{index}].contract_digest",
                ),
            )
        )
    return tuple(result)


def run_input_snapshot_from_value(value: object) -> RunInputSnapshot:
    """Decode a durable input snapshot without resolving external resources."""
    document = _strict_object(
        value, {"records", "shared_inputs", "digest", "sources"}, "inputs"
    )
    records_value = document["records"]
    sources_value = document["sources"]
    if not isinstance(records_value, list):
        raise ExecutionDecodingError("inputs.records must be an array")
    if not isinstance(sources_value, list):
        raise ExecutionDecodingError("inputs.sources must be an array")

    records: list[InputRecord] = []
    for index, item in enumerate(cast(list[object], records_value)):
        record = _strict_object(
            item, {"key", "inputs", "labels"}, f"inputs.records[{index}]"
        )
        records.append(
            InputRecord(
                key=InputRecordKey(
                    _string(record["key"], f"inputs.records[{index}].key")
                ),
                inputs=_string_tuple_mapping(
                    record["inputs"], f"inputs.records[{index}].inputs"
                ),
                labels=_json_mapping(
                    record["labels"], f"inputs.records[{index}].labels"
                ),
            )
        )

    sources: list[InputSourceDescriptor] = []
    for index, item in enumerate(cast(list[object], sources_value)):
        source = _strict_object(
            item,
            {"kind", "identifier", "configuration", "result_digest"},
            f"inputs.sources[{index}]",
        )
        try:
            kind = InputSourceKind(
                _string(source["kind"], f"inputs.sources[{index}].kind")
            )
        except ValueError as error:
            raise ExecutionDecodingError(
                f"invalid input source kind: {error}"
            ) from error
        sources.append(
            InputSourceDescriptor(
                kind=kind,
                identifier=_string(
                    source["identifier"], f"inputs.sources[{index}].identifier"
                ),
                configuration=_json_mapping(
                    source["configuration"],
                    f"inputs.sources[{index}].configuration",
                ),
                result_digest=_string(
                    source["result_digest"],
                    f"inputs.sources[{index}].result_digest",
                ),
            )
        )

    return RunInputSnapshot(
        records=records,
        shared_inputs=_string_tuple_mapping(
            document["shared_inputs"], "inputs.shared_inputs"
        ),
        digest=_string(document["digest"], "inputs.digest"),
        sources=sources,
    )


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


def resolved_pipeline_configuration_from_value(
    value: object,
) -> ResolvedPipelineConfiguration:
    """Decode the resolved configuration audit stored with a compiled pipeline."""
    document = _strict_object(
        value,
        {"layers", "nodes", "document", "document_digest"},
        "pipeline.resolved_configuration",
    )
    layers: list[PipelineConfigurationLayer] = []
    for index, item in enumerate(
        _object_array(document["layers"], "pipeline.resolved_configuration.layers")
    ):
        context = f"pipeline.resolved_configuration.layers[{index}]"
        layer_document = _strict_object(
            item, {"source", "configuration", "document", "digest"}, context
        )
        configuration_value = _json_mapping(
            layer_document["configuration"], f"{context}.configuration"
        )
        try:
            configuration = PipelineConfiguration.model_validate(configuration_value)
        except ValueError as error:
            raise ExecutionDecodingError(
                f"invalid {context}.configuration: {error}"
            ) from error
        layer = PipelineConfigurationLayer(
            source=_string(layer_document["source"], f"{context}.source"),
            configuration=configuration,
        )
        if (
            _json_mapping(layer_document["document"], f"{context}.document")
            != layer.document
        ):
            raise ExecutionDecodingError(
                f"{context}.document does not match configuration"
            )
        if _string(layer_document["digest"], f"{context}.digest") != layer.digest:
            raise ExecutionDecodingError(
                f"{context}.digest does not match configuration"
            )
        layers.append(layer)

    nodes: list[ResolvedNodeConfiguration] = []
    for index, item in enumerate(
        _object_array(document["nodes"], "pipeline.resolved_configuration.nodes")
    ):
        context = f"pipeline.resolved_configuration.nodes[{index}]"
        node = _strict_object(item, {"node", "source_layers", "snapshot"}, context)
        nodes.append(
            ResolvedNodeConfiguration(
                node=_string(node["node"], f"{context}.node"),
                source_layers=_string_tuple(
                    node["source_layers"], f"{context}.source_layers"
                ),
                snapshot=_configuration_snapshot(node["snapshot"]),
            )
        )

    return ResolvedPipelineConfiguration(
        layers=tuple(layers),
        nodes=tuple(nodes),
        document=_json_mapping(
            document["document"], "pipeline.resolved_configuration.document"
        ),
        document_digest=_string(
            document["document_digest"],
            "pipeline.resolved_configuration.document_digest",
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


def _output_contracts(
    value: object, context: str
) -> tuple[CompiledOutputContract, ...]:
    result: list[CompiledOutputContract] = []
    fields = {"field", "artifact_identifier", "minimum", "maximum", "digest"}
    for index, item in enumerate(_object_array(value, context)):
        document = _strict_object(item, fields, f"{context}[{index}]")
        result.append(
            CompiledOutputContract(
                field=_string(document["field"], f"{context}[{index}].field"),
                artifact_identifier=_string(
                    document["artifact_identifier"],
                    f"{context}[{index}].artifact_identifier",
                ),
                minimum=_integer(document["minimum"], f"{context}[{index}].minimum"),
                maximum=_optional_integer(
                    document["maximum"], f"{context}[{index}].maximum"
                ),
                digest=_string(document["digest"], f"{context}[{index}].digest"),
            )
        )
    return tuple(result)


def compiled_pipeline_nodes_from_value(
    value: object,
) -> tuple[CompiledPipelineNode, ...]:
    """Decode compiled nodes without resolving persisted procedure targets."""
    result: list[CompiledPipelineNode] = []
    fields = {
        "identifier",
        "procedure_identifier",
        "procedure_contract_digest",
        "configuration_snapshot",
        "setup_bindings",
        "input_bindings",
        "output_contracts",
        "cache_policy",
        "preparation_contract_digest",
        "output_contract_digest",
    }
    for index, item in enumerate(_object_array(value, "pipeline.nodes")):
        context = f"pipeline.nodes[{index}]"
        document = _strict_object(item, fields, context)
        cache_policy = _string(document["cache_policy"], f"{context}.cache_policy")
        if cache_policy not in {"enabled", "disabled"}:
            raise ExecutionDecodingError(
                f"{context}.cache_policy must be enabled or disabled"
            )
        result.append(
            CompiledPipelineNode(
                identifier=_string(document["identifier"], f"{context}.identifier"),
                procedure_identifier=_string(
                    document["procedure_identifier"], f"{context}.procedure_identifier"
                ),
                procedure_contract_digest=_string(
                    document["procedure_contract_digest"],
                    f"{context}.procedure_contract_digest",
                ),
                configuration_snapshot=_configuration_snapshot(
                    document["configuration_snapshot"]
                ),
                setup_bindings=_binding_plans(
                    document["setup_bindings"], f"{context}.setup_bindings"
                ),
                input_bindings=_binding_plans(
                    document["input_bindings"], f"{context}.input_bindings"
                ),
                output_contracts=_output_contracts(
                    document["output_contracts"], f"{context}.output_contracts"
                ),
                cache_policy=cast(Literal["enabled", "disabled"], cache_policy),
                preparation_contract_digest=_string(
                    document["preparation_contract_digest"],
                    f"{context}.preparation_contract_digest",
                ),
                output_contract_digest=_string(
                    document["output_contract_digest"],
                    f"{context}.output_contract_digest",
                ),
            )
        )
    return tuple(result)


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
