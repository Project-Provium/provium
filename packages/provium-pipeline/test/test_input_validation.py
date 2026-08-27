from __future__ import annotations

import pytest

from provium_pipeline import InputRecord, InputValidationError, RunInputSnapshot
from provium_pipeline.artifact import InMemoryArtifactIndex
from provium_pipeline.compiler import CompiledPipelineInput
from provium_pipeline.definition import PipelineInputScope
from provium_pipeline.identifiers import InputRecordKey
from provium_pipeline.input.validation import validate_input_snapshot
from test.artifact.test_index import descriptor, location


def compiled_input(
    *,
    name: str = "image",
    artifact_identifier: str = "example.document",
    scope: PipelineInputScope = PipelineInputScope.RECORD,
    minimum: int = 1,
    maximum: int | None = 1,
) -> CompiledPipelineInput:
    return CompiledPipelineInput(name, artifact_identifier, scope, minimum, maximum)


def snapshot(inputs: dict[str, tuple[str, ...]]) -> RunInputSnapshot:
    return RunInputSnapshot.create(
        records=(
            InputRecord(
                key=InputRecordKey("record-1"),
                inputs=inputs,
                labels={},
            ),
        ),
        shared_inputs={},
        sources=(),
    )


def registered_index() -> tuple[InMemoryArtifactIndex, str]:
    index = InMemoryArtifactIndex()
    managed = descriptor()
    index.register_artifact(managed)
    index.register_location(managed.identity, location())
    return index, managed.identity


def test_input_validation_accepts_registered_typed_active_artifact() -> None:
    index, identity = registered_index()

    validate_input_snapshot(
        snapshot({"image": (identity,)}), (compiled_input(),), index
    )


@pytest.mark.parametrize(
    ("bindings", "declaration", "message"),
    [
        ({"unknown": ()}, compiled_input(minimum=0), "unknown input: unknown"),
        ({}, compiled_input(), "image requires at least 1 artifact"),
        (
            {"image": ("one", "two")},
            compiled_input(maximum=1),
            "image accepts at most 1 artifact",
        ),
    ],
)
def test_input_validation_rejects_names_and_cardinality(
    bindings: dict[str, tuple[str, ...]],
    declaration: CompiledPipelineInput,
    message: str,
) -> None:
    with pytest.raises(InputValidationError, match=message):
        validate_input_snapshot(
            snapshot(bindings), (declaration,), InMemoryArtifactIndex()
        )


def test_input_validation_rejects_missing_mismatched_and_unavailable_artifacts() -> (
    None
):
    with pytest.raises(
        InputValidationError, match="artifact is not registered: missing"
    ):
        validate_input_snapshot(
            snapshot({"image": ("missing",)}),
            (compiled_input(),),
            InMemoryArtifactIndex(),
        )

    index, identity = registered_index()
    with pytest.raises(InputValidationError, match="expected other.ImageV1"):
        validate_input_snapshot(
            snapshot({"image": (identity,)}),
            (compiled_input(artifact_identifier="other.ImageV1"),),
            index,
        )

    unavailable = InMemoryArtifactIndex()
    managed = descriptor()
    unavailable.register_artifact(managed)
    with pytest.raises(InputValidationError, match="has no active location"):
        validate_input_snapshot(
            snapshot({"image": (managed.identity,)}),
            (compiled_input(),),
            unavailable,
        )
