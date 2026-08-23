from typing import Any

import pytest

from provium import (
    ArtifactDefinition,
    ProcedureContract,
    ProcedureDefinition,
    ProcedureInputs,
    ProcedureOutputs,
)
from provium.procedure.io import ProcedureInputField, ProcedureOutputField
from provium_pipeline.definition import PipelineBuilder

SOURCE: ArtifactDefinition[Any] = ArtifactDefinition(
    identifier="test-source",
    target="test_pipeline.artifacts:SourceArtifact",
    description="Test artifact.",
)
OTHER: ArtifactDefinition[Any] = ArtifactDefinition(
    identifier="test-other",
    target="test_pipeline.artifacts:OtherArtifact",
    description="Test artifact.",
)
RESULT: ArtifactDefinition[Any] = ArtifactDefinition(
    identifier="test-result",
    target="test_pipeline.artifacts:ResultArtifact",
    description="Test artifact.",
)


class ValidationContract(ProcedureContract[None]):
    class SetupInputs(ProcedureInputs):
        setup_source = ProcedureInputField(SOURCE)

    class Inputs(ProcedureInputs):
        source = ProcedureInputField(SOURCE)

    class Outputs(ProcedureOutputs):
        result = ProcedureOutputField(RESULT)


VALIDATION_PROCEDURE: ProcedureDefinition[Any] = ProcedureDefinition(
    identifier="test-validate",
    target="missing_module:MissingProcedure",
    label="Validate",
    description="Test procedure.",
    contract=ValidationContract,
)


def make_builder() -> PipelineBuilder:
    return PipelineBuilder(identifier="test-pipeline", version="1")


@pytest.mark.parametrize(
    ("binding_group", "field"),
    [("setup", "unknown"), ("inputs", "unknown")],
)
def test_node_rejects_unknown_contract_binding_fields(
    binding_group: str, field: str
) -> None:
    builder = make_builder()
    source = builder.record_input("source", SOURCE)

    group_label = binding_group.removesuffix("s")
    with pytest.raises(ValueError, match=f"unknown {group_label} field {field!r}"):
        builder.node(
            "node",
            VALIDATION_PROCEDURE,
            setup=(
                {field: source}
                if binding_group == "setup"
                else {"setup_source": source}
            ),
            inputs=(
                {field: source} if binding_group == "inputs" else {"source": source}
            ),
        )


def test_node_rejects_missing_required_contract_bindings() -> None:
    builder = make_builder()
    source = builder.record_input("source", SOURCE)

    with pytest.raises(ValueError, match="missing required setup field 'setup_source'"):
        builder.node("node", VALIDATION_PROCEDURE, inputs={"source": source})


def test_node_rejects_artifact_mismatch() -> None:
    builder = make_builder()
    source = builder.record_input("source", SOURCE)
    other = builder.record_input("other", OTHER)

    with pytest.raises(
        ValueError, match="input field 'source'.*test-source.*test-other"
    ):
        builder.node(
            "node",
            VALIDATION_PROCEDURE,
            setup={"setup_source": source},
            inputs={"source": other},
        )


def test_node_rejects_repeated_handle_for_scalar_field() -> None:
    builder = make_builder()
    source = builder.record_input("source", SOURCE)
    repeated = builder.repeated_record_input("sources", SOURCE)

    with pytest.raises(ValueError, match="input field 'source'.*scalar"):
        builder.node(
            "node",
            VALIDATION_PROCEDURE,
            setup={"setup_source": source},
            inputs={"source": repeated},
        )
