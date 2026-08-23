from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path

import pytest

from provium_pipeline.artifact import (
    ArtifactLocation,
    ArtifactLocationState,
    ArtifactStore,
    ArtifactStoreConflictError,
    ArtifactStoreCorruptionError,
    ArtifactStoreError,
    ArtifactStoreNotFoundError,
    ArtifactStorePermissionError,
    ArtifactStoreTransientError,
    InvalidArtifactLocatorError,
    ManagedArtifactDescriptor,
    MaterializationCleanup,
    MaterializedArtifact,
    StagedArtifactFile,
)


def descriptor() -> ManagedArtifactDescriptor:
    return ManagedArtifactDescriptor(
        identity="artifact-identity",
        artifact_identifier="example.document",
        body_digest="body-digest",
        container_digest="container-digest",
        size_bytes=42,
        created_at=datetime(2026, 8, 22, tzinfo=UTC),
    )


def test_managed_artifact_values_are_immutable() -> None:
    managed = descriptor()
    location = ArtifactLocation(
        store_identifier="local",
        locator={"path": "objects/artifact.pa"},
        state=ArtifactLocationState.ACTIVE,
        size_bytes=42,
        created_at=datetime(2026, 8, 22, tzinfo=UTC),
        verified_at=None,
    )
    staged = StagedArtifactFile(
        store_identifier="local",
        staging_identity="staging-id",
        local_path=Path("staging/artifact.tmp"),
    )
    materialized = MaterializedArtifact(
        descriptor=managed,
        path=Path("materialized/artifact.pa"),
        cleanup=MaterializationCleanup.REQUIRED,
    )

    assert location.state is ArtifactLocationState.ACTIVE
    assert staged.local_path.name == "artifact.tmp"
    assert materialized.cleanup is MaterializationCleanup.REQUIRED
    with pytest.raises(FrozenInstanceError):
        managed.size_bytes = 43  # type: ignore[misc]


def test_artifact_store_failure_kinds_share_a_typed_base() -> None:
    failure_types = (
        ArtifactStoreConflictError,
        ArtifactStoreNotFoundError,
        ArtifactStoreCorruptionError,
        ArtifactStorePermissionError,
        ArtifactStoreTransientError,
        InvalidArtifactLocatorError,
    )

    assert all(issubclass(failure, ArtifactStoreError) for failure in failure_types)


def test_artifact_store_protocol_exposes_complete_lifecycle() -> None:
    assert {
        "identifier",
        "publish",
        "stat",
        "materialize",
        "delete",
        "list_orphans",
    } <= ArtifactStore.__dict__.keys()
