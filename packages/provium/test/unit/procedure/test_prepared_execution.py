"""Tests for prepared procedure artifact execution."""

import hashlib
from pathlib import Path

import pytest
from pydantic import Field

from provium import (
    Artifact,
    ArtifactDefinition,
    ArtifactHeader,
    ArtifactLineage,
    ArtifactReadBinding,
    ArtifactReader,
    ArtifactRecord,
    ArtifactReference,
    ArtifactWriteBinding,
    ArtifactWriter,
    PreparedProcedure,
    Procedure,
    ProcedureConfig,
    ProcedureContract,
    ProcedureDefinition,
    ProcedureExecutionRecord,
    ProcedureExecutionResult,
    ProcedureInputs,
    ProcedureOutputs,
    ProcedureProcessContext,
    ProcedureRecord,
    ProcedureSetupContext,
    current_session,
    decode_header,
    encode_header,
    input,
    optional_input,
    optional_output,
    output,
    repeated_input,
    session,
)


class BytesReader(ArtifactReader):
    def read(self) -> bytes:
        return self.body.read()


class BytesWriter(ArtifactWriter):
    def write(self, value: bytes) -> int:
        return self.body.write(value)


BYTES = ArtifactDefinition(
    "example.PreparedBytesV1",
    f"{__name__}:BytesArtifact",
    "Prepared procedure bytes.",
)


class BytesArtifact(Artifact[BytesReader, BytesWriter]):
    definition = BYTES
    reader = BytesReader
    writer = BytesWriter


def write_source(path: Path, identity: str, body: bytes) -> ArtifactRecord:
    reference = ArtifactReference(identity, BYTES.identifier)
    digest = hashlib.sha256(body).hexdigest()
    execution = ProcedureExecutionRecord(
        f"{identity}-execution",
        ProcedureRecord("example.SourceV1", "source-contract"),
        outputs=(reference,),
    )
    record = ArtifactRecord(reference, digest, execution.identity)
    lineage = ArtifactLineage.for_execution(execution, (record,))
    header = ArtifactHeader.create(
        artifact_identifier=reference.artifact_identifier,
        artifact_identity=reference.identity,
        body_length=len(body),
        body_digest=digest,
        lineage=lineage,
    )
    path.write_bytes(encode_header(header) + body)
    return record


class Contract(ProcedureContract[None]):
    class Inputs(ProcedureInputs):
        pass

    class Outputs(ProcedureOutputs):
        result = output(BYTES)


DEFINITION = ProcedureDefinition(
    "example.PreparedOutputV1",
    f"{__name__}:WritingProcedure",
    "Write output",
    None,
    Contract,
)


class WritingProcedure(
    Procedure[None, Contract.SetupInputs, Contract.Inputs, Contract.Outputs]
):
    definition = DEFINITION

    def process(
        self,
        context: ProcedureProcessContext,
        configuration: None,
        inputs: Contract.Inputs,
        outputs: Contract.Outputs,
    ) -> None:
        with outputs.result.open() as writer:
            writer.write(b"result")


def prepare(
    procedure: WritingProcedure | None = None,
) -> PreparedProcedure[None, Contract.Inputs, Contract.Outputs]:
    return PreparedProcedure(
        WritingProcedure() if procedure is None else procedure,
        None,
        Contract.SetupInputs._from_bindings({}),
    )


