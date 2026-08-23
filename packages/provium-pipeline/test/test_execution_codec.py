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
from provium_pipeline.execution_codec import (
    ExecutionDecodingError,
    ExecutionEncodingError,
    pipeline_run_document,
    pipeline_run_document_from_json,
    pipeline_run_json,
    pipeline_task_document,
    pipeline_task_from_json,
    pipeline_task_json,
    run_input_snapshot_from_value,
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
