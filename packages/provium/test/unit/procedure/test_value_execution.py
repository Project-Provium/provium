"""Value inputs survive execution and artifact provenance."""

import sys

import pytest
from unit.procedure.test_executor import BYTES, BytesArtifact

from provium import (
    FilePath,
    Procedure,
    ProcedureContract,
    ProcedureDefinition,
    ProcedureExecutionRecord,
    ProcedureExecutor,
    ProcedureInputs,
    ProcedureOutputs,
    decode_header,
    input_field,
    multi_input_field,
    output,
)


class ImportContract(ProcedureContract[None]):
    class SetupInputs(ProcedureInputs):
        scale = input_field(float)

    class Inputs(ProcedureInputs):
        source = input_field(FilePath)
        count = input_field(int)
        counts = multi_input_field(int, minimum=2, optional=True)
        previous = input_field(BYTES, optional=True)

    class Outputs(ProcedureOutputs):
        result = output(BYTES)


class ImportProcedure(
    Procedure[
        None, ImportContract.SetupInputs, ImportContract.Inputs, ImportContract.Outputs
    ]
):
    def setup(self, context, configuration, inputs):
        self.scale = inputs.scale

    def process(self, context, configuration, inputs, outputs):
        assert inputs.previous is None
        outputs.result.open().write(
            f"{inputs.source}:{inputs.count * self.scale}:{inputs.counts}".encode()
        )


IMPORT = ProcedureDefinition(
    "tests.ImportV1",
    f"{__name__}:ImportProcedure",
    "Import",
    None,
    ImportContract,
)
ImportProcedure.definition = IMPORT


def test_import_has_value_provenance_without_artifact_parents(tmp_path):
    destination = tmp_path / "out.pa"
    result = ProcedureExecutor().execute(
        IMPORT,
        setup_inputs={"scale": 2},
        inputs={"source": "s3://bucket/input.csv", "count": 3, "counts": [2, 3]},
        outputs={"result": BytesArtifact.bind_write(destination)},
    )
    assert result.inputs == ()
    expected = {
        "setup.scale": {"type": "float", "value": 2.0},
        "inputs.source": {"type": "file-path", "value": "s3://bucket/input.csv"},
        "inputs.count": {"type": "int", "value": 3},
        "inputs.counts": {"type": "int", "value": [2, 3]},
    }
    assert result.value_inputs == expected
    execution = result.lineage.executions[result.identity]
    assert execution.value_inputs == expected
    assert ProcedureExecutionRecord.from_dict(execution.to_dict()) == execution
    header = decode_header(destination.read_bytes())
    assert header.lineage.executions[result.identity].value_inputs == expected
    assert execution.bindings["inputs.previous"] is None
    copy = execution.value_inputs
    copy["inputs.counts"]["value"].append(9)
    assert execution.value_inputs == expected


def test_value_only_execution_and_prepared_reuse(monkeypatch):
    class Contract(ProcedureContract[None]):
        class Inputs(ProcedureInputs):
            count = input_field(int)

        Outputs = None

    class ReadProcedure(Procedure[None, Contract.SetupInputs, Contract.Inputs, None]):
        def process(self, context, configuration, inputs, outputs):
            assert type(inputs.count) is int

    definition = ProcedureDefinition(
        "tests.ReadValues",
        f"{__name__}:ReadProcedure",
        "Read",
        None,
        Contract,
    )
    ReadProcedure.definition = definition
    monkeypatch.setattr(
        sys.modules[__name__], "ReadProcedure", ReadProcedure, raising=False
    )
    prepared = ProcedureExecutor().prepare(definition)
    try:
        first = prepared.execute(inputs={"count": 2})
        second = prepared.execute(inputs={"count": 3})
    finally:
        prepared.close()
    assert first.value_inputs == {"inputs.count": {"type": "int", "value": 2}}
    assert second.value_inputs == {"inputs.count": {"type": "int", "value": 3}}
    assert first.identity != second.identity


def test_invalid_value_fails_before_output_is_changed(tmp_path):
    destination = tmp_path / "out.pa"
    destination.write_bytes(b"keep")
    with pytest.raises(TypeError, match="count"):
        ProcedureExecutor().execute(
            IMPORT,
            setup_inputs={"scale": 1},
            inputs={"source": "data.csv", "count": 3.9},
            outputs={"result": BytesArtifact.bind_write(destination)},
        )
    assert destination.read_bytes() == b"keep"


def test_value_inputs_affect_fingerprints_even_when_output_bytes_match(tmp_path):
    results = []
    for index, scale in enumerate((1, 2)):
        results.append(
            ProcedureExecutor().execute(
                IMPORT,
                setup_inputs={"scale": scale},
                inputs={"source": "data.csv", "count": 0},
                outputs={"result": BytesArtifact.bind_write(tmp_path / f"{index}.pa")},
            )
        )
    from provium.semantic import artifact_fingerprint

    first = artifact_fingerprint(results[0].lineage, results[0].outputs["result"])
    second = artifact_fingerprint(results[1].lineage, results[1].outputs["result"])
    assert first["content"] == second["content"]
    assert first["provenance"] != second["provenance"]


def test_prepared_rejects_an_unrelated_input_record():
    from provium.procedure.io import build_procedure_inputs

    class Other(ProcedureInputs):
        value = input_field(int)

    prepared = ProcedureExecutor().prepare(IMPORT, setup_inputs={"scale": 1})
    try:
        with pytest.raises(TypeError, match="input record"):
            prepared.execute(inputs=build_procedure_inputs(Other, {"value": 1}))
    finally:
        prepared.close()


def test_value_bindings_cannot_shadow_artifact_bindings(tmp_path):
    result = ProcedureExecutor().execute(
        IMPORT,
        setup_inputs={"scale": 1},
        inputs={"source": "data.csv", "count": 1},
        outputs={"result": BytesArtifact.bind_write(tmp_path / "out.pa")},
    )
    payload = result.lineage.executions[result.identity].to_dict()
    payload["value_inputs"]["inputs.previous"] = {"type": "int", "value": 7}
    with pytest.raises(ValueError, match="both"):
        ProcedureExecutionRecord.from_dict(payload)


def test_mixed_values_and_artifacts_keep_artifact_lineage(tmp_path, monkeypatch):
    from unit.procedure.test_executor import write_artifact

    source = tmp_path / "previous.pa"
    parent = write_artifact(source, "previous", b"previous")

    def process(self, context, configuration, inputs, outputs):
        with inputs.previous.open() as reader:
            outputs.result.open().write(reader.read() + str(inputs.count).encode())

    monkeypatch.setattr(ImportProcedure, "process", process)
    result = ProcedureExecutor().execute(
        IMPORT,
        setup_inputs={"scale": 1},
        inputs={
            "source": "data.csv",
            "count": 3,
            "previous": BytesArtifact.bind_read(source),
        },
        outputs={"result": BytesArtifact.bind_write(tmp_path / "out.pa")},
    )
    assert result.inputs == (parent.reference,)
    execution = result.lineage.executions[result.identity]
    assert execution.bindings["inputs.previous"] == parent.reference.identity
    assert "inputs.previous" not in execution.value_inputs
    assert execution.value_inputs["inputs.count"]["value"] == 3
