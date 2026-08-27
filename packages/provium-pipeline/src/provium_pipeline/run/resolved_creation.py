"""Create durable runs from discovered input-record resolvers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from provium import JsonValue

from ..artifact.index import ArtifactIndex
from ..definition.models import PipelineDefinition
from ..input.models import RunInputSnapshot
from ..input.resolver import (
    InputRecordResolverCatalog,
    InputResolutionContext,
    ResolveInputRecordsRequest,
    resolve_input_snapshot,
)
from .models import PipelineRun


class SnapshotRunCreator(Protocol):
    """Persist a run from an immutable input snapshot."""

    def create_from_snapshot(
        self,
        definition: PipelineDefinition,
        *,
        input_snapshot: RunInputSnapshot,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> PipelineRun: ...


class ResolvedRunCreationService:
    """Resolve input records once and persist their frozen run snapshot."""

    def __init__(
        self,
        *,
        resolvers: InputRecordResolverCatalog,
        artifact_index: ArtifactIndex,
        runs: SnapshotRunCreator,
    ) -> None:
        self._resolvers = resolvers
        self._context = InputResolutionContext(artifact_index=artifact_index)
        self._runs = runs

    def create(
        self,
        definition: PipelineDefinition,
        *,
        resolver_identifier: str,
        configuration: Mapping[str, JsonValue],
        shared_inputs: Mapping[str, Sequence[str]] | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> PipelineRun:
        resolver = self._resolvers.get(resolver_identifier)
        snapshot = resolve_input_snapshot(
            resolver,
            request=ResolveInputRecordsRequest(
                configuration=configuration,
                shared_inputs=shared_inputs or {},
            ),
            context=self._context,
        )
        return self._runs.create_from_snapshot(
            definition,
            input_snapshot=snapshot,
            metadata=metadata,
        )


__all__ = ["ResolvedRunCreationService"]
