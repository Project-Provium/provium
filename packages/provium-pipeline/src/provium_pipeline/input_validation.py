from __future__ import annotations

from collections.abc import Mapping, Sequence

from .artifact import ArtifactIndex, ArtifactIndexNotFoundError
from .compiler import CompiledPipelineInput
from .definition import PipelineInputScope
from .inputs import RunInputSnapshot


class InputValidationError(ValueError):
    """A frozen input snapshot does not satisfy its compiled pipeline contract."""


def validate_input_snapshot(
    snapshot: RunInputSnapshot,
    inputs: Sequence[CompiledPipelineInput],
    artifact_index: ArtifactIndex,
) -> None:
    """Validate exact bindings, artifact types, and managed availability."""
    record_inputs = {
        item.name: item for item in inputs if item.scope is PipelineInputScope.RECORD
    }
    shared_inputs = {
        item.name: item for item in inputs if item.scope is PipelineInputScope.SHARED
    }
    for record in snapshot.records:
        _validate_bindings(record.inputs, record_inputs, artifact_index)
    _validate_bindings(snapshot.shared_inputs, shared_inputs, artifact_index)


def _validate_bindings(
    bindings: Mapping[str, tuple[str, ...]],
    declarations: Mapping[str, CompiledPipelineInput],
    artifact_index: ArtifactIndex,
) -> None:
    for name in bindings:
        if name not in declarations:
            raise InputValidationError(f"unknown input: {name}")
    for name, declaration in declarations.items():
        identities = bindings.get(name, ())
        count = len(identities)
        if count < declaration.minimum:
            raise InputValidationError(
                f"{name} requires at least {declaration.minimum} artifact(s)"
            )
        if declaration.maximum is not None and count > declaration.maximum:
            raise InputValidationError(
                f"{name} accepts at most {declaration.maximum} artifact(s)"
            )
        for identity in identities:
            _validate_artifact(identity, declaration, artifact_index)


def _validate_artifact(
    identity: str,
    declaration: CompiledPipelineInput,
    artifact_index: ArtifactIndex,
) -> None:
    try:
        descriptor = artifact_index.get_artifact(identity)
    except ArtifactIndexNotFoundError as error:
        raise InputValidationError(f"artifact is not registered: {identity}") from error
    if descriptor.artifact_identifier != declaration.artifact_identifier:
        raise InputValidationError(
            f"input {declaration.name} expected {declaration.artifact_identifier}, "
            f"got {descriptor.artifact_identifier} for {identity}"
        )
    if not artifact_index.get_active_locations(identity):
        raise InputValidationError(f"artifact {identity} has no active location")


__all__ = ["InputValidationError", "validate_input_snapshot"]
