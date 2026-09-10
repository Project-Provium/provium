"""Tests for immutable procedure execution results."""

from pathlib import Path
from types import MappingProxyType

import pytest

from provium import (
    ArtifactLineage,
    ArtifactReference,
    FinalizedArtifactInspection,
    ProcedureExecutionResult,
    ProcedureOutputResult,
    ProcedureRecord,
)

REFERENCE = ArtifactReference("artifact", "example.ArtifactV1")
PROCEDURE = ProcedureRecord("example.ProcedureV1", "contract-digest")
INSPECTION = FinalizedArtifactInspection(
    path=Path("artifact.pa"),
    artifact_identifier=REFERENCE.artifact_identifier,
    artifact_identity=REFERENCE.identity,
    body_digest="a" * 64,
    container_digest="b" * 64,
    size_bytes=1,
    created_at=None,
    lineage=ArtifactLineage(),
)


def test_output_result_represents_produced_and_absent_outputs() -> None:
    produced = ProcedureOutputResult(Path("produced.pa"), REFERENCE, INSPECTION)
    absent = ProcedureOutputResult(Path("absent.pa"))

    assert produced.produced
    assert not absent.produced


@pytest.mark.parametrize(
    ("arguments", "error", "message"),
    [
        ((object(),), TypeError, "path"),
        ((Path("value.pa"), REFERENCE, None), ValueError, "present or absent"),
        ((Path("value.pa"), object(), INSPECTION), TypeError, "reference"),
        ((Path("value.pa"), REFERENCE, object()), TypeError, "inspection"),
        (
            (
                Path("value.pa"),
                ArtifactReference("other", REFERENCE.artifact_identifier),
                INSPECTION,
            ),
            ValueError,
            "does not match",
        ),
    ],
)
def test_output_result_validates_state(
    arguments: tuple[object, ...], error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message):
        ProcedureOutputResult(*arguments)  # type: ignore[arg-type]


def test_execution_result_copies_and_freezes_named_outputs() -> None:
    outputs = {"result": REFERENCE}
    result = ProcedureExecutionResult(
        "execution",
        PROCEDURE,
        (REFERENCE,),
        outputs,
    )
    outputs.clear()

    assert result.outputs == {"result": REFERENCE}
    assert isinstance(result.outputs, MappingProxyType)


@pytest.mark.parametrize("identity", [object(), ""])
def test_execution_result_rejects_invalid_identity(identity: object) -> None:
    with pytest.raises((TypeError, ValueError), match="identity"):
        ProcedureExecutionResult(identity, PROCEDURE, ())  # type: ignore[arg-type]


def test_execution_result_rejects_invalid_procedure() -> None:
    with pytest.raises(TypeError, match="procedure"):
        ProcedureExecutionResult("execution", object(), ())  # type: ignore[arg-type]


def test_execution_result_rejects_invalid_inputs() -> None:
    with pytest.raises(TypeError, match="inputs"):
        ProcedureExecutionResult("execution", PROCEDURE, (object(),))  # type: ignore[arg-type]


def test_execution_result_rejects_mutable_input_collection() -> None:
    with pytest.raises(TypeError, match="tuple"):
        ProcedureExecutionResult(
            "execution",
            PROCEDURE,
            [REFERENCE],  # type: ignore[arg-type]
        )


def test_execution_result_rejects_non_mapping_outputs() -> None:
    with pytest.raises(TypeError, match="outputs"):
        ProcedureExecutionResult("execution", PROCEDURE, (), object())  # type: ignore[arg-type]


def test_execution_result_rejects_non_string_output_name() -> None:
    with pytest.raises(TypeError, match="names"):
        ProcedureExecutionResult(
            "execution",
            PROCEDURE,
            (),
            {1: REFERENCE},  # type: ignore[dict-item]
        )


def test_execution_result_rejects_empty_output_name() -> None:
    with pytest.raises(ValueError, match="names"):
        ProcedureExecutionResult("execution", PROCEDURE, (), {"": REFERENCE})


def test_execution_result_rejects_invalid_output_reference() -> None:
    with pytest.raises(TypeError, match="outputs"):
        ProcedureExecutionResult(
            "execution",
            PROCEDURE,
            (),
            {"result": object()},  # type: ignore[dict-item]
        )


def test_execution_result_rejects_invalid_lineage() -> None:
    with pytest.raises(TypeError, match="lineage"):
        ProcedureExecutionResult(
            "execution",
            PROCEDURE,
            (),
            lineage=object(),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("output_results", "error", "message"),
    [
        (object(), TypeError, "mapping"),
        ({"": ProcedureOutputResult(Path("absent.pa"))}, ValueError, "names"),
        ({1: ProcedureOutputResult(Path("absent.pa"))}, ValueError, "names"),
        ({"result": object()}, TypeError, "ProcedureOutputResult"),
        (
            {"result": ProcedureOutputResult(Path("absent.pa"))},
            ValueError,
            "does not match",
        ),
    ],
)
def test_execution_result_validates_output_results(
    output_results: object, error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message):
        ProcedureExecutionResult(
            "execution",
            PROCEDURE,
            (),
            {"result": REFERENCE},
            output_results=output_results,  # type: ignore[arg-type]
        )


def test_execution_result_requires_every_produced_output_result() -> None:
    with pytest.raises(ValueError, match="every produced"):
        ProcedureExecutionResult(
            "execution",
            PROCEDURE,
            (),
            {"first": REFERENCE, "second": REFERENCE},
            output_results={
                "first": ProcedureOutputResult(Path("first.pa"), REFERENCE, INSPECTION)
            },
        )


def test_execution_result_defaults_to_empty_lineage() -> None:
    result = ProcedureExecutionResult("execution", None, ())

    assert result.lineage == ArtifactLineage()