def test_prepared_execution_stages_and_publishes_declared_output(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "result.pa"
    binding = BytesArtifact.bind_write(destination)

    with session():
        prepared = prepare()
        result = prepared.execute(
            inputs=Contract.Inputs._from_bindings({}),
            outputs=Contract.Outputs._from_bindings({"result": binding}),
        )

    assert isinstance(result, ProcedureExecutionResult)
    assert result.procedure.name == DEFINITION.identifier
    assert result.inputs == ()
    assert result.outputs == {"result": result.outputs["result"]}
    output_result = result.output_results["result"]
    assert output_result.produced
    assert output_result.path == destination.resolve()
    assert output_result.reference == result.outputs["result"]
    assert output_result.inspection is not None
    assert output_result.inspection.path == destination.resolve()
    assert (
        output_result.inspection.artifact_identity
        == result.outputs["result"].identity
    )
    assert result.lineage is not None
    data = destination.read_bytes()
    header = decode_header(data)
    record = next(iter(header.lineage.artifacts.values()))
    execution = header.lineage.producing_execution(record.reference)
    assert execution.procedure.name == DEFINITION.identifier
    assert execution.procedure.version == Contract.metadata.digest
    assert execution.procedure.config is None
    assert execution.procedure.config_codec is None
    assert result.identity == execution.identity
    assert result.outputs["result"] in execution.outputs
    assert result.lineage == header.lineage
    assert data[header.body_offset :] == b"result"

    with pytest.raises(TypeError):
        result.outputs["other"] = result.outputs["result"]  # type: ignore[index]


def test_prepared_execution_validates_public_input_mappings(
    tmp_path: Path,
) -> None:
    prepared = prepare()

    with pytest.raises(
        TypeError,
        match=(
            "invalid processing inputs for procedure example.PreparedOutputV1: "
            "unknown field: unexpected"
        ),
    ):
        prepared.execute(inputs={"unexpected": object()}, outputs={})  # type: ignore[dict-item]

    result = prepared.execute(
        inputs={},
        outputs={"result": BytesArtifact.bind_write(tmp_path / "result.pa")},
    )
    prepared.close()

    assert "result" in result.outputs


def test_prepared_execution_validates_public_output_mappings() -> None:
    prepared = prepare()

    with pytest.raises(
        TypeError,
        match=(
            "invalid outputs for procedure example.PreparedOutputV1: "
            "missing required field: result"
        ),
    ):
        prepared.execute(inputs={}, outputs={})

    prepared.close()


def test_callback_rejects_an_equal_but_undeclared_output_binding(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "result.pa"

    class UndeclaredBindingProcedure(WritingProcedure):
        def process(
            self,
            context: ProcedureProcessContext,
            configuration: None,
            inputs: Contract.Inputs,
            outputs: Contract.Outputs,
        ) -> None:
            BytesArtifact.bind_write(destination).open()

    with session(), pytest.raises(RuntimeError, match="provided input/output bindings"):
        prepare(UndeclaredBindingProcedure()).execute(
            inputs=Contract.Inputs._from_bindings({}),
            outputs=Contract.Outputs._from_bindings(
                {"result": BytesArtifact.bind_write(destination)}
            ),
        )

    assert not destination.exists()


@pytest.mark.parametrize(
    "operation",
    ["bind_read", "bind_write", "open", "create", "read_binding", "write_binding"],
)
def test_declarative_callback_rejects_manual_artifact_access(
    tmp_path: Path,
    operation: str,
) -> None:
    arbitrary = tmp_path / "arbitrary.pa"

    class ManualArtifactProcedure(WritingProcedure):
        def process(
            self,
            context: ProcedureProcessContext,
            configuration: None,
            inputs: Contract.Inputs,
            outputs: Contract.Outputs,
        ) -> None:
            if operation == "bind_read":
                BytesArtifact.bind_read(arbitrary)
            elif operation == "bind_write":
                BytesArtifact.bind_write(arbitrary)
            elif operation == "open":
                BytesArtifact.open(arbitrary)
            elif operation == "create":
                BytesArtifact.create(arbitrary)
            elif operation == "read_binding":
                ArtifactReadBinding(BytesArtifact, arbitrary)
            else:
                ArtifactWriteBinding(BytesArtifact, arbitrary)

    destination = tmp_path / "result.pa"
    with session(), pytest.raises(RuntimeError, match="provided input/output bindings"):
        prepare(ManualArtifactProcedure()).execute(
            inputs=Contract.Inputs._from_bindings({}),
            outputs=Contract.Outputs._from_bindings(
                {"result": BytesArtifact.bind_write(destination)}
            ),
        )

    assert not destination.exists()


@pytest.mark.parametrize("operation", ["open", "create", "session_open"])
def test_declarative_callback_rejects_prebound_undeclared_artifacts(
    tmp_path: Path,
    operation: str,
) -> None:
    source = tmp_path / "source.pa"
    write_source(source, "source", b"source")
    arbitrary = (
        BytesArtifact.bind_read(source)
        if operation in ("open", "session_open")
        else BytesArtifact.bind_write(tmp_path / "arbitrary.pa")
    )

    class UndeclaredArtifactProcedure(WritingProcedure):
        def process(
            self,
            context: ProcedureProcessContext,
            configuration: None,
            inputs: Contract.Inputs,
            outputs: Contract.Outputs,
        ) -> None:
            if operation == "session_open":
                active = current_session()
                assert active is not None
                assert isinstance(arbitrary, ArtifactReadBinding)
                active.open_artifact(arbitrary)
            else:
                arbitrary.open()

    destination = tmp_path / "result.pa"
    with session(), pytest.raises(RuntimeError, match="not declared"):
        prepare(UndeclaredArtifactProcedure()).execute(
            inputs=Contract.Inputs._from_bindings({}),
            outputs=Contract.Outputs._from_bindings(
                {"result": BytesArtifact.bind_write(destination)}
            ),
        )

    assert not destination.exists()


def test_processing_failure_preserves_existing_output(tmp_path: Path) -> None:
    destination = tmp_path / "result.pa"
    destination.write_bytes(b"existing")

    class FailingProcedure(WritingProcedure):
        def process(
            self,
            context: ProcedureProcessContext,
            configuration: None,
            inputs: Contract.Inputs,
            outputs: Contract.Outputs,
        ) -> None:
            outputs.result.open().write(b"replacement")
            raise ValueError("processing failed")

    with session(), pytest.raises(ValueError, match="processing failed"):
        prepare(FailingProcedure()).execute(
            inputs=Contract.Inputs._from_bindings({}),
            outputs=Contract.Outputs._from_bindings(
                {"result": BytesArtifact.bind_write(destination)}
            ),
        )

    assert destination.read_bytes() == b"existing"


def test_output_binding_cannot_open_outside_a_procedure_callback(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="active procedure callback"):
        BytesArtifact.bind_write(tmp_path / "result.pa").open()


def test_unregistered_procedure_cannot_produce_an_artifact(tmp_path: Path) -> None:
    class UnregisteredProcedure(
        Procedure[None, ProcedureInputs, ProcedureInputs, Contract.Outputs]
    ):
        def process(
            self,
            context: ProcedureProcessContext,
            configuration: None,
            inputs: ProcedureInputs,
            outputs: Contract.Outputs,
        ) -> None:
            pass

    prepared = PreparedProcedure(
        UnregisteredProcedure(),
        None,
        ProcedureInputs._from_bindings({}),
    )

    with pytest.raises(TypeError, match="must declare a definition"):
        prepared.execute(
            inputs=ProcedureInputs._from_bindings({}),
            outputs=Contract.Outputs._from_bindings(
                {"result": BytesArtifact.bind_write(tmp_path / "result.pa")}
            ),
        )


def test_prepared_rejects_duplicate_destinations_with_field_context(
    tmp_path: Path,
) -> None:
    class TwoOutputContract(ProcedureContract[None]):
        class Outputs(ProcedureOutputs):
            first = output(BYTES)
            second = output(BYTES)
            omitted = optional_output(BYTES)

    definition = ProcedureDefinition(
        "example.PreparedDuplicatesV1",
        f"{__name__}:TwoOutputProcedure",
        "Two outputs",
        None,
        TwoOutputContract,
    )
    processed: list[bool] = []

    class TwoOutputProcedure(
        Procedure[
            None,
            TwoOutputContract.SetupInputs,
            TwoOutputContract.Inputs,
            TwoOutputContract.Outputs,
        ]
    ):
        def process(
            self,
            context: ProcedureProcessContext,
            configuration: None,
            inputs: TwoOutputContract.Inputs,
            outputs: TwoOutputContract.Outputs,
        ) -> None:
            processed.append(True)

    TwoOutputProcedure.definition = definition
    destination = tmp_path / "same.pa"
    prepared = PreparedProcedure(
        TwoOutputProcedure(),
        None,
        TwoOutputContract.SetupInputs._from_bindings({}),
    )

    with pytest.raises(
        ValueError,
        match=(
            "invalid outputs for procedure example.PreparedDuplicatesV1: "
            "fields first and second use the same destination"
        ),
    ):
        prepared.execute(
            inputs=TwoOutputContract.Inputs._from_bindings({}),
            outputs=TwoOutputContract.Outputs._from_bindings(
                {
                    "first": BytesArtifact.bind_write(destination),
                    "second": BytesArtifact.bind_write(destination),
                }
            ),
        )

    assert processed == []


def test_prepared_execution_records_canonical_configuration(tmp_path: Path) -> None:
    class Config(ProcedureConfig):
        internal_value: int = Field(alias="external-value")

    class ConfiguredContract(ProcedureContract[Config]):
        configuration = Config

        class Inputs(ProcedureInputs):
            pass

        class Outputs(ProcedureOutputs):
            result = output(BYTES)

    definition = ProcedureDefinition(
        "example.ConfiguredOutputV1",
        f"{__name__}:ConfiguredProcedure",
        "Write configured output",
        None,
        ConfiguredContract,
    )

    class ConfiguredProcedure(
        Procedure[
            Config,
            ConfiguredContract.SetupInputs,
            ConfiguredContract.Inputs,
            ConfiguredContract.Outputs,
        ]
    ):
        def process(
            self,
            context: ProcedureProcessContext,
            configuration: Config,
            inputs: ConfiguredContract.Inputs,
            outputs: ConfiguredContract.Outputs,
        ) -> None:
            outputs.result.open().write(str(configuration.internal_value).encode())

    ConfiguredProcedure.definition = definition
    destination = tmp_path / "configured.pa"
    prepared = PreparedProcedure(
        ConfiguredProcedure(),
        Config(**{"external-value": 7}),
        ConfiguredContract.SetupInputs._from_bindings({}),
    )

    with session():
        prepared.execute(
            inputs=ConfiguredContract.Inputs._from_bindings({}),
            outputs=ConfiguredContract.Outputs._from_bindings(
                {"result": BytesArtifact.bind_write(destination)}
            ),
        )

    header = decode_header(destination.read_bytes())
    execution = next(iter(header.lineage.executions.values()))
    assert execution.procedure.config == {"external-value": 7}
    assert execution.procedure.config_codec == "pydantic-v2"


def test_no_output_callback_still_rejects_undeclared_write_binding(
    tmp_path: Path,
) -> None:
    class EmptyOutputs(ProcedureOutputs):
        pass

    class UndeclaredOutputProcedure(
        Procedure[None, ProcedureInputs, ProcedureInputs, EmptyOutputs]
    ):
        def process(
            self,
            context: ProcedureProcessContext,
            configuration: None,
            inputs: ProcedureInputs,
            outputs: EmptyOutputs,
        ) -> None:
            BytesArtifact.bind_write(tmp_path / "undeclared.pa").open()

    prepared = PreparedProcedure(
        UndeclaredOutputProcedure(),
        None,
        ProcedureInputs._from_bindings({}),
    )

    with pytest.raises(RuntimeError, match="provided input/output bindings"):
        prepared.execute(
            inputs=ProcedureInputs._from_bindings({}),
            outputs=EmptyOutputs._from_bindings({}),
        )


def test_output_free_executions_return_fresh_immutable_metadata() -> None:
    class EmptyOutputs(ProcedureOutputs):
        pass

    class EmptyProcedure(
        Procedure[None, ProcedureInputs, ProcedureInputs, EmptyOutputs]
    ):
        def process(
            self,
            context: ProcedureProcessContext,
            configuration: None,
            inputs: ProcedureInputs,
            outputs: EmptyOutputs,
        ) -> None:
            pass

    prepared = PreparedProcedure(
        EmptyProcedure(),
        None,
        ProcedureInputs._from_bindings({}),
    )
    results = [
        prepared.execute(
            inputs=ProcedureInputs._from_bindings({}),
            outputs=EmptyOutputs._from_bindings({}),
        )
        for _ in range(2)
    ]

    assert all(isinstance(result, ProcedureExecutionResult) for result in results)
    assert results[0].identity != results[1].identity
    assert results[0].inputs == ()
    assert results[0].outputs == {}
    assert results[0].lineage == ArtifactLineage()


class InputContract(ProcedureContract[None]):
    class Inputs(ProcedureInputs):
        source = input(BYTES)

    class Outputs(ProcedureOutputs):
        result = output(BYTES)


INPUT_DEFINITION = ProcedureDefinition(
    "example.PreparedInputV1",
    f"{__name__}:InputProcedure",
    "Consume input",
    None,
    InputContract,
)


class InputProcedure(
    Procedure[
        None,
        InputContract.SetupInputs,
        InputContract.Inputs,
        InputContract.Outputs,
    ]
):
    definition = INPUT_DEFINITION

    def process(
        self,
        context: ProcedureProcessContext,
        configuration: None,
        inputs: InputContract.Inputs,
        outputs: InputContract.Outputs,
    ) -> None:
        with inputs.source.open() as reader:
            value = reader.read()
        outputs.result.open().write(value)


def prepare_input(
    procedure: InputProcedure | None = None,
) -> PreparedProcedure[None, InputContract.Inputs, InputContract.Outputs]:
    return PreparedProcedure(
        InputProcedure() if procedure is None else procedure,
        None,
        InputContract.SetupInputs._from_bindings({}),
    )


def test_declared_input_is_authorized_and_registered_before_callback(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.pa"
    source_record = write_source(source_path, "source", b"source body")
    destination = tmp_path / "result.pa"

    with session():
        prepare_input().execute(
            inputs=InputContract.Inputs._from_bindings(
                {"source": BytesArtifact.bind_read(source_path)}
            ),
            outputs=InputContract.Outputs._from_bindings(
                {"result": BytesArtifact.bind_write(destination)}
            ),
        )

    header = decode_header(destination.read_bytes())
    output_record = header.lineage.artifact(
        ArtifactReference(header.artifact_identity, header.artifact_identifier)
    )
    execution = header.lineage.producing_execution(output_record.reference)
    assert execution.inputs == (source_record.reference,)
    assert destination.read_bytes()[header.body_offset :] == b"source body"


def test_unopened_declared_input_is_still_registered(tmp_path: Path) -> None:
    source_path = tmp_path / "source.pa"
    source_record = write_source(source_path, "unused-source", b"unused")
    destination = tmp_path / "result.pa"

    class IgnoringProcedure(InputProcedure):
        def process(
            self,
            context: ProcedureProcessContext,
            configuration: None,
            inputs: InputContract.Inputs,
            outputs: InputContract.Outputs,
        ) -> None:
            outputs.result.open().write(b"result")

    with session():
        prepare_input(IgnoringProcedure()).execute(
            inputs=InputContract.Inputs._from_bindings(
                {"source": BytesArtifact.bind_read(source_path)}
            ),
            outputs=InputContract.Outputs._from_bindings(
                {"result": BytesArtifact.bind_write(destination)}
            ),
        )

    header = decode_header(destination.read_bytes())
    execution = next(
        execution
        for execution in header.lineage.executions.values()
        if execution.procedure.name == INPUT_DEFINITION.identifier
    )
    assert execution.inputs == (source_record.reference,)


def test_callback_rejects_equal_but_undeclared_input_binding(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.pa"
    write_source(source_path, "source", b"source")
    destination = tmp_path / "result.pa"

    class UndeclaredInputProcedure(InputProcedure):
        def process(
            self,
            context: ProcedureProcessContext,
            configuration: None,
            inputs: InputContract.Inputs,
            outputs: InputContract.Outputs,
        ) -> None:
            BytesArtifact.bind_read(source_path).open()

    with session(), pytest.raises(RuntimeError, match="provided input/output bindings"):
        prepare_input(UndeclaredInputProcedure()).execute(
            inputs=InputContract.Inputs._from_bindings(
                {"source": BytesArtifact.bind_read(source_path)}
            ),
            outputs=InputContract.Outputs._from_bindings(
                {"result": BytesArtifact.bind_write(destination)}
            ),
        )

    assert not destination.exists()


def test_repeated_inputs_are_registered_in_supplied_order(tmp_path: Path) -> None:
    class RepeatedContract(ProcedureContract[None]):
        class Inputs(ProcedureInputs):
            sources = repeated_input(BYTES, minimum=2)

        class Outputs(ProcedureOutputs):
            result = output(BYTES)

    definition = ProcedureDefinition(
        "example.RepeatedInputV1",
        f"{__name__}:RepeatedProcedure",
        "Consume repeated inputs",
        None,
        RepeatedContract,
    )

    class RepeatedProcedure(
        Procedure[
            None,
            RepeatedContract.SetupInputs,
            RepeatedContract.Inputs,
            RepeatedContract.Outputs,
        ]
    ):
        def process(
            self,
            context: ProcedureProcessContext,
            configuration: None,
            inputs: RepeatedContract.Inputs,
            outputs: RepeatedContract.Outputs,
        ) -> None:
            outputs.result.open().write(b"result")

    RepeatedProcedure.definition = definition
    first_path = tmp_path / "first.pa"
    second_path = tmp_path / "second.pa"
    first = write_source(first_path, "first", b"first")
    second = write_source(second_path, "second", b"second")
    destination = tmp_path / "result.pa"
    prepared = PreparedProcedure(
        RepeatedProcedure(),
        None,
        RepeatedContract.SetupInputs._from_bindings({}),
    )

    with session():
        prepared.execute(
            inputs=RepeatedContract.Inputs._from_bindings(
                {
                    "sources": (
                        BytesArtifact.bind_read(second_path),
                        BytesArtifact.bind_read(first_path),
                    )
                }
            ),
            outputs=RepeatedContract.Outputs._from_bindings(
                {"result": BytesArtifact.bind_write(destination)}
            ),
        )

    header = decode_header(destination.read_bytes())
    execution = next(
        execution
        for execution in header.lineage.executions.values()
        if execution.procedure.name == definition.identifier
    )
    assert execution.inputs == (second.reference, first.reference)


def test_declared_input_cannot_change_identity_after_preregistration(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.pa"
    write_source(source_path, "original", b"original")
    destination = tmp_path / "result.pa"

    class ReplacingProcedure(InputProcedure):
        def process(
            self,
            context: ProcedureProcessContext,
            configuration: None,
            inputs: InputContract.Inputs,
            outputs: InputContract.Outputs,
        ) -> None:
            write_source(source_path, "replacement", b"replacement")
            inputs.source.open()

    with session(), pytest.raises(RuntimeError, match="changed after registration"):
        prepare_input(ReplacingProcedure()).execute(
            inputs=InputContract.Inputs._from_bindings(
                {"source": BytesArtifact.bind_read(source_path)}
            ),
            outputs=InputContract.Outputs._from_bindings(
                {"result": BytesArtifact.bind_write(destination)}
            ),
        )

    assert not destination.exists()


def test_input_only_callback_uses_authorized_child_session(tmp_path: Path) -> None:
    source_path = tmp_path / "source.pa"
    write_source(source_path, "source", b"input only")
    values: list[bytes] = []

    class InputOnlyInputs(ProcedureInputs):
        source = input(BYTES)
        omitted = optional_input(BYTES)

    class InputOnlyProcedure(
        Procedure[None, ProcedureInputs, InputOnlyInputs, ProcedureOutputs]
    ):
        def process(
            self,
            context: ProcedureProcessContext,
            configuration: None,
            inputs: InputOnlyInputs,
            outputs: ProcedureOutputs,
        ) -> None:
            with inputs.source.open() as reader:
                values.append(reader.read())

    prepared = PreparedProcedure(
        InputOnlyProcedure(),
        None,
        ProcedureInputs._from_bindings({}),
    )
    with session() as parent:
        prepared.execute(
            inputs=InputOnlyInputs._from_bindings(
                {"source": BytesArtifact.bind_read(source_path)}
            ),
            outputs=ProcedureOutputs._from_bindings({}),
        )
        assert parent.inputs == ()

    assert values == [b"input only"]


class SetupContract(ProcedureContract[None]):
    class SetupInputs(ProcedureInputs):
        model = input(BYTES)

    class Inputs(ProcedureInputs):
        pass

    class Outputs(ProcedureOutputs):
        result = output(BYTES)


SETUP_DEFINITION = ProcedureDefinition(
    "example.PreparedSetupV1",
    f"{__name__}:SetupProcedure",
    "Use persistent setup input",
    None,
    SetupContract,
)


class SetupProcedure(
    Procedure[
        None,
        SetupContract.SetupInputs,
        SetupContract.Inputs,
        SetupContract.Outputs,
    ]
):
    definition = SETUP_DEFINITION

    def __init__(self) -> None:
        self.model: BytesReader | None = None
        self.closed_with_access = False

    def setup(
        self,
        context: ProcedureSetupContext,
        configuration: None,
        inputs: SetupContract.SetupInputs,
    ) -> None:
        self.model = inputs.model.open()

    def process(
        self,
        context: ProcedureProcessContext,
        configuration: None,
        inputs: SetupContract.Inputs,
        outputs: SetupContract.Outputs,
    ) -> None:
        assert self.model is not None
        self.model.body.seek(0)
        outputs.result.open().write(self.model.read())

    def close(self) -> None:
        assert self.model is not None
        self.model.body.seek(0)
        self.closed_with_access = self.model.read() == b"model"


def test_setup_inputs_persist_and_flow_into_every_execution_lineage(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model.pa"
    model_record = write_source(model_path, "model", b"model")
    procedure = SetupProcedure()
    prepared = PreparedProcedure(
        procedure,
        None,
        SetupContract.SetupInputs._from_bindings(
            {"model": BytesArtifact.bind_read(model_path)}
        ),
    )

    destinations = (tmp_path / "first.pa", tmp_path / "second.pa")
    for destination in destinations:
        prepared.execute(
            inputs=SetupContract.Inputs._from_bindings({}),
            outputs=SetupContract.Outputs._from_bindings(
                {"result": BytesArtifact.bind_write(destination)}
            ),
        )

    assert procedure.model is not None
    assert not procedure.model.closed
    for destination in destinations:
        header = decode_header(destination.read_bytes())
        execution = next(
            execution
            for execution in header.lineage.executions.values()
            if execution.procedure.name == SETUP_DEFINITION.identifier
        )
        assert execution.inputs == (model_record.reference,)
        assert destination.read_bytes()[header.body_offset :] == b"model"

    prepared.close()

    assert procedure.closed_with_access
    assert procedure.model.closed


def test_setup_callback_rejects_equal_but_undeclared_input_binding(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model.pa"
    write_source(model_path, "model", b"model")

    class UndeclaredSetupProcedure(SetupProcedure):
        def setup(
            self,
            context: ProcedureSetupContext,
            configuration: None,
            inputs: SetupContract.SetupInputs,
        ) -> None:
            BytesArtifact.bind_read(model_path).open()

    with pytest.raises(RuntimeError, match="provided input/output bindings"):
        PreparedProcedure(
            UndeclaredSetupProcedure(),
            None,
            SetupContract.SetupInputs._from_bindings(
                {"model": BytesArtifact.bind_read(model_path)}
            ),
        )


def test_close_callback_rejects_undeclared_input_and_closes_setup_resources(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model.pa"
    write_source(model_path, "model", b"model")

    class UndeclaredCloseProcedure(SetupProcedure):
        def close(self) -> None:
            BytesArtifact.bind_read(model_path).open()

    procedure = UndeclaredCloseProcedure()
    prepared = PreparedProcedure(
        procedure,
        None,
        SetupContract.SetupInputs._from_bindings(
            {"model": BytesArtifact.bind_read(model_path)}
        ),
    )

    with pytest.raises(RuntimeError, match="provided input/output bindings"):
        prepared.close()

    assert procedure.model is not None
    assert procedure.model.closed
