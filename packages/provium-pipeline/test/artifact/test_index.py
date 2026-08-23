from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from provium_pipeline.artifact import (
    ArtifactIdentityCollisionError,
    ArtifactIndex,
    ArtifactIndexNotFoundError,
    ArtifactLocation,
    ArtifactLocationState,
    InMemoryArtifactIndex,
    ManagedArtifactDescriptor,
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


def location() -> ArtifactLocation:
    return ArtifactLocation(
        store_identifier="local",
        locator={"path": "objects/artifact.pa"},
        state=ArtifactLocationState.ACTIVE,
        size_bytes=42,
        created_at=datetime(2026, 8, 22, tzinfo=UTC),
        verified_at=None,
    )


def test_index_registration_is_idempotent_only_for_identical_descriptors() -> None:
    index = InMemoryArtifactIndex()
    managed = descriptor()

    assert index.register_artifact(managed) == managed
    assert index.register_artifact(managed) == managed

    with pytest.raises(ArtifactIdentityCollisionError):
        index.register_artifact(replace(managed, body_digest="different"))


def test_index_registers_resolves_and_deactivates_locations() -> None:
    index = InMemoryArtifactIndex()
    managed = index.register_artifact(descriptor())
    stored_location = location()

    second_location = replace(
        stored_location,
        locator={"path": "objects/second-artifact.pa"},
    )
    index.register_location(managed.identity, stored_location)
    index.register_location(managed.identity, stored_location)
    index.register_location(managed.identity, second_location)

    assert index.get_artifact(managed.identity) == managed
    assert index.get_active_locations(managed.identity) == (
        stored_location,
        second_location,
    )

    index.deactivate_location(managed.identity, stored_location)

    assert index.get_active_locations(managed.identity) == (second_location,)


def test_index_rejects_locations_for_unknown_artifacts() -> None:
    index = InMemoryArtifactIndex()

    with pytest.raises(ArtifactIndexNotFoundError, match="unknown"):
        index.register_location("unknown", location())


def test_index_rejects_unknown_artifact_lookup() -> None:
    index = InMemoryArtifactIndex()

    with pytest.raises(ArtifactIndexNotFoundError, match="unknown"):
        index.get_artifact("unknown")


def test_deactivating_an_unregistered_location_is_idempotent() -> None:
    index = InMemoryArtifactIndex()
    managed = index.register_artifact(descriptor())
    registered = location()
    unregistered = replace(registered, locator={"path": "objects/unknown.pa"})
    index.register_location(managed.identity, registered)

    index.deactivate_location(managed.identity, unregistered)

    assert index.get_active_locations(managed.identity) == (registered,)


def test_artifact_index_protocol_exposes_registration_and_resolution() -> None:
    assert {
        "register_artifact",
        "register_location",
        "deactivate_location",
        "get_artifact",
        "get_active_locations",
    } <= ArtifactIndex.__dict__.keys()
