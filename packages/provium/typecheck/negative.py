"""Guarded negative static typing contracts for Provium's generic API."""

from __future__ import annotations

from typing import ClassVar

from provium import (
    Artifact,
    ArtifactDefinition,
    ArtifactReadBinding,
    ArtifactReader,
    ArtifactWriter,
    PreparedProcedure,
    Procedure,
    ProcedureConfig,
    ProcedureContract,
    ProcedureDefinition,
    ProcedureExecutor,
    ProcedureInputs,
    ProcedureOutputs,
)


class Reader(ArtifactReader):
    pass


class Writer(ArtifactWriter):
    pass


class OtherReader(ArtifactReader):
    pass


class ExampleArtifact(Artifact[Reader, Writer]):
    definition: ClassVar[ArtifactDefinition[ExampleArtifact]]
    reader = Reader
    writer = Writer


class WrongReaderArtifact(Artifact[Reader, Writer]):
    definition: ClassVar[ArtifactDefinition[WrongReaderArtifact]]
    reader = OtherReader  # pyright: ignore[reportAssignmentType]
    writer = Writer


class OtherArtifact(Artifact[Reader, Writer]):
    definition: ClassVar[ArtifactDefinition[OtherArtifact]]
    reader = Reader
    writer = Writer


OTHER_ARTIFACT: ArtifactDefinition[OtherArtifact] = ArtifactDefinition(
    "example.OtherV1",
    "example.artifacts:OtherArtifact",
    "Another artifact.",
)

wrong_artifact_definition: ArtifactDefinition[ExampleArtifact] = OTHER_ARTIFACT  # pyright: ignore[reportAssignmentType]


class Config(ProcedureConfig):
    pass


class OtherConfig(ProcedureConfig):
    pass


class SetupInputs(ProcedureInputs):
    pass


class Inputs(ProcedureInputs):
    pass


class Outputs(ProcedureOutputs):
    pass


class OtherInputs(ProcedureInputs):
    pass


class OtherOutputs(ProcedureOutputs):
    pass


class Contract(ProcedureContract[Config]):
    pass


class WrongConfigContract(ProcedureContract[Config]):
    configuration = OtherConfig  # pyright: ignore[reportAssignmentType]


class ExampleProcedure(Procedure[Config, SetupInputs, Inputs, Outputs]):
    definition: ClassVar[ProcedureDefinition[ExampleProcedure]]


class OtherProcedure(Procedure[Config, SetupInputs, Inputs, Outputs]):
    definition: ClassVar[ProcedureDefinition[OtherProcedure]]


class WrongIOProcedure(
    Procedure[
        Config,
        Outputs,  # pyright: ignore[reportInvalidTypeArguments]
        Inputs,
        Outputs,
    ]
):
    pass


OTHER_PROCEDURE: ProcedureDefinition[OtherProcedure] = ProcedureDefinition(
    "example.OtherV1",
    "example.procedures:OtherProcedure",
    "Other",
    None,
    Contract,
)

wrong_procedure_definition: ProcedureDefinition[ExampleProcedure] = OTHER_PROCEDURE  # pyright: ignore[reportAssignmentType]


def reject_mismatched_prepared_types(
    procedure: ExampleProcedure,
    configuration: Config,
    other_configuration: OtherConfig,
    setup_inputs: SetupInputs,
    inputs: Inputs,
    outputs: Outputs,
    other_inputs: OtherInputs,
    other_outputs: OtherOutputs,
) -> None:
    prepared = PreparedProcedure(procedure, configuration, setup_inputs)

    PreparedProcedure(procedure, other_configuration, setup_inputs)  # pyright: ignore[reportArgumentType]
    prepared.execute(inputs=other_inputs, outputs=outputs)  # pyright: ignore[reportArgumentType]
    prepared.execute(inputs=inputs, outputs=other_outputs)  # pyright: ignore[reportArgumentType]


def reject_reversed_executor_binding_directions(
    input_binding: ArtifactReadBinding[Reader],
) -> None:
    ProcedureExecutor().execute(
        OTHER_PROCEDURE,
        outputs={"output": input_binding},  # pyright: ignore[reportArgumentType]
    )
