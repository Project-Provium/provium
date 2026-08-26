from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import entry_points as installed_entry_points
from types import MappingProxyType
from typing import Protocol, cast

from provium import JsonValue

from .artifact import ArtifactIndex
from .inputs import (
    InputRecord,
    InputSourceDescriptor,
    InputSourceKind,
    RunInputSnapshot,
)

INPUT_RECORD_RESOLVER_ENTRY_POINT_GROUP = "provium.input_record_resolvers"


class _EntryPoint(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def value(self) -> str: ...

    def load(self) -> object: ...


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


class InputRecordResolverCatalog:
    """Deterministic registry of discovered input-record resolvers."""

    def __init__(self) -> None:
        self._resolvers: dict[str, InputRecordResolver] = {}

    def register(self, resolver: InputRecordResolver) -> None:
        identifier = resolver.identifier
        if not isinstance(identifier, str) or not identifier:
            raise TypeError("input-record resolver identifier must be non-empty text")
        if not callable(getattr(resolver, "resolve", None)):
            raise TypeError(
                f"input-record resolver {identifier!r} has no resolve method"
            )
        if identifier in self._resolvers:
            raise ValueError(
                f"input-record resolver {identifier!r} is already registered"
            )
        self._resolvers[identifier] = resolver

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._resolvers))

    def get(self, identifier: str) -> InputRecordResolver:
        try:
            return self._resolvers[identifier]
        except KeyError as error:
            raise KeyError(f"unknown input-record resolver: {identifier}") from error


def discover_input_record_resolvers(
    *,
    entry_points: Iterable[_EntryPoint] | None = None,
) -> InputRecordResolverCatalog:
    """Load installed resolver objects and validate entry-point identities."""
    selected = (
        installed_entry_points(group=INPUT_RECORD_RESOLVER_ENTRY_POINT_GROUP)
        if entry_points is None
        else entry_points
    )
    catalog = InputRecordResolverCatalog()
    for entry_point in sorted(selected, key=lambda value: (value.name, value.value)):
        resolver = entry_point.load()
        identifier = getattr(resolver, "identifier", None)
        if identifier != entry_point.name:
            raise ValueError(
                f"input-record resolver entry point {entry_point.name!r} "
                f"loaded identifier {identifier!r}"
            )
        catalog.register(cast(InputRecordResolver, resolver))
    return catalog


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
    "INPUT_RECORD_RESOLVER_ENTRY_POINT_GROUP",
    "InputRecordResolver",
    "InputRecordResolverCatalog",
    "InputResolutionContext",
    "ResolveInputRecordsRequest",
    "discover_input_record_resolvers",
    "resolve_input_snapshot",
]
