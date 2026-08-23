from dataclasses import replace

import pytest

from provium import (
    ProcedureCatalog,
    ProcedureContract,
    ProcedureInputs,
    ProcedureOutputs,
    input,
    output,
)
from provium.procedure.io import (
    ProcedureOptionalInputField,
    ProcedureRepeatedInputField,
)
from provium_pipeline import (
    PipelineInputName,
    PipelineNodeIdentifier,
    PipelineOutputName,
)
from provium_pipeline.compiler import (
    PipelineCompilationDiagnostic,
    PipelineCompilationError,
    PipelineCompiler,
    ProcedureCatalogCollection,
)
from provium_pipeline.definition import (
    NodeOutputReference,
    PipelineDefinition,
    PipelineInputReference,
)
from test.compiler.test_compiler_success import RESULT, compiler, definition
from test.definition.test_builder import REPEATED_TRANSFORM, SOURCE, TRANSFORM


class BoundedRepeatedContract(ProcedureContract[None]):
    class SetupInputs(ProcedureInputs):
        model = input(SOURCE)

    class Inputs(ProcedureInputs):
        source = ProcedureRepeatedInputField(SOURCE, minimum=2, maximum=2)

    class Outputs(ProcedureOutputs):
        destination = output(RESULT)


BOUNDED_REPEATED_TRANSFORM = replace(
    TRANSFORM,
    identifier="example.BoundedRepeatedTransformV1",
    contract=BoundedRepeatedContract,
)


class SourceConditionContract(ProcedureContract[None]):
    class Outputs(ProcedureOutputs):
        result = output(SOURCE)


SOURCE_CONDITION = replace(
    TRANSFORM,
    identifier="example.SourceConditionV1",
    contract=SourceConditionContract,
)


class OptionalDisconnectedContract(ProcedureContract[None]):
    class Inputs(ProcedureInputs):
        source = ProcedureOptionalInputField(SOURCE)

    class Outputs(ProcedureOutputs):
        result = output(SOURCE)


OPTIONAL_DISCONNECTED = replace(
    TRANSFORM,
    identifier="example.OptionalDisconnectedV1",
    contract=OptionalDisconnectedContract,
)


def test_compiler_reports_unknown_procedure_with_structured_location() -> None:
    source = definition()
    node = source.nodes["transform"].model_copy(update={"uses": "example.MissingV1"})
    invalid = source.model_copy(update={"nodes": {"transform": node}})

    with pytest.raises(PipelineCompilationError) as caught:
        compiler().compile(invalid)

    assert caught.value.diagnostics == (
        PipelineCompilationDiagnostic(
            code="unknown-procedure",
            path="nodes.transform.uses",
            message="procedure 'example.MissingV1' is not registered",
        ),
    )


def diagnostic_pairs(error: PipelineCompilationError) -> list[tuple[str, str]]:
    return [(item.code, item.path) for item in error.diagnostics]


def test_compiler_reports_missing_and_unknown_bindings() -> None:
    source = definition()
    node = source.nodes["transform"].model_copy(
        update={
            "setup": {},
            "inputs": {
                "unexpected": next(iter(source.nodes["transform"].inputs.values()))
            },
        }
    )
    invalid = source.model_copy(update={"nodes": {"transform": node}})

    with pytest.raises(PipelineCompilationError) as caught:
        compiler().compile(invalid)

    assert diagnostic_pairs(caught.value) == [
        ("missing-binding", "nodes.transform.setup.model"),
        ("missing-binding", "nodes.transform.inputs.source"),
        ("unknown-binding", "nodes.transform.inputs.unexpected"),
    ]


def test_compiler_reports_unknown_pipeline_input_reference() -> None:
    source = definition()
    node = source.nodes["transform"].model_copy(
        update={
            "inputs": {
                "source": PipelineInputReference(name=PipelineInputName("missing"))
            }
        }
    )
    invalid = source.model_copy(update={"nodes": {"transform": node}})

    with pytest.raises(PipelineCompilationError) as caught:
        compiler().compile(invalid)

    assert diagnostic_pairs(caught.value) == [
        ("unknown-pipeline-input", "nodes.transform.inputs.source")
    ]


def test_compiler_reports_artifact_mismatch() -> None:
    source = definition()
    pipeline_input = source.inputs["source"].model_copy(
        update={"artifact": RESULT.identifier}
    )
    invalid = source.model_copy(
        update={"inputs": {**source.inputs, "source": pipeline_input}}
    )

    with pytest.raises(PipelineCompilationError) as caught:
        compiler().compile(invalid)

    assert diagnostic_pairs(caught.value) == [
        ("artifact-mismatch", "nodes.transform.inputs.source")
    ]


def test_compiler_reports_unknown_pipeline_output_field() -> None:
    source = definition()
    invalid = source.model_copy(
        update={
            "outputs": {
                "result": NodeOutputReference(
                    node=next(iter(source.outputs.values())).node,
                    output=PipelineOutputName("missing"),
                )
            }
        }
    )

    with pytest.raises(PipelineCompilationError) as caught:
        compiler().compile(invalid)

    assert diagnostic_pairs(caught.value) == [("unknown-node-output", "outputs.result")]


