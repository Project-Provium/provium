"""Typed programmatic pipeline builder contracts."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from provium import (
    ArtifactDefinition,
    ProcedureContract,
    ProcedureDefinition,
    ProcedureInputs,
    ProcedureOutputs,
    input,
    output,
)
from provium.procedure.io import ProcedureRepeatedInputField
from provium_pipeline.definition import (
    NodeOutputHandle,
    PipelineBuilder,
    PipelineInputHandle,
    PipelineRepeatedInputHandle,
    canonical_definition_document,
    load_pipeline_yaml,
)

SOURCE: ArtifactDefinition[Any] = ArtifactDefinition(
    identifier="example.SourceV1",
    target="example.artifact:SourceV1",
    description="source",
)
RESULT: ArtifactDefinition[Any] = ArtifactDefinition(
    identifier="example.ResultV1",
    target="example.artifact:ResultV1",
    description="result",
)


class TransformContract(ProcedureContract[None]):
    class SetupInputs(ProcedureInputs):
        model = input(SOURCE)

    class Inputs(ProcedureInputs):
        source = input(SOURCE)

    class Outputs(ProcedureOutputs):
        result = output(RESULT)


TRANSFORM: ProcedureDefinition[Any] = ProcedureDefinition(
    identifier="example.TransformV1",
    target="implementation_that_must_not_import:TransformV1",
    label="Transform",
    description=None,
    contract=TransformContract,
)


def test_builder_produces_the_same_definition_as_yaml() -> None:
    builder = PipelineBuilder(
        identifier="example.pipeline",
        version="1",
        label="Example",
    )
    source = builder.record_input("source", SOURCE)
    model = builder.shared_input("model", SOURCE)
    transform = builder.node(
        "transform",
        TRANSFORM,
        setup={"model": model},
        inputs={"source": source},
        config={"threshold": 0.5},
    )
    result = transform.output("result")
    builder.output("result", result)

    definition = builder.build()
    from_yaml = load_pipeline_yaml(
        """
schema: provium.pipeline/v1
pipeline:
  identifier: example.pipeline
  version: "1"
  label: Example
inputs:
  source: {artifact: example.SourceV1, scope: record}
  model: {artifact: example.SourceV1, scope: shared}
nodes:
  transform:
    uses: example.TransformV1
    setup: {model: $inputs.model}
    inputs: {source: $inputs.source}
    config: {threshold: 0.5}
outputs: {result: $nodes.transform.result}
"""
    )

    assert canonical_definition_document(definition) == canonical_definition_document(
        from_yaml
    )
    assert isinstance(source, PipelineInputHandle)
    assert isinstance(result, NodeOutputHandle)
    assert source.artifact is SOURCE
    assert result.artifact is RESULT


def test_builder_supports_optional_and_repeated_inputs() -> None:
    builder = PipelineBuilder(identifier="example.pipeline", version="1")

    optional = builder.record_input("optional", SOURCE, optional=True)
    repeated = builder.repeated_record_input("images", SOURCE, minimum=0)

    assert isinstance(optional, PipelineInputHandle)
    assert isinstance(repeated, PipelineRepeatedInputHandle)
    assert str(repeated.reference) == "$inputs.images"
    definition = builder.build()
    assert definition.inputs["optional"].cardinality.minimum == 0
    assert definition.inputs["optional"].cardinality.maximum == 1
    assert definition.inputs["images"].cardinality.maximum is None


@pytest.mark.parametrize("kind", ["input", "node", "output"])
def test_builder_rejects_duplicate_names(kind: str) -> None:
    builder = PipelineBuilder(identifier="example.pipeline", version="1")
    source = builder.record_input("source", SOURCE)

    with pytest.raises(ValueError, match="already declared"):
        if kind == "input":
            builder.record_input("source", SOURCE)
        elif kind == "node":
            builder.node(
                "same", TRANSFORM, setup={"model": source}, inputs={"source": source}
            )
            builder.node(
                "same", TRANSFORM, setup={"model": source}, inputs={"source": source}
            )
        else:
            node = builder.node(
                "transform",
                TRANSFORM,
                setup={"model": source},
                inputs={"source": source},
            )
            result = node.output("result")
            builder.output("same", result)
            builder.output("same", result)


def test_builder_rejects_handles_from_another_builder() -> None:
    first = PipelineBuilder(identifier="example.first", version="1")
    second = PipelineBuilder(identifier="example.second", version="1")
    foreign = first.record_input("source", SOURCE)

    with pytest.raises(ValueError, match="different PipelineBuilder"):
        second.node(
            "transform",
            TRANSFORM,
            setup={"model": foreign},
            inputs={"source": foreign},
        )


def test_node_output_rejects_unknown_contract_field_without_resolving_target() -> None:
    builder = PipelineBuilder(identifier="example.pipeline", version="1")
    source = builder.record_input("source", SOURCE)
    node = builder.node(
        "transform",
        TRANSFORM,
        setup={"model": source},
        inputs={"source": source},
    )

    with pytest.raises(ValueError, match="has no output field 'missing'"):
        node.output("missing")


class RepeatedTransformContract(ProcedureContract[None]):
    class SetupInputs(ProcedureInputs):
        model = input(SOURCE)

    class Inputs(ProcedureInputs):
        source = ProcedureRepeatedInputField(SOURCE)

    class Outputs(ProcedureOutputs):
        destination = output(RESULT)


REPEATED_TRANSFORM: ProcedureDefinition[Any] = replace(
    TRANSFORM, contract=RepeatedTransformContract
)


def test_repeated_binding_preserves_handle_order() -> None:
    builder = PipelineBuilder(identifier="example.pipeline", version="1")
    first = builder.record_input("first", SOURCE)
    second = builder.record_input("second", SOURCE)
    node = builder.node(
        "transform",
        REPEATED_TRANSFORM,
        setup={"model": first},
        inputs={"source": [second, first]},
    )

    definition = builder.build()
    binding = definition.nodes["transform"].inputs["source"]
    assert isinstance(binding, tuple)
    assert tuple(str(reference) for reference in binding) == (
        "$inputs.second",
        "$inputs.first",
    )
    assert node.procedure is REPEATED_TRANSFORM
