from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import cast
from uuid import UUID

import pytest

from provium import JsonValue
from provium_pipeline.compiler.models import CompiledBindingPlan, ConfigurationSnapshot
from provium_pipeline.compiler.models import CompiledPipeline as _CompiledPipeline
from provium_pipeline.compiler.resolution import (
    ResolvedPipelineConfiguration as _ResolvedPipelineConfiguration,
)
from provium_pipeline.execution_codec import (
    ExecutionDecodingError,
    ExecutionEncodingError,
    compiled_pipeline_from_value,
    compiled_pipeline_inputs_from_value,
    compiled_pipeline_nodes_from_value,
    compiled_pipeline_outputs_from_value,
    pipeline_run_document,
    pipeline_run_document_from_json,
    pipeline_run_json,
    pipeline_task_document,
    pipeline_task_from_json,
    pipeline_task_json,
    resolved_pipeline_configuration_from_value,
    run_input_snapshot_from_value,
    to_json_value,
)
from provium_pipeline.execution_codec import to_json_value as _to_json_value
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
        dependencies=(TaskId(UUID("00000000-0000-0000-0000-000000000003")),),
        state=TaskState.READY,
    )


def test_json_domain_encoder_handles_nested_immutable_runtime_values() -> None:
    value = {
        "nested": _Nested("example", (1, 2)),
        "mapping": MappingProxyType({"enabled": True}),
        "identifier": TaskId(UUID("00000000-0000-0000-0000-000000000004")),
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


def test_pipeline_task_json_round_trips_through_strict_decoder() -> None:
    task = _task()

    assert pipeline_task_from_json(pipeline_task_json(task)) == task


def test_pipeline_task_decoder_reconstructs_configuration_and_bindings() -> None:
    binding = CompiledBindingPlan(
        field="source",
        artifact_identifier="example.artifact/v1",
        minimum=1,
        maximum=None,
        references=("upstream.output",),
    )
    task = replace(
        _task(),
        configuration_snapshot=ConfigurationSnapshot(
            model_target="example.models:Configuration",
            schema_digest="b" * 64,
            value={"enabled": True, "thresholds": [1, 2]},
            value_digest="c" * 64,
        ),
        setup_bindings=(binding,),
        input_bindings=(binding,),
    )

    assert pipeline_task_from_json(pipeline_task_json(task)) == task


_MALFORMED_MUTATIONS: list[tuple[Callable[[dict[str, JsonValue]], object], str]] = [
    (lambda document: document.update(schema="provium.pipeline-task/v2"), "schema"),
    (lambda document: document.update(unexpected=True), "fields"),
    (lambda document: document.pop("state"), "fields"),
    (lambda document: document.update(identifier="not-a-uuid"), "identifier"),
    (lambda document: document.update(state="unknown"), "state"),
    (lambda document: document.update(node_identifier=1), "node_identifier"),
    (
        lambda document: document.update(expected_output_fields=[1]),
        "expected_output_fields",
    ),
    (
        lambda document: document.update(expected_output_fields="invalid"),
        "expected_output_fields",
    ),
]


@pytest.mark.parametrize(("mutation", "message"), _MALFORMED_MUTATIONS)
def test_pipeline_task_decoder_rejects_malformed_documents(
    mutation: Callable[[dict[str, JsonValue]], object], message: str
) -> None:
    document = pipeline_task_document(_task())
    mutation(document)

    with pytest.raises(ExecutionDecodingError, match=message):
        pipeline_task_from_json(json.dumps(document))


def test_pipeline_task_decoder_rejects_malformed_nested_values() -> None:
    document = pipeline_task_document(_task())
    document["configuration_snapshot"] = {
        "model_target": "target",
        "schema_digest": "digest",
        "value": {},
        "value_digest": "digest",
        "extra": True,
    }
    with pytest.raises(ExecutionDecodingError, match="configuration_snapshot fields"):
        pipeline_task_from_json(json.dumps(document))

    binding: dict[str, JsonValue] = {
        "field": "source",
        "artifact_identifier": "artifact",
        "minimum": True,
        "maximum": None,
        "references": [],
    }
    document = pipeline_task_document(_task())
    document["setup_bindings"] = [binding]
    with pytest.raises(ExecutionDecodingError, match="minimum"):
        pipeline_task_from_json(json.dumps(document))

    binding["minimum"] = 1
    binding["maximum"] = True
    with pytest.raises(ExecutionDecodingError, match="maximum"):
        pipeline_task_from_json(json.dumps(document))

    binding["maximum"] = None
    binding["references"] = [1]
    with pytest.raises(ExecutionDecodingError, match="references"):
        pipeline_task_from_json(json.dumps(document))

    document["setup_bindings"] = "not-an-array"
    with pytest.raises(ExecutionDecodingError, match="array"):
        pipeline_task_from_json(json.dumps(document))


@pytest.mark.parametrize("payload", ["not json", "[]", "null"])
def test_pipeline_task_decoder_requires_a_json_object(payload: str) -> None:
    with pytest.raises(ExecutionDecodingError, match="JSON object"):
        pipeline_task_from_json(payload)


def test_pipeline_run_json_is_versioned_complete_and_canonical() -> None:
    from datetime import UTC, datetime

    from provium_pipeline.execution_store import InMemoryExecutionStore
    from test.test_execution_store import request

    store = InMemoryExecutionStore(clock=lambda: datetime(2026, 8, 23, tzinfo=UTC))
    run = store.create_run(request())

    document = pipeline_run_document(run)
    encoded = pipeline_run_json(run)

    pipeline = cast(dict[str, JsonValue], document["pipeline"])
    inputs = cast(dict[str, JsonValue], document["inputs"])
    assert document["schema"] == "provium.pipeline-run/v1"
    assert document["identifier"] == str(run.identifier)
    assert pipeline["definition_digest"] == run.pipeline.definition_digest
    assert inputs["digest"] == run.inputs.digest
    assert document["state"] == run.state.value
    assert json.loads(encoded) == document
    assert encoded == pipeline_run_json(run)
    assert pipeline_run_document_from_json(encoded) == document


def test_pipeline_run_document_decoder_rejects_invalid_envelopes() -> None:
    from datetime import UTC, datetime

    from provium_pipeline.execution_store import InMemoryExecutionStore
    from test.test_execution_store import request

    run = InMemoryExecutionStore(
        clock=lambda: datetime(2026, 8, 23, tzinfo=UTC)
    ).create_run(request())
    document = pipeline_run_document(run)

    document["schema"] = "provium.pipeline-run/v2"
    with pytest.raises(ExecutionDecodingError, match="schema"):
        pipeline_run_document_from_json(json.dumps(document))

    document = pipeline_run_document(run)
    document["unexpected"] = True
    with pytest.raises(ExecutionDecodingError, match="fields"):
        pipeline_run_document_from_json(json.dumps(document))

    for payload in ("not json", "[]", "null"):
        with pytest.raises(ExecutionDecodingError, match="JSON object"):
            pipeline_run_document_from_json(payload)


def test_run_input_snapshot_value_round_trips_typed_models() -> None:
    from datetime import UTC, datetime

    from provium_pipeline.execution_store import InMemoryExecutionStore
    from test.test_execution_store import request

    run = InMemoryExecutionStore(
        clock=lambda: datetime(2026, 8, 23, tzinfo=UTC)
    ).create_run(request())
    value = pipeline_run_document(run)["inputs"]

    assert run_input_snapshot_from_value(value) == run.inputs


def test_run_input_snapshot_decoder_rejects_malformed_nested_values() -> None:
    valid: dict[str, JsonValue] = {
        "records": [{"key": "record", "inputs": {}, "labels": {}}],
        "shared_inputs": {},
        "digest": "digest",
        "sources": [],
    }
    assert run_input_snapshot_from_value(valid).digest == "digest"

    with pytest.raises(ExecutionDecodingError, match="fields"):
        run_input_snapshot_from_value({**valid, "unexpected": True})
    with pytest.raises(ExecutionDecodingError, match="records"):
        run_input_snapshot_from_value({**valid, "records": "invalid"})
    with pytest.raises(ExecutionDecodingError, match="inputs"):
        run_input_snapshot_from_value(
            cast(
                JsonValue,
                {**valid, "records": [{"key": "record", "inputs": [], "labels": {}}]},
            )
        )
    with pytest.raises(ExecutionDecodingError, match="shared_inputs"):
        run_input_snapshot_from_value({**valid, "shared_inputs": {"model": [1]}})
    with pytest.raises(ExecutionDecodingError, match="labels"):
        run_input_snapshot_from_value(
            cast(
                JsonValue,
                {**valid, "records": [{"key": "record", "inputs": {}, "labels": []}]},
            )
        )
    with pytest.raises(ExecutionDecodingError, match="labels"):
        run_input_snapshot_from_value(
            cast(
                object,
                {
                    **valid,
                    "records": [{"key": "record", "inputs": {}, "labels": {1: "bad"}}],
                },
            )
        )
    with pytest.raises(ExecutionDecodingError, match="sources"):
        run_input_snapshot_from_value({**valid, "sources": "invalid"})


def test_run_input_snapshot_decoder_reconstructs_source_descriptors() -> None:
    from provium_pipeline.inputs import InputSourceKind

    kind = next(iter(InputSourceKind))
    value: dict[str, JsonValue] = {
        "records": [],
        "shared_inputs": {},
        "digest": "digest",
        "sources": [
            {
                "kind": kind.value,
                "identifier": "source",
                "configuration": {"path": "inputs.ndjson"},
                "result_digest": "result",
            }
        ],
    }

    snapshot = run_input_snapshot_from_value(value)

    assert snapshot.sources[0].kind is kind
    assert snapshot.sources[0].configuration == {"path": "inputs.ndjson"}

    source = cast(dict[str, JsonValue], cast(list[JsonValue], value["sources"])[0])
    source["kind"] = "invalid"
    with pytest.raises(ExecutionDecodingError, match="source kind"):
        run_input_snapshot_from_value(value)


def test_compiled_pipeline_decoder_round_trips_typed_model() -> None:
    resolved_configuration = _ResolvedPipelineConfiguration(
        layers=(),
        nodes=(),
        document={},
        document_digest="configuration-digest",
    )
    pipeline = _CompiledPipeline(
        identifier="example.pipeline",
        version="1.0.0",
        definition_snapshot={},
        definition_digest="definition-digest",
        semantic_digest="semantic-digest",
        inputs=(),
        nodes=(),
        outputs=(),
        resolved_configuration=resolved_configuration,
    )

    document = _to_json_value(pipeline)

    assert compiled_pipeline_from_value(document) == pipeline


def test_compiled_pipeline_decoder_rejects_malformed_envelopes() -> None:
    pipeline = _CompiledPipeline(
        identifier="example.pipeline",
        version="1.0.0",
        definition_snapshot={},
        definition_digest="definition-digest",
        semantic_digest="semantic-digest",
        inputs=(),
        nodes=(),
        outputs=(),
        resolved_configuration=_ResolvedPipelineConfiguration(
            layers=(),
            nodes=(),
            document={},
            document_digest="configuration-digest",
        ),
    )
    document = _to_json_value(pipeline)
    assert isinstance(document, dict)

    with pytest.raises(ExecutionDecodingError, match="compiled pipeline fields"):
        compiled_pipeline_from_value({**document, "unexpected": True})
    with pytest.raises(ExecutionDecodingError, match="compiled pipeline fields"):
        compiled_pipeline_from_value(
            {key: value for key, value in document.items() if key != "identifier"}
        )
    with pytest.raises(ExecutionDecodingError, match="compiled pipeline identifier"):
        compiled_pipeline_from_value({**document, "identifier": 1})
    with pytest.raises(
        ExecutionDecodingError, match="compiled pipeline definition snapshot"
    ):
        compiled_pipeline_from_value({**document, "definition_snapshot": []})


def test_compiled_pipeline_inputs_and_outputs_round_trip_typed_models() -> None:
    from datetime import UTC, datetime

    from provium_pipeline.execution_store import InMemoryExecutionStore
    from test.test_execution_store import request

    run = InMemoryExecutionStore(
        clock=lambda: datetime(2026, 8, 23, tzinfo=UTC)
    ).create_run(request())
    pipeline = cast(dict[str, JsonValue], pipeline_run_document(run)["pipeline"])

    assert (
        compiled_pipeline_inputs_from_value(pipeline["inputs"]) == run.pipeline.inputs
    )
    assert (
        compiled_pipeline_outputs_from_value(pipeline["outputs"])
        == run.pipeline.outputs
    )


def test_compiled_pipeline_input_and_output_decoders_reject_malformed_values() -> None:
    valid_input: dict[str, JsonValue] = {
        "name": "source",
        "artifact_identifier": "example.SourceV1",
        "scope": "record",
        "minimum": 1,
        "maximum": None,
    }
    assert compiled_pipeline_inputs_from_value([valid_input])[0].maximum is None

    valid_output: dict[str, JsonValue] = {
        "name": "result",
        "node": "transform",
        "field": "result",
        "artifact_identifier": "example.ResultV1",
        "contract_digest": "digest",
    }

    with pytest.raises(ExecutionDecodingError, match="array"):
        compiled_pipeline_inputs_from_value({})
    with pytest.raises(ExecutionDecodingError, match="scope"):
        compiled_pipeline_inputs_from_value([{**valid_input, "scope": "invalid"}])
    with pytest.raises(ExecutionDecodingError, match="minimum"):
        compiled_pipeline_inputs_from_value([{**valid_input, "minimum": True}])
    with pytest.raises(ExecutionDecodingError, match="maximum"):
        compiled_pipeline_inputs_from_value([{**valid_input, "maximum": False}])
    with pytest.raises(ExecutionDecodingError, match="fields"):
        compiled_pipeline_outputs_from_value([{**valid_output, "unexpected": True}])
    with pytest.raises(ExecutionDecodingError, match="contract_digest"):
        compiled_pipeline_outputs_from_value([{**valid_output, "contract_digest": 1}])


def test_compiled_pipeline_nodes_round_trip_typed_models() -> None:
    from datetime import UTC, datetime

    from provium_pipeline.execution_store import InMemoryExecutionStore
    from test.test_execution_store import request

    run = InMemoryExecutionStore(
        clock=lambda: datetime(2026, 8, 23, tzinfo=UTC)
    ).create_run(request())
    pipeline = cast(dict[str, JsonValue], pipeline_run_document(run)["pipeline"])

    assert compiled_pipeline_nodes_from_value(pipeline["nodes"]) == run.pipeline.nodes


def test_compiled_pipeline_node_decoder_rejects_malformed_values() -> None:
    valid_contract: dict[str, JsonValue] = {
        "field": "result",
        "artifact_identifier": "example.ResultV1",
        "minimum": 1,
        "maximum": None,
        "digest": "digest",
    }
    valid_node: dict[str, JsonValue] = {
        "identifier": "transform",
        "procedure_identifier": "example.TransformV1",
        "procedure_contract_digest": "procedure",
        "configuration_snapshot": None,
        "setup_bindings": [],
        "input_bindings": [],
        "output_contracts": [valid_contract],
        "cache_policy": "enabled",
        "preparation_contract_digest": "preparation",
        "output_contract_digest": "output",
    }

    assert compiled_pipeline_nodes_from_value([valid_node])[0].cache_policy == "enabled"
    with pytest.raises(ExecutionDecodingError, match="cache_policy"):
        compiled_pipeline_nodes_from_value([{**valid_node, "cache_policy": "unknown"}])
    with pytest.raises(ExecutionDecodingError, match="output_contracts"):
        compiled_pipeline_nodes_from_value([{**valid_node, "output_contracts": {}}])
    with pytest.raises(ExecutionDecodingError, match="minimum"):
        compiled_pipeline_nodes_from_value(
            [{**valid_node, "output_contracts": [{**valid_contract, "minimum": True}]}]
        )
    with pytest.raises(ExecutionDecodingError, match="digest"):
        compiled_pipeline_nodes_from_value(
            [{**valid_node, "output_contracts": [{**valid_contract, "digest": 1}]}]
        )


def test_resolved_pipeline_configuration_round_trips_typed_models() -> None:
    from datetime import UTC, datetime

    from provium_pipeline.execution_store import InMemoryExecutionStore
    from test.test_execution_store import request

    run = InMemoryExecutionStore(
        clock=lambda: datetime(2026, 8, 23, tzinfo=UTC)
    ).create_run(request())
    pipeline = cast(dict[str, JsonValue], pipeline_run_document(run)["pipeline"])

    assert (
        resolved_pipeline_configuration_from_value(pipeline["resolved_configuration"])
        == run.pipeline.resolved_configuration
    )


def test_resolved_pipeline_configuration_decoder_rejects_malformed_values() -> None:
    valid: dict[str, JsonValue] = {
        "layers": [],
        "nodes": [{"node": "transform", "source_layers": [], "snapshot": None}],
        "document": {"schema": "provium.pipeline-config/v1", "nodes": {}},
        "document_digest": "digest",
    }

    assert resolved_pipeline_configuration_from_value(valid).document_digest == "digest"
    with pytest.raises(ExecutionDecodingError, match="layers"):
        resolved_pipeline_configuration_from_value({**valid, "layers": {}})
    with pytest.raises(ExecutionDecodingError, match="source_layers"):
        resolved_pipeline_configuration_from_value(
            {
                **valid,
                "nodes": [
                    {"node": "transform", "source_layers": [1], "snapshot": None}
                ],
            }
        )
    with pytest.raises(ExecutionDecodingError, match="document"):
        resolved_pipeline_configuration_from_value({**valid, "document": []})


def test_resolved_pipeline_configuration_decoder_verifies_layer_audit() -> None:
    from provium_pipeline.compiler.configuration import PipelineConfiguration
    from provium_pipeline.compiler.resolution import PipelineConfigurationLayer

    layer = PipelineConfigurationLayer(
        source="override.json",
        configuration=PipelineConfiguration(nodes={"transform": {"threshold": 2}}),
    )
    layer_value = cast(dict[str, JsonValue], to_json_value(layer))
    value: dict[str, JsonValue] = {
        "layers": [layer_value],
        "nodes": [],
        "document": {"schema": "provium.pipeline-config/v1", "nodes": {}},
        "document_digest": "digest",
    }

    decoded = resolved_pipeline_configuration_from_value(value)

    assert decoded.layers == (layer,)

    invalid_configuration = {**layer_value, "configuration": {"unexpected": True}}
    with pytest.raises(ExecutionDecodingError, match="configuration"):
        resolved_pipeline_configuration_from_value(
            {**value, "layers": [invalid_configuration]}
        )
    with pytest.raises(ExecutionDecodingError, match="document does not match"):
        resolved_pipeline_configuration_from_value(
            {**value, "layers": [{**layer_value, "document": {}}]}
        )
    with pytest.raises(ExecutionDecodingError, match="digest does not match"):
        resolved_pipeline_configuration_from_value(
            {**value, "layers": [{**layer_value, "digest": "wrong"}]}
        )
