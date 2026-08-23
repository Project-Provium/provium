"""Artifact index contracts and the deterministic in-memory reference index."""

from __future__ import annotations

from dataclasses import replace
from threading import RLock
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from . import ArtifactLocation, ManagedArtifactDescriptor


class ArtifactIndexError(Exception):
    """Base class for typed artifact-index failures."""


class ArtifactIdentityCollisionError(ArtifactIndexError):
    """An identity was reused with different immutable descriptor fields."""


class ArtifactIndexNotFoundError(ArtifactIndexError):
    """A requested logical artifact is not registered."""


class ArtifactIndex(Protocol):
    """Registry of logical artifacts and their managed locations."""

    def register_artifact(
        self, descriptor: ManagedArtifactDescriptor
    ) -> ManagedArtifactDescriptor: ...

    def register_location(
        self,
        artifact_identity: str,
        location: ArtifactLocation,
    ) -> None: ...

    def deactivate_location(
        self,
        artifact_identity: str,
        location: ArtifactLocation,
    ) -> None: ...

    def get_artifact(self, artifact_identity: str) -> ManagedArtifactDescriptor: ...

    def get_active_locations(
        self, artifact_identity: str
    ) -> tuple[ArtifactLocation, ...]: ...


class InMemoryArtifactIndex:
    """Thread-safe reference implementation of the core index contract."""

    def __init__(self) -> None:
        self._artifacts: dict[str, ManagedArtifactDescriptor] = {}
        self._locations: dict[str, list[ArtifactLocation]] = {}
        self._lock = RLock()

    def register_artifact(
        self, descriptor: ManagedArtifactDescriptor
    ) -> ManagedArtifactDescriptor:
        with self._lock:
            existing = self._artifacts.get(descriptor.identity)
            if existing is not None and existing != descriptor:
                raise ArtifactIdentityCollisionError(
                    f"artifact identity {descriptor.identity!r} is already registered "
                    "with different immutable metadata"
                )
            if existing is None:
                self._artifacts[descriptor.identity] = descriptor
                self._locations[descriptor.identity] = []
            return descriptor

    def register_location(
        self,
        artifact_identity: str,
        location: ArtifactLocation,
    ) -> None:
        with self._lock:
            locations = self._locations_for(artifact_identity)
            if location not in locations:
                locations.append(location)

    def deactivate_location(
        self,
        artifact_identity: str,
        location: ArtifactLocation,
    ) -> None:
        with self._lock:
            locations = self._locations_for(artifact_identity)
            for position, existing in enumerate(locations):
                if existing == location:
                    locations[position] = replace(
                        existing,
                        state=type(existing.state).INACTIVE,
                    )
                    return

    def get_artifact(self, artifact_identity: str) -> ManagedArtifactDescriptor:
        with self._lock:
            try:
                return self._artifacts[artifact_identity]
            except KeyError as error:
                raise ArtifactIndexNotFoundError(
                    f"artifact identity {artifact_identity!r} is not registered"
                ) from error

    def get_active_locations(
        self, artifact_identity: str
    ) -> tuple[ArtifactLocation, ...]:
        with self._lock:
            active_locations: list[ArtifactLocation] = []
            for location in self._locations_for(artifact_identity):
                if location.state.value == "active":
                    active_locations.append(location)
            return tuple(active_locations)

    def _locations_for(self, artifact_identity: str) -> list[ArtifactLocation]:
        try:
            return self._locations[artifact_identity]
        except KeyError as error:
            raise ArtifactIndexNotFoundError(
                f"artifact identity {artifact_identity!r} is not registered"
            ) from error


__all__ = [
    "ArtifactIdentityCollisionError",
    "ArtifactIndex",
    "ArtifactIndexError",
    "ArtifactIndexNotFoundError",
    "InMemoryArtifactIndex",
]