def test_compiler_reports_unknown_artifact_definition() -> None:
    source = definition()
    pipeline_input = source.inputs["source"].model_copy(
        update={"artifact": "example.MissingV1"}
    )
    invalid = source.model_copy(
        update={"inputs": {**source.inputs, "source": pipeline_input}}
    )

    with pytest.raises(PipelineCompilationError) as caught:
        compiler().compile(invalid)

    assert diagnostic_pairs(caught.value) == [
        ("unknown-artifact", "inputs.source.artifact"),
        ("artifact-mismatch", "nodes.transform.inputs.source"),
    ]


def test_required_scalar_rejects_statically_optional_source() -> None:
    source = definition()
    optional = source.inputs["source"].model_copy(
        update={
            "cardinality": source.inputs["source"].cardinality.model_copy(
                update={"minimum": 0}
            )
        }
    )
    invalid = source.model_copy(
        update={"inputs": {**source.inputs, "source": optional}}
    )

    with pytest.raises(PipelineCompilationError) as caught:
        compiler().compile(invalid)

    assert diagnostic_pairs(caught.value) == [
        ("cardinality-mismatch", "nodes.transform.inputs.source")
    ]


def test_repeated_field_rejects_single_scalar_reference() -> None:
    source = definition()
    base_compiler = compiler()
    procedure_catalog = ProcedureCatalog()
    procedure_catalog.register(REPEATED_TRANSFORM)
    active_compiler = PipelineCompiler(
        artifact_catalogs=base_compiler.artifact_catalogs,
        procedure_catalogs=ProcedureCatalogCollection((procedure_catalog,)),
    )
    node = source.nodes["transform"].model_copy(
        update={"uses": REPEATED_TRANSFORM.identifier}
    )
    existing_output = next(iter(source.outputs.values()))
    output = NodeOutputReference(
        node=existing_output.node,
        output=PipelineOutputName("destination"),
    )
    invalid = source.model_copy(
        update={"nodes": {"transform": node}, "outputs": {"result": output}}
    )

    with pytest.raises(PipelineCompilationError) as caught:
        active_compiler.compile(invalid)

    assert diagnostic_pairs(caught.value) == [
        ("cardinality-mismatch", "nodes.transform.inputs.source")
    ]


def test_compiler_revalidates_pipeline_schema_before_compiling() -> None:
    invalid = definition().model_copy(update={"schema_version": "unsupported"})

    with pytest.raises(PipelineCompilationError) as caught:
        compiler().compile(invalid)

    assert diagnostic_pairs(caught.value) == [("invalid-definition", "schema")]


def test_compiler_reports_unknown_node_in_pipeline_output() -> None:
    source = definition()
    invalid = source.model_copy(
        update={
            "outputs": {
                "result": NodeOutputReference(
                    node=PipelineNodeIdentifier("missing"),
                    output=PipelineOutputName("result"),
                )
            }
        }
    )

    with pytest.raises(PipelineCompilationError) as caught:
        compiler().compile(invalid)

    assert diagnostic_pairs(caught.value) == [("unknown-node", "outputs.result")]


@pytest.mark.parametrize(
    ("reference", "code"),
    [
        (
            NodeOutputReference(
                node=PipelineNodeIdentifier("missing"),
                output=PipelineOutputName("result"),
            ),
            "unknown-node",
        ),
        (
            NodeOutputReference(
                node=PipelineNodeIdentifier("transform"),
                output=PipelineOutputName("missing"),
            ),
            "unknown-node-output",
        ),
    ],
)
def test_compiler_reports_invalid_node_output_bindings(
    reference: NodeOutputReference, code: str
) -> None:
    source = definition()
    node = source.nodes["transform"].model_copy(
        update={"inputs": {"source": reference}}
    )
    invalid = source.model_copy(update={"nodes": {"transform": node}})

    with pytest.raises(PipelineCompilationError) as caught:
        compiler().compile(invalid)

    assert diagnostic_pairs(caught.value) == [(code, "nodes.transform.inputs.source")]


def test_unknown_procedure_reference_does_not_add_cascading_output_errors() -> None:
    source = definition()
    missing = source.nodes["transform"].model_copy(update={"uses": "example.MissingV1"})
    transform = source.nodes["transform"].model_copy(
        update={
            "inputs": {
                "source": NodeOutputReference(
                    node=PipelineNodeIdentifier("missing"),
                    output=PipelineOutputName("result"),
                )
            }
        }
    )
    invalid = source.model_copy(
        update={"nodes": {"missing": missing, "transform": transform}}
    )

    with pytest.raises(PipelineCompilationError) as caught:
        compiler().compile(invalid)

    assert diagnostic_pairs(caught.value) == [
        ("unknown-procedure", "nodes.missing.uses")
    ]


