from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from provium import JsonValue

from .artifact import ArtifactIndex
from .inputs import (
    InputRecord,
    InputSourceDescriptor,
    InputSourceKind,
    RunInputSnapshot,
)


@dataclass(frozen=True, slots=True, init=False)
class ResolveInputRecordsRequest:
    """Immutable configuration supplied to one input resolver invocation."""

    configuration: Mapping[str, JsonValue]
    shared_inputs: Mapping[str, tuple[str, ...]]

    def __init__(
        self,
        *,
        configuration: Mapping[str, JsonValue],
        shared_inputs: Mapping[str, Sequence[str]],
    ) -> None:
        object.__setattr__(
            self, "configuration", MappingProxyType(dict(configuration))
        )
        object.__setattr__(
            self,
            "shared_inputs",
            MappingProxyType(
                {name: tuple(values) for name, values in shared_inputs.items()}
            ),
        )


@dataclass(frozen=True, slots=True)
class InputResolutionContext:
    """Pipeline-owned services available while resolving input records."""

    artifact_index: ArtifactIndex


class InputRecordResolver(Protocol):
    """Resolve a complete deterministic sequence of input records."""

    @property
    def identifier(self) -> str: ...

    def resolve(
        self,
        request: ResolveInputRecordsRequest,
        context: InputResolutionContext,
    ) -> tuple[InputRecord, ...]: ...


def resolve_input_snapshot(
    resolver: InputRecordResolver,
    *,
    request: ResolveInputRecordsRequest,
    context: InputResolutionContext,
) -> RunInputSnapshot:
    """Resolve once and freeze exact records plus resolver replay metadata."""
    records = resolver.resolve(request, context)
    resolved = RunInputSnapshot.create(
        records=records,
        shared_inputs=request.shared_inputs,
        sources=(),
    )
    source = InputSourceDescriptor(
        kind=InputSourceKind.RESOLVER,
        identifier=resolver.identifier,
        configuration=request.configuration,
        result_digest=resolved.digest,
    )
    return RunInputSnapshot.create(
        records=resolved.records,
        shared_inputs=resolved.shared_inputs,
        sources=(source,),
    )


__all__ = [
    "InputRecordResolver",
    "InputResolutionContext",
    "ResolveInputRecordsRequest",
    "resolve_input_snapshot",
]
