"""Complete pipeline definition document and codec contracts."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from pydantic import ValidationError

from provium_pipeline.definition import (
    NodeOutputReference,
    PipelineDefinition,
    PipelineDefinitionLoadError,
    PipelineInputReference,
    PipelineInputScope,
    PipelineNodeDefinition,
    canonical_definition_document,
    load_pipeline_json,
    load_pipeline_yaml,
)


@pytest.fixture
def document() -> dict[str, object]:
    return {
        "schema": "provium.pipeline/v1",
        "pipeline": {
            "identifier": "tngo.session-analysis",
            "version": "1",
            "label": "Session analysis",
        },
        "inputs": {
            "session": {"artifact": "tngo.SessionManifestV1", "scope": "record"},
            "model": {"artifact": "tngo.ModelV1", "scope": "shared"},
        },
        "nodes": {
            "detect": {
                "uses": "tngo.DetectSessionV1",
                "setup": {"model": "$inputs.model"},
                "inputs": {"session": "$inputs.session"},
                "config": {"confidence": 0.5},
            },
            "track": {
                "uses": "tngo.TrackSessionV1",
                "inputs": {
                    "detections": [
                        "$nodes.detect.detections",
                        "$inputs.session",
                    ]
                },
                "cache": "disabled",
                "metadata": {"owner": "vision"},
            },
        },
        "outputs": {"detections": "$nodes.detect.detections"},
    }


def test_complete_definition_coerces_references_and_defaults(
    document: dict[str, object],
) -> None:
    definition = PipelineDefinition.model_validate(document)

    assert definition.schema_version == "provium.pipeline/v1"
    assert definition.inputs["session"].scope is PipelineInputScope.RECORD
    assert definition.inputs["session"].cardinality.minimum == 1
    assert isinstance(definition.nodes["detect"].setup["model"], PipelineInputReference)
    repeated = definition.nodes["track"].inputs["detections"]
    assert isinstance(repeated, tuple)
    assert isinstance(repeated[0], NodeOutputReference)
    assert definition.nodes["detect"].cache == "enabled"
    assert isinstance(definition.outputs["detections"], NodeOutputReference)


def test_definition_is_strict_frozen_and_validates_defaults(
    document: dict[str, object],
) -> None:
    document["unexpected"] = True
    with pytest.raises(ValidationError):
        PipelineDefinition.model_validate(document)

    definition = PipelineDefinition.model_validate(
        {key: value for key, value in document.items() if key != "unexpected"}
    )
    with pytest.raises(ValidationError):
        definition.schema_version = "other"  # type: ignore[misc]


@pytest.mark.parametrize("reference", ["$inputs.session", 1])
def test_pipeline_outputs_must_reference_node_outputs(
    document: dict[str, object], reference: object
) -> None:
    document["outputs"] = {"bad": reference}

    with pytest.raises(ValidationError, match="node output"):
        PipelineDefinition.model_validate(document)


def test_canonical_definition_dump_is_json_domain_and_stable(
    document: dict[str, object],
) -> None:
    definition = PipelineDefinition.model_validate(document)

    dumped = canonical_definition_document(definition)

    assert dumped["schema"] == "provium.pipeline/v1"
    inputs = cast(dict[str, Any], dumped["inputs"])
    nodes = cast(dict[str, Any], dumped["nodes"])
    assert inputs["session"]["cardinality"] == {"minimum": 1, "maximum": 1}
    assert nodes["detect"]["setup"] == {"model": "$inputs.model"}
    assert PipelineDefinition.model_validate(dumped) == definition
    assert json.loads(json.dumps(dumped)) == dumped


def test_json_and_yaml_loaders_create_equivalent_definitions(
    document: dict[str, object],
) -> None:
    json_definition = load_pipeline_json(json.dumps(document), source="pipeline.json")
    yaml_definition = load_pipeline_yaml(
        """
schema: provium.pipeline/v1
pipeline:
  identifier: tngo.session-analysis
  version: "1"
  label: Session analysis
inputs:
  session: {artifact: tngo.SessionManifestV1, scope: record}
  model: {artifact: tngo.ModelV1, scope: shared}
nodes:
  detect:
    uses: tngo.DetectSessionV1
    setup: {model: $inputs.model}
    inputs: {session: $inputs.session}
    config: {confidence: 0.5}
  track:
    uses: tngo.TrackSessionV1
    inputs:
      detections: [$nodes.detect.detections, $inputs.session]
    cache: disabled
    metadata: {owner: vision}
outputs: {detections: $nodes.detect.detections}
""",
        source="pipeline.yaml",
    )

    assert json_definition == yaml_definition


def test_binding_coercion_accepts_preparsed_values() -> None:
    scalar = PipelineInputReference(name="session")  # type: ignore[arg-type]
    repeated = (scalar, NodeOutputReference(node="detect", output="image"))  # type: ignore[arg-type]

    node = PipelineNodeDefinition(
        uses="tngo.ExampleV1",
        setup={"scalar": scalar},
        inputs={"repeated": repeated},
    )

    assert node.setup["scalar"] is scalar
    assert node.inputs["repeated"] == repeated


@pytest.mark.parametrize(
    ("field", "value"),
    [("setup", []), ("inputs", 1), ("setup", {"bad": 1})],
)
def test_node_rejects_invalid_binding_shapes(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        PipelineNodeDefinition.model_validate({"uses": "tngo.ExampleV1", field: value})


def test_definition_accepts_a_preparsed_node_output(
    document: dict[str, object],
) -> None:
    reference = NodeOutputReference(node="detect", output="detections")  # type: ignore[arg-type]
    document["outputs"] = {"detections": reference}

    definition = PipelineDefinition.model_validate(document)

    assert definition.outputs["detections"] is reference


def test_node_rejects_a_mixed_preparsed_binding() -> None:
    reference = PipelineInputReference(name="session")  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        PipelineNodeDefinition(
            uses="tngo.ExampleV1",
            inputs={"bad": (reference, 1)},  # type: ignore[dict-item]
        )


def test_definition_rejects_nonmapping_outputs(document: dict[str, object]) -> None:
    document["outputs"] = []

    with pytest.raises(ValidationError):
        PipelineDefinition.model_validate(document)


@pytest.mark.parametrize(
    ("loader", "content", "source", "path"),
    [
        (load_pipeline_json, "{", "broken.json", "$"),
        (
            load_pipeline_json,
            '{"schema":"wrong"}',
            "invalid.json",
            "pipeline",
        ),
        (load_pipeline_yaml, "inputs: [", "broken.yaml", "$"),
    ],
)
def test_load_diagnostics_include_source_and_best_effort_path(
    loader: object, content: str, source: str, path: str
) -> None:
    with pytest.raises(PipelineDefinitionLoadError) as error:
        loader(content, source=source)  # type: ignore[operator]

    assert source in str(error.value)
    assert path in str(error.value)
