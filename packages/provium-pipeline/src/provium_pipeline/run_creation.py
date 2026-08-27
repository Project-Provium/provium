"""Validated creation of durable runs from immutable input sets."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from provium import JsonValue

from .artifact.index import ArtifactIndex
from .compiler.models import CompiledPipeline
from .definition.models import PipelineDefinition
from .identifiers import InputSetIdentifier
from .input_validation import validate_input_snapshot
from .inputs import (
    InputSet,
    InputSourceDescriptor,
    InputSourceKind,
    RunInputSnapshot,
)
from .run_models import CreateRunRequest, PipelineRun


class PipelineCompilation(Protocol):
    def compile(self, definition: PipelineDefinition) -> CompiledPipeline: ...


class InputSetLookup(Protocol):
    def get(self, identity: str | InputSetIdentifier) -> InputSet: ...


class RunCreationStore(Protocol):
    def create_run(self, request: CreateRunRequest) -> PipelineRun: ...


class RunCreationService:
    """Compile, freeze, validate, and atomically persist a pipeline run."""

    def __init__(
        self,
        *,
        compiler: PipelineCompilation,
        input_sets: InputSetLookup,
        artifact_index: ArtifactIndex,
        runs: RunCreationStore,
    ) -> None:
        self._compiler = compiler
        self._input_sets = input_sets
        self._artifact_index = artifact_index
        self._runs = runs

    def create(
        self,
        definition: PipelineDefinition,
        *,
        input_set: str,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> PipelineRun:
        compiled = self._compiler.compile(definition)
        inputs = self._input_sets.get(input_set)
        snapshot = RunInputSnapshot.create(
            records=inputs.records,
            shared_inputs={},
            sources=(
                InputSourceDescriptor(
                    kind=InputSourceKind.INPUT_SET,
                    identifier=inputs.identity,
                    configuration={"identifier": str(inputs.identifier)},
                    result_digest=inputs.digest,
                ),
            ),
        )
        return self._create_from_snapshot(
            compiled,
            input_snapshot=snapshot,
            metadata=metadata,
        )

    def create_from_snapshot(
        self,
        definition: PipelineDefinition,
        *,
        input_snapshot: RunInputSnapshot,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> PipelineRun:
        """Compile and persist a run from an already-frozen input snapshot."""
        compiled = self._compiler.compile(definition)
        return self._create_from_snapshot(
            compiled,
            input_snapshot=input_snapshot,
            metadata=metadata,
        )

    def _create_from_snapshot(
        self,
        compiled: CompiledPipeline,
        *,
        input_snapshot: RunInputSnapshot,
        metadata: Mapping[str, JsonValue] | None,
    ) -> PipelineRun:
        validate_input_snapshot(
            input_snapshot,
            compiled.inputs,
            self._artifact_index,
        )
        return self._runs.create_run(
            CreateRunRequest(
                pipeline=compiled,
                inputs=input_snapshot,
                metadata=metadata,
            )
        )


__all__ = ["RunCreationService"]
