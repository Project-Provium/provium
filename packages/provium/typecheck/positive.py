"""Positive static typing contracts for Provium's generic public API."""

from __future__ import annotations

from enum import Enum
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
    FilePath,
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
    input_field,
    multi_input_field,
    optional_input,
    optional_output,
    output,
    register_input_type,
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


class ValueInputs(ProcedureInputs):
    count = input_field(int)
    scale = input_field(float)
    source = input_field(FilePath)
    optional_count = input_field(int, optional=True)
    counts = multi_input_field(int, optional=True, minimum=2)
    artifact = input_field(IMAGE_ARTIFACT)
    optional_artifact = input_field(IMAGE_ARTIFACT, optional=True)
    artifacts = multi_input_field(IMAGE_ARTIFACT)


def assert_value_input_types(inputs: ValueInputs) -> None:
    assert_type(inputs.count, int)
    assert_type(inputs.scale, float)
    assert_type(inputs.source, FilePath)
    assert_type(inputs.optional_count, int | None)
    assert_type(inputs.counts, tuple[int, ...])
    assert_type(inputs.artifact, ArtifactReadBinding[ImageReader])
    assert_type(inputs.optional_artifact, ArtifactReadBinding[ImageReader] | None)
    assert_type(inputs.artifacts, tuple[ArtifactReadBinding[ImageReader], ...])


class ImportFormat(Enum):
    CSV = "csv"


register_input_type(
    ImportFormat,
    name="typecheck.import-format",
    decode=ImportFormat,
    encode=lambda value: value.value,
)


class CustomInputs(ProcedureInputs):
    format = input_field(ImportFormat)
    formats = multi_input_field(ImportFormat, optional=True)
    optional_format = input_field(ImportFormat, optional=True)


def assert_custom_input_types(inputs: CustomInputs) -> None:
    assert_type(inputs.format, ImportFormat)
    assert_type(inputs.formats, tuple[ImportFormat, ...])
    assert_type(inputs.optional_format, ImportFormat | None)
