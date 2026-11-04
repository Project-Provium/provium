"""Typed value input declarations and validation."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from provium import (
    FilePath,
    ProcedureContract,
    ProcedureInputs,
    input_field,
    multi_input_field,
    register_input_type,
)
from provium.procedure.io import build_procedure_inputs


class Values(ProcedureInputs):
    count = input_field(int)
    scale = input_field(float)
    source = input_field(FilePath)
    maybe = input_field(int, optional=True)
    extras = multi_input_field(int, optional=True, minimum=2, maximum=3)


def make_values(**overrides):
    return build_procedure_inputs(
        Values, {"count": 2, "scale": 1, "source": "./missing.csv", **overrides}
    )


def test_values_are_typed_and_optional_collections_are_empty():
    values = make_values()
    assert values.count == 2
    assert values.scale == 1.0 and type(values.scale) is float
    assert isinstance(values.source, FilePath)
    assert values.maybe is None
    assert values.extras == ()
    assert make_values(extras=[2, 3]).extras == (2, 3)
    assert Values.extras.minimum == 2
    assert not Values.extras.required


@pytest.mark.parametrize("value", [3.9, 3.0, True, "3", None])
def test_integer_rejects_lossy_or_incompatible_values(value):
    with pytest.raises(TypeError, match="count"):
        make_values(count=value)


@pytest.mark.parametrize("value", [True, "1.5", float("inf"), float("nan")])
def test_float_rejects_invalid_values(value):
    with pytest.raises((TypeError, ValueError), match="scale"):
        make_values(scale=value)


@pytest.mark.parametrize("values", [[1], [1, 2, 3, 4]])
def test_optional_multi_still_enforces_nonempty_bounds(values):
    with pytest.raises(ValueError, match="extras"):
        make_values(extras=values)


def test_multi_reports_bad_element_index():
    with pytest.raises(TypeError, match=r"extras\[1\]"):
        make_values(extras=[1, 3.9])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"minimum": 0},
        {"minimum": -1},
        {"minimum": True},
        {"minimum": 2, "maximum": 1},
        {"maximum": 0},
    ],
)
def test_multi_rejects_invalid_declarations(kwargs):
    with pytest.raises((TypeError, ValueError)):
        multi_input_field(int, **kwargs)


def test_required_multi_needs_at_least_one_value():
    class Inputs(ProcedureInputs):
        values = multi_input_field(int)

    with pytest.raises(ValueError, match="values"):
        build_procedure_inputs(Inputs, {})
    assert build_procedure_inputs(Inputs, {"values": [1]}).values == (1,)


@pytest.mark.parametrize(
    "location",
    ["data.csv", "file:///tmp/data.csv", "s3://bucket/data.csv", Path("data.csv")],
)
def test_file_paths_accept_locations_without_accessing_them(location):
    path = FilePath(location)
    assert make_values(source=path).source == path
    assert str(path)


@pytest.mark.parametrize(
    "location", ["", "https://host/data", "s3://bucket", "file://remote/data"]
)
def test_file_paths_reject_invalid_locations(location):
    with pytest.raises((TypeError, ValueError)):
        FilePath(location)


def test_custom_types_validate_instances_and_round_trip():
    @dataclass(frozen=True)
    class Format:
        name: str

    codec = register_input_type(
        Format,
        name="tests.format",
        decode=lambda value: Format(str(value)),
        encode=lambda value: value.name,
    )

    class Inputs(ProcedureInputs):
        format = input_field(Format)

    value = Format("csv")
    assert build_procedure_inputs(Inputs, {"format": value}).format == value
    assert codec.decode(codec.encode(value)) == value
    with pytest.raises(TypeError, match="format"):
        build_procedure_inputs(Inputs, {"format": "csv"})


def test_metadata_distinguishes_values_and_optional_multi():
    class Contract(ProcedureContract[None]):
        Inputs = Values

    fields = {field.name: field for field in Contract.metadata.inputs}
    assert fields["count"].value_type == "int"
    assert fields["count"].artifact_identifier is None
    assert fields["extras"].minimum == 2
    assert not fields["extras"].required


def test_float_overflow_has_field_context():
    with pytest.raises(ValueError, match="scale.*finite float"):
        make_values(scale=10**1000)
