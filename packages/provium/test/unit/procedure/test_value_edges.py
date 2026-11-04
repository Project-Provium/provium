"""Boundary and compatibility checks for value inputs."""

from dataclasses import dataclass

import pytest
from unit.procedure.test_io import EXAMPLE_ARTIFACT, ExampleArtifact

from provium import (
    ArtifactReference,
    FilePath,
    PreparedProcedure,
    ProcedureExecutionRecord,
    ProcedureInputs,
    ProcedureRecord,
    input_field,
    multi_input_field,
    register_input_type,
)
from provium.input_types import input_type
from provium.procedure.io import build_procedure_inputs


@pytest.mark.parametrize(
    "kind,value,expected",
    [
        (int, 3, 3),
        (float, 3, 3.0),
        (float, 1.5, 1.5),
        (FilePath, "file:///tmp/a.csv", FilePath("/tmp/a.csv")),
    ],
)
def test_codecs_decode_external_values(kind, value, expected):
    assert input_type(kind).parse(value) == expected


@pytest.mark.parametrize(
    "kind,value",
    [
        (int, 3.9),
        (int, True),
        (float, True),
        (float, []),
    ],
)
def test_codecs_reject_incompatible_external_values(kind, value):
    with pytest.raises(TypeError):
        input_type(kind).parse(value)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"python_type": 3},
        {"name": ""},
        {"decode": None},
        {"encode": None},
        {"name": "int"},
        {"python_type": int, "name": "other-int"},
    ],
)
def test_invalid_or_duplicate_codec_registration_is_rejected(kwargs):
    class Custom:
        pass

    arguments = {
        "python_type": Custom,
        "name": "tests.invalid",
        "decode": Custom,
        "encode": lambda value: str(value),
        **kwargs,
    }
    with pytest.raises((TypeError, ValueError)):
        register_input_type(**arguments)


def test_unregistered_custom_types_require_a_codec():
    class Custom:
        pass

    with pytest.raises(TypeError, match="register_input_type"):
        input_field(Custom)


@pytest.mark.parametrize("helper", [input_field, multi_input_field])
def test_optional_is_strictly_boolean(helper):
    with pytest.raises(TypeError, match="boolean"):
        helper(int, optional=1)


def test_value_field_inheritance_preserves_type_and_optional_bounds():
    class Base(ProcedureInputs):
        value = input_field(int)
        values = multi_input_field(int, optional=True, minimum=2)

    class Derived(Base):
        value = input_field(int)
        values = multi_input_field(int, optional=True, minimum=2)

    assert build_procedure_inputs(Derived, {"value": 1}).values == ()
    with pytest.raises(TypeError, match="cannot change"):

        class WrongType(Base):
            value = input_field(float)

    with pytest.raises(TypeError, match="cardinality"):

        class WrongOptional(Base):
            values = multi_input_field(int, minimum=2)

    with pytest.raises(TypeError, match="no artifact"):
        _ = Base.value.artifact


def test_multi_artifacts_keep_binding_types_and_optional_bounds(tmp_path):
    class Inputs(ProcedureInputs):
        source = input_field(EXAMPLE_ARTIFACT)
        sources = multi_input_field(EXAMPLE_ARTIFACT, optional=True, minimum=2)

    binding = ExampleArtifact.bind_read(tmp_path / "unused")
    values = build_procedure_inputs(Inputs, {"source": binding})
    assert values.source is binding
    assert values.sources == ()
    values = build_procedure_inputs(
        Inputs, {"source": binding, "sources": [binding, binding]}
    )
    assert values.sources == (binding, binding)
    with pytest.raises(ValueError, match="at least 2"):
        build_procedure_inputs(Inputs, {"source": binding, "sources": [binding]})


@pytest.mark.parametrize(
    "values",
    [
        [],
        {1: {"type": "int", "value": 1}},
        {"outputs.x": {"type": "int", "value": 1}},
        {"inputs.": {"type": "int", "value": 1}},
        {"inputs.x": 1},
        {"inputs.x": {"type": "int"}},
        {"inputs.x": {"type": "", "value": 1}},
        {"inputs.x": {"type": "float", "value": float("nan")}},
    ],
)
def test_serialized_value_inputs_are_validated(values):
    with pytest.raises((TypeError, ValueError)):
        ProcedureExecutionRecord(
            "run",
            ProcedureRecord("tests.proc", "v1"),
            outputs=(ArtifactReference("out", "tests.artifact"),),
            value_inputs=values,
        )


def test_unknown_value_input_versions_are_rejected():
    record = ProcedureExecutionRecord(
        "run",
        ProcedureRecord("tests.proc", "v1"),
        outputs=(ArtifactReference("out", "tests.artifact"),),
        value_inputs={"inputs.x": {"type": "int", "value": 1}},
    )
    payload = record.to_dict()
    payload["value_inputs_version"] = 2
    with pytest.raises(ValueError, match="version"):
        ProcedureExecutionRecord.from_dict(payload)


def test_failed_custom_serialization_precedes_setup_resources(monkeypatch):
    class Unserializable:
        pass

    register_input_type(
        Unserializable,
        name="tests.unserializable",
        decode=lambda value: Unserializable(),
        encode=lambda value: object(),
    )

    class Inputs(ProcedureInputs):
        data = input_field(Unserializable)

    monkeypatch.setattr(
        "provium.procedure.prepared._SetupTemporaryDirectory",
        lambda: pytest.fail("resource allocated before validation"),
    )
    with pytest.raises(TypeError, match="setup.data"):
        PreparedProcedure(
            object(), None, build_procedure_inputs(Inputs, {"data": Unserializable()})
        )


def test_snapshots_detach_mutable_custom_data():
    @dataclass
    class Custom:
        values: list[int]

    codec = register_input_type(
        Custom,
        name="tests.mutable",
        decode=lambda value: Custom(value),
        encode=lambda value: value.values,
    )
    value = Custom([1, 2])
    snapshot = codec.snapshot(value)
    value.values.append(3)
    assert snapshot == [1, 2]


def test_external_float_overflow_is_a_validation_error():
    with pytest.raises(ValueError, match="finite float"):
        input_type(float).parse(10**1000)