def bounded_repeated_case(
    input_names: tuple[str, ...],
) -> tuple[PipelineCompiler, PipelineDefinition]:
    source = definition()
    procedure_catalog = ProcedureCatalog()
    procedure_catalog.register(BOUNDED_REPEATED_TRANSFORM)
    base_compiler = compiler()
    active_compiler = PipelineCompiler(
        artifact_catalogs=base_compiler.artifact_catalogs,
        procedure_catalogs=ProcedureCatalogCollection((procedure_catalog,)),
    )
    references = tuple(
        PipelineInputReference(name=PipelineInputName(name)) for name in input_names
    )
    node = source.nodes["transform"].model_copy(
        update={
            "uses": BOUNDED_REPEATED_TRANSFORM.identifier,
            "inputs": {"source": references},
        }
    )
    existing_output = next(iter(source.outputs.values()))
    output_reference = NodeOutputReference(
        node=existing_output.node,
        output=PipelineOutputName("destination"),
    )
    pipeline = source.model_copy(
        update={
            "nodes": {"transform": node},
            "outputs": {"result": output_reference},
        }
    )
    return active_compiler, pipeline


@pytest.mark.parametrize("input_names", [("source",), ("source", "model", "source")])
def test_ordered_binding_enforces_static_minimum_and_maximum(
    input_names: tuple[str, ...],
) -> None:
    active_compiler, pipeline = bounded_repeated_case(input_names)

    with pytest.raises(PipelineCompilationError) as caught:
        active_compiler.compile(pipeline)

    assert diagnostic_pairs(caught.value) == [
        ("cardinality-mismatch", "nodes.transform.inputs.source")
    ]


def test_ordered_binding_preserves_valid_reference_order() -> None:
    active_compiler, pipeline = bounded_repeated_case(("source", "model"))

    compiled = active_compiler.compile(pipeline)

    assert compiled.nodes[0].input_bindings[0].references == (
        "$inputs.source",
        "$inputs.model",
    )


def test_repeated_pipeline_input_satisfies_repeated_field() -> None:
    source = definition()
    procedure_catalog = ProcedureCatalog()
    procedure_catalog.register(REPEATED_TRANSFORM)
    base_compiler = compiler()
    active_compiler = PipelineCompiler(
        artifact_catalogs=base_compiler.artifact_catalogs,
        procedure_catalogs=ProcedureCatalogCollection((procedure_catalog,)),
    )
    repeated_input = source.inputs["source"].model_copy(
        update={
            "cardinality": source.inputs["source"].cardinality.model_copy(
                update={"minimum": 0, "maximum": None}
            )
        }
    )
    node = source.nodes["transform"].model_copy(
        update={"uses": REPEATED_TRANSFORM.identifier}
    )
    existing_output = next(iter(source.outputs.values()))
    output_reference = NodeOutputReference(
        node=existing_output.node,
        output=PipelineOutputName("destination"),
    )
    pipeline = source.model_copy(
        update={
            "inputs": {**source.inputs, "source": repeated_input},
            "nodes": {"transform": node},
            "outputs": {"result": output_reference},
        }
    )

    compiled = active_compiler.compile(pipeline)

    assert compiled.nodes[0].input_bindings[0].references == ("$inputs.source",)


def test_inputless_procedure_is_a_reachable_source_condition() -> None:
    source = definition()
    procedure_catalog = ProcedureCatalog()
    procedure_catalog.register(SOURCE_CONDITION)
    base_compiler = compiler()
    active_compiler = PipelineCompiler(
        artifact_catalogs=base_compiler.artifact_catalogs,
        procedure_catalogs=ProcedureCatalogCollection((procedure_catalog,)),
    )
    node = source.nodes["transform"].model_copy(
        update={"uses": SOURCE_CONDITION.identifier, "setup": {}, "inputs": {}}
    )
    pipeline = source.model_copy(update={"nodes": {"transform": node}})

    compiled = active_compiler.compile(pipeline)

    assert compiled.nodes[0].identifier == "transform"


def test_unbound_optional_input_does_not_create_a_source_condition() -> None:
    source = definition()
    procedure_catalog = ProcedureCatalog()
    procedure_catalog.register(OPTIONAL_DISCONNECTED)
    base_compiler = compiler()
    active_compiler = PipelineCompiler(
        artifact_catalogs=base_compiler.artifact_catalogs,
        procedure_catalogs=ProcedureCatalogCollection((procedure_catalog,)),
    )
    node = source.nodes["transform"].model_copy(
        update={"uses": OPTIONAL_DISCONNECTED.identifier, "setup": {}, "inputs": {}}
    )
    pipeline = source.model_copy(update={"nodes": {"transform": node}})

    with pytest.raises(PipelineCompilationError) as caught:
        active_compiler.compile(pipeline)

    assert diagnostic_pairs(caught.value) == [("unreachable-node", "nodes.transform")]
