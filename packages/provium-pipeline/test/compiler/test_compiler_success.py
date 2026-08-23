from dataclasses import replace

import pytest

from provium import (
    ArtifactCatalog,
    ProcedureCatalog,
    ProcedureContract,
    ProcedureInputs,
    ProcedureOutputs,
    input,
    output,
)
from provium_pipeline import PipelineNodeIdentifier, PipelineOutputName
from provium_pipeline.compiler import (
    ArtifactCatalogCollection,
    CompiledPipeline,
    PipelineCompiler,
    ProcedureCatalogCollection,
)
from provium_pipeline.definition import (
    NodeOutputReference,
    PipelineBuilder,
    PipelineDefinition,
    canonical_definition_document,
)
from test.definition.test_builder import RESULT, SOURCE, TRANSFORM


class PassthroughContract(ProcedureContract[None]):
    class SetupInputs(ProcedureInputs):
        model = input(SOURCE)

    class Inputs(ProcedureInputs):
        source = input(SOURCE)

    class Outputs(ProcedureOutputs):
        result = output(SOURCE)


PASSTHROUGH = replace(
    TRANSFORM,
    identifier="example.PassthroughV1",
    contract=PassthroughContract,
)


def compiler() -> PipelineCompiler:
    artifacts = ArtifactCatalog()
    artifacts.register(SOURCE)
    artifacts.register(RESULT)
    procedures = ProcedureCatalog()
    procedures.register(TRANSFORM)
    procedures.register(PASSTHROUGH)
    return PipelineCompiler(
        artifact_catalogs=ArtifactCatalogCollection((artifacts,)),
        procedure_catalogs=ProcedureCatalogCollection((procedures,)),
    )


def definition(
    *,
    identifier: str = "example-pipeline",
    version: str = "1",
    node_name: str = "transform",
    label: str | None = None,
) -> PipelineDefinition:
    builder = PipelineBuilder(
        identifier=identifier,
        version=version,
        label=label,
    )
    model = builder.record_input("model", SOURCE)
    source = builder.record_input("source", SOURCE)
    node = builder.node(
        node_name,
        TRANSFORM,
        setup={"model": model},
        inputs={"source": source},
    )
    builder.output("result", node.output("result"))
    return builder.build()


def test_compiler_emits_complete_immutable_plan() -> None:
    source = definition()

    compiled = compiler().compile(source)

    assert isinstance(compiled, CompiledPipeline)
    assert compiled.identifier == "example-pipeline"
    assert compiled.version == "1"
    assert compiled.definition_snapshot == canonical_definition_document(source)
    assert len(compiled.definition_digest) == 64
    assert len(compiled.semantic_digest) == 64
    assert [value.name for value in compiled.inputs] == ["model", "source"]
    assert [value.artifact_identifier for value in compiled.inputs] == [
        SOURCE.identifier,
        SOURCE.identifier,
    ]

    node = compiled.nodes[0]
    assert node.identifier == "transform"
    assert node.procedure_identifier == TRANSFORM.identifier
    assert (
        node.procedure_contract_digest == TRANSFORM.resolve_contract().metadata.digest
    )
    assert node.configuration_snapshot is None
    assert [(binding.field, binding.references) for binding in node.setup_bindings] == [
        ("model", ("$inputs.model",)),
    ]
    assert [(binding.field, binding.references) for binding in node.input_bindings] == [
        ("source", ("$inputs.source",)),
    ]
    assert len(node.preparation_contract_digest) == 64
    assert len(node.output_contract_digest) == 64
    assert node.cache_policy == "enabled"

    output_contract = node.output_contracts[0]
    assert output_contract.field == "result"
    assert output_contract.artifact_identifier == RESULT.identifier
    assert len(output_contract.digest) == 64

    output = compiled.outputs[0]
    assert (output.name, output.node, output.field) == (
        "result",
        "transform",
        "result",
    )
    assert output.artifact_identifier == RESULT.identifier
    assert output.contract_digest == output_contract.digest


def test_definition_digest_includes_display_metadata_but_semantic_digest_does_not() -> (
    None
):
    plain = compiler().compile(definition(label=None))
    labeled = compiler().compile(definition(label="Display label"))

    assert plain.definition_digest != labeled.definition_digest
    assert plain.semantic_digest == labeled.semantic_digest


def test_pipeline_identity_and_node_names_do_not_change_semantic_digest() -> None:
    first = compiler().compile(
        definition(identifier="first-pipeline", version="1", node_name="first-node")
    )
    second = compiler().compile(
        definition(identifier="second-pipeline", version="2", node_name="second-node")
    )

    assert first.definition_digest != second.definition_digest
    assert first.semantic_digest == second.semantic_digest


def test_compiler_orders_dependencies_and_preserves_node_output_references() -> None:
    source = definition()
    first = source.nodes["transform"].model_copy(
        update={"uses": PASSTHROUGH.identifier}
    )
    second = first.model_copy(
        update={
            "setup": {
                "model": NodeOutputReference(
                    node=PipelineNodeIdentifier("first"),
                    output=PipelineOutputName("result"),
                )
            },
            "inputs": {
                "source": NodeOutputReference(
                    node=PipelineNodeIdentifier("first"),
                    output=PipelineOutputName("result"),
                )
            },
        }
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

    assert [node.identifier for node in compiled.nodes] == ["first", "second"]
    assert compiled.nodes[1].input_bindings[0].references == (
        "$nodes.first.outputs.result",
    )


def test_compiler_rejects_cycles() -> None:
    source = definition()
    template = source.nodes["transform"].model_copy(
        update={"uses": PASSTHROUGH.identifier}
    )
    first = template.model_copy(
        update={
            "inputs": {
                "source": NodeOutputReference(
                    node=PipelineNodeIdentifier("second"),
                    output=PipelineOutputName("result"),
                )
            }
        }
    )
    second = template.model_copy(
        update={
            "inputs": {
                "source": NodeOutputReference(
                    node=PipelineNodeIdentifier("first"),
                    output=PipelineOutputName("result"),
                )
            }
        }
    )
    graph = source.model_copy(
        update={
            "nodes": {"first": first, "second": second},
            "outputs": {
                "result": NodeOutputReference(
                    node=PipelineNodeIdentifier("first"),
                    output=PipelineOutputName("result"),
                )
            },
        }
    )

    with pytest.raises(ValueError, match="pipeline graph contains a cycle"):
        compiler().compile(graph)


def independent_definition(
    transform_name: str, passthrough_name: str
) -> PipelineDefinition:
    source = definition()
    transform = source.nodes["transform"]
    passthrough = transform.model_copy(update={"uses": PASSTHROUGH.identifier})
    return source.model_copy(
        update={
            "nodes": {
                transform_name: transform,
                passthrough_name: passthrough,
            },
            "outputs": {
                "result": NodeOutputReference(
                    node=PipelineNodeIdentifier(transform_name),
                    output=PipelineOutputName("result"),
                ),
                "source": NodeOutputReference(
                    node=PipelineNodeIdentifier(passthrough_name),
                    output=PipelineOutputName("result"),
                ),
            },
        }
    )


def test_independent_node_renames_do_not_change_semantic_digest() -> None:
    first = compiler().compile(independent_definition("a-transform", "z-source"))
    renamed = compiler().compile(independent_definition("z-transform", "a-source"))

    assert first.definition_digest != renamed.definition_digest
    assert first.semantic_digest == renamed.semantic_digest
