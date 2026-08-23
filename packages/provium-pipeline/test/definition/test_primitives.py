"""Definition cardinality and reference contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from provium_pipeline import (
    PipelineInputName,
    PipelineNodeIdentifier,
    PipelineOutputName,
)
from provium_pipeline.definition import (
    NodeOutputReference,
    PipelineInputCardinality,
    PipelineInputReference,
    PipelineInputScope,
    parse_reference,
    parse_reference_value,
)


def test_input_cardinality_defaults_to_required_scalar() -> None:
    cardinality = PipelineInputCardinality()

    assert cardinality.minimum == 1
    assert cardinality.maximum == 1
    assert cardinality.required is True
    assert cardinality.repeated is False


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [(-1, 1), (2, 1), (1, 0)],
)
def test_input_cardinality_rejects_invalid_ranges(
    minimum: int, maximum: int | None
) -> None:
    with pytest.raises(ValidationError):
        PipelineInputCardinality(minimum=minimum, maximum=maximum)


def test_input_cardinality_supports_optional_and_unbounded_repeated() -> None:
    optional = PipelineInputCardinality(minimum=0, maximum=1)
    repeated = PipelineInputCardinality(minimum=0, maximum=None)

    assert optional.required is False
    assert optional.repeated is False
    assert repeated.required is False
    assert repeated.repeated is True


def test_input_cardinality_is_frozen_and_forbids_extra_fields() -> None:
    cardinality = PipelineInputCardinality()

    with pytest.raises(ValidationError):
        cardinality.minimum = 0
    with pytest.raises(ValidationError):
        PipelineInputCardinality(unexpected=True)  # type: ignore[call-arg]


def test_input_scope_values_are_stable() -> None:
    assert PipelineInputScope.RECORD.value == "record"
    assert PipelineInputScope.SHARED.value == "shared"


def test_parse_pipeline_input_reference() -> None:
    reference = parse_reference("$inputs.auxiliary-images")

    assert reference == PipelineInputReference(
        name=PipelineInputName("auxiliary-images")
    )
    assert str(reference) == "$inputs.auxiliary-images"


def test_parse_node_output_reference() -> None:
    reference = parse_reference("$nodes.detect.raw-output")

    assert reference == NodeOutputReference(
        node=PipelineNodeIdentifier("detect"),
        output=PipelineOutputName("raw-output"),
    )
    assert str(reference) == "$nodes.detect.raw-output"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "inputs.value",
        "$inputs",
        "$inputs.a.b",
        "$inputs.a:b",
        "$nodes.a",
        "$nodes.a.b.c",
        "$other.a",
    ],
)
def test_parse_reference_rejects_unsupported_grammar(value: str) -> None:
    with pytest.raises(ValueError, match="pipeline reference"):
        parse_reference(value)


def test_parse_reference_value_preserves_repeated_binding_order() -> None:
    parsed = parse_reference_value(
        ["$inputs.images", "$nodes.decode.image", "$inputs.fallback"]
    )

    assert isinstance(parsed, tuple)
    assert tuple(str(reference) for reference in parsed) == (
        "$inputs.images",
        "$nodes.decode.image",
        "$inputs.fallback",
    )


def test_parse_reference_value_accepts_a_scalar_reference() -> None:
    assert parse_reference_value("$inputs.session") == PipelineInputReference(
        name=PipelineInputName("session")
    )
