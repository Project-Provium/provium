"""Positive static typing contracts for Provium's generic public API."""

from __future__ import annotations

from typing import ClassVar, assert_type

from provium import (
    Artifact,
    ArtifactCatalog,
    ArtifactDefinition,
    ArtifactHeader,
    ArtifactReadBinding,
    ArtifactReader,
    ArtifactWriteBinding,
    ArtifactWriter,
    PreparedProcedure,
    Procedure,
    ProcedureCatalog,
    ProcedureConfig,
    ProcedureContract,
    ProcedureDefinition,
    ProcedureExecutionResult,
    ProcedureExecutor,
    ProcedureInputs,
    ProcedureOutputs,
    ProcedureProcessContext,
    Session,
    StagedArtifact,
    input,
    optional_input,
    optional_output,
    output,
    repeated_input,
    stage_artifact,
    validate_procedure_configuration,
)


class ImageReader(ArtifactReader):
    pass


class ImageWriter(ArtifactWriter):
    pass


class ImageArtifact(Artifact[ImageReader, ImageWriter]):
    definition: ClassVar[ArtifactDefinition[ImageArtifact]]
    reader = ImageReader
    writer = ImageWriter


IMAGE_ARTIFACT: ArtifactDefinition[ImageArtifact] = ArtifactDefinition(
    identifier="example.ImageV1",
    target="example.artifacts:ImageArtifact",
    description="An image.",
)

ImageArtifact.definition = IMAGE_ARTIFACT

assert_type(ImageArtifact.bind_read("image.pa"), ArtifactReadBinding[ImageReader])
assert_type(ImageArtifact.bind_read("image.pa").open(), ImageReader)
assert_type(ImageArtifact.bind_write("image.pa"), ArtifactWriteBinding[ImageWriter])
assert_type(ImageArtifact.open("image.pa"), ImageReader)
assert_type(ImageArtifact.create("image.pa"), ImageWriter)
assert_type(IMAGE_ARTIFACT.resolve(), type[ImageArtifact])
assert_type(
    ArtifactCatalog().register(IMAGE_ARTIFACT), ArtifactDefinition[ImageArtifact]
)


def assert_staging_types(
    binding: ArtifactWriteBinding[ImageWriter],
    metadata: ArtifactHeader,
    owner: Session,
) -> None:
    assert_type(
        stage_artifact(binding, metadata, owner),
        StagedArtifact[ImageWriter],
    )


class Config(ProcedureConfig):
    threshold: float = 0.5


assert_type(
    validate_procedure_configuration("example.DetectV1", Config, {}),
    Config,
)


class Contract(ProcedureContract[Config]):
    configuration = Config

    class SetupInputs(ProcedureInputs):
        pass

    class Inputs(ProcedureInputs):
        image = input(IMAGE_ARTIFACT)
        previous = optional_input(IMAGE_ARTIFACT)
        images = repeated_input(IMAGE_ARTIFACT, minimum=1)

    class Outputs(ProcedureOutputs):
        image = output(IMAGE_ARTIFACT)
        preview = optional_output(IMAGE_ARTIFACT)


def assert_procedure_io_types(
    inputs: Contract.Inputs, outputs: Contract.Outputs
) -> None:
    assert_type(inputs.image, ArtifactReadBinding[ImageReader])
    assert_type(inputs.previous, ArtifactReadBinding[ImageReader] | None)
    assert_type(inputs.images, tuple[ArtifactReadBinding[ImageReader], ...])
    assert_type(outputs.image, ArtifactWriteBinding[ImageWriter])
    assert_type(outputs.preview, ArtifactWriteBinding[ImageWriter] | None)


class DetectProcedure(
    Procedure[Config, Contract.SetupInputs, Contract.Inputs, Contract.Outputs]
):
    definition: ClassVar[ProcedureDefinition[DetectProcedure]]

    def process(
        self,
        context: ProcedureProcessContext,
        configuration: Config,
        inputs: Contract.Inputs,
        outputs: Contract.Outputs,
    ) -> None:
        pass


DETECT_PROCEDURE: ProcedureDefinition[DetectProcedure] = ProcedureDefinition(
    identifier="example.DetectV1",
    target="example.procedures:DetectProcedure",
    label="Detect",
    description="Detect objects.",
    contract="example.contracts:Contract",
)

DetectProcedure.definition = DETECT_PROCEDURE

assert_type(DETECT_PROCEDURE.resolve(), type[DetectProcedure])
assert_type(
    ProcedureCatalog().register(DETECT_PROCEDURE),
    ProcedureDefinition[DetectProcedure],
)


def assert_prepared_procedure_types(
    procedure: DetectProcedure,
    configuration: Config,
    setup_inputs: Contract.SetupInputs,
    inputs: Contract.Inputs,
    outputs: Contract.Outputs,
) -> None:
    prepared = PreparedProcedure(procedure, configuration, setup_inputs)

    assert_type(
        prepared,
        PreparedProcedure[Config, Contract.Inputs, Contract.Outputs],
    )
    assert_type(prepared.configuration, Config)
    assert_type(
        prepared.execute(inputs=inputs, outputs=outputs),
        ProcedureExecutionResult,
    )


def assert_executor_mapping_types(
    input_binding: ArtifactReadBinding[ImageReader],
    output_binding: ArtifactWriteBinding[ImageWriter],
) -> None:
    assert_type(
        ProcedureExecutor().execute(
            DETECT_PROCEDURE,
            setup_inputs={},
            inputs={"image": input_binding, "images": [input_binding]},
            outputs={"image": output_binding},
        ),
        ProcedureExecutionResult,
    )
