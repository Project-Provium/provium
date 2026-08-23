"""Managed artifact values and storage contracts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from provium import inspect_finalized_artifact

from .index import (
    ArtifactIdentityCollisionError,
    ArtifactIndex,
    ArtifactIndexError,
    ArtifactIndexNotFoundError,
    InMemoryArtifactIndex,
)

type JsonScalar = None | bool | int | float | str
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


class ArtifactLocationState(StrEnum):
    """Whether an artifact location may be used for resolution."""

    ACTIVE = "active"
    INACTIVE = "inactive"


class MaterializationCleanup(StrEnum):
    """Whether the materialized path is owned by the caller."""

    REQUIRED = "required"
    NOT_REQUIRED = "not_required"


@dataclass(frozen=True, slots=True)
class ManagedArtifactDescriptor:
    """Immutable metadata for a complete, finalized Provium artifact."""

    identity: str
    artifact_identifier: str
    body_digest: str
    container_digest: str | None
    size_bytes: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ArtifactLocation:
    """A store-specific location for a managed artifact."""

    store_identifier: str
    locator: JsonValue
    state: ArtifactLocationState
    size_bytes: int | None
    created_at: datetime
    verified_at: datetime | None


@dataclass(frozen=True, slots=True)
class StagedArtifactFile:
    """A temporary artifact file owned by a store."""

    store_identifier: str
    staging_identity: str
    local_path: Path


@dataclass(frozen=True, slots=True)
class MaterializedArtifact:
    """A verified local artifact path and its cleanup ownership."""

    descriptor: ManagedArtifactDescriptor
    path: Path
    cleanup: MaterializationCleanup


@dataclass(frozen=True, slots=True)
class ArtifactObjectMetadata:
    """Store metadata returned without materializing an artifact."""

    size_bytes: int
    modified_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class OrphanQuery:
    """Store-specific bounds for orphan discovery."""

    created_before: datetime | None = None


class ArtifactStoreError(Exception):
    """Base class for typed artifact-store failures."""


class ArtifactStoreConflictError(ArtifactStoreError):
    """A locator already contains a different artifact."""


class ArtifactStoreNotFoundError(ArtifactStoreError):
    """The requested artifact object does not exist."""


class ArtifactStoreCorruptionError(ArtifactStoreError):
    """Stored bytes do not match the managed descriptor."""


class ArtifactStorePermissionError(ArtifactStoreError):
    """The store rejected an operation for permission reasons."""


class ArtifactStoreTransientError(ArtifactStoreError):
    """A retryable store failure occurred."""


class InvalidArtifactLocatorError(ArtifactStoreError):
    """A store locator is malformed or unsupported."""


def describe_finalized_artifact(path: Path) -> ManagedArtifactDescriptor:
    """Fully verify a finalized artifact and return its managed descriptor."""
    try:
        inspection = inspect_finalized_artifact(path)
    except ValueError as error:
        raise ArtifactStoreCorruptionError(str(error)) from error
    if inspection.created_at is None:
        raise ArtifactStoreCorruptionError(
            "artifact container does not include a creation timestamp"
        )
    return ManagedArtifactDescriptor(
        identity=inspection.artifact_identity,
        artifact_identifier=inspection.artifact_identifier,
        body_digest=inspection.body_digest,
        container_digest=inspection.container_digest,
        size_bytes=inspection.size_bytes,
        created_at=inspection.created_at,
    )


class ArtifactStore(Protocol):
    """Storage backend for complete finalized Provium artifacts."""

    @property
    def identifier(self) -> str: ...

    def publish(
        self,
        *,
        source: Path,
        descriptor: ManagedArtifactDescriptor,
    ) -> ArtifactLocation: ...

    def stat(self, location: ArtifactLocation) -> ArtifactObjectMetadata: ...

    def materialize(
        self,
        *,
        descriptor: ManagedArtifactDescriptor,
        locations: Sequence[ArtifactLocation],
        destination: Path,
    ) -> MaterializedArtifact: ...

    def delete(self, location: ArtifactLocation) -> None: ...

    def list_orphans(self, request: OrphanQuery) -> tuple[ArtifactLocation, ...]: ...


__all__ = [
    "ArtifactLocation",
    "ArtifactIdentityCollisionError",
    "ArtifactIndex",
    "ArtifactIndexError",
    "ArtifactIndexNotFoundError",
    "ArtifactLocationState",
    "ArtifactObjectMetadata",
    "ArtifactStore",
    "ArtifactStoreConflictError",
    "ArtifactStoreCorruptionError",
    "ArtifactStoreError",
    "ArtifactStoreNotFoundError",
    "ArtifactStorePermissionError",
    "ArtifactStoreTransientError",
    "InMemoryArtifactIndex",
    "InvalidArtifactLocatorError",
    "ManagedArtifactDescriptor",
    "MaterializationCleanup",
    "MaterializedArtifact",
    "OrphanQuery",
    "StagedArtifactFile",
    "describe_finalized_artifact",
]
